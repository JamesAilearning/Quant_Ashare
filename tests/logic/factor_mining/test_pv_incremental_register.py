"""pv_incremental_v1 candidate-registration tests.

Dimensions: frozen-plan discipline × GP-run binding (universe/window/
fields/fitness/baseline) × selection (fitness order, -inf exclusion,
top-K) × manifest shape (safe unique ids, verbatim expressions,
orientation carried through) × self-verification against the
evaluator's own preflight × write discipline (never overwrite a frozen
registration) × ledger record.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.research.pv_incremental_register_candidates as rg  # noqa: E402

_CSF = "cs_rank(ts_pctchange($close, 20))"
_CSF2 = "cs_zscore(ts_delta($volume, 10))"


def _plan() -> dict:
    return rg.load_frozen_plan()


def _entry(expression: str, fitness: float, orientation: int = 1):
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.factor_pool import PoolEntry
    expr = parse_expression(expression)
    return PoolEntry(
        expr=expr, fitness=fitness, ic_mean=0.01, ic_std=0.1, ir=0.1,
        rank_ic_mean=0.02 * orientation, rank_ic_std=0.1, rank_ir=0.2,
        turnover_daily=0.1, coverage=1.0, n_obs_per_day_min=300,
        expr_size=5, expr_hash=hash(expr), orientation=orientation)


def _run_config(**overrides) -> dict:
    plan = _plan()
    cfg = {
        "data": {
            "mode": "pit",
            "forward_horizon": plan["metric"]["signal_to_execution_lag"],
            "universe_name": plan["universe"]["instruments"],
            "start_date": plan["windows"]["is_start"],
            "end_date": plan["windows"]["is_end"],
            "forward_return_price": "close",
            "fields": [f"${f}" for f in plan["fields"]],
            "baseline_preds_path": "out/baseline_preds.parquet",
            "baseline_model": plan["fitness"]["baseline"]["model"],
        },
        "fitness": {
            "ic_term": plan["fitness"]["ic_term"],
            "w_complexity": plan["fitness"]["parsimony_lambda_per_node"],
            "w_orthogonality":
                plan["fitness"]["orthogonality"]["fitness_penalty_weight"],
            "orthogonality_band":
                plan["fitness"]["orthogonality"]["fitness_band_abs_rho"],
            "min_names_per_day": plan["metric"]["min_names_per_day"],
        },
        "gp": {"max_depth": plan["gp"]["max_depth"],
               "min_depth": plan["gp"]["min_depth"], "seed": 42},
        "run_id": "gp0001",
    }
    for section, patch in overrides.items():
        cfg[section] = {**cfg[section], **patch}
    return cfg


def _make_run(tmp: Path, entries, *, config=None) -> Path:
    from src.factor_mining.factor_pool import FactorPool
    tmp.mkdir(parents=True, exist_ok=True)
    pool = FactorPool()
    for e in entries:
        pool.add(e)
    pool.save(tmp)
    (tmp / "config.yaml").write_text(
        yaml.safe_dump(config if config is not None else _run_config(),
                       sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    return tmp


class FrozenPlanTests(unittest.TestCase):
    def test_committed_plan_loads(self) -> None:
        plan = _plan()
        self.assertEqual("pv_incremental_v1", plan["protocol_id"])
        self.assertIs(False, plan["holdout_unblinded"])

    def test_unblinded_plan_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = dict(_plan())
            bad["holdout_unblinded"] = True
            p = Path(d) / "plan.yaml"
            p.write_text(yaml.safe_dump(bad), encoding="utf-8")
            with self.assertRaises(rg.PVRegisterError):
                rg.load_frozen_plan(p)


class RunBindingTests(unittest.TestCase):
    """A run bred outside the frozen protocol produces trials the
    pre-registration does not cover."""

    def test_matching_run_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(Path(d) / "run", [_entry(_CSF, 0.05)])
            prov = rg.check_run_config(_plan(), run)
            self.assertEqual("gp0001", prov["gp_run_id"])
            self.assertRegex(prov["gp_config_sha256"], r"^[0-9a-f]{64}$")
            # codex #402 r1: the INPUTS are digested, not just named.
            d = prov["gp_input_sha256"]
            self.assertRegex(d["factor_pool.parquet"], r"^[0-9a-f]{64}$")
            self.assertRegex(d["factor_expressions.json"],
                             r"^[0-9a-f]{64}$")
            self.assertEqual("ABSENT_AT_REGISTRATION",
                             d["baseline_preds.parquet"])

    def test_pool_digest_changes_when_pool_changes(self) -> None:
        # A pool replaced in place must not present identical
        # provenance — the top-K selection could not be reconstructed.
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(Path(d) / "run", [_entry(_CSF, 0.05)])
            first = rg.check_run_config(
                _plan(), run)["gp_input_sha256"]["factor_pool.parquet"]
            _make_run(run, [_entry(_CSF, 0.05), _entry(_CSF2, 0.09)])
            second = rg.check_run_config(
                _plan(), run)["gp_input_sha256"]["factor_pool.parquet"]
            self.assertNotEqual(first, second)

    def test_incomplete_run_dir_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(Path(d) / "run", [_entry(_CSF, 0.05)])
            (run / "factor_expressions.json").unlink()
            with self.assertRaises(rg.PVRegisterError):
                rg.check_run_config(_plan(), run)

    def test_protocol_drift_refuses(self) -> None:
        plan = _plan()
        for label, override in (
                ("other universe", {"data": {"universe_name": "csi300"}}),
                ("OOS window", {"data": {"start_date": "2023-01-01"}}),
                ("holdout tail", {"data": {"end_date": "2025-12-31"}}),
                ("open label", {"data": {"forward_return_price": "open"}}),
                ("legacy criterion",
                 {"fitness": {"ic_term": "v1_composite"}}),
                ("no thin-day floor",
                 {"fitness": {"min_names_per_day": 0}}),
                ("band drift",
                 {"fitness": {"orthogonality_band": 0.5}}),
                # codex #402 r1: dimensions that change what was BRED
                # while leaving universe/window strings intact.
                ("synthetic panel", {"data": {"mode": "synthetic"}}),
                ("other horizon", {"data": {"forward_horizon": 5}}),
                ("other baseline model",
                 {"data": {"baseline_model": "some_other_model"}}),
                ("looser depth", {"gp": {"max_depth": 9}})):
            with tempfile.TemporaryDirectory() as d:
                run = _make_run(Path(d) / "run", [_entry(_CSF, 0.05)],
                                config=_run_config(**override))
                with self.assertRaises(rg.PVRegisterError, msg=label):
                    rg.check_run_config(plan, run)

    def test_foreign_field_set_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(
                Path(d) / "run", [_entry(_CSF, 0.05)],
                config=_run_config(data={"fields": ["$close", "$pe"]}))
            with self.assertRaises(rg.PVRegisterError) as ctx:
                rg.check_run_config(_plan(), run)
            self.assertIn("admits exactly", str(ctx.exception))

    def test_unbound_baseline_refuses(self) -> None:
        # A run without a baseline bred with NO incremental criterion.
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(
                Path(d) / "run", [_entry(_CSF, 0.05)],
                config=_run_config(data={"baseline_preds_path": ""}))
            with self.assertRaises(rg.PVRegisterError) as ctx:
                rg.check_run_config(_plan(), run)
            self.assertIn("incremental criterion", str(ctx.exception))

    def test_missing_config_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run"
            run.mkdir()
            with self.assertRaises(rg.PVRegisterError):
                rg.check_run_config(_plan(), run)


class SelectionTests(unittest.TestCase):
    def test_top_k_by_fitness_excludes_invalid(self) -> None:
        from src.factor_mining.factor_pool import FactorPool
        pool = FactorPool()
        for e in (_entry(_CSF, 0.05), _entry(_CSF2, 0.09),
                  _entry("cs_rank(ts_mean($money, 5))",
                         float("-inf"))):
            pool.add(e)
        picked = rg.select_candidates(pool, top_k=2)
        self.assertEqual([0.09, 0.05], [e.fitness for e in picked])
        # -inf never registers: it would consume a family slot and
        # raise the FWER bar for real trials.
        self.assertEqual(
            1, len(rg.select_candidates(pool, top_k=99)) - 1)

    def test_all_invalid_refuses(self) -> None:
        from src.factor_mining.factor_pool import FactorPool
        pool = FactorPool()
        pool.add(_entry(_CSF, float("-inf")))
        with self.assertRaises(rg.PVRegisterError):
            rg.select_candidates(pool, top_k=5)

    def test_non_positive_top_k_refuses(self) -> None:
        from src.factor_mining.factor_pool import FactorPool
        pool = FactorPool()
        pool.add(_entry(_CSF, 0.05))
        with self.assertRaises(rg.PVRegisterError):
            rg.select_candidates(pool, top_k=0)


class ManifestTests(unittest.TestCase):
    def test_ids_are_safe_stable_and_unique(self) -> None:
        entries = [_entry(_CSF2, 0.09), _entry(_CSF, 0.05)]
        manifest = rg.build_manifest(entries)
        ids = [c["candidate_id"] for c in manifest]
        self.assertEqual(len(set(ids)), len(ids))
        # Safe slug — the evaluator turns the id into a filename.
        from scripts.research.pv_incremental_eval import check_candidate_id
        for cid in ids:
            check_candidate_id(cid)
        # Content-derived: the same expression at the same rank is
        # reproducible across processes (expr_hash is NOT, being
        # Python's randomised hash()).
        self.assertEqual(manifest,
                         rg.build_manifest(list(entries)))

    def test_expressions_are_verbatim_from_the_pool(self) -> None:
        entries = [_entry(_CSF, 0.05)]
        manifest = rg.build_manifest(entries)
        self.assertEqual(entries[0].expr.to_qlib_string(),
                         manifest[0]["expression"])
        # And it must round-trip through the frozen grammar — the
        # AST repr form does NOT (this is what the self-check caught).
        from src.factor_mining.expression import parse_expression
        self.assertEqual(
            manifest[0]["expression"],
            parse_expression(manifest[0]["expression"]).to_qlib_string())

    def test_orientation_carried_through_positionally(self) -> None:
        # The registered sign must belong to the SAME row as the
        # expression — a positional slip would test factors backwards.
        entries = [_entry(_CSF2, 0.09, orientation=-1),
                   _entry(_CSF, 0.05, orientation=1)]
        manifest = rg.build_manifest(entries)
        by_expr = {c["expression"]: c["orientation"] for c in manifest}
        self.assertEqual(-1, by_expr[_CSF2])
        self.assertEqual(1, by_expr[_CSF])

    def test_invalid_pool_orientation_refuses(self) -> None:
        entry = _entry(_CSF, 0.05)
        object.__setattr__(entry, "orientation", 0)
        with self.assertRaises(rg.PVRegisterError):
            rg.build_manifest([entry])


class EvaluatorSelfCheckTests(unittest.TestCase):
    """The registration must be acceptable to the tool that consumes
    it — discovering otherwise at OOS time would burn the one-shot
    window."""

    def test_valid_manifest_passes_evaluator_preflight(self) -> None:
        manifest = rg.build_manifest([_entry(_CSF, 0.05)])
        rg.selfcheck_against_evaluator(_plan(), manifest)

    def test_forbidden_terminal_would_be_refused(self) -> None:
        manifest = [{"candidate_id": "pv001_deadbeef",
                     "expression": "cs_rank(ts_pctchange($pe, 20))",
                     "orientation": 1}]
        with self.assertRaises(rg.PVRegisterError) as ctx:
            rg.selfcheck_against_evaluator(_plan(), manifest)
        self.assertIn("REFUSED by the OOS evaluator",
                      str(ctx.exception))

    def test_non_csf_root_would_be_refused(self) -> None:
        manifest = [{"candidate_id": "pv001_deadbeef",
                     "expression": "$close", "orientation": 1}]
        with self.assertRaises(rg.PVRegisterError):
            rg.selfcheck_against_evaluator(_plan(), manifest)


class EndToEndTests(unittest.TestCase):
    def test_writes_manifest_provenance_and_ledger_entry(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(
                Path(d) / "run",
                [_entry(_CSF2, 0.09, orientation=-1),
                 _entry(_CSF, 0.05)])
            out = Path(d) / "out"
            rc = rg.main(["--run-dir", str(run), "--out-dir", str(out),
                          "--top-k", "2", "--when", "2026-08-06"])
            self.assertEqual(0, rc)
            manifest = json.loads(
                (out / "candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(manifest))
            self.assertEqual({"candidate_id", "expression",
                              "orientation"},
                             set(manifest[0]))
            # Highest fitness first, with its own sign.
            self.assertEqual(_CSF2, manifest[0]["expression"])
            self.assertEqual(-1, manifest[0]["orientation"])
            prov = json.loads(
                (out / "candidates.json.provenance.json")
                .read_text(encoding="utf-8"))
            self.assertEqual("pv_incremental_v1", prov["protocol_id"])
            self.assertEqual(2, prov["registered"])
            self.assertEqual(str(run), prov["gp_run_dir"])
            ledger = yaml.safe_load(
                (out / "ledger_entry.yaml").read_text(encoding="utf-8"))
            self.assertEqual("intent", ledger[0]["kind"])
            self.assertEqual({"pool_size": 2, "registered": 2},
                             ledger[0]["numbers"])

    def test_never_overwrites_a_frozen_registration(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(Path(d) / "run", [_entry(_CSF, 0.05)])
            out = Path(d) / "out"
            args = ["--run-dir", str(run), "--out-dir", str(out),
                    "--top-k", "1", "--when", "2026-08-06"]
            self.assertEqual(0, rg.main(args))
            first = (out / "candidates.json").read_bytes()
            self.assertEqual(2, rg.main(args))
            # codex #402 r1: exclusive create — the frozen file is
            # byte-identical after the refused second attempt.
            self.assertEqual(first,
                             (out / "candidates.json").read_bytes())

    def test_manifest_is_consumable_by_the_adjudicator(self) -> None:
        # End-to-end shape check against the OTHER consumer: the
        # family-manifest binding reads candidate_id / expression /
        # orientation, so a registration must satisfy it verbatim.
        import scripts.research.pv_incremental_fwer_adjudication as fw
        with tempfile.TemporaryDirectory() as d:
            run = _make_run(Path(d) / "run",
                            [_entry(_CSF, 0.05, orientation=-1)])
            out = Path(d) / "out"
            self.assertEqual(0, rg.main(
                ["--run-dir", str(run), "--out-dir", str(out),
                 "--top-k", "1", "--when", "2026-08-06"]))
            manifest = json.loads(
                (out / "candidates.json").read_text(encoding="utf-8"))
            artifact = {"candidate_id": manifest[0]["candidate_id"],
                        "expression": manifest[0]["expression"],
                        "orientation": manifest[0]["orientation"]}
            fw.check_family_manifest([artifact], manifest)
            # And a sign flip in the artifact refuses, proving the
            # registration's orientation is load-bearing.
            with self.assertRaises(fw.PVFwerError):
                fw.check_family_manifest(
                    [dict(artifact, orientation=1)], manifest)


if __name__ == "__main__":
    unittest.main()

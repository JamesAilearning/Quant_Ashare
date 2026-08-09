"""Governance pins for the pv_incremental_v1 freeze (PV-DP-1..8).

The frozen plan is the signed artifact — these pins make any drift a
red test instead of a silent re-registration.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as _PD
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_PLAN = yaml.safe_load(
    (_PROJECT_ROOT / "docs" / "prereg" / "pv_incremental.yaml").read_text(
        encoding="utf-8"))


class PvIncrementalFreezePins(unittest.TestCase):
    def test_protocol_identity(self) -> None:
        self.assertEqual("pv_incremental_v1", _PLAN["protocol_id"])
        self.assertIs(False, _PLAN["holdout_unblinded"])

    def test_operator_whitelist_is_the_registry_verbatim(self) -> None:
        # PV-DP-4: exactly the baseline 28, zero extensions, no ts_cov.
        from src.factor_mining.grammar import REGISTRY

        self.assertEqual(sorted(REGISTRY.names()),
                         sorted(_PLAN["operators"]))
        self.assertEqual(28, len(_PLAN["operators"]))
        self.assertNotIn("ts_cov", _PLAN["operators"])
        self.assertIs(True, _PLAN["ts_cov_excluded"])

    def test_gp_depth_pins_match_engine_defaults(self) -> None:
        from src.factor_mining.gp_engine import GPConfig

        cfg = GPConfig()
        self.assertEqual(cfg.max_depth, _PLAN["gp"]["max_depth"])
        self.assertEqual(cfg.min_depth, _PLAN["gp"]["min_depth"])

    def test_fields_are_the_signed_seven(self) -> None:
        self.assertEqual(
            ["open", "high", "low", "close", "volume", "money",
             "turnover_rate"], _PLAN["fields"])

    def test_windows_are_the_signed_values(self) -> None:
        w = _PLAN["windows"]
        self.assertEqual(
            ("2018-01-01", "2022-12-31", "2023-01-01", "2024-12-31",
             2025, "2026-01-01"),
            (w["is_start"], w["is_end"], w["oos_start"], w["oos_end"],
             w["holdout_year"], w["forbidden_from"]))

    def test_fwer_mechanism_pins(self) -> None:
        f = _PLAN["fwer"]
        # codex #399 r10: sidedness is FROZEN — survival is the
        # one-sided event t >= threshold, so the null statistic is
        # the signed max-t (Gate-4A-sourced), never |t|.
        self.assertEqual("one_sided_max_t", f["statistic"])
        self.assertEqual("full_batch_block_bootstrap_max_statistic",
                         f["method"])
        self.assertEqual(2.85, f["hard_floor_t"])
        self.assertEqual(0.95, f["quantile"])
        self.assertEqual(120, f["per_trial_min_n_days"])
        self.assertEqual("reject_iff", f["tri_state"]["clean_negative"])
        # codex #399 r1: significant-but-non-incremental is a DISTINCT
        # state routed to the operator — never laundered into
        # clean_negative's reject_iff.
        self.assertEqual("operator_decision",
                         f["tri_state"]["significant_non_incremental"])

    def test_orthogonality_coverage_pin(self) -> None:
        self.assertEqual(
            0.95,
            _PLAN["fitness"]["orthogonality"]["min_coverage_of_ic_days"])

    def test_implementation_pr_decisions_pinned(self) -> None:
        # The three口径 the operator signed for the implementation PR
        # (PV-DP-3 left them to it). Frozen here so a later edit is a
        # deliberate protocol change, not a silent drift.
        orth = _PLAN["fitness"]["orthogonality"]
        base = _PLAN["fitness"]["baseline"]
        # ① IS coverage: penalise only days the baseline covers — the
        # baseline keeps the production fold geometry rather than
        # manufacturing earlier folds.
        self.assertEqual("penalize_covered_days_only",
                         orth["is_coverage_policy"])
        # ② production-equivalent warm ensemble, not single-model.
        self.assertEqual(3, base["ensemble_window"])
        # ③ GP breeding signal uses |rank-IC| (direction belongs to the
        # expression; the one-sided FWER threshold culls negatives).
        self.assertEqual("abs_rank_ic", _PLAN["fitness"]["ic_term"])
        # Sacred-invariant defence: the baseline run's hard tail must
        # stop before the blinded holdout year.
        self.assertEqual("2024-12-31", base["overall_end"])
        self.assertEqual(_PLAN["windows"]["oos_end"], base["overall_end"])

    def test_frozen_fitness_is_expressible_and_consumed(self) -> None:
        # codex #401 r1: the frozen breeding criterion must map onto
        # REAL FitnessConfig fields the engine consumes — a plan value
        # no code reads would let the GP select factors by a metric
        # other than the pre-registered one.
        from src.factor_mining.fitness import FitnessConfig, compute_fitness
        f = _PLAN["fitness"]
        cfg = FitnessConfig(
            ic_term=f["ic_term"],
            w_complexity=f["parsimony_lambda_per_node"],
            w_orthogonality=f["orthogonality"]["fitness_penalty_weight"],
            orthogonality_band=f["orthogonality"]["fitness_band_abs_rho"],
        )
        self.assertEqual("abs_rank_ic", cfg.ic_term)
        self.assertEqual(0.002, cfg.w_complexity)
        self.assertEqual(2.0, cfg.w_orthogonality)
        self.assertEqual(0.30, cfg.orthogonality_band)
        # And the formula actually honours them.
        from src.factor_mining.evaluator import EvaluationResult
        result = EvaluationResult(
            factor_values=_PD.DataFrame(
                {"a": [1.0], "b": [2.0]},
                index=_PD.DatetimeIndex(["2023-01-02"])),
            ic_mean=0.9, ic_std=0.1, ir=5.0, rank_ic_mean=0.05,
            rank_ic_std=0.1, rank_ir=0.5, turnover_daily=0.9,
            coverage=1.0, n_obs_per_day_min=2)
        score = compute_fitness(result, expr_size=10, novelty_penalty=0.9,
                                config=cfg, baseline_mean_abs_rho=0.40)
        # |0.05| − 0.002×10 − 2.0×(0.40−0.30); no IR/turnover/novelty.
        self.assertAlmostEqual(0.05 - 0.02 - 0.2, score)

    def test_campaign_miner_config_matches_the_frozen_plan(self) -> None:
        # codex #401 r9: without a campaign miner config the only
        # available path loads all twelve V1 terminals and the
        # open→open label — the GP would breed on forbidden inputs
        # against a different target than the one that adjudicates.
        cfg = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "factor_mining"
             / "pv_incremental_v1.yaml").read_text(encoding="utf-8"))
        data, fit = cfg["data"], cfg["fitness"]
        plan_fit = _PLAN["fitness"]
        # PV-DP-1: exactly the seven frozen fields, verbatim order-free.
        self.assertEqual(sorted(f"${f}" for f in _PLAN["fields"]),
                         sorted(data["fields"]))
        # PV-DP-2: the GP may see the IS window and nothing else.
        self.assertEqual(_PLAN["windows"]["is_start"], data["start_date"])
        self.assertEqual(_PLAN["windows"]["is_end"], data["end_date"])
        self.assertEqual(_PLAN["universe"]["instruments"],
                         data["universe_name"])
        # PV-DP-3: breeding target == adjudicating target.
        self.assertEqual("close", data["forward_return_price"])
        self.assertEqual("close_exec_to_close_next",
                         _PLAN["metric"]["forward_return"])
        self.assertEqual(plan_fit["baseline"]["model"],
                         data["baseline_model"])
        # Frozen fitness constants, consumed by real config fields.
        self.assertEqual(plan_fit["ic_term"], fit["ic_term"])
        self.assertEqual(plan_fit["parsimony_lambda_per_node"],
                         fit["w_complexity"])
        self.assertEqual(
            plan_fit["orthogonality"]["fitness_penalty_weight"],
            fit["w_orthogonality"])
        self.assertEqual(
            plan_fit["orthogonality"]["fitness_band_abs_rho"],
            fit["orthogonality_band"])
        self.assertEqual(_PLAN["metric"]["min_names_per_day"],
                         fit["min_names_per_day"])
        # The config must parse into the real dataclasses (a typo in a
        # key would otherwise sit here until ignition).
        from src.factor_mining.fitness import FitnessConfig
        from src.factor_mining.miner import DataConfig
        DataConfig(**data)
        FitnessConfig(**fit)

    def test_baseline_path_is_portable_and_env_overridable(self) -> None:
        # The campaign config is tracked and governance-pinned, so the
        # baseline binding must NOT require editing it at ignition: a
        # dirty tree at that moment contaminates the GP run's config
        # dump, which is precisely what the registrar binds these
        # candidates' provenance to. Repo-relative default + env
        # override keeps ignition a zero-edit operation and keeps
        # machine-local paths out of the repo (CLAUDE.md).
        import os

        from src.factor_mining.miner import load_config
        cfg_path = (_PROJECT_ROOT / "config" / "factor_mining"
                    / "pv_incremental_v1.yaml")
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        declared = raw["data"]["baseline_preds_path"]
        self.assertIn("${PV_BASELINE_PREDS", declared)
        # No machine-local path may be committed here.
        self.assertNotIn(":\\", declared)
        self.assertNotIn(":/", declared.replace("${", ""))
        self.assertFalse(declared.startswith("/"))

        # Both branches of the fallback must actually resolve — a
        # placeholder no loader expands would hand the literal
        # "${...}" string to the miner (codex #401 r11).
        before = os.environ.get("PV_BASELINE_PREDS")
        try:
            os.environ.pop("PV_BASELINE_PREDS", None)
            default = load_config(cfg_path).data.baseline_preds_path
            self.assertNotIn("$", default)
            # The default IS the exporter's standard out-dir, or the
            # zero-edit ignition promise is empty.
            self.assertEqual(
                "output/factor_mining/pv_incremental_v1/baseline/"
                "baseline_preds.parquet", default)
            os.environ["PV_BASELINE_PREDS"] = "elsewhere/other.parquet"
            self.assertEqual(
                "elsewhere/other.parquet",
                load_config(cfg_path).data.baseline_preds_path)
        finally:
            os.environ.pop("PV_BASELINE_PREDS", None)
            if before is not None:
                os.environ["PV_BASELINE_PREDS"] = before

    def test_missing_baseline_is_a_refusal_not_a_default(self) -> None:
        # The path having a default must NOT weaken the guard that a
        # campaign with the orthogonality penalty enabled and no
        # exported baseline refuses (miner.py): a silently-zero
        # incremental criterion looks exactly like a healthy run.
        import inspect

        from src.factor_mining import miner
        src = inspect.getsource(miner)
        self.assertIn("data.baseline_preds_path is empty", src)
        self.assertIn("does not exist", src)

    def test_orientation_contract_is_pinned_and_wired(self) -> None:
        # codex #401 r13: the sign-blind breeding criterion is only
        # safe if the IS orientation is recorded and APPLIED. Pin both
        # the frozen flag and the two ends that implement it.
        self.assertIs(True, _PLAN["fitness"]["orientation_recorded"])
        from dataclasses import fields as dc_fields

        from src.factor_mining.factor_pool import PoolEntry
        self.assertIn("orientation",
                      {f.name for f in dc_fields(PoolEntry)})
        import inspect

        import scripts.research.pv_incremental_eval as ev
        # The evaluator must both validate and apply it.
        self.assertIn("orientation",
                      inspect.getsource(ev.preflight_candidates))
        self.assertIn("factor = -factor", inspect.getsource(ev.main))

    def test_registration_tool_binds_the_frozen_protocol(self) -> None:
        # The registration is what the OOS batch is adjudicated
        # against, so the tool that writes it must bind the same
        # frozen values the miner config does — otherwise a run bred
        # outside the protocol could still be registered.
        import inspect

        import scripts.research.pv_incremental_register_candidates as rg
        src = inspect.getsource(rg.check_run_config)
        for token in ("universe", "is_start", "is_end", "ic_term",
                      "min_names_per_day", "orthogonality",
                      "baseline_preds_path"):
            self.assertIn(token, src)
        # And it must self-verify against the consumer's own preflight
        # rather than re-implementing its rules.
        self.assertIn(
            "preflight_candidates",
            inspect.getsource(rg.selfcheck_against_evaluator))

    def test_baseline_preset_matches_frozen_tail(self) -> None:
        # The preset that drives the baseline run must pin the frozen
        # tail: the parent config_walk.yaml default (2025-12-31) would
        # train into and predict the holdout year.
        preset = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "presets"
             / "pv_incremental_baseline.yaml").read_text(encoding="utf-8"))
        self.assertEqual(_PLAN["fitness"]["baseline"]["overall_end"],
                         preset["overall_end"])
        self.assertEqual(_PLAN["universe"]["instruments"],
                         preset["instruments"])

    def test_baseline_start_yields_full_is_coverage(self) -> None:
        # Pin the INTENT, not the literal date: the baseline must start
        # early enough that its first out-of-fold TEST window lands on
        # the frozen IS start. The parent config's 2018-01-01 does not —
        # 24m train + 3m valid pushed the first prediction to
        # 2020-04-01, leaving 670 of ~1215 IS days uncovered, so the
        # campaign's only incremental criterion (the orthogonality
        # penalty) had no purchase on 45% of the breeding window.
        #
        # Derived from the fold geometry so a change to train/valid
        # months re-derives the requirement instead of silently
        # invalidating this pin.
        from dateutil.relativedelta import relativedelta

        preset = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "presets"
             / "pv_incremental_baseline.yaml").read_text(encoding="utf-8"))
        parent = yaml.safe_load(
            (_PROJECT_ROOT / "config_walk.yaml").read_text(encoding="utf-8"))
        train = int(preset.get("train_months", parent["train_months"]))
        valid = int(preset.get("valid_months", parent["valid_months"]))
        start = _PD.Timestamp(str(preset["overall_start"])).date()
        first_test = start + relativedelta(months=train + valid)
        is_start = _PD.Timestamp(_PLAN["windows"]["is_start"]).date()
        self.assertEqual(
            is_start, first_test,
            f"first out-of-fold test window is {first_test}, but the "
            f"frozen IS window starts {is_start} — the baseline would "
            f"leave part of IS uncovered")
        # And the tail stays where the sacred invariant put it.
        self.assertEqual("2024-12-31", str(preset["overall_end"]))
        # Decision 1-rev1: plan, preset and exporter binding must agree
        # on the start — two of the three drifting apart would let a
        # run bred under one definition be certified under another.
        self.assertEqual(str(preset["overall_start"]),
                         str(_PLAN["fitness"]["baseline"]["overall_start"]))
        import inspect

        import scripts.research.pv_incremental_baseline_export as ex
        self.assertIn('"overall_start": base["overall_start"]',
                      inspect.getsource(ex.check_run_config_binding))

    def test_engine_refuses_uncovered_training_history(self) -> None:
        # codex #411 r1-b: _generate_windows snaps valid/test ENDS back
        # onto the calendar but never checked the HEAD — on the old
        # 2018-start bundle a fold declaring train 2016-04..2018-03
        # silently trained on three months of data while its manifest
        # recorded the declared 24-month window, and the exporter would
        # have certified it. Behavioural: a calendar that starts after
        # overall_start must refuse outright.
        from datetime import date, timedelta

        from src.core.walk_forward.config import WalkForwardConfig
        from src.core.walk_forward.engine import WalkForwardEngine, WalkForwardError

        cal = [date(2018, 1, 2) + timedelta(days=i) for i in range(2400)]
        cfg = WalkForwardConfig(overall_start="2015-10-01",
                                overall_end="2024-12-31")
        with self.assertRaises(WalkForwardError) as ctx:
            WalkForwardEngine._generate_windows(cfg, calendar=cal)
        self.assertIn("predates the bound data calendar",
                      str(ctx.exception))
        # AUTHORITATIVE branch (codex #412 r2): when the bundle stamp
        # carries the fetch-coverage start, the guard compares against
        # THAT — no gap heuristic. A bundle whose fetch began
        # 2015-10-12 gaps only 7 weekdays (inside any closure
        # tolerance) yet misses the real 10-08/10-09 sessions; the
        # stamp says so, and the run refuses.
        cal_1012 = [date(2015, 10, 12) + timedelta(days=i)
                    for i in range(3400)]
        with self.assertRaises(WalkForwardError) as ctx_cov:
            WalkForwardEngine._generate_windows(
                cfg, calendar=cal_1012,
                data_coverage_start="2015-10-12")
        self.assertIn("fetched data coverage", str(ctx_cov.exception))
        # An honest full-coverage stamp passes with the SAME calendar
        # shape the real bundle has (first session 10-08).
        cal_full = [date(2015, 10, 8) + timedelta(days=i)
                    for i in range(3400)]
        wins_cov = WalkForwardEngine._generate_windows(
            cfg, calendar=cal_full, data_coverage_start="2015-10-01")
        self.assertGreater(len(wins_cov), 19)

        # ANCHOR-SPECIFIC exactness (codex #412 r6): with the
        # exchange-calendar-derived expected first session stamped,
        # even a ONE-session truncation refuses - the case no global
        # gap threshold can ever catch (every bound K accepts a gap of
        # exactly K; lowering 7 to 6 merely moved the hiding place
        # from 10-12 to 10-09).
        cal_1009 = [date(2015, 10, 9) + timedelta(days=i)
                    for i in range(3400)]
        with self.assertRaises(WalkForwardError) as ctx_exact:
            WalkForwardEngine._generate_windows(
                cfg, calendar=cal_1009,
                data_coverage_start="2015-10-01",
                expected_first_session="2015-10-08")
        self.assertIn("does not honour the stamped coverage",
                      str(ctx_exact.exception))
        # And the honest bundle passes the exact check.
        wins_exact = WalkForwardEngine._generate_windows(
            cfg, calendar=cal_full,
            data_coverage_start="2015-10-01",
            expected_first_session="2015-10-08")
        self.assertGreater(len(wins_exact), 19)

        # ADDITIVE, not a replacement (codex #412 r4): the manifest
        # coverage is the fetch's REQUESTED window, so a stamp can
        # claim 2015-10-01 while leading raw files are missing and the
        # built calendar starts years later. The calendar-gap refusal
        # must still fire even though a stamp exists and equals
        # overall_start - the previous revision disabled it whenever a
        # stamp was present, which let exactly this case through.
        cal_2018 = [date(2018, 1, 2) + timedelta(days=i)
                    for i in range(2600)]
        with self.assertRaises(WalkForwardError) as ctx_add:
            WalkForwardEngine._generate_windows(
                cfg, calendar=cal_2018,
                data_coverage_start="2015-10-01")
        self.assertIn("weekdays of history missing",
                      str(ctx_add.exception))

        # LEGACY branch (no stamp): the weekday tolerance stays as the
        # fallback (codex #412 r1) — a partially built bundle starting
        # 2015-10-20 sits only 19 calendar days after overall_start,
        # inside any holiday-sized fixed window, while genuinely
        # missing 13 weekdays of history. It must refuse too.
        cal_partial = [date(2015, 10, 20) + timedelta(days=i)
                       for i in range(3400)]
        with self.assertRaises(WalkForwardError) as ctx2:
            WalkForwardEngine._generate_windows(cfg, calendar=cal_partial)
        self.assertIn("weekdays of history missing", str(ctx2.exception))
        # The boundary itself (codex #412 r5): an UNSTAMPED calendar
        # starting 2015-10-12 gaps exactly 7 weekdays — the earlier
        # +1-slack threshold accepted it while the real 10-08/10-09
        # sessions were missing. The bound is the EXACT historical
        # maximum (6), so 7 refuses.
        cal_1012_legacy = [date(2015, 10, 12) + timedelta(days=i)
                           for i in range(3400)]
        with self.assertRaises(WalkForwardError) as ctx3:
            WalkForwardEngine._generate_windows(
                cfg, calendar=cal_1012_legacy)
        self.assertIn("weekdays of history missing", str(ctx3.exception))
        # A calendar that starts on the first session AT OR AFTER the
        # anchor (2015-10-08 — the National Day week has none; a
        # 5-weekday gap, within the 6-weekday worst closure) passes.
        cal_ok = [date(2015, 10, 8) + timedelta(days=i) for i in range(3400)]
        wins = WalkForwardEngine._generate_windows(cfg, calendar=cal_ok)
        self.assertGreater(len(wins), 19)

    def test_coverage_stamp_is_read_on_the_real_run_path(self) -> None:
        # codex #412 r3 - and the exact miss of the previous round: the
        # guard itself was verified end to end, but the RUN wiring read
        # provider_uri off WalkForwardConfig, which has no such field
        # (the CLI strips it into QlibRuntimeConfig), so real runs
        # always fell back to the legacy heuristic and the stamp was
        # never consulted. Pin all three sides.
        import inspect
        import unittest.mock as mock
        from dataclasses import fields

        from src.core.walk_forward import engine
        from src.core.walk_forward.config import WalkForwardConfig
        from src.core.walk_forward.engine import WalkForwardEngine

        # 1) The config REALLY has no provider_uri - if someone adds
        #    one, this pin forces them to reconcile the two sources.
        self.assertNotIn("provider_uri",
                         {f.name for f in fields(WalkForwardConfig)})
        # 2) run() resolves through the canonical qlib config, never
        #    off the config object.
        src = inspect.getsource(engine)
        self.assertIn("_read_coverage_stamp(",
                      src)
        self.assertIn("cls._resolve_provider_uri())", src)
        self.assertNotIn('getattr(config, "provider_uri"', src)
        # 3) Behaviour: the resolver returns the canonical URI when the
        #    runtime is initialized, None when it is not.
        class _Cfg:
            provider_uri = "D:/some/bundle"
        with mock.patch("src.core.qlib_runtime.get_canonical_qlib_config",
                        return_value=_Cfg()):
            self.assertEqual("D:/some/bundle",
                             WalkForwardEngine._resolve_provider_uri())
        with mock.patch("src.core.qlib_runtime.get_canonical_qlib_config",
                        return_value=None):
            self.assertIsNone(WalkForwardEngine._resolve_provider_uri())

    def test_malformed_coverage_stamp_refuses_actionably(self) -> None:
        # codex #412 r3 P2: a damaged/hand-edited stamp must surface as
        # a classified, actionable refusal - not a raw pd.Timestamp
        # parse traceback deep inside window generation.
        import json as _json
        import tempfile

        from src.core.walk_forward.config import WalkForwardError
        from src.core.walk_forward.engine import _read_coverage_stamp
        from src.data.pit.bundle_integrity import (
            BundleIntegrityError,
            read_bundle_integrity,
        )
        for bad in ("not-a-date", "2015-13-40", "20151001"):
            with tempfile.TemporaryDirectory() as d:
                stamp = {"schema_version": 1,
                         "built_from_holey_fetch": False,
                         "built_at": "2026-08-08T00:00:00+00:00",
                         "holes": [],
                         "data_coverage_start": bad}
                (Path(d) / "_fetch_integrity.json").write_text(
                    _json.dumps(stamp), encoding="utf-8")
                with self.assertRaises(BundleIntegrityError, msg=bad):
                    read_bundle_integrity(Path(d))
                # And the engine-side reader translates, not crashes.
                with self.assertRaises(WalkForwardError, msg=bad):
                    _read_coverage_stamp(d)

    def test_builder_cross_checks_stamp_against_built_calendar(self) -> None:
        # codex #412 r4: the builder must not stamp a coverage claim
        # the data does not honour. Shared-helper behaviour plus a
        # source pin that the builder actually calls it before
        # stamping (the full build path needs a real fixture tree; the
        # helper carries the logic, the pin carries the wiring).
        import inspect
        from datetime import date

        from src.data.pit import qlib_bin_builder
        from src.data.pit.bundle_integrity import (
            MAX_EXCHANGE_CLOSURE_WEEKDAYS,
            missing_weekdays_between,
        )
        # EXACT historical bound, zero slack (codex #412 r5): the
        # earlier +1 buffer was precisely where truncation could hide
        # (a 10-01 anchor with files missing through 10-09 gaps
        # exactly 7 weekdays and sailed through a >7 check).
        self.assertEqual(6, MAX_EXCHANGE_CLOSURE_WEEKDAYS)
        # Holiday-sized gap (National Day 2015): 5 weekdays - stampable.
        self.assertEqual(5, missing_weekdays_between(
            date(2015, 10, 1), date(2015, 10, 8)))
        # Missing leading files: far beyond any closure.
        self.assertEqual(13, missing_weekdays_between(
            date(2015, 10, 1), date(2015, 10, 20)))
        self.assertEqual(0, missing_weekdays_between(
            date(2015, 10, 8), date(2015, 10, 8)))
        src = inspect.getsource(qlib_bin_builder)
        self.assertIn("missing_weekdays_between(_cov, _first)", src)
        self.assertIn("> MAX_EXCHANGE_CLOSURE_WEEKDAYS", src)
        # And the refusal precedes the stamp write.
        self.assertLess(src.index("weekdays of "),
                        src.index("write_bundle_integrity("))

    def test_builder_derives_the_anchor_specific_first_session(self) -> None:
        # codex #412 r6: with the fetched exchange calendar on disk,
        # the builder derives the EXACT expected first session for the
        # coverage anchor and reconciles/stamps against it - no gap
        # heuristic. Behavioural on a synthetic trade_cal; wiring
        # pinned by source (exact-match refusal precedes the stamp).
        import inspect
        import tempfile

        import pandas as pd_

        from src.data.pit import qlib_bin_builder
        from src.data.pit.qlib_bin_builder import (
            QlibBinBuilderError,
            derive_expected_first_session,
        )
        rows = [{"exchange": "SSE", "cal_date": d, "is_open": o}
                for d, o in (("20151001", 0), ("20151002", 0),
                             ("20151005", 0), ("20151006", 0),
                             ("20151007", 0), ("20151008", 1),
                             ("20151009", 1), ("20151012", 1))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd_.DataFrame(rows).to_parquet(root / "trade_cal.parquet",
                                           index=False)
            self.assertEqual(
                "2015-10-08",
                derive_expected_first_session(root, "2015-10-01"))
            self.assertEqual(
                "2015-10-09",
                derive_expected_first_session(root, "2015-10-09"))
            # Anchor beyond the calendar tail: no evidence, legacy.
            self.assertIsNone(
                derive_expected_first_session(root, "2026-01-01"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No trade_cal at all (legacy dump): None, not an error.
            self.assertIsNone(
                derive_expected_first_session(root, "2015-10-01"))
            # A malformed calendar is corrupt input, never legacy.
            pd_.DataFrame([{"wrong": 1}]).to_parquet(
                root / "trade_cal.parquet", index=False)
            with self.assertRaises(QlibBinBuilderError):
                derive_expected_first_session(root, "2015-10-01")
        src = inspect.getsource(qlib_bin_builder)
        self.assertIn("derive_expected_first_session(", src)
        self.assertLess(src.index("calendar[0] != expected_first_session"),
                        src.index("write_bundle_integrity("))

    def test_universe_and_scope(self) -> None:
        self.assertEqual("csi800", _PLAN["universe"]["instruments"])
        self.assertIs(False, _PLAN["universe"]["ex_financials"])
        self.assertIs(
            False, _PLAN["promotion"]["production_wiring_in_scope"])


if __name__ == "__main__":
    unittest.main()

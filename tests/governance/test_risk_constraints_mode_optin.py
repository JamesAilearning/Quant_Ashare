"""Governance: ``risk_constraints_mode`` is a narrow, pinned opt-in.

``WARN_AND_CLIP`` exists so a run whose PURPOSE is exporting
out-of-fold predictions is not killed by a post-trade portfolio
violation that has nothing to do with them. Returns computed under a
clipped allocation are NOT the RAISE-validated ones, so the escape
hatch must never become the way official metrics get past the cap:
these pins keep the default at RAISE, reject unknown values on both
engines, and enumerate exactly which tracked presets may declare it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# The ONLY tracked presets allowed to relax the reaction. Adding a name
# here is a deliberate governance act, reviewed as such.
_ALLOWED_WARN_AND_CLIP = {"pv_incremental_baseline.yaml"}


def _bare_request() -> object:
    """A valid request, borrowed from the runner's own test helper —
    the guard under test runs right after input validation, so the
    request must pass that first."""
    from tests.logic.test_backtest_runner import _make_request
    return _make_request()


def _wf_fold(status, idx=0):
    """A WalkForwardFold with a valid test_period for the aggregator."""
    from src.core.walk_forward._types import WalkForwardFold
    return WalkForwardFold(
        fold_index=idx,
        train_period="2020-01-01 ~ 2021-12-31",
        valid_period="2022-01-01 ~ 2022-06-30",
        test_period=f"202{3 + idx}-01-01 ~ 202{3 + idx}-03-31",
        ic_1d=0.01, ic_5d=0.02, annualized_return=0.1,
        max_drawdown=-0.1, information_ratio=0.5,
        prediction_shape=(10,), metric_status=status)


class RiskConstraintsModeOptIn(unittest.TestCase):
    def test_default_is_raise_on_both_engines(self) -> None:
        # Two engines, one schema: a default that drifted on one side
        # would silently change the other's semantics at the next
        # config copy.
        from src.core.pipeline import PipelineConfig
        from src.core.walk_forward.config import WalkForwardConfig
        for cls in (PipelineConfig, WalkForwardConfig):
            field = cls.__dataclass_fields__["risk_constraints_mode"]
            self.assertEqual("raise", field.default, cls.__name__)

    def test_unknown_mode_is_rejected_on_both_engines(self) -> None:
        from src.core.pipeline import PipelineConfig, PipelineError
        from src.core.walk_forward.config import (
            WalkForwardConfig,
            WalkForwardError,
        )
        with self.assertRaises(PipelineError) as ctx:
            PipelineConfig(provider_uri="x", risk_constraints_mode="clip")
        self.assertIn("risk_constraints_mode", str(ctx.exception))
        with self.assertRaises(WalkForwardError) as ctx2:
            WalkForwardConfig(risk_constraints_mode="clip")
        self.assertIn("risk_constraints_mode", str(ctx2.exception))

    def test_mode_reaches_the_constraints_object(self) -> None:
        # Recording the field without threading it would look exactly
        # like a working opt-in while every fold still aborted.
        import inspect

        from src.core import pipeline
        from src.core.walk_forward import engine
        for mod in (pipeline, engine):
            src = inspect.getsource(mod)
            self.assertIn(
                "mode=RiskConstraintMode(config.risk_constraints_mode)",
                src, mod.__name__)

    def test_runtime_boundary_refuses_official_warn_and_clip(self) -> None:
        # codex #406 r1: the preset scan below cannot protect the other
        # entry paths — personal overrides (my_*.yaml / *.local.yaml)
        # are gitignored BY DESIGN, and tests / replay scripts /
        # single-fold callers construct the runner directly. The
        # refusal therefore lives at the boundary every path crosses.
        import inspect

        from src.core.backtest_runner import BacktestRunner
        src = inspect.getsource(BacktestRunner.run)
        self.assertIn("metrics_purpose", src)
        self.assertIn("RiskConstraintMode.WARN_AND_CLIP", src)
        # The refusal must be BEFORE any metric is computed.
        refusal = src.index("metrics_purpose != \"predictions_only\"")
        self.assertLess(refusal, src.index("positions_map ="))
        # And the signature must default to the strict purpose: a
        # caller that says nothing must not get the tolerant path.
        sig = inspect.signature(BacktestRunner.run)
        self.assertEqual("official",
                         sig.parameters["metrics_purpose"].default)

    def test_warn_and_clip_refused_without_declared_purpose(self) -> None:
        # Behavioural, not just source-shaped: the guard must fire on a
        # real call. It sits ahead of any data access, so a bare
        # request object is enough to reach it.
        from dataclasses import replace

        from src.core.backtest_runner import (
            BacktestRunner,
            BacktestRunnerError,
        )
        from src.core.risk_constraints import (
            RiskConstraintMode,
            campaign_risk_constraints_v1,
        )
        clip = replace(campaign_risk_constraints_v1(),
                       mode=RiskConstraintMode.WARN_AND_CLIP)
        with self.assertRaises(BacktestRunnerError) as ctx:
            BacktestRunner.run(request=_bare_request(),
                               predictions="dummy",
                               risk_constraints=clip)
        self.assertIn("WARN_AND_CLIP", str(ctx.exception))
        self.assertIn("predictions_only", str(ctx.exception))

    def test_purpose_is_threaded_and_recorded_by_both_engines(self) -> None:
        # Refusing at the boundary is only half of it: the purpose must
        # travel WITH the run, or a tolerant run's numbers still read
        # as canonical at rest.
        import inspect

        from src.core import pipeline
        from src.core.walk_forward import engine
        for mod in (pipeline, engine):
            src = inspect.getsource(mod)
            self.assertIn("metrics_purpose=config.metrics_purpose",
                          src, mod.__name__)
            # NEVER derived (codex #406 r2). The previous revision
            # computed the purpose FROM risk_constraints_mode, which
            # handed the guard's key to the very switch it guards:
            # selecting the relaxed mode auto-granted the escape, so a
            # private YAML needed only one line to bypass the boundary
            # check entirely.
            self.assertNotIn('metrics_purpose=("predictions_only"',
                             src, mod.__name__)
        # The pipeline report projects its config BY HAND (walk-forward
        # gets it free via asdict) — codex #406 r2.
        psrc = inspect.getsource(pipeline)
        self.assertIn('"risk_constraints_mode": config.risk_constraints_mode',
                      psrc)
        self.assertIn('"metrics_purpose": config.metrics_purpose', psrc)

    def test_purpose_is_an_independent_declaration(self) -> None:
        # Two engines: the relaxed reaction alone must NOT be enough.
        from src.core.pipeline import PipelineConfig, PipelineError
        from src.core.walk_forward.config import (
            WalkForwardConfig,
            WalkForwardError,
        )
        for cls, err, extra in ((PipelineConfig, PipelineError,
                                 {"provider_uri": "x"}),
                                (WalkForwardConfig, WalkForwardError, {})):
            field = cls.__dataclass_fields__["metrics_purpose"]
            self.assertEqual("official", field.default, cls.__name__)
            with self.assertRaises(err, msg=cls.__name__):
                cls(risk_constraints_mode="warn_and_clip", **extra)
            with self.assertRaises(err, msg=cls.__name__):
                cls(metrics_purpose="research", **extra)
            # Both declared together is the only way through.
            cfg = cls(risk_constraints_mode="warn_and_clip",
                      metrics_purpose="predictions_only", **extra)
            self.assertEqual("predictions_only", cfg.metrics_purpose)

    def test_relaxed_runs_are_not_labelled_official(self) -> None:
        # codex #406 r2: refusing un-declared callers is half of it —
        # a DECLARED predictions-only run still returned
        # metric_status="official", which pipeline_report, the result
        # artifacts and the walk-forward aggregate all copy verbatim.
        import inspect

        from src.core.backtest_runner import BacktestRunner
        from src.core.canonical_backtest_contract import (
            OFFICIAL_METRIC_STATUS,
            PREDICTIONS_ONLY_METRIC_STATUS,
        )
        self.assertNotEqual(OFFICIAL_METRIC_STATUS,
                            PREDICTIONS_ONLY_METRIC_STATUS)
        src = inspect.getsource(BacktestRunner.run)
        self.assertIn("PREDICTIONS_ONLY_METRIC_STATUS", src)
        self.assertIn('metrics_purpose == "predictions_only"', src)
        # And the purpose is readable from the artifact alone —
        # as a SIBLING of risk_constraints, never inside it (r5).
        self.assertIn("metrics_purpose=metrics_purpose", src)

    def test_both_engine_reports_record_the_purpose(self) -> None:
        # codex #406 r2: walk-forward gets it via asdict(config) ONLY
        # if it is a real config field; the pipeline projects by hand.
        # A purpose recorded on one engine and absent on the other
        # leaves the baseline artifact without its claimed marker.
        import inspect
        from dataclasses import fields

        from src.core import pipeline
        from src.core.walk_forward.config import WalkForwardConfig
        self.assertIn("metrics_purpose",
                      {f.name for f in fields(WalkForwardConfig)})
        self.assertIn('"metrics_purpose": config.metrics_purpose',
                      inspect.getsource(pipeline))

    def test_status_travels_to_aggregate_and_catalog(self) -> None:
        # codex #406 r3: stamping each fold is not enough. The
        # per-fold status was DISCARDED when building WalkForwardFold,
        # so build_aggregate_report published raw aggregate_metrics
        # and the catalog wrote output/runs/_index.jsonl as an
        # ordinary status="ok" record — RAISE-refused returns sitting
        # next to official ones, indistinguishable to run comparison.
        import inspect
        from dataclasses import fields

        from src.core import pipeline
        from src.core.run_catalog import build_record
        from src.core.walk_forward import engine
        from src.core.walk_forward._types import WalkForwardFold

        # The fold carries it.
        self.assertIn("metric_status",
                      {f.name for f in fields(WalkForwardFold)})
        # Source-shaped, and that is its LIMIT (codex #406 r4): it
        # proves the wiring exists, never that the artifact carries
        # the value. test_report_actually_carries_the_status above is
        # the one that checks the output.
        self.assertIn("metric_status=backtest_output.metric_status",
                      inspect.getsource(engine))

        # The catalog schema carries BOTH, on both engines.
        rec = build_record(engine="walk_forward", status="ok")
        self.assertIn("metric_status", rec)
        self.assertIn("metrics_purpose", rec)
        relaxed = build_record(
            engine="walk_forward", status="ok",
            metric_status="predictions_only_non_canonical",
            metrics_purpose="predictions_only")
        self.assertEqual("predictions_only_non_canonical",
                         relaxed["metric_status"])
        for mod in (pipeline, engine):
            self.assertIn("metrics_purpose=config.metrics_purpose",
                          inspect.getsource(mod), mod.__name__)

    def test_failed_fold_is_never_stamped_official(self) -> None:
        # codex #406 r7 — and this one I created in r3: stamping the
        # NaN placeholder with the DECLARED purpose kept ``None`` from
        # reading as official, but under an official run it asserted
        # the opposite, marking a fold that never reached the
        # canonical boundary as having passed it. A failed fold has no
        # metrics, so it is evidence of nothing.
        import inspect

        from src.core.canonical_backtest_contract import (
            FAILED_METRIC_STATUS,
            OFFICIAL_METRIC_STATUS,
            PREDICTIONS_ONLY_METRIC_STATUS,
        )
        from src.core.walk_forward import engine
        from src.core.walk_forward.aggregate import build_aggregate_report
        from src.core.walk_forward.config import WalkForwardConfig
        src = inspect.getsource(engine)
        self.assertIn("metric_status=FAILED_METRIC_STATUS", src)
        self.assertNotIn("metric_status=_run_metric_status(config)", src)

        # Behavioural: a failed fold neither certifies nor taints.
        fail = _wf_fold(FAILED_METRIC_STATUS, 0)
        ok = _wf_fold(OFFICIAL_METRIC_STATUS, 1)
        official_cfg = WalkForwardConfig()
        # Surviving official folds still make an (partial) official run.
        self.assertEqual(
            OFFICIAL_METRIC_STATUS,
            build_aggregate_report(
                config=official_cfg, folds=[fail, ok],
                aggregate_metrics={})["metric_status"])
        # But a run where NOTHING was measured cannot claim more than
        # its declared purpose.
        self.assertEqual(
            PREDICTIONS_ONLY_METRIC_STATUS,
            build_aggregate_report(
                config=WalkForwardConfig(
                    risk_constraints_mode="warn_and_clip",
                    metrics_purpose="predictions_only"),
                folds=[fail], aggregate_metrics={})["metric_status"])
        # And the failed fold says so in the report itself.
        rep = build_aggregate_report(config=official_cfg, folds=[fail],
                                     aggregate_metrics={})
        self.assertEqual(FAILED_METRIC_STATUS,
                         rep["folds"][0]["metric_status"])

    def test_report_actually_carries_the_status(self) -> None:
        # codex #406 r4, and the lesson of this whole PR: the r3 pins
        # asserted the SOURCE TEXT and passed while the field never
        # reached walk_forward_report.json — build_aggregate_report
        # projects WalkForwardFold BY HAND. Assert on the produced
        # artifact, not on the code that is supposed to produce it.
        from src.core.canonical_backtest_contract import (
            OFFICIAL_METRIC_STATUS,
            PREDICTIONS_ONLY_METRIC_STATUS,
        )
        from src.core.walk_forward._types import WalkForwardFold
        from src.core.walk_forward.aggregate import build_aggregate_report
        from src.core.walk_forward.config import WalkForwardConfig

        def _fold(status: str | None,
                  idx: int = 0) -> WalkForwardFold:
            return WalkForwardFold(
                fold_index=idx,
                train_period="2020-01-01 ~ 2021-12-31",
                valid_period="2022-01-01 ~ 2022-06-30",
                test_period=f"202{3 + idx}-01-01 ~ 202{3 + idx}-03-31",
                ic_1d=0.01, ic_5d=0.02,
                annualized_return=0.1, max_drawdown=-0.1,
                information_ratio=0.5, prediction_shape=(10,),
                metric_status=status)

        relaxed = WalkForwardConfig(
            risk_constraints_mode="warn_and_clip",
            metrics_purpose="predictions_only")
        report = build_aggregate_report(
            config=relaxed, folds=[_fold(PREDICTIONS_ONLY_METRIC_STATUS)],
            aggregate_metrics={"mean_ic_1d": 0.01})
        self.assertEqual(PREDICTIONS_ONLY_METRIC_STATUS,
                         report["metric_status"])
        self.assertEqual("predictions_only", report["metrics_purpose"])
        self.assertEqual(PREDICTIONS_ONLY_METRIC_STATUS,
                         report["folds"][0]["metric_status"])

        official = build_aggregate_report(
            config=WalkForwardConfig(),
            folds=[_fold(OFFICIAL_METRIC_STATUS)],
            aggregate_metrics={"mean_ic_1d": 0.01})
        self.assertEqual(OFFICIAL_METRIC_STATUS, official["metric_status"])

        # One non-official fold taints the run: mixing must not launder
        # the label.
        mixed = build_aggregate_report(
            config=WalkForwardConfig(),
            folds=[_fold(OFFICIAL_METRIC_STATUS, 0),
                   _fold(PREDICTIONS_ONLY_METRIC_STATUS, 1)],
            aggregate_metrics={"mean_ic_1d": 0.01})
        self.assertEqual(PREDICTIONS_ONLY_METRIC_STATUS,
                         mixed["metric_status"])

        # An UNSTAMPED fold under a relaxed run must not read official.
        legacy = build_aggregate_report(
            config=relaxed, folds=[_fold(None)],
            aggregate_metrics={"mean_ic_1d": 0.01})
        self.assertEqual(PREDICTIONS_ONLY_METRIC_STATUS,
                         legacy["metric_status"])

    def test_purpose_never_pollutes_the_constraint_mapping(self) -> None:
        # codex #406 r5: the campaign veto compares
        # provenance.config.risk_constraints by EXACT equality against
        # the five calibration values, so ONE extra key there fails
        # veto 4 on every newly generated csi800 fold and blocks
        # promotion. Behavioural: build the real provenance and read
        # the real mapping, and bind it to the veto's own expectation
        # so the two cannot drift apart silently.
        import sys as _sys
        from pathlib import Path as _Path

        from src.core.backtest_runner import BacktestRunner
        from src.core.risk_constraints import campaign_risk_constraints_v1

        root = _Path(__file__).resolve().parents[2]
        if str(root) not in _sys.path:
            _sys.path.insert(0, str(root))
        from scripts.research.csi800_campaign_attach_vetoes import (
            CAMPAIGN_V1_EXPECTED,
        )
        c = campaign_risk_constraints_v1()
        rc = {"max_per_name": c.max_per_name,
              "max_per_board": c.max_per_board,
              "cash_buffer_min": c.cash_buffer_min,
              "max_leverage": c.max_leverage,
              "mode": c.mode.value}
        prov = BacktestRunner._build_provenance(
            _bare_request(), topk=50, n_drop=5,
            risk_constraints=rc, metrics_purpose="predictions_only")
        cfg = prov["config"]
        # EXACTLY the calibration schema the veto expects.
        self.assertEqual(CAMPAIGN_V1_EXPECTED, cfg["risk_constraints"])
        # …and the purpose is still readable, as a sibling.
        self.assertEqual("predictions_only", cfg["metrics_purpose"])
        # Recorded for official runs too — absence must not be the
        # only signal.
        prov_official = BacktestRunner._build_provenance(
            _bare_request(), topk=50, n_drop=5, risk_constraints=rc)
        self.assertEqual("official",
                         prov_official["config"]["metrics_purpose"])
        self.assertEqual(CAMPAIGN_V1_EXPECTED,
                         prov_official["config"]["risk_constraints"])

    def test_only_the_baseline_preset_relaxes_the_reaction(self) -> None:
        # The leak guard: an official-metrics preset that quietly
        # adopted warn_and_clip would publish returns the RAISE
        # validation never saw.
        offenders = []
        for preset in sorted(
                (_PROJECT_ROOT / "config" / "presets").glob("*.yaml")):
            raw = yaml.safe_load(preset.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            if raw.get("risk_constraints_mode") == "warn_and_clip" \
                    and preset.name not in _ALLOWED_WARN_AND_CLIP:
                offenders.append(preset.name)
        self.assertEqual([], offenders)

    def test_the_baseline_preset_actually_declares_it(self) -> None:
        # The other direction: if the campaign preset loses the opt-in,
        # the run silently goes back to discarding whole quarters of
        # baseline predictions on a 0.01pp drift.
        raw = yaml.safe_load(
            (_PROJECT_ROOT / "config" / "presets"
             / "pv_incremental_baseline.yaml").read_text(encoding="utf-8"))
        self.assertEqual("warn_and_clip", raw["risk_constraints_mode"])
        # Independently declared, never derived.
        self.assertEqual("predictions_only", raw["metrics_purpose"])
        # And it stays production-equivalent everywhere else.
        self.assertIs(True, raw["risk_constraints_enabled"])
        self.assertEqual("campaign_v1", raw["risk_constraints_calibration"])

    def test_campaign_calibration_limits_are_untouched(self) -> None:
        # The opt-in changes the REACTION, never the limits — relaxing
        # those would be a different (and much larger) decision.
        from src.core.risk_constraints import (
            RiskConstraintMode,
            campaign_risk_constraints_v1,
        )
        c = campaign_risk_constraints_v1()
        self.assertEqual(0.05, c.max_per_name)
        self.assertEqual(1.0, c.max_leverage)
        self.assertIs(RiskConstraintMode.RAISE, c.mode)


if __name__ == "__main__":
    unittest.main()

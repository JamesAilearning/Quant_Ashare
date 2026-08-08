"""Governance: the two engine configs reject the SAME bad values.

"Two engines, one schema" (AGENTS.md) is usually read as a report-key rule,
but the same asymmetry hides in VALIDATION: a field both configs carry can
be rejected at construction by one engine and only much later — deep inside
a run, at the canonical contract boundary — by the other. The value never
escapes validation, but the operator waits through feature build + model
train + predict to learn about a typo the other engine catches instantly.
That asymmetry existed (``init_cash`` pipeline-only, ``adjust_mode``
walk-forward-only) until the shared validators in
``src/core/_shared_validators.py``.

This test pins the symmetry BEHAVIORALLY: for each shared field, a value
known to be invalid must be rejected by BOTH configs at construction. It
is deliberately value-driven rather than source-text driven, so a future
refactor that moves the checks again cannot make it pass vacuously.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.pipeline import PipelineConfig, PipelineError  # noqa: E402
from src.core.walk_forward import WalkForwardConfig  # noqa: E402
from src.core.walk_forward.config import WalkForwardError  # noqa: E402

# (field, bad value, substring the message must name). Every entry is a
# field BOTH configs declare — the shared-surface contract.
_BAD_VALUES: tuple[tuple[str, object, str], ...] = (
    ("init_cash", 0, "init_cash"),
    ("init_cash", -1.0, "init_cash"),
    ("limit_threshold", 0.0, "limit_threshold"),
    ("limit_threshold", 0.9, "limit_threshold"),
    ("adjust_mode", "sideways_adjusted", "adjust_mode"),
    ("execution_price_kind", "twap", "execution_price_kind"),
    ("commission_rate", -0.1, "commission_rate"),
    ("min_cost", -1.0, "min_cost"),
    ("slippage_bps", -5.0, "slippage_bps"),
    ("signal_to_execution_lag", 0, "signal_to_execution_lag"),
    ("signal_to_execution_lag", True, "signal_to_execution_lag"),
    ("label_horizon_days", 0, "label_horizon_days"),
    ("label_horizon_days", True, "label_horizon_days"),
    ("compute_device", "tpu", "compute_device"),
    ("topk", 0, "topk"),
    ("n_drop", -1, "n_drop"),
    ("metrics_purpose", "whatever", "metrics_purpose"),
    ("risk_constraints_mode", "ignore", "risk_constraints_mode"),
    ("risk_constraints_calibration", "campaign_v2",
     "risk_constraints_calibration"),
)

_ENGINES = (
    (PipelineConfig, PipelineError, {"provider_uri": "x"}),
    (WalkForwardConfig, WalkForwardError, {}),
)


class ConfigValidationParityTests(unittest.TestCase):
    def test_both_engines_reject_the_same_bad_values(self) -> None:
        for field, bad, must_name in _BAD_VALUES:
            for cls, error_class, base in _ENGINES:
                with self.subTest(field=field, value=bad, engine=cls.__name__):
                    with self.assertRaises(error_class) as ctx:
                        cls(**base, **{field: bad})
                    self.assertIn(
                        must_name, str(ctx.exception),
                        f"{cls.__name__} rejected {field}={bad!r} but the "
                        "message does not name the field, so the operator "
                        "cannot tell which config key to fix",
                    )

    def test_csi800_guard_triple_enforced_on_both_engines(self) -> None:
        # The guard is an interlock, not a single-field check: csi800
        # without the sleeve report + campaign calibration is refused.
        for cls, error_class, base in _ENGINES:
            with self.subTest(engine=cls.__name__):
                with self.assertRaises(error_class) as ctx:
                    cls(**base, instruments="csi800")
                self.assertIn("csi800", str(ctx.exception))

    def test_warn_and_clip_interlock_enforced_on_both_engines(self) -> None:
        # warn_and_clip tolerates violations, so it is legal ONLY when the
        # run declares predictions_only — on both engines.
        for cls, error_class, base in _ENGINES:
            with self.subTest(engine=cls.__name__):
                with self.assertRaises(error_class) as ctx:
                    cls(**base, risk_constraints_enabled=True,
                        risk_constraints_mode="warn_and_clip",
                        metrics_purpose="official")
                self.assertIn("warn_and_clip", str(ctx.exception))
                # ...and accepted with the declared purpose.
                cls(**base, risk_constraints_enabled=True,
                    risk_constraints_mode="warn_and_clip",
                    metrics_purpose="predictions_only")

    def test_sleeve_grouping_interlocks_enforced_on_both_engines(self) -> None:
        for cls, error_class, base in _ENGINES:
            with self.subTest(engine=cls.__name__):
                with self.assertRaises(error_class):
                    cls(**base, attribution_sleeve_grouping=True,
                        industry_artifact_path="x.parquet")
                with self.assertRaises(error_class):
                    cls(**base, attribution_sleeve_grouping=True,
                        run_attribution=False)

    def test_good_defaults_construct_on_both_engines(self) -> None:
        # The negative cases above are only meaningful if the positive
        # case still passes — a validator that rejects everything would
        # satisfy every assertion above.
        for cls, _error_class, base in _ENGINES:
            with self.subTest(engine=cls.__name__):
                cls(**base)


if __name__ == "__main__":
    unittest.main()

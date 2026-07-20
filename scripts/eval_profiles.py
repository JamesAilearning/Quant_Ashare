"""Pre-registered eval profiles for the frozen-model OOS guard tool
(2026-07-20-csi800-n5-production-promotion PR-B, DP-3; codex #387 r1).

PURE stdlib module — deliberately free of pandas/qlib imports so the
governance pins (tests/governance/test_csi800_n5_production_serving.py)
can assert the knob sets without dragging the qlib-bound eval module
onto the import path.

A profile is the pre-registered semantic knob set for one promotion
family — not operator-tunable per run (overriding any value means a
different gate and needs a new OpenSpec change):

* ``csi300_daily`` — the legacy ④ profile: byte-identical to the eval
  tool's historical behaviour (csi300 / SH000300TR / daily rebalance /
  replay slippage / no risk constraints). ``slippage_bps`` here MUST
  equal ``scripts.regen.replay_frozen_baseline.SLIPPAGE_BPS`` — that
  module is qlib-bound, so the equality is cross-pinned by the
  qlib-gated tests/logic/test_eval_frozen_model_oos.py rather than
  imported here.
* ``csi800_n5`` — the certified-winner production-guard semantics:
  csi800 / SH000906TR / N=5 iso_week cadence / rebalance_days
  constraint scoping / campaign_v1 constraints / 20 bps slippage.
"""

from __future__ import annotations

from typing import Any

EVAL_PROFILES: dict[str, dict[str, Any]] = {
    "csi300_daily": {
        "instruments": "csi300",
        "benchmark_code": "SH000300TR",
        "slippage_bps": 5.0,
        "rebalance_cadence_days": 1,
        "rebalance_phase": 0,
        "rebalance_anchor": "fold_phase",
        "risk_constraint_scope": "all_days",
        "campaign_constraints": False,
    },
    "csi800_n5": {
        "instruments": "csi800",
        "benchmark_code": "SH000906TR",
        "slippage_bps": 20.0,
        "rebalance_cadence_days": 5,
        "rebalance_phase": 0,
        "rebalance_anchor": "iso_week",
        "risk_constraint_scope": "rebalance_days",
        "campaign_constraints": True,
    },
}


def resolve_profile(name: str) -> dict[str, Any]:
    """Return the pre-registered knob set for ``name`` (pure; testable)."""
    if name not in EVAL_PROFILES:
        raise ValueError(
            f"unknown eval profile {name!r}; valid: "
            f"{sorted(EVAL_PROFILES)}")
    return dict(EVAL_PROFILES[name])

"""Unit tests for the CSI800 veto attach tool (checks 2-5).

Coverage matrix (>=1 case per dimension):
  clean attach     — all five checks present, none triggered, COMPLETE +
                     promotion-eligible; turnover ratio ~1.
  evidence binding — a run dir whose aggregate differs from the certified
                     pair entry refuses (report_sha256 mismatch).
  sleeve coverage  — a conservative fold with non-ok attribution triggers
                     checks 2 and 5 (fail closed), never passes them.
  positions cover  — a missing csi800 positions artifact triggers check 3
                     with the problem listed.
  ref integrity    — missing reference positions on a fold the aggregate
                     documents as OFFICIAL refuses (torn artifact);
                     a documented-failed fold (report_path null) is
                     allowed and disclosed via ref_failed_folds.
  ref binding      — a reference whose config drifts beyond the pinned
                     {instruments, benchmark_code,
                     attribution_sleeve_grouping} diff refuses.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.csi800_campaign_attach_vetoes import (  # noqa: E402
    CAMPAIGN_V1_EXPECTED,
    attach,
)
from scripts.research.csi800_campaign_pair_report import (  # noqa: E402
    build_pair_report,
)

_CAMPAIGN_CFG = {
    "instruments": "csi800",
    "benchmark_code": "SH000906TR",
    "topk": 50,
    "n_drop": 5,
    "slippage_bps": 5.0,
    "attribution_sleeve_grouping": True,
    "risk_constraints_enabled": True,
    "risk_constraints_calibration": "campaign_v1",
    "output_dir": "output/walk_forward/base",
}

_SLEEVE_ROWS = [
    {"sector": "csi500_sleeve", "portfolio_weight": 0.45,
     "total_effect": 0.010},
    {"sector": "csi300_sleeve", "portfolio_weight": 0.55,
     "total_effect": 0.008},
    {"sector": "unknown", "portfolio_weight": 0.0,
     "total_effect": -0.001},
]


def _positions(shift: float = 0.0) -> dict[str, dict[str, float]]:
    # three days, two instruments, small daily rebalance -> nonzero
    # turnover identical across runs (ratio ~1).
    return {
        "2024-01-02": {"SH600000": 0.5 + shift, "SZ000001": 0.5 - shift},
        "2024-01-03": {"SH600000": 0.45 + shift, "SZ000001": 0.55 - shift},
        "2024-01-04": {"SH600000": 0.5 + shift, "SZ000001": 0.5 - shift},
    }


def _mk_campaign_run(root: Path, name: str, cfg: dict,
                     mean_net: float, n_folds: int = 2) -> Path:
    d = root / name
    d.mkdir(parents=True)
    folds = []
    for i in range(n_folds):
        (d / f"fold_{i:02d}_report.json").write_text(json.dumps({
            "fold_index": i,
            "backtest": {
                "metric_status": "official",
                "provenance": {
                    "config": {"risk_constraints": dict(CAMPAIGN_V1_EXPECTED)},
                },
            },
            "attribution": {
                "status": "ok",
                "sector_attribution": _SLEEVE_ROWS,
                "sector_effects_sum": sum(
                    r["total_effect"] for r in _SLEEVE_ROWS),
            },
        }), encoding="utf-8")
        (d / f"fold_{i:02d}_positions.json").write_text(
            json.dumps(_positions()), encoding="utf-8")
        folds.append({"fold_index": i, "annualized_return": mean_net,
                      "report_path": f"fold_{i:02d}_report.json"})
    (d / "walk_forward_report.json").write_text(json.dumps({
        "config": cfg,
        "folds": folds,
        "aggregate_metrics": {"mean_annualized_return": mean_net,
                              "mean_information_ratio": 0.1,
                              "worst_drawdown": -0.05},
        "num_folds": n_folds,
    }), encoding="utf-8")
    return d


def _mk_reference_run(root: Path, cfg_over: dict | None = None,
                      n_folds: int = 2,
                      failed_folds: tuple[int, ...] = ()) -> Path:
    cfg = {**_CAMPAIGN_CFG, "instruments": "csi300",
           "benchmark_code": "SH000300TR",
           "attribution_sleeve_grouping": False,
           "output_dir": "output/walk_forward/ref"}
    cfg.update(cfg_over or {})
    d = root / "ref"
    d.mkdir(parents=True)
    folds = []
    for i in range(n_folds):
        if i in failed_folds:
            folds.append({"fold_index": i, "annualized_return": None,
                          "report_path": None})
            continue
        (d / f"fold_{i:02d}_positions.json").write_text(
            json.dumps(_positions()), encoding="utf-8")
        folds.append({"fold_index": i, "annualized_return": 0.01,
                      "report_path": f"fold_{i:02d}_report.json"})
    (d / "walk_forward_report.json").write_text(json.dumps({
        "config": cfg,
        "folds": folds,
        "aggregate_metrics": {"mean_annualized_return": 0.01},
        "num_folds": n_folds,
    }), encoding="utf-8")
    return d


def _mk_trio(root: Path, cons_net: float = 0.02, **ref_kwargs):
    base = _mk_campaign_run(root, "base", _CAMPAIGN_CFG, mean_net=0.05)
    cons = _mk_campaign_run(
        root, "cons",
        {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
         "output_dir": "output/walk_forward/cons"},
        mean_net=cons_net)
    ref = _mk_reference_run(root, **ref_kwargs)
    pair_p = root / "pair.json"
    pair_p.write_text(json.dumps(build_pair_report(base, cons)),
                      encoding="utf-8")
    return pair_p, base, cons, ref


def test_clean_attach_completes_and_is_eligible():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=0.02)
        r = attach(pair_p, base, cons, ref)
        vc = r["veto_checklist"]
        assert all(isinstance(vc[k], dict) and vc[k]["veto_triggered"]
                   is False for k in vc)
        assert r["veto_checklist_status"] == "COMPLETE"
        assert r["promotion_eligible"] is True
        c3 = vc["3_turnover_vs_csi300_ref"]
        assert c3["conservative_over_reference_ratio"] == pytest.approx(1.0)
        assert c3["coverage_problems"] == []
        assert c3["ref_failed_folds"] == []
        # rewritten in place
        assert json.loads(pair_p.read_text(encoding="utf-8"))[
            "promotion_eligible"] is True


def test_tampered_run_dir_refuses_binding():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        # mutate the conservative aggregate AFTER pairing: report_sha256
        # no longer matches the certified entry.
        wf = cons / "walk_forward_report.json"
        payload = json.loads(wf.read_text(encoding="utf-8"))
        payload["aggregate_metrics"]["mean_annualized_return"] = 0.99
        wf.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit,
                           match="does not match the certified"):
            attach(pair_p, base, cons, ref)


def test_partial_sleeve_attribution_fails_closed():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        rep_p = cons / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload["attribution"] = {"status": "skipped_no_data"}
        rep_p.write_text(json.dumps(payload), encoding="utf-8")
        r = attach(pair_p, base, cons, ref)
        vc = r["veto_checklist"]
        assert vc["2_csi500_dependence"]["veto_triggered"] is True
        assert "fail closed" in vc["2_csi500_dependence"]["note"]
        assert vc["5_midcap_concentration"]["veto_triggered"] is True
        # all five PRESENT -> COMPLETE, but triggered -> not eligible
        assert r["veto_checklist_status"] == "COMPLETE"
        assert r["promotion_eligible"] is False


def test_missing_csi800_positions_triggers_turnover_veto():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        (cons / "fold_01_positions.json").unlink()
        r = attach(pair_p, base, cons, ref)
        c3 = r["veto_checklist"]["3_turnover_vs_csi300_ref"]
        assert c3["veto_triggered"] is True
        assert any("conservative: fold 1" in p
                   for p in c3["coverage_problems"])
        assert r["promotion_eligible"] is False


def test_reference_torn_positions_refuses():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        # fold 1 is documented OFFICIAL in the ref aggregate, yet its
        # positions artifact is gone -> torn evidence, refuse.
        (ref / "fold_01_positions.json").unlink()
        with pytest.raises(SystemExit, match="torn positions"):
            attach(pair_p, base, cons, ref)


def test_reference_documented_failed_fold_is_disclosed():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), failed_folds=(1,))
        r = attach(pair_p, base, cons, ref)
        c3 = r["veto_checklist"]["3_turnover_vs_csi300_ref"]
        assert c3["veto_triggered"] is False
        assert c3["ref_failed_folds"] == [1]
        assert c3["ref_valid_folds"] == 1.0


def test_reference_config_drift_refuses():
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t),
                                           cfg_over={"topk": 60})
        with pytest.raises(SystemExit, match="expected exactly"):
            attach(pair_p, base, cons, ref)

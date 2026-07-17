"""Unit tests for the CSI800 paired sensitivity-band report tool.

Coverage matrix (>=1 case per dimension):
  clean pair      — projected diff exactly slippage_bps(5->20), report
                    carries both run ids + veto-1 verdict.
  projection      — differing output_dir (run-identity) does NOT refuse.
  semantic drift  — a non-whitelisted field differing refuses.
  band tampering  — conservative slippage != 20 refuses (DP-2).
  missing side    — absent conservative dir refuses (invalid, not
                    pending).
  identity        — non-csi800 universe / wrong benchmark refuses;
                    non-official metric_status refuses.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.csi800_campaign_pair_report import (  # noqa: E402
    PairReportError,
    build_pair_report,
)

_BASE_CFG = {
    "mode": "pipeline",
    "instruments": "csi800",
    "benchmark_code": "SH000906TR",
    "topk": 50,
    "n_drop": 5,
    "slippage_bps": 5.0,
    "output_dir": "output/walk_forward/csi800_base",
}


def _mk_run(root: Path, name: str, cfg: dict, net_ann: float = -0.02,
            status: str = "official") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    (d / "metrics.json").write_text(json.dumps({
        "metric_status": status,
        "official_metrics": {
            "excess_return_with_cost": {"annualized_return": net_ann},
        },
        "benchmark": {"code": cfg.get("benchmark_code")},
    }), encoding="utf-8")
    return d


def _cons_cfg(**over) -> dict:
    cfg = dict(_BASE_CFG)
    cfg["slippage_bps"] = 20.0
    cfg["output_dir"] = "output/walk_forward/csi800_conservative"
    cfg.update(over)
    return cfg


def test_clean_pair_builds_report_with_veto1():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG, net_ann=0.01)
        b = _mk_run(root, "run_b", _cons_cfg(), net_ann=-0.02)
        r = build_pair_report(a, b)
        assert r["base"]["run_id"] == "run_a"
        assert r["conservative"]["run_id"] == "run_b"
        assert set(r["config_diff_projected"]) == {"slippage_bps"}
        # output_dir differed but is projection-whitelisted.
        assert r["projection_whitelist"] == ["output_dir"]
        v1 = r["veto_checklist"]["1_conservative_net_excess"]
        assert v1["veto_triggered"] is True   # -0.02 <= 0
        # the other checks are explicit nulls, never silently "passed" —
        # and the artifact self-declares as NOT promotion-eligible
        # (codex #369 r6).
        assert r["veto_checklist"]["2_csi500_dependence"] is None
        assert r["promotion_eligible"] is False
        assert "INCOMPLETE" in r["veto_checklist_status"]
        assert "2_csi500_dependence" in r["incomplete_checks"]


def test_semantic_field_drift_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG)
        b = _mk_run(root, "run_b", _cons_cfg(topk=60))
        with pytest.raises(PairReportError, match="slippage_bps"):
            build_pair_report(a, b)


def test_band_tampering_refuses():
    # DP-2: the conservative magnitude is pre-registered; 12bps is not a
    # legal band even though it "differs only in slippage".
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG)
        b = _mk_run(root, "run_b", _cons_cfg(slippage_bps=12.0))
        with pytest.raises(PairReportError, match="20.0"):
            build_pair_report(a, b)


def test_missing_conservative_side_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG)
        with pytest.raises(PairReportError, match="absent side|missing"):
            build_pair_report(a, root / "nope")


_WF_CFG = {
    "instruments": "csi800",
    "benchmark_code": "SH000906TR",
    "topk": 50,
    "n_drop": 5,
    "slippage_bps": 5.0,
    "output_dir": "output/walk_forward/csi800_base",
}


def _mk_wf_run(root: Path, name: str, cfg: dict,
               fold_status: str = "official",
               mean_net: float | None = -0.02) -> Path:
    d = root / name
    d.mkdir(parents=True)
    fold_report = d / "fold_0_report.json"
    # mirror the PRODUCER schema (write_fold_report): metric_status is
    # nested under "backtest" (codex #369 r2 — a synthetic top-level
    # field masked the real shape and hid an always-refuse bug).
    fold_report.write_text(json.dumps({
        "fold_index": 0,
        "backtest": {"metric_status": fold_status},
    }), encoding="utf-8")
    (d / "walk_forward_report.json").write_text(json.dumps({
        "config": cfg,
        "folds": [{
            "fold_index": 0,
            "annualized_return": mean_net,
            "report_path": "fold_0_report.json",
        }],
        "aggregate_metrics": {
            "mean_annualized_return": mean_net,
            "mean_information_ratio": -0.2,
            "worst_drawdown": -0.07,
        },
        "num_folds": 1,
    }), encoding="utf-8")
    return d


def test_walk_forward_pair_builds_report():
    # codex #369 r1 P1: the campaign shape is walk-forward — the tool
    # must consume walk_forward_report.json (embedded config, per-fold
    # official status via report_path).
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG, mean_net=0.011)
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"},
            mean_net=-0.015)
        r = build_pair_report(a, b)
        assert r["base"]["artifact_shape"] == "walk_forward"
        assert set(r["config_diff_projected"]) == {"slippage_bps"}
        v1 = r["veto_checklist"]["1_conservative_net_excess"]
        assert v1["value_annualized"] == -0.015
        assert v1["veto_triggered"] is True
        assert r["conservative"]["per_fold_net_annualized"] == [-0.015]


def test_walk_forward_non_official_fold_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG, fold_status="research")
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError, match="official"):
            build_pair_report(a, b)


def test_fold_report_outside_run_dir_refuses():
    # codex #369 r5 P1: an aggregate pointing at ANOTHER run's official
    # fold report (absolute path, or a ../ escape) must refuse — borrowed
    # status cannot certify this aggregate's metrics.
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        foreign = _mk_wf_run(root, "foreign", _WF_CFG)   # donor run
        a = _mk_wf_run(root, "wf_a", _WF_CFG)
        # rewrite wf_a's aggregate to point at the foreign fold report.
        wf_p = root / "wf_a" / "walk_forward_report.json"
        payload = json.loads(wf_p.read_text(encoding="utf-8"))
        payload["folds"][0]["report_path"] = str(
            (foreign / "fold_0_report.json").resolve())
        wf_p.write_text(json.dumps(payload), encoding="utf-8")
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError, match="OUTSIDE"):
            build_pair_report(a, b)
        # relative ../ escape refuses identically.
        payload["folds"][0]["report_path"] = "../foreign/fold_0_report.json"
        wf_p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(PairReportError, match="OUTSIDE"):
            build_pair_report(a, b)


def test_fold_index_mismatch_refuses():
    # codex #369 r5: the selected fold must belong to the aggregate
    # entry — the producer stamps fold_index into each fold report.
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG)
        fold_p = root / "wf_a" / "fold_0_report.json"
        fold_p.write_text(json.dumps({
            "fold_index": 3,
            "backtest": {"metric_status": "official"},
        }), encoding="utf-8")
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError, match="fold_index"):
            build_pair_report(a, b)


def test_walk_forward_missing_backtest_block_refuses():
    # codex #369 r2: a fold report without the nested backtest block is
    # a producer-schema mismatch — refuse, never default to "official".
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG)
        (root / "wf_a" / "fold_0_report.json").write_text(
            json.dumps({"fold_index": 0,
                        "metric_status": "official"}),  # top-level only
            encoding="utf-8")
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError,
                           match="backtest.metric_status"):
            build_pair_report(a, b)


def test_walk_forward_missing_fold_report_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG)
        (root / "wf_a" / "fold_0_report.json").unlink()
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError, match="unreadable"):
            build_pair_report(a, b)


def test_walk_forward_nonfinite_aggregate_refuses():
    # codex #369 r3: json.loads yields NaN/Infinity floats and
    # ``nan <= 0.0`` is False — a malformed artifact must REFUSE, never
    # read as "veto not triggered".
    for bad in (float("nan"), float("inf")):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = _mk_wf_run(root, "wf_a", _WF_CFG)
            b = _mk_wf_run(
                root, "wf_b",
                {**_WF_CFG, "slippage_bps": 20.0,
                 "output_dir": "output/walk_forward/csi800_conservative"},
                mean_net=bad)
            with pytest.raises(PairReportError, match="FINITE"):
                build_pair_report(a, b)


def test_pipeline_nonfinite_net_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG)
        b = _mk_run(root, "run_b", _cons_cfg(), net_ann=float("nan"))
        with pytest.raises(PairReportError, match="FINITE"):
            build_pair_report(a, b)


def test_base_side_missing_or_nonfinite_net_refuses():
    # codex #369 r4 P1: the BASE side's net excess is required too — a
    # malformed base must not ride into a certified pair as "evidence".
    for bad in (None, float("nan")):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = _mk_run(root, "run_a", _BASE_CFG, net_ann=bad)
            b = _mk_run(root, "run_b", _cons_cfg(), net_ann=-0.02)
            with pytest.raises(PairReportError, match="base.*FINITE"):
                build_pair_report(a, b)


def test_wf_config_hash_is_config_not_report():
    # codex #369 r4 P2: identical embedded configs must hash identically
    # even when fold outcomes/timestamps differ — the field is verifiable
    # against report["config"], not an artifact hash in disguise.
    from scripts.research.csi800_campaign_pair_report import _config_sha256
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        cfg_c = {**_WF_CFG, "slippage_bps": 20.0,
                 "output_dir": "output/walk_forward/csi800_conservative"}
        a = _mk_wf_run(root, "wf_a", _WF_CFG, mean_net=0.011)
        b = _mk_wf_run(root, "wf_b", cfg_c, mean_net=-0.015)
        r1 = build_pair_report(a, b)
        assert r1["base"]["config_sha256"] == _config_sha256(_WF_CFG)
        assert r1["conservative"]["config_sha256"] == _config_sha256(cfg_c)
        # different outcomes, same configs -> same config hashes.
        with tempfile.TemporaryDirectory() as t2:
            root2 = Path(t2)
            a2 = _mk_wf_run(root2, "wf_a2", _WF_CFG, mean_net=0.030)
            b2 = _mk_wf_run(root2, "wf_b2", cfg_c, mean_net=-0.001)
            r2 = build_pair_report(a2, b2)
            assert (r1["base"]["config_sha256"]
                    == r2["base"]["config_sha256"])
            assert (r1["conservative"]["report_sha256"]
                    != r2["conservative"]["report_sha256"])


def test_walk_forward_null_aggregate_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_wf_run(root, "wf_a", _WF_CFG)
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"},
            mean_net=None)
        with pytest.raises(PairReportError, match="mean_annualized_return"):
            build_pair_report(a, b)


def test_mixed_artifact_shapes_refuse():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG)
        b = _mk_wf_run(
            root, "wf_b",
            {**_WF_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/csi800_conservative"})
        with pytest.raises(PairReportError, match="mixed artifact shapes"):
            build_pair_report(a, b)


def test_promotion_eligibility_semantics():
    # codex #369 r6: eligibility only when ALL five checks are present
    # and NONE triggered; any null/missing/triggered entry blocks it.
    from scripts.research.csi800_campaign_pair_report import (
        evaluate_promotion_eligibility,
    )
    ok = {f"{i}_check": {"veto_triggered": False} for i in range(1, 6)}
    assert evaluate_promotion_eligibility(ok) == (True, [])
    with_null = {**ok, "4_check": None}
    eligible, incomplete = evaluate_promotion_eligibility(with_null)
    assert eligible is False and incomplete == ["4_check"]
    with_trigger = {**ok, "1_check": {"veto_triggered": True}}
    assert evaluate_promotion_eligibility(with_trigger)[0] is False
    with_none_state = {**ok, "2_check": {"veto_triggered": None}}
    assert evaluate_promotion_eligibility(with_none_state)[0] is False


def test_wrong_universe_or_status_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a",
                    {**_BASE_CFG, "instruments": "csi300",
                     "benchmark_code": "SH000300TR"})
        b = _mk_run(root, "run_b",
                    _cons_cfg(instruments="csi300",
                              benchmark_code="SH000300TR"))
        with pytest.raises(PairReportError, match="csi800"):
            build_pair_report(a, b)
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        a = _mk_run(root, "run_a", _BASE_CFG, status="research")
        b = _mk_run(root, "run_b", _cons_cfg())
        with pytest.raises(PairReportError, match="official"):
            build_pair_report(a, b)

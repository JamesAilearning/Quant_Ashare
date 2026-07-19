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

import hashlib
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
                     mean_net: float, n_folds: int = 2,
                     gross: float = 0.05,
                     producer_paths: bool = False) -> Path:
    """``producer_paths=True`` declares report/positions paths the way
    the real WalkForwardEngine does — ``str(output_dir / name)`` — so
    resolver basename-fallback behaviour is exercised (codex #376 r2)."""
    d = root / name
    d.mkdir(parents=True)
    folds = []
    for i in range(n_folds):
        positions_bytes = json.dumps(_positions()).encode("utf-8")
        (d / f"fold_{i:02d}_positions.json").write_bytes(positions_bytes)
        _prefix = (cfg.get("output_dir", "") + "/") if producer_paths else ""
        (d / f"fold_{i:02d}_report.json").write_text(json.dumps({
            "fold_index": i,
            "positions_path": f"{_prefix}fold_{i:02d}_positions.json",
            # producer attestation (PR-A): digest of the persisted bytes
            "positions_sha256": hashlib.sha256(positions_bytes).hexdigest(),
            # producer-embedded per-sleeve turnover; _positions() yields
            # 0.05 one-way per transition x 2 transitions = 0.10 total.
            "sleeve_turnover": {
                "csi300_sleeve": {"total_oneway": 0.06,
                                  "daily_mean_oneway": 0.03,
                                  "n_transitions": 2.0},
                "csi500_sleeve": {"total_oneway": 0.04,
                                  "daily_mean_oneway": 0.02,
                                  "n_transitions": 2.0},
            },
            "backtest": {
                "metric_status": "official",
                "report": {"start_date": "2024-01-02",
                           "end_date": "2024-01-04",
                           "positions_days": 3, "total_days": 3},
                # v3: per-fold GROSS is extracted at pairing time
                "risk_analysis": {"excess_return_without_cost": {
                    "annualized_return": gross}},
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
        folds.append({"fold_index": i, "annualized_return": mean_net,
                      "report_path": f"{_prefix}fold_{i:02d}_report.json"})
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
                      failed_folds: tuple[int, ...] = (),
                      authenticated: bool = True) -> Path:
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
        # completed reference folds must be DOCUMENTED by their own
        # official fold report (codex #373 r2); the report also binds
        # the positions series (declared path + window, codex r4).
        # ``authenticated`` models a producer that embeds an immutable
        # turnover binding (codex r9) — the production reference today
        # does NOT (sleeve grouping off per the #371 pin), which blocks
        # promotion but not vetoes.
        positions_bytes = json.dumps(_positions()).encode("utf-8")
        (d / f"fold_{i:02d}_positions.json").write_bytes(positions_bytes)
        (d / f"fold_{i:02d}_report.json").write_text(json.dumps({
            "fold_index": i,
            "positions_path": f"fold_{i:02d}_positions.json",
            "positions_sha256": hashlib.sha256(positions_bytes).hexdigest(),
            "sleeve_turnover": ({
                "csi300_sleeve": {"total_oneway": 0.10,
                                  "daily_mean_oneway": 0.05,
                                  "n_transitions": 2.0},
            } if authenticated else None),
            "backtest": {
                "metric_status": "official",
                "report": {"start_date": "2024-01-02",
                           "end_date": "2024-01-04",
                           "positions_days": 3, "total_days": 3},
            },
        }), encoding="utf-8")
        folds.append({"fold_index": i, "annualized_return": 0.01,
                      "report_path": f"fold_{i:02d}_report.json"})
    (d / "walk_forward_report.json").write_text(json.dumps({
        "config": cfg,
        "folds": folds,
        "aggregate_metrics": {"mean_annualized_return": 0.01},
        "num_folds": n_folds,
    }), encoding="utf-8")
    return d


def _mk_trio(root: Path, cons_net: float = 0.02, pre_pair=None,
             **ref_kwargs):
    base = _mk_campaign_run(root, "base", _CAMPAIGN_CFG, mean_net=0.05)
    cons = _mk_campaign_run(
        root, "cons",
        {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
         "output_dir": "output/walk_forward/cons"},
        mean_net=cons_net)
    ref = _mk_reference_run(root, **ref_kwargs)
    if pre_pair is not None:
        # run-state edits that must exist BEFORE pairing (the pair
        # report pins per-fold report hashes at generation time).
        pre_pair(base, cons, ref)
    pair_p = root / "pair.json"
    pair_p.write_text(json.dumps(build_pair_report(base, cons, ref)),
                      encoding="utf-8")
    return pair_p, base, cons, ref


def _set_attribution(run_dir: Path, fold: int, block) -> None:
    rep_p = run_dir / f"fold_{fold:02d}_report.json"
    payload = json.loads(rep_p.read_text(encoding="utf-8"))
    payload["attribution"] = block
    rep_p.write_text(json.dumps(payload), encoding="utf-8")


def test_attached_checklist_floats_are_9dp_canonical():
    # codex #379 gen3 P1: checks 2/3/5 aggregate through numpy, whose
    # reduction order drifts ~1 ulp across builds; certify EXACT-compares
    # the recomputed checklist against the anchored pair bytes, so every
    # attached float must be canonicalized (round 9dp) at serialization.
    def _walk(obj):
        if isinstance(obj, bool):
            return
        if isinstance(obj, float):
            assert round(obj, 9) == obj, f"non-canonical float {obj!r}"
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=0.02)
        r = attach(pair_p, base, cons, ref)
        for key in ("2_csi500_dependence", "3_turnover_vs_csi300_ref",
                    "4_risk_constraints_recorded",
                    "5_midcap_concentration"):
            entry = dict(r["veto_checklist"][key])
            # check 2's conservative_net_excess is deliberately RAW —
            # it must equal check 1's predicate value byte-for-byte
            # (codex #381 r2); it is a stored official metric with no
            # cross-environment drift surface.
            entry.pop("conservative_net_excess", None)
            _walk(entry)
        assert (r["veto_checklist"]["2_csi500_dependence"]
                ["conservative_net_excess"]
                == r["veto_checklist"]["1_conservative_net_excess"]
                ["value_annualized"])


def test_threshold_adjacent_share_flags_from_rounded_value():
    # codex #381 r1: the veto flag must be derived from the SAME rounded
    # value that gets stored — a raw share of 0.7999999996 serializes as
    # 0.8 (== threshold), so the flag must read True, never a stored-0.8
    # / flag-False contradiction.
    from scripts.research.csi800_campaign_attach_vetoes import (
        compute_csi500_dependence,
    )

    def _fold(total_effect: float):
        return (0, {"attribution": {
            "status": "ok",
            "sector_effects_sum": 1.0,
            "sector_attribution": [
                {"sector": "csi500_sleeve", "total_effect": total_effect,
                 "portfolio_weight": 0.5},
                {"sector": "csi300_sleeve",
                 "total_effect": 1.0 - total_effect,
                 "portfolio_weight": 0.5},
            ],
        }})

    adjacent = compute_csi500_dependence(
        [_fold(0.7999999996)], cons_net_excess=-0.01, expected_folds=1)
    assert adjacent["csi500_effect_share_of_gross"] == 0.8
    assert adjacent["veto_triggered"] is True
    below = compute_csi500_dependence(
        [_fold(0.799999999)], cons_net_excess=-0.01, expected_folds=1)
    assert below["csi500_effect_share_of_gross"] == 0.799999999
    assert below["veto_triggered"] is False
    # codex #381 r2: the zero-net leg runs on the RAW official net —
    # a marginally positive net (4e-10, below the 9dp half-step) must
    # NOT be rounded to 0.0 and veto a run that check 1 passes; the
    # stored value stays byte-identical to check 1's.
    marginal = compute_csi500_dependence(
        [_fold(0.85)], cons_net_excess=4e-10, expected_folds=1)
    assert marginal["veto_triggered"] is False
    assert marginal["conservative_net_excess"] == 4e-10


def test_ceil9_is_fail_closed_on_grid_thresholds():
    # codex #381 r3: canonicalization must never round a violating value
    # back under a strict > threshold. _ceil9 rounds toward the
    # triggering side; with thresholds on the 9dp grid the predicate on
    # the ceiled value is equivalent to the raw one.
    from scripts.research.csi800_campaign_attach_vetoes import _ceil9

    assert _ceil9(1.5000000004) == 1.500000001
    assert _ceil9(1.5000000004) > 1.5          # violation preserved
    assert _ceil9(1.5) == 1.5                  # exact threshold: no veto
    assert not _ceil9(1.5) > 1.5
    assert _ceil9(1.4999999996) == 1.5         # under stays under (>)
    assert not _ceil9(1.4999999996) > 1.5
    assert _ceil9(0.7500000004) > 0.75
    assert not _ceil9(0.75) > 0.75
    assert _ceil9(0.1000000004) > 0.10


def test_marginal_over_threshold_concentration_still_vetoes():
    # codex #381 r3: raw avg csi500 weight 0.7500000004 must veto — the
    # stored value ceils to 0.750000001 and the strict > predicate runs
    # on that same stored value.
    from scripts.research.csi800_campaign_attach_vetoes import (
        compute_midcap_concentration,
    )

    fold = (0, {"attribution": {
        "status": "ok",
        "sector_effects_sum": 1.0,
        "sector_attribution": [
            {"sector": "csi500_sleeve", "total_effect": 0.5,
             "portfolio_weight": 0.7500000004},
            {"sector": "csi300_sleeve", "total_effect": 0.5,
             "portfolio_weight": 0.2499999996},
        ],
    }})
    r = compute_midcap_concentration([fold], expected_folds=1)
    assert r["csi500_time_avg_weight"] == 0.750000001
    assert r["veto_triggered"] is True
    # exactly at the threshold: strict > must NOT veto.
    fold_at = (0, {"attribution": {
        "status": "ok",
        "sector_effects_sum": 1.0,
        "sector_attribution": [
            {"sector": "csi500_sleeve", "total_effect": 0.5,
             "portfolio_weight": 0.75},
            {"sector": "csi300_sleeve", "total_effect": 0.5,
             "portfolio_weight": 0.25},
        ],
    }})
    r2 = compute_midcap_concentration([fold_at], expected_folds=1)
    assert r2["csi500_time_avg_weight"] == 0.75
    assert r2["veto_triggered"] is False


def test_clean_attach_completes_but_promotion_gated_on_reference():
    # all five checks pass and are recorded COMPLETE — but with no
    # producer-certified reference binding in existence (codex #373 r10),
    # promotion eligibility is structurally blocked, never granted.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=0.02)
        r = attach(pair_p, base, cons, ref)
        vc = r["veto_checklist"]
        assert all(isinstance(vc[k], dict) and vc[k]["veto_triggered"]
                   is False for k in vc)
        assert r["veto_checklist_status"] == "COMPLETE"
        c3 = vc["3_turnover_vs_csi300_ref"]
        assert c3["conservative_over_reference_ratio"] == pytest.approx(1.0)
        assert c3["coverage_problems"] == []
        assert c3["ref_failed_folds"] == []
        assert c3["reference_content_binding"] == (
            "window_only_unauthenticated")
        assert c3["reference_embedded_turnover_verified"] is True
        assert r["promotion_eligible"] is False
        assert r["checks_all_pass"] is True
        # attach is NEVER the promotion authority (#374 r4): the sole
        # authority is a committed certify verdict sidecar.
        assert "certify" in r["promotion_blocked_reason"]
        # rewritten in place
        assert json.loads(pair_p.read_text(encoding="utf-8"))[
            "promotion_eligible"] is False


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
    # non-ok attribution present at PAIRING time (a genuinely degraded
    # run, not post-pairing tampering) fails closed on checks 2 and 5.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(
            Path(t),
            pre_pair=lambda b, c, r: _set_attribution(
                c, 1, {"status": "skipped_no_data"}))
        r = attach(pair_p, base, cons, ref)
        vc = r["veto_checklist"]
        assert vc["2_csi500_dependence"]["veto_triggered"] is True
        assert "fail closed" in vc["2_csi500_dependence"]["note"]
        assert vc["5_midcap_concentration"]["veto_triggered"] is True
        # all five PRESENT -> COMPLETE, but triggered -> not eligible
        assert r["veto_checklist_status"] == "COMPLETE"
        assert r["promotion_eligible"] is False
        # the structural producer prerequisite is recorded even on a
        # vetoed artifact (codex #373 r11) — the veto is never presented
        # as the only blocker.
        assert any("reference_binding_unauthenticated" in b
                   for b in r["promotion_blockers"])


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
    # v3: the drifted reference is refused at PAIRING time already —
    # pairing itself certifies the third party.
    from scripts.research.csi800_campaign_pair_report import (
        PairReportError,
    )

    with tempfile.TemporaryDirectory() as t:
        with pytest.raises(PairReportError, match="expected exactly"):
            _mk_trio(Path(t), cfg_over={"topk": 60})


def test_injected_extra_fold_report_cannot_stand_in():
    # codex #373 r2: fold evidence is enumerated through the certified
    # aggregate's DECLARED report_path set — an injected extra report
    # with favorable attribution must not repair coverage lost when a
    # certified fold's attribution is non-ok.
    def degrade_and_inject(_b: Path, c: Path, _r: Path) -> None:
        _set_attribution(c, 1, {"status": "skipped_no_data"})
        extra = json.loads(
            (c / "fold_00_report.json").read_text(encoding="utf-8"))
        extra["fold_index"] = 2
        (c / "fold_02_report.json").write_text(json.dumps(extra),
                                               encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t),
                                           pre_pair=degrade_and_inject)
        r = attach(pair_p, base, cons, ref)
        vc = r["veto_checklist"]
        # only the DECLARED folds count: 1/2 ok -> fail closed.
        assert vc["2_csi500_dependence"]["veto_triggered"] is True
        assert vc["2_csi500_dependence"]["folds_used"] == 1
        assert vc["5_midcap_concentration"]["veto_triggered"] is True
        assert r["promotion_eligible"] is False


def test_reference_nonofficial_fold_report_refuses():
    # a completed reference fold whose own report is not official is not
    # a documented reference run.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        rep_p = ref / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload["backtest"]["metric_status"] = "degraded"
        rep_p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit,
                           match="changed after pairing|not a documented"):
            attach(pair_p, base, cons, ref)


def test_unauthenticated_reference_blocks_promotion_not_vetoes():
    # codex #373 r9+r10: without a producer-certified binding for
    # reference positions (the production state today — with OR without
    # a presence-only embedded turnover block), an otherwise
    # fully-passing checklist must NOT emit promotion_eligible=true —
    # but the checks are still recorded (unauthenticated evidence may
    # support a veto, never a promotion).
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=0.02,
                                           authenticated=False)
        r = attach(pair_p, base, cons, ref)
        c3 = r["veto_checklist"]["3_turnover_vs_csi300_ref"]
        assert c3["veto_triggered"] is False
        assert c3["reference_content_binding"] == (
            "window_only_unauthenticated")
        assert c3["reference_embedded_turnover_verified"] is False
        assert r["promotion_eligible"] is False
        assert "certify" in r["promotion_blocked_reason"]
        assert any("reference_binding_unauthenticated" in b
                   for b in r["promotion_blockers"])


def _strip_attestation(*dirs: Path) -> None:
    """Remove producer attestation digests BEFORE pairing — models a
    pre-attestation run so the malformed-shape defenses (which sit
    behind the digest check for attested runs) stay exercised."""
    for d in dirs:
        for rep_p in d.glob("fold_*_report.json"):
            payload = json.loads(rep_p.read_text(encoding="utf-8"))
            payload.pop("positions_sha256", None)
            rep_p.write_text(json.dumps(payload), encoding="utf-8")


def test_malformed_daily_positions_refuse_cleanly():
    # codex #373 r14: a null day or non-numeric weight must refuse with
    # AttachError, not crash inside sleeve_turnover.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(
            Path(t), pre_pair=lambda b, c, r: _strip_attestation(b, c, r))
        bad = _positions()
        bad["2024-01-03"] = None
        (cons / "fold_01_positions.json").write_text(
            json.dumps(bad), encoding="utf-8")
        with pytest.raises(SystemExit, match="not a holdings mapping"):
            attach(pair_p, base, cons, ref)
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(
            Path(t), pre_pair=lambda b, c, r: _strip_attestation(b, c, r))
        bad = _positions()
        bad["2024-01-03"] = {"SH600000": "0.5", "SZ000001": 0.5}
        (cons / "fold_01_positions.json").write_text(
            json.dumps(bad), encoding="utf-8")
        with pytest.raises(SystemExit, match="not numeric"):
            attach(pair_p, base, cons, ref)


def test_zero_holding_positions_refuse_cleanly():
    # codex #373 r13: >=2 dates with all-empty daily maps must refuse
    # with AttachError, not crash with a bare StopIteration.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(
            Path(t), pre_pair=lambda b, c, r: _strip_attestation(b, c, r))
        (cons / "fold_01_positions.json").write_text(
            json.dumps({"2024-01-02": {}, "2024-01-03": {},
                        "2024-01-04": {}}), encoding="utf-8")
        with pytest.raises(SystemExit, match="no holdings"):
            attach(pair_p, base, cons, ref)


def test_rerun_clears_stale_promotion_blocked_reason():
    # codex #373 r12: a first attach over a clean checklist records
    # promotion_blocked_reason ("all five pass, blocked only by the
    # prerequisite"); when evidence later degrades (positions removed —
    # deliberately outside the pair-report hash) a rerun must not carry
    # the stale claim alongside a now-triggered veto.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=0.02)
        first = attach(pair_p, base, cons, ref)
        assert "promotion_blocked_reason" in first
        (cons / "fold_01_positions.json").unlink()
        second = attach(pair_p, base, cons, ref)
        c3 = second["veto_checklist"]["3_turnover_vs_csi300_ref"]
        assert c3["veto_triggered"] is True
        assert "promotion_blocked_reason" not in second
        assert any("reference_binding_unauthenticated" in b
                   for b in second["promotion_blockers"])
        persisted = json.loads(pair_p.read_text(encoding="utf-8"))
        assert "promotion_blocked_reason" not in persisted


def test_duplicate_reference_fold_entry_refuses():
    # codex #373 r8: a repeated reference fold would be pooled twice into
    # the veto-3 denominator (num_folds can be set to match) — refuse.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        wf_p = ref / "walk_forward_report.json"
        payload = json.loads(wf_p.read_text(encoding="utf-8"))
        payload["folds"] = [payload["folds"][0], payload["folds"][0]]
        payload["num_folds"] = 2
        wf_p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit, match="more than once"):
            attach(pair_p, base, cons, ref)


def test_duplicate_sleeve_row_refuses():
    # codex #373 r8: duplicate sector rows must refuse — a trailing
    # low-weight csi500 row would silently mask an earlier high one.
    def dup_row(_b: Path, c: Path, _r: Path) -> None:
        rep_p = c / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        rows = payload["attribution"]["sector_attribution"]
        high = {"sector": "csi500_sleeve", "portfolio_weight": 0.9,
                "total_effect": 0.5}
        payload["attribution"]["sector_attribution"] = [high] + rows
        rep_p.write_text(json.dumps(payload), encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), pre_pair=dup_row)
        with pytest.raises(SystemExit, match="more than once"):
            attach(pair_p, base, cons, ref)


def test_ok_attribution_missing_fields_refuses():
    # codex #373 r16: an "ok" attribution whose csi500 row or effects
    # sum is omitted must refuse — absence is not a favorable zero.
    def drop_csi500(_b: Path, c: Path, _r: Path) -> None:
        rep_p = c / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload["attribution"]["sector_attribution"] = [
            r for r in payload["attribution"]["sector_attribution"]
            if r["sector"] != "csi500_sleeve"]
        rep_p.write_text(json.dumps(payload), encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), pre_pair=drop_csi500)
        with pytest.raises(SystemExit, match="favorable zeros|zero conc"):
            attach(pair_p, base, cons, ref)

    def drop_sum(_b: Path, c: Path, _r: Path) -> None:
        rep_p = c / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        del payload["attribution"]["sector_effects_sum"]
        rep_p.write_text(json.dumps(payload), encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), pre_pair=drop_sum)
        with pytest.raises(SystemExit, match="favorable zeros"):
            attach(pair_p, base, cons, ref)


def test_deleted_weighty_unknown_row_breaks_mass_closure():
    # codex #373 r16: the unknown row may be legitimately absent when it
    # carried nothing — but DELETING a row that held real weight breaks
    # the ~1.0 portfolio weight-mass closure and must refuse.
    def hide_unknown(_b: Path, c: Path, _r: Path) -> None:
        rep_p = c / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload["attribution"]["sector_attribution"] = [
            {"sector": "csi500_sleeve", "portfolio_weight": 0.45,
             "total_effect": 0.010},
            {"sector": "csi300_sleeve", "portfolio_weight": 0.35,
             "total_effect": 0.008},
            # unknown row (0.20 weight) deleted -> sum 0.80
        ]
        rep_p.write_text(json.dumps(payload), encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), pre_pair=hide_unknown)
        with pytest.raises(SystemExit, match="weight-carrying row"):
            attach(pair_p, base, cons, ref)


def test_nonfinite_sleeve_weight_refuses():
    # codex #373 r7: NaN weights make every threshold comparison False —
    # veto 5 must refuse on corrupted evidence, never record a pass.
    def poison(_b: Path, c: Path, _r: Path) -> None:
        rep_p = c / "fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload["attribution"]["sector_attribution"] = [
            {"sector": "csi500_sleeve", "portfolio_weight": float("nan"),
             "total_effect": 0.010},
            {"sector": "csi300_sleeve", "portfolio_weight": 0.55,
             "total_effect": 0.008},
            {"sector": "unknown", "portfolio_weight": 0.0,
             "total_effect": -0.001},
        ]
        rep_p.write_text(json.dumps(payload), encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), pre_pair=poison)
        with pytest.raises(SystemExit, match="non-finite"):
            attach(pair_p, base, cons, ref)


def test_edited_check1_in_pair_report_refuses():
    # codex #373 r6: veto 1 is re-derived from the bound conservative
    # side's official metrics — flipping the stored check-1 value/flag
    # in the mutable pair-report JSON must refuse, not emit eligibility.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), cons_net=-0.02)
        payload = json.loads(pair_p.read_text(encoding="utf-8"))
        c1 = payload["veto_checklist"]["1_conservative_net_excess"]
        c1["value_annualized"] = 0.05
        c1["veto_triggered"] = False
        pair_p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit, match="edited after pairing"):
            attach(pair_p, base, cons, ref)


def test_post_pairing_fold_report_tamper_refuses():
    # codex #373 r5: the pair report pins each declared fold report's
    # content hash at generation time — a fold report replaced AFTER
    # pairing (even self-consistently with its positions) refuses.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        _set_attribution(cons, 1, {"status": "ok",
                                   "sector_attribution": _SLEEVE_ROWS,
                                   "sector_effects_sum": 0.5})
        with pytest.raises(SystemExit, match="changed after pairing"):
            attach(pair_p, base, cons, ref)


def test_tampered_positions_fail_embedded_turnover_binding():
    # codex #373 r4: positions are mutable — a swapped csi800 series must
    # be caught by the content binding against the certified fold
    # report's producer-embedded sleeve_turnover total.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        tampered = _positions()
        tampered["2024-01-03"] = {"SH600000": 0.40, "SZ000001": 0.60}
        (cons / "fold_01_positions.json").write_text(
            json.dumps(tampered), encoding="utf-8")
        with pytest.raises(SystemExit,
                           match="not the one the (run|producer)"):
            attach(pair_p, base, cons, ref)


def test_reference_positions_window_mismatch_refuses():
    # codex #373 r4: a replaced reference series must at least match the
    # certified fold report's documented window/day count.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        longer = _positions()
        longer["2024-01-05"] = dict(longer["2024-01-04"])
        (ref / "fold_01_positions.json").write_text(
            json.dumps(longer), encoding="utf-8")
        with pytest.raises(SystemExit,
                           match="torn/replaced|producer stamped"):
            attach(pair_p, base, cons, ref)


def test_reference_stale_positions_on_failed_fold_refuses():
    # codex #373 r3: a documented-failed fold with a leftover/injected
    # positions series would inflate the reference turnover denominator
    # and could suppress veto 3 — refuse instead of consuming it.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t), failed_folds=(1,))
        (ref / "fold_01_positions.json").write_text(
            json.dumps(_positions(shift=0.2)), encoding="utf-8")
        with pytest.raises(SystemExit, match="documented FAILED"):
            attach(pair_p, base, cons, ref)


def test_reference_missing_fold_report_refuses():
    # config-shaped directory without the documented fold reports (the
    # synthetic low-turnover baseline scenario) refuses.
    with tempfile.TemporaryDirectory() as t:
        pair_p, base, cons, ref = _mk_trio(Path(t))
        (ref / "fold_01_report.json").unlink()
        with pytest.raises(Exception, match="unreadable"):
            attach(pair_p, base, cons, ref)


def test_cadence_artifact_requires_scope_disclosure():
    # codex #378 r2: with a non-default cadence declared in the config,
    # veto 4 requires each fold report to disclose
    # risk_constraint_scope=rebalance_days — a pre-R1 artifact (no
    # scope field) triggers; a post-R1 artifact passes.
    cadence_cfg = {**_CAMPAIGN_CFG, "rebalance_cadence_days": 5,
                   "rebalance_phase": 0, "rebalance_anchor": "fold_phase",
                   "risk_constraint_scope": "rebalance_days"}

    def add_scope(with_scope: bool):
        def _hook(b: Path, c: Path, _r: Path) -> None:
            for d in (b, c):
                for rep_p in d.glob("fold_*_report.json"):
                    payload = json.loads(rep_p.read_text(encoding="utf-8"))
                    reb = {"cadence_days": 5, "phase": 0,
                           "anchor": "fold_phase"}
                    if with_scope:
                        reb["risk_constraint_scope"] = "rebalance_days"
                    payload["backtest"]["provenance"]["config"][
                        "rebalance"] = reb
                    rep_p.write_text(json.dumps(payload), encoding="utf-8")
        return _hook

    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        base = _mk_campaign_run(root, "base", cadence_cfg, mean_net=0.05)
        cons = _mk_campaign_run(
            root, "cons",
            {**cadence_cfg, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/cons"},
            mean_net=0.02)
        ref = _mk_reference_run(
            root, cfg_over={"rebalance_cadence_days": 5,
                            "rebalance_phase": 0,
                            "rebalance_anchor": "fold_phase",
                            "risk_constraint_scope": "rebalance_days"})
        add_scope(True)(base, cons, ref)
        pair_p = root / "pair.json"
        pair_p.write_text(json.dumps(build_pair_report(base, cons, ref)),
                          encoding="utf-8")
        r = attach(pair_p, base, cons, ref)
        assert r["veto_checklist"]["4_risk_constraints_recorded"][
            "veto_triggered"] is False

    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        base = _mk_campaign_run(root, "base", cadence_cfg, mean_net=0.05)
        cons = _mk_campaign_run(
            root, "cons",
            {**cadence_cfg, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/cons"},
            mean_net=0.02)
        ref = _mk_reference_run(
            root, cfg_over={"rebalance_cadence_days": 5,
                            "rebalance_phase": 0,
                            "rebalance_anchor": "fold_phase",
                            "risk_constraint_scope": "rebalance_days"})
        # pre-R1 shape: no scope field in fold provenance
        pair_p = root / "pair.json"
        pair_p.write_text(json.dumps(build_pair_report(base, cons, ref)),
                          encoding="utf-8")
        r = attach(pair_p, base, cons, ref)
        c4 = r["veto_checklist"]["4_risk_constraints_recorded"]
        assert c4["veto_triggered"] is True
        assert any("risk_constraint_scope" in p for p in c4["problems"])

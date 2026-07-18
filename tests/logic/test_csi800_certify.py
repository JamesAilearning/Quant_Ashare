"""Certify step tests — synthetic git repos with a real origin/main ref.

Coverage matrix (>=1 case per dimension):
  WIN certifies      — anchored trio + N1 baseline, all vetoes pass,
                       primary criteria pass -> sidecar written with
                       producer_digest_certified + three anchors.
  unanchored refuses — inputs not present at the mainline anchor refuse
                       (working-tree/feature-only evidence can never
                       certify).
  chain incomplete   — a fold report without positions_sha256 refuses
                       (only PR-A attestation producers certify).
  vetoed refuses     — veto-1 triggered (cons net <= 0) -> no sidecar.
  gross collapse     — N5 gross < 50% of N1 gross -> LOSE, no sidecar.
  verify roundtrip   — a written sidecar re-verifies; a tampered field
                       breaks reproduction and refuses.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.csi800_campaign_attach_vetoes import attach  # noqa: E402
from scripts.research.csi800_campaign_certify import (  # noqa: E402
    certify,
    verify,
)
from scripts.research.csi800_campaign_pair_report import (  # noqa: E402
    build_pair_report,
)
from tests.logic.test_csi800_attach_vetoes import (  # noqa: E402
    _CAMPAIGN_CFG,
    _mk_campaign_run,
    _mk_reference_run,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _mk_repo(root: Path, cons_net: float = 0.02) -> dict[str, str]:
    """Build a committed campaign world: N5 trio (attached pair) + the
    same artifacts doubling as the N1 baseline, on a real mainline."""
    repo = root / "repo"
    ev = repo / "evidence"
    ev.mkdir(parents=True)
    base = _mk_campaign_run(ev, "base", _CAMPAIGN_CFG, mean_net=0.05)
    cons = _mk_campaign_run(
        ev, "cons",
        {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
         "output_dir": "output/walk_forward/cons"},
        mean_net=cons_net)
    ref = _mk_reference_run(ev)
    pair_p = repo / "pair.json"
    pair_p.write_text(
        json.dumps(build_pair_report(base, cons, ref)), encoding="utf-8")
    attach(pair_p, base, cons, ref)
    # N1 baseline: reuse the attached pair + copy the fold reports into
    # the pinned N1 evidence layout (side dirs base/conservative).
    n1_ev = repo / "n1_evidence"
    for side, run in (("base", base), ("conservative", cons)):
        d = n1_ev / side
        d.mkdir(parents=True)
        for rep_p in sorted(run.glob("fold_*_report.json")):
            (d / rep_p.name).write_bytes(rep_p.read_bytes())
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "campaign evidence", "--no-verify")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    return {
        "repo": str(repo), "head": head,
        "pair": "pair.json",
        "base": "evidence/base", "cons": "evidence/cons",
        "ref": "evidence/ref", "n1_pair": "pair.json",
        "n1_ev": "n1_evidence",
    }


def _run_certify(w: dict[str, str], out: Path) -> dict:
    return certify(Path(w["repo"]), w["pair"], w["base"], w["cons"],
                   w["ref"], w["n1_pair"], w["n1_ev"], out)


def test_win_certifies_with_three_anchors_and_sidecar():
    with tempfile.TemporaryDirectory() as t:
        w = _mk_repo(Path(t), cons_net=0.02)
        out = Path(t) / "verdict.json"
        sidecar = _run_certify(w, out)
        assert out.is_file()
        v = sidecar["verdict"]
        assert v["promotion_eligible"] is True
        assert v["reference_content_binding"] == "producer_digest_certified"
        assert v["gross_retention"] == pytest.approx(1.0)
        assert set(sidecar["anchors"]) == {
            "pair_anchor", "evidence_anchor", "n1_anchor", "mainline_ref"}
        assert sidecar["anchors"]["pair_anchor"] == w["head"]


def test_uncommitted_input_refuses():
    with tempfile.TemporaryDirectory() as t:
        w = _mk_repo(Path(t))
        # a pair path that exists ONLY in the working tree
        wt_only = Path(w["repo"]) / "pair_wt.json"
        wt_only.write_bytes((Path(w["repo"]) / "pair.json").read_bytes())
        with pytest.raises(SystemExit, match="CERTIFY REFUSED"):
            certify(Path(w["repo"]), "pair_wt.json", w["base"], w["cons"],
                    w["ref"], w["n1_pair"], w["n1_ev"],
                    Path(t) / "v.json")


def test_incomplete_digest_chain_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _mk_repo(root)
        repo = Path(w["repo"])
        # strip attestation from one committed fold report, re-pair,
        # re-attach, re-commit — self-consistent but chain-incomplete
        rep_p = repo / "evidence/cons/fold_01_report.json"
        payload = json.loads(rep_p.read_text(encoding="utf-8"))
        payload.pop("positions_sha256")
        rep_p.write_text(json.dumps(payload), encoding="utf-8")
        pair_p = repo / "pair.json"
        pair_p.write_text(json.dumps(build_pair_report(
            repo / "evidence/base", repo / "evidence/cons",
            repo / "evidence/ref")), encoding="utf-8")
        attach(pair_p, repo / "evidence/base", repo / "evidence/cons",
               repo / "evidence/ref")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "strip", "--no-verify")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD"))
        with pytest.raises(SystemExit, match="digest chain is incomplete"):
            _run_certify(w, root / "v.json")


def test_vetoed_campaign_refuses():
    with tempfile.TemporaryDirectory() as t:
        w = _mk_repo(Path(t), cons_net=-0.02)   # veto-1 triggers
        with pytest.raises(SystemExit, match="does not pass"):
            _run_certify(w, Path(t) / "v.json")
        assert not (Path(t) / "v.json").exists()


def test_gross_collapse_refuses():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _mk_repo(root)
        repo = Path(w["repo"])
        # inflate the N1 baseline gross (3x the N5 gross) in BOTH side
        # evidence dirs, then re-pin the N1 pair hashes accordingly by
        # building a dedicated N1 trio with high gross.
        ev2 = repo / "n1_high"
        base2 = _mk_campaign_run(ev2, "base", _CAMPAIGN_CFG,
                                 mean_net=0.05, gross=0.20)
        cons2 = _mk_campaign_run(
            ev2, "cons",
            {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
             "output_dir": "output/walk_forward/cons"},
            mean_net=0.02, gross=0.20)
        ref2 = _mk_reference_run(ev2)
        (repo / "n1_pair_high.json").write_text(
            json.dumps(build_pair_report(base2, cons2, ref2)),
            encoding="utf-8")
        n1_ev = repo / "n1_evidence_high"
        for side, run in (("base", base2), ("conservative", cons2)):
            d = n1_ev / side
            d.mkdir(parents=True)
            for rep_p in sorted(run.glob("fold_*_report.json")):
                (d / rep_p.name).write_bytes(rep_p.read_bytes())
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "n1 high", "--no-verify")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD"))
        with pytest.raises(SystemExit, match="gross retention"):
            certify(repo, w["pair"], w["base"], w["cons"], w["ref"],
                    "n1_pair_high.json", "n1_evidence_high",
                    root / "v.json")


def test_verify_roundtrip_and_tamper():
    with tempfile.TemporaryDirectory() as t:
        w = _mk_repo(Path(t))
        repo = Path(w["repo"])
        out = repo / "verdict.json"
        _run_certify(w, out)
        # an UNMERGED sidecar must not verify (codex #376 r5): the
        # sidecar itself is read through the mainline anchor.
        with pytest.raises(SystemExit, match="CERTIFY REFUSED"):
            verify(repo, "verdict.json")
        # merge the sidecar -> verifies clean
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "sidecar", "--no-verify")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD"))
        verify(repo, "verdict.json")
        # merged-but-tampered sidecar breaks reproduction
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload["verdict"]["gross_retention"] = 9.9
        out.write_text(json.dumps(payload, indent=2) + "\n",
                       encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "tamper", "--no-verify")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD"))
        with pytest.raises(SystemExit, match="do not reproduce"):
            verify(repo, "verdict.json")


def test_edited_pair_gross_fields_refuse():
    # codex #376 r1: certify re-derives the N5 gross series from the
    # anchored fold reports — an anchored pair whose stored
    # per_fold_gross_annualized was inflated must refuse, not mint a
    # passing retention.
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        w = _mk_repo(root)
        repo = Path(w["repo"])
        pair_p = repo / "pair.json"
        payload = json.loads(pair_p.read_text(encoding="utf-8"))
        payload["conservative"]["per_fold_gross_annualized"] = [
            x * 3 for x in
            payload["conservative"]["per_fold_gross_annualized"]]
        pair_p.write_text(json.dumps(payload), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "inflate", "--no-verify")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD"))
        with pytest.raises(SystemExit, match="were edited"):
            _run_certify(w, root / "v.json")


def test_producer_style_paths_certify(tmp_path: Path) -> None:
    # codex #376 r2: the real WalkForwardEngine declares
    # str(output_dir / "fold_XX_report.json") paths — a materialized
    # (relocated) evidence copy must still resolve them (basename
    # fallback, confined to the claimed run dir).
    repo = tmp_path / "repo"
    ev = repo / "evidence"
    ev.mkdir(parents=True)
    base = _mk_campaign_run(ev, "base", _CAMPAIGN_CFG, mean_net=0.05,
                            producer_paths=True)
    cons = _mk_campaign_run(
        ev, "cons",
        {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
         "output_dir": "output/walk_forward/cons"},
        mean_net=0.02, producer_paths=True)
    ref = _mk_reference_run(ev)
    pair_p = repo / "pair.json"
    pair_p.write_text(
        json.dumps(build_pair_report(base, cons, ref)), encoding="utf-8")
    attach(pair_p, base, cons, ref)
    n1_ev = repo / "n1_evidence"
    for side, run in (("base", base), ("conservative", cons)):
        d = n1_ev / side
        d.mkdir(parents=True)
        for rep_p in sorted(run.glob("fold_*_report.json")):
            (d / rep_p.name).write_bytes(rep_p.read_bytes())
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "producer paths", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    sidecar = certify(repo, "pair.json", "evidence/base",
                      "evidence/cons", "evidence/ref", "pair.json",
                      "n1_evidence", tmp_path / "v.json")
    assert sidecar["verdict"]["promotion_eligible"] is True


def test_nonfinite_n1_gross_refuses(tmp_path: Path) -> None:
    # codex #376 r2: a NaN N1 gross would make every threshold
    # comparison False — fail closed on malformed baseline evidence.
    import hashlib as _hashlib

    w = _mk_repo(tmp_path)
    repo = Path(w["repo"])
    bad_report = json.dumps({
        "fold_index": 0,
        "backtest": {"risk_analysis": {"excess_return_without_cost": {
            "annualized_return": float("nan")}}},
    }).encode("utf-8")
    digest = _hashlib.sha256(bad_report).hexdigest()
    n1_dir = repo / "n1_nan"
    for side in ("base", "conservative"):
        (n1_dir / side).mkdir(parents=True)
        (n1_dir / side / "fold_00_report.json").write_bytes(bad_report)
    (repo / "n1_pair_nan.json").write_text(json.dumps({
        "base": {"fold_report_sha256": {"0": digest}, "num_folds": 1},
        "conservative": {"fold_report_sha256": {"0": digest},
                         "num_folds": 1},
    }), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "nan n1", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    with pytest.raises(SystemExit, match="non-finite"):
        certify(repo, w["pair"], w["base"], w["cons"], w["ref"],
                "n1_pair_nan.json", "n1_nan", tmp_path / "v.json")


def test_confined_resolution_wins_over_repo_root_leftover(
        tmp_path: Path, monkeypatch) -> None:
    # codex #376 r3: a leftover artifact at the ORIGINAL repo-root path
    # must not trip the outside-run_dir refusal when the materialized
    # (confined) copy resolves by basename.
    import scripts.research.csi800_campaign_pair_report as pair_mod

    fake_repo = tmp_path / "fake_repo"
    leftover = fake_repo / "output/walk_forward/cons"
    leftover.mkdir(parents=True)
    (leftover / "fold_00_report.json").write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "materialized_cons"
    run_dir.mkdir()
    (run_dir / "fold_00_report.json").write_text(
        json.dumps({"fold_index": 0}), encoding="utf-8")
    monkeypatch.setattr(pair_mod, "_REPO", fake_repo)
    resolved = pair_mod._resolve_fold_report(
        run_dir, "output/walk_forward/cons/fold_00_report.json")
    assert resolved == (run_dir / "fold_00_report.json").resolve()


def test_nonfinite_n5_gross_refuses(tmp_path: Path) -> None:
    # codex #376 r4: Infinity in the anchored N5 fold reports with a
    # CONSISTENTLY edited pair series passes inf==inf equality while
    # NaN divergence bypasses the guard — the re-derived N5 series must
    # fail closed exactly like the N1 path.
    import hashlib as _hashlib

    w = _mk_repo(tmp_path)
    repo = Path(w["repo"])
    # rewrite one cons fold report with Infinity gross, then repair the
    # WHOLE chain consistently: pair regenerated would refuse, so edit
    # the committed pair by hand (fold hash + gross series + digest ok).
    rep_p = repo / "evidence/cons/fold_01_report.json"
    payload = json.loads(rep_p.read_text(encoding="utf-8"))
    payload["backtest"]["risk_analysis"]["excess_return_without_cost"][
        "annualized_return"] = float("inf")
    rep_p.write_text(json.dumps(payload), encoding="utf-8")
    pair_p = repo / "pair.json"
    pair = json.loads(pair_p.read_text(encoding="utf-8"))
    pair["conservative"]["fold_report_sha256"]["1"] = _hashlib.sha256(
        rep_p.read_bytes()).hexdigest()
    pair["conservative"]["per_fold_gross_annualized"][1] = float("inf")
    pair_p.write_text(json.dumps(pair), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "inf n5", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    # the v3 loader already refuses non-finite gross during the attach
    # re-load (PairReportError "FINITE"); certify's own isfinite check
    # is defense-in-depth behind it. Either loud exit closes the hole.
    with pytest.raises((SystemExit, RuntimeError),
                       match="FINITE|non-finite"):
        _run_certify(w, tmp_path / "v.json")
    assert not (tmp_path / "v.json").exists()


def test_failed_reference_fold_refuses_certification(tmp_path: Path) -> None:
    # codex #376 r6: a reference with a documented failed fold passes
    # the attach turnover check (disclosed exemption) but must NOT
    # certify — promotion-grade coverage requires the full fold set.
    repo = tmp_path / "repo"
    ev = repo / "evidence"
    ev.mkdir(parents=True)
    base = _mk_campaign_run(ev, "base", _CAMPAIGN_CFG, mean_net=0.05)
    cons = _mk_campaign_run(
        ev, "cons",
        {**_CAMPAIGN_CFG, "slippage_bps": 20.0,
         "output_dir": "output/walk_forward/cons"},
        mean_net=0.02)
    ref = _mk_reference_run(ev, failed_folds=(1,))
    pair_p = repo / "pair.json"
    pair_p.write_text(
        json.dumps(build_pair_report(base, cons, ref)), encoding="utf-8")
    attach(pair_p, base, cons, ref)
    n1_ev = repo / "n1_evidence"
    for side, run in (("base", base), ("conservative", cons)):
        d = n1_ev / side
        d.mkdir(parents=True)
        for rep_p in sorted(run.glob("fold_*_report.json")):
            (d / rep_p.name).write_bytes(rep_p.read_bytes())
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "failed ref", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    with pytest.raises(SystemExit, match="failed folds"):
        certify(repo, "pair.json", "evidence/base", "evidence/cons",
                "evidence/ref", "pair.json", "n1_evidence",
                tmp_path / "v.json")


def test_aliased_n1_fold_keys_refuse(tmp_path: Path) -> None:
    # codex #376 r6 P2: alias keys ("0"/"00") satisfying the count
    # while omitting a fold must refuse.
    import hashlib as _hashlib

    w = _mk_repo(tmp_path)
    repo = Path(w["repo"])
    rep_bytes = (repo / "n1_evidence/base/fold_00_report.json").read_bytes()
    digest = _hashlib.sha256(rep_bytes).hexdigest()
    (repo / "n1_pair_alias.json").write_text(json.dumps({
        "base": {"fold_report_sha256": {"0": digest, "00": digest},
                 "num_folds": 2},
        "conservative": {"fold_report_sha256": {"0": digest, "00": digest},
                         "num_folds": 2},
    }), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "alias", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    with pytest.raises(SystemExit, match="aliased"):
        certify(repo, w["pair"], w["base"], w["cons"], w["ref"],
                "n1_pair_alias.json", "n1_evidence", tmp_path / "v.json")


def test_aliased_n5_fold_keys_refuse(tmp_path: Path) -> None:
    # codex #376 r7: alias keys in the N5 pair maps would double-count
    # a favorable fold in the retention mean — same exact-key-set rule
    # as the N1 side.
    w = _mk_repo(tmp_path)
    repo = Path(w["repo"])
    pair_p = repo / "pair.json"
    pair = json.loads(pair_p.read_text(encoding="utf-8"))
    digest0 = pair["conservative"]["fold_report_sha256"]["0"]
    pair["conservative"]["fold_report_sha256"] = {
        "0": digest0, "00": digest0}
    pair["conservative"]["per_fold_gross_annualized"] = [0.05, 0.05]
    pair_p.write_text(json.dumps(pair), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "alias n5", "--no-verify")
    _git(repo, "update-ref", "refs/remotes/origin/main",
         _git(repo, "rev-parse", "HEAD"))
    with pytest.raises((SystemExit, RuntimeError),
                       match="aliased|does not match|changed after"):
        _run_certify(w, tmp_path / "v.json")

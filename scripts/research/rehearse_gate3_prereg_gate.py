"""Gate-3 pre-registration gate REHEARSAL — twenty-three scenarios, 23/23 required.

Exercises ``gate3_prereg_gate.py`` for real (no mocks) against the freeze
worktree, per ``docs/prereg/quality_profitability_rehearsal.md``:

  R1 ACCEPT   clean checkout + frozen plan + intact manifest + registered C1.
  R2 REFUSE   unregistered candidate (C4_ROE).
  R3 REFUSE   dirty checkout (temp untracked file injected, then removed).
  R4 REFUSE   run timestamped BEFORE the plan's freeze commit.
  R5 REFUSE   store/manifest mismatch (minimal temp store: one file per
              endpoint — re-hash cannot match the frozen 1880-file manifest;
              the gate has NO manifest override to bypass, codex #352 P1).
  R6 REFUSE   PIT battery fails (injected look-ahead probe: asserts a value
              IS visible before its announcement — the correct view makes
              that assertion FAIL, so the gate must refuse).

  R7-R11    see docs/prereg/quality_profitability_rehearsal.md (ledger not
            frozen / holdout-touching window / beyond-holdout adjudication /
            partial-holdout peek / claim-config mismatch).
  R12 REFUSE out-of-repo run config (not git-provable, codex #352 r9).
  R13 REFUSE extends chain leaving the frozen package (codex #352 r10).
  R14 REFUSE repeat final adjudication after holdout unblinding (r10).
  R15 REFUSE env placeholder in a chain value (data-version lock, r11).
  R16 REFUSE candidate/config binding mismatch (r11).
  R17 REFUSE run config that binds no candidate (r11).
  R18 REFUSE --final-adjudication on a dev window (fake provenance, r13).
  R19 REFUSE holding-period drift vs the signed quarterly design (r14).
  R20 REFUSE universe stamp drift vs the frozen ex-financial design (r14).
  R21 REFUSE dev runs after unblinding (plan consumed, r15).
  R22 refused adjudication must print NO unblinding banner (r16).
  R23 ACCEPT clean exact-holdout adjudication WITH banner (gate check
      only — nothing runs, ledger untouched).

Any scenario deviating = the gate itself has a hole -> exit 1 (fix the gate
before freezing). Exit 0 = 23/23, paste the printed block into the rehearsal
execution record.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

GATE = "scripts/research/gate3_prereg_gate.py"
MANIFEST_REL = "docs/prereg/quality_profitability_store_manifest.json"

_FAILING_PIT_PROBE = '''\
"""Injected look-ahead probe (rehearsal R6): asserts a filing IS visible
BEFORE its announcement. Against the correct view this FAILS -> gate refuses."""
from datetime import date

import pandas as pd

from src.data.trading_calendar import StaticTradingCalendar
from src.research.financial_pit_view import FinancialPITDataView

_CAL = StaticTradingCalendar([date(2022, 3, 31), date(2022, 4, 1)])


def test_lookahead_probe(tmp_path):
    inc = tmp_path / "income"
    inc.mkdir(parents=True)
    row = {"ts_code": "000001.SZ", "end_date": "20211231", "ann_date": "20220331",
           "f_ann_date": "20220331", "update_flag": "0", "revenue": 100.0,
           "total_revenue": pd.NA, "oper_cost": pd.NA, "sell_exp": pd.NA,
           "admin_exp": pd.NA, "rd_exp": pd.NA, "int_exp": pd.NA, "fin_exp": pd.NA,
           "_content_hash": "h", "_fetch_batch": "b1", "_source_endpoint": "income"}
    pd.DataFrame([row]).to_parquet(inc / "000001.SZ.parquet", index=False)
    v = FinancialPITDataView(tmp_path, _CAL, financial_issuers=frozenset())
    got = v.as_of("2022-03-30", ["revenue"], ["000001.SZ"]).loc["000001.SZ", "revenue"]
    assert got == 100.0, "value must be visible BEFORE announcement (look-ahead)"
'''


# Default run config = the C1 dev STUB (binds gate3_candidate: C1_GPA and
# extends the self-contained frozen parent). The PARENT paths are what
# R9/R10/R13/R15 byte-modify — the stub chain picks the change up.
DEV_STUB_REL = "config/presets/quality_gate3_dev_c1_gpa.yaml"
DEV_PARENT_REL = "config/presets/quality_gate3_dev.yaml"
FINAL_STUB_REL = "config/presets/quality_gate3_final_adjudication_c1_gpa.yaml"
FINAL_PARENT_REL = "config/presets/quality_gate3_final_adjudication.yaml"


def _run_gate(repo: Path, store: Path, *extra: str) -> tuple[int, str]:
    argv = [sys.executable, str(repo / GATE), "--repo-root", str(repo),
            "--store-dir", str(store), *extra]
    if "--run-config" not in extra:
        argv += ["--run-config", str(repo / DEV_STUB_REL)]  # tracked, frozen
    out = subprocess.run(argv, capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path,
                   default=Path(__file__).resolve().parents[2])
    p.add_argument("--store-dir", type=Path, required=True)
    args = p.parse_args(argv)
    repo, store = args.repo_root, args.store_dir
    results: list[tuple[str, bool, str]] = []

    # codex #352 r9: run configs must be repo-tracked — the default (R1) and
    # both final-adjudication scenarios (R9/R10) now use the FROZEN presets;
    # the only temp config left is R12's, which exists to be REFUSED.
    cfg_dir = Path(tempfile.mkdtemp(prefix="g3_rehearsal_cfg_"))
    outside_cfg = cfg_dir / "outside_repo.yaml"
    outside_cfg.write_text('overall_end: "2024-12-31"\n', encoding="utf-8")
    final_cfg_path = repo / FINAL_PARENT_REL
    final_cfg_bytes = final_cfg_path.read_bytes()
    final_stub_path = repo / FINAL_STUB_REL

    # R1 ACCEPT
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
    ok = rc == 0 and "GATE ACCEPT" in out
    results.append(("R1 normal accept", ok, out.splitlines()[-1] if out else ""))

    # R2 REFUSE unregistered
    rc, out = _run_gate(repo, store, "--candidate", "C4_ROE")
    results.append(("R2 unregistered candidate refused",
                    rc == 1 and "NOT in registered_candidates" in out,
                    out.splitlines()[0] if out else ""))

    # R3 REFUSE dirty checkout
    probe = repo / "_rehearsal_dirty_probe.tmp"
    probe.write_text("dirty", encoding="utf-8")
    try:
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R3 dirty checkout refused",
                        rc == 1 and "dirty checkout" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        probe.unlink()

    # R4 REFUSE plan committed after run
    past = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA", "--run-ts", past)
    results.append(("R4 plan-after-run refused",
                    rc == 1 and "NOT" in out and "before the run" in out,
                    out.splitlines()[0] if out else ""))

    # R5 REFUSE store/manifest mismatch: minimal temp store (one parquet per
    # endpoint) re-hashes to something the FROZEN manifest cannot match; the
    # gate has no manifest override to bypass (codex #352 P1).
    with tempfile.TemporaryDirectory() as td:
        tampered_store = Path(td) / "store"
        for ep in ("income", "balancesheet", "cashflow"):
            src_dir = store / ep
            first = sorted(src_dir.glob("*.parquet"))[0]
            dst_dir = tampered_store / ep
            dst_dir.mkdir(parents=True)
            (dst_dir / first.name).write_bytes(first.read_bytes())
        rc, out = _run_gate(repo, tampered_store, "--candidate", "C1_GPA")
        results.append(("R5 manifest mismatch refused",
                        rc == 1 and "manifest mismatch" in out,
                        out.splitlines()[0] if out else ""))

    # R6 REFUSE PIT battery failure (look-ahead probe)
    with tempfile.TemporaryDirectory() as td:
        probe_file = Path(td) / "test_rehearsal_lookahead_probe.py"
        probe_file.write_text(_FAILING_PIT_PROBE, encoding="utf-8")
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--extra-pit-case", str(probe_file))
        results.append(("R6 PIT-case failure refused",
                        rc == 1 and "PIT case battery FAILED" in out,
                        out.splitlines()[0] if out else ""))

    # R7 REFUSE unfrozen ledger (codex #352 r4: the r3 patch CLAIMED this
    # enforcement but silently failed to land — a rehearsal scenario now pins
    # it so a claimed check can never again be absent). Temp-downgrade the
    # ledger status in the worktree; the status check runs BEFORE the
    # dirty-tree check, so the refusal reason must name the ledger.
    # BYTES I/O throughout: text-mode write_text would translate the file's
    # LF to CRLF on Windows, leaving the ledger permanently dirty after the
    # restore (this exact failure surfaced in the v4 re-run — R1 then refused
    # on the leftover dirt).
    ledger_path = repo / "docs/prereg/quality_profitability_ledger.yaml"
    original_bytes = ledger_path.read_bytes()
    try:
        ledger_path.write_bytes(original_bytes.replace(
            b"status: frozen_with_this_package", b"status: draft_pre_freeze"))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R7 unfrozen ledger refused",
                        rc == 1 and "ledger status" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        ledger_path.write_bytes(original_bytes)

    # R8 REFUSE holdout-touching window (codex #352 r5+r8): the REAL default
    # config_walk.yaml runs through 2025-12-31 — gating THAT config (window
    # derived from the config, not a claim) must refuse NAMING the holdout.
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--run-config", str(repo / "config_walk.yaml"))
    results.append(("R8 holdout-touching window refused",
                    rc == 1 and "untouched holdout" in out,
                    out.splitlines()[0] if out else ""))

    # R9 REFUSE final-adjudication beyond the signed holdout (codex #352 r6):
    # the verdict run must cover the holdout EXACTLY — 2026H1 data is outside
    # the registered adjudication scope, flag or no flag.
    try:
        final_cfg_path.write_bytes(final_cfg_bytes.replace(
            b'"2025-12-31"', b'"2026-06-30"'))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--run-config", str(final_stub_path),
                            "--final-adjudication")
    finally:
        final_cfg_path.write_bytes(final_cfg_bytes)
    results.append(("R9 adjudication-beyond-holdout refused",
                    rc == 1 and "EXACTLY" in out,
                    out.splitlines()[0] if out else ""))

    # R10 REFUSE partial-holdout adjudication (codex #352 r7): a verdict run
    # ending mid-holdout (2025-06-30) would be an interim PEEK at partial
    # holdout results before the one-time verdict — must refuse.
    try:
        final_cfg_path.write_bytes(final_cfg_bytes.replace(
            b'"2025-12-31"', b'"2025-06-30"'))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--run-config", str(final_stub_path),
                            "--final-adjudication")
    finally:
        final_cfg_path.write_bytes(final_cfg_bytes)
    results.append(("R10 partial-holdout peek refused",
                    rc == 1 and "EXACTLY" in out and "no partial peek" in out,
                    out.splitlines()[0] if out else ""))

    # R11 REFUSE claim/config mismatch (codex #352 r8): a bare claim saying
    # 2024-12-31 while the config derives 2025-12-31 must be refused — the
    # gate validates the CONFIG, not the claim.
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--run-config", str(repo / "config_walk.yaml"),
                        "--test-window-end", "2024-12-31")
    results.append(("R11 claim/config mismatch refused",
                    rc == 1 and "claim/config mismatch" in out,
                    out.splitlines()[0] if out else ""))

    # R12 REFUSE out-of-repo run config (codex #352 r9): a /tmp config is
    # outside clean-tree/frozen-package coverage, so the boundary it declares
    # is not git-provable — refuse even though its overall_end (2024-12-31)
    # would pass the window check, proving the refusal is on tracked-ness.
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--run-config", str(outside_cfg))
    results.append(("R12 out-of-repo config refused",
                    rc == 1 and "NOT under the repository" in out,
                    out.splitlines()[0] if out else ""))

    # R13 REFUSE extends chain leaving the frozen package (codex #352 r10):
    # temp-append an `extends` link from the (self-contained) dev preset to
    # the unfrozen config_walk.yaml — later commits to that parent could
    # silently drift model/universe/cost/fold params after freeze, so the
    # gate must refuse. Bytes I/O; chain check runs before the dirty check.
    dev_cfg_path = repo / DEV_PARENT_REL
    dev_cfg_bytes = dev_cfg_path.read_bytes()
    try:
        dev_cfg_path.write_bytes(
            dev_cfg_bytes + b"\nextends: ../../config_walk.yaml\n")
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R13 unfrozen extends chain refused",
                        rc == 1 and "NOT part of the frozen package" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        dev_cfg_path.write_bytes(dev_cfg_bytes)

    # R14 REFUSE repeat final adjudication (codex #352 r10): once the ledger
    # records holdout_unblinded: true, the ONE-TIME verdict is consumed —
    # a second --final-adjudication (same clean checkout, exact holdout
    # window) must be refused permanently. Bytes I/O flip + restore.
    ledger_bytes2 = ledger_path.read_bytes()
    try:
        ledger_path.write_bytes(ledger_bytes2.replace(
            b"holdout_unblinded: false", b"holdout_unblinded: true"))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--run-config", str(final_stub_path),
                            "--final-adjudication")
        results.append(("R14 repeat adjudication refused",
                        rc == 1 and "ALREADY UNBLINDED" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        ledger_path.write_bytes(ledger_bytes2)

    # R15 REFUSE env placeholder in a chain VALUE (codex #352 r11): temp
    # re-introduce ${QUANT_PROVIDER_URI:-...} into the frozen dev parent —
    # the same config sha256 must never resolve to different data bundles
    # at runtime. Bytes I/O + restore; comments MENTIONING ${VAR} are inert
    # (value-level scan only).
    try:
        dev_cfg_path.write_bytes(dev_cfg_bytes.replace(
            b'provider_uri: "D:/qlib_data/my_cn_data_pit"',
            b'provider_uri: "${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}"'))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R15 env placeholder refused",
                        rc == 1 and "env placeholder" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        dev_cfg_path.write_bytes(dev_cfg_bytes)

    # R16 REFUSE candidate/config binding mismatch (codex #352 r11): gate
    # a REGISTERED candidate (C2_PROF) against the C1-bound stub — an
    # unbound CLI claim must not collect ACCEPT for a config that
    # evaluates a different candidate.
    rc, out = _run_gate(repo, store, "--candidate", "C2_PROF")
    results.append(("R16 candidate binding mismatch refused",
                    rc == 1 and "binding mismatch" in out,
                    out.splitlines()[0] if out else ""))

    # R17 REFUSE unbound run config (codex #352 r11): the frozen PARENT
    # snapshot alone declares no gate3_candidate — the binding is
    # REQUIRED, not optional (its window/chain are otherwise fully legal).
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--run-config", str(dev_cfg_path))
    results.append(("R17 unbound config refused",
                    rc == 1 and "declares no gate3_candidate" in out,
                    out.splitlines()[0] if out else ""))

    # R18 REFUSE the verdict flag on a dev window (codex #352 r13): with
    # --final-adjudication + the DEV stub the whole adjudication branch
    # would otherwise be skipped — an ACCEPT carrying [FINAL ADJUDICATION]
    # provenance without covering the holdout or consuming the unblinding
    # state. The flag is valid ONLY for the exact holdout window.
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--final-adjudication")
    results.append(("R18 verdict flag on dev window refused",
                    rc == 1 and "DEV window" in out
                    and "verdict provenance" in out,
                    out.splitlines()[0] if out else ""))

    # R19 REFUSE holding-period drift (codex #352 r14): temp-flip the dev
    # parent's quarterly cadence back to the daily default — metrics from a
    # daily-rebalance run must not masquerade as the signed quarterly
    # design. Bytes I/O + restore.
    try:
        dev_cfg_path.write_bytes(dev_cfg_bytes.replace(
            b"rebalance_cadence_days: 63", b"rebalance_cadence_days: 1"))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R19 holding-period mismatch refused",
                        rc == 1 and "holding-period mismatch" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        dev_cfg_path.write_bytes(dev_cfg_bytes)

    # R20 REFUSE universe stamp drift (codex #352 r14): temp-swap the
    # frozen ex-financial universe stamp for a full-csi300 claim — a bare
    # csi300 run must not claim the frozen study universe.
    try:
        dev_cfg_path.write_bytes(dev_cfg_bytes.replace(
            b'gate3_universe: "csi300_pit_ex_financials"',
            b'gate3_universe: "csi300_full"'))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R20 universe stamp mismatch refused",
                        rc == 1 and "universe stamp mismatch" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        dev_cfg_path.write_bytes(dev_cfg_bytes)

    # R21 REFUSE dev runs after unblinding (codex #352 r15): once the
    # verdict fired, the plan is CONSUMED — a post-verdict DEV-window
    # "decision-level" run is iteration on a family whose holdout has been
    # spent, and must refuse just like a repeat adjudication (R14).
    try:
        ledger_path.write_bytes(ledger_bytes2.replace(
            b"holdout_unblinded: false", b"holdout_unblinded: true"))
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA")
        results.append(("R21 dev run after unblinding refused",
                        rc == 1 and "ALREADY UNBLINDED" in out
                        and "CONSUMED" in out,
                        out.splitlines()[0] if out else ""))
    finally:
        ledger_path.write_bytes(ledger_bytes2)

    # R22 NO unblinding banner on a refused adjudication (codex #352 r16):
    # a legit exact-holdout adjudication that fails a LATER check (dirty
    # tree here) must refuse WITHOUT ever printing the UNBLINDING banner —
    # a banner before REFUSE could mislead the operator into flipping the
    # ledger for a rejected run.
    probe2 = repo / "_rehearsal_dirty_probe.tmp"
    probe2.write_text("dirty", encoding="utf-8")
    try:
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--run-config", str(final_stub_path),
                            "--final-adjudication")
        results.append(("R22 refused adjudication prints no banner",
                        rc == 1 and "dirty checkout" in out
                        and "HOLDOUT UNBLINDING" not in out,
                        out.splitlines()[0] if out else ""))
    finally:
        probe2.unlink()

    # R23 ACCEPT of a clean exact-holdout adjudication DOES print the
    # banner (immediately before GATE ACCEPT, after all refusal paths) —
    # this is only the pre-run GATE check, not the verdict run itself:
    # nothing executes and the ledger is untouched.
    rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                        "--run-config", str(final_stub_path),
                        "--final-adjudication")
    results.append(("R23 clean adjudication accepts with banner",
                    rc == 0 and "GATE ACCEPT" in out
                    and "HOLDOUT UNBLINDING" in out
                    and "[FINAL ADJUDICATION]" in out,
                    out.splitlines()[-1] if out else ""))

    print("\n=== GATE REHEARSAL RESULTS ===")
    n_ok = 0
    for name, ok, detail in results:
        n_ok += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  | {detail[:90]}")
    print(f"  => {n_ok}/23")
    return 0 if n_ok == 23 else 1


if __name__ == "__main__":
    sys.exit(main())

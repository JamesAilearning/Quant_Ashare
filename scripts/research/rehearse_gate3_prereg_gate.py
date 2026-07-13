"""Gate-3 pre-registration gate REHEARSAL — six scenarios, 6/6 required.

Exercises ``gate3_prereg_gate.py`` for real (no mocks) against the freeze
worktree, per ``docs/prereg/quality_profitability_rehearsal.md``:

  R1 ACCEPT   clean checkout + frozen plan + intact manifest + registered C1.
  R2 REFUSE   unregistered candidate (C4_ROE).
  R3 REFUSE   dirty checkout (temp untracked file injected, then removed).
  R4 REFUSE   run timestamped BEFORE the plan's freeze commit.
  R5 REFUSE   manifest tampered (temp copy with one hash flipped).
  R6 REFUSE   PIT battery fails (injected look-ahead probe: asserts a value
              IS visible before its announcement — the correct view makes
              that assertion FAIL, so the gate must refuse).

Any scenario deviating = the gate itself has a hole -> exit 1 (fix the gate
before freezing). Exit 0 = 6/6, paste the printed block into the rehearsal
execution record.
"""
from __future__ import annotations

import argparse
import json
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


def _run_gate(repo: Path, store: Path, *extra: str) -> tuple[int, str]:
    out = subprocess.run(
        [sys.executable, str(repo / GATE), "--repo-root", str(repo),
         "--store-dir", str(store), *extra],
        capture_output=True, text=True,
    )
    return out.returncode, out.stdout + out.stderr


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path,
                   default=Path(__file__).resolve().parents[2])
    p.add_argument("--store-dir", type=Path, required=True)
    args = p.parse_args(argv)
    repo, store = args.repo_root, args.store_dir
    results: list[tuple[str, bool, str]] = []

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

    # R5 REFUSE manifest mismatch (tampered temp copy)
    manifest = json.loads((repo / MANIFEST_REL).read_text(encoding="utf-8"))
    first_key = next(iter(manifest["files"]))
    manifest["files"][first_key] = "0" * 64
    with tempfile.TemporaryDirectory() as td:
        tampered = Path(td) / "tampered_manifest.json"
        tampered.write_text(json.dumps(manifest), encoding="utf-8")
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--manifest", str(tampered))
        results.append(("R5 manifest mismatch refused",
                        rc == 1 and "manifest mismatch" in out,
                        out.splitlines()[0] if out else ""))

    # R6 REFUSE PIT battery failure (look-ahead probe)
    with tempfile.TemporaryDirectory() as td:
        probe_file = Path(td) / "test_rehearsal_lookahead_probe.py"
        probe_file.write_text(_FAILING_PIT_PROBE, encoding="utf-8")
        rc, out = _run_gate(repo, store, "--candidate", "C1_GPA",
                            "--pit-cases", str(probe_file))
        results.append(("R6 PIT-case failure refused",
                        rc == 1 and "PIT case battery FAILED" in out,
                        out.splitlines()[0] if out else ""))

    print("\n=== GATE REHEARSAL RESULTS ===")
    n_ok = 0
    for name, ok, detail in results:
        n_ok += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  | {detail[:90]}")
    print(f"  => {n_ok}/6")
    return 0 if n_ok == 6 else 1


if __name__ == "__main__":
    sys.exit(main())

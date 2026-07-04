"""阶段6 step 3.5 — gate REHEARSAL: full plan->run->compare chain on CPU.

Dry-runs the ENTIRE comparison pipeline the campaign will use — the committed
pre-registration plan, the git ancestry gate, the paired bootstrap verdict —
against SYNTHETIC run directories, so every moving part is exercised BEFORE
any real compute is burned. Three scenarios, all against the real CLI in a
subprocess (no mocks):

  1. ACCEPT  — registered variant (5d), clean provenance stamped with the
               current HEAD: the gate must verify ancestry and print a verdict.
  2. FLAG    — unregistered variant (10d): the verdict must carry the
               UNREGISTERED MULTIPLE COMPARISON flag (flagged, not refused).
  3. REFUSE  — dirty-worktree provenance (git_dirty=true): the gate must
               refuse with no verdict.
  4. REFUSE  — ST-handling mismatch (one side ST-on, one ST-off): the gate
               must refuse — the pair would measure the ST interaction, not
               the registered hypothesis (codex P1 #323).

Prerequisite: docs/prereg/label_horizon.yaml is COMMITTED (the gate anchors to
committed content only — an uncommitted plan correctly fails scenario 1).

Synthetic dirs are written under output/stage6/rehearsal/ (disposable, never
compared to anything real). Run from the repo root:

    python scripts/rehearse_label_horizon_gate.py

Exit 0 = all three scenarios behaved; exit 1 = the chain is broken somewhere
— fix BEFORE igniting the real runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN = "docs/prereg/label_horizon.yaml"
CLI = PROJECT_ROOT / "scripts" / "compare_walk_forward_runs.py"
REHEARSAL_ROOT = PROJECT_ROOT / "output" / "stage6" / "rehearsal"


def _head_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_run(root: Path, *, delta: float, git_commit: str,
               git_dirty: bool, st_mask_mode: str = "off_experiment",
               namechange_path: str = "") -> Path:
    """A minimal 1-fold run dir carrying the daily_series substrate the ruler
    needs (mirrors tests/logic/test_compare_cli.py's builder)."""
    from src.core.walk_forward.aggregate import FOLD_REPORT_SCHEMA_VERSION

    root.mkdir(parents=True, exist_ok=True)
    d0 = date.fromisoformat("2025-07-01")
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(120)]
    # Deterministic, mildly autocorrelated excess series; the treatment adds
    # a VARYING daily edge (a constant paired difference has zero sampling
    # variance and the ruler rightly refuses the degenerate CI — fail-loud).
    # The rehearsal asserts CHAIN behavior, never the numeric verdict.
    base = [0.0004 if (i // 5) % 2 == 0 else -0.0003 for i in range(len(dates))]
    excess = [
        b + delta * (0.5 + (i % 5) / 4.0)  # 0.5x..1.5x delta, deterministic
        for i, b in enumerate(base)
    ]
    ds = {
        "excess_return": dict(zip(dates, excess, strict=True)),
        "components": {
            "return": {d: e + 0.0015 for d, e in zip(dates, excess, strict=True)},
            "bench": {d: 0.001 for d in dates},
            "cost": {d: 0.0005 for d in dates},
        },
        "ic": {"1": {d: 0.02 for d in dates}},
    }
    tp = f"{dates[0]}..{dates[-1]}"
    (root / "fold_00_report.json").write_text(json.dumps({
        "fold_index": 0, "test_period": tp, "ic_1d": 0.02,
        "annualized_return": 0.05, "information_ratio": 0.3,
        "daily_series": ds, "schema_version": FOLD_REPORT_SCHEMA_VERSION,
    }), encoding="utf-8")
    (root / "walk_forward_report.json").write_text(json.dumps({
        "num_folds": 1, "generated_at": "2026-07-03T00:00:00Z",
        "git_commit": git_commit, "git_dirty": git_dirty,
        # The gate derives ST-handling parity from the embedded config
        # (codex P1 #323); the campaign shape is off_experiment + no inputs.
        "config": {"st_mask_mode": st_mask_mode,
                   "namechange_path": namechange_path},
        "folds": [{"test_period": tp, "fold_index": 0, "ic_1d": 0.02,
                   "annualized_return": 0.05, "information_ratio": 0.3}],
        "aggregate_metrics": {"pooled_ir": 0.3},
    }), encoding="utf-8")
    return root


def _compare(a: Path, b: Path, variant: str) -> tuple[int, str]:
    # Pin BOTH ends of the pipe to UTF-8: the CLI prints em-dashes, and on a
    # GBK-locale Windows box a mixed encoding misaligns the multi-byte decode
    # and corrupts ASCII downstream of the first dash — the assertions below
    # would then fail on perfectly correct CLI output.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, str(CLI), str(a), str(b),
         "--prereg-plan", PLAN, "--variant", variant],
        cwd=PROJECT_ROOT, capture_output=True, encoding="utf-8",
        errors="replace", timeout=300, env=env,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    head = _head_commit()
    failures: list[str] = []

    clean_a = _write_run(REHEARSAL_ROOT / "A", delta=0.0,
                         git_commit=head, git_dirty=False)
    clean_b = _write_run(REHEARSAL_ROOT / "B", delta=0.0002,
                         git_commit=head, git_dirty=False)
    dirty_b = _write_run(REHEARSAL_ROOT / "B_dirty", delta=0.0002,
                         git_commit=head, git_dirty=True)
    st_on_b = _write_run(REHEARSAL_ROOT / "B_st_on", delta=0.0002,
                         git_commit=head, git_dirty=False,
                         st_mask_mode="required",
                         namechange_path="D:/data/all_namechanges.parquet")

    # 1. ACCEPT — registered variant, clean provenance: the gate must verify
    # ancestry AND a verdict must print.
    rc, out = _compare(clean_a, clean_b, "5d")
    if (
        "pre-registration GATE: PASSED" not in out
        or "VERDICT" not in out
        or "UNREGISTERED" in out
    ):
        failures.append(
            f"scenario 1 (accept): expected gate PASSED + a verdict for "
            f"variant 5d; rc={rc}. Tail:\n{out[-800:]}"
        )
    else:
        print("scenario 1 ACCEPT ok — ancestry verified, verdict printed.")

    # 2. FLAG — unregistered variant stays visible, not driven underground.
    rc, out = _compare(clean_a, clean_b, "10d")
    if "UNREGISTERED MULTIPLE COMPARISON" not in out:
        failures.append(
            f"scenario 2 (flag): variant 10d must be FLAGGED as unregistered; "
            f"rc={rc}. Tail:\n{out[-800:]}"
        )
    else:
        print("scenario 2 FLAG ok — 10d flagged as unregistered.")

    # 3. REFUSE — dirty provenance yields no verdict.
    rc, out = _compare(clean_a, dirty_b, "5d")
    if (
        "NO VERDICT (pre-registration gate failed)" not in out
        or "DIRTY" not in out
    ):
        failures.append(
            f"scenario 3 (refuse): dirty provenance must refuse the gate with "
            f"no verdict; rc={rc}. Tail:\n{out[-800:]}"
        )
    else:
        print("scenario 3 REFUSE ok — dirty worktree refused, no verdict.")

    # 4. REFUSE — ST-handling mismatch (codex P1 #323): baseline ST-off vs a
    # treatment accidentally run ST-on must never yield a decision-grade
    # verdict.
    rc, out = _compare(clean_a, st_on_b, "5d")
    if (
        "NO VERDICT (pre-registration gate failed)" not in out
        or "ST-handling MISMATCH" not in out
    ):
        failures.append(
            f"scenario 4 (refuse): mismatched ST handling must refuse the "
            f"gate with no verdict; rc={rc}. Tail:\n{out[-800:]}"
        )
    else:
        print("scenario 4 REFUSE ok — ST-handling mismatch refused, no verdict.")

    if failures:
        print("\nREHEARSAL FAILED:")
        for f in failures:
            print(f"- {f}")
        return 1
    print("\nREHEARSAL PASS: plan->gate->verdict chain fully exercised "
          "(accept / flag / refuse-dirty / refuse-st-mismatch). Ready for "
          "the 1-fold smoke, then the real runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

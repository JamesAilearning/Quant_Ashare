"""阶段6 label-horizon campaign — fold-structure preflight (CPU, seconds).

The H=5 treatment widens the label-lookahead embargo 2 -> 6 trading days.
By design (`WalkForwardEngine._generate_windows`) that shrinks train/valid
segment TAILS only — the month-aligned test windows must be unchanged, or the
paired daily net-excess statistics would not compare identical OOS days.
This preflight PROVES that on the real trading calendar BEFORE any run burns
compute (the add-label-horizon-config operator warning):

  1. H=1 and H=5 presets generate the SAME fold count (no fold squeezed out,
     no boundary-fold overflow of the fold-22 class);
  2. every fold's (test_start, test_end) is IDENTICAL across the two presets;
  3. the paired shared-OOS-day count (trading days per test window) is
     reported per fold + total — the paired sample size the ruler will see;
  4. the 1-fold smoke preset yields exactly ONE fold.

No qlib init, no model, no data reads beyond the bundle's calendar text file.
Run it from the repo root and commit its output as the campaign's preflight
evidence (docs/prereg/label_horizon_preflight.md):

    python scripts/preflight_label_horizon.py

Exit 0 = all assertions hold; exit 1 = the fold structure moved (STOP — fix
the presets / re-plan before igniting the runs).
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for entry in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

H1_PRESET = "config/presets/stage6_label_h1.yaml"
H5_PRESET = "config/presets/stage6_label_h5.yaml"
SMOKE_PRESET = "config/presets/stage6_smoke_h5_1fold.yaml"
EXPECTED_FOLDS = 23  # the documented 23-fold layout (config_walk.yaml)


def _load_calendar(provider_uri: str) -> list[date]:
    """Read the bundle's trading calendar DIRECTLY (no qlib init)."""
    cal_file = Path(provider_uri) / "calendars" / "day.txt"
    if not cal_file.is_file():
        raise SystemExit(
            f"FAIL: trading calendar not found at {cal_file} — set "
            "QUANT_PROVIDER_URI to a real qlib bundle before preflighting."
        )
    days: list[date] = []
    for line in cal_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            days.append(datetime.strptime(line, "%Y-%m-%d").date())
    if not days:
        raise SystemExit(f"FAIL: calendar {cal_file} is empty.")
    return days


def main() -> int:
    from run_walk_forward import _load_config

    from src.core.walk_forward.engine import WalkForwardEngine

    cfg_h1, qlib_h1 = _load_config(H1_PRESET)
    cfg_h5, _ = _load_config(H5_PRESET)
    cfg_smoke, _ = _load_config(SMOKE_PRESET)
    cal = _load_calendar(qlib_h1.provider_uri)

    w_h1 = WalkForwardEngine._generate_windows(cfg_h1, calendar=cal)
    w_h5 = WalkForwardEngine._generate_windows(cfg_h5, calendar=cal)
    w_smoke = WalkForwardEngine._generate_windows(cfg_smoke, calendar=cal)

    failures: list[str] = []
    print("# 阶段6 label-horizon preflight — fold structure H=1 vs H=5")
    print()
    print(f"calendar: {cal[0]} .. {cal[-1]} ({len(cal)} trading days)")
    print(f"folds: H1={len(w_h1)}  H5={len(w_h5)}  smoke={len(w_smoke)}")
    print()

    if len(w_h1) != len(w_h5):
        failures.append(
            f"fold count differs: H1={len(w_h1)} vs H5={len(w_h5)} — a fold "
            "was squeezed out by the wider embargo; the pair is not comparable."
        )
    if len(w_h1) != EXPECTED_FOLDS:
        failures.append(
            f"H1 fold count {len(w_h1)} != documented {EXPECTED_FOLDS} — the "
            "walk-forward layout moved; re-derive the campaign plan."
        )
    if len(w_smoke) != 1:
        failures.append(
            f"smoke preset generated {len(w_smoke)} folds (want exactly 1) — "
            "fix overall_end in stage6_smoke_h5_1fold.yaml."
        )

    # Per-fold table: test windows must be identical; train/valid tails may
    # differ (that IS the embargo doing its job — 4 extra trading days).
    print("| fold | test window (H1==H5?) | shared OOS days "
          "| H1 train_end/valid_end | H5 train_end/valid_end |")
    print("|---|---|---|---|---|")
    total_days = 0
    # strict=False: a fold-count mismatch is already a recorded failure above;
    # still render the common-prefix table for diagnosis.
    for i, (f1, f5) in enumerate(zip(w_h1, w_h5, strict=False)):
        t1s, t1e = f1[4], f1[5]
        t5s, t5e = f5[4], f5[5]
        same = (t1s, t1e) == (t5s, t5e)
        if not same:
            failures.append(
                f"fold {i}: test window differs — H1 {t1s}..{t1e} vs "
                f"H5 {t5s}..{t5e}. Paired daily stats would compare "
                "different OOS days; STOP."
            )
        lo = datetime.strptime(t1s, "%Y-%m-%d").date()
        hi = datetime.strptime(t1e, "%Y-%m-%d").date()
        n_days = sum(1 for d in cal if lo <= d <= hi)
        total_days += n_days
        print(
            f"| {i} | {t1s}..{t1e} {'==' if same else '!!DIFF!!'} | {n_days} "
            f"| {f1[1]} / {f1[3]} | {f5[1]} / {f5[3]} |"
        )
    print()
    print(f"paired shared-OOS-day total: {total_days} trading days "
          f"across {len(w_h1)} folds")
    print()

    if failures:
        for f in failures:
            print(f"- **FAIL: {f}**")
        print("\nPREFLIGHT FAILED — do not ignite the campaign runs.")
        return 1
    print("PREFLIGHT PASS: identical test windows, fold count intact, "
          "smoke=1 fold. The H1/H5 pair is structurally comparable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

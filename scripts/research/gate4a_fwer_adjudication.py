"""Gate-4A FULL-BATCH FWER adjudication (quality_profitability_v1).

Implements the FROZEN rule (plan `gate_4A.fwer_multiple_testing_rule`) with
the operator-signed mechanics (ledger pin, 2026-07-16):

  * Trials (frozen N=9): C1_GPA / C2_PROF / C3_cash_based_OP primary runs
    + the six registered sensitivity slices, where exclude_fold_0 is a
    PURE RE-AGGREGATION of the recorded C1 series (E018 — derived here,
    no run of its own).
  * Per-trial statistic: t = mean / (std / sqrt(n)) over the trial's
    fold-level rank_ic series (primary stamps only, straight from each
    run's result.json — never recomputed from prices).
  * Null: each trial's series DEMEANED (family null = no signal anywhere).
  * JOINT moving-block bootstrap: block starts are drawn on the MASTER
    stamp-position axis (union of all trials' fold indices, -4..18) with
    circular wrap, and every trial consumes THE SAME drawn position
    sequence (restricted to the positions it has) — cross-trial
    correlation (the profitability family co-moves) is preserved inside
    every draw, which is exactly why min/max-statistic FWER was frozen.
    A draw leaving any trial with < MIN_OBS observations is redrawn
    (counted; sparse trials like holding_annual make this possible).
  * block = 4 stamps (one year), B = 10_000, seed = 20260716, one-sided
    (positive: rank_direction descending — higher factor should earn
    higher return).
  * PASS iff  max observed t  >  95th percentile of the bootstrap max-t
    distribution  AND  max observed t >= 2.85 (the frozen A-share
    calibration floor, Hou-Qiao-Zhang 2024 — the STRICTER of the two
    always binds).

Output: verdict.json + verdict.md under
``<artifacts-root>/fwer_<UTCstamp>/`` — a VERDICT INPUT for the operator
under the frozen three-state rules; this script never auto-pivots and a
clean negative is a valid, reportable research outcome.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

BLOCK = 4
N_BOOT = 10_000
SEED = 20260716
T_FLOOR = 2.85
MIN_OBS = 2
ALPHA_QUANTILE = 0.95

# The eight artifact-backed trials (ledger refs) -> the (candidate, slice,
# FROZEN fold-index geometry) their artifact MUST carry. Identity guards a
# mis-mapped directory (codex #361 r1 P1); geometry guards a stale or
# truncated artifact whose arbitrary fold ids would silently reshape the
# master bootstrap axis and every trial's draws (codex #361 r2 P1).
# exclude_fold_0 derives (geometry = dev folds minus fold 0).
_DEV = frozenset(range(19))
RUN_TRIALS: dict[str, tuple[str, str, frozenset[int]]] = {
    "C1_GPA": ("C1_GPA", "primary", _DEV),
    "C2_PROF": ("C2_PROF", "primary", _DEV),
    "C3_cash_based_OP": ("C3_cash_based_OP", "primary", _DEV),
    "C1_from_2018": ("C1_GPA", "C1_from_2018", frozenset(range(-4, 19))),
    "holding_semiannual": ("C1_GPA", "holding_semiannual",
                           frozenset(range(0, 17, 2))),
    "holding_annual": ("C1_GPA", "holding_annual", frozenset({0, 4, 8, 12})),
    "st_off": ("C1_GPA", "st_off", _DEV),
    "size_decile_variants": ("C1_GPA", "size_decile_variants", _DEV),
}
DERIVED_TRIAL = "exclude_fold_0"
DERIVED_GEOMETRY = frozenset(range(1, 19))
PROTOCOL_ID = "quality_profitability_v1"


class FwerError(RuntimeError):
    """Fail-loud: the adjudication aborts rather than guess."""


def load_trial_series(result_json: Path,
                      expect_candidate: str | None = None,
                      expect_slice: str | None = None) -> dict[int, float]:
    """{fold_index: rank_ic} over PRIMARY stamps from a run artifact.

    Identity is VALIDATED before any value is used (codex #361 r1 P1):
    protocol_id, gate, candidate and slice must match the trial the
    caller maps this artifact to (pre-#360 primary artifacts carry no
    ``slice`` field — treated as "primary"). Non-finite rank_ic values
    fail loud (codex #361 r1 P1): a damaged artifact must abort the
    adjudication, never launder into a CLEAN_NEGATIVE."""
    data = json.loads(result_json.read_text(encoding="utf-8"))
    if data.get("protocol_id") != PROTOCOL_ID or data.get("gate") != "4A":
        raise FwerError(f"{result_json}: not a {PROTOCOL_ID} Gate-4A "
                        f"artifact (protocol_id={data.get('protocol_id')!r}, "
                        f"gate={data.get('gate')!r}).")
    if expect_candidate is not None:
        got_cand = data.get("candidate")
        got_slice = data.get("slice", "primary")
        if (got_cand, got_slice) != (expect_candidate, expect_slice):
            raise FwerError(
                f"{result_json}: artifact identity (candidate={got_cand!r}, "
                f"slice={got_slice!r}) does not match the mapped trial "
                f"(expected candidate={expect_candidate!r}, "
                f"slice={expect_slice!r}) — refusing a mis-mapped series.")
    series: dict[int, float] = {}
    for row in data["folds"]:
        if row.get("stamp_kind") != "primary":
            continue
        idx = int(row["fold"])
        if idx in series:
            raise FwerError(f"{result_json}: duplicate primary fold {idx}.")
        val = float(row["rank_ic"])
        if not np.isfinite(val):
            raise FwerError(f"{result_json}: non-finite rank_ic at fold "
                            f"{idx} — damaged artifact; refusing.")
        series[idx] = val
    if not series:
        raise FwerError(f"{result_json}: no primary stamps found.")
    return series


def validate_trial_geometry(name: str, series: dict[int, float]) -> None:
    """The trial's fold-index set must equal its FROZEN design geometry —
    a stale/truncated artifact would reshape the shared bootstrap axis
    and every trial's draws (codex #361 r2 P1)."""
    geometry = (DERIVED_GEOMETRY if name == DERIVED_TRIAL
                else RUN_TRIALS[name][2])
    if frozenset(series) != geometry:
        raise FwerError(
            f"{name}: fold geometry {sorted(series)} does not match the "
            f"frozen design {sorted(geometry)} — refusing a reshaped "
            "bootstrap axis.")


def derive_exclude_fold0(c1: dict[int, float]) -> dict[int, float]:
    """E018: the recorded C1 series minus fold 0 — nothing recomputed."""
    if 0 not in c1:
        raise FwerError("C1 series lacks fold 0 — cannot derive "
                        "exclude_fold_0.")
    out = {k: v for k, v in c1.items() if k != 0}
    if len(out) < MIN_OBS:
        raise FwerError("exclude_fold_0 would keep fewer than 2 folds.")
    return out


def observed_t(series: dict[int, float]) -> float:
    v = np.array(list(series.values()), dtype=float)
    n = len(v)
    if n < MIN_OBS:
        raise FwerError(f"series has only {n} observations.")
    sd = float(v.std(ddof=1))
    if sd < 1e-12:
        raise FwerError("zero-variance series — corrupted input.")
    return float(v.mean() / (sd / np.sqrt(n)))


def _draw_positions(rng: np.random.Generator, master: list[int],
                    length: int) -> list[int]:
    """Circular moving-block draw of ``length`` positions from ``master``."""
    m = len(master)
    out: list[int] = []
    while len(out) < length:
        start = int(rng.integers(0, m))
        out.extend(master[(start + j) % m] for j in range(BLOCK))
    return out[:length]


def mbb_max_t(trials: dict[str, dict[int, float]],
              n_boot: int = N_BOOT, seed: int = SEED,
              ) -> tuple[np.ndarray[Any, Any], int]:
    """Bootstrap distribution of the FAMILY max-t under the demeaned null
    (shared position draws across trials). Returns (max_t draws, redraws)."""
    master = sorted({p for s in trials.values() for p in s})
    demeaned: dict[str, dict[int, float]] = {}
    for name, s in trials.items():
        mu = float(np.mean(list(s.values())))
        demeaned[name] = {p: v - mu for p, v in s.items()}
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot, dtype=float)
    redraws = 0
    for b in range(n_boot):
        while True:
            positions = _draw_positions(rng, master, len(master))
            per_trial: list[float] = []
            ok = True
            for s in demeaned.values():
                vals = [s[p] for p in positions if p in s]
                if len(vals) < MIN_OBS:
                    ok = False
                    break
                arr = np.array(vals, dtype=float)
                sd = float(arr.std(ddof=1))
                if sd < 1e-12:
                    ok = False
                    break
                per_trial.append(float(arr.mean() / (sd / np.sqrt(len(arr)))))
            if ok:
                draws[b] = max(per_trial)
                break
            redraws += 1
            if redraws > n_boot * 10:
                raise FwerError("bootstrap redraw budget exhausted — "
                                "trial index geometry degenerate.")
    return draws, redraws


def adjudicate(trials: dict[str, dict[int, float]]) -> dict[str, Any]:
    t_obs = {name: observed_t(s) for name, s in trials.items()}
    draws, redraws = mbb_max_t(trials)
    bar = float(np.quantile(draws, ALPHA_QUANTILE))
    max_name = max(t_obs, key=lambda k: t_obs[k])
    max_t = t_obs[max_name]
    passes = [n for n, t in t_obs.items() if t > bar and t >= T_FLOOR]
    return {
        "per_trial_t": {k: round(v, 4) for k, v in t_obs.items()},
        "per_trial_n": {k: len(s) for k, s in trials.items()},
        "max_t_trial": max_name,
        "max_t_observed": round(max_t, 4),
        "bootstrap_bar_q95": round(bar, 4),
        "t_floor": T_FLOOR,
        "block": BLOCK, "n_boot": N_BOOT, "seed": SEED,
        "one_sided": "positive",
        "n_redraws": redraws,
        "passing_trials": passes,
        "family_verdict_input": ("PASS" if passes else "CLEAN_NEGATIVE"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifacts-root", type=Path, required=True)
    p.add_argument("--trial", action="append", required=True,
                   metavar="NAME=DIR",
                   help="registered trial ref -> artifact dir name under "
                        "artifacts-root; exactly the eight run trials "
                        "(exclude_fold_0 is derived, never passed).")
    p.add_argument("--evidence-out", type=Path, default=None,
                   help="OPTIONAL committed-evidence sidecar: writes the "
                        "nine consumed fold series + the full verdict as "
                        "one JSON so the adjudication is reproducible from "
                        "git alone (codex #361 r2 P2 — output/ is "
                        "gitignored).")
    args = p.parse_args(argv)

    mapping: dict[str, str] = {}
    for spec in args.trial:
        name, _, dirname = spec.partition("=")
        mapping[name] = dirname
    if sorted(mapping) != sorted(RUN_TRIALS):
        raise FwerError(f"trials must be exactly {sorted(RUN_TRIALS)}; "
                        f"got {sorted(mapping)}.")

    trials: dict[str, dict[int, float]] = {}
    for name, dirname in mapping.items():
        expect_candidate, expect_slice, _geometry = RUN_TRIALS[name]
        series = load_trial_series(
            args.artifacts_root / dirname / "result.json",
            expect_candidate=expect_candidate, expect_slice=expect_slice)
        validate_trial_geometry(name, series)
        trials[name] = series
    trials[DERIVED_TRIAL] = derive_exclude_fold0(trials["C1_GPA"])
    validate_trial_geometry(DERIVED_TRIAL, trials[DERIVED_TRIAL])

    result = adjudicate(trials)
    result["artifact_dirs"] = mapping
    result["derived"] = {DERIVED_TRIAL: "C1_GPA minus fold 0 (E018)"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.artifacts_root / f"fwer_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verdict.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Gate-4A full-batch FWER verdict input (quality_profitability_v1)",
        "",
        f"- family verdict input: **{result['family_verdict_input']}**",
        f"- max observed t: {result['max_t_observed']:+.3f} "
        f"({result['max_t_trial']})",
        f"- bootstrap q95 bar: {result['bootstrap_bar_q95']:+.3f}; "
        f"hard floor t >= {T_FLOOR}",
        "- per-trial t: " + ", ".join(
            f"{k} {v:+.2f}" for k, v in result["per_trial_t"].items()),
        "",
        "Frozen three-state rules apply; the operator adjudicates. "
        "No auto-pivot.",
    ]
    (out_dir / "verdict.md").write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")
    if args.evidence_out is not None:
        evidence = {
            "protocol_id": PROTOCOL_ID, "gate": "4A",
            "consumed_fold_series": {
                name: {str(k): v for k, v in sorted(s.items())}
                for name, s in trials.items()},
            "verdict": result,
        }
        args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_out.write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"evidence sidecar: {args.evidence_out}")
    print("\n".join(lines))
    print(f"\nartifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

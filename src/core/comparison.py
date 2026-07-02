"""Run-comparison ruler (add-run-comparison-methodology, PR-2).

Compares walk-forward run B against baseline A on their PERSISTED per-fold daily
series (PR-1's ``daily_series`` block) — offline, CPU, no replay, no qlib, no bundle.

The point (see the openspec proposal): the old comparison averaged K per-fold scalar
IRs, whose CI is dominated by between-fold variance (the SE≈0.42 noise floor). This
ruler instead:

* **Pooled IR** over the TRUE-concatenated daily excess of the whole WF study protocol
  (includes the fold-boundary model switches + each fold starting from cash — it is the
  study protocol's realized IR, NOT a continuous production strategy's).
* **Paired moving-block bootstrap** of the daily A-vs-B excess difference on the shared
  dates (common market moves cancel), with an ACF-calibrated block length; annualized
  difference + 95% CI.
* A **fail-loud three-state verdict** ("indistinguishable at this power" when the CI
  straddles 0 — never a point-estimate winner), and when indistinguishable it MANDATES
  a diagnostic breakdown (gross vs net, IC, direction) so an "equally good, pick either"
  misread (the n_drop trap) cannot happen.
* Backtest excess is the primary arbiter; a disagreeing IC verdict is FLAGGED.
* Every output carries its limitation envelope (regime heterogeneity, block-length
  provenance, date-overlap fraction) and the pre-registration reference.

FAIL-LOUD everywhere: a run whose folds lack the ``daily_series`` substrate, or a
date-overlap below the floor, refuses a verdict rather than fabricating one.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from src.core.walk_forward.aggregate import FOLD_REPORT_SCHEMA_VERSION

_ANN = 252.0
DEFAULT_OVERLAP_FLOOR = 0.90
DEFAULT_MIN_PAIRED_DAYS = 20   # a paired bootstrap on fewer days gives a ~zero-width CI
DEFAULT_N_BOOT = 10000
DEFAULT_SEED = 42
_MAX_BLOCK = 40

REGIME_CAVEAT = (
    "CI narrows the SAMPLING SE (pooled + paired + block bootstrap for autocorrelation) "
    "but does NOT model regime heterogeneity — a COVID-2020 fold vs a calm fold carry "
    "structural uncertainty the bootstrap cannot resample away. A narrow CI is not "
    "certainty; a single-period OOS can leave a small real edge 'indistinguishable'."
)


class ComparisonError(RuntimeError):
    """Raised when a comparison cannot be made honestly (missing substrate, too little
    date overlap, missing pre-registration) — fail-loud, never a fabricated verdict."""


@dataclass(frozen=True)
class RunSeries:
    """A run's concatenated daily series, loaded from its per-fold reports."""
    run_dir: str
    excess: dict[str, float]          # net excess = return - bench - cost
    gross: dict[str, float]           # gross excess = return - bench
    ic: dict[str, float]              # daily 1d IC
    fold_boundary_dates: list[str]    # first date of each fold (for the seam bound)


@dataclass(frozen=True)
class RunComparison:
    baseline_dir: str
    treatment_dir: str
    n_paired_days: int
    overlap_fraction: float
    block_length: int
    block_length_source: str
    # pooled IR (study-protocol, true concatenation)
    pooled_net_ir_baseline: float
    pooled_net_ir_treatment: float
    pooled_gross_ir_baseline: float
    pooled_gross_ir_treatment: float
    # paired inferential result on NET excess (the primary arbiter)
    paired_net_ann_diff: float
    paired_net_se: float
    paired_net_ci95: tuple[float, float]
    verdict: str                      # "treatment_better" | "treatment_worse" | "indistinguishable"
    # diagnostics (ALWAYS emitted; the mandated companion of an indistinguishable verdict)
    diagnostics: dict[str, Any]
    contradiction_flag: str | None
    # honesty envelope
    seam_bound: dict[str, float]
    pre_registration_ref: str
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- load

def _read_json(p: Path) -> dict[str, Any]:
    with p.open(encoding="utf-8") as f:
        loaded: dict[str, Any] = json.load(f)
        return loaded


def load_run_daily_series(run_dir: str | Path) -> RunSeries:
    """Load + concatenate a run's per-fold ``daily_series``. FAIL-LOUD, actionably, if
    any fold predates the substrate (the message names the run and how to backfill)."""
    run = Path(run_dir)
    agg = run / "walk_forward_report.json"
    if not agg.is_file():
        raise ComparisonError(f"No walk_forward_report.json under {run} — not a run dir.")
    n_folds = int(_read_json(agg).get("num_folds", 0))
    if n_folds <= 0:
        raise ComparisonError(f"Run {run} reports num_folds={n_folds}.")

    excess: dict[str, float] = {}
    gross: dict[str, float] = {}
    ic: dict[str, float] = {}
    boundaries: list[str] = []
    for i in range(n_folds):
        fp = run / f"fold_{i:02d}_report.json"
        if not fp.is_file():
            raise ComparisonError(f"Run {run}: missing fold report {fp.name}.")
        rep = _read_json(fp)
        ds = rep.get("daily_series")
        if ds is None or rep.get("schema_version") != FOLD_REPORT_SCHEMA_VERSION:
            raise ComparisonError(
                f"Run {run}: fold {i} lacks the daily_series substrate "
                f"(schema_version={rep.get('schema_version')!r}, want "
                f"{FOLD_REPORT_SCHEMA_VERSION!r}). This run pre-dates the comparison "
                "contract and is NON-COMPARABLE. Backfill by re-running the walk-forward "
                "for this config (the daily series cannot be reconstructed post-hoc)."
            )
        comp = ds["components"]
        ret, bench = comp["return"], comp["bench"]  # net excess read from excess_return
        fold_dates = sorted(ds["excess_return"])
        if fold_dates:
            boundaries.append(fold_dates[0])
        for d in fold_dates:
            v = ds["excess_return"][d]
            if v is None:  # sanitized NaN (a gap day) — not part of the realized series
                continue
            if d in excess:
                # overlapping test windows share an OOS date across folds; collapsing by
                # date would silently drop one fold's realized day and shift IR/verdict.
                # Refuse rather than pick a winner arbitrarily (codex P2).
                raise ComparisonError(
                    f"Run {run}: duplicate OOS date {d} across folds (overlapping test "
                    "windows). Pooling/pairing by date would drop a realized fold-day; "
                    "refuse. Use non-overlapping test windows for run comparison."
                )
            excess[d] = float(v)
            gross[d] = float(ret[d]) - float(bench[d])
        for d, v in (ds.get("ic", {}).get("1", {}) or {}).items():
            if v is not None:
                ic[d] = float(v)
    if not excess:
        raise ComparisonError(f"Run {run}: no finite daily excess across any fold.")
    return RunSeries(str(run), excess, gross, ic, boundaries)


# ---------------------------------------------------------------------- statistics

def _annualized_ir(values: np.ndarray[Any, Any]) -> float:
    """Annualized IR of a daily series: mean/std * sqrt(252). NaN if <2 pts or zero std."""
    v = values[np.isfinite(values)]
    if v.size < 2:
        return float("nan")
    sd = float(v.std(ddof=1))
    # Guard a degenerate (constant) series: exact-zero std, OR a numerically-tiny std
    # from float error on identical-ish values (which would otherwise divide into an
    # absurd IR). 1e-15 sits far below any real daily-excess std (~1e-2).
    if not math.isfinite(sd) or sd <= 1e-15:
        return float("nan")
    return float(v.mean() / sd * math.sqrt(_ANN))


def estimate_block_length(diff: np.ndarray[Any, Any]) -> int:
    """Moving-block length = the autocorrelation-DECAY length of the difference series
    (first lag whose |ACF| falls below the ~2/sqrt(n) significance band), NOT a
    holding-period proxy. Clamped to [1, _MAX_BLOCK]."""
    n = diff.size
    if n < 8:
        return 1
    x = diff - diff.mean()
    denom = float((x * x).sum())
    if denom == 0.0:
        return 1
    thresh = 2.0 / math.sqrt(n)
    for lag in range(1, min(n // 2, _MAX_BLOCK)):
        ac = float((x[:-lag] * x[lag:]).sum()) / denom
        if abs(ac) < thresh:
            return max(lag, 1)
    # decay never observed within the checked lags -> the series is persistently
    # autocorrelated, so the block should be LONGEST (the cap), not a mid value; a short
    # block here would understate the bootstrap SE and overstate confidence (codex P1).
    return min(n // 2, _MAX_BLOCK)


def paired_block_bootstrap(
    diff: np.ndarray[Any, Any], block_len: int, n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED,
) -> tuple[float, float, float, float]:
    """Annualized mean of the paired daily difference + moving-block bootstrap SE / 95%
    CI. Block resampling honours autocorrelation (an i.i.d. bootstrap understates SE)."""
    n = diff.size
    block_len = max(1, min(block_len, n))
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_len))
    max_start = n - block_len
    starts = rng.integers(0, max_start + 1, size=(n_boot, n_blocks))
    offs = np.arange(block_len)
    idx = (starts[:, :, None] + offs[None, None, :]).reshape(n_boot, n_blocks * block_len)[:, :n]
    boot_ann = diff[idx].mean(axis=1) * _ANN
    ann = float(diff.mean() * _ANN)
    lo, hi = (float(x) for x in np.percentile(boot_ann, [2.5, 97.5]))
    return ann, float(boot_ann.std()), lo, hi


# ------------------------------------------------------------------------- compare

def _aligned(a: dict[str, float], b: dict[str, float]) -> tuple[list[str], np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    dates = sorted(set(a) & set(b))
    return dates, np.array([a[d] for d in dates]), np.array([b[d] for d in dates])


def compare_runs(
    baseline_dir: str | Path,
    treatment_dir: str | Path,
    *,
    pre_registration_ref: str,
    overlap_floor: float = DEFAULT_OVERLAP_FLOOR,
    min_paired_days: int = DEFAULT_MIN_PAIRED_DAYS,
    block_length: int | None = None,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
) -> RunComparison:
    """Compare treatment (B) vs baseline (A). FAIL-LOUD on missing substrate, on too
    little date overlap, or on a missing pre-registration reference."""
    if not str(pre_registration_ref or "").strip():
        raise ComparisonError(
            "A pre-registration reference (the committed hypothesis's git commit hash) "
            "is REQUIRED — design-time control of multiple comparisons. Refusing to run "
            "an unregistered comparison."
        )
    a = load_run_daily_series(baseline_dir)
    b = load_run_daily_series(treatment_dir)

    # date alignment on NET excess, overlap = intersection / SHORTER series
    dates, ea, eb = _aligned(a.excess, b.excess)
    shorter = min(len(a.excess), len(b.excess))
    overlap = (len(dates) / shorter) if shorter else 0.0
    if overlap < overlap_floor:
        raise ComparisonError(
            f"Date overlap {overlap:.1%} (intersection {len(dates)} / shorter {shorter}) "
            f"is below the floor {overlap_floor:.0%}: the runs barely share dates, so a "
            "paired comparison would be on a biased subset. Refusing a verdict."
        )
    if len(dates) < min_paired_days:
        raise ComparisonError(
            f"Only {len(dates)} shared finite day(s) (< min_paired_days={min_paired_days}): "
            "too few for a paired bootstrap. A 1-2 day 'CI' is ~zero-width and would declare "
            "a spurious winner from a point estimate with no estimable sampling uncertainty. "
            "Refusing a verdict — use longer runs."
        )

    diff = eb - ea
    if block_length is not None:
        # reject an out-of-range override up front, so the RECORDED block_length always
        # equals the one the bootstrap actually used (the bootstrap clamps internally;
        # recording the un-clamped override would make the CI non-reproducible — codex P2).
        cap = diff.size // 2
        if not (1 <= block_length <= cap):
            raise ComparisonError(
                f"block_length override {block_length} is out of range [1, {cap}] "
                f"(<= n_paired//2). A block near the full sample length ({diff.size}) "
                "collapses the moving-block bootstrap (max_start -> 0, every replicate is "
                "the whole sample) to a zero-width CI, which would fake a point-estimate "
                "verdict (codex P2). Pass a smaller value or omit it for the ACF default."
            )
        blk, blk_src = block_length, "operator-override"
    else:
        blk, blk_src = estimate_block_length(diff), "acf-decay"
    ann, se, lo, hi = paired_block_bootstrap(diff, blk, n_boot, seed)

    # verdict SIDE comes from the CI, NOT the point estimate: with the non-circular block
    # sampler the bootstrap CI can land opposite the sample mean, and the verdict must
    # never contradict its own reported CI (codex P2).
    if lo > 0:
        verdict = "treatment_better"
    elif hi < 0:
        verdict = "treatment_worse"
    else:
        verdict = "indistinguishable"

    # diagnostics (ALWAYS present; mandated companion of an indistinguishable verdict).
    # gross AND IC are measured over the SAME shared comparison dates as the net paired
    # test — otherwise out-of-comparison tail dates (the label-horizon case) could shift
    # the mean IC and flip the contradiction flag on dates that were never compared.
    ga = np.array([a.gross[d] for d in dates])
    gb = np.array([b.gross[d] for d in dates])
    ic_dates = [d for d in dates if d in a.ic and d in b.ic]
    ica = np.array([a.ic[d] for d in ic_dates])
    icb = np.array([b.ic[d] for d in ic_dates])
    mean_ic_a = float(ica.mean()) if ica.size else float("nan")
    mean_ic_b = float(icb.mean()) if icb.size else float("nan")
    diagnostics = {
        "net_ann_diff": ann,
        "gross_ir_baseline": _annualized_ir(ga),
        "gross_ir_treatment": _annualized_ir(gb),
        "mean_ic_baseline": mean_ic_a,
        "mean_ic_treatment": mean_ic_b,
        "n_ic_shared_days": len(ic_dates),
        "direction": (
            "undetermined (non-finite)" if not math.isfinite(ann)
            else "treatment>baseline" if ann > 0
            else "treatment<baseline" if ann < 0
            else "flat (treatment==baseline)"
        ),
        "note": (
            "'indistinguishable' means NOT statistically separable at this power — it is "
            "NOT 'equivalent'. Check the gross-vs-net and IC breakdown for a divergence "
            "the net-excess headline may mask (the n_drop lesson)."
        ),
    }

    # Backtest is authoritative, but surface ANY backtest-vs-IC divergence — INCLUDING
    # when the net is indistinguishable yet IC clearly favours a side (that is precisely
    # the masked-divergence case this ruler exists to expose), not only when the net is
    # conclusive.
    ic_diff = mean_ic_b - mean_ic_a
    contradiction = None
    if math.isfinite(ic_diff) and ic_diff != 0.0:
        ic_side = "treatment" if ic_diff > 0 else "baseline"
        if verdict == "treatment_better" and ic_diff < 0:
            contradiction = (
                f"backtest says treatment_better but IC favours BASELINE (mean_ic diff "
                f"{ic_diff:+.4f}); backtest is authoritative, divergence surfaced."
            )
        elif verdict == "treatment_worse" and ic_diff > 0:
            contradiction = (
                f"backtest says treatment_worse but IC favours TREATMENT (mean_ic diff "
                f"{ic_diff:+.4f}); backtest is authoritative, divergence surfaced."
            )
        elif verdict == "indistinguishable":
            contradiction = (
                f"net excess is INDISTINGUISHABLE but IC favours {ic_side} (mean_ic diff "
                f"{ic_diff:+.4f}); the net headline may hide an IC signal — investigate, "
                "do NOT read as 'equivalent'."
            )

    # seam bound: pooled net IR with fold-boundary days excluded vs included, for BOTH
    # runs — a large baseline seam can drive the comparison as much as a treatment one.
    # Computed over each run's FULL series minus THAT run's own boundary dates, so it
    # bounds the seam on the reported (full-series) pooled IR, not the intersection
    # (which would miss boundary/tail effects outside the shared dates — codex P2).
    def _seam(excess_map: dict[str, float], run_boundaries: set[str]) -> tuple[float, float, float]:
        full = sorted(excess_map)
        incl = _annualized_ir(np.array([excess_map[d] for d in full]))
        excl = _annualized_ir(np.array([excess_map[d] for d in full if d not in run_boundaries]))
        impact = (excl - incl) if (math.isfinite(incl) and math.isfinite(excl)) else float("nan")
        return incl, excl, impact

    incl_a, excl_a, imp_a = _seam(a.excess, set(a.fold_boundary_dates))
    incl_b, excl_b, imp_b = _seam(b.excess, set(b.fold_boundary_dates))
    seam_bound = {
        "baseline_pooled_net_ir_incl_boundary": incl_a,
        "baseline_pooled_net_ir_excl_boundary": excl_a,
        "baseline_seam_impact": imp_a,
        "treatment_pooled_net_ir_incl_boundary": incl_b,
        "treatment_pooled_net_ir_excl_boundary": excl_b,
        "treatment_seam_impact": imp_b,
    }

    return RunComparison(
        baseline_dir=str(baseline_dir),
        treatment_dir=str(treatment_dir),
        n_paired_days=len(dates),
        overlap_fraction=round(overlap, 4),
        block_length=blk,
        block_length_source=blk_src,
        # pooled IR is each run's FULL study-protocol series (all its OOS days), NOT the
        # paired intersection — the intersection is only for the paired bootstrap. In the
        # label-horizon tail-loss case, restricting these to shared dates would silently
        # discard valid run-specific days and misreport the study-protocol IR (codex P1).
        pooled_net_ir_baseline=_annualized_ir(np.array(list(a.excess.values()))),
        pooled_net_ir_treatment=_annualized_ir(np.array(list(b.excess.values()))),
        pooled_gross_ir_baseline=_annualized_ir(np.array(list(a.gross.values()))),
        pooled_gross_ir_treatment=_annualized_ir(np.array(list(b.gross.values()))),
        paired_net_ann_diff=round(ann, 6),
        paired_net_se=round(se, 6),
        paired_net_ci95=(round(lo, 6), round(hi, 6)),
        verdict=verdict,
        diagnostics=diagnostics,
        contradiction_flag=contradiction,
        seam_bound=seam_bound,
        pre_registration_ref=pre_registration_ref,
        caveats=[
            REGIME_CAVEAT,
            "pooled_*_ir are the WF STUDY-PROTOCOL realized IR (each run's full OOS series, "
            "INCLUDING fold-boundary model switches + each fold starting from cash) — NOT a "
            "continuous production strategy's return; do not read them as 'what running this "
            "live would earn'.",
            f"block_length={blk} ({blk_src}); moving-block bootstrap, n_boot={n_boot}, seed={seed}.",
            f"date overlap {overlap:.1%} of the shorter series ({len(dates)} shared days).",
        ],
    )

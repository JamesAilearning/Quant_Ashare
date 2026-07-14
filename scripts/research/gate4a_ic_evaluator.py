"""Gate-4A IC evaluator — per-candidate OOS factor validation (decision-level).

Implements the FROZEN pre-registration ``docs/prereg/quality_profitability.yaml``
(protocol quality_profitability_v1, frozen at main 4d9fab7, 2026-07-14):

  * primary_factor_metric: rank_ic_mean + ic_ir on the OOS dev folds
    (fold_0..fold_18, test 2020Q2 -> 2024Q4 — derived from the frozen run
    config chain, never hardcoded).
  * ic_forward_horizon = primary_holding_period (quarterly, decision (3)).
  * ranking: within_size_decile (Liu-Stambaugh-Yuan shell-value guard) —
    size = ``$total_mv`` from the canonical PIT bundle (operator-approved
    source, 2026-07-14): a daily market observable, same provider_uri the
    frozen presets pin, delisted names covered.
  * standardization_rule: as_of_or_earlier_only — every cross-section uses
    only data available on the rebalance day; no full-sample z-scores.
  * inputs are the AS-REPORTED as-of values (A-share income statements are
    YTD-cumulative). The frozen plan's permitted_transformations whitelist
    is exactly [cross_sectional_rank, within_size_decile] — TTM /
    annualization / seasonal alignment are UNREGISTERED variants and must
    NOT be "fixed in" here; a new registration + ledger entry is the only
    path to such a change.
  * universe: csi300_pit_ex_financials — bundle instruments intervals
    (PIT membership incl. delisted) minus the signed financial exclusion
    (live stock_basic graded L/D/P fetch, fail-loud, Step-A rule).
  * FWER: NOT computed here. The frozen rule is a FULL-BATCH
    block-bootstrap min-statistic test (all candidates + variants at once,
    t~=2.85); this evaluator persists the fold-level series that batch
    step will consume.

Pinned run semantics (decided at work-order level, echoed in the artifact):

  * Rebalance/signal day t_i = FIRST trading day of dev fold i's 3-month
    test window (cadence 63 + anchor fold_phase + phase 0 == one rebalance
    per fold at its start; the rebalance day is the signal-stamp day).
  * Execution day = t_i + signal_to_execution_lag(=1) trading days.
  * Forward return = close[execution day] -> close[LAST trading day of the
    SAME fold's test window]. Fold-contained by construction: no dev fold
    ever consumes a price after the frozen end_boundary (2024-12-31), so
    the signed 2025 holdout stays untouched; windows never overlap, so the
    fold IC series is overlap-free for t-stats.
  * Size deciles: total_mv as-of (last value <= t_i, staleness capped at
    MAX_MV_STALENESS_DAYS trading days — beyond that the name is DROPPED
    from that fold and counted, never silently ranked on stale size).
  * ALL data roots derive from the GATED frozen config chain — the qlib
    bundle (provider_uri), calendar, membership and the namechange
    snapshot (namechange_path) are frozen literals; there is NO CLI
    override, so an unregistered bundle can never ride a GATE ACCEPT
    (codex #354 r2 P1).
  * st_on (registered design, production-faithful): names that were
    ST/*ST on the execution day per the PIT namechange reconstruction
    (src/data/st_history.py) are excluded from the cross-section and
    counted, exactly as the canonical backtest drops them
    (codex #354 r2 P1). ST-off is a registered sensitivity slice, run
    separately, never silently.
  * Execution-day tradeability reuses the CANONICAL microstructure mask
    (src/core/microstructure_mask.py, audit P0-3): names suspended with a
    carried close (volume<1 / NaN) or one-price locked (high==low) on the
    execution day cannot actually fill — they are excluded from that
    fold's cross-section and counted, exactly as the canonical backtest
    masks them (codex #354 r1 P1).
  * Names with no close on the execution day (suspended) are dropped from
    that fold and counted. Names delisting mid-fold use the last available
    close <= fold end (realized, conservative) and are counted; a name
    with NO post-entry close at all marks at its entry close (return 0.0,
    the last available close <= fold end) and is counted separately —
    never silently dropped (codex #354 r1 P2).

Decision-level discipline (the script REFUSES to run otherwise):

  1. ``gate3_prereg_gate.py --candidate <id> --run-config <frozen stub>``
     must ACCEPT (subprocess, output archived into the run artifact).
  2. The ledger must carry a committed pre-run entry for this run — the
     gate's clean-tree + freeze-ordering checks enforce the commit part;
     registering the entry itself is the operator workflow.

Usage (C1 first — C2/C3 are registered but not yet implemented, fail-loud):

    python scripts/research/gate4a_ic_evaluator.py \\
        --candidate C1_GPA \\
        --store-dir D:/qlib_data/financial_pit_raw \\
        --provider-root D:/qlib_data/my_cn_data_pit \\
        --out output/gate4a

Outputs ``<out>/<candidate>_<runstamp>/report.md`` + ``result.json``
(fold-level rank_ic/ic series, diagnostics, full provenance).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.research.gate3_step_a_coverage_report import (  # noqa: E402
    fetch_financial_issuers,
    members_on,
    parse_membership,
)
from src.core.microstructure_mask import (  # noqa: E402
    MicrostructureMaskResult,
    compute_unavailable_mask,
)
from src.data.pit._common import qlib_to_ts_code  # noqa: E402
from src.data.st_history import (  # noqa: E402
    StLookup,
    assert_covers,
    build_st_lookup,
    is_st_on,
    load_namechange,
)
from src.data.trading_calendar import StaticTradingCalendar  # noqa: E402
from src.data.tushare.client import TushareClient  # noqa: E402
from src.research.financial_pit_view import FinancialPITDataView  # noqa: E402

GATE = "scripts/research/gate3_prereg_gate.py"
PLAN_REL = "docs/prereg/quality_profitability.yaml"

# Frozen run-config stubs per candidate (gate v10+: the config chain BINDS the
# candidate; the gate refuses any other pairing).
CANDIDATE_RUN_CONFIG = {
    "C1_GPA": "config/presets/quality_gate3_dev_c1_gpa.yaml",
    "C2_PROF": "config/presets/quality_gate3_dev_c2_prof.yaml",
    "C3_cash_based_OP": "config/presets/quality_gate3_dev_c3_cash_op.yaml",
}

# view fields per candidate (only C1 is implemented in this first pass —
# requesting an unimplemented candidate fails loud, it never guesses).
C1_FIELDS = ("revenue", "oper_cost", "total_assets")

N_SIZE_DECILES = 10
MAX_MV_STALENESS_DAYS = 20  # trading days; beyond -> drop from fold, counted


class EvaluatorError(RuntimeError):
    """Fail-loud: abort rather than emit optimistic confirmatory numbers."""


# ---------------------------------------------------------------------------
# Fold geometry — derived from the frozen run config chain (mirrors
# WalkForwardEngine's calendar-month arithmetic; asserted by unit tests
# against the documented 23-fold / 19-dev-fold setup).
# ---------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    return date(y, m + 1, d.day)


def load_config_chain(cfg_path: Path) -> dict[str, object]:
    """Shallow-merge the ``extends`` chain (child overrides parent)."""
    merged: dict[str, object] = {}
    cur = cfg_path
    for _ in range(6):
        data = yaml.safe_load(cur.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise EvaluatorError(f"run config is not a mapping: {cur}")
        for k, v in data.items():
            merged.setdefault(k, v)
        ext = data.get("extends")
        if not ext:
            return merged
        cur = (cur.parent / str(ext)).resolve()
    raise EvaluatorError("extends chain deeper than 5 — refusing.")


@dataclass(frozen=True)
class FoldWindow:
    index: int
    test_start: date   # calendar month anchor (1st of quarter month)
    test_end: date     # inclusive calendar end (last day BEFORE next anchor)


def dev_fold_windows(cfg: dict[str, object]) -> list[FoldWindow]:
    """Dev-fold test windows from the frozen config chain: anchor at
    overall_start, train+valid months to the first test start, step by
    step_months while the FULL test window fits within overall_end."""
    try:
        overall_start = date.fromisoformat(str(cfg["overall_start"]))
        overall_end = date.fromisoformat(str(cfg["overall_end"]))
        train_m = int(str(cfg["train_months"]))
        valid_m = int(str(cfg["valid_months"]))
        test_m = int(str(cfg["test_months"]))
        step_m = int(str(cfg["step_months"]))
    except (KeyError, ValueError) as exc:
        raise EvaluatorError(f"run config chain lacks fold geometry: {exc}") from exc
    folds: list[FoldWindow] = []
    i = 0
    while True:
        test_start = _add_months(overall_start, i * step_m + train_m + valid_m)
        test_end = _add_months(test_start, test_m) - timedelta(days=1)
        if test_end > overall_end:
            break
        folds.append(FoldWindow(i, test_start, test_end))
        i += 1
    if not folds:
        raise EvaluatorError("derived ZERO dev folds — config geometry wrong.")
    return folds


# ---------------------------------------------------------------------------
# Pure per-fold computations (unit-tested with synthetic frames).
# ---------------------------------------------------------------------------

def size_deciles_asof(total_mv: pd.DataFrame, day: date, codes: list[str],
                      calendar_days: list[date]) -> tuple[pd.Series, dict[str, int]]:
    """Decile (0..9) per ts_code from total_mv as-of ``day``.

    ``total_mv``: DataFrame indexed by trading date (datetime.date), columns
    = ts_codes, values = total market cap. As-of = last non-NA value at a
    trading date <= day, no older than MAX_MV_STALENESS_DAYS trading days.
    Returns (deciles, drop_counts).
    """
    eligible = [d for d in calendar_days if d <= day]
    if not eligible:
        raise EvaluatorError(f"no trading day <= {day} in calendar slice.")
    window = eligible[-(MAX_MV_STALENESS_DAYS + 1):]
    slab = total_mv.reindex(index=window, columns=codes)
    asof = slab.ffill().iloc[-1]
    dropped_stale = asof[asof.isna()].index.tolist()
    live = asof.dropna()
    counts = {"size_dropped_stale_or_missing": len(dropped_stale)}
    if len(live) < N_SIZE_DECILES * 2:
        raise EvaluatorError(
            f"only {len(live)} names carry usable total_mv on {day} — too few "
            f"for {N_SIZE_DECILES} deciles; refusing to rank on a sliver.")
    deciles = pd.qcut(live.rank(method="first"), N_SIZE_DECILES,
                      labels=False).astype(int)
    return deciles, counts


def within_decile_rank(factor: pd.Series, deciles: pd.Series) -> pd.Series:
    """Signal = factor rank within each size decile, mapped to (0,1)
    (as_of_or_earlier_only: pure cross-section of one day)."""
    joined = pd.concat({"f": factor, "d": deciles}, axis=1).dropna()
    if joined.empty:
        return pd.Series(dtype=float)
    return joined.groupby("d")["f"].transform(
        lambda s: s.rank(method="average") / (len(s) + 1))


def st_ts_codes_on(lookup: StLookup, universe: list[str],
                   day: date) -> frozenset[str]:
    """Names that were ST/*ST on ``day`` per the PIT namechange
    reconstruction — the registered design is st_on (production-faithful:
    the canonical backtest drops these on each execution day), so the
    Gate-4A cross-section must drop them too (codex #354 r2 P1)."""
    iso = day.isoformat()
    return frozenset(ts for ts in universe if is_st_on(lookup, ts, iso))


def masked_ts_codes_on(mask: MicrostructureMaskResult,
                       day: date) -> frozenset[str]:
    """ts_codes untradeable on ``day`` per the CANONICAL microstructure
    mask (suspended-with-carried-close / one-price locked) — the same
    states the canonical backtest refuses to fill (codex #354 r1 P1)."""
    iso = day.isoformat()
    return frozenset(qlib_to_ts_code(inst)
                     for d, inst in mask.masked if d == iso)


def forward_returns(close: pd.DataFrame, exec_day: date, fold_end_day: date,
                    codes: list[str]) -> tuple[pd.Series, dict[str, int]]:
    """close[exec_day] -> close[last available <= fold_end_day] per name.

    Suspended on exec day (no close) -> dropped + counted. Delisted/halted
    mid-fold -> last available close (realized, conservative) + counted.
    """
    if exec_day not in close.index:
        raise EvaluatorError(f"execution day {exec_day} not in price index — "
                             "calendar/price mismatch.")
    entry = close.loc[exec_day].reindex(codes)
    window = close.loc[(close.index > exec_day) & (close.index <= fold_end_day)]
    if window.empty:
        raise EvaluatorError(f"no price rows in ({exec_day}, {fold_end_day}] — "
                             "fold horizon empty.")
    slab = window.reindex(columns=codes)
    has_post = slab.notna().any()
    exit_px = slab.ffill().iloc[-1]
    # rule: exit = last available close <= fold_end. With zero post-entry
    # closes that IS the entry close (return 0.0) — counted, never
    # silently dropped (codex #354 r1 P2).
    exit_px = exit_px.where(has_post, entry)
    flat_no_post = int((entry.notna() & ~has_post).sum())
    truncated = int((slab.iloc[-1].isna() & has_post & entry.notna()).sum())
    dropped = int(entry.isna().sum())
    ret = (exit_px / entry) - 1.0
    counts = {"return_dropped_no_entry_close": dropped,
              "return_truncated_last_close": truncated,
              "return_flat_no_post_entry_close": flat_no_post}
    return ret.dropna(), counts


def fold_ic(signal: pd.Series, fwd_ret: pd.Series) -> dict[str, float | int]:
    joined = pd.concat({"s": signal, "r": fwd_ret}, axis=1).dropna()
    n = len(joined)
    if n < 30:
        raise EvaluatorError(f"only {n} names carry both signal and forward "
                             "return — refusing a sliver IC.")
    return {
        "n": n,
        "rank_ic": float(joined["s"].corr(joined["r"], method="spearman")),
        "ic": float(joined["s"].corr(joined["r"], method="pearson")),
    }


def monotonicity(signal: pd.Series, fwd_ret: pd.Series,
                 n_buckets: int = 10) -> list[float]:
    """Mean forward return per signal decile (bucket 0 = lowest signal)."""
    joined = pd.concat({"s": signal, "r": fwd_ret}, axis=1).dropna()
    buckets = pd.qcut(joined["s"].rank(method="first"), n_buckets, labels=False)
    return [float(x) for x in joined.groupby(buckets)["r"].mean()]


def aggregate(fold_rows: list[dict[str, float | int]]) -> dict[str, float | int]:
    ric = pd.Series([float(r["rank_ic"]) for r in fold_rows])
    ic = pd.Series([float(r["ic"]) for r in fold_rows])
    n = len(ric)
    out: dict[str, float | int] = {
        "n_folds": n,
        "rank_ic_mean": float(ric.mean()),
        "rank_ic_std": float(ric.std(ddof=1)),
        "ic_mean": float(ic.mean()),
        "ic_ir": float(ic.mean() / ic.std(ddof=1)) if float(ic.std(ddof=1)) else float("nan"),
        "rank_ic_ir": float(ric.mean() / ric.std(ddof=1)) if float(ric.std(ddof=1)) else float("nan"),
        "rank_ic_t": float(ric.mean() / (ric.std(ddof=1) / (n ** 0.5))) if n > 1 else float("nan"),
        "rank_ic_positive_folds": int((ric > 0).sum()),
    }
    return out


# ---------------------------------------------------------------------------
# Candidate formulas (C1 only in this pass; unknown/unimplemented fail loud).
# ---------------------------------------------------------------------------

def compute_c1_gpa(asof_frame: pd.DataFrame) -> pd.Series:
    """C1_GPA = (revenue - oper_cost) / total_assets, any input NA -> NA,
    non-positive total_assets -> NA (never a sign-flipped denominator)."""
    for col in C1_FIELDS:
        if col not in asof_frame.columns:
            raise EvaluatorError(f"as-of frame lacks field {col!r}.")
    f = asof_frame[list(C1_FIELDS)].apply(pd.to_numeric, errors="coerce")
    ta = f["total_assets"].where(f["total_assets"] > 0)
    return (f["revenue"] - f["oper_cost"]) / ta


CANDIDATE_FORMULAS = {"C1_GPA": (compute_c1_gpa, C1_FIELDS)}


# ---------------------------------------------------------------------------
# Orchestration (real data; not unit-tested — the gate + fail-loud checks
# plus the pure-function tests carry the correctness weight).
# ---------------------------------------------------------------------------

def run_gate(repo: Path, candidate: str, store_dir: Path,
             run_config_rel: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(repo / GATE), "--repo-root", str(repo),
         "--candidate", candidate, "--store-dir", str(store_dir),
         "--run-config", str(repo / run_config_rel)],
        capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    if proc.returncode != 0 or "GATE ACCEPT" not in out:
        raise EvaluatorError(
            "pre-registration gate REFUSED this run — fix the refusal, never "
            f"bypass it:\n{out}")
    return out


def load_qlib_frames(provider_root: Path, qlib_codes: list[str],
                     start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(close, total_mv) frames indexed by date, columns ts_code."""
    import qlib
    from qlib.config import C
    from qlib.data import D
    C["kernels"] = 1  # Windows non-interactive: multiproc D.features hangs
    qlib.init(provider_uri=str(provider_root), region="cn", kernels=1)
    raw = D.features(qlib_codes, ["$close", "$total_mv"],
                     start_time=start.isoformat(), end_time=end.isoformat(),
                     freq="day")
    if raw.empty:
        raise EvaluatorError("qlib returned an EMPTY panel for the dev window.")
    raw = raw.reset_index()
    raw.columns = ["qlib_code", "dt", "close", "total_mv"]
    raw["ts_code"] = [qlib_to_ts_code(str(c)) for c in raw["qlib_code"]]
    raw["d"] = pd.to_datetime(raw["dt"]).dt.date
    close = raw.pivot_table(index="d", columns="ts_code", values="close",
                            aggfunc="last")
    mv = raw.pivot_table(index="d", columns="ts_code", values="total_mv",
                         aggfunc="last")
    return close, mv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", required=True)
    p.add_argument("--store-dir", type=Path, required=True)
    # NOTE: no --provider-root override. The qlib bundle, calendar,
    # membership and namechange snapshot all derive from the GATED frozen
    # config chain (provider_uri / namechange_path are frozen literals) —
    # a caller-supplied path could evaluate an unregistered bundle while
    # archiving GATE ACCEPT (codex #354 r2 P1).
    p.add_argument("--out", type=Path, default=Path("output/gate4a"))
    p.add_argument("--repo-root", type=Path, default=_REPO)
    args = p.parse_args(argv)
    repo = args.repo_root

    if args.candidate not in CANDIDATE_RUN_CONFIG:
        raise EvaluatorError(f"candidate {args.candidate!r} has no frozen run "
                             f"config stub: {sorted(CANDIDATE_RUN_CONFIG)}")
    if args.candidate not in CANDIDATE_FORMULAS:
        raise EvaluatorError(
            f"candidate {args.candidate!r} is registered but its formula is "
            "NOT implemented in this evaluator yet — implement + review it; "
            "never run a guessed formula at decision level.")

    # 0. pre-registration gate (fail-loud, output archived)
    run_config_rel = CANDIDATE_RUN_CONFIG[args.candidate]
    gate_out = run_gate(repo, args.candidate, args.store_dir, run_config_rel)

    # 1. fold geometry from the frozen config chain
    cfg = load_config_chain((repo / run_config_rel).resolve())
    folds = dev_fold_windows(cfg)
    end_boundary = date.fromisoformat(str(cfg["overall_end"]))

    # 2. data roots DERIVED from the gated frozen config chain — never a
    # caller-supplied path (codex #354 r2 P1): provider_uri and
    # namechange_path are frozen literals in the self-contained parent
    # snapshot (the gate refuses ${...} placeholders and unfrozen chains).
    provider_uri = cfg.get("provider_uri")
    if not provider_uri:
        raise EvaluatorError("gated config chain carries no provider_uri — "
                             "cannot bind the qlib bundle.")
    provider_root = Path(str(provider_uri))
    if not provider_root.is_dir():
        raise EvaluatorError(f"frozen provider_uri {provider_root} is not a "
                             "directory on this machine — refusing to "
                             "substitute another bundle.")
    namechange_raw = cfg.get("namechange_path")
    if not namechange_raw:
        raise EvaluatorError("gated config chain carries no namechange_path — "
                             "the registered design is st_on; refusing to "
                             "run without the PIT ST mask.")
    namechange = load_namechange(Path(str(namechange_raw)))

    # 3. calendar + membership + financial exclusion + ST lookup
    cal_path = provider_root / "calendars" / "day.txt"
    cal_days = [date.fromisoformat(t.strip()) for t in
                cal_path.read_text(encoding="utf-8").split() if t.strip()]
    membership_path = provider_root / "instruments" / "csi300.txt"
    intervals = parse_membership(membership_path)
    client = TushareClient.from_environment()
    issuers, issuers_note = fetch_financial_issuers(client)

    assert_covers(namechange, end_boundary.isoformat())
    st_lookup = build_st_lookup(namechange)

    calendar = StaticTradingCalendar(cal_days)
    view = FinancialPITDataView(args.store_dir, calendar,
                                financial_issuers=issuers)
    formula, fields = CANDIDATE_FORMULAS[args.candidate]

    # 4. price/size panel over the dev span (one qlib load)
    span_start = min(f.test_start for f in folds)
    ever = sorted({ts for ts, _, _ in intervals})
    code_map = {ts: ts[-2:].upper() + ts[:6] for ts in ever}  # ts -> qlib
    close, mv = load_qlib_frames(provider_root,
                                 sorted(code_map.values()),
                                 _add_months(span_start, -3), end_boundary)
    # canonical microstructure mask over the dev span (codex #354 r1 P1):
    # execution-day untradeable names (suspension w/ carried close,
    # one-price lock) must not enter the IC cross-section — the canonical
    # backtest refuses these fills, so a decision-level IC must too.
    micro_mask = compute_unavailable_mask(
        sorted(code_map.values()),
        span_start.isoformat(), end_boundary.isoformat())

    # 4. per-fold evaluation
    fold_rows: list[dict[str, object]] = []
    ic_rows: list[dict[str, float | int]] = []
    for fw in folds:
        t_i = next(d for d in cal_days if d >= fw.test_start)
        exec_i = next(d for d in cal_days if d > t_i)
        fold_end = max(d for d in cal_days
                       if fw.test_start <= d <= min(fw.test_end, end_boundary))
        members = members_on(intervals, t_i)
        universe = [ts for ts in members if ts not in issuers]
        st_names = st_ts_codes_on(st_lookup, universe, exec_i)
        n_st = len(st_names)
        universe = [ts for ts in universe if ts not in st_names]
        untradeable = masked_ts_codes_on(micro_mask, exec_i)
        n_untradeable = sum(1 for ts in universe if ts in untradeable)
        universe = [ts for ts in universe if ts not in untradeable]
        asof = view.as_of(t_i.isoformat(), list(fields), universe)
        factor = formula(asof)
        deciles, size_counts = size_deciles_asof(mv, t_i, universe, cal_days)
        signal = within_decile_rank(factor, deciles)
        fwd, ret_counts = forward_returns(close, exec_i, fold_end, universe)
        ics = fold_ic(signal, fwd)
        ic_rows.append(ics)
        row: dict[str, object] = {
            "fold": fw.index, "signal_day": t_i.isoformat(),
            "execution_day": exec_i.isoformat(),
            "horizon_end": fold_end.isoformat(),
            "n_members": len(members),
            "exec_day_st_masked": n_st,
            "exec_day_untradeable_masked": n_untradeable,
            "n_universe_exfin_tradeable": len(universe),
            "n_factor_nonna": int(factor.notna().sum()),
            **size_counts, **ret_counts, **ics,
            "monotonicity_decile_means": monotonicity(signal, fwd),
        }
        # na_conditional_coverage: does factor-NA select a return regime?
        na_names = factor[factor.isna()].index
        both = fwd.reindex(na_names).dropna()
        row["na_names_mean_fwd_ret"] = (float(both.mean()) if len(both) else None)
        fold_rows.append(row)

    agg = aggregate(ic_rows)

    # 5. artifact
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out / f"{args.candidate}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_sha = hashlib.sha256(
        (repo / run_config_rel).read_bytes()).hexdigest()
    result = {
        "protocol_id": "quality_profitability_v1",
        "gate": "4A", "candidate": args.candidate,
        "run_config": run_config_rel, "run_config_sha256": cfg_sha,
        "generated_utc": stamp,
        "pinned_semantics": {
            "rebalance": "first trading day of each dev fold test window "
                         "(cadence 63 / fold_phase / phase 0)",
            "execution_lag_days": 1,
            "forward_return": "close[exec] -> close[last trading day of the "
                              "same fold] (fold-contained; never touches the "
                              "2025 holdout)",
            "size_source": "$total_mv (canonical PIT bundle, operator-approved "
                           "2026-07-14)",
            "size_staleness_cap_trading_days": MAX_MV_STALENESS_DAYS,
            "ranking": f"within_size_decile (n={N_SIZE_DECILES})",
            "standardization": "as_of_or_earlier_only",
            "execution_day_tradeability": "canonical microstructure mask "
                                          "(suspended w/ carried close, "
                                          "one-price lock) excluded + counted",
            "st_handling": "st_on — PIT namechange reconstruction, ST/*ST "
                           "on the execution day excluded + counted",
            "data_roots": "provider_uri / namechange_path from the gated "
                          "frozen config chain (no CLI override)",
        },
        "data_roots": {"provider_uri": str(provider_root),
                       "namechange_path": str(namechange_raw)},
        "financial_exclusion": {"n": len(issuers), "provenance": issuers_note},
        "microstructure_mask_span_counts": {
            "n_suspended": micro_mask.n_suspended,
            "n_one_price_days": micro_mask.n_one_price_days,
        },
        "aggregate": agg,
        "folds": fold_rows,
        "fwer_note": "batch-level FWER (block-bootstrap min-statistic, "
                     "t~=2.85) runs AFTER all candidates/variants — this "
                     "artifact only feeds it.",
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "gate_accept.txt").write_text(gate_out, encoding="utf-8")
    lines = [
        f"# Gate-4A IC report — {args.candidate} (quality_profitability_v1)",
        "",
        f"- folds: {agg['n_folds']} dev folds "
        f"({fold_rows[0]['signal_day']} -> {fold_rows[-1]['horizon_end']})",
        f"- rank_ic_mean: {agg['rank_ic_mean']:+.4f}  "
        f"(std {agg['rank_ic_std']:.4f}, t {agg['rank_ic_t']:+.2f}, "
        f"positive folds {agg['rank_ic_positive_folds']}/{agg['n_folds']})",
        f"- ic_ir: {agg['ic_ir']:+.3f}   rank_ic_ir: {agg['rank_ic_ir']:+.3f}",
        f"- financial exclusion: {len(issuers)} names ({issuers_note})",
        "",
        "VERDICT INPUT ONLY — no per-candidate p-threshold; the frozen "
        "full-batch FWER rule adjudicates after C1/C2/C3 + variants.",
    ]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nartifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Backtest runner — implements the canonical backtest runtime.

Bridges ``CanonicalBacktestInput`` to qlib's backtest engine and
produces ``CanonicalBacktestOutput`` with official metrics.

Boundaries
----------
- Uses ``qlib.backtest.backtest`` directly — the canonical anchored callable.
- Does NOT call ``qlib.init``. Requires prior canonical init.
- All input validation is delegated to ``CanonicalBacktestContract
  .validate_input()``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from src.contracts.benchmark_data_contract import validate_benchmark_values
from src.core.canonical_backtest_contract import (
    CANONICAL_OFFICIAL_BACKTEST_PATH,
    CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE,
    CANONICAL_OFFICIAL_METRIC_HELPER_PATH,
    OFFICIAL_METRIC_STATUS,
    CanonicalBacktestContract,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    compute_effective_stamp_tax_bps,
)
from src.core.logger import get_logger
from src.core.microstructure_mask import (
    MicrostructureMaskError,
    apply_mask_to_predictions,
    compute_unavailable_mask,
    ts_to_iso_date,
)
from src.core.qlib_runtime import (
    get_canonical_qlib_config,
    is_canonical_qlib_initialized,
)
from src.core.risk_constraints import (
    MinimalRiskConstraints,
    RiskConstraintError,
)
from src.data.st_history import (
    StHistoryError,
    assert_covers,
    build_st_lookup,
    compute_st_mask,
    load_namechange,
)

_logger = get_logger(__name__)

# PR-C (audit A1): version tag for the signal→execution timing semantics,
# folded into backtest provenance fingerprints and the walk-forward resume
# fingerprint. "lag_total_v2" = signal_to_execution_lag is the TOTAL
# signal→fill delay (qlib's built-in one-day shift included; lag=1 ⇒ no
# external restamp ⇒ T+1 fill). The unversioned pre-PR-C behavior restamped
# the full lag ON TOP of qlib's shift (lag=1 ⇒ T+2 fill); bumping this
# constant invalidates resume state and distinguishes provenance across the
# semantics change.
EXECUTION_TIMING_SEMANTICS = "lag_total_v2"

# PR-D (audit A2): version tag for the price-limit enforcement semantics,
# folded into backtest provenance fingerprints and the walk-forward resume
# fingerprint alongside EXECUTION_TIMING_SEMANTICS. "close_expr_v1" =
# limit_threshold reaches qlib as the Not-form close-ratio expression tuple
# (limits actually block fills; unverifiable moves block conservatively;
# the exchange universe is bounded to the tradable signal set). The
# untagged pre-PR-D behavior passed a float that qlib silently ignored on
# change-less bundles — the same config byte-for-byte now yields different
# official metrics, so the tag keeps provenance distinguishable and
# invalidates cross-semantics walk-forward resume.
PRICE_LIMIT_SEMANTICS = "close_expr_v1"


class BacktestRunnerError(RuntimeError):
    """Raised on backtest execution failures."""


class BacktestRunner:
    """Runs the canonical backtest pipeline.

    Usage::

        output = BacktestRunner.run(
            request=CanonicalBacktestInput(...),
            predictions=model_result.predictions,
        )
        print(output.risk_analysis)
    """

    @classmethod
    def run(
        cls,
        *,
        request: CanonicalBacktestInput,
        predictions: Any,
        topk: int = 50,
        n_drop: int = 5,
        compute_baselines: bool = True,
        pit_provider: Any | None = None,
        risk_constraints: MinimalRiskConstraints | None = None,
        namechange_path: str | None = None,
        st_audit_path: str | None = None,
        require_st_mask: bool = False,
    ) -> CanonicalBacktestOutput:
        # validate_input() enforces benchmark_code is non-empty as of the
        # contract level — no redundant check needed here.
        CanonicalBacktestContract.validate_input(request)

        if predictions is None or (hasattr(predictions, "empty") and predictions.empty):
            raise BacktestRunnerError("predictions must be non-empty.")

        # ``WalkForwardConfig`` and ``PipelineConfig`` already reject
        # ``n_drop >= topk`` at __post_init__ time, but ``BacktestRunner.run``
        # is also a public entry point used directly from research scripts.
        # Defence-in-depth: refuse the degenerate combination here so a
        # caller bypassing the config layer still gets a loud error
        # instead of a zero-position backtest that returns "valid"
        # all-zero metrics.
        if not isinstance(topk, int) or isinstance(topk, bool) or topk < 1:
            raise BacktestRunnerError(
                f"BacktestRunner.run: topk must be a positive int; got "
                f"{topk!r}."
            )
        if not isinstance(n_drop, int) or isinstance(n_drop, bool) or n_drop < 0:
            raise BacktestRunnerError(
                f"BacktestRunner.run: n_drop must be a non-negative int; "
                f"got {n_drop!r}."
            )
        if n_drop >= topk:
            raise BacktestRunnerError(
                f"BacktestRunner.run: n_drop ({n_drop}) must be strictly "
                f"less than topk ({topk}); otherwise TopkDropoutStrategy "
                "rotates out every name and the backtest returns silently "
                "with an empty portfolio."
            )

        if not is_canonical_qlib_initialized():
            raise BacktestRunnerError(
                "Canonical qlib runtime must be initialized via "
                "src.core.qlib_runtime.init_qlib_canonical(...) before "
                "running official backtests."
            )

        # Phase D.3 alignment guard: if the caller supplied a PIT
        # provider, the canonical qlib config's provider_uri MUST
        # match. Otherwise the backtest reads features through qlib
        # against a legacy provider while the operator believes PIT
        # mode is active — a silent survivorship bias on the most
        # consequential code path (real money decisions).
        if pit_provider is not None:
            cls._validate_pit_provider_alignment(pit_provider)

        runtime_config = get_canonical_qlib_config()
        if runtime_config is None:
            raise BacktestRunnerError(
                "Canonical qlib runtime reports initialized but has no "
                "recorded config; refusing to produce official metrics."
            )
        if request.adjust_mode != runtime_config.data_adjust_mode:
            raise BacktestRunnerError(
                "Canonical backtest adjust_mode does not match initialized "
                "qlib provider adjustment mode: "
                f"request.adjust_mode={request.adjust_mode!r}, "
                f"runtime.data_adjust_mode={runtime_config.data_adjust_mode!r}. "
                "Official metrics require matching data-adjustment semantics."
            )

        try:
            from qlib.backtest import backtest as qlib_backtest
            from qlib.backtest.executor import SimulatorExecutor
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
            from qlib.utils.time import Freq
        except ImportError as exc:
            # Name the actual failing import: qlib's core can be installed
            # and initialized while qlib.backtest's own import chain breaks
            # on a missing/incompatible transitive dep — "qlib is not
            # importable" alone sent that diagnosis in the wrong direction.
            raise BacktestRunnerError(
                "qlib backtest stack is not importable; cannot run backtest. "
                f"Underlying import failure: {exc!r}"
            ) from exc

        # Official risk metrics must go through the governance-anchored helper,
        # not a direct import — this keeps the runtime path aligned with the
        # path that governance locks (see tests/governance/test_no_alt_backtest_path.py).
        if CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE is None:
            raise BacktestRunnerError(
                "Canonical metric helper "
                f"({CANONICAL_OFFICIAL_METRIC_HELPER_PATH}) is not importable; "
                "cannot compute official risk metrics."
            )
        risk_analysis = CANONICAL_OFFICIAL_METRIC_HELPER_CALLABLE

        # Map CanonicalExchangeConfig → qlib exchange_kwargs
        cost = request.exchange_config.cost_model

        # Resolve the time-ordered stamp-tax schedule into the single
        # scalar that qlib's ``exchange_kwargs["close_cost"]``
        # accepts. The helper returns a trading-day-weighted average
        # when the backtest period crosses one or more rate
        # transitions (e.g. the 2023-08-28 CN reform 10→5 bps), AND
        # the list of transitions actually crossed. We WARN once per
        # run when crossings happen so the operator can decide
        # whether the weighted scalar is good enough or whether they
        # need to split the backtest at the transition.
        #
        # The qlib calendar IS passed so the weighting matches what
        # qlib's executor actually charges per sell. Without the
        # calendar the helper falls back to calendar-day weighting,
        # which produces a different scalar when holidays cluster
        # asymmetrically around a transition (e.g. CN long-holiday
        # in October before a hypothetical reform on Oct 15 would
        # over-weight the post-reform side because it skips the
        # holiday days on the pre-reform side). Codex P2 follow-up
        # on PR #178.
        #
        # Audit P0-4 + add-stamp-tax-schedule.
        period_start = date.fromisoformat(request.evaluation_start)
        period_end = date.fromisoformat(request.evaluation_end)
        try:
            from qlib.data import D as _qlib_D
            trading_calendar_ts = _qlib_D.calendar(
                start_time=request.evaluation_start,
                end_time=request.evaluation_end,
            )
            trading_calendar: list[date] = [
                ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                for ts in trading_calendar_ts
            ]
        except Exception as exc:
            # Hard-fail rather than fall back to calendar-day
            # weighting. The repo's "no silent fallback" rule
            # forbids degrading the official metrics path on a
            # canonical runtime data failure — calendar-day
            # weighting would produce a different ``close_cost``
            # than what qlib's executor actually charges per sell,
            # silently shifting the official scalar from the
            # documented trading-day-weighted value. Codex P1
            # follow-up on PR #178. If you genuinely need to
            # proceed without a calendar (e.g. a contract-only
            # unit test), patch ``qlib.data.D.calendar`` at the
            # test boundary; production runs must raise here.
            raise BacktestRunnerError(
                "BacktestRunner: failed to fetch qlib trading "
                f"calendar for stamp-tax weighting ({type(exc).__name__}: "
                f"{exc}). The official cost model requires the trading-day "
                "calendar to weight per-segment rates across schedule "
                "transitions; falling back to calendar-day weighting "
                "would produce a scalar that does not match what qlib's "
                "executor charges per sell. Verify canonical qlib init "
                "and that ``provider_uri`` points at a bundle covering "
                f"[{request.evaluation_start}, {request.evaluation_end}]."
            ) from exc
        effective_stamp_tax = compute_effective_stamp_tax_bps(
            cost.stamp_tax_schedule,
            period_start,
            period_end,
            calendar=trading_calendar,
        )
        if effective_stamp_tax.transitions:
            schedule_repr = ", ".join(
                f"{entry.effective_from.isoformat()}={entry.bps}bps"
                for entry in cost.stamp_tax_schedule
            )
            crossed_repr = ", ".join(
                entry.effective_from.isoformat()
                for entry in effective_stamp_tax.transitions
            )
            _logger.warning(
                "BacktestRunner: backtest period %s → %s crosses "
                "stamp-tax transition(s) at %s. Using trading-day-"
                "weighted scalar %.4f bps; the actual per-day cost is "
                "time-varying (full schedule: %s). To get exact "
                "per-segment costs, split the backtest at the "
                "transition(s) and reconcile the per-segment "
                "outputs externally. Audit P0-4.",
                request.evaluation_start, request.evaluation_end,
                crossed_repr, effective_stamp_tax.bps, schedule_repr,
            )
        stamp_tax_fraction = effective_stamp_tax.bps / 10000.0
        slippage_fraction = cost.slippage_bps / 10000.0

        # PR-J: fail-loud VALUE-level validation of the benchmark series this
        # backtest consumes for excess-return — BEFORE qlib reads it below.
        # Every excess number depends on the benchmark, so a corrupt/implausible
        # one must stop the run, not silently skew the metrics. Placed after the
        # stamp-tax calendar fetch so a calendar-load failure still surfaces as
        # itself; still strictly before the qlib backtest consumes the benchmark.
        cls._validate_consumed_benchmark(
            request.benchmark_code,
            request.evaluation_start,
            request.evaluation_end,
        )
        # Price-limit enforcement (PR-D, audit A2): the contract's
        # ``limit_threshold`` float is translated into qlib's EXPRESSION-mode
        # tuple ``(limit_buy_expr, limit_sell_expr)`` — NEVER passed through
        # as a float. qlib's float mode keys on the STORED ``$change`` field,
        # which the PIT bundle deliberately does not produce; qlib then
        # evaluates ``NaN >= threshold`` == False for every row and the limit
        # checks silently disable — backtests could buy at limit-up and sell
        # at limit-down, inflating returns.
        #
        # The expressions run on stored ADJUSTED closes (the bundle ships no
        # raw prices) and that is the exchange-correct test, not an
        # approximation: tushare's adj_factor is built from the exchange-
        # published previous close (the rounded 除权除息参考价), so on
        # ex-dividend/ex-rights days — 配股 included — the adjusted ratio
        # equals the exchange's own move vs its limit reference. Verified
        # against exchange pre_close on all 34,597 adj-factor-jump days
        # 2021-2025: zero missed main-board limit closes (243/243 up, 52/52
        # down); 99.9% of divergences < 0.1pp vs the 0.5pp buffer in 0.095;
        # raw-price ratios would instead diverge by the full event magnitude
        # on every ex-date. Residuals (all conservative for the csi800
        # non-ST path): ST 重整除权 factor disagreements (3 in 5y, ST is
        # masked anyway) and rare factor restatements (≤9 in 5y, over-block
        # direction only).
        #
        # ``Not(move <= thr)`` rather than ``move > thr``: when the previous
        # close is NaN (resumption day after a suspension, or a ticker's
        # first bundle day) the move is UNVERIFIABLE — numpy comparisons on
        # NaN are False, so the ``>`` form would silently PERMIT the fill
        # (the same liberal failure class as the dead float mode), while the
        # Not-form blocks it. Unverifiable ⇒ untradeable, matching the
        # microstructure mask's philosophy; cost is a one-day fill delay on
        # resumptions. Uniform magnitude across boards is a documented
        # conservative bias (688/300 ±20%, BJ ±30%, ST ±5% refinement is
        # backlogged — audit A4).
        limit_magnitude = float(request.exchange_config.limit_threshold)
        limit_expressions = (
            f"Not($close/Ref($close,1)-1 <= {limit_magnitude})",
            f"Not($close/Ref($close,1)-1 >= -{limit_magnitude})",
        )
        exchange_kwargs = {
            "freq": request.exchange_config.freq,
            "deal_price": request.exchange_config.execution_price_kind,
            "open_cost": cost.commission_rate + slippage_fraction,
            "close_cost": cost.commission_rate + stamp_tax_fraction + slippage_fraction,
            "min_cost": cost.min_cost,
            "limit_threshold": limit_expressions,
        }

        # Map signal_to_execution_lag onto the TWO shifts in the chain (PR-C,
        # audit A1). qlib's ``TopkDropoutStrategy`` already consumes, on trade
        # day D, the signal stamped D-1 (``get_step_time(trade_step,
        # shift=1)`` in qlib/contrib/strategy/signal_strategy.py) — a built-in
        # one-trading-day delay. ``signal_to_execution_lag`` is the TOTAL
        # signal→fill delay, so the external restamp applied here is
        # ``lag - 1`` rows: lag=1 (T+1 execution, the default) needs NO
        # external restamp; lag=2 restamps one row. lag=0 is REJECTED by the
        # canonical contract — same-day fills require a backward restamp
        # (look-ahead) and this runner stamps every output official (codex
        # P1 on PR #241). Before PR-C the full lag was restamped ON TOP of
        # qlib's built-in shift, so every official backtest filled on T+2
        # and traded a one-day-stale signal.
        shifted_predictions = cls._apply_lag(
            predictions, request.signal_to_execution_lag - 1,
        )

        # A-share microstructure mask (audit P0-3 /
        # openspec/changes/add-microstructure-mask). Drop every
        # (date, instrument) row in the predictions Series whose
        # TRUE EXECUTION DAY is a suspended day (volume <= 0 or
        # close NaN) or a one-price-lock day (high == low). qlib's
        # ``TopkDropoutStrategy`` would otherwise pick those rows
        # by score and the executor would report phantom fills at
        # the carried-forward close (suspended) or the locked
        # limit price (one-price). Routes OHLCV fetch through PIT
        # when supplied (audit P0-6 compliance — when no provider,
        # the helper's direct ``D.features`` call is allow-listed).
        #
        # Execution-day keying (PR-C): a signal stamped S fills on
        # the NEXT trading day after S (qlib's built-in shift — see
        # the lag mapping above), so the mask must be matched at
        # S+1, not S. ``apply_mask_to_predictions`` matches by the
        # series' STAMPED date, so each masked (execution_day,
        # instrument) pair is translated BACK to the stamp that
        # would fill on it (the preceding trading day). A masked
        # first-calendar-day has no in-window stamp; a signal
        # stamped on evaluation_end has no in-window execution day
        # and is both unmaskable and untradeable by construction.
        # This holds for every lag value: the restamp above already
        # moved stamps so that fill day == stamp + 1 trading day.
        instruments_in_predictions = sorted({
            str(inst)
            for inst in shifted_predictions.index.get_level_values("instrument").unique()
        })

        try:
            mask_result = compute_unavailable_mask(
                instruments=instruments_in_predictions,
                start_date=request.evaluation_start,
                end_date=request.evaluation_end,
                pit_provider=pit_provider,
            )
        except MicrostructureMaskError as exc:
            raise BacktestRunnerError(
                f"BacktestRunner.run: microstructure mask "
                f"computation failed ({exc}). The canonical backtest "
                "path requires a valid mask before strategy "
                "construction — refuse to fall back to unmasked "
                "predictions. Audit P0-3."
            ) from exc
        # The remap calendar is padded 20 calendar days BEFORE
        # evaluation_start (codex P2 on PR #241): a prediction stamped on the
        # trading day immediately before the window is consumed by qlib on
        # the FIRST evaluation day, so that day's mask entries must translate
        # back to the pre-window stamp — an unpadded calendar would let a
        # first-day suspension/ST fill slip through unmasked. 20 days covers
        # the longest CN holiday gap. The stamp-tax calendar above stays
        # window-exact (its weighting depends on it).
        try:
            from qlib.data import D as _remap_D
            remap_start = (
                date.fromisoformat(request.evaluation_start)
                - timedelta(days=20)
            ).isoformat()
            remap_calendar_ts = _remap_D.calendar(
                start_time=remap_start, end_time=request.evaluation_end,
            )
        except Exception as exc:
            raise BacktestRunnerError(
                f"BacktestRunner.run: trading-calendar fetch for the "
                f"execution-day mask remap failed ({exc}). Refusing to fall "
                "back to stamp-day masking. Audit A1 / PR-C."
            ) from exc
        iso_calendar = [ts_to_iso_date(ts) for ts in remap_calendar_ts]
        stamp_of_execution_day = {
            iso_calendar[i]: iso_calendar[i - 1]
            for i in range(1, len(iso_calendar))
        }
        execution_day_of_stamp = {
            iso_calendar[i - 1]: iso_calendar[i]
            for i in range(1, len(iso_calendar))
        }
        micro_mask_on_stamps = frozenset(
            (stamp_of_execution_day[day], inst)
            for day, inst in mask_result.masked
            if day in stamp_of_execution_day
        )
        shifted_predictions, n_masked_dropped = apply_mask_to_predictions(
            shifted_predictions, micro_mask_on_stamps,
        )
        if mask_result.masked:
            _logger.warning(
                "BacktestRunner: microstructure mask dropped %d "
                "(date, instrument) candidates from predictions before "
                "strategy construction — %d suspended (volume<=0 or "
                "NaN close), %d one-price-locked (high==low). The "
                "filtered Series has %d fewer rows than the input. "
                "Audit P0-3.",
                mask_result.total_masked,
                mask_result.n_suspended,
                mask_result.n_one_price_days,
                n_masked_dropped,
            )

        # A-share ST/*ST mask (C2-d PR2) — parallel to the microstructure mask.
        # Drops rows whose instrument was ST/*ST on the TRUE EXECUTION DAY
        # (stamp + 1 trading day — see the execution-day keying note above),
        # reconstructed point-in-time from the tushare namechange table
        # (as-of start_date; see src/data/st_history.py). ST is a
        # SELECTION-time exclusion only — the model was trained on the full
        # panel (ST included) upstream.
        # ST mask provenance — recorded in the fingerprint below so two runs of
        # the same request with different ST inputs (off vs on, or a different
        # namechange snapshot) get DIFFERENT fingerprints despite different
        # official metrics (Codex P2 on #223).
        st_mask_provenance: dict[str, Any] = {"namechange_path": None}
        if namechange_path is None or not str(namechange_path).strip():
            # OFFICIAL paths (the single-fold pipeline and the walk-forward
            # engine) pass require_st_mask=True so a missing namechange_path
            # is a HARD error, aligning the single-fold backtest with the
            # walk-forward and live recommend paths, which exclude ST (audit
            # E1 / PR-F). The WARN-pass survives ONLY for raw research/unit
            # callers that deliberately run an ST-included universe — the
            # backward-compatible default the governance tests rely on.
            if require_st_mask:
                raise BacktestRunnerError(
                    "BacktestRunner.run: ST mask is REQUIRED on the official "
                    "backtest path but no namechange_path was supplied. The "
                    "single-fold backtest must exclude ST/*ST names exactly "
                    "like the walk-forward and live recommend paths — set "
                    "namechange_path (config.yaml: "
                    "${QUANT_NAMECHANGE_PATH:-…/all_namechanges.parquet}). "
                    "Refusing to emit official metrics over an ST-included "
                    "universe (audit E1 / PR-F)."
                )
            _logger.warning(
                "BacktestRunner: ST mask DISABLED (no namechange_path) — this "
                "backtest's universe still includes ST/*ST names. Set "
                "namechange_path to exclude them (C2-d PR2). This WARN-pass is "
                "for research/raw callers only; official runs pass "
                "require_st_mask=True and would fail here."
            )
        else:
            try:
                namechange = load_namechange(namechange_path)
                assert_covers(namechange, request.evaluation_end)
                st_lookup = build_st_lookup(namechange)
            except StHistoryError as exc:
                raise BacktestRunnerError(
                    f"BacktestRunner.run: ST mask construction failed ({exc}). "
                    "Refusing to fall back to an ST-unmasked backtest."
                ) from exc
            with open(namechange_path, "rb") as nc_file:
                namechange_sha = hashlib.sha256(nc_file.read()).hexdigest()[:16]
            # Build the lookup pairs on EXECUTION days (stamp + 1 trading
            # day): the ST question is "is this name ST on the day the fill
            # would happen", not on the signal's stamp. The attribution
            # records therefore carry execution dates. Stamps without an
            # in-window execution day (stamped on evaluation_end) never fill
            # and are skipped. The masked execution-day pairs are translated
            # back to stamps for ``apply_mask_to_predictions``, which matches
            # by the series' stamped date.
            st_pairs = []
            for ts, inst in zip(
                shifted_predictions.index.get_level_values("datetime"),
                shifted_predictions.index.get_level_values("instrument"),
                strict=True,
            ):
                stamp_iso = ts_to_iso_date(ts)
                exec_iso = execution_day_of_stamp.get(stamp_iso)
                if exec_iso is None:
                    continue
                st_pairs.append((exec_iso, str(inst)))
            st_mask_exec, st_attribution = compute_st_mask(st_pairs, st_lookup)
            st_mask = frozenset(
                (stamp_of_execution_day[day], inst)
                for day, inst in st_mask_exec
                if day in stamp_of_execution_day
            )
            shifted_predictions, n_st_dropped = apply_mask_to_predictions(
                shifted_predictions, st_mask,
            )
            if n_st_dropped:
                sample = sorted({inst for _d, inst in st_mask})[:5]
                _logger.warning(
                    "BacktestRunner: ST mask dropped %d (date, instrument) "
                    "candidates (PIT-historical ST/*ST) before strategy "
                    "construction; %d distinct instrument(s) (e.g. %s). "
                    "C2-d PR2.",
                    n_st_dropped, len({i for _d, i in st_mask}), sample,
                )
            if st_audit_path is not None:
                import csv
                with open(
                    st_audit_path, "w", newline="", encoding="utf-8-sig",
                ) as audit_file:
                    writer = csv.DictWriter(
                        audit_file,
                        fieldnames=["date", "instrument", "ts_code", "name"],
                    )
                    writer.writeheader()
                    writer.writerows(st_attribution)
                _logger.info(
                    "BacktestRunner: wrote ST mask audit (%d row(s)) -> %s",
                    len(st_attribution), st_audit_path,
                )
            st_mask_provenance = {
                "namechange_path": namechange_path,
                "namechange_sha256": namechange_sha,
                "n_st_masked": n_st_dropped,
            }

        # Bound the exchange's quote universe to the FINAL tradable signal
        # universe — post-mask, benchmark EXCLUDED (codex P2 rounds 2+4 on
        # PR #242). Without ``codes``, qlib loads the provider's ENTIRE
        # universe and a missing ``$factor`` anywhere in it disables
        # trade_unit for the whole run; and an untraded symbol inside codes
        # (the benchmark index has close but no factor; a name the masks
        # fully removed) would itself trigger that global degradation. The
        # benchmark reaches qlib separately via the ``benchmark`` argument
        # and is never traded; the strategy only trades signal names and
        # positions originate from prior signal days, so nothing tradeable
        # lives outside this set. An EMPTY post-mask universe fails loud:
        # qlib would silently substitute the FULL provider universe for
        # empty codes (exchange.py replaces falsy codes with
        # ``D.instruments()``), load every quote to trade nothing, and the
        # run would emit zero-position metrics stamped official — the
        # degenerate outcome the no-silent-fallback rule exists to prevent.
        exchange_codes = sorted({
            str(inst)
            for inst in shifted_predictions.index.get_level_values("instrument").unique()
        })
        if not exchange_codes:
            raise BacktestRunnerError(
                "BacktestRunner.run: every prediction row was removed by the "
                "microstructure/ST masks — the tradable universe is empty. "
                "Refusing to produce official zero-position metrics from a "
                "fully-masked signal; inspect the masks and the prediction "
                "window."
            )
        exchange_kwargs["codes"] = exchange_codes

        # Round-lot capability preflight (PR-D): qlib's Exchange switches to
        # adjusted-price mode and DISABLES trade_unit — fractional fills
        # instead of 100-share A-share round lots — as soon as ANY quoted
        # row lacks a usable ``$factor``, and says so only in its own
        # low-visibility log. Probe EXACTLY the universe the exchange will
        # load (``exchange_codes`` above), mirroring qlib's degradation
        # condition: ``$factor`` NaN on a row whose ``$close`` is PRESENT
        # (codex P3 round 3 — a NaN factor on suspended rows, where close is
        # also NaN, keeps round lots and must not false-fire). Diagnostic
        # only (warning, never a block): an unprobeable provider is reported
        # the same way rather than failing the official path.
        try:
            from qlib.data import D
            _logger.warning(
                "BacktestRunner: round-lot preflight probes $factor/$close "
                "directly via qlib D.features (allow-listed diagnostic — "
                "the post-delist mask is irrelevant to a factor-availability "
                "check on the run's own candidate set). Audit P0-6."
            )
            _factor_probe = D.features(
                exchange_codes,
                ["$factor", "$close"],
                start_time=request.evaluation_start,
                end_time=request.evaluation_end,
                freq="day",
            )
            _factor_col = _factor_probe.iloc[:, 0]
            _close_col = _factor_probe.iloc[:, 1]
            factor_usable = (
                len(_factor_probe) > 0
                and not bool((_factor_col.isna() & _close_col.notna()).any())
            )
        except Exception:  # diagnostic probe only — never block on it
            factor_usable = False
        if not factor_usable:
            _logger.warning(
                "BacktestRunner: $factor is missing or incomplete across "
                "the exchange universe — qlib trades in adjusted-price "
                "mode with trade_unit (100-share round lots) DISABLED, "
                "so fills may be fractional. Metrics remain valid but "
                "ignore round-lot frictions. Ship factor bins in the "
                "bundle to restore round-lot simulation (PR-D preflight; "
                "audit A2 sibling)."
            )

        strategy = TopkDropoutStrategy(
            signal=shifted_predictions,
            topk=topk,
            n_drop=n_drop,
        )

        executor = SimulatorExecutor(
            time_per_step=request.exchange_config.freq,
            generate_portfolio_metrics=True,
        )

        try:
            portfolio_metric_dict, indicator_dict = qlib_backtest(
                start_time=request.evaluation_start,
                end_time=request.evaluation_end,
                strategy=strategy,
                executor=executor,
                account=request.account_config.init_cash,
                benchmark=request.benchmark_code,
                exchange_kwargs=exchange_kwargs,
            )
        except Exception as exc:
            raise BacktestRunnerError(
                f"qlib backtest execution failed: {exc}"
            ) from exc

        # Extract report from portfolio_metric_dict
        analysis_freq = "{}{}".format(*Freq.parse(request.exchange_config.freq))
        freq_result = portfolio_metric_dict.get(analysis_freq)
        if freq_result is None:
            raise BacktestRunnerError(
                f"No portfolio metrics for freq '{analysis_freq}'. "
                "Check that generate_portfolio_metrics=True."
            )
        report_normal, positions_normal = freq_result

        if report_normal is None or report_normal.empty:
            raise BacktestRunnerError(
                "Backtest produced no results. Check date ranges and predictions."
            )

        # Extract risk analysis. Use the *configured* exchange frequency
        # so qlib's annualisation factor matches the underlying data
        # cadence. The previous hardcoded ``freq="day"`` would have
        # over-stated annualised return / Sharpe by 2-4× if a future
        # caller ever ran an hourly or minute-level backtest.
        try:
            excess_return_without_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"],
                freq=request.exchange_config.freq,
            )
            excess_return_with_cost = risk_analysis(
                report_normal["return"] - report_normal["bench"] - report_normal["cost"],
                freq=request.exchange_config.freq,
            )
        except Exception as exc:
            raise BacktestRunnerError(
                f"Risk analysis extraction failed: {exc}"
            ) from exc

        risk_dict = {
            "excess_return_without_cost": _risk_analysis_to_flat_dict(excess_return_without_cost),
            "excess_return_with_cost": _risk_analysis_to_flat_dict(excess_return_with_cost),
        }

        return_series = {
            "return": _series_to_dict(report_normal["return"], name="return"),
            "bench": _series_to_dict(report_normal["bench"], name="bench"),
            "cost": _series_to_dict(report_normal["cost"], name="cost"),
        }

        # Compute equal-weight top-k baseline post-hoc from predictions +
        # qlib close prices. Avoids a second full backtest run (~50%
        # overhead) by using the same position set with 1/topk weights.
        if compute_baselines:
            try:
                eqw_returns = cls._compute_equalweight_baseline(
                    predictions=shifted_predictions,
                    topk=topk,
                    evaluation_start=request.evaluation_start,
                    evaluation_end=request.evaluation_end,
                    pit_provider=pit_provider,
                )
                if eqw_returns:
                    return_series["equalweight_topk"] = eqw_returns
            except BacktestRunnerError as exc:
                _logger.warning(
                    "Equal-weight baseline skipped: %s. Strategy "
                    "backtest and risk_analysis remain valid.",
                    exc,
                )

        positions_map = _positions_to_weight_map(positions_normal)

        # Risk-constraints layer (audit P0-1 /
        # openspec/changes/add-minimal-risk-constraints). Post-trade:
        # ``return_series`` and ``risk_analysis`` above were computed
        # from qlib's unclipped execution. To keep the canonical
        # output internally consistent, ``positions`` ALSO stays
        # tied to qlib's unclipped execution — downstream consumers
        # (PerformanceAttribution, pipeline_result_artifacts) use
        # ``positions`` as the authoritative portfolio that
        # produced the returns, so a clipped substitution would
        # give them an attribution / holdings record that does NOT
        # match the official numbers. Instead, the clipped map
        # lives on the sibling field ``positions_clipped`` —
        # populated only in WARN_AND_CLIP mode AND only when at
        # least one clip happened. Codex P1 follow-up on PR #179.
        positions_clipped: Mapping[str, Mapping[str, float]] = {}
        if risk_constraints is None:
            _logger.warning(
                "BacktestRunner.run: ``risk_constraints`` was not "
                "supplied — the backtest ran with NO position-level "
                "risk constraints active. Single-name / single-board "
                "concentration, cash-buffer-min, and leverage caps "
                "are all unbounded. Pass a "
                "``MinimalRiskConstraints(...)`` instance to opt in. "
                "Audit P0-1."
            )
        else:
            try:
                apply_result = risk_constraints.apply(positions_map)
            except RiskConstraintError as exc:
                # RAISE mode — surface the consolidated violation
                # report as a BacktestRunnerError so callers
                # catching either error class get a clean signal.
                # ``__cause__`` preserves the RiskConstraintError
                # for callers that want the structured violations.
                raise BacktestRunnerError(
                    f"BacktestRunner.run: risk constraints rejected the "
                    f"backtest positions map. {exc}"
                ) from exc
            # WARN_AND_CLIP mode (or RAISE mode with zero
            # violations). When clipping moved weight, expose the
            # constraint-respecting allocation on the sibling
            # field — ``positions`` stays unchanged so it remains
            # internally consistent with the official return
            # series and risk_analysis above.
            if apply_result.was_clipped:
                positions_clipped = {
                    d: dict(w) for d, w in apply_result.clipped_positions.items()
                }

        report = {
            "total_days": len(report_normal),
            "start_date": str(report_normal.index.min().date()),
            "end_date": str(report_normal.index.max().date()),
            "positions_days": len(positions_map),
        }

        provenance = cls._build_provenance(request, topk, n_drop, st_mask_provenance)

        return CanonicalBacktestOutput(
            metric_status=OFFICIAL_METRIC_STATUS,
            official_backtest_path=CANONICAL_OFFICIAL_BACKTEST_PATH,
            return_series=return_series,
            risk_analysis=risk_dict,
            report=report,
            provenance=provenance,
            positions=positions_map,
            positions_clipped=positions_clipped,
        )

    @staticmethod
    def _compute_equalweight_baseline(
        predictions: Any,
        topk: int,
        evaluation_start: str,
        evaluation_end: str,
        pit_provider: Any | None = None,
    ) -> dict[str, float]:
        """Post-hoc equal-weight top-k daily return series.

        Replaces ``n_drop>0`` rotation with a static buy-and-hold of the
        prediction-ranked top-k — same universe, same ranking, same
        rebalance dates, but equal-weight and no dropout. This provides
        the "does alpha come from model rotation or from top-k selection?"
        decomposition.

        The computation uses qlib close prices fetched once for the full
        evaluation window. Each rebalance day picks the ``topk``
        highest-scored instruments; the day's equal-weight return is the
        arithmetic mean of those instruments' close-to-close returns.

        When ``pit_provider`` is supplied, the close-panel fetch routes
        through ``PITDataProvider.get_features`` so post-delist positions
        return NaN (mask applied at the query layer per §4.3.2), and the
        per-day equal-weight mean correctly excludes delisted tickers
        instead of silently consuming forward-filled stale values.
        Phase D.3 opt-in; default ``None`` falls through to direct
        ``qlib.data.D.features`` preserving the legacy behaviour.
        """
        try:
            import numpy as np
            import pandas as pd
        except ImportError as exc:
            raise BacktestRunnerError(
                "numpy / pandas not importable; cannot compute baseline."
            ) from exc

        if not isinstance(predictions, pd.Series) or predictions.empty:
            raise BacktestRunnerError(
                "predictions must be a non-empty pd.Series for "
                "equal-weight baseline computation."
            )
        if not isinstance(predictions.index, pd.MultiIndex):
            raise BacktestRunnerError(
                "predictions must have a (datetime, instrument) MultiIndex "
                "for equal-weight baseline computation."
            )

        # Extract per-date top-k instrument sets.
        daily_topk: dict[pd.Timestamp, set[str]] = {}
        for dt, group in predictions.groupby(level=0):
            top = group.nlargest(topk)
            daily_topk[dt] = set(top.index.get_level_values(1))

        if not daily_topk:
            raise BacktestRunnerError(
                "No daily top-k instruments could be extracted from "
                "predictions for equal-weight baseline."
            )

        all_instruments = sorted(
            {inst for names in daily_topk.values() for inst in names}
        )
        try:
            if pit_provider is not None:
                close = pit_provider.get_features(
                    ["$close"], evaluation_start, evaluation_end,
                    instruments=all_instruments,
                )
            else:
                # Legacy non-PIT path. Documented in the function
                # docstring as "Phase D.3 opt-in; default None falls
                # through to direct D.features preserving the legacy
                # behaviour". Audit P0-6 added the WARN below so the
                # bypass is observable — the silent fallthrough used
                # to leave operators unable to tell whether a backtest
                # had post-delist values leaking through window
                # operators (qlib's default min_periods does not mask
                # past delist_date — see ``src/pit/query.py``
                # docstring §4.3.2). When you want the §4.3.2 mask,
                # pass a configured ``PITDataProvider`` instance.
                # TODO(P0-6 follow-up): thread pit_provider through
                # ``Pipeline`` / ``WalkForwardEngine`` so this legacy
                # branch can be removed once all production callers
                # opt in.
                _logger.warning(
                    "BacktestRunner._compute_equalweight_baseline: "
                    "pit_provider is None — falling back to direct "
                    "qlib.data.D.features. The §4.3.2 post-delist "
                    "mask is NOT applied; window-operator outputs "
                    "for delisted tickers may leak across the "
                    "delist_date boundary. Pass a PITDataProvider "
                    "to opt into the mask. Audit P0-6."
                )
                from qlib.data import D
                close = D.features(
                    all_instruments,
                    ["$close"],
                    start_time=evaluation_start,
                    end_time=evaluation_end,
                    freq="day",
                )
        except Exception as exc:
            raise BacktestRunnerError(
                "qlib D.features() failed for equal-weight baseline: "
                f"{exc}"
            ) from exc

        if close is None or close.empty:
            raise BacktestRunnerError(
                "qlib returned no close prices for equal-weight baseline."
            )

        # Align the baseline to the strategy's TRUE execution timing
        # (PR-C): a signal stamped dt fills at dt+1 close (qlib's
        # built-in shift) and its first holding return is dt+1→dt+2.
        # ``pct_change().shift(-2)`` keyed at dt is exactly that
        # window. (The pre-PR-C ``shift(-1)`` — dt→dt+1 — was offset
        # one day earlier than the strategy's actual fills under
        # either timing regime.)
        close_unstacked = close.unstack(level="instrument")["$close"]
        close_unstacked = close_unstacked.sort_index()
        ret_matrix = close_unstacked.pct_change().shift(-2)
        # The last two rows have no fill-day→next-day return; drop them.
        ret_matrix = ret_matrix.iloc[:-2].dropna(how="all")

        result: dict[str, float] = {}
        for dt, instruments in daily_topk.items():
            if dt not in ret_matrix.index:
                continue
            row = ret_matrix.loc[dt]
            valid = [row.get(inst) for inst in instruments if inst in row]
            if not valid or any(
                v is None or (isinstance(v, float) and np.isnan(v))
                for v in valid
            ):
                continue
            result[str(dt.date())] = float(np.nanmean(valid))

        return result

    @staticmethod
    def _validate_consumed_benchmark(
        benchmark_code: str, start: str, end: str,
    ) -> None:
        """PR-J: fail-loud VALUE-level validation of the benchmark series this
        backtest consumes for excess-return, BEFORE qlib reads it. Loads the
        benchmark close (and its price/total-return sibling, when the bundle has
        one) over the evaluation window and runs ``validate_benchmark_values``
        (finite / positive / no intra-span gaps, plus the TR>=price
        cumulative-return invariant).

        Reads ``D.features`` directly: the §4.3.2 post-delist mask is irrelevant
        for a benchmark INDEX (an index does not delist), so routing through
        ``PITDataProvider`` would add nothing. Allow-listed in
        tests/governance/test_pit_provider_is_sole_qlib_features_caller.py; the
        WARN log makes the bypass observable.
        """
        import pandas as pd
        from qlib.data import D

        if benchmark_code.endswith("TR"):
            price_code, tr_code = benchmark_code[:-2], benchmark_code
        else:
            price_code, tr_code = benchmark_code, benchmark_code + "TR"
        candidates = [benchmark_code]
        sibling = tr_code if benchmark_code == price_code else price_code
        if sibling not in candidates:
            candidates.append(sibling)

        # qlib computes the benchmark leg as $close/Ref($close,1)-1, so the
        # FIRST evaluation day's benchmark return consumes the PRIOR trading
        # day's close. Pad the fetch back so that pre-window close is validated
        # too — a NaN/zero/negative there would skew excess-return through the
        # first return even though [start, end] looked clean (codex P2 round 5).
        # 15 calendar days guarantees >= 1 prior trading day across the longest
        # CN market closure (Spring Festival ~10 days); at the bundle's first
        # day there simply is no prior row (qlib's first return is NaN anyway).
        fetch_start = (
            date.fromisoformat(start) - timedelta(days=15)
        ).isoformat()
        _logger.warning(
            "BacktestRunner: consume-time benchmark value-level check loads %s "
            "over [%s, %s] (start padded back from %s to cover the prior-day "
            "close qlib's return consumes) via direct qlib D.features "
            "(allow-listed — a benchmark index has no post-delist mask). PR-J.",
            candidates, fetch_start, end, start,
        )
        try:
            raw = D.features(
                candidates, ["$close"], start_time=fetch_start, end_time=end,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a loud runner error
            raise BacktestRunnerError(
                f"BacktestRunner: failed to load benchmark {candidates} for "
                f"consume-time value-level validation "
                f"({type(exc).__name__}: {exc}). The backtest consumes the "
                f"benchmark for excess-return; verify the bundle covers "
                f"[{start}, {end}] and contains the benchmark series."
            ) from exc

        series_by_code: dict[str, pd.Series] = {}
        if raw is not None and not raw.empty:
            present = set(raw.index.get_level_values("instrument").unique())
            for code in candidates:
                if code in present:
                    series_by_code[code] = raw.loc[code, "$close"]
        if benchmark_code not in series_by_code:
            raise BacktestRunnerError(
                f"BacktestRunner: benchmark {benchmark_code!r} returned no rows "
                f"over [{start}, {end}] — cannot validate or compute "
                f"excess-return. Verify the bundle's benchmark series."
            )
        # Always declare the TR/price pair so validate_benchmark_values emits a
        # "cross-check skipped" warning when the sibling is ABSENT (bundle lacks
        # it / no rows in the window) — an absent optional sibling must stay
        # observable, not silently indistinguishable from a clean cross-check
        # (codex P2 round 2). Both present ⇒ the cumret cross-check runs; sibling
        # missing ⇒ a skipped warning.
        pairs = {tr_code: price_code}
        # Only the CONSUMED benchmark gets the per-series hard-error checks; the
        # optional sibling is loaded solely for the (warning) cross-check, so a
        # defect in a non-consumed TR sibling never aborts a valid price-
        # benchmark backtest (codex P2 round 1 on PR-J).
        report = validate_benchmark_values(
            series_by_code,
            consumed_codes={benchmark_code},
            tr_price_pairs=pairs,
        )
        for warning in report.warnings:
            _logger.warning(
                "BacktestRunner: benchmark value-level check: %s", warning,
            )
        if not report.ok:
            raise BacktestRunnerError(
                "BacktestRunner: benchmark value-level validation FAILED for "
                f"{benchmark_code!r} over [{start}, {end}] — "
                + "; ".join(report.errors)
            )

    @classmethod
    def _validate_pit_provider_alignment(cls, pit_provider: Any) -> None:
        """Phase D.3 alignment guard — identical contract to Phase D.2's
        :meth:`src.data.feature_dataset_builder.FeatureDatasetBuilder
        ._validate_pit_provider_alignment`. When a PIT provider is
        supplied, the canonical qlib runtime's ``provider_uri`` MUST
        match. The duplication is intentional: backtest_runner and
        feature_dataset_builder enforce the same invariant from two
        independent entry points, so a future refactor changing one
        cannot silently weaken the other.
        """
        from src.core.qlib_runtime import _normalize_provider_uri

        canonical = get_canonical_qlib_config()
        if canonical is None:
            raise BacktestRunnerError(
                "pit_provider was supplied but the canonical qlib config "
                "is unavailable despite is_canonical_qlib_initialized() "
                "returning True. Internal inconsistency — investigate "
                "qlib_runtime state."
            )
        pit_uri_raw = str(getattr(pit_provider, "_provider_uri", ""))
        if not pit_uri_raw:
            raise BacktestRunnerError(
                "pit_provider has no readable _provider_uri attribute "
                f"(got {pit_provider!r}). Expected a PITDataProvider."
            )
        pit_norm = _normalize_provider_uri(pit_uri_raw)
        if canonical.provider_uri != pit_norm:
            raise BacktestRunnerError(
                "PIT provider / qlib provider_uri mismatch — backtest "
                "would silently consume features from the wrong provider. "
                f"qlib canonical provider_uri = {canonical.provider_uri!r}; "
                f"pit_provider._provider_uri = {pit_norm!r}. "
                "Re-init qlib with the PIT-corrected provider before "
                "passing pit_provider to BacktestRunner.run()."
            )

    @staticmethod
    def _apply_lag(predictions: Any, rows: int) -> Any:
        """Restamp prediction dates by ``rows`` trading rows — the EXTERNAL
        component of the signal→execution delay.

        Semantics (PR-C, audit A1)
        --------------------------
        Predictions arrive indexed by *signal date* — the day the model
        scored the universe. qlib's ``TopkDropoutStrategy`` does NOT
        rebalance on the stamped date: on trade day D it consumes the
        signal stamped D-1 (``get_step_time(trade_step, shift=1)`` in
        qlib/contrib/strategy/signal_strategy.py), a built-in
        one-trading-day delay. The caller therefore passes
        ``signal_to_execution_lag - 1`` here::

            # lag=1 (T+1 fill, default):  rows=0   → stamps unchanged
            # lag=2 (T+2 fill):           rows=1   → stamps move to T+1

        ``lag=0`` (same-day fill) is REJECTED upstream by the canonical
        contract: it would require restamping signals BACKWARD —
        look-ahead — while this runner stamps every output
        ``metric_status=official`` (codex P1 on PR #241). Negative
        ``rows`` therefore cannot arise from a valid config and are
        refused here as defence in depth.

        The pre-PR-C implementation restamped the FULL lag on top of
        qlib's built-in shift, so the default lag=1 filled on T+2 and
        every official backtest traded a one-day-stale signal.

        Shifting is row-wise within the prediction's own date set
        (``unstack`` by date then ``shift``), which equals trading-day
        shifting when the index is a trading-day calendar. ``rows=0``
        returns the input unchanged after shape validation.
        """
        if rows < 0:
            raise BacktestRunnerError(
                f"BacktestRunner._apply_lag: restamp of {rows} rows would "
                "move signals backward (look-ahead). The canonical contract "
                "rejects signal_to_execution_lag < 1, so this cannot arise "
                "from a valid config. Refusing."
            )
        # Validate predictions shape *before* the lag=0 short-circuit so
        # the same-day-execution path cannot bypass the structural
        # contract. The previous implementation skipped validation
        # whenever ``lag == 0`` — which meant a research script feeding
        # a wrong-shape Series or DataFrame to ``signal_to_execution_lag=0``
        # would still produce official metrics from qlib without any
        # complaint here, while ``lag>=1`` callers got a loud error.
        # Validate uniformly.
        import pandas as pd
        if not isinstance(predictions, pd.Series):
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions must be a pandas "
                f"Series with (datetime, instrument) MultiIndex; got "
                f"{type(predictions).__name__}. Refusing to forward to "
                "qlib silently."
            )
        if not isinstance(predictions.index, pd.MultiIndex):
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions Series must carry a "
                "(datetime, instrument) MultiIndex; got "
                f"{type(predictions.index).__name__}. Refusing to forward "
                "to qlib silently."
            )
        # Names matter: qlib's ``TopkDropoutStrategy`` and the unstack
        # path below access levels by *name*, so an
        # ``(instrument, datetime)``-ordered MultiIndex would silently
        # feed instruments to the date axis. Pin it.
        expected_names = ("datetime", "instrument")
        if tuple(predictions.index.names) != expected_names:
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions index names must be "
                f"{expected_names}; got {tuple(predictions.index.names)!r}. "
                "Refusing to forward to qlib silently."
            )
        if not predictions.index.is_unique:
            raise BacktestRunnerError(
                "BacktestRunner._apply_lag: predictions index must be unique "
                "before unstack/lag. Duplicate (datetime, instrument) rows "
                "would make pandas raise ValueError deep in unstack and leave "
                "the official backtest boundary ambiguous."
            )

        if rows == 0:
            # lag=1: qlib's built-in shift IS the entire T+1 delay.
            return predictions
        # MultiIndex (datetime, instrument): shift the datetime level.
        # ``unstack()`` pivots instrument to columns so ``shift(rows)``
        # moves every instrument's date stamps by the same number of
        # rows; ``stack().dropna()`` drops the boundary rows that now
        # have no source.
        df = predictions.unstack()
        df = df.shift(rows)
        return df.stack().dropna()

    @staticmethod
    def _build_provenance(
        request: CanonicalBacktestInput,
        topk: int,
        n_drop: int,
        st_mask: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Build a provenance record covering the full request + strategy
        params *plus* the qlib runtime config the metrics depend on.

        Previously only ``topk`` and ``n_drop`` were captured, then the
        full request — but the same ``predictions_ref`` evaluated against
        a different qlib provider (different ``provider_uri`` / ``region``
        / ``data_adjust_mode``) would yield different official metrics
        and the fingerprint stayed identical, so a downstream comparison
        tool diff'ing two run reports could not tell the difference
        between "true regression" and "switched data bundle".

        We now also hash the live qlib runtime config — the
        ``runtime.data_adjust_mode`` / ``runtime.provider_uri`` /
        ``runtime.region`` triple — into the same JSON blob so the
        fingerprint changes whenever any of those change.
        """
        # Strategy params not captured by CanonicalBacktestInput. ``st_mask``
        # (Codex P2 on #223): the namechange path + content hash change the
        # official universe and metrics, so they must move the fingerprint —
        # otherwise the same request run ST-off vs ST-on shares a fingerprint
        # despite different metrics. None -> {"namechange_path": None}.
        strategy_dict: dict[str, Any] = {
            "topk": topk,
            "n_drop": n_drop,
            "st_mask": dict(st_mask) if st_mask is not None else {"namechange_path": None},
        }
        # Full request serialised via dataclass asdict — captures every field
        # including nested cost model and exchange config.
        request_dict = asdict(request)
        # qlib runtime config snapshot. ``run`` already verified the
        # runtime is initialised and that ``request.adjust_mode`` matches
        # ``runtime.data_adjust_mode``, so a non-None config is the
        # expected path. We tolerate ``None`` defensively rather than
        # crashing the provenance step — the metrics themselves would
        # have already failed earlier in that case.
        runtime_config = get_canonical_qlib_config()
        runtime_dict: dict[str, Any] = (
            {
                "provider_uri": runtime_config.provider_uri,
                "region": runtime_config.region,
                "data_adjust_mode": runtime_config.data_adjust_mode,
            }
            if runtime_config is not None
            else {}
        )
        config_dict = {
            "request": request_dict,
            "strategy": strategy_dict,
            "runtime": runtime_dict,
            # PR-C: the MEANING of signal_to_execution_lag changed (lag is
            # now the TOTAL delay including qlib's built-in shift; the same
            # lag=1 config used to fill on T+2 and now fills on T+1). The
            # semantics version moves the fingerprint so a post-PR-C run can
            # never be confused with — or resume from — a pre-PR-C run of
            # the byte-identical config.
            "execution_timing_semantics": EXECUTION_TIMING_SEMANTICS,
            # PR-D: same rationale for the price-limit semantics — the same
            # float magnitude now actually blocks limit fills.
            "price_limit_semantics": PRICE_LIMIT_SEMANTICS,
        }
        config_json = json.dumps(config_dict, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:16]
        return {
            # Flat surface for human readability; includes runtime so a
            # diff between two runs can also see provider / region /
            # adjust_mode side by side without re-deriving from the
            # fingerprint alone.
            "config": {**request_dict, **strategy_dict, "runtime": runtime_dict},
            "config_fingerprint": fingerprint,
            "official_backtest_path": CANONICAL_OFFICIAL_BACKTEST_PATH,
        }


def _risk_analysis_to_flat_dict(df: Any) -> dict[str, Any]:
    """Normalize a qlib risk_analysis DataFrame to a flat {metric: value} dict.

    qlib's ``risk_analysis`` can return a DataFrame in two orientations:

    Column-oriented (metric as columns, index row = "risk")::

        index  annualized_return  information_ratio  max_drawdown
        risk   -0.27              -1.05              -0.15

        df.to_dict() → {"annualized_return": {"risk": -0.27},
                         "information_ratio": {"risk": -1.05}, ...}

    Row-oriented (index = metric names, single column "risk")::

        index              risk
        annualized_return  -0.27
        information_ratio  -1.05
        max_drawdown       -0.15

        df.to_dict() → {"risk": {"annualized_return": -0.27,
                                  "information_ratio": -1.05, ...}}

    Both are normalized to ``{"annualized_return": -0.27, ...}``.

    If neither shape matches or ``to_dict`` itself raises, a
    ``BacktestRunnerError`` is raised. The previous implementation
    swallowed every exception into ``{"raw": str(df)}``, which then
    flowed downstream as *missing* metrics that callers like
    ``WalkForwardEngine`` coerced to 0.0 — a silent regression path
    for any future qlib shape change.
    """
    try:
        raw = df.to_dict()
    except Exception as exc:
        raise BacktestRunnerError(
            f"risk_analysis.to_dict() failed ({type(exc).__name__}: {exc}). "
            "qlib risk_analysis output shape may have changed; downstream "
            "metric extraction cannot proceed."
        ) from exc

    if not raw:
        return {}

    first_val = next(iter(raw.values()))

    if not isinstance(first_val, dict):
        # Already flat scalars.
        return {str(k): (float(v) if hasattr(v, "__float__") else str(v))
                for k, v in raw.items()}

    # Detect row-oriented shape: single outer key "risk" whose value
    # is a dict of {metric_name: scalar}.
    if len(raw) == 1 and "risk" in raw and isinstance(raw["risk"], dict):
        inner = raw["risk"]
        return {str(k): (float(v) if hasattr(v, "__float__") else str(v))
                for k, v in inner.items()}

    # Column-oriented shape: outer keys are metric names, inner dicts
    # have index labels as keys (typically a single "risk" entry).
    flat: dict[str, Any] = {}
    for metric, sub in raw.items():
        if not isinstance(sub, dict):
            try:
                flat[str(metric)] = float(sub)
            except (TypeError, ValueError):
                flat[str(metric)] = str(sub)
            continue
        # Prefer the "risk" index label; fall back to first value.
        val = sub.get("risk", next(iter(sub.values())))
        try:
            flat[str(metric)] = float(val)
        except (TypeError, ValueError):
            flat[str(metric)] = str(val)
    return flat


def _series_to_dict(series: Any, *, name: str = "series") -> dict[str, float]:
    """Convert a pandas-like Series to ``{date_str: float}``.

    Unknown qlib output shapes are boundary failures. Returning a raw string
    envelope would make ``CanonicalBacktestOutput.return_series`` no longer a
    structured return series while allowing downstream consumers to fail later.
    """
    if not hasattr(series, "items"):
        raise BacktestRunnerError(
            f"return_series[{name!r}] must expose .items(); got "
            f"{type(series).__name__}. qlib report output shape may have changed."
        )
    try:
        return {str(k.date()) if hasattr(k, "date") else str(k): float(v) for k, v in series.items()}
    except Exception as exc:
        raise BacktestRunnerError(
            f"Failed to serialize return_series[{name!r}] "
            f"({type(exc).__name__}: {exc}). qlib report output shape may "
            "have changed; refusing to emit an unstructured raw fallback."
        ) from exc


def _positions_to_weight_map(positions_normal: Any) -> dict[str, dict[str, float]]:
    """Serialize qlib positions into ``{date_str: {instrument: weight}}``.

    qlib's ``positions_normal`` comes out of ``generate_portfolio_metrics`` as
    either a ``pd.Series`` indexed by timestamp whose values are ``Position``
    objects, or a plain ``dict`` with the same shape. Either way, each
    ``Position`` exposes ``position`` — a dict of ``{instrument: {amount,
    price, weight, ...}}`` plus bookkeeping keys like ``"cash"`` and
    ``"now_account_value"``.

    Error handling
    --------------
    The previous implementation wrapped the whole function in a catch-all
    that returned ``{}`` on *any* failure. Downstream (``pipeline.py``)
    then silently coerced an empty positions map to ``None``, which made
    ``PerformanceAttribution`` switch from "real-portfolio attribution"
    to a prediction-score fallback — a semantically-different run under
    the same metric name. That conflicts with the repo's "no implicit
    fallback" governance rule.

    The new contract:

    * ``None`` input → ``{}`` — this is qlib's legitimate "no positions
      generated" signal (e.g. backtest was configured without
      ``generate_portfolio_metrics=True``).
    * Non-``None`` input that *cannot be iterated* (no ``.items()`` or it
      raises) → raise ``BacktestRunnerError``. This is an upstream
      contract violation and must surface immediately.
    * Per-day rows whose shape is malformed (e.g. ``position`` isn't a
      dict) → logged at WARNING with date context and skipped; they do
      not abort the whole map but they also cannot be silently dropped.
    * Per-instrument entries with unusable weights → logged at DEBUG
      and skipped (these are common across qlib version differences).

    Cash inclusion in the denominator
    ---------------------------------
    The total denominator includes ``raw["cash"]`` alongside the
    instruments' market value. This means per-instrument weights sum
    to ``< 1`` whenever the portfolio holds any cash — they reflect
    *NAV share*, not *equity share*.

    Downstream consequence in Brinson attribution: the sector
    decomposition (``allocation + selection + interaction``) only
    covers the equity portion, so its sum will not exactly match
    ``total_excess_return`` whenever cash > 0. That gap is the
    ``reconciliation_residual`` already surfaced on
    :class:`AttributionResult`; the
    :func:`PerformanceAttribution.print_report` method emits a
    WARNING when ``|residual| > RECONCILIATION_WARN_THRESHOLD`` so
    the gap is visible. We deliberately do *not* renormalise the
    weights to sum to 1 here — that would hide the cash position from
    NAV-aware consumers (turnover analysis, position-size limits,
    risk budgets) which is a much costlier silent change than the
    Brinson residual.
    """
    if positions_normal is None:
        return {}

    # Outer contract: must be iterable.  The previous catch-all let
    # non-iterable inputs (e.g. ints, strings) quietly disappear.
    if not hasattr(positions_normal, "items"):
        raise BacktestRunnerError(
            "positions_to_weight_map: input is not iterable "
            f"(got {type(positions_normal).__name__}). qlib "
            "positions_normal must be a pd.Series or dict; receiving a "
            "different type indicates an upstream contract violation."
        )
    try:
        items = list(positions_normal.items())
    except Exception as exc:
        raise BacktestRunnerError(
            f"positions_to_weight_map: failed to iterate positions "
            f"({type(exc).__name__}: {exc}). qlib positions_normal shape "
            "may have changed; refusing to silently return empty map."
        ) from exc

    result: dict[str, dict[str, float]] = {}
    bookkeeping_keys = {"cash", "now_account_value"}
    skipped_days = 0

    def _finite_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None

    for ts, pos in items:
        try:
            date_str = str(ts.date()) if hasattr(ts, "date") else str(ts)
            raw = getattr(pos, "position", pos)
            if not isinstance(raw, dict):
                _logger.warning(
                    "positions_to_weight_map: day %s has non-dict "
                    "position payload (%s); skipping.",
                    date_str, type(raw).__name__,
                )
                skipped_days += 1
                continue

            # Compute total value for fallback weighting
            total_value: float = 0.0
            for inst, info in raw.items():
                if inst in bookkeeping_keys or not isinstance(info, dict):
                    continue
                amt = _finite_float(info.get("amount")) or 0.0
                price = _finite_float(info.get("price")) or 0.0
                total_value += amt * price
            # Include cash in denominator so weights reflect NAV share
            cash = _finite_float(raw.get("cash"))
            if cash is not None:
                total_value += cash

            day_weights: dict[str, float] = {}
            for inst, info in raw.items():
                if inst in bookkeeping_keys or not isinstance(info, dict):
                    continue
                w = info.get("weight")
                if w is None and total_value > 0:
                    amt = _finite_float(info.get("amount")) or 0.0
                    price = _finite_float(info.get("price")) or 0.0
                    w = (amt * price) / total_value
                if w is None:
                    continue
                weight = _finite_float(w)
                if weight is None:
                    # Individual entry coerce failure — common across qlib
                    # versions; log at DEBUG so noise stays low.
                    _logger.debug(
                        "positions_to_weight_map: day %s inst %s: weight "
                        "%r is not finite/coercible to float; skipping entry.",
                        date_str, inst, w,
                    )
                    continue
                day_weights[str(inst)] = weight

            if day_weights:
                result[date_str] = day_weights
        except Exception as exc:
            # Per-day robustness: do NOT silently continue — surface the
            # exception class and date so the caller can tell how much
            # data was actually captured.
            _logger.warning(
                "positions_to_weight_map: failed to parse day %s (%s: %s); "
                "skipping.",
                ts, type(exc).__name__, exc,
            )
            skipped_days += 1
            continue

    if skipped_days:
        _logger.warning(
            "positions_to_weight_map: %d of %d days were skipped due to "
            "malformed entries; downstream attribution based on this map "
            "will cover only %d days.",
            skipped_days, len(items), len(result),
        )
    return result

"""Factor-level analysis — per-factor IC/IR, correlation matrix, and decay comparison.

Unlike :mod:`signal_analyzer` which evaluates the *aggregate* model prediction,
this module decomposes prediction quality at the individual Alpha158 feature
level.  It answers: which factors carry signal, which are redundant, and how
quickly each factor's predictive power decays.

Boundaries
----------
- Requires canonical qlib init (for fetching price data and dataset features).
- Importing this module does NOT import qlib or pandas. Imports are lazy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.core._ic_utils import compute_ic_for_group
from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


# Minimum (factor, forward-return) observations per lag before we trust the
# cross-sectional IC. Below this, daily IC estimates are too noisy to mean
# anything and we emit NaN rather than a zero that would contaminate decay
# plots.
_MIN_IC_OBSERVATIONS_PER_LAG = 10


class FactorAnalyzerError(RuntimeError):
    """Raised on factor analysis failures."""


@dataclass(frozen=True)
class FactorAnalysisConfig:
    """Configuration for factor-level analysis."""

    # Dataset params (reuse the same splits as the pipeline)
    instruments: str = "csi300"
    feature_handler: str = "Alpha158"
    test_start: str = "2025-07-01"
    test_end: str = "2025-12-31"

    # Analysis params
    forward_period: int = 5
    ic_method: str = "rank"  # "rank" (Spearman) or "normal" (Pearson)
    max_decay_lag: int = 20
    top_n_factors: int = 20  # how many top factors to include in results


@dataclass(frozen=True)
class FactorICStats:
    """IC statistics for a single factor."""

    factor_name: str
    mean_ic: float
    std_ic: float
    ir: float  # IC / std(IC)
    ic_positive_ratio: float
    num_days: int


@dataclass(frozen=True)
class FactorAnalysisResult:
    """Complete factor analysis result."""

    # Per-factor IC stats, sorted by |mean_ic| descending
    factor_ic_stats: tuple[FactorICStats, ...]

    # Correlation matrix of top factors (factor_name → {factor_name → corr})
    correlation_matrix: Mapping[str, Mapping[str, float]]

    # IC decay for top factors (factor_name → [ic_at_lag_1, ic_at_lag_2, ...])
    ic_decay: Mapping[str, Sequence[float]]

    # Total factors analyzed
    total_factors: int


class FactorAnalyzer:
    """Analyzes individual factor quality from Alpha158 features."""

    @classmethod
    def analyze(
        cls,
        config: FactorAnalysisConfig | None = None,
        *,
        dataset: Any | None = None,
    ) -> FactorAnalysisResult:
        """Run complete factor analysis.

        Parameters
        ----------
        config : FactorAnalysisConfig, optional
            Analysis configuration. Uses defaults if None.
        dataset : qlib DatasetH, optional
            Pre-built dataset to reuse (typically from FeatureDatasetBuilder).
            When provided, we skip the expensive Alpha158 rebuild.  The
            "test" segment is prepared from this dataset.  When None, a new
            handler+dataset is built from ``config``.

        Steps:
          1. Get factor values (from provided dataset or fresh build)
          2. Fetch forward returns for the test period
          3. Compute per-factor cross-sectional IC
          4. Build correlation matrix of top factors
          5. Compute IC decay for top factors
        """
        if config is None:
            config = FactorAnalysisConfig()

        cls._validate(config)

        if dataset is not None:
            _logger.info("Reusing pre-built dataset (skipping Alpha158 rebuild)...")
            factor_df = cls._prepare_from_dataset(dataset)
            # Governance: when callers hand us a pre-built dataset, the
            # FactorAnalysisConfig's data-source fields (instruments /
            # test_start / test_end) stop exerting any constraint on what
            # gets analyzed. Validate the prepared DataFrame's date range
            # lines up with the config, so a handler/segment drift in the
            # pipeline doesn't silently shift factor-analysis results.
            cls._validate_dataset_matches_config(factor_df, config)
        else:
            _logger.info("Building factor dataset for %s ~ %s...", config.test_start, config.test_end)
            factor_df = cls._get_factor_values(config)
        factor_names = list(factor_df.columns)
        _logger.info("Loaded %d factors, %d rows", len(factor_names), len(factor_df))

        # Step 2: Fetch close-price matrix once, derive all forward-return
        # matrices from it. Shared by per-factor IC (lag=forward_period) and
        # decay analysis (lags 1..max_decay_lag). Extension window covers
        # the LARGER of forward_period and max_decay_lag — otherwise with
        # forward_period > max_decay_lag the IC lag's forward return gets
        # truncated and IC goes NaN. (P1 regression fix.)
        max_lag = max(config.forward_period, config.max_decay_lag)
        _logger.info("Fetching close prices + building forward-return cache (max_lag=%d)...", max_lag)
        close_unstacked = cls._fetch_close_unstacked(factor_df, max_lag)
        forward_ret_cache = cls._build_forward_ret_cache(
            close_unstacked, factor_df.index,
            lags=sorted({config.forward_period, *range(1, config.max_decay_lag + 1)}),
        )
        forward_ret = forward_ret_cache[config.forward_period]

        # Step 3: Per-factor IC
        _logger.info("Computing per-factor IC (%s) at lag=%d...", config.ic_method, config.forward_period)
        all_stats = cls._compute_all_factor_ic(factor_df, forward_ret, config)

        # Sort by absolute mean IC
        all_stats.sort(key=lambda s: abs(s.mean_ic), reverse=True)

        # Step 4: Top factors for correlation and decay
        top_names = [s.factor_name for s in all_stats[: config.top_n_factors]]

        _logger.info("Computing correlation matrix for top %d factors...", len(top_names))
        corr_matrix = cls._compute_correlation_matrix(factor_df, top_names)

        _logger.info(
            "Computing IC decay (max lag %d) for top %d factors using cached forward returns...",
            config.max_decay_lag, len(top_names),
        )
        decay = cls._compute_factor_decay_cached(
            factor_df, forward_ret_cache, top_names, config,
        )

        return FactorAnalysisResult(
            factor_ic_stats=tuple(all_stats),
            correlation_matrix=corr_matrix,
            ic_decay=decay,
            total_factors=len(all_stats),
        )

    @staticmethod
    def _validate_dataset_matches_config(
        factor_df: Any, config: FactorAnalysisConfig,
    ) -> None:
        """Raise if the prepared DataFrame's date range drifts from config.

        When a caller passes a pre-built ``dataset``, the ``instruments``,
        ``test_start`` and ``test_end`` fields in ``FactorAnalysisConfig``
        become annotations rather than constraints. That's fine as long as
        they *agree* with reality — if the upstream pipeline quietly shifts
        the test segment (bug, wrong handler, cache replay), factor analysis
        would silently analyze the wrong period. We check that the actual
        datetime range falls within the declared window.
        """
        import pandas as pd

        if factor_df.empty:
            raise FactorAnalyzerError(
                "Provided dataset yielded an empty 'test' segment."
            )
        actual_start = pd.Timestamp(factor_df.index.get_level_values("datetime").min()).date()
        actual_end = pd.Timestamp(factor_df.index.get_level_values("datetime").max()).date()
        declared_start = pd.Timestamp(config.test_start).date()
        declared_end = pd.Timestamp(config.test_end).date()

        # 1. Bounds check — dataset must not escape the declared window.
        if actual_start < declared_start or actual_end > declared_end:
            raise FactorAnalyzerError(
                "Dataset test-segment date range escapes FactorAnalysisConfig: "
                f"actual [{actual_start} .. {actual_end}] vs config "
                f"[{declared_start} .. {declared_end}]. "
                "Config and dataset must agree on the analysis window."
            )

        # 2. Narrowing check — dataset must not be silently shorter than
        # declared. A dataset that ends (declared_end - 14 days) or more
        # early indicates an upstream handler/segment/cache change that
        # would silently analyze the wrong period. We use 14 calendar days
        # as the tolerance to allow for weekends, holidays, and minor
        # data-tail differences. Raise rather than produce a quietly-
        # truncated factor report.
        tolerance = pd.Timedelta(days=14)
        if pd.Timestamp(actual_end) < pd.Timestamp(declared_end) - tolerance:
            raise FactorAnalyzerError(
                "Dataset test-segment ends significantly before declared "
                f"FactorAnalysisConfig.test_end: actual_end={actual_end}, "
                f"declared_end={declared_end} (tolerance=14 days). "
                "The upstream dataset may have been narrowed by a handler, "
                "segment, or cache change. Align the dataset or update config."
            )
        if pd.Timestamp(actual_start) > pd.Timestamp(declared_start) + tolerance:
            raise FactorAnalyzerError(
                "Dataset test-segment starts significantly after declared "
                f"FactorAnalysisConfig.test_start: actual_start={actual_start}, "
                f"declared_start={declared_start} (tolerance=14 days). "
                "The upstream dataset may have been narrowed by a handler, "
                "segment, or cache change. Align the dataset or update config."
            )

    @staticmethod
    def _prepare_from_dataset(dataset: Any) -> Any:
        """Extract the test-segment feature DataFrame from a DatasetH.

        Mirrors the shape returned by ``_get_factor_values`` so downstream
        code (column indexing, IC computation) is invariant to the source.
        """
        try:
            df = dataset.prepare("test", col_set="feature")
        except Exception as exc:  # pragma: no cover - defensive
            raise FactorAnalyzerError(
                "Provided dataset does not expose a 'test' segment with "
                f"'feature' col_set: {exc}"
            ) from exc
        return df

    @classmethod
    def _validate(cls, config: FactorAnalysisConfig) -> None:
        if not is_canonical_qlib_initialized():
            raise FactorAnalyzerError(
                "Canonical qlib runtime is not initialized. "
                "Call src.core.qlib_runtime.init_qlib_canonical(...) first."
            )
        if config.forward_period < 1:
            raise FactorAnalyzerError("forward_period must be >= 1.")
        if config.ic_method not in ("rank", "normal"):
            raise FactorAnalyzerError("ic_method must be 'rank' or 'normal'.")

    @classmethod
    def _get_factor_values(cls, config: FactorAnalysisConfig) -> Any:
        """Load the configured feature handler's features for the test period.

        Looks up the handler factory from the same registry
        :class:`FeatureDatasetBuilder` uses, so a caller's
        ``feature_handler="Alpha360"`` (or any custom-registered
        handler) is honoured rather than silently overridden by a
        hardcoded ``Alpha158`` import. The previous implementation
        ignored ``config.feature_handler`` entirely — which Pipeline's
        dataset-reuse path masked, but a standalone
        ``FactorAnalyzer.analyze(config=...)`` invocation hit head-on.
        """
        from qlib.data.dataset import DatasetH

        from src.data.feature_dataset_builder import (
            FeatureDatasetConfig,
            _FEATURE_HANDLER_REGISTRY,
        )

        factory = _FEATURE_HANDLER_REGISTRY.get(config.feature_handler)
        if factory is None:
            raise FactorAnalyzerError(
                f"feature_handler {config.feature_handler!r} is not "
                "registered. Register a factory via "
                "``register_feature_handler`` before calling "
                "``FactorAnalyzer.analyze``, or use one of the built-in "
                f"handlers: {sorted(_FEATURE_HANDLER_REGISTRY.keys())}."
            )

        # The shared factories take a ``FeatureDatasetConfig``. The
        # standalone factor-analyzer path has no train/valid windows
        # of its own, so we collapse all four to the test window — the
        # factory uses ``instruments`` and the test dates, plus
        # ``fit_*_time`` for handler-internal normalisation that
        # ``FactorAnalyzer`` does not need to be tuned to a separate
        # train window.
        builder_config = FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=config.test_start,
            train_end=config.test_end,
            valid_start=config.test_start,
            valid_end=config.test_end,
            test_start=config.test_start,
            test_end=config.test_end,
        )
        handler = factory(builder_config)
        dataset = DatasetH(
            handler=handler,
            segments={"test": [config.test_start, config.test_end]},
        )
        df = dataset.prepare("test", col_set="feature")
        return df

    @classmethod
    def _fetch_close_unstacked(cls, factor_df: Any, max_lag: int) -> Any:
        """Fetch $close for all instruments and return a date × instrument matrix.

        End date is extended by ``max_lag`` *trading* days (via D.calendar),
        falling back to ``max_lag * 3`` calendar days if the calendar lookup
        fails. This prevents holiday clusters (Spring Festival etc.) from
        starving the forward-return computation.
        """
        import pandas as pd
        from qlib.data import D

        instruments = factor_df.index.get_level_values("instrument").unique().tolist()
        start = str(factor_df.index.get_level_values("datetime").min().date())
        end_dt = factor_df.index.get_level_values("datetime").max()
        end_extended = cls._extend_end_trading_days(end_dt, max_lag)

        close = D.features(
            instruments, ["$close"], start_time=start, end_time=end_extended
        )
        # qlib returns (instrument, datetime) — swap to
        # (datetime, instrument) and sort so shift(-lag) moves by
        # trading-day order, not physical row order.  Missing sort
        # produces silently wrong forward returns / IC / decay values.
        close = close.swaplevel()
        close = close.sort_index()
        close.columns = ["close"]
        return close["close"].unstack(level="instrument")

    @staticmethod
    def _extend_end_trading_days(end_dt: Any, max_lag: int) -> Any:
        """Thin wrapper around :func:`src.data.trading_calendar.extend_end_by_trading_days`.

        Kept as a method so existing tests that patch this attribute on
        ``FactorAnalyzer`` continue to work; the actual logic now lives
        in the shared helper to eliminate the line-for-line duplicate
        previously held by ``SignalAnalyzer``.
        """
        from src.data.trading_calendar import extend_end_by_trading_days

        return extend_end_by_trading_days(
            end_dt, max_lag,
            logger=_logger, caller_name="FactorAnalyzer",
        )

    @classmethod
    def _build_forward_ret_cache(
        cls, close_unstacked: Any, factor_index: Any, lags: list[int],
    ) -> dict[int, Any]:
        """Precompute reindexed forward returns for each requested lag.

        Why this exists: previously ``_compute_factor_decay`` did
        ``close.shift(-lag) / close - 1`` → ``stack`` → ``reindex`` inside
        an inner loop. With 158 factors × 20 lags that's 3160 redundant
        stack+reindex passes. Here we do 20 stack+reindex passes total and
        the inner factor loop just reads from the dict.
        """
        cache: dict[int, Any] = {}
        for lag in lags:
            fwd = close_unstacked.shift(-lag) / close_unstacked - 1
            # pandas 2.1+: stack(dropna=...) is deprecated in favor of
            # future_stack=True, which preserves NaN rows and has the
            # desired index behavior. reindex() below aligns to factor_df
            # anyway, so NaN rows are harmless.
            fwd_stacked = fwd.stack(future_stack=True)
            fwd_stacked.name = "forward_ret"
            fwd_stacked.index.names = ["datetime", "instrument"]
            cache[lag] = fwd_stacked.reindex(factor_index)
        return cache

    @classmethod
    def _compute_all_factor_ic(
        cls, factor_df: Any, forward_ret: Any, config: FactorAnalysisConfig
    ) -> list[FactorICStats]:
        """Compute IC stats for every factor column."""
        import pandas as pd

        results = []
        ic_method = config.ic_method
        for col in factor_df.columns:
            factor_col = factor_df[col]
            merged = pd.DataFrame({"factor": factor_col, "ret": forward_ret}).dropna()

            if len(merged) < _MIN_IC_OBSERVATIONS_PER_LAG:
                continue

            daily_ic = merged.groupby(level="datetime").apply(
                lambda g: compute_ic_for_group(g, ic_method),
                include_groups=False,
            ).dropna()

            if len(daily_ic) == 0:
                continue

            mean_ic = float(daily_ic.mean())
            # Standard deviation is undefined with a single sample; ranking
            # such a factor against multi-day factors via a fabricated 0.0
            # std (and the 0.0 IR that follows) makes a "one good day"
            # factor look identical to a genuinely flat one. NaN preserves
            # the "undefined" status all the way to the report. SignalAnalyzer
            # uses the same convention (signal_analyzer.py:136-145).
            std_ic = float(daily_ic.std()) if len(daily_ic) > 1 else float("nan")
            # IR = mean_ic / std_ic; undefined when std_ic is NaN or zero
            # (zero std means every day's IC is identical, IR is not "0",
            # it does not exist). Returning 0.0 here would tell Optuna /
            # walk-forward the factor scored a flat-zero IR — structurally
            # indistinguishable from a real mediocre factor.
            if std_ic != std_ic or std_ic <= 1e-9:  # NaN or near-zero
                ir = float("nan")
            else:
                ir = mean_ic / std_ic

            results.append(FactorICStats(
                factor_name=str(col) if not isinstance(col, tuple) else str(col[-1]),
                mean_ic=mean_ic,
                std_ic=std_ic,
                ir=ir,
                ic_positive_ratio=float((daily_ic > 0).mean()),
                num_days=len(daily_ic),
            ))

        return results

    @classmethod
    def _compute_correlation_matrix(
        cls, factor_df: Any, factor_names: list[str]
    ) -> dict[str, dict[str, float]]:
        """Compute pairwise rank correlation between top factors."""
        import pandas as pd

        # Alpha158 columns may be tuples like ('KLEN',) — match by last element
        col_map = {}
        for col in factor_df.columns:
            name = str(col) if not isinstance(col, tuple) else str(col[-1])
            if name in factor_names:
                col_map[name] = col

        subset = factor_df[[col_map[n] for n in factor_names if n in col_map]]
        subset.columns = [n for n in factor_names if n in col_map]

        corr = subset.corr(method="spearman")

        result: dict[str, dict[str, float]] = {}
        for row_name in corr.index:
            result[str(row_name)] = {
                str(col_name): round(float(corr.loc[row_name, col_name]), 4)
                for col_name in corr.columns
            }
        return result

    @classmethod
    def _compute_factor_decay_cached(
        cls, factor_df: Any, forward_ret_cache: dict[int, Any],
        factor_names: list[str], config: FactorAnalysisConfig,
    ) -> dict[str, list[float]]:
        """Compute IC decay for each top factor using the precomputed cache.

        The expensive ``shift/stack/reindex`` is done once per lag in
        :meth:`_build_forward_ret_cache`; this routine only does the
        per-factor join + groupby.
        """
        import pandas as pd

        # Build column map once (factor_names are the human-readable last
        # elements of possibly-tuple Alpha158 columns).
        col_map: dict[str, Any] = {}
        for col in factor_df.columns:
            name = str(col) if not isinstance(col, tuple) else str(col[-1])
            if name in factor_names:
                col_map[name] = col

        # Any factor we were asked to analyze that isn't in the DataFrame
        # is a bug (names came from factor_df itself upstream) or a config
        # drift — either way, silently skipping produces a "looks complete,
        # actually missing" report. Fail loud instead.
        missing = [n for n in factor_names if n not in col_map]
        if missing:
            raise FactorAnalyzerError(
                f"Requested factor decay for {len(missing)} name(s) not "
                f"present in factor_df columns: {missing[:5]}"
                + ("..." if len(missing) > 5 else "")
            )

        result: dict[str, list[float]] = {}

        for fname in factor_names:
            factor_col = factor_df[col_map[fname]]
            decay_values: list[float] = []

            for lag in range(1, config.max_decay_lag + 1):
                fwd = forward_ret_cache[lag]
                merged = pd.DataFrame({"factor": factor_col, "ret": fwd}).dropna()

                # Too few observations → NaN, not 0.0. Zero used to hide
                # "no data at this lag" inside the decay curve so it looked
                # like the factor's predictive power vanished at lag N.
                if len(merged) < _MIN_IC_OBSERVATIONS_PER_LAG:
                    decay_values.append(float("nan"))
                    continue

                daily_ic = merged.groupby(level="datetime").apply(
                    lambda g: compute_ic_for_group(g, config.ic_method),
                    include_groups=False,
                ).dropna()
                decay_values.append(
                    float(daily_ic.mean()) if len(daily_ic) > 0 else float("nan")
                )

            result[fname] = decay_values

        return result

    @classmethod
    def print_report(cls, result: FactorAnalysisResult) -> None:
        """Log a formatted factor analysis report."""
        log = _logger.info
        log("=" * 70)
        log("FACTOR ANALYSIS REPORT")
        log("=" * 70)

        log("Total factors analyzed: %d", result.total_factors)
        log("")
        log("Top Factors by |IC|:")
        log(f"{'Factor':>25} {'Mean IC':>10} {'Std IC':>10} {'IR':>8} {'IC>0%':>8} {'Days':>6}")
        log("-" * 70)

        for s in result.factor_ic_stats[:20]:
            log(
                f"{s.factor_name:>25} "
                f"{s.mean_ic:>10.4f} "
                f"{s.std_ic:>10.4f} "
                f"{s.ir:>8.3f} "
                f"{s.ic_positive_ratio:>7.1%} "
                f"{s.num_days:>6}"
            )

        if result.ic_decay:
            log("")
            log("IC Decay (top factors):")
            first_name = next(iter(result.ic_decay))
            max_lag = len(result.ic_decay[first_name])
            header = f"{'Factor':>25} " + " ".join(f"{i+1:>5}" for i in range(min(max_lag, 10)))
            log(header)
            log("-" * len(header))
            for fname, decay in list(result.ic_decay.items())[:10]:
                vals = " ".join(f"{v:>5.3f}" for v in decay[:10])
                log(f"{fname:>25} {vals}")

        log("=" * 70)

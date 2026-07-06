"""Performance attribution — Brinson-style sector attribution and time decomposition.

Decomposes portfolio return into:
1. **Sector allocation effect** — did we over/underweight winning sectors?
2. **Stock selection effect** — did we pick winners within each sector?
3. **Interaction effect** — combined allocation × selection
4. **Time decomposition** — which calendar periods contributed most to P&L?

Boundaries
----------
- Operates on backtest return_series + portfolio positions (post-backtest).
- Requires canonical qlib init for fetching sector/industry data.

Methodological caveat
---------------------
The sector decomposition here is a **Brinson-Fachler single-period**
approximation computed with *time-averaged* portfolio weights and
*point-to-point* instrument returns. The ``total_excess_return`` in the
result, by contrast, is the compound of daily portfolio vs benchmark
returns. The two do not reconcile in general — a long holding period,
turnover in the portfolio, or path-dependent compounding will all open
gaps between ``sector_effects_sum`` and ``total_excess_return``.

The :class:`AttributionResult` surfaces this explicitly:

- ``attribution_method`` labels the model so callers can filter/flag it.
- ``sector_effects_sum`` is the arithmetic sum of the three Brinson
  effects.
- ``reconciliation_residual`` = ``total_excess_return - sector_effects_sum``.
  A non-zero residual is expected for the single-period approximation;
  :meth:`PerformanceAttribution.print_report` emits a WARNING when the
  absolute residual exceeds :data:`RECONCILIATION_WARN_THRESHOLD` so
  readers know not to treat the decomposition as exact.

If exact daily-level reconciliation is needed, a separate daily Brinson
(weights-and-returns per day, summed across days) would be required —
this module intentionally keeps the cheaper single-period form and is
honest about the limitation rather than hiding it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from collections.abc import Mapping as _MappingABC
from dataclasses import dataclass
from typing import Any

from src.core.board_heuristic import (
    BOARD_HEURISTIC_TAXONOMY_ID,
    classify_instruments,
)
from src.core.logger import get_logger
from src.core.qlib_runtime import is_canonical_qlib_initialized

_logger = get_logger(__name__)


class PerformanceAttributionError(RuntimeError):
    """Raised on attribution computation failures."""


# Threshold (in absolute return) above which the single-period
# Brinson approximation is flagged as "does not reconcile with the
# compounded excess return". 50 bps is wide enough to absorb typical
# path-dependence across a few months but tight enough that a genuinely
# broken decomposition (e.g. wrong weights) trips the warning.
RECONCILIATION_WARN_THRESHOLD: float = 0.005


# Human-readable label for the attribution model used. Callers (dashboards,
# reporting pipelines) should display this next to the effects so the
# single-period nature of the decomposition is visible and cannot be
# mistaken for an exact daily-accurate contribution analysis.
ATTRIBUTION_METHOD_SINGLE_PERIOD: str = "brinson_fachler_single_period_approximation"


# Equal-weight benchmark across the predictions universe. This is what
# the engine has always done internally; the named constant exists so the
# choice is explicit at the config layer instead of buried in
# :meth:`PerformanceAttribution._brinson_attribution`. It is *not* the
# same as the index's actual constituent weights (e.g. CSI 300's
# free-float-cap weighting) — the limitation is surfaced via
# :meth:`PerformanceAttribution.print_report`.
BENCH_WEIGHT_METHOD_EQUAL_PROXY: str = "equal_weight_proxy"
BENCH_WEIGHT_METHOD_EQUAL: str = "equal"
BENCH_WEIGHT_METHOD_EXPLICIT: str = "explicit"

# Free-float-cap weighting (audit P6, implemented): weights derive from the
# PIT bundle's $circ_mv as-of the attribution period start, read through the
# run-level PITDataProvider. Without a provider (and without explicit
# benchmark_weights) the method FAILS LOUD — never a silent equal-weight
# result under the market_cap label (the misnomer trap stays closed).
BENCH_WEIGHT_METHOD_MARKET_CAP: str = "market_cap"

_SUPPORTED_BENCH_WEIGHT_METHODS: frozenset[str] = frozenset(
    {
        BENCH_WEIGHT_METHOD_EQUAL_PROXY,
        BENCH_WEIGHT_METHOD_EQUAL,
        BENCH_WEIGHT_METHOD_EXPLICIT,
        BENCH_WEIGHT_METHOD_MARKET_CAP,
    }
)


@dataclass(frozen=True)
class AttributionConfig:
    """Configuration for performance attribution."""

    # Date range (should match backtest period)
    start_date: str = "2025-07-01"
    end_date: str = "2025-12-31"

    # How benchmark weights are derived for the Brinson decomposition.
    #
    # * ``"equal"`` (default): every instrument in the predictions universe
    #   gets weight ``1/n``. This is a known approximation — it does not
    #   reproduce CSI 300's free-float-cap weighting and will mis-attribute
    #   sector-level allocation/selection effects when the real index is
    #   concentrated in a subset of names.  :meth:`print_report` surfaces
    #   this caveat in the report header.
    # * ``"market_cap"`` (audit P6): free-float-cap weights from the PIT
    #   bundle's ``$circ_mv``, as-of the attribution period's first day
    #   (strictly ``<= T0``), read through the run-level
    #   ``PITDataProvider``. Requires either explicit ``benchmark_weights``
    #   or a ``pit_provider`` — with neither, attribution FAILS LOUD (the
    #   misnomer trap stays closed: equal weights are never published
    #   under the market_cap label). HONEST APPROXIMATION: ``circ_mv``
    #   weighting approximates the official CSI 300 tiered free-float
    #   methodology (分级靠档); the tiering steps are not reproduced.
    bench_weight_method: str = BENCH_WEIGHT_METHOD_EQUAL_PROXY

    # Optional static ``{instrument: weight}`` mapping for the Brinson
    # benchmark leg. Weights are coerced to finite non-negative floats,
    # aligned to the analyzed instrument universe, and normalized to sum to
    # one over that overlap. Missing instruments receive zero weight.
    benchmark_weights: Mapping[str, float] | None = None

    # Optional explicit ``{instrument: industry}`` override.
    #
    # When ``None`` (default), Brinson attribution buckets instruments
    # via :func:`src.core.board_heuristic.classify_instruments` — the
    # A-share *board* heuristic (SH main / ChiNext / STAR / …). That is
    # NOT a real industry classification and the result honestly carries
    # ``sector_taxonomy = BOARD_HEURISTIC_TAXONOMY_ID`` to flag the
    # coarseness.
    #
    # When set, the engine uses this map verbatim and stamps the
    # taxonomy id from ``industry_taxonomy_id`` onto the result so a
    # downstream consumer can tell board-heuristic runs apart from
    # real-industry runs (e.g. ``"tushare_sw_l2"``).
    #
    # Instruments missing from the override map fall back to
    # ``"unknown"`` rather than to the board heuristic — mixing the two
    # taxonomies in one Brinson run would produce nonsensical
    # comparisons.
    industry_map_override: Mapping[str, str] | None = None

    # Stable taxonomy id stamped onto :class:`AttributionResult` when
    # ``industry_map_override`` is in use. Required to be non-empty
    # whenever ``industry_map_override`` is set; left as the empty
    # string when the override is absent (the engine then uses the
    # board-heuristic id).
    industry_taxonomy_id: str = ""

    # NOTE: benchmark_code is intentionally absent here. The attribution
    # engine operates on ``return_series["bench"]`` produced by
    # CanonicalBacktestOutput, which already embeds the correct benchmark
    # data. Duplicating it as a config field would create an unvalidated
    # second entry point for benchmark selection with no enforcement.


@dataclass(frozen=True)
class SectorAttribution:
    """Brinson attribution for a single sector."""

    sector: str
    portfolio_weight: float
    benchmark_weight: float
    portfolio_return: float
    benchmark_return: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_effect: float


@dataclass(frozen=True)
class MonthlyReturn:
    """Return for a single month."""

    year: int
    month: int
    portfolio_return: float
    benchmark_return: float
    excess_return: float


@dataclass(frozen=True)
class AttributionResult:
    """Complete attribution result.

    ``attribution_method``, ``sector_effects_sum`` and
    ``reconciliation_residual`` make the single-period Brinson
    approximation's inexactness explicit (see module docstring).
    """

    # Brinson sector attribution
    sector_attribution: tuple[SectorAttribution, ...]
    total_allocation_effect: float
    total_selection_effect: float
    total_interaction_effect: float

    # Time decomposition
    monthly_returns: tuple[MonthlyReturn, ...]

    # Summary
    total_portfolio_return: float
    total_benchmark_return: float
    total_excess_return: float

    # Provenance / reconciliation for the Brinson approximation.
    # These make the methodological gap between the sector decomposition
    # (single-period, time-averaged weights) and total_excess_return
    # (compounded daily) explicit and observable — silent discrepancies
    # used to let readers mistake the effects for exact attributions.
    attribution_method: str = ATTRIBUTION_METHOD_SINGLE_PERIOD
    sector_effects_sum: float = 0.0
    reconciliation_residual: float = 0.0

    # Identifies the taxonomy used to bucket instruments into "sectors".
    # When this is :data:`src.core.board_heuristic.BOARD_HEURISTIC_TAXONOMY_ID`
    # the buckets are A-share *boards* (SH main, ChiNext, STAR, …) — a
    # coarse listing-venue heuristic, NOT real industries. Consumers
    # must not silently treat it as an industry classification: one
    # board can contain banks, real estate, and utilities all at once.
    sector_taxonomy: str = BOARD_HEURISTIC_TAXONOMY_ID

    # How the benchmark weights were derived. Currently always
    # ``"equal"`` — see :class:`AttributionConfig.bench_weight_method`.
    # Carried on the result so dashboards do not need to look at the
    # config object to know whether the bench-weighting was the simple
    # equal split or a real cap-weighted scheme.
    bench_weight_method: str = BENCH_WEIGHT_METHOD_EQUAL_PROXY


class PerformanceAttribution:
    """Brinson-style performance attribution engine."""

    @classmethod
    def analyze(
        cls,
        return_series: Mapping[str, Any],
        predictions: Any,
        config: AttributionConfig | None = None,
        positions: Mapping[str, Mapping[str, float]] | None = None,
        pit_provider: Any | None = None,
    ) -> AttributionResult:
        """Run complete performance attribution.

        Parameters
        ----------
        return_series : dict
            From ``CanonicalBacktestOutput.return_series`` with keys
            ``"return"``, ``"bench"``, ``"cost"``.
        predictions : pd.Series
            Model predictions with ``(datetime, instrument)`` MultiIndex.
        config : AttributionConfig, optional
            Attribution configuration.
        positions : mapping, optional
            From ``CanonicalBacktestOutput.positions`` — authoritative per-day
            portfolio weights ``{date: {instrument: weight}}``. When supplied,
            Brinson weighting reflects the real topk-dropout selection rather
            than a predictions-score proxy. Pass ``None`` to fall back to
            prediction-score weighting (looser but works without a backtest).
        pit_provider : PITDataProvider, optional
            Audit P2 (P0-6 follow-up): when supplied, per-instrument close
            fetches route through ``pit_provider.get_features`` (post-delist
            masking applied — no forward-filled pseudo-prices from delisted
            names in the Brinson decomposition). When omitted, the direct
            ``D.features`` path runs unchanged with the existing WARN.
        """
        if config is None:
            config = AttributionConfig()

        cls._validate(config, return_series, positions, pit_provider=pit_provider)
        cls._validate_predictions(predictions)
        if pit_provider is not None:
            cls._validate_pit_provider_alignment(pit_provider)

        import pandas as pd

        _logger.info("Running performance attribution %s ~ %s...", config.start_date, config.end_date)

        # Parse return series — both keys are validated present by _validate.
        ret_dict = return_series["return"]
        bench_dict = return_series["bench"]

        port_returns = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in ret_dict.items()}
        ).sort_index()

        bench_returns = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in bench_dict.items()}
        ).sort_index()
        cls._require_finite_series(port_returns, "return_series['return']")
        cls._require_finite_series(bench_returns, "return_series['bench']")

        # Step 1: Brinson sector attribution
        _logger.info("Computing Brinson sector attribution...")
        sector_attr = cls._brinson_attribution(
            predictions, port_returns, bench_returns, config, positions,
            pit_provider=pit_provider,
        )

        total_alloc = sum(s.allocation_effect for s in sector_attr)
        total_select = sum(s.selection_effect for s in sector_attr)
        total_interact = sum(s.interaction_effect for s in sector_attr)

        # Step 2: Time decomposition
        _logger.info("Computing monthly time decomposition...")
        monthly = cls._monthly_decomposition(port_returns, bench_returns)

        # Total returns
        total_port = float((1 + port_returns).prod() - 1) if len(port_returns) > 0 else 0.0
        total_bench = float((1 + bench_returns).prod() - 1) if len(bench_returns) > 0 else 0.0
        total_excess = total_port - total_bench

        # Reconciliation: the Brinson single-period sum vs the compounded
        # daily excess return. These will diverge for any path-dependent
        # portfolio — we surface the gap rather than hide it.
        sector_effects_sum = total_alloc + total_select + total_interact
        reconciliation_residual = total_excess - sector_effects_sum

        # Stamp the taxonomy id onto the result. ``industry_map_override``
        # set → caller-supplied id (e.g. ``"tushare_sw_l2"``); otherwise
        # the board heuristic id. Validated in pair earlier in
        # :meth:`_validate` so the two cannot diverge.
        sector_taxonomy = (
            config.industry_taxonomy_id
            if config.industry_map_override is not None
            else BOARD_HEURISTIC_TAXONOMY_ID
        )

        return AttributionResult(
            sector_attribution=tuple(sector_attr),
            total_allocation_effect=total_alloc,
            total_selection_effect=total_select,
            total_interaction_effect=total_interact,
            monthly_returns=tuple(monthly),
            total_portfolio_return=total_port,
            total_benchmark_return=total_bench,
            total_excess_return=total_excess,
            attribution_method=ATTRIBUTION_METHOD_SINGLE_PERIOD,
            sector_effects_sum=sector_effects_sum,
            reconciliation_residual=reconciliation_residual,
            bench_weight_method=cls._effective_bench_weight_method(config),
            sector_taxonomy=sector_taxonomy,
        )

    @staticmethod
    def _require_finite_series(series: Any, label: str) -> None:
        bad = []
        for key, value in series.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                bad.append((key, value))
                continue
            if not math.isfinite(numeric):
                bad.append((key, value))
        if bad:
            sample = ", ".join(f"{k!s}={v!r}" for k, v in bad[:3])
            raise PerformanceAttributionError(
                f"{label} contains non-finite or non-numeric values "
                f"({sample}). Attribution refuses to let pandas silently "
                "skip or propagate malformed return data."
            )

    @classmethod
    def _validate(
        cls,
        config: AttributionConfig,
        return_series: Mapping[str, Any],
        positions: Mapping[str, Mapping[str, float]] | None,
        pit_provider: Any | None = None,
    ) -> None:
        if not is_canonical_qlib_initialized():
            raise PerformanceAttributionError(
                "Canonical qlib runtime is not initialized."
            )
        # Both keys must be present AND non-empty. An empty mapping for
        # either side produces a degenerate run: a missing portfolio
        # return series collapses every effect to zero; a missing benchmark
        # silently sets the benchmark return to 0.0 and the "excess"
        # return becomes the portfolio return itself, which is nonsense
        # but looks plausible in a report. Both shapes are caller errors,
        # not signals to fall back.
        #
        # Order: presence checks before emptiness checks, so a request
        # missing the bench key still raises "missing bench" rather than
        # "empty return" when the return value happens to also be empty.
        if "return" not in return_series:
            raise PerformanceAttributionError(
                "return_series must contain 'return' key."
            )
        # 'bench' is mandatory: attribution is defined relative to a benchmark,
        # and CanonicalBacktestOutput always populates it. Previously this was
        # silently defaulted to an empty dict, which produced zero-benchmark
        # attribution results that looked plausible but were nonsense. No
        # implicit fallback — callers must pass real bench data.
        if "bench" not in return_series:
            raise PerformanceAttributionError(
                "return_series must contain 'bench' key. "
                "Attribution is defined relative to a benchmark; pass the full "
                "return_series from CanonicalBacktestOutput."
            )
        # ``isinstance(Mapping) + len()`` rather than ``not value`` — a
        # bare ``not value`` raises ValueError("truth value is ambiguous")
        # when ``value`` is a pandas Series/DataFrame, which would
        # surprise callers who happen to pass a Series-shaped return
        # series. The contract is "non-empty mapping" so we test that
        # explicitly.
        ret_value = return_series["return"]
        if not isinstance(ret_value, _MappingABC) or len(ret_value) == 0:
            raise PerformanceAttributionError(
                "return_series['return'] must be a non-empty mapping; "
                f"got {type(ret_value).__name__} of size "
                f"{len(ret_value) if hasattr(ret_value, '__len__') else 'unknown'}. "
                "Pass the populated return series from CanonicalBacktestOutput; "
                "attribution cannot run on an empty portfolio return."
            )
        bench_value = return_series["bench"]
        if not isinstance(bench_value, _MappingABC) or len(bench_value) == 0:
            raise PerformanceAttributionError(
                "return_series['bench'] must be a non-empty mapping; "
                f"got {type(bench_value).__name__} of size "
                f"{len(bench_value) if hasattr(bench_value, '__len__') else 'unknown'}. "
                "An empty benchmark would silently coerce total_benchmark_return "
                "to 0.0 and turn 'excess' return into 'portfolio' return — a "
                "label mismatch that publishes meaningless attribution. Pass "
                "the populated bench series from CanonicalBacktestOutput."
            )
        # Explicit empty positions dict is a caller error, not a signal to fall
        # back silently to prediction-score weights.  Pass None to opt into the
        # predictions fallback intentionally; pass a non-empty dict for real
        # positions.  This upholds the project's "no implicit fallback" rule.
        if positions is not None and len(positions) == 0:
            raise PerformanceAttributionError(
                "positions was supplied as an empty dict. "
                "Pass positions=None to use the predictions-score fallback, "
                "or supply the non-empty positions map from CanonicalBacktestOutput."
            )
        # bench_weight_method is fail-fast validated here so the engine never
        # runs with an unsupported value, and never accepts "market_cap"
        # while still computing equal-weight numbers (which would be a
        # silent label mismatch).
        if config.bench_weight_method not in _SUPPORTED_BENCH_WEIGHT_METHODS:
            raise PerformanceAttributionError(
                f"AttributionConfig.bench_weight_method must be one of "
                f"{sorted(_SUPPORTED_BENCH_WEIGHT_METHODS)}; "
                f"got {config.bench_weight_method!r}."
            )
        if config.benchmark_weights is not None and len(config.benchmark_weights) == 0:
            raise PerformanceAttributionError(
                "AttributionConfig.benchmark_weights is an empty mapping. "
                "Pass None to use the equal-weight proxy, or provide a "
                "non-empty {instrument: weight} mapping."
            )
        if (
            config.bench_weight_method == BENCH_WEIGHT_METHOD_EXPLICIT
            and config.benchmark_weights is None
        ):
            raise PerformanceAttributionError(
                f"bench_weight_method={config.bench_weight_method!r} requires "
                "AttributionConfig.benchmark_weights. This module does not "
                "fetch or infer benchmark constituent weights implicitly."
            )
        # market_cap (audit P6): the approved source of truth is the PIT
        # bundle's $circ_mv read through the run-level PITDataProvider —
        # WITHOUT one (and without explicit weights) attribution refuses
        # up front; an equal-weight fallback under the market_cap label is
        # exactly the misnomer trap this fail-fast validation exists to
        # close.
        if (
            config.bench_weight_method == BENCH_WEIGHT_METHOD_MARKET_CAP
            and config.benchmark_weights is None
            and pit_provider is None
        ):
            raise PerformanceAttributionError(
                "bench_weight_method='market_cap' without explicit "
                "benchmark_weights requires the run-level PITDataProvider "
                "(configure delisted_registry_path so the engine constructs "
                "one, or pass pit_provider=...) — free-float caps are read "
                "through the §4.3.2 layer, and silently falling back to "
                "equal weights under the market_cap label is refused "
                "(audit P6)."
            )
        # Industry override / taxonomy id pairing — both must be set
        # together, never just one. Without this guard a caller could
        # supply an override map and get a result stamped with the
        # board-heuristic taxonomy id, or set a custom taxonomy id while
        # the engine silently uses the board heuristic. Either way is
        # the kind of label-mismatch trap the no-implicit-fallback rule
        # is meant to close.
        if config.industry_map_override is not None:
            if len(config.industry_map_override) == 0:
                raise PerformanceAttributionError(
                    "AttributionConfig.industry_map_override is an empty mapping. "
                    "Pass None to use the board heuristic explicitly, or supply "
                    "a non-empty {instrument: industry} map."
                )
            if not str(config.industry_taxonomy_id or "").strip():
                raise PerformanceAttributionError(
                    "AttributionConfig.industry_taxonomy_id must be a non-empty "
                    "string when industry_map_override is set. The taxonomy id "
                    "(e.g. 'tushare_sw_l2') is stamped onto the result so "
                    "downstream consumers can tell board-heuristic runs apart "
                    "from real-industry runs."
                )
        elif config.industry_taxonomy_id:
            raise PerformanceAttributionError(
                "AttributionConfig.industry_taxonomy_id is set but "
                "industry_map_override is None. Provide both together, or "
                "neither: a taxonomy id with no override would mis-label the "
                "result as a real-industry run while the engine actually used "
                "the board heuristic."
            )

    @staticmethod
    def _validate_pit_provider_alignment(pit_provider: Any) -> None:
        """Alignment guard — identical contract to
        ``BacktestRunner._validate_pit_provider_alignment`` and
        ``FeatureDatasetBuilder._validate_pit_provider_alignment``. When a
        PIT provider is supplied, the canonical qlib runtime's
        ``provider_uri`` MUST match. The (third) duplication is intentional:
        each entry point enforces the invariant independently, so a future
        refactor changing one cannot silently weaken the others.
        """
        from src.core.qlib_runtime import (
            _normalize_provider_uri,
            get_canonical_qlib_config,
        )

        canonical = get_canonical_qlib_config()
        if canonical is None:
            raise PerformanceAttributionError(
                "pit_provider was supplied but the canonical qlib config is "
                "unavailable. Initialize qlib via init_qlib_canonical(...) "
                "before passing a PITDataProvider to attribution."
            )
        pit_uri_raw = str(getattr(pit_provider, "_provider_uri", ""))
        if not pit_uri_raw:
            raise PerformanceAttributionError(
                "pit_provider has no readable _provider_uri attribute "
                f"(got {pit_provider!r}). Expected a PITDataProvider."
            )
        pit_norm = _normalize_provider_uri(pit_uri_raw)
        if canonical.provider_uri != pit_norm:
            raise PerformanceAttributionError(
                "PIT provider / qlib provider_uri mismatch — attribution "
                "would silently consume closes from the wrong provider. "
                f"qlib canonical provider_uri = {canonical.provider_uri!r}; "
                f"pit_provider._provider_uri = {pit_norm!r}."
            )

    @staticmethod
    def _validate_predictions(predictions: Any) -> None:
        """Structural validation for the predictions Series.

        Mirrors SignalAnalyzer's contract: ``predictions`` must be a
        non-empty ``pd.Series`` carrying a ``(datetime, instrument)``
        MultiIndex. The attribution math reads ``instrument`` off the
        index — a flat index or a wrong level name produces silent
        miscomputation rather than an obvious failure, so we surface the
        mismatch here.
        """
        import pandas as pd

        if not isinstance(predictions, pd.Series):
            raise PerformanceAttributionError(
                f"predictions must be pd.Series, got {type(predictions).__name__}."
            )
        if not isinstance(predictions.index, pd.MultiIndex):
            raise PerformanceAttributionError(
                "predictions must have a (datetime, instrument) MultiIndex."
            )
        if "instrument" not in predictions.index.names:
            raise PerformanceAttributionError(
                "predictions.index must have an 'instrument' level; "
                f"got levels {list(predictions.index.names)}."
            )
        if predictions.empty:
            raise PerformanceAttributionError("predictions Series is empty.")

    @staticmethod
    def _build_sector_map(
        instruments: list[str], config: AttributionConfig,
    ) -> dict[str, str]:
        """Resolve the ``{instrument: bucket}`` map for Brinson.

        Two paths:

        - ``config.industry_map_override`` is set → use it verbatim,
          falling back to ``"unknown"`` for instruments missing from
          the override. Mixing in board-heuristic buckets for the
          missing names would silently produce a Brinson run that
          claims a real industry taxonomy while half the rows came
          from the listing-venue heuristic.
        - Otherwise → ``classify_instruments`` (the board heuristic).

        The corresponding ``sector_taxonomy`` stamping happens in
        :meth:`analyze` based on the same ``config.industry_map_override``
        flag, so the result label and the actual map can never disagree.
        """
        if config.industry_map_override is not None:
            override = config.industry_map_override
            return {
                inst: str(override.get(inst, "unknown")) for inst in instruments
            }
        return classify_instruments(instruments)

    @classmethod
    def _brinson_attribution(
        cls,
        predictions: Any,
        port_returns: Any,
        bench_returns: Any,
        config: AttributionConfig,
        positions: Mapping[str, Mapping[str, float]] | None = None,
        pit_provider: Any | None = None,
    ) -> list[SectorAttribution]:
        """Brinson-Fachler single-period attribution by sector.

        Portfolio weights are derived from ``positions`` (time-averaged real
        holdings) when available — this matches the actual topk-dropout
        selection. Otherwise we fall back to a prediction-score proxy,
        clipping negatives to zero so ranked-low names do not leak weight.
        """
        import pandas as pd

        # Instruments universe: union of predictions and held positions
        pred_instruments = predictions.index.get_level_values("instrument").unique().tolist()
        held_instruments: list[str] = []
        if positions:
            seen: set[str] = set()
            for day_map in positions.values():
                for inst in day_map:
                    if inst not in seen:
                        seen.add(inst)
                        held_instruments.append(inst)
        instruments = sorted(set(pred_instruments) | set(held_instruments))
        sector_map = cls._build_sector_map(instruments, config)

        # Portfolio weights: prefer real positions, fall back to prediction scores.
        if positions:
            # Time-average the actual per-day weights
            weight_sum: dict[str, float] = {}
            day_count = 0
            for day_key, day_map in positions.items():
                # ``positions`` contract is ``{date_str: {instrument: float}}``.
                # If a day's value is anything other than a mapping (a stray
                # list, scalar, or string from a corrupt upstream serialiser),
                # ``not day_map`` was True only for empty falsy values and
                # the next ``.items()`` call would crash mid-iteration with
                # AttributeError, after several days had already accumulated.
                # Reject the bad day loudly so the operator can find the
                # serialisation upstream instead of chasing a partial result.
                if not isinstance(day_map, dict):
                    raise PerformanceAttributionError(
                        "positions contract violation: each day's value must "
                        "be a mapping ``{instrument: weight}``. Got "
                        f"{type(day_map).__name__} for date {day_key!r}. "
                        "This indicates an upstream serialisation bug."
                    )
                if not day_map:
                    continue
                day_count += 1
                for inst, w in day_map.items():
                    try:
                        weight = float(w)
                    except (TypeError, ValueError) as exc:
                        # Record the drop instead of silently skipping —
                        # a string / None weight is a serialisation defect
                        # the operator needs to see. We continue rather
                        # than abort (a single bad name should not
                        # invalidate the whole attribution) but the
                        # WARNING leaves a trail in the run log.
                        _logger.warning(
                            "PerformanceAttribution: dropping non-numeric "
                            "weight for %s on %s (%s: %s).",
                            inst, day_key, type(exc).__name__, exc,
                        )
                        continue
                    if not math.isfinite(weight):
                        _logger.warning(
                            "PerformanceAttribution: dropping non-finite "
                            "weight for %s on %s (%r).",
                            inst, day_key, w,
                        )
                        continue
                    weight_sum[inst] = weight_sum.get(inst, 0.0) + weight
            if day_count == 0 or not weight_sum:
                # positions was non-empty at validation time but every day
                # deserialized to zero weights — treat as corrupted input.
                raise PerformanceAttributionError(
                    "positions was provided but all entries yielded zero usable "
                    "weights after deserialization. Check the positions map from "
                    "CanonicalBacktestOutput for corruption."
                )
            raw = pd.Series({k: v / day_count for k, v in weight_sum.items()})
            total = float(raw.sum())
            # ``total <= 0`` happens when every per-instrument averaged weight
            # is zero (or the rare negative-leg case): the dict is non-empty
            # but the values are all 0.0, so ``not weight_sum`` above does NOT
            # catch it. Without this guard the engine would silently feed an
            # all-zero weight Series into Brinson and produce a "valid-looking"
            # zero-allocation, zero-selection attribution — exactly the kind
            # of degenerate output the no-implicit-fallback rule is meant to
            # block.
            if total <= 0:
                raise PerformanceAttributionError(
                    "positions yielded a non-positive aggregate weight "
                    f"({total:.6g}) after time-averaging. Every per-instrument "
                    "averaged weight is zero (or net-negative); this is a "
                    "corrupted positions map, not a valid input. Check the "
                    "CanonicalBacktestOutput.positions serialization."
                )
            port_weights = raw / total
        else:
            port_weights = cls._predictions_to_weights(predictions)

        bench_weights = cls._resolve_benchmark_weights(
            instruments, config, pit_provider=pit_provider,
        )

        # Get per-instrument returns over the period
        inst_returns = cls._get_instrument_returns(
            instruments, config, pit_provider=pit_provider,
        )
        if inst_returns.empty:
            raise PerformanceAttributionError(
                "No finite instrument close returns were available for "
                "Brinson attribution. Refusing to emit all-zero sector "
                "effects from missing qlib close data."
            )

        # Aggregate by sector
        sectors = sorted(set(sector_map.values()))
        results = []

        # Overall benchmark return for BF model (compound, consistent with portfolio)
        total_bench_ret = float((1 + bench_returns).prod() - 1) if len(bench_returns) > 0 else 0.0

        for sector in sectors:
            sector_instruments = [i for i in instruments if sector_map.get(i) == sector]
            if not sector_instruments:
                continue

            # Portfolio weight in this sector
            w_p = float(port_weights.reindex(sector_instruments).sum())
            # Benchmark weight in this sector
            w_b = float(bench_weights.reindex(sector_instruments).sum())

            # Sector return in portfolio (weighted avg of instrument returns)
            sector_port_w = port_weights.reindex(sector_instruments).dropna()
            sector_inst_r = inst_returns.reindex(sector_instruments).dropna()
            common = sector_port_w.index.intersection(sector_inst_r.index)

            if len(common) > 0 and w_p > 1e-9:
                r_p = float((sector_port_w[common] * sector_inst_r[common]).sum() / w_p)
            else:
                r_p = 0.0

            # Sector return in benchmark using the configured benchmark weights.
            sector_bench_w = bench_weights.reindex(sector_instruments).dropna()
            common_b = sector_bench_w.index.intersection(sector_inst_r.index)
            if len(common_b) > 0 and w_b > 1e-9:
                r_b = float((sector_bench_w[common_b] * sector_inst_r[common_b]).sum() / w_b)
            else:
                r_b = 0.0

            # Brinson-Fachler decomposition
            allocation = (w_p - w_b) * (r_b - total_bench_ret)
            selection = w_b * (r_p - r_b)
            interaction = (w_p - w_b) * (r_p - r_b)
            total = allocation + selection + interaction

            results.append(SectorAttribution(
                sector=sector,
                portfolio_weight=round(w_p, 4),
                benchmark_weight=round(w_b, 4),
                portfolio_return=round(r_p, 4),
                benchmark_return=round(r_b, 4),
                allocation_effect=round(allocation, 6),
                selection_effect=round(selection, 6),
                interaction_effect=round(interaction, 6),
                total_effect=round(total, 6),
            ))

        # Sort by absolute total effect
        results.sort(key=lambda s: abs(s.total_effect), reverse=True)
        return results

    @staticmethod
    def _effective_bench_weight_method(config: AttributionConfig) -> str:
        """Return the result label that matches the weights actually used."""
        if config.benchmark_weights is not None:
            if config.bench_weight_method == BENCH_WEIGHT_METHOD_MARKET_CAP:
                return BENCH_WEIGHT_METHOD_MARKET_CAP
            return BENCH_WEIGHT_METHOD_EXPLICIT
        if config.bench_weight_method == BENCH_WEIGHT_METHOD_EQUAL:
            return BENCH_WEIGHT_METHOD_EQUAL_PROXY
        return config.bench_weight_method

    # As-of lookback for the free-float cap fetch (audit P6): the attribution
    # period's first day T0 can fall inside a suspension for individual names
    # — the last published $circ_mv within this many CALENDAR days up to (and
    # including) T0 is used. 30 days tolerates long suspensions without
    # reaching into a materially different capitalization regime.
    _MARKET_CAP_ASOF_LOOKBACK_DAYS: int = 30

    @classmethod
    def _market_cap_weights(
        cls,
        instruments: Sequence[str],
        config: AttributionConfig,
        pit_provider: Any,
    ) -> Any:
        """Free-float-cap benchmark weights, as-of the period start (audit P6).

        Source of truth: the PIT bundle's ``$circ_mv`` (free-float market
        cap from ``daily_basic``) read through the run-level
        ``PITDataProvider`` — the single sanctioned §4.3.2 door; this module
        deliberately opens NO new direct ``D.features`` bypass. As-of
        semantics: for each analyzed instrument, the LAST published value at
        or before the attribution period's first day (strictly ``<= T0``,
        within a bounded lookback) — never a value from inside the period
        (that would grade the benchmark with future capitalization).

        HONEST APPROXIMATION (project convention): ``circ_mv`` weighting
        approximates the official CSI 300 methodology, which applies TIERED
        free-float ratios (分级靠档) on top of the free-float cap; the
        tiering steps are not reproduced here. The analyzed universe is the
        PREDICTIONS universe — for canonical ``csi300`` runs it is already
        PIT-membered upstream (qlib instruments intervals), so membership is
        not re-derived here.

        FAIL-LOUD: a missing provider, an instrument with no as-of value in
        the lookback, or a non-positive/non-finite cap refuses attribution —
        never a silent equal-weight fallback under the ``market_cap`` label
        and never a silent partial drop.
        """
        import pandas as pd

        if pit_provider is None:
            raise PerformanceAttributionError(
                "bench_weight_method='market_cap' requires the run-level "
                "PITDataProvider (configure delisted_registry_path so the "
                "engine constructs one, or pass pit_provider=...) — "
                "free-float caps are read through the §4.3.2 layer; "
                "guessing weights is refused (audit P6)."
            )
        t0 = pd.Timestamp(config.start_date)
        lookback_start = t0 - pd.Timedelta(days=cls._MARKET_CAP_ASOF_LOOKBACK_DAYS)
        panel = pit_provider.get_features(
            ["$circ_mv"],
            lookback_start.strftime("%Y-%m-%d"),
            t0.strftime("%Y-%m-%d"),
            instruments=list(instruments),
        )
        if panel is None or len(panel) == 0:
            raise PerformanceAttributionError(
                "market_cap weights: no $circ_mv rows in "
                f"[{lookback_start.date()}, {t0.date()}] for the analyzed "
                "universe — the PIT bundle lacks daily_basic coverage for "
                "this window. Rebuild the bundle with daily_basic, or use "
                "explicit benchmark_weights / the equal-weight proxy."
            )
        col = panel["$circ_mv"]
        caps: dict[str, float] = {}
        missing: list[str] = []
        for inst in instruments:
            try:
                series = col.xs(inst, level="instrument").sort_index().dropna()
            except KeyError:
                missing.append(inst)
                continue
            if series.empty:
                missing.append(inst)
                continue
            # as-of value: last published <= T0 (the fetch window's end)
            caps[inst] = float(series.iloc[-1])
        if missing:
            raise PerformanceAttributionError(
                f"market_cap weights: {len(missing)} of {len(list(instruments))} "
                "analyzed instrument(s) have no as-of $circ_mv within "
                f"{cls._MARKET_CAP_ASOF_LOOKBACK_DAYS} calendar days up to "
                f"{t0.date()} (first: {missing[:5]}). Refusing to guess or "
                "silently drop them — fix daily_basic coverage or pass "
                "explicit benchmark_weights (audit P6, no-silent-fallback)."
            )
        bad = sorted(
            inst for inst, cap in caps.items()
            if not math.isfinite(cap) or cap <= 0
        )
        if bad:
            raise PerformanceAttributionError(
                f"market_cap weights: non-positive/non-finite as-of $circ_mv "
                f"for {bad[:5]} — corrupt daily_basic data; refusing to "
                "weight the benchmark with it (audit P6)."
            )
        total = sum(caps.values())
        return pd.Series(
            {inst: caps[inst] / total for inst in instruments}, dtype=float,
        )

    @classmethod
    def _resolve_benchmark_weights(
        cls,
        instruments: Sequence[str],
        config: AttributionConfig,
        pit_provider: Any | None = None,
    ) -> Any:
        """Build the Brinson benchmark weight vector for ``instruments``."""
        import pandas as pd

        instrument_list = list(instruments)
        if not instrument_list:
            raise PerformanceAttributionError(
                "Cannot compute benchmark weights for an empty instrument universe."
            )

        if config.benchmark_weights is None:
            if config.bench_weight_method == BENCH_WEIGHT_METHOD_MARKET_CAP:
                # Audit P6: the approved automatic source — PIT free-float
                # caps as-of the period start (see _market_cap_weights).
                return cls._market_cap_weights(
                    instrument_list, config, pit_provider,
                )
            if config.bench_weight_method == BENCH_WEIGHT_METHOD_EXPLICIT:
                raise PerformanceAttributionError(
                    f"bench_weight_method={config.bench_weight_method!r} requires "
                    "AttributionConfig.benchmark_weights."
                )
            return pd.Series(
                1.0 / len(instrument_list),
                index=instrument_list,
                dtype=float,
            )

        raw_values: dict[str, float] = {}
        for inst, value in config.benchmark_weights.items():
            inst_key = str(inst)
            try:
                weight = float(value)
            except (TypeError, ValueError) as exc:
                raise PerformanceAttributionError(
                    "AttributionConfig.benchmark_weights contains a non-numeric "
                    f"weight for {inst_key!r}: {value!r}."
                ) from exc
            if not math.isfinite(weight) or weight < 0:
                raise PerformanceAttributionError(
                    "AttributionConfig.benchmark_weights must contain finite "
                    f"non-negative weights; got {weight!r} for {inst_key!r}."
                )
            raw_values[inst_key] = weight

        raw = pd.Series(raw_values, dtype=float)
        aligned = raw.reindex(instrument_list).fillna(0.0)
        total = float(aligned.sum())
        if total <= 0:
            raise PerformanceAttributionError(
                "AttributionConfig.benchmark_weights has no positive overlap "
                "with the analyzed instrument universe."
            )
        return aligned / total

    @staticmethod
    def _predictions_to_weights(predictions: Any) -> Any:
        """Fallback: convert prediction scores to long-only weights.

        Clips negative scores to zero so that names the model ranks poorly
        do not absorb portfolio weight.

        Raises
        ------
        PerformanceAttributionError
            When *every* per-instrument averaged score is non-positive.
            The previous implementation quietly fell back to a uniform
            ``1/n`` weighting in this case — mathematically well-defined,
            but semantically wrong: "model produces no long signal" is a
            failure mode that must not be disguised as "equal-weight
            portfolio attribution". The caller in :meth:`analyze` now
            catches this and downgrades gracefully (skip attribution with
            a loud WARNING) rather than hiding the problem.
        """
        avg_pred = predictions.groupby(level="instrument").mean()
        clipped = avg_pred.clip(lower=0.0)
        total = float(clipped.sum())
        if total > 0:
            return clipped / total
        raise PerformanceAttributionError(
            "All prediction scores are non-positive — cannot derive a "
            "long-only weight vector. This indicates the model is "
            "emitting no buy signal (zero/negative scores across every "
            "instrument), not a normal attribution input. Previously we "
            "fell back to uniform weighting here, which disguised model "
            "failure as a valid equal-weight portfolio."
        )

    @classmethod
    def _get_instrument_returns(
        cls,
        instruments: list[str],
        config: AttributionConfig,
        pit_provider: Any | None = None,
    ) -> Any:
        """Get total return per instrument over the attribution period.

        Audit P2 (P0-6 follow-up): when ``pit_provider`` is supplied the
        close fetch routes through ``PITDataProvider.get_features`` — the
        §4.3.2 post-delist mask applies, so a delisted instrument's closes
        past its delist_date are NaN instead of forward-filled stale values,
        and its total return is computed from real trading days only. Same
        opt-in pattern as ``BacktestRunner._compute_equalweight_baseline``
        and ``FactorAnalyzer._fetch_close_panel``.

        When ``pit_provider`` is None the legacy direct ``D.features`` path
        runs BIT-IDENTICALLY to the pre-opt-in implementation, and the WARN
        below keeps the bypass observable (independent callers unchanged).
        """
        import pandas as pd

        if pit_provider is not None:
            close = pit_provider.get_features(
                ["$close"], config.start_date, config.end_date,
                instruments=instruments,
            )
        else:
            from qlib.data import D

            _logger.warning(
                "PerformanceAttribution._get_instrument_returns: bypasses "
                "PITDataProvider (pit_provider is None) — close prices for "
                "delisted instruments may carry stale / forward-filled "
                "values into the Brinson decomposition. Pass a "
                "PITDataProvider to opt into the §4.3.2 post-delist mask. "
                "Audit P0-6."
            )
            close = D.features(
                instruments, ["$close"],
                start_time=config.start_date, end_time=config.end_date,
            )
        close.columns = ["close"]

        # Total return = last_close / first_close - 1
        result = {}
        for inst in instruments:
            try:
                inst_close = close.xs(inst, level="instrument")["close"].dropna()
                if len(inst_close) >= 2:
                    result[inst] = float(inst_close.iloc[-1] / inst_close.iloc[0] - 1)
            except (KeyError, IndexError):
                continue

        return pd.Series(result)

    @classmethod
    def _monthly_decomposition(cls, port_returns: Any, bench_returns: Any) -> list[MonthlyReturn]:
        """Decompose returns by calendar month."""
        import pandas as pd

        if port_returns.empty:
            return []

        # Group by year-month
        port_monthly = port_returns.groupby(
            [port_returns.index.year, port_returns.index.month]
        ).apply(lambda x: float((1 + x).prod() - 1))

        bench_monthly = bench_returns.groupby(
            [bench_returns.index.year, bench_returns.index.month]
        ).apply(lambda x: float((1 + x).prod() - 1)) if len(bench_returns) > 0 else pd.Series(dtype=float)

        results = []
        for (year, month), port_r in port_monthly.items():
            # When a month is missing from ``bench_monthly`` the previous
            # implementation defaulted to ``0.0``, which silently
            # disguises a benchmark-data gap as "the index didn't move
            # this month" and inflates the reported excess return.
            # Surface the gap as a NaN benchmark / NaN excess so a
            # downstream consumer can tell "missing" from "actually flat".
            if (year, month) in bench_monthly:
                bench_r = float(bench_monthly[(year, month)])
                excess = round(port_r - bench_r, 6)
            else:
                _logger.warning(
                    "PerformanceAttribution: monthly benchmark return missing "
                    "for %04d-%02d; reporting NaN benchmark / NaN excess "
                    "rather than substituting 0.0.",
                    int(year), int(month),
                )
                bench_r = float("nan")
                excess = float("nan")
            # ``round`` of NaN is NaN, so the same call works for both
            # the present-bench and missing-bench branches.
            results.append(MonthlyReturn(
                year=int(year),
                month=int(month),
                portfolio_return=round(port_r, 6),
                benchmark_return=round(bench_r, 6),
                excess_return=excess,
            ))

        return results

    @classmethod
    def print_report(cls, result: AttributionResult) -> None:
        """Log a formatted attribution report."""
        log = _logger.info
        log("=" * 75)
        log("PERFORMANCE ATTRIBUTION REPORT")
        log("=" * 75)

        log("Overall:")
        log("  Portfolio return:  %.2f%%", result.total_portfolio_return * 100)
        log("  Benchmark return:  %.2f%%", result.total_benchmark_return * 100)
        log("  Excess return:     %.2f%%", result.total_excess_return * 100)
        log("")
        log("Brinson Decomposition (method: %s):", result.attribution_method)
        # The taxonomy label tells the reader whether the buckets below
        # are real industries or A-share *boards* (a coarse code-prefix
        # heuristic). Without this line a SH_Main / ChiNext label could
        # be misread as an industry classification, which it is not.
        log("  Sector taxonomy:   %s", result.sector_taxonomy)
        # The benchmark weighting choice — currently always equal across
        # the predictions universe — is not the same as the index's real
        # constituent weights (CSI 300 is free-float-cap weighted). The
        # report flags the gap explicitly so allocation effects are not
        # misread as exact contributions vs. the actual index.
        log("  Bench weight:      %s", result.bench_weight_method)
        if result.bench_weight_method == BENCH_WEIGHT_METHOD_EQUAL_PROXY:
            log(
                "    NOTE: equal-weight benchmark across the predictions "
                "universe; this does NOT reproduce the index's real "
                "(e.g. free-float-cap) weighting. Allocation effects vs. "
                "the actual published index will differ."
            )
        elif result.bench_weight_method == BENCH_WEIGHT_METHOD_EXPLICIT:
            log("    NOTE: explicit caller-supplied benchmark weights.")
        elif result.bench_weight_method == BENCH_WEIGHT_METHOD_MARKET_CAP:
            log("    NOTE: caller-supplied market-cap benchmark weights.")
        log("  Allocation effect: %+.4f", result.total_allocation_effect)
        log("  Selection effect:  %+.4f", result.total_selection_effect)
        log("  Interaction effect:%+.4f", result.total_interaction_effect)
        log("  Sector effects sum:%+.4f", result.sector_effects_sum)
        log(
            "  Reconciliation residual (excess - sum): %+.4f",
            result.reconciliation_residual,
        )
        if abs(result.reconciliation_residual) > RECONCILIATION_WARN_THRESHOLD:
            _logger.warning(
                "Attribution reconciliation residual %+.4f exceeds threshold "
                "%.4f. The Brinson single-period approximation does not match "
                "the compounded daily excess return — expected for path-dependent "
                "portfolios, but treat the sector effects as indicative, not exact.",
                result.reconciliation_residual,
                RECONCILIATION_WARN_THRESHOLD,
            )

        log("")
        log("Sector Attribution:")
        log(f"{'Sector':>12} {'Wt_P':>7} {'Wt_B':>7} {'Ret_P':>8} {'Ret_B':>8} "
            f"{'Alloc':>9} {'Select':>9} {'Total':>9}")
        log("-" * 75)
        for s in result.sector_attribution:
            log(
                f"{s.sector:>12} "
                f"{s.portfolio_weight:>6.1%} "
                f"{s.benchmark_weight:>6.1%} "
                f"{s.portfolio_return:>7.2%} "
                f"{s.benchmark_return:>7.2%} "
                f"{s.allocation_effect:>+9.4f} "
                f"{s.selection_effect:>+9.4f} "
                f"{s.total_effect:>+9.4f}"
            )

        if result.monthly_returns:
            log("")
            log("Monthly Returns:")
            log(f"{'Month':>10} {'Portfolio':>10} {'Benchmark':>10} {'Excess':>10}")
            log("-" * 42)
            for m in result.monthly_returns:
                log(
                    f"{m.year}-{m.month:02d}    "
                    f"{m.portfolio_return:>9.2%} "
                    f"{m.benchmark_return:>9.2%} "
                    f"{m.excess_return:>+9.2%}"
                )

        log("=" * 75)

"""PIT adapter — the SOLE data door for factor mining.

`FactorMiningDataView` is the only place in `src/factor_mining/` that
holds a reference to ``src.pit.query.PITDataProvider``. Every later
phase (evaluator, fitness, factor pool, GP engine, miner, validator,
handler) consumes panel + forward-return data through this view and
never imports the PIT layer directly.

D5 strict gate (``docs/factor_mining/decisions.md``) remains satisfied
trivially for this file: ``PITDataProvider`` is imported from
``src.pit.query`` (the PIT door), which encapsulates the qlib runtime
internally; this module imports nothing in the qlib package directly.

Shape adaptation
----------------
``PITDataProvider.get_features`` returns a ``pd.DataFrame`` indexed by
``(instrument, datetime)`` MultiIndex with one column per requested
field (``inventory.md`` §A.3). The operator engine in
``operators.py`` works on date × ticker ``DataFrame``s. This adapter
performs the swaplevel + unstack so downstream code sees the
operator-friendly layout, with the post-delist NaN mask preserved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from src.pit.query import PITDataProvider

from .grammar import FeatureRegistry


def _default_fields() -> tuple[str, ...]:
    return tuple(FeatureRegistry.V1)


class FactorMiningDataView:
    """The only bridge between ``src/factor_mining/`` and the PIT layer.

    Responsibilities:

    - Load the OHLCV panel (six PIT bin fields per ``decisions.md`` D3)
      once via ``PITDataProvider.get_features``.
    - Convert PIT's ``(instrument, datetime)`` MultiIndex output into
      a dict of date × ticker DataFrames per field — the shape the
      operator engine consumes.
    - Construct the forward-return label panel via a qlib expression
      string (``Ref($open, -h-1) / Ref($open, -1) - 1`` per
      ``decisions.md`` D1 and ``factor_mining_design.md`` §5.3).
    - Expose a boolean date × ticker universe membership mask.

    The class accepts any object implementing the
    ``PITDataProvider.get_features`` /
    ``PITDataProvider.get_universe_range`` API by duck typing, so
    Phase 2 unit tests can pass a lightweight stub without
    instantiating the real provider (which would require a built PIT
    bundle on disk per ``inventory.md`` §F.3).
    """

    def __init__(
        self,
        pit_provider: PITDataProvider | Any,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        universe_name: str = "all",
        instruments: list[str] | None = None,
        fields: Iterable[str] | None = None,
    ) -> None:
        self._provider = pit_provider
        self._start = start
        self._end = end
        self._universe_name = universe_name
        self._instruments = list(instruments) if instruments is not None else None
        self._fields: tuple[str, ...] = (
            tuple(fields) if fields is not None else _default_fields()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def fields(self) -> tuple[str, ...]:
        return self._fields

    @property
    def universe_name(self) -> str:
        return self._universe_name

    def load_panel(self) -> dict[str, pd.DataFrame]:
        """Load the configured OHLCV panel.

        Returns a dict mapping each field name (e.g. ``"$close"``) to
        a ``pd.DataFrame`` whose index is a sorted ``DatetimeIndex``
        and whose columns are sorted ticker symbols. The post-delist
        NaN mask is preserved from PIT (a cell ``(date, ticker)`` with
        ``date > delist_date`` is NaN).
        """
        raw = self._provider.get_features(
            fields=list(self._fields),
            start=self._start,
            end=self._end,
            universe_name=self._universe_name,
            instruments=self._instruments,
        )
        return self._pivot_per_field(raw, self._fields)

    def forward_return(self, horizon: int = 1) -> pd.DataFrame:
        """T+1 → T+1+horizon open-to-open return panel.

        Per ``decisions.md`` D1 and ``factor_mining_design.md`` §5.3:

        ``Ref($open, -horizon-1) / Ref($open, -1) - 1``

        which corresponds to "buy at T+1's open, sell at T+1+horizon's
        open". Routed through ``PITDataProvider.get_features`` so the
        post-delist NaN mask applies to the label too.
        """
        if horizon < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")
        expr = f"Ref($open, -{horizon + 1}) / Ref($open, -1) - 1"
        raw = self._provider.get_features(
            fields=[expr],
            start=self._start,
            end=self._end,
            universe_name=self._universe_name,
            instruments=self._instruments,
        )
        pivoted = self._pivot_per_field(raw, (expr,))
        return pivoted[expr]

    def universe_mask(self) -> pd.DataFrame:
        """Boolean date × ticker mask of universe membership.

        ``True`` on (date, ticker) cells where the ticker is in the
        universe set on that trading day per
        ``PITDataProvider.get_universe_range``. Useful for downstream
        coverage / validity checks.
        """
        per_day = self._provider.get_universe_range(
            self._start, self._end, self._universe_name,
        )
        if not per_day:
            return pd.DataFrame()
        all_tickers = sorted({t for ts in per_day.values() for t in ts})
        all_dates = sorted(per_day.keys())
        mask = pd.DataFrame(False, index=all_dates, columns=all_tickers)
        for d, ts in per_day.items():
            if ts:
                mask.loc[d, ts] = True
        mask.index.name = "datetime"
        mask.columns.name = "instrument"
        return mask

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pivot_per_field(
        raw: pd.DataFrame,
        fields: Iterable[str],
    ) -> dict[str, pd.DataFrame]:
        """Convert PIT (instrument, datetime) MultiIndex output to a
        dict of date × ticker DataFrames per field."""
        out: dict[str, pd.DataFrame] = {}
        if raw.empty:
            for f in fields:
                out[f] = pd.DataFrame()
            return out
        # PIT guarantees (instrument, datetime) MultiIndex; assert for
        # diagnostic clarity if a future PIT change drifts the shape.
        if (
            not isinstance(raw.index, pd.MultiIndex)
            or set(raw.index.names) != {"instrument", "datetime"}
        ):
            raise ValueError(
                "PITDataProvider.get_features returned an unexpected index "
                f"shape (names={raw.index.names!r}); expected MultiIndex "
                "with names {'instrument', 'datetime'}"
            )
        for field in fields:
            if field not in raw.columns:
                raise KeyError(
                    f"field {field!r} missing from PITDataProvider output "
                    f"(returned columns: {list(raw.columns)})"
                )
            series = raw[field]
            df = series.unstack(level="instrument")
            df = df.sort_index().sort_index(axis=1)
            df.index.name = "datetime"
            df.columns.name = "instrument"
            out[field] = df
        return out


def panel_to_mapping(panel: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Identity helper — kept as a named symbol so downstream callers
    can write ``from src.factor_mining.pit_adapter import panel_to_mapping``
    without leaking internal types. Useful when wrapping a synthetic
    panel for tests."""
    return dict(panel)

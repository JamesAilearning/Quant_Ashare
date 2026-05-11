"""Provider bundle comparison — compare two qlib binary bundles."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.core.logger import get_logger
from src.data.tushare.provider_bundle._types import (
    TushareProviderComparisonReport,
)
from src.data.tushare.provider_bundle._utils import (
    _read_provider_calendar,
    _read_provider_feature,
    _read_provider_instruments,
)

_logger = get_logger(__name__)

def compare_provider_bundles(
    *,
    generated_provider_uri: str,
    baseline_provider_uri: str,
    max_price_instruments: int = 50,
) -> TushareProviderComparisonReport:
    """Create an informational comparison between two qlib provider dirs."""
    generated = Path(generated_provider_uri)
    baseline = Path(baseline_provider_uri)
    warnings: list[str] = []

    generated_instruments = _read_provider_instruments(generated)
    baseline_instruments = _read_provider_instruments(baseline)
    generated_calendar = _read_provider_calendar(generated)
    baseline_calendar = _read_provider_calendar(baseline)

    overlap_instruments = sorted(set(generated_instruments) & set(baseline_instruments))
    overlap_calendar = sorted(set(generated_calendar) & set(baseline_calendar))

    max_close_delta: float | None = None
    max_volume_delta: float | None = None
    close_points = 0
    volume_points = 0
    for instrument in overlap_instruments[:max_price_instruments]:
        for field in ("close", "volume"):
            try:
                generated_series = _read_provider_feature(generated, instrument, field, generated_calendar)
                baseline_series = _read_provider_feature(baseline, instrument, field, baseline_calendar)
            except OSError as exc:
                warnings.append(f"missing_{field}_feature:{instrument}:{exc}")
                continue
            joined = pd.concat(
                [generated_series.rename("generated"), baseline_series.rename("baseline")],
                axis=1,
                join="inner",
            ).dropna()
            if joined.empty:
                continue
            delta = (joined["generated"] - joined["baseline"]).abs()
            if field == "close":
                close_points += int(len(delta))
                value = float(delta.max())
                max_close_delta = value if max_close_delta is None else max(max_close_delta, value)
            else:
                volume_points += int(len(delta))
                value = float(delta.max())
                max_volume_delta = value if max_volume_delta is None else max(max_volume_delta, value)

    return TushareProviderComparisonReport(
        baseline_provider_uri=str(baseline),
        generated_provider_uri=str(generated),
        baseline_instrument_count=len(baseline_instruments),
        generated_instrument_count=len(generated_instruments),
        overlap_instrument_count=len(overlap_instruments),
        missing_from_generated=tuple(sorted(set(baseline_instruments) - set(generated_instruments))[:20]),
        new_in_generated=tuple(sorted(set(generated_instruments) - set(baseline_instruments))[:20]),
        baseline_calendar_count=len(baseline_calendar),
        generated_calendar_count=len(generated_calendar),
        overlap_calendar_count=len(overlap_calendar),
        compared_close_points=close_points,
        max_abs_close_delta=max_close_delta,
        compared_volume_points=volume_points,
        max_abs_volume_delta=max_volume_delta,
        warnings=tuple(warnings[:20]),
    )




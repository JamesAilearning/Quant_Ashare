"""Provider-bundle configuration dataclass with validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.data.tushare.client import TushareClient

from src.data.tushare.provider_bundle._types import (
    FORBIDDEN_CONFIG_KEYS,
    SOURCE_APIS,
    TushareQlibProviderBundleError,
    TushareQlibProviderManifest,
)

@dataclass(frozen=True)
class TushareQlibProviderBundleConfig:
    """Configuration for publishing an opt-in Tushare qlib provider bundle."""

    output_dir: str
    start_date: str
    end_date: str
    data_adjust_mode: str
    instruments: tuple[str, ...] = ("all",)
    staging_dir: str | None = None
    manifest_path: str | None = None
    validation_path: str | None = None
    comparison_path: str | None = None
    baseline_provider_uri: str | None = None
    benchmark_indexes: tuple[tuple[str, str], ...] = tuple()
    reuse_staged: bool = True
    region: str = "cn"
    freq: str = "day"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TushareQlibProviderBundleConfig":
        """Build config from a YAML/JSON mapping while rejecting secrets."""
        if not isinstance(raw, Mapping):
            raise TushareQlibProviderBundleError(
                f"Config must be a mapping, got {type(raw).__name__}."
            )

        forbidden = sorted(k for k in raw if str(k).lower() in FORBIDDEN_CONFIG_KEYS)
        if forbidden:
            raise TushareQlibProviderBundleError(
                "Tushare token fields are forbidden in config: "
                f"{forbidden}. Use the TUSHARE_TOKEN environment variable."
            )

        valid_fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = sorted(set(raw) - valid_fields)
        if unknown:
            raise TushareQlibProviderBundleError(
                f"Unknown Tushare provider config keys: {unknown}."
            )

        values = dict(raw)
        if "instruments" in values:
            values["instruments"] = _normalize_instrument_scope(values["instruments"])
        if "benchmark_indexes" in values:
            values["benchmark_indexes"] = _normalize_benchmark_indexes(
                values["benchmark_indexes"]
            )
        return cls(**values)

    def __post_init__(self) -> None:
        _require_non_empty_str(self.output_dir, "output_dir")
        _parse_iso_date(self.start_date, "start_date")
        _parse_iso_date(self.end_date, "end_date")
        if self.start_date > self.end_date:
            raise TushareQlibProviderBundleError(
                f"start_date ({self.start_date}) must be <= end_date ({self.end_date})."
            )
        if self.data_adjust_mode not in SUPPORTED_ADJUST_MODES:
            raise TushareQlibProviderBundleError(
                "Unsupported data_adjust_mode "
                f"{self.data_adjust_mode!r}. Allowed: {SUPPORTED_ADJUST_MODES}."
            )
        if not self.instruments:
            raise TushareQlibProviderBundleError("instruments must not be empty.")
        normalized_scope = _normalize_instrument_scope(self.instruments)
        object.__setattr__(self, "instruments", normalized_scope)
        object.__setattr__(
            self,
            "benchmark_indexes",
            _normalize_benchmark_indexes(self.benchmark_indexes),
        )
        if self.freq != "day":
            raise TushareQlibProviderBundleError(
                f"Only day frequency is supported in v1; got {self.freq!r}."
            )
        if self.region.strip().lower() != "cn":
            raise TushareQlibProviderBundleError(
                f"Only cn region is supported for A-share Tushare bundles; got {self.region!r}."
            )
        object.__setattr__(self, "region", "cn")

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def staging_path(self) -> Path:
        if self.staging_dir:
            return Path(self.staging_dir)
        return self.output_path.parent / f".{self.output_path.name}.staging"

    @property
    def manifest_file(self) -> Path:
        if self.manifest_path:
            return Path(self.manifest_path)
        return self.output_path / DEFAULT_MANIFEST_NAME

    @property
    def validation_file(self) -> Path:
        if self.validation_path:
            return Path(self.validation_path)
        return self.output_path / DEFAULT_VALIDATION_NAME

    @property
    def comparison_file(self) -> Path:
        if self.comparison_path:
            return Path(self.comparison_path)
        return self.output_path / DEFAULT_COMPARISON_NAME

    @property
    def requested_tushare_codes(self) -> tuple[str, ...] | None:
        if self.instruments == ("all",):
            return None
        converted = []
        for instrument in self.instruments:
            ts_code = _qlib_to_tushare_instrument(instrument)
            if ts_code is None:
                raise TushareQlibProviderBundleError(
                    f"Unsupported instrument code {instrument!r}; expected qlib "
                    "shape SH600000/SZ000001/BJ430047 or Tushare shape 600000.SH."
                )
            converted.append(ts_code)
        return tuple(sorted(set(converted)))


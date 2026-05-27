"""Internal helper functions — normalizers, parsers, cache, file I/O."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from src.core.logger import get_logger
from src.data.tushare.industry_publisher import _tushare_to_qlib_instrument
from src.data.tushare.provider_bundle._types import (
    INDEX_SOURCE_API,
    SOURCE_APIS,
    STAGED_CACHE_METADATA_SUFFIX,
    STAGED_CACHE_METADATA_VERSION,
    TushareQlibProviderBundleError,
)

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from src.data.tushare.provider_bundle.config import TushareQlibProviderBundleConfig


def _normalize_instrument_scope(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("all",)
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, Iterable):
        parts = [str(p).strip() for p in value if str(p).strip()]
    else:
        raise TushareQlibProviderBundleError(
            f"instruments must be 'all', a comma-separated string, or a list; got {type(value).__name__}."
        )
    if not parts:
        raise TushareQlibProviderBundleError("instruments must not be empty.")
    if any(p.lower() == "all" for p in parts):
        if len(parts) > 1:
            raise TushareQlibProviderBundleError(
                "instruments='all' cannot be combined with explicit symbols."
            )
        return ("all",)
    normalized: list[str] = []
    for part in parts:
        qlib_code = _tushare_to_qlib_instrument(part.upper()) if "." in part else part.upper()
        if qlib_code is None:
            qlib_code = part.upper()
        normalized.append(qlib_code)
    return tuple(sorted(set(normalized)))


def _normalize_benchmark_indexes(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return tuple()
    if isinstance(value, Mapping):
        raw_items = list(value.items())
    elif isinstance(value, str):
        raise TushareQlibProviderBundleError(
            "benchmark_indexes must be a mapping or list of qlib/Tushare code pairs."
        )
    elif isinstance(value, Iterable):
        raw_items = []
        for item in value:
            if isinstance(item, Mapping):
                qlib_code = item.get("qlib_code") or item.get("qlib")
                ts_code = item.get("tushare_code") or item.get("ts_code")
                raw_items.append((qlib_code, ts_code))
            elif isinstance(item, Sequence) and not isinstance(item, str) and len(item) == 2:
                raw_items.append((item[0], item[1]))
            else:
                raise TushareQlibProviderBundleError(
                    "benchmark_indexes entries must be mappings or two-item pairs."
                )
    else:
        raise TushareQlibProviderBundleError(
            f"benchmark_indexes must be a mapping or list; got {type(value).__name__}."
        )

    normalized: list[tuple[str, str]] = []
    seen_qlib: set[str] = set()
    for raw_qlib_code, raw_ts_code in raw_items:
        qlib_code = _normalize_qlib_index_code(raw_qlib_code)
        ts_code = _normalize_tushare_index_code(raw_ts_code)
        if qlib_code is None or ts_code is None:
            raise TushareQlibProviderBundleError(
                "benchmark_indexes must map qlib index codes like SH000300 "
                "to Tushare index codes like 000300.SH."
            )
        expected_qlib_code = _tushare_to_qlib_instrument(ts_code)
        if expected_qlib_code != qlib_code:
            raise TushareQlibProviderBundleError(
                f"benchmark index mapping mismatch: {qlib_code!r} does not "
                f"match Tushare code {ts_code!r}."
            )
        if qlib_code in seen_qlib:
            raise TushareQlibProviderBundleError(
                f"Duplicate benchmark index qlib code {qlib_code!r}."
            )
        seen_qlib.add(qlib_code)
        normalized.append((qlib_code, ts_code))
    return tuple(sorted(normalized))


def _normalize_qlib_index_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if "." in text:
        return _tushare_to_qlib_instrument(text)
    if len(text) != 8:
        return None
    suffix, code = text[:2], text[2:]
    if suffix not in ("SH", "SZ") or not code.isdigit():
        return None
    return text


def _normalize_tushare_index_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if _tushare_to_qlib_instrument(text) is None:
        return None
    return text


def _qlib_to_tushare_instrument(instrument: str) -> str | None:
    text = str(instrument).strip().upper()
    if "." in text:
        return text if _tushare_to_qlib_instrument(text) is not None else None
    if len(text) != 8:
        return None
    suffix, code = text[:2], text[2:]
    if suffix not in ("SH", "SZ", "BJ") or not code.isdigit():
        return None
    return f"{code}.{suffix}"


def _source_apis_for_config(config: TushareQlibProviderBundleConfig) -> tuple[str, ...]:
    if config.benchmark_indexes:
        return SOURCE_APIS + (INDEX_SOURCE_API,)
    return SOURCE_APIS


def _require_non_empty_str(value: Any, field_name: str) -> None:
    if not str(value or "").strip():
        raise TushareQlibProviderBundleError(f"{field_name} is required.")


def _parse_iso_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise TushareQlibProviderBundleError(
            f"{field_name} must be ISO date YYYY-MM-DD; got {value!r}."
        ) from exc


def _to_tushare_date(value: str) -> str:
    return _parse_iso_date(value, "date").strftime("%Y%m%d")


def _parse_tushare_date_series(series: pd.Series, field_name: str, errors: list[str]) -> pd.Series:
    parsed = pd.to_datetime(series.astype(str), format="%Y%m%d", errors="coerce")
    if parsed.isna().any():
        errors.append(f"unparseable_date:{field_name}")
    return parsed


def _ensure_frame(result: Any, api_name: str) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result.copy()
    try:
        return pd.DataFrame(result)
    except Exception as exc:
        raise TushareQlibProviderBundleError(
            f"Tushare API {api_name!r} did not return DataFrame-like data."
        ) from exc


def _write_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8")


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _staged_cache_metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{STAGED_CACHE_METADATA_SUFFIX}")


def _stable_cache_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): _stable_cache_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_cache_value(v) for v in value]
    return value


def _staged_cache_signature(api_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": STAGED_CACHE_METADATA_VERSION,
        "api_name": str(api_name),
        "params": {
            str(k): _stable_cache_value(v)
            for k, v in sorted(params.items(), key=lambda item: str(item[0]))
        },
    }


def _write_staged_cache_metadata(
    path: Path,
    *,
    api_name: str,
    params: Mapping[str, Any],
) -> None:
    _write_json(
        _staged_cache_metadata_path(path),
        _staged_cache_signature(api_name, params),
    )


def _staged_cache_metadata_matches(
    path: Path,
    *,
    api_name: str,
    params: Mapping[str, Any],
) -> bool:
    if not path.exists():
        return False
    metadata_path = _staged_cache_metadata_path(path)
    if not metadata_path.exists():
        return False
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(cached == _staged_cache_signature(api_name, params))


def _concat_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [f for f in frames if f is not None and not f.empty]
    if not non_empty:
        for frame in frames:
            if frame is not None:
                return frame.copy().iloc[0:0]
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True, sort=False)


def _filter_tushare_codes(frame: pd.DataFrame, requested_codes: tuple[str, ...] | None) -> pd.DataFrame:
    if requested_codes is None or frame.empty or "ts_code" not in frame.columns:
        return frame
    return frame[frame["ts_code"].astype(str).str.upper().isin(set(requested_codes))].copy()


def _extract_open_trade_dates(trade_calendar: pd.DataFrame) -> tuple[str, ...]:
    if trade_calendar.empty or "cal_date" not in trade_calendar.columns:
        return tuple()
    frame = trade_calendar.copy()
    if "is_open" in frame.columns:
        frame = frame[frame["is_open"].astype(str).str.strip().isin(("1", "1.0", "True", "true"))]
    return tuple(sorted(frame["cal_date"].astype(str).str.replace("-", "", regex=False).unique()))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _copy_file(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _temporary_publish_dir(final_dir: Path) -> Path:
    return final_dir.parent / f".{final_dir.name}.publishing"


def _replace_directory_atomically(temp_dir: Path, final_dir: Path) -> None:
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = final_dir.parent / f".{final_dir.name}.previous"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if final_dir.exists():
        final_dir.rename(backup_dir)
    try:
        temp_dir.rename(final_dir)
    except Exception:
        if backup_dir.exists() and not final_dir.exists():
            backup_dir.rename(final_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def _get_tushare_version() -> str | None:
    try:
        import tushare as ts
    except ImportError:
        return None
    return str(getattr(ts, "__version__", "unknown"))


def _read_provider_calendar(provider_dir: Path) -> tuple[str, ...]:
    path = provider_dir / "calendars" / "day.txt"
    if not path.exists():
        return tuple()
    return tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _read_provider_instruments(provider_dir: Path) -> tuple[str, ...]:
    path = provider_dir / "instruments" / "all.txt"
    if not path.exists():
        return tuple()
    instruments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        instruments.append(line.split("\t")[0].strip().upper())
    return tuple(sorted(set(instruments)))


def _read_provider_feature(
    provider_dir: Path,
    instrument: str,
    field: str,
    calendar: Sequence[str],
) -> pd.Series:
    path = provider_dir / "features" / instrument.lower() / f"{field}.day.bin"
    payload = np.fromfile(path, dtype="<f4")
    if payload.size == 0:
        return pd.Series(dtype="float32")
    start_idx = int(payload[0])
    values = payload[1:]
    dates = list(calendar[start_idx: start_idx + len(values)])
    return pd.Series(values, index=pd.Index(dates, name="date"))


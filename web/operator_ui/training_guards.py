"""UI-only guards for training launch inputs.

These helpers read provider metadata and files from disk. They deliberately do
not initialize qlib, call Tushare, or compute official metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.data._segment_embargo import (  # noqa: E402
    LABEL_LOOKAHEAD_DAYS,
    trading_days_between,
)

FORWARD_RETURN_BUFFER_DAYS = 20

# ``LABEL_LOOKAHEAD_DAYS`` is sourced from ``src/data/_segment_embargo``
# and re-exported above so the operator UI and the core
# ``FeatureDatasetBuilder._validate`` cannot drift apart. Callers that
# imported the constant from this module continue to work.

# Heuristic for B2: when the strategy universe is much wider than what the
# benchmark covers, the resulting "excess return vs benchmark" is partly
# driven by the universe mismatch (small-caps, STAR / BJ stocks). We warn on
# the common pairings below; operators who deliberately accept the mismatch
# can ignore the warning.
_BENCHMARK_UNIVERSE_HINTS: dict[str, str] = {
    "SH000300": "csi300",  # 沪深 300
    "SH000905": "csi500",  # 中证 500
    "SH000852": "csi1000",  # 中证 1000
    "SH000906": "csi800",  # 中证 800
}


@dataclass(frozen=True)
class ProviderMetadata:
    provider_uri: str
    provider_path: Path | None
    metadata_root: Path | None
    validation_path: Path | None
    manifest_path: Path | None
    coverage_start_date: date | None
    coverage_end_date: date | None
    calendar_dates: tuple[date, ...]
    instrument_universes: tuple[str, ...]
    health: str | None
    row_count: int | None
    instrument_count: int | None
    calendar_count: int | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrainingGuardResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    provider_metadata: ProviderMetadata

    @property
    def ok(self) -> bool:
        return not self.errors


def inspect_provider_metadata(provider_uri: str) -> ProviderMetadata:
    raw_uri = str(provider_uri or "").strip()
    if not raw_uri:
        return ProviderMetadata(
            provider_uri=raw_uri,
            provider_path=None,
            metadata_root=None,
            validation_path=None,
            manifest_path=None,
            coverage_start_date=None,
            coverage_end_date=None,
            calendar_dates=(),
            instrument_universes=(),
            health=None,
            row_count=None,
            instrument_count=None,
            calendar_count=None,
            errors=(),
            warnings=("provider_uri is empty.",),
        )

    provider_path = Path(raw_uri)
    errors: list[str] = []
    warnings: list[str] = []
    if not provider_path.exists():
        errors.append(f"provider_uri does not exist: {provider_path}")
        return ProviderMetadata(
            provider_uri=raw_uri,
            provider_path=provider_path,
            metadata_root=None,
            validation_path=None,
            manifest_path=None,
            coverage_start_date=None,
            coverage_end_date=None,
            calendar_dates=(),
            instrument_universes=(),
            health=None,
            row_count=None,
            instrument_count=None,
            calendar_count=None,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
    if not provider_path.is_dir():
        errors.append(f"provider_uri must be a directory: {provider_path}")

    metadata_root = _metadata_root(provider_path)
    validation_path = _first_existing(
        metadata_root / "validation.json",
        provider_path / "validation.json",
    )
    manifest_path = _first_existing(
        metadata_root / "manifest.json",
        provider_path / "manifest.json",
    )
    validation = _read_json(validation_path)
    manifest = _read_json(manifest_path)
    source = validation or manifest

    coverage_start = _parse_optional_date(source.get("coverage_start_date"))
    coverage_end = _parse_optional_date(source.get("coverage_end_date"))
    calendar_dates = _read_calendar_dates(provider_path / "calendars" / "day.txt")
    if calendar_dates:
        coverage_start = coverage_start or calendar_dates[0]
        coverage_end = coverage_end or calendar_dates[-1]

    instrument_universes = _read_instrument_universes(provider_path)
    if not validation and not manifest:
        warnings.append(
            "未找到相邻的 validation.json 或 manifest.json，数据源覆盖预览功能受限。"
        )
    if not calendar_dates:
        warnings.append(
            "未找到 calendars/day.txt，数据源末日校验不可用。"
        )
    if not instrument_universes:
        warnings.append(
            "未找到 instruments/*.txt，命名标的池校验不可用。"
        )

    health = _optional_str(source.get("health") or source.get("validation_health"))
    return ProviderMetadata(
        provider_uri=raw_uri,
        provider_path=provider_path,
        metadata_root=metadata_root,
        validation_path=validation_path,
        manifest_path=manifest_path,
        coverage_start_date=coverage_start,
        coverage_end_date=coverage_end,
        calendar_dates=calendar_dates,
        instrument_universes=instrument_universes,
        health=health,
        row_count=_optional_int(source.get("row_count")),
        instrument_count=_optional_int(source.get("instrument_count")),
        calendar_count=_optional_int(source.get("calendar_count")) or len(calendar_dates) or None,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_pipeline_training_inputs(
    *,
    provider_uri: str,
    instruments: str,
    train_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    test_start: str,
    test_end: str,
    benchmark_code: str = "",
) -> TrainingGuardResult:
    metadata = inspect_provider_metadata(provider_uri)
    errors: list[str] = list(metadata.errors)
    warnings: list[str] = list(metadata.warnings)

    parsed = {
        "train_start": _parse_required_date("train_start", train_start, errors),
        "train_end": _parse_required_date("train_end", train_end, errors),
        "valid_start": _parse_required_date("valid_start", valid_start, errors),
        "valid_end": _parse_required_date("valid_end", valid_end, errors),
        "test_start": _parse_required_date("test_start", test_start, errors),
        "test_end": _parse_required_date("test_end", test_end, errors),
    }

    if all(parsed.values()):
        if not parsed["train_start"] < parsed["train_end"]:
            errors.append("train_start 必须严格早于 train_end。")
        if not parsed["train_end"] < parsed["valid_start"]:
            errors.append(
                "valid_start 必须严格晚于 train_end "
                f"（train_end={train_end}, valid_start={valid_start}）。"
            )
        if not parsed["valid_start"] < parsed["valid_end"]:
            errors.append("valid_start 必须严格早于 valid_end。")
        if not parsed["valid_end"] < parsed["test_start"]:
            errors.append(
                "test_start 必须严格晚于 valid_end "
                f"（valid_end={valid_end}, test_start={test_start}）。"
            )
        if not parsed["test_start"] < parsed["test_end"]:
            errors.append("test_start 必须严格早于 test_end。")

        _validate_provider_coverage(parsed, metadata, errors, warnings)
        _validate_segment_embargo(parsed, metadata, errors)

    _validate_instruments(instruments, metadata, errors)
    _validate_universe_benchmark_alignment(instruments, benchmark_code, warnings)

    return TrainingGuardResult(
        errors=tuple(errors),
        warnings=tuple(warnings),
        provider_metadata=metadata,
    )


def _validate_segment_embargo(
    parsed: dict[str, date | None],
    metadata: ProviderMetadata,
    errors: list[str],
) -> None:
    """Surface Alpha158 label-lookahead embargo violations in Chinese.

    Delegates the gap arithmetic to the shared validator in
    ``src.data._segment_embargo`` so this UI guard and the core
    ``FeatureDatasetBuilder._validate`` cannot drift apart. This
    wrapper exists to (a) skip when the UI's calendar source is empty,
    (b) tolerate the optional-date inputs the UI form may surface, and
    (c) render the error messages in Chinese for the Chinese-only UI.
    """

    calendar = metadata.calendar_dates
    if not calendar:
        # Without a calendar we can't reason about trading-day distances;
        # the coverage check already warns about this case.
        return

    train_end = parsed.get("train_end")
    valid_start = parsed.get("valid_start")
    valid_end = parsed.get("valid_end")
    test_start = parsed.get("test_start")
    pairs = (
        ("train_end", train_end, "valid_start", valid_start),
        ("valid_end", valid_end, "test_start", test_start),
    )
    for e_name, e_date, l_name, l_date in pairs:
        if e_date is None or l_date is None or l_date <= e_date:
            # Other validators already flag missing / non-monotone dates.
            continue
        gap = trading_days_between(e_date, l_date, calendar)
        if gap < LABEL_LOOKAHEAD_DAYS:
            errors.append(
                f"{e_name}（{e_date}）与 {l_name}（{l_date}）之间只有 "
                f"{gap} 个交易日，少于 Alpha158 默认 label 所需的 "
                f"{LABEL_LOOKAHEAD_DAYS} 个交易日 embargo——"
                f"前一段的尾部 label 会用到后一段的收盘价，"
                "造成验证集/测试集的 label 泄漏。请将后一段起始日往后推 "
                f"至少 {LABEL_LOOKAHEAD_DAYS} 个交易日。"
            )


def _validate_universe_benchmark_alignment(
    instruments: str,
    benchmark_code: str,
    warnings: list[str],
) -> None:
    """Warn (not error) when the strategy universe doesn't match the
    benchmark constituents.

    Common pitfall: operator picks ``instruments=all`` to fish from the
    full market (incl. STAR / Beijing / micro-caps) but keeps the default
    ``benchmark_code=SH000300`` — so the "excess return vs benchmark" is
    inflated by the universe mismatch rather than reflecting model skill.

    Heuristic only — we cannot know whether a custom universe matches a
    custom benchmark. We warn on the obvious cases (``instruments=all``
    against a major index) and let the operator override.
    """

    instr = str(instruments or "").strip().lower()
    bench = str(benchmark_code or "").strip().upper()
    if not instr or not bench:
        return
    expected_universe = _BENCHMARK_UNIVERSE_HINTS.get(bench)
    if expected_universe is None:
        return
    if instr == expected_universe:
        return
    if instr == "all":
        warnings.append(
            f"股票池 instruments=all（全市场，含科创/北交/小盘）与 "
            f"基准 benchmark_code={bench}（{expected_universe.upper()}）不一致；"
            "「相对基准超额收益」会被股票池差异天然抬高。"
            f"若要比较同口径，请把 instruments 改成 {expected_universe}，"
            "或换一个与策略池匹配的基准。"
        )
    elif instr in _BENCHMARK_UNIVERSE_HINTS.values():
        warnings.append(
            f"股票池 instruments={instr} 与 基准 benchmark_code={bench}"
            f"（对应 {expected_universe.upper()}）不同口径，"
            "相对超额收益可能因股票池范围差异而非模型能力而被放大。"
        )


def provider_metadata_summary(metadata: ProviderMetadata) -> dict[str, str]:
    return {
        "coverage": _format_coverage(metadata),
        "health": metadata.health or "暂无数据",
        "calendar_count": str(metadata.calendar_count or "暂无数据"),
        "instrument_count": str(metadata.instrument_count or "暂无数据"),
        "row_count": str(metadata.row_count or "暂无数据"),
        "universes": ", ".join(metadata.instrument_universes) or "暂无数据",
    }


def _validate_provider_coverage(
    parsed: dict[str, date | None],
    metadata: ProviderMetadata,
    errors: list[str],
    warnings: list[str],
) -> None:
    coverage_start = metadata.coverage_start_date
    coverage_end = metadata.coverage_end_date
    train_start = parsed["train_start"]
    test_end = parsed["test_end"]
    if coverage_start and train_start and train_start < coverage_start:
        errors.append(
            f"train_start（{train_start}）早于数据源 coverage_start "
            f"（{coverage_start}）。"
        )
    if coverage_end:
        for name, value in parsed.items():
            if value and value > coverage_end:
                errors.append(
                    f"{name}（{value}）晚于数据源 coverage_end（{coverage_end}）。"
                )

    calendar = metadata.calendar_dates
    if not calendar or test_end is None:
        return
    calendar_end = calendar[-1]
    if test_end >= calendar_end:
        safe_end = calendar[-2] if len(calendar) >= 2 else None
        suggestion = f" 请将 test_end 设为 ≤ {safe_end}，或拉取更多数据。" if safe_end else ""
        errors.append(
            f"test_end（{test_end}）必须早于数据源最后一个交易日"
            f"（{calendar_end}），否则会触发 qlib 回测日历越界。{suggestion}"
        )
        return

    future_days = sum(1 for day in calendar if day > test_end)
    if future_days < FORWARD_RETURN_BUFFER_DAYS:
        warnings.append(
            f"test_end（{test_end}）之后只剩 {future_days} 个交易日；"
            "末尾的 20 日前向收益信号摘要可能不完整。"
            "请拉取更多数据或把 test_end 往前移以保证尾部完整。"
        )


def _validate_instruments(
    instruments: str,
    metadata: ProviderMetadata,
    errors: list[str],
) -> None:
    universes = set(metadata.instrument_universes)
    if not universes:
        return
    instrument_name = str(instruments or "").strip()
    if not instrument_name:
        errors.append("instruments 不能为空。")
        return
    if "," in instrument_name:
        return
    if instrument_name not in universes:
        errors.append(
            f"instruments={instrument_name!r} 不在数据源的 instruments/*.txt 中。"
            f"可选标的池：{sorted(universes)}。"
        )


def _metadata_root(provider_path: Path) -> Path:
    if provider_path.name == "qlib_provider":
        return provider_path.parent
    return provider_path


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def _read_calendar_dates(path: Path) -> tuple[date, ...]:
    if not path.is_file():
        return ()
    dates: list[date] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value:
                parsed = _parse_optional_date(value)
                if parsed is not None:
                    dates.append(parsed)
    except OSError:
        return ()
    return tuple(sorted(set(dates)))


def _read_instrument_universes(provider_path: Path) -> tuple[str, ...]:
    instruments_dir = provider_path / "instruments"
    if not instruments_dir.is_dir():
        return ()
    try:
        return tuple(sorted(path.stem for path in instruments_dir.glob("*.txt")))
    except OSError:
        return ()


def _parse_required_date(name: str, raw: str, errors: list[str]) -> date | None:
    parsed = _parse_optional_date(raw)
    if parsed is None:
        errors.append(f"{name} 必须是 YYYY-MM-DD 格式的 ISO 日期；当前为 {raw!r}。")
    return parsed


def _parse_optional_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if len(text) != 10:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    if parsed.isoformat() != text:
        return None
    return parsed


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _format_coverage(metadata: ProviderMetadata) -> str:
    if metadata.coverage_start_date and metadata.coverage_end_date:
        return f"{metadata.coverage_start_date} to {metadata.coverage_end_date}"
    return "unavailable"

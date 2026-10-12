"""Small configured-calendar identity reader; never follows artifact paths."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web.operator_ui.incumbent import anchored_to_repo, unusable_path_reason

MAX_CALENDAR_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TradingCalendarIdentity:
    path: str | None
    sha256: str | None
    problem: str = ""


def _path_key(spelling: str) -> str:
    if not spelling.strip() or unusable_path_reason(spelling) is not None:
        raise ValueError("交易日安排路径不可用")
    # Lexical only: artifact-supplied paths are compared, never opened/resolved.
    return os.path.normcase(os.path.abspath(anchored_to_repo(spelling)))


def _ordinary_calendar_file(info: os.stat_result) -> None:
    # Match the producer's file-shape boundary, including descriptor and final
    # path observations: matching bytes do not make a linked source usable.
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not 0 < info.st_size <= MAX_CALENDAR_BYTES):
        raise ValueError("交易日安排必须是单链接、非重解析点、≤1 MiB 的非空普通文件")


def read_trading_calendar_identity(configured_path: str) -> TradingCalendarIdentity:
    """Read only the deployment-selected bounded regular file, once per render."""
    try:
        key = _path_key(configured_path)
        path = Path(key)
        before = path.lstat()
        _ordinary_calendar_file(before)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            _ordinary_calendar_file(opened)
            raw = stream.read(MAX_CALENDAR_BYTES + 1)
            finished = os.fstat(stream.fileno())
            _ordinary_calendar_file(finished)
        after = path.lstat()
        _ordinary_calendar_file(after)
        pins = [(x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns) for x in (before, opened, finished, after)]
        if len(raw) != before.st_size or len(raw) > MAX_CALENDAR_BYTES or any(pin != pins[0] for pin in pins[1:]):
            raise ValueError("交易日安排在读取时变化或超出大小限制")
        return TradingCalendarIdentity(key, hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError) as exc:
        return TradingCalendarIdentity(None, None, f"交易日安排身份无法核验：{type(exc).__name__}")


def trading_calendar_matches(payload: Any, current: TradingCalendarIdentity | None) -> bool:
    """Compare existing bindings without opening any payload-provided path."""
    if current is None or current.problem or current.path is None or current.sha256 is None:
        return False
    meta = payload.get("meta") if isinstance(payload, dict) else None
    context = meta.get("trading_calendar") if isinstance(meta, dict) else None
    if not isinstance(context, dict) or not isinstance(context.get("path"), str):
        return False
    try:
        return _path_key(context["path"]) == current.path and context.get("sha256") == current.sha256
    except ValueError:
        return False

"""Append-only JSONL catalog of every pipeline / walk-forward run.

Each successful or partial run appends one JSON line to
``output/runs/_index.jsonl`` so operators can query historical runs
without resorting to ``find`` + ``jq``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core._json_utils import _sanitize_for_json
from src.core.logger import get_logger

_logger = get_logger(__name__)

#: 仓库根。与 ``git_provenance`` 同惯例。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 默认索引位置,**锚在仓库根**而不是进程 CWD。
#:
#: 相对路径意味着「同一份代码从不同目录启动就写到不同文件」——pytest 从仓库
#: 根跑,于是每一次触发引擎的测试都往操作人的真实索引追加一行(实测该文件
#: 3560 行里 3455 行、97.1% 是这么来的)。这与 #444 修过的
#: ``run_dir_is_inspectable`` 锚定问题是同一类。
_DEFAULT_CATALOG_PATH = _REPO_ROOT / "output" / "runs" / "_index.jsonl"


#: 默认索引所索引的那棵 output 树。**不从索引路径反推**——
#: `<tree>/runs/<file>` 这种布局约定一旦被当成判据,任意 `catalog_path` 都会
#: 悄悄改变边界:`/tmp/catalog.jsonl` 推出 `/`(于是接受一切绝对路径),
#: `<repo>/output/custom.jsonl` 推出 `<repo>`(于是接受 output 树外的运行)。
#: 把文件摆放位置变成隐藏的安全契约是坏设计(codex #453)。
_DEFAULT_OUTPUT_TREE = _REPO_ROOT / "output"


def _is_inside(child: Path, root: Path) -> bool:
    """``child`` 是否落在 ``root`` 内。

    **两侧都 resolve**。只归一化词法(normpath/normcase)挡不住同一目录的第三种
    拼写:符号链接、Windows 联接、以及 8.3 短名——GitHub 的 Windows runner 把
    TEMP 设成 `C:/Users/RUNNER~1/...`,于是索引解析成长名而 `output_dir` 保持
    短名,合法运行被误拒(codex #453,本仓 CI 实测三个 Windows 位全红)。
    #444 在控制台读边界上栽过同一个坑,这里不再重犯。

    这里 resolve 的代价可接受:每次**运行**才走一次,不是逐行热路径(控制台那
    条判据是逐行的,所以它才必须纯词法)。
    """
    try:
        child_resolved = child.resolve()
        root_resolved = root.resolve()
    except OSError:                       # pragma: no cover - 路径异常
        return False
    try:
        Path(os.path.normcase(str(child_resolved))).relative_to(
            Path(os.path.normcase(str(root_resolved)))
        )
    except ValueError:
        return False
    return True


def catalog_boundary_verdict(
    output_dir: str, *, tree: Path,
) -> str | None:
    """``None`` = 可以编目;否则返回拒绝原因。

    与清理脚本**共用这一个判据**——两处各写一份正是它们会分叉的方式
    (codex #453 明确点了重复实现这一点)。
    """
    text = str(output_dir or "").strip()
    if not text:
        return "output_dir is missing"
    target = Path(text)
    if not target.is_absolute():
        target = tree.parent / target
    if not _is_inside(target, tree):
        return f"output_dir is not inside the output tree ({tree})"
    return None


def append_run_record(
    record: dict[str, Any],
    *,
    catalog_path: Path | None = None,
) -> None:
    """Append a single JSON line to the run catalog.

    Thread-safe on POSIX (O_APPEND + single write ≤ PIPE_BUF). On
    Windows the single ``json.dumps`` + ``os.write`` is also safe in
    practice because CPython holds the GIL during the write; for
    multi-process safety use a file lock or a dedicated writer process.
    """
    dest = catalog_path or _DEFAULT_CATALOG_PATH

    # 边界**只管默认那份共享索引**。显式传 catalog_path 本身就是「我知道我在
    # 做什么、就要记到这里」的刻意行为(也是文档里给树外运行留的逃生口),不该
    # 被二次猜测;而从任意索引路径反推它的 output 树,只会把文件摆放位置变成
    # 隐藏契约(codex #453)。
    #
    # 污染仍被堵住:测试从不传 catalog_path,它们走的正是这条默认路径。
    if catalog_path is None:
        reason = catalog_boundary_verdict(
            str(record.get("output_dir") or ""), tree=_DEFAULT_OUTPUT_TREE)
        if reason is not None:
            _logger.warning(
                "Run catalog append SKIPPED: %s. A run whose artifacts live "
                "outside the tree can never be opened from the console, so "
                "cataloguing it would only produce a row that is always set "
                "aside. Pass an explicit catalog_path to index such a run "
                "deliberately.", reason,
            )
            return

    dest.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(_sanitize_for_json(record), ensure_ascii=False,
                      sort_keys=True, default=str, allow_nan=False) + "\n"

    try:
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        _logger.warning(
            "Run catalog append failed (path=%s) — run results are "
            "still intact in the per-run directory.", dest,
        )


def build_record(
    *,
    engine: str,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    config_fingerprint: str = "",
    config_summary: dict[str, Any] | None = None,
    headline_metrics: dict[str, Any] | None = None,
    report_path: str | None = None,
    output_dir: str = "",
    metric_status: str = "official",
    metrics_purpose: str = "official",
) -> dict[str, Any]:
    """Build a run-catalog record with consistent schema.

    ``metric_status`` / ``metrics_purpose`` (codex #406 r3): the
    catalog is what run comparison reads, so a run whose numbers did
    NOT pass the RAISE risk-constraint validation must say so here —
    otherwise it sits next to official runs as an ordinary record.
    """
    return {
        "run_id": _build_run_id(engine, completed_at, config_fingerprint),
        "engine": engine,
        "started_at": started_at,
        "completed_at": completed_at or datetime.now(tz=timezone.utc).isoformat(),
        "status": status,
        "config_fingerprint": config_fingerprint,
        "config_summary": config_summary or {},
        "headline_metrics": headline_metrics or {},
        "report_path": report_path,
        "output_dir": output_dir,
        "metric_status": metric_status,
        "metrics_purpose": metrics_purpose,
    }


def _build_run_id(engine: str, completed_at: str | None,
                  fingerprint: str) -> str:
    ts = (completed_at or datetime.now(tz=timezone.utc).isoformat())[:19]
    fp = fingerprint[:12] if fingerprint else "no_fingerprint"
    return f"{engine}-{ts}-{fp}".replace(":", "-")

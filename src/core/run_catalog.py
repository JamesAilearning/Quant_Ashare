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


def _catalog_output_tree(catalog_path: Path) -> Path:
    """索引所索引的那棵 output 树。

    布局约定是 ``<tree>/runs/_index.jsonl``,所以树就是 ``parent.parent``。
    判据锚在**索引自己**而不是硬编码仓库根:别的 worktree 有自己的 output
    树,它们的运行该进它们自己的索引。
    """
    return catalog_path.resolve().parent.parent


def _is_inside(child: Path, root: Path) -> bool:
    """``child`` 是否落在 ``root`` 内(纯词法,不碰盘)。"""
    try:
        norm_child = Path(os.path.normcase(os.path.normpath(str(child))))
        norm_root = Path(os.path.normcase(os.path.normpath(str(root))))
        norm_child.relative_to(norm_root)
    except ValueError:
        return False
    return True


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

    # 产物落在 output 树外的运行,操作人**永远打不开**(那是控制台钉死的读
    # 边界),却照样进索引 —— 于是索引里 97.1% 是必然被搁置的残骸。写入侧
    # 在这里挡住:不是这棵树的运行,就不写这棵树的索引。
    #
    # 跳过而非抛错,与本函数既有契约一致(下面 OSError 也是 warning + 继续,
    # 「产物仍在运行目录里」)——编目是旁路记录,不是运行的产物。确实需要为
    # 树外运行编目的调用方,显式传 ``catalog_path``。
    output_dir = str(record.get("output_dir") or "").strip()
    tree = _catalog_output_tree(dest)
    resolved_output = Path(output_dir) if output_dir else None
    if resolved_output is not None and not resolved_output.is_absolute():
        resolved_output = tree.parent / resolved_output
    if resolved_output is None or not _is_inside(resolved_output, tree):
        _logger.warning(
            "Run catalog append SKIPPED: output_dir=%r is not inside this "
            "catalog's output tree (%s). A run whose artifacts live outside "
            "the tree can never be opened from the console, so cataloguing "
            "it would only produce a row that is always set aside. Pass an "
            "explicit catalog_path to index such a run deliberately.",
            output_dir or "(missing)", tree,
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

"""Append-only JSONL catalog of every pipeline / walk-forward run.

Each successful or partial run appends one JSON line to
``output/runs/_index.jsonl`` so operators can query historical runs
without resorting to ``find`` + ``jq``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


def anchor_output_dir(output_dir: str, *, relative_base: Path) -> Path | None:
    """把记录里的 ``output_dir`` 变成绝对路径;空值返回 ``None``。

    ``relative_base`` **必须由调用方点名**,因为相对路径该按谁解析,取决于
    调用方站在哪个位置:

    - **写入侧**站在生产者进程里,产物就在生产者的 CWD 下
      (`WalkForwardConfig.output_dir` 缺省是相对的 `"output/walk_forward"`,
      索引里 105 条合法行有 101 条是相对路径)。按仓库根去解析,会把一次在
      `/tmp` 里启动的运行当成 `<repo>/output/...` 而放行——索引照旧被污染,
      控制台还会指向毫不相干的仓库产物(codex #453)。
    - **清理脚本**是事后另起的进程,拿不到历史行当年的 CWD,只能沿用控制台
      读侧那条约定(相对 = 相对仓库根)。它的判据本来就是「控制台永远打不开的
      行」,与控制台同约定才自洽。
    """
    text = str(output_dir or "").strip()
    if not text:
        return None
    target = Path(text)
    if not target.is_absolute():
        target = relative_base / target
    return Path(os.path.normpath(str(target)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def catalog_boundary_verdict(
    output_dir: str, *, tree: Path, relative_base: Path,
) -> str | None:
    """``None`` = 可以编目;否则返回拒绝原因。

    与清理脚本**共用这一个判据**——两处各写一份正是它们会分叉的方式
    (codex #453 明确点了重复实现这一点)。
    """
    target = anchor_output_dir(output_dir, relative_base=relative_base)
    if target is None:
        return "output_dir is missing"
    if not _is_inside(target, tree):
        return f"output_dir is not inside the output tree ({tree})"
    return None


#: 写入侧等锁的上限。等不到就**照样写**并告警——一次运行的旁路记账不该把运行
#: 本身卡住。清理脚本的临界区只有毫秒级,正常情况下永远等不到这个超时。
_WRITER_LOCK_TIMEOUT = 5.0
_LOCK_POLL_SECONDS = 0.05


def _try_lock(fd: int) -> bool:
    """非阻塞地尝试拿排他锁。

    用 OS 级劝告锁(POSIX ``flock`` / Windows ``msvcrt.locking``):进程崩溃时
    内核自动释放,于是不需要「陈旧锁文件怎么办」那一整套超时启发式——那正是
    角落穷举的开端。
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:                       # pragma: no cover - 释放失败
        pass


@contextmanager
def catalog_lock(catalog: Path, *, timeout: float) -> Iterator[bool]:
    """索引的跨进程互斥。``yield`` 出「是否真的拿到了」。

    没有它,清理脚本读完与写回之间被追加的那一行会**永久丢失**:它既不在保留
    集里,也不在旁车留证里(codex #453)。原子替换关不上这个窗口——原子的是
    「换文件」这一步,不是「读—改—写」这整段。
    """
    lock_path = catalog.with_name(catalog.name + ".lock")
    fd = -1
    held = False
    try:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")        # Windows 锁的是字节区间
        # 建不出锁文件时如实 yield False:清理脚本据此拒绝动手,写入侧据此告警
        # 后继续——没有任何一方会把它当成「拿到了锁」。
        except OSError:  # fallback-ok: 如实上报「没拿到锁」,调用方各自处置
            yield False
            return
        deadline = time.monotonic() + timeout
        while True:
            if _try_lock(fd):
                held = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_LOCK_POLL_SECONDS)
        yield held
    finally:
        if fd >= 0:
            if held:
                _unlock(fd)
            os.close(fd)


def append_run_record(
    record: dict[str, Any],
    *,
    catalog_path: Path | None = None,
) -> None:
    """Append a single JSON line to the run catalog.

    追加本身走跨进程劝告锁 (`catalog_lock`),与清理脚本互斥。等不到锁则照样
    写并告警:一次运行的旁路记账不该把运行本身卡住。
    """
    dest = catalog_path or _DEFAULT_CATALOG_PATH

    # 边界**只管默认那份共享索引**。显式传 catalog_path 本身就是「我知道我在
    # 做什么、就要记到这里」的刻意行为(也是文档里给树外运行留的逃生口),不该
    # 被二次猜测;而从任意索引路径反推它的 output 树,只会把文件摆放位置变成
    # 隐藏契约(codex #453)。
    #
    # 污染仍被堵住:测试从不传 catalog_path,它们走的正是这条默认路径。
    if catalog_path is None:
        # 相对路径按**生产者的 CWD** 解析:产物就在那里。
        launch_dir = Path.cwd()
        text = str(record.get("output_dir") or "")
        reason = catalog_boundary_verdict(
            text, tree=_DEFAULT_OUTPUT_TREE, relative_base=launch_dir)
        if reason is not None:
            _logger.warning(
                "Run catalog append SKIPPED: %s. A run whose artifacts live "
                "outside the tree can never be opened from the console, so "
                "cataloguing it would only produce a row that is always set "
                "aside. Pass an explicit catalog_path to index such a run "
                "deliberately.", reason,
            )
            return

        # 读侧(控制台、清理脚本)只能把相对路径当成「相对仓库根」。绝大多数
        # 运行从仓库根启动,两种约定同解,**存的字符串一个字节都不变**;只有
        # 分歧时才改存绝对路径,免得记录指向一个不存在的目录。
        anchored = anchor_output_dir(text, relative_base=launch_dir)
        as_read = anchor_output_dir(text, relative_base=_REPO_ROOT)
        if anchored is not None and as_read is not None and not _same_path(
                anchored, as_read):
            record = dict(record)
            record["output_dir"] = str(anchored)

    dest.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(_sanitize_for_json(record), ensure_ascii=False,
                      sort_keys=True, default=str, allow_nan=False) + "\n"

    try:
        with catalog_lock(dest, timeout=_WRITER_LOCK_TIMEOUT) as held:
            if not held:
                _logger.warning(
                    "Run catalog lock not acquired within %.1fs (path=%s) — "
                    "appending anyway, because a run's bookkeeping must never "
                    "block the run itself.", _WRITER_LOCK_TIMEOUT, dest,
                )
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

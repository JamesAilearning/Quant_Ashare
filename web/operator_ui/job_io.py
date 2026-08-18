"""Shared job.json IO helpers for operator UI job lifecycle state."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from web.operator_ui._path_guard import PROJECT_ROOT, allowed_output_roots
from web.operator_ui.formatting import to_cn_date

# Platform-conditional locking primitives. ``sys.platform`` (not
# ``os.name``) is the platform check mypy understands as narrowing —
# without it the cross-platform run would see ``fcntl.flock`` /
# ``msvcrt.locking`` references as unbound attributes on the other OS.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def read_job_json(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {}


def write_job_json(
    job_dir: Path,
    updates: dict[str, Any],
    *,
    only_if_status: Collection[str] | None = None,
) -> bool:
    """Atomically merge ``updates`` into job.json. Returns True iff written.

    When ``only_if_status`` is given, the merge is an atomic compare-and-set: it
    happens only if the CURRENT on-disk ``status`` is one of those values, read
    inside the same cross-process lock as the write. This prevents a UI-side
    reconcile / stop from clobbering a terminal status that the job_runner
    process wrote concurrently (it takes the same lock). Returns False, having
    written nothing, when the guard does not hold.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    with _job_lock(job_dir):
        existing = read_job_json(job_dir)
        if only_if_status is not None and existing.get("status") not in only_if_status:
            return False
        existing.update(updates)
        tmp = job_dir / "job.json.tmp"
        tmp.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, job_dir / "job.json")
    return True


@contextlib.contextmanager
def _job_lock(job_dir: Path) -> Iterator[None]:
    lock_path = job_dir / "job.json.lock"
    with open(lock_path, "a+b") as lock_file:
        lock_file.seek(0)
        if sys.platform == "win32":
            lock_file.write(b"\0")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock_file.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Unified job listing (UI + CLI) for the Jobs page
# ---------------------------------------------------------------------------

# Path constants — defined here so the listing helpers live close to the
# existing job_dir helpers in the same module.
_JOB_ROOT = Path(__file__).resolve().parents[2] / "output" / "operator_ui" / "jobs"
_RUNS_INDEX = (
    Path(__file__).resolve().parents[2] / "output" / "runs" / "_index.jsonl"
)


@dataclass
class JobSummary:
    """Normalised view of a single run, regardless of launch source."""

    run_id: str
    type: str  # pipeline / walk_forward
    status: str
    source: str = "ui"  # "ui" or "cli"
    #: 该运行的产物目录。UI 作业取 job.json 的 run_dir，CLI 运行取索引
    #: 的 output_dir —— 详情页据此打开 CLI 运行（此前只认 UI 作业目录，
    #: 于是列表里占绝大多数的 CLI 滚动验证行点进去是「暂无记录」）。
    run_dir: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float | None = None
    key_metric_label: str = ""
    key_metric_value: str = ""
    config_summary: dict[str, str] = field(default_factory=dict)
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "type": self.type,
            "status": self.status,
            "source": self.source,
            "run_dir": self.run_dir,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "key_metric_label": self.key_metric_label,
            "key_metric_value": self.key_metric_value,
            "config_summary": self.config_summary,
            "error_message": self.error_message,
        }


def _load_ui_jobs() -> list[dict[str, Any]]:
    """Return raw dicts for every UI-launched job directory."""
    # Lazy imports (job_manager imports this module) — cycle-break pattern.
    # _reconcile_zombie must run on THIS primary Jobs-page list path
    # (list_all_jobs -> here), not only via JobManager.list_jobs(); otherwise a
    # reboot/OOM-killed run shows as "running" forever in the operator's main
    # list / running-count (audit G2).
    from web.operator_ui.job_manager import _reconcile_zombie
    from web.operator_ui.progress import build_job_progress

    if not _JOB_ROOT.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for job_dir in sorted(_JOB_ROOT.iterdir(), reverse=True):
        if not job_dir.is_dir():
            continue
        data = read_job_json(job_dir)
        if not data:
            continue
        data = _reconcile_zombie(job_dir, data)
        data["progress"] = build_job_progress(job_dir, data)
        data["_job_dir"] = str(job_dir)
        results.append(data)
    return results


def _load_cli_entries() -> list[dict[str, Any]]:
    """Return raw dicts for every CLI catalog entry."""
    if not _RUNS_INDEX.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with open(_RUNS_INDEX, encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
                record["_cli_source"] = True
                entries.append(record)
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda e: str(e.get("completed_at") or ""), reverse=True)


_STDERR_TAIL_BYTES = 8 * 1024  # 8 KiB is plenty for a Python traceback summary.
_FAILURE_HINT_TOKENS: tuple[str, ...] = (
    "Error",
    "error:",
    "Exception",
    "Traceback",
    "ValueError",
    "RuntimeError",
    "TypeError",
    "KeyError",
    "AssertionError",
    "FileNotFoundError",
)


def _extract_failure_detail(job_dir: Path, *, max_chars: int = 200) -> str:
    """Return a one-line summary of the failure from ``stderr.log``.

    Reads the trailing :data:`_STDERR_TAIL_BYTES` bytes (avoids loading a
    multi-megabyte log into memory), splits on newlines, then walks
    backwards looking for the most-recent line that contains an obvious
    error marker (``Error``, ``Exception``, ``Traceback``, etc.).  Falls
    back to the last non-empty line.  Returns an empty string when no
    stderr file exists or it is empty.

    The result is truncated to ``max_chars`` so the Jobs page table cell
    stays readable; the full log is always one click away from the
    Results page.
    """

    stderr_path = job_dir / "stderr.log"
    if not stderr_path.is_file():
        return ""
    try:
        with stderr_path.open("rb") as handle:
            try:
                handle.seek(-_STDERR_TAIL_BYTES, 2)  # 2 = SEEK_END
            except OSError:
                handle.seek(0)
            data = handle.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if any(token in line for token in _FAILURE_HINT_TOKENS):
            return line[:max_chars]
    return lines[-1][:max_chars]


def _normalise_ui_job(raw: dict[str, Any]) -> JobSummary:
    job_id = str(raw.get("job_id") or raw.get("run_id") or "")
    mode = str(raw.get("mode") or "")
    status = str(raw.get("status") or "unknown")
    if status == "success":
        status = "completed"

    # Backfill created_at from started_at for jobs written before created_at was
    # stamped (PR-K) so in-flight legacy jobs still sort/filter correctly.
    created = str(raw.get("created_at") or raw.get("started_at") or "")
    started = str(raw.get("started_at") or "")
    finished = str(raw.get("ended_at") or "")
    dur = raw.get("duration_seconds") if isinstance(raw.get("duration_seconds"), (int, float)) else None

    key_label, key_value = "", ""
    if status == "running":
        # Assign to a local first so isinstance can narrow it; the
        # inline ternary doesn't propagate the narrowing through the
        # second ``raw.get`` call.
        progress_raw = raw.get("progress")
        progress: dict[str, Any] = progress_raw if isinstance(progress_raw, dict) else {}
        key_label = "阶段"
        key_value = str(progress.get("label") or status)
    elif status == "completed":
        key_label = "结果"
        key_value = "✓"
    elif status == "failed":
        # Surface the actual error so the operator can diagnose without
        # opening stderr.log. Order of preference: explicit error / stop_error
        # in job.json → tail of stderr.log → progress label fallback.
        progress_raw = raw.get("progress")
        progress = progress_raw if isinstance(progress_raw, dict) else {}
        key_label = "失败原因"
        explicit_error = str(raw.get("stop_error") or raw.get("error") or "").strip()
        stderr_tail = ""
        job_dir_str = raw.get("_job_dir")
        if job_dir_str:
            try:
                stderr_tail = _extract_failure_detail(Path(str(job_dir_str)))
            except OSError:
                stderr_tail = ""
        key_value = (
            explicit_error
            or stderr_tail
            or str(progress.get("label") or "失败")
        )

    config = raw.get("config")
    if isinstance(raw.get("config_yaml"), str):
        try:
            import yaml
            config = yaml.safe_load(raw["config_yaml"]) if isinstance(config, str) else config
        except Exception:
            pass

    cfg_summary: dict[str, str] = {}
    if isinstance(config, dict):
        inst = config.get("instruments", "")
        if inst:
            cfg_summary["instruments"] = str(inst) if isinstance(inst, str) else ",".join(inst) if isinstance(inst, list) else str(inst)
        model = config.get("model_type", "")
        if model:
            cfg_summary["model"] = str(model)

    error_msg = str(raw.get("stop_error") or raw.get("error") or "")

    return JobSummary(
        # ``run_id`` is the canonical full id used for routing (st.switch_page
        # carries it via query_params / session_state). Display surfaces are
        # responsible for their own truncation; we MUST NOT truncate here, or
        # the walk-forward detail page's exact-match selectbox lookup misses
        # any job whose full id exceeds the old 40-char ceiling.
        run_id=job_id,
        type=mode,
        status=status,
        source="ui",
        run_dir=str(raw.get("run_dir") or ""),
        created_at=created,
        started_at=started,
        finished_at=finished,
        duration_seconds=dur,
        key_metric_label=key_label,
        key_metric_value=key_value,
        config_summary=cfg_summary,
        error_message=error_msg,
    )


#: ``(_ALLOWED_ROOTS 当时那个对象, 算好的比较键)``。缓存**按身份**失效:
#: 测试用 ``patch`` 换上另一个元组对象时身份就变了,于是重算——宁可多算,
#: 绝不返回过期的边界(那会让判据放行本该拒绝的路径)。
_ROOT_KEYS_CACHE: tuple[object, tuple[str, ...]] | None = None


def _allowed_root_keys() -> tuple[str, ...]:
    """读边界的比较键。

    ``allowed_output_roots()`` 每次调用都对两个根 ``resolve()``(碰盘)。
    根不随行变化,逐行调用就是把 2×N 次 resolve 摊到整轮过滤上——本机
    3527 行实测 771 ms,把行侧改成词法后仍剩 409 ms,余量全在这里。
    """
    global _ROOT_KEYS_CACHE
    from web.operator_ui import _path_guard

    marker = _path_guard._ALLOWED_ROOTS
    cached = _ROOT_KEYS_CACHE
    if cached is not None and cached[0] is marker:
        return cached[1]
    keys = tuple(
        os.path.normcase(os.path.normpath(str(root)))
        for root in allowed_output_roots()
    )
    _ROOT_KEYS_CACHE = (marker, keys)
    return keys


def run_dir_is_inspectable(run_dir: str) -> bool:
    """True iff a run's artifacts could be opened from the detail pages.

    Pure path arithmetic — **no per-row filesystem I/O**. That claim used
    to be false: the containment check ran ``Path.resolve()`` per row,
    which walks symlinks and therefore hits the disk. Measured on this
    box, 3527 catalog rows cost **771 ms every render** (219 µs/row), and
    ``resolve()`` is ~320x slower than ``normpath`` (codex #444 r6 sweep).
    Now the row side is lexical only (``anchored_run_dir`` normpaths, then
    ``normcase``) and the two roots are resolved once per pass.

    Dropping ``resolve()`` gives up symlink-escape detection **here**;
    that is deliberate. This predicate answers "is this row worth
    listing", and every actual read still goes through
    ``guard_output_path`` (which resolves) before touching a file — the
    security check stays where the file access is. Lexical ``..`` escapes,
    the case this filter is really about, are still caught because
    ``anchored_run_dir`` collapses them first.

    The predicate is exactly the console spec's read boundary (file
    access confined to ``output/`` and ``output/operator_ui/``): a run
    whose artifacts live anywhere else can never be rendered, whatever
    its status says.

    This matters because the catalog's default path is CWD-relative, so
    test runs executed from the repo root append their records to the
    OPERATOR's catalog while writing artifacts to a temp dir that is
    then deleted (this box: 3404 such rows against 105 real ones). They
    are not runs the operator can inspect; the page discloses how many
    it set aside rather than silently truncating.
    """
    text = str(run_dir or "").strip()
    if not text:
        return False
    # 锚定走 ``anchored_run_dir`` —— 就是那**一段**代码,不是各写一份。
    # 判据与页面折叠若各锚各的,在仓库根之外启动 UI 时「判定可达」的运行
    # 会反被路径守卫拒绝(codex #444 r1)。
    candidate = os.path.normcase(str(anchored_run_dir(text)))
    for root in _allowed_root_keys():
        if candidate == root or candidate.startswith(root + os.sep):
            return True
    return False


def anchored_run_dir(run_dir: str) -> Path:
    """把目录记录里的 ``output_dir`` 锚在**仓库根**,并折平 ``..``。

    :func:`run_dir_is_inspectable` 与 :func:`fold_catalog_by_dir` 都调它——
    是同一段代码,不是各写一份。两处各写一份不等式正是它们会分叉的方式:
    判据锚在仓库根、页面锚在 CWD 时,在仓库根之外启动 UI,「判定可达」的
    运行会反被路径守卫拒绝(codex #444 r1)。

    ``..`` 必须在这里折平:``output/runs/a`` 与 ``output/x/../runs/a`` 指向
    同一份产物、两者都判可达,若折叠键保留字面的 ``..`` 就会被当成两次不同
    的运行——被覆盖的历史行于是静默渲染出当前那份报告(codex #444 r6)。
    折平用 ``os.path.normpath``:纯词法、无文件系统 I/O,与「判据不做逐行
    I/O」的约束一致(``resolve()`` 会走符号链接,那是判据自己那一步的事)。
    """
    candidate = Path(str(run_dir or "").strip())
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return Path(os.path.normpath(str(candidate)))


@dataclass(frozen=True)
class CatalogFold:
    """CLI 目录记录按**产物目录**折叠后的结果。

    同一个 preset 反复跑会追加新的目录条目,却把报告写回**同一个**
    ``output_dir``——旧运行的产物已被覆盖,盘上只剩最新一份。所以每个目录
    只有最新那条能当选择器条目;更早的那些既不能列出(会渲染出别人的报告),
    也不能静默别名过去(点它就会以为看的是自己点的那次)。
    """

    #: 每个目录最新的那条记录,按输入顺序(``list_all_jobs`` 已按完成时间倒序)。
    newest: tuple[JobSummary, ...]
    #: ``newest`` 里每条的 run_id → 锚定后的绝对目录。
    dir_of_run: Mapping[str, Path]
    #: 产物被同目录更晚运行覆盖的 run_id → 那个目录(绝对)。走告警路径,不别名。
    superseded_dir_of_run: Mapping[str, Path]

    @property
    def superseded_count(self) -> int:
        return len(self.superseded_dir_of_run)


def fold_catalog_by_dir(rows: Iterable[JobSummary]) -> CatalogFold:
    """把目录记录折叠成「每个产物目录一条」。

    详情页(``results.py`` / ``walk_forward.py``)都要做这件事,而且必须做得
    **一模一样**:锚定、首条即最新、被覆盖者只计数不别名——这三条各自都被
    审查抓到过一次(#444 r1/r2/r4)。所以它只有这一份实现。

    比较键走 ``os.path.normcase``:Windows 上路径大小写不敏感,同一个目录写成
    两种大小写会被当成两个目录,折叠就漏了。展示用的路径保留原样。
    """
    newest: list[JobSummary] = []
    dir_of_run: dict[str, Path] = {}
    superseded: dict[str, Path] = {}
    seen: dict[str, Path] = {}
    for row in rows:
        if not row.run_dir:
            continue
        resolved = anchored_run_dir(row.run_dir)
        key = os.path.normcase(str(resolved))
        if key in seen:
            superseded[row.run_id] = seen[key]
            continue
        seen[key] = resolved
        newest.append(row)
        dir_of_run[row.run_id] = resolved
    return CatalogFold(
        newest=tuple(newest),
        dir_of_run=dir_of_run,
        superseded_dir_of_run=superseded,
    )


def count_cli_rows_outside_output_tree() -> int:
    """How many catalog rows ``list_all_jobs`` set aside as unopenable.

    A separate read rather than a side channel out of ``list_all_jobs``:
    the page needs ONE number for a disclosure line, and threading it
    through a pinned return signature (or a module global) would couple
    two unrelated concerns. The catalog read is a single small file.
    """
    return sum(
        1
        for raw in _load_cli_entries()
        if not run_dir_is_inspectable(str(raw.get("output_dir") or ""))
    )


def _normalise_cli_entry(raw: dict[str, Any]) -> JobSummary:
    # See `_normalise_ui_job` — keep the full run id; display layer truncates.
    run_id = str(raw.get("run_id") or "")
    engine = str(raw.get("engine") or "")
    etype = engine if engine else "unknown"
    status = str(raw.get("status") or "completed")
    # CLI 侧词汇归一到 UI 词汇。运行目录写的是 ok / partial(见
    # src/core/pipeline.py 与 walk_forward/engine.py),而本页的筛选下拉、
    # 标签与图标都说 completed/partial —— 不归一的话,列表把一千多条
    # ok 行标成「已完成」,筛选「已完成」却一条都选不出来(UI drift 审计)。
    # 与上面 _normalise_ui_job 的 success→completed 同源;partial 原样
    # 保留:它已经是下拉选项、有标签、有图标、在 _param_guard 白名单里。
    if status == "ok":
        status = "completed"
    created = str(raw.get("completed_at") or "")
    dur = raw.get("duration_seconds") if isinstance(raw.get("duration_seconds"), (int, float)) else None

    key_label, key_value = "", ""
    if status == "completed":
        key_label = "结果"
        key_value = "✓"

    cfg_summary: dict[str, str] = {}
    return JobSummary(
        run_id=run_id,
        type=etype,
        status=status,
        source="cli",
        run_dir=str(raw.get("output_dir") or ""),
        created_at=created,
        finished_at=created,
        duration_seconds=dur,
        key_metric_label=key_label,
        key_metric_value=key_value,
        config_summary=cfg_summary,
    )


SORT_OPTIONS: tuple[str, ...] = (
    "created_at",
    "duration",
    "status",
    "type",
    "run_id",
)
SORT_DIRECTIONS: tuple[str, ...] = ("desc", "asc")

# Terminal statuses that are safe to bulk-delete. ``running`` /
# ``pending`` / ``queued`` are deliberately excluded — JobManager.delete
# also refuses to remove a running job, but filtering here keeps them
# out of the preview count too.
_CLEANUP_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "success", "ok", "failed", "stopped", "cancelled", "stop_failed"}
)


def jobs_eligible_for_cleanup(
    jobs: list[JobSummary],
    *,
    older_than_days: int,
    today: date,
) -> list[str]:
    """Return run_ids of UI-launched jobs old enough to bulk-delete.

    Eligibility (UI review P2-11 "清理 > N 天前的已完成 job"):
    * ``source == "ui"`` — only UI-managed jobs have a deletable
      on-disk directory; CLI catalog entries are not removable here.
    * terminal status (not running / pending) — see
      :data:`_CLEANUP_TERMINAL_STATUSES`.
    * the job's timestamp (created_at, else finished_at) is a valid
      ISO date strictly older than ``today - older_than_days``.

    Pure + deterministic (``today`` injected) so the cleanup preview
    count is unit-testable without touching the clock or the filesystem.
    """

    if older_than_days < 0:
        raise ValueError(f"older_than_days={older_than_days!r} must be >= 0.")
    cutoff = today - timedelta(days=older_than_days)
    eligible: list[str] = []
    for job in jobs:
        if job.source != "ui":
            continue
        if job.status not in _CLEANUP_TERMINAL_STATUSES:
            continue
        stamp = to_cn_date(job.created_at or job.finished_at or "")
        if not stamp:
            continue
        try:
            job_date = date.fromisoformat(stamp)
        except ValueError:
            continue
        if job_date < cutoff:
            eligible.append(job.run_id)
    return eligible


def list_all_jobs(
    *,
    type_filter: str = "all",
    status_filter: str = "all",
    source_filter: str = "all",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[JobSummary], int, int]:
    """Return a page of unified job summaries plus filter-wide counts.

    Returns ``(page_items, total_filtered, running_count_filtered)``.
    The third element is the count of running jobs across the FULL
    filtered set (not just the current page window) so the jobs page's
    auto-refresh control stays visible while the operator paginates
    away from page 1 — without it, ``running_count`` would only see
    the page's slice and the refresh affordance would disappear
    (Codex P2 on PR #197).

    Filters are applied *before* sort, sort before pagination.

    ``type_filter`` accepts ``"all"`` or one of ``"pipeline"``,
    ``"walk_forward"``, ``"provider"``.

    ``status_filter`` accepts ``"all"``, ``"queued"``, ``"running"``,
    ``"completed"``, ``"failed"``, ``"cancelled"``.

    ``source_filter`` accepts ``"all"``, ``"ui"``, ``"cli"``.

    ``date_from`` / ``date_to`` are inclusive ISO-8601 date strings
    (``YYYY-MM-DD``).  Empty strings disable that side of the range.  The
    range is applied against each job's ``created_at`` (UI jobs) or
    ``completed_at`` (CLI catalog) timestamp.  Malformed dates are
    rejected loudly so caller bugs do not silently widen the result set
    (AGENTS.md #8 "no silent fallback").

    ``sort_by`` is one of :data:`SORT_OPTIONS`; ``sort_dir`` is one of
    :data:`SORT_DIRECTIONS`.  Unknown values raise :class:`ValueError`.

    Pagination is now a **real offset slice**, not the cumulative
    "load more" pattern. Page N (1-indexed) returns
    ``sorted_items[(N-1) * page_size : N * page_size]``. A request past
    the end returns an empty list with the same ``total`` count — the
    UI surfaces "no items on this page" while still showing the page
    indicator (UI review P1-10).
    """
    if sort_by not in SORT_OPTIONS:
        raise ValueError(
            f"sort_by={sort_by!r} not in {SORT_OPTIONS}; "
            "extend SORT_OPTIONS if a new key is required."
        )
    if sort_dir not in SORT_DIRECTIONS:
        raise ValueError(
            f"sort_dir={sort_dir!r} not in {SORT_DIRECTIONS}."
        )
    if page < 1:
        raise ValueError(f"page={page!r} must be >= 1.")
    if page_size < 1:
        raise ValueError(f"page_size={page_size!r} must be >= 1.")
    _parse_date_or_raise(date_from, field="date_from")
    _parse_date_or_raise(date_to, field="date_to")

    # Load raw data
    ui_raw = _load_ui_jobs()
    cli_raw = _load_cli_entries()

    # Normalise
    all_items: list[JobSummary] = []
    for raw in ui_raw:
        all_items.append(_normalise_ui_job(raw))
    # CLI rows whose artifacts sit outside the readable output tree can
    # never be opened from a detail page (see run_dir_is_inspectable).
    # They are set aside rather than listed — but COUNTED, so the page
    # can disclose the number instead of silently truncating.
    for raw in cli_raw:
        summary = _normalise_cli_entry(raw)
        if not run_dir_is_inspectable(summary.run_dir):
            continue
        all_items.append(summary)

    # Filter
    filtered = _apply_filters(
        all_items,
        type_filter,
        status_filter,
        source_filter,
        search,
        date_from,
        date_to,
    )

    # Count running jobs across the FULL filtered set BEFORE pagination
    # so the jobs page's auto-refresh control stays visible while the
    # operator paginates away from the running rows. Codex P2 on
    # PR #197 — without this the count was derived from the page slice
    # downstream and disappeared as soon as the operator clicked
    # "下一页".
    running_count_filtered = sum(
        1 for item in filtered if item.status == "running"
    )

    # Sort
    sorted_items = _apply_sort(filtered, sort_by, sort_dir)

    # Paginate — real offset slice. The cumulative "first N*size items"
    # form (UI review P1-10) made dataframe formatting cost grow
    # linearly with click count and broke any "what page am I on"
    # mental model. Page 1 ⇒ items 0..size-1, page 2 ⇒ size..2*size-1,
    # …, past-end ⇒ [].
    total = len(sorted_items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = sorted_items[start:end]

    return page_items, total, running_count_filtered


def _parse_date_or_raise(value: str, *, field: str) -> None:
    """Validate ISO-date string; raise on malformed input (no silent fallback)."""
    if not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field}={value!r} is not a valid ISO date (YYYY-MM-DD): {exc}"
        ) from exc


def _job_timestamp(item: JobSummary) -> str:
    """Return the canonical date-stamp for filter / sort purposes.

    For UI jobs prefer ``created_at``; for CLI catalog entries the
    timestamp lives in ``finished_at`` (the catalog records
    ``completed_at`` which is normalised into both).
    """
    return item.created_at or item.finished_at or ""


def _apply_filters(
    items: list[JobSummary],
    type_filter: str,
    status_filter: str,
    source_filter: str,
    search: str,
    date_from: str = "",
    date_to: str = "",
) -> list[JobSummary]:
    result: list[JobSummary] = []
    search_lower = search.strip().lower()
    for item in items:
        if type_filter != "all" and item.type != type_filter:
            continue
        if status_filter != "all" and item.status != status_filter:
            continue
        if source_filter != "all" and item.source != source_filter:
            continue
        if date_from or date_to:
            stamp = _job_timestamp(item)
            if not stamp:
                # No timestamp at all — drop on any date filter so the
                # date range is honoured rather than silently widened.
                continue
            # CN-local date bucket, consistent with the CN-local display + the
            # CN date.today() the quick-range presets use (PR-K). A raw UTC[:10]
            # would skew near-midnight jobs one day off the displayed date.
            day = to_cn_date(stamp)
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
        if search_lower:
            combined = (
                f"{item.run_id} {item.type} {item.status} "
                f"{item.key_metric_label} {item.key_metric_value} "
                f"{item.error_message}"
            ).lower()
            if search_lower not in combined:
                continue
        result.append(item)
    return result


def _apply_sort(
    items: list[JobSummary], sort_by: str, sort_dir: str
) -> list[JobSummary]:
    """Return a new sorted list.

    For ``duration`` and ``created_at``, missing values are always
    rendered at the bottom regardless of ``sort_dir``.  Operationally
    "unknown" is never the most/least valued row — it just sits below
    the known rows so it never crowds the active comparison.
    """
    reverse = sort_dir == "desc"

    if sort_by == "duration":
        has = [x for x in items if x.duration_seconds is not None]
        missing = [x for x in items if x.duration_seconds is None]
        return (
            sorted(has, key=lambda x: float(x.duration_seconds or 0.0), reverse=reverse)
            + missing
        )

    if sort_by == "created_at":
        has = [x for x in items if _job_timestamp(x)]
        missing = [x for x in items if not _job_timestamp(x)]
        return (
            sorted(has, key=lambda x: _job_timestamp(x), reverse=reverse)
            + missing
        )

    key_fn: Any
    if sort_by == "status":
        key_fn = lambda x: x.status  # noqa: E731
    elif sort_by == "type":
        key_fn = lambda x: x.type  # noqa: E731
    elif sort_by == "run_id":
        key_fn = lambda x: x.run_id  # noqa: E731
    else:  # pragma: no cover — guarded earlier in list_all_jobs
        raise ValueError(f"sort_by={sort_by!r} not supported")

    return sorted(items, key=key_fn, reverse=reverse)

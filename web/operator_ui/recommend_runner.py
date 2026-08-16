"""出单同步 runner — 运行中心页的「跑今日出单」执行通道。

Runs ``scripts/daily_recommend.py`` synchronously in a subprocess
(minutes-scale; the 2026-08-05 cutover run wrote its three artifacts in
well under the 15-minute ceiling) and reports the outcome for the page
to render. Mirrors the audited-runner precedent
(``pit_validation_runner``): pinned script path, UTF-8 text mode,
timeout, ``cwd`` = repo root so the CLI's relative ``out_dir`` lands in
``output/daily_recommend/`` exactly like a terminal run.

Boundaries (openspec 2026-08-16-ui-run-center):

* Ensemble serving only — argv carries the five data/identity flags the
  cockpit's ``morning_command`` prints and NOTHING else. In particular
  no ``--model``/``--fit-start``/``--fit-end`` (refused by the CLI in
  ensemble mode) and no ``--topk``/``--instruments``/
  ``--rebalance-cadence-days`` (universe/cadence/topk stay with the
  serving-config binding chain inside the CLI).
* The only coupling with inference code is the CLI process boundary —
  this module never imports ``src.inference.*``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from scripts.child_env import utf8_child_env
from web.operator_ui.provider_lock import hold_update_lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECOMMEND_SCRIPT = PROJECT_ROOT / "scripts" / "daily_recommend.py"

# Where a terminal run's artifacts land (RecommendationConfig.out_dir is
# CWD-relative; both this runner and the 今日推荐 page anchor it to the
# repo root). The CLI is pointed at a per-run STAGING dir under it and
# the finished files are published per-file via atomic os.replace — a
# timeout kill mid-``write_outputs`` must never leave a torn file where
# a previously valid day's artifact stood (codex #440 r1).
OUT_DIR = PROJECT_ROOT / "output" / "daily_recommend"

# The CLI loads the bundle, scores ~800 names and writes three files —
# minutes, not hours. 15 min mirrors the PIT runner's generous ceiling.
DEFAULT_TIMEOUT_S = 900

_STD_TAIL_CHARS = 4000


@dataclass(frozen=True)
class RecommendRunResult:
    """Outcome of one synchronous run. ``kind`` drives the page:

    * ``ok`` — CLI exit 0; the artifacts are on disk and ``stdout_tail``
      carries the banner (entry_date / rebalance_day / HOLD).
    * ``failed`` — CLI exit != 0. Every refusal in this CLI is loud on
      STDOUT — the repo logger's handler is ``StreamHandler(sys.stdout)``
      with ``propagate=False`` (``src/core/logger.py``), so
      ``stdout_tail`` carries the reason; ``stderr_tail`` is mostly
      import-time environment noise and is secondary.
    * ``blocked_by_update`` — the updater's single-flight provider lock
      is held (or cannot be opened): a data update is live, and the
      swap window is not reader-concurrent. The page's status-based
      gate is only UX; THIS refusal is the authority (codex #440 r2).
    * ``timeout`` — exceeded ``timeout_s`` and was killed. The staging
      dir (with any half-written files) is cleaned up; previously
      published artifacts are untouched.
    * ``launch_failed`` — the interpreter could not be started.
    * ``run_failed`` — the script is missing (repo layout drift), the
      CLI broke its contract (exit 0 without artifacts), or publishing
      failed. A mid-publish failure is ROLLED BACK (new files return to
      staging, replaced prior versions are restored from the staging
      ``.prior`` dir — codex #440 r3: a sequential publish must never
      leave a mixed-run artifact set); only an INCOMPLETE rollback is a
      torn state, and then ``error`` names every residual exactly. The
      staging dir is kept for manual disposal in every publish-failure
      case.
    """

    kind: str
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""
    elapsed_s: float = 0.0
    published: tuple[str, ...] = ()


def _tail(text: str) -> str:
    return text.strip()[-_STD_TAIL_CHARS:]


def build_recommend_argv(
    *,
    ensemble_manifest: str,
    provider_uri: str,
    delisted_registry: str,
    name_source: str,
    bundle_max_age_days: int,
    out_dir: str,
    python: str | None = None,
) -> list[str]:
    """Argv mirroring the cockpit's printed morning command, ensemble form,
    plus ``--out-dir`` pointed at the caller's staging dir.

    List-form argv — no shell, no quoting; the cockpit's single-quote
    convention exists only for paste-able TEXT.
    """
    return [
        python or sys.executable,
        str(RECOMMEND_SCRIPT),
        "--ensemble-manifest",
        ensemble_manifest,
        "--provider-uri",
        provider_uri,
        "--delisted-registry",
        delisted_registry,
        "--name-source",
        name_source,
        "--bundle-max-age-days",
        str(int(bundle_max_age_days)),
        "--out-dir",
        out_dir,
    ]


def run_daily_recommend(
    *,
    ensemble_manifest: str,
    provider_uri: str,
    delisted_registry: str,
    name_source: str,
    bundle_max_age_days: int,
    python: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> RecommendRunResult:
    """Run the recommender once, synchronously, and report the outcome.

    ``python`` defaults to ``sys.executable`` — the interpreter running
    the UI, in production the pinned canonical venv.
    """
    if not RECOMMEND_SCRIPT.exists():
        return RecommendRunResult(
            kind="run_failed",
            error=f"出单脚本不在预期路径(仓库布局变了?):{RECOMMEND_SCRIPT}",
        )
    # Per-run staging under the final dir (same volume → os.replace is
    # atomic). The CLI itself mkdirs it via write_outputs.
    staging = OUT_DIR / f".staging-{os.getpid()}-{uuid4().hex}"
    cmd = build_recommend_argv(
        ensemble_manifest=ensemble_manifest,
        provider_uri=provider_uri,
        delisted_registry=delisted_registry,
        name_source=name_source,
        bundle_max_age_days=bundle_max_age_days,
        out_dir=str(staging),
        python=python,
    )
    started = time.monotonic()
    # The CLI's bundle READ must not overlap the updater's swap window —
    # hold the updater's own provider lock for the child's lifetime
    # (authoritative; the status artifact is advisory only). Publishing
    # happens after release: it only touches output/, which the updater
    # never writes.
    with hold_update_lock(Path(provider_uri)) as held:
        if not held:
            return RecommendRunResult(
                kind="blocked_by_update",
                error=(
                    "数据更新的单飞锁正被持有(或锁文件不可用)——换库"
                    "不与读者并发,等更新结束后再跑;锁随对方进程退出"
                    "自动释放。"
                ),
            )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                cwd=str(PROJECT_ROOT),
                env=utf8_child_env(),
            )
        except subprocess.TimeoutExpired:
            # Half-written files exist only in staging — junk, clean
            # them. Published artifacts were never touched.
            shutil.rmtree(staging, ignore_errors=True)
            return RecommendRunResult(
                kind="timeout",
                error=(
                    f"超过 {timeout_s}s 上限,子进程已终止。半成品只在"
                    "暂存目录且已清理,已发布的工件未被触碰。"
                ),
                elapsed_s=time.monotonic() - started,
            )
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return RecommendRunResult(
                kind="launch_failed",
                error=f"无法启动解释器 {cmd[0]!r}:{exc}",
                elapsed_s=time.monotonic() - started,
            )
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        return RecommendRunResult(
            kind="failed",
            exit_code=proc.returncode,
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
            error=(
                f"出单 CLI 退出码 {proc.returncode}"
                "(拒绝原因见输出尾部;本仓 CLI 经 logger 落 stdout)。"
            ),
            elapsed_s=elapsed,
        )
    return _publish(staging, proc.stdout, proc.stderr, elapsed)


def _rollback(
    staging: Path,
    prior_dir: Path,
    published: list[str],
    saved: list[str],
) -> list[str]:
    """Undo a partial publish. Returns residuals (empty = fully undone).

    Order matters: first move every published NEW file back to staging
    (clearing the destinations), then restore every saved prior version.
    The publish loop's precedent is the rotation executor's late-write
    rollback (created files undone in reverse); here the unit is the
    per-name pair (new file, prior version).
    """
    residual: list[str] = []
    failed_unpublish: set[str] = set()
    for name in published:
        try:
            os.replace(OUT_DIR / name, staging / name)
        except OSError:
            residual.append(f"{name}(新文件仍在发布目录)")
            failed_unpublish.add(name)
    for name in saved:
        if name in failed_unpublish:
            # Restoring the prior would OVERWRITE the new file still
            # sitting at the destination — the only copy left. Keep the
            # prior in the ledger dir and say so instead.
            residual.append(f"{name}(旧版本滞留在回滚目录)")
            continue
        try:
            os.replace(prior_dir / name, OUT_DIR / name)
        except OSError:
            residual.append(f"{name}(旧版本未能恢复)")
    if not residual:
        # Empty now — every prior went back.
        shutil.rmtree(prior_dir, ignore_errors=True)
    return residual


def _publish(
    staging: Path, stdout: str, stderr: str, elapsed: float
) -> RecommendRunResult:
    """Move the finished artifacts from staging into ``OUT_DIR``.

    Per-file ``os.replace`` on the same volume, with a rollback ledger
    (codex #440 r3): each replaced prior version is FIRST moved into
    ``staging/.prior``, so a failure at the second or third replace
    never strands a mixed-run artifact set — new files return to
    staging, priors return to the publish dir. Only an incomplete
    rollback is a torn state, and it is reported name by name. The
    staging dir is kept on every publish failure (it holds the only
    complete copy).
    """
    try:
        files = sorted(p for p in staging.iterdir() if p.is_file())
    except OSError as exc:
        return RecommendRunResult(
            kind="run_failed",
            exit_code=0,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            error=f"退出码 0 但暂存目录不可读({exc})——CLI 契约被违反。",
            elapsed_s=elapsed,
        )
    if not files:
        shutil.rmtree(staging, ignore_errors=True)
        return RecommendRunResult(
            kind="run_failed",
            exit_code=0,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            error="退出码 0 但暂存目录无产物——CLI 契约被违反。",
            elapsed_s=elapsed,
        )
    prior_dir = staging / ".prior"
    try:
        prior_dir.mkdir()
    except OSError as exc:
        return RecommendRunResult(
            kind="run_failed",
            exit_code=0,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            error=(
                f"发布前无法创建回滚目录({exc})——一个文件都没动;"
                f"本次产物完整保留在暂存 {staging}。"
            ),
            elapsed_s=elapsed,
        )
    published: list[str] = []
    saved: list[str] = []
    try:
        for f in files:
            dest = OUT_DIR / f.name
            if dest.exists():
                os.replace(dest, prior_dir / f.name)
                saved.append(f.name)
            os.replace(f, dest)
            published.append(f.name)
    except OSError as exc:
        residual = _rollback(staging, prior_dir, published, saved)
        if residual:
            return RecommendRunResult(
                kind="run_failed",
                exit_code=0,
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                error=(
                    f"发布在中途失败({exc})且回滚不完整——发布目录可能"
                    f"混着两次运行的文件。残留:{';'.join(residual)}。"
                    f"暂存与回滚目录保留在 {staging},请手工处置。"
                ),
                elapsed_s=elapsed,
            )
        return RecommendRunResult(
            kind="run_failed",
            exit_code=0,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            error=(
                f"发布在中途失败({exc});已整体回滚——发布目录恢复原状,"
                f"本次产物完整保留在暂存 {staging} 以便手工处置。"
            ),
            elapsed_s=elapsed,
        )
    shutil.rmtree(staging, ignore_errors=True)
    return RecommendRunResult(
        kind="ok",
        exit_code=0,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        elapsed_s=elapsed,
        published=tuple(published),
    )

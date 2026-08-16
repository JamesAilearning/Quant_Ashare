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

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from scripts.child_env import utf8_child_env

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECOMMEND_SCRIPT = PROJECT_ROOT / "scripts" / "daily_recommend.py"

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
      stderr (stale bundle, integrity stamp, binding mismatch, …);
      ``stderr_tail`` is the reason to show, verbatim.
    * ``timeout`` — exceeded ``timeout_s`` and was killed.
    * ``launch_failed`` — the interpreter could not be started.
    * ``run_failed`` — the script is missing (repo layout drift).
    """

    kind: str
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""
    elapsed_s: float = 0.0


def _tail(text: str) -> str:
    return text.strip()[-_STD_TAIL_CHARS:]


def build_recommend_argv(
    *,
    ensemble_manifest: str,
    provider_uri: str,
    delisted_registry: str,
    name_source: str,
    bundle_max_age_days: int,
    python: str | None = None,
) -> list[str]:
    """Argv mirroring the cockpit's printed morning command, ensemble form.

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
    cmd = build_recommend_argv(
        ensemble_manifest=ensemble_manifest,
        provider_uri=provider_uri,
        delisted_registry=delisted_registry,
        name_source=name_source,
        bundle_max_age_days=bundle_max_age_days,
        python=python,
    )
    started = time.monotonic()
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
        return RecommendRunResult(
            kind="timeout",
            error=f"超过 {timeout_s}s 上限,子进程已终止。",
            elapsed_s=time.monotonic() - started,
        )
    except OSError as exc:
        return RecommendRunResult(
            kind="launch_failed",
            error=f"无法启动解释器 {cmd[0]!r}:{exc}",
            elapsed_s=time.monotonic() - started,
        )
    elapsed = time.monotonic() - started
    if proc.returncode == 0:
        return RecommendRunResult(
            kind="ok",
            exit_code=0,
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
            elapsed_s=elapsed,
        )
    return RecommendRunResult(
        kind="failed",
        exit_code=proc.returncode,
        stdout_tail=_tail(proc.stdout),
        stderr_tail=_tail(proc.stderr),
        error=f"出单 CLI 退出码 {proc.returncode}(拒绝原因见 stderr 尾部)。",
        elapsed_s=elapsed,
    )

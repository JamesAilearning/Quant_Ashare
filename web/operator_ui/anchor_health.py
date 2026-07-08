"""REGEN-2 anchor health for the sidebar badge (UI 轨道 PR-B).

Surfaces three local facts and one CI fact about the canonical replay anchor
(`tests/regression/fixtures/walk_forward_baseline_metrics.json`):

* the baseline's content identity — CRLF→LF normalized SHA-256, the SAME
  algorithm the anchor regression test pins
  (``tests/regression/test_walk_forward_replay_baseline_regen2._normalized_sha256``);
* the last re-sign — the baseline file's last-touch commit (date + short sha),
  the same "identity = last-touched commit" convention the prereg gate uses;
* whether the ``.evidence.json`` sidecar exists (mandatory from the next
  re-sign onward; absent = legacy, marked explicitly);
* the latest completed conclusion of the CI anchor leg (the
  ``test (ubuntu-latest, 3.12)`` job of ``test.yml`` on ``main``) via the
  local ``gh`` CLI.

Boundaries (spec ``v2-operator-ui-console``): read-only; ``gh``/``git`` are
OPTIONAL probes — absence, auth failure, timeout or unparsable output degrade
to an explicit "unknown" with an honest reason, never a fabricated state and
never a crash; probes are pull-based (the page caches them behind a TTL) — no
background polling lives here. Zero Streamlit imports: everything is
injectable and unit-testable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = (
    _REPO_ROOT / "tests" / "regression" / "fixtures"
    / "walk_forward_baseline_metrics.json"
)


def evidence_sidecar_for(baseline: Path) -> Path:
    """The mandatory evidence sidecar path for a baseline file.

    Replaces the ``.json`` suffix with ``.evidence.json`` (stem-based) so the
    result is byte-identical to the canonical regression guard's
    ``EVIDENCE_SIDECAR`` (``walk_forward_baseline_metrics.evidence.json`` — see
    ``tests/regression/test_walk_forward_replay_baseline_regen2.py``). The
    earlier form appended to the full name, yielding
    ``…metrics.json.evidence.json``, which never matches the committed sidecar
    so the badge would report ``缺(legacy)`` forever (codex #335 P2).
    """
    return baseline.with_name(baseline.stem + ".evidence.json")


EVIDENCE_PATH = evidence_sidecar_for(BASELINE_PATH)
# The CI leg that actually replays the anchor (see .github/workflows/test.yml:
# the REGEN-2 replay runs ONCE, on the dedicated ubuntu-3.12 matrix cell).
ANCHOR_JOB_NAME = "test (ubuntu-latest, 3.12)"
ANCHOR_WORKFLOW = "test.yml"
_PROBE_TIMEOUT_S = 5.0

# A command runner returns stdout on success and raises on ANY failure
# (missing executable, non-zero exit, timeout). Injectable for tests.
CommandRunner = Callable[[list[str]], str]


class _ProbeFailure(RuntimeError):
    """Internal: a probe subprocess failed — callers degrade to unknown."""


def _default_runner(cmd: list[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, shell=False
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_PROBE_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # FileNotFoundError (no gh/git), CalledProcessError, TimeoutExpired…
        raise _ProbeFailure(f"{type(exc).__name__}: {exc}") from exc
    return completed.stdout


@dataclass(frozen=True)
class AnchorIdentity:
    """Local facts about the baseline file. None = honestly unknown."""

    sha8: str | None            # normalized sha256, first 8 hex
    signed_at: str | None       # last-touch commit date (ISO, as git reports)
    signed_commit: str | None   # last-touch commit short sha
    evidence_present: bool


@dataclass(frozen=True)
class CiLegStatus:
    """The CI anchor-leg conclusion, or an explicit unknown with a reason."""

    conclusion: str   # "success" / "failure" / … / "unknown"
    url: str | None   # run URL when resolved
    detail: str       # honest context ("" when the leg resolved cleanly)


def normalized_sha256(path: Path) -> str:
    """sha256 over CRLF->LF-normalized bytes — checkout-stable for text files.

    MUST stay byte-identical to the anchor test's ``_normalized_sha256``
    (tests/regression/test_walk_forward_replay_baseline_regen2.py) so the
    badge and the pin can never disagree about what "the same file" means.
    """
    return hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def baseline_identity(
    *,
    baseline_path: Path | None = None,
    run: CommandRunner | None = None,
) -> AnchorIdentity:
    """Resolve the baseline's local identity, degrading field-by-field.

    The re-sign date comes from ``git log -1`` on the baseline file — on a
    shallow clone (CI checkout depth 1) or without git this is unresolvable
    and reported as None, never guessed.
    """
    path = baseline_path if baseline_path is not None else BASELINE_PATH
    runner = run if run is not None else _default_runner
    sha8: str | None
    try:
        sha8 = normalized_sha256(path)[:8]
    except OSError:
        sha8 = None
    signed_at: str | None = None
    signed_commit: str | None = None
    try:
        stdout = runner([
            "git", "-C", str(path.parent), "log", "-1",
            "--format=%cI %h", "--", str(path),
        ]).strip()
    except _ProbeFailure:
        stdout = ""
    if stdout:
        parts = stdout.split()
        if len(parts) == 2:
            signed_at, signed_commit = parts[0], parts[1]
    return AnchorIdentity(
        sha8=sha8,
        signed_at=signed_at,
        signed_commit=signed_commit,
        evidence_present=evidence_sidecar_for(path).is_file(),
    )


def ci_leg_status(*, run: CommandRunner | None = None) -> CiLegStatus:
    """Latest completed anchor-leg conclusion on main, via the gh CLI.

    Two hops: the newest completed ``test.yml`` run, then its jobs to find
    :data:`ANCHOR_JOB_NAME`. Any failure at any hop degrades to "unknown"
    with the reason; a missing anchor job name falls back to the whole run's
    conclusion, saying so.
    """
    runner = run if run is not None else _default_runner
    try:
        listed_raw = runner([
            "gh", "run", "list", "--workflow", ANCHOR_WORKFLOW,
            "--branch", "main", "--status", "completed", "--limit", "1",
            "--json", "databaseId,conclusion,url",
        ])
    except _ProbeFailure as exc:
        return CiLegStatus("unknown", None, f"gh 不可用:{exc}")
    try:
        listed = json.loads(listed_raw)
    except json.JSONDecodeError as exc:
        return CiLegStatus("unknown", None, f"gh 输出不可解析:{exc}")
    if not isinstance(listed, list) or not listed:
        return CiLegStatus("unknown", None, "main 上无已完成的 test.yml run")
    head = listed[0]
    run_url = str(head.get("url")) if head.get("url") else None
    run_conclusion = str(head.get("conclusion") or "unknown")
    run_id = head.get("databaseId")
    if run_id is None:
        # No run id → the second hop is impossible, so the anchor leg was never
        # inspected. Presenting the whole-run conclusion as the leg's would be a
        # fabricated leg state (spec: degrade to explicit unknown). Keep the run
        # url so the operator can still click through.
        return CiLegStatus("unknown", run_url, "run id 缺失,未能核对锚腿")
    try:
        jobs_raw = runner(["gh", "run", "view", str(run_id), "--json", "jobs"])
        jobs_payload = json.loads(jobs_raw)
    except (_ProbeFailure, json.JSONDecodeError) as exc:
        # A probe failure on the second hop (timeout / auth / bad JSON) means the
        # anchor leg was never inspected — per spec this SHALL degrade the CI
        # element to explicit unknown, NOT the whole-run conclusion labelled as
        # the leg (codex #335 P2). The run-conclusion fallback below is reserved
        # for the one case where the jobs list resolved cleanly but the anchor
        # job name is genuinely absent.
        return CiLegStatus(
            "unknown", run_url,
            f"取不到 job 明细({exc}),未能核对锚腿",
        )
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict) and job.get("name") == ANCHOR_JOB_NAME:
                return CiLegStatus(
                    str(job.get("conclusion") or "unknown"), run_url, "",
                )
    # Jobs resolved but the anchor leg is not separately named — the only
    # sanctioned fall-through to the whole-run conclusion, and we say so.
    return CiLegStatus(
        run_conclusion, run_url,
        f"未找到锚腿 job({ANCHOR_JOB_NAME}),已用整 run 结论",
    )

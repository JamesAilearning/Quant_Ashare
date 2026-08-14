"""Read-only probe for the annual re-certification state and its clock.

The runbook is explicit that the operator MUST NOT substitute an assertion
for the machine check ("执行器会机器校验,操作人无须也**不得**以口头断言替代",
docs/csi800-n5-production-runbook.md). So this module never states the
verdict or the expiry from a constant, a cache, or a human-entered value —
it runs the executor's OWN parser and validity function
(``scripts.rotation_lib``) over bytes read from the mainline.

Two things it deliberately does NOT do:

* **Guess.** Every failure — git missing, ``origin/main`` unfetched, an
  unparsable artifact — degrades to an explicit "unknown" carrying the
  reason. A page that silently showed the last good answer, or defaulted to
  "valid", would be asserting a certification state nobody verified.
* **Read the mainline twice.** ``origin/main`` is a moving ref: resolving it
  once and then reading the artifact body and the tip date under that single
  pinned rev is what keeps an old WIN body from being dated by a newer
  commit. The pinned rev is reported so the operator can judge whether their
  local ``origin/main`` is fresh enough to trust.

No Streamlit imports: plain, unit-testable Python.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from scripts.rotation_lib import (
    RECERT_STATUS_PATH,
    VALIDITY_MONTHS,
    git_resolve_mainline_cmd,
    git_show_status_cmd,
    git_status_tip_cmd,
    parse_recert_status,
    recert_validity,
)

_PROBE_TIMEOUT_S = 5.0

# Returns stdout on success, raises on ANY failure (missing executable,
# non-zero exit, timeout). Injectable so tests never touch a real repo.
CommandRunner = Callable[[list[str]], str]


class _ProbeFailure(RuntimeError):
    """Internal: a git probe failed — callers degrade to unknown."""


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
        raise _ProbeFailure(f"{type(exc).__name__}: {exc}") from exc
    return completed.stdout


@dataclass(frozen=True)
class RecertHealth:
    """The certification state as the ROTATION EXECUTOR would see it.

    ``known`` False means the probe could not establish the state. That is
    reported as its own outcome — never as "valid", never as a stale
    previous answer.
    """

    known: bool
    verdict: str | None = None          # "WIN" | "LOSE" — never invented
    rotation_allowed: bool | None = None
    reason: str = ""
    pinned_rev: str | None = None       # the single mainline rev both reads used
    status_tip_iso: str | None = None   # the 15-month clock's anchor
    status_path: str = RECERT_STATUS_PATH
    validity_months: int = VALIDITY_MONTHS

    @property
    def is_frozen(self) -> bool:
        """Rotation is provably frozen (as opposed to merely unknown)."""
        return self.known and self.rotation_allowed is False


def executor_now_iso() -> str:
    """The evaluation instant the ROTATION EXECUTOR uses: UTC.

    ``rotate_ensemble_member.py`` passes ``datetime.now(tz=timezone.utc)``
    and ``recert_validity`` compares ``now.date()`` WITHOUT normalizing
    zones, so the clock's offset decides which calendar day the expiry is
    judged against. Handing it a ``+08:00`` instant makes this page disagree
    with the executor for eight hours around the boundary — reporting
    rotation frozen while the executor would still permit it — and a page
    that claims to transcribe the machine decision must not invent its own
    (codex #431 r2).
    """
    return datetime.now(tz=timezone.utc).isoformat()


def probe_recert_health(
    *, now_iso: str | None = None, run: CommandRunner | None = None,
) -> RecertHealth:
    """Run the executor's own checks over the mainline status artifact.

    ``now_iso`` defaults to the executor's own UTC clock; tests inject a
    fixture. The default is deliberately not a caller's choice: every
    caller getting it right is a weaker guarantee than there being nothing
    to get wrong.
    """
    now_iso = now_iso if now_iso is not None else executor_now_iso()
    runner = run or _default_runner
    try:
        rev = runner(git_resolve_mainline_cmd()).strip()
    except Exception as exc:  # any probe failure → unknown
        return RecertHealth(
            known=False,
            reason=f"无法解析主线 rev(origin/main 未 fetch 或 git 不可用):{exc}")
    if not rev:
        return RecertHealth(known=False, reason="git 返回了空的主线 rev")

    # Both reads use THIS rev. Re-resolving origin/main between them could
    # pair an old status body with a newer commit's date.
    try:
        body = runner(git_show_status_cmd(rev))
    except Exception as exc:  # any probe failure → unknown
        return RecertHealth(
            known=False, pinned_rev=rev,
            reason=f"无法在 rev {rev[:12]} 上读取 {RECERT_STATUS_PATH}:{exc}")
    try:
        tip_iso = runner(git_status_tip_cmd(rev)).strip()
    except Exception as exc:  # any probe failure → unknown
        return RecertHealth(
            known=False, pinned_rev=rev,
            reason=f"无法读取状态工件路径的 tip commit 日期:{exc}")
    if not tip_iso:
        return RecertHealth(
            known=False, pinned_rev=rev,
            reason=f"{RECERT_STATUS_PATH} 在 rev {rev[:12]} 上没有 commit 历史")

    try:
        status: dict[str, Any] = parse_recert_status(body)
    except Exception as exc:  # RotationRefusal and any parse error
        # The executor's parser is the authority on what a valid status
        # artifact is; if it refuses, the UI must not second-guess it into
        # a displayable verdict.
        return RecertHealth(
            known=False, pinned_rev=rev, status_tip_iso=tip_iso,
            reason=f"状态工件被规范解析器拒绝:{type(exc).__name__}: {exc}")

    allowed, reason = recert_validity(status, tip_iso, now_iso)
    return RecertHealth(
        known=True,
        verdict=str(status.get("verdict")),
        rotation_allowed=allowed,
        reason=reason,
        pinned_rev=rev,
        status_tip_iso=tip_iso,
    )

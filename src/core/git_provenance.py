"""Git provenance of the CODE that produced a run — shared by BOTH engines.

``pipeline_report.json`` and ``walk_forward_report.json`` are parallel artifacts
(two engines, one schema): both record the same top-level ``git_commit`` +
``git_dirty`` fields via this single helper, so the run-comparison
pre-registration gate can prove (topologically, ``git merge-base --is-ancestor``)
that a pre-registered hypothesis commit PREDATES a run — provable from git
history, not trusted to a timestamp — through one provenance path regardless of
which engine produced the run.
"""
from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]


def capture_git_provenance() -> dict[str, str | bool | None]:
    """Best-effort git HEAD (full sha) + working-tree-dirty flag of this repo.

    Runs generated before this field simply lack it (the pre-registration gate
    then fails loud on that run rather than trusting an absent commit).

    NEVER raises. The two probes are guarded SEPARATELY (codex P2 on #313): if
    ``rev-parse HEAD`` succeeds but the dirty probe fails (e.g. ``git status``
    times out on a large/locked worktree), the commit is KEPT and only ``dirty``
    degrades to ``None`` — otherwise a run from a valid checkout would lose its
    ``git_commit`` and the pre-registration ancestor gate would reject it even
    though the commit was available. ``{'commit': None, 'dirty': None}`` only
    when git or a repo is unavailable (detached bundle env, no ``.git``, git not
    on PATH, a timeout on rev-parse itself)."""
    try:
        # BINARY + safe decode, like the status probe below: the
        # inherited environment can point GIT_DIR at a non-UTF-8 path
        # that git echoes on stderr, and a strict decode would raise
        # past this function's never-raise contract (#410 r61).
        commit: str | None = subprocess.run(
            ["git", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=/dev/null",
             "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, timeout=5, check=True,
        ).stdout.decode("utf-8", errors="replace").strip() or None
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    try:
        # BINARY + explicit decode: an attributes ``clean`` filter runs
        # when stat info cannot settle a comparison, and its stderr
        # lands in the captured stream — decoding that as UTF-8 would
        # raise past this function's never-raise contract (#410 r37).
        status = subprocess.run(
            ["git", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=/dev/null",
             "-c", "core.quotePath=true",
             "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True, timeout=5, check=True,
        ).stdout.decode("utf-8", errors="replace")
        dirty: bool | None = bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = None
    return {"commit": commit, "dirty": dirty}


def resolve_run_git_provenance(
    per_fold: Sequence[Mapping[str, Any] | None],
) -> dict[str, str | bool | None]:
    """Resolve ONE report-level provenance from per-fold provenance.

    A resumed walk-forward run can mix folds produced by DIFFERENT commits
    (folds generated at commit A, run resumed at commit B). Stamping the
    current invocation's HEAD on such a report would let a pre-registration
    plan committed between A and B falsely appear to predate the fold
    artifacts (codex P1 on #313, round 4). So:

    - every fold agrees on one known commit -> that commit; ``dirty`` is True
      if ANY fold was dirty, None if any fold's dirty is unknown, else False;
    - mixed commits, or ANY fold with unknown provenance (a legacy manifest
      written before provenance stamping, or a capture that failed) -> BOTH
      fields None. The ancestor gate then fails loud on the run instead of
      trusting a commit that did not produce every fold. NEVER guesses.
    """
    commits = {(p or {}).get("commit") for p in per_fold}
    if len(commits) != 1 or None in commits:
        return {"commit": None, "dirty": None}
    dirties = [(p or {}).get("dirty") for p in per_fold]
    dirty: bool | None
    if any(d is True for d in dirties):
        dirty = True
    elif any(d is None for d in dirties):
        dirty = None
    else:
        dirty = False
    return {"commit": commits.pop(), "dirty": dirty}

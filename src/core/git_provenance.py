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
from pathlib import Path

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
        commit: str | None = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    try:
        status = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        dirty: bool | None = bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        dirty = None
    return {"commit": commit, "dirty": dirty}

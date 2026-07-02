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

    NEVER raises: returns ``{'commit': None, 'dirty': None}`` if git or a repo
    is unavailable (a detached bundle env, no ``.git``, git not on PATH, a
    timeout)."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        return {"commit": commit or None, "dirty": bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}

"""Atomic two-stage swap of the qlib provider bundle (P3-6a).

The daily update builds a full bundle into ``<provider>.new`` and only after
validation promotes it over the live ``<provider>``:

    stage 1:  <provider>      ->  <provider>.bak    (only if a live one exists)
    stage 2:  <provider>.new  ->  <provider>

Each stage is a single same-volume directory rename (atomic on NTFS/POSIX), so
a crash can interrupt only BETWEEN stages, never mid-copy — qlib readers never
see a half-written bundle. ``<provider>.bak`` is kept after a successful swap as
an instant manual rollback; the NEXT swap clears it.

``check_and_repair`` runs at orchestrator startup and resolves every reachable
crash state: a swap interrupted between the stages is COMPLETED (stage 1 having
run proves validation had passed); a live bundle lost with only the backup left
is RESTORED; a stale ``.new`` from a run that died before its swap is REMOVED
(it cannot be proven validated, and the next run rebuilds it). Anything else is
either healthy or loudly unrepairable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.core.logger import get_logger

_logger = get_logger(__name__)


class BundleSwapError(RuntimeError):
    """A swap precondition or repair failed — fail loud, never half-swap."""


def new_dir(provider_dir: Path) -> Path:
    """The staging sibling the rebuild writes into (``<provider>.new``)."""
    return provider_dir.with_name(provider_dir.name + ".new")


def bak_dir(provider_dir: Path) -> Path:
    """The rollback sibling kept after a swap (``<provider>.bak``)."""
    return provider_dir.with_name(provider_dir.name + ".bak")


def check_and_repair(provider_dir: Path, *, dry_run: bool = False) -> str:
    """Detect (and unless ``dry_run`` repair) a crash-interrupted prior swap.

    Returns the action taken: ``"healthy"`` (nothing to do),
    ``"completed-interrupted-swap"``, ``"restored-from-backup"``, or
    ``"removed-stale-new"``. With ``dry_run`` the action is only REPORTED —
    nothing on disk moves.
    """
    new_p, bak_p = new_dir(provider_dir), bak_dir(provider_dir)
    if not provider_dir.exists():
        if bak_p.exists():
            if new_p.exists():
                # Crash BETWEEN stage 1 and stage 2: the backup rename happened,
                # which proves validation had passed — complete the swap.
                if not dry_run:
                    new_p.rename(provider_dir)
                    _logger.warning(
                        "Repaired an interrupted swap: completed %s -> %s "
                        "(backup kept at %s).", new_p, provider_dir, bak_p,
                    )
                return "completed-interrupted-swap"
            # Backup exists but both live and .new are gone — restore the
            # backup so the system has a working bundle again.
            if not dry_run:
                bak_p.rename(provider_dir)
                _logger.warning(
                    "Restored the live bundle from backup %s (no live bundle "
                    "and no staged .new were present).", bak_p,
                )
            return "restored-from-backup"
        if new_p.exists():
            # No live bundle, no backup, only a .new: a first-ever build died
            # before its swap. It cannot be PROVEN validated -> remove; the
            # next run rebuilds it.
            if not dry_run:
                shutil.rmtree(new_p)
                _logger.warning(
                    "Removed stale staging dir %s from an interrupted first "
                    "build (cannot prove it was validated).", new_p,
                )
            return "removed-stale-new"
        return "healthy"  # nothing exists yet — first run ever
    if new_p.exists():
        # Live bundle is fine but a prior run died after building (or after a
        # failed validate) leaving its staging behind. Remove it loudly — it
        # cannot be proven validated, and this run rebuilds it anyway.
        if not dry_run:
            shutil.rmtree(new_p)
            _logger.warning(
                "Removed stale staging dir %s left by an interrupted prior "
                "run (the live bundle %s is untouched).", new_p, provider_dir,
            )
        return "removed-stale-new"
    return "healthy"


def swap(provider_dir: Path) -> None:
    """Promote a validated ``<provider>.new`` over the live bundle.

    Two atomic renames; the backup of the previous live bundle is KEPT for
    manual rollback (cleared at the start of the next swap). Refuses (raises)
    if the staging dir is missing — the caller must have built + validated it.
    """
    new_p, bak_p = new_dir(provider_dir), bak_dir(provider_dir)
    if not new_p.exists():
        raise BundleSwapError(
            f"Cannot swap: staged bundle {new_p} does not exist. Build + "
            "validate must succeed before the swap stage."
        )
    if bak_p.exists():
        # Clear the previous rollback generation. If we crash right after this
        # rmtree, the state (live + .new, no .bak) is exactly the pre-swap
        # state — a re-run repairs nothing and swaps again. Safe.
        shutil.rmtree(bak_p)
    if provider_dir.exists():
        provider_dir.rename(bak_p)  # stage 1
    new_p.rename(provider_dir)      # stage 2
    _logger.info(
        "Swapped %s into place (previous bundle kept at %s).",
        provider_dir, bak_p if bak_p.exists() else "<none — first deploy>",
    )

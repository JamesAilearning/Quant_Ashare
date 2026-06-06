"""Fold-level resume support for ``WalkForwardEngine``.

Each successful fold writes a small ``fold_{i:02d}_manifest.json``
alongside its existing artifacts (model pickle, report JSON,
predictions pickle, positions JSON). On the next ``WalkForwardEngine.run``
invocation, the engine scans these manifests and skips folds whose
manifest matches the current config + window — turning an "all or
nothing" walk-forward into one that makes incremental progress across
restarts.

See ``openspec/changes/add-walk-forward-fold-resume/`` for the full
contract.

This module deliberately has **zero qlib imports** and **zero engine
imports** — it's pure dataclass + JSON arithmetic so engine.py can
import it without creating cycles.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from src.core.walk_forward._types import WalkForwardFold

_log = logging.getLogger(__name__)

MANIFEST_VERSION = 1


# ---------------------------------------------------------------------------
# ResumeMode — tagged union for the three resume policies
# ---------------------------------------------------------------------------


class _ResumeKind(str, Enum):
    AUTO = "auto"
    FORCE_RERUN = "force_rerun"
    RESUME_FROM_FOLD = "resume_from_fold"


@dataclass(frozen=True)
class ResumeMode:
    """Tagged union for the three resume policies.

    Construct via the class-level constants :data:`ResumeMode.AUTO`
    and :data:`ResumeMode.FORCE_RERUN`, or via the factory method
    :meth:`from_fold` for the bounded variant. Direct instantiation is
    permitted but the constants are the documented entry points.
    """

    kind: _ResumeKind
    from_fold_index: int | None = None

    # Class-level singletons populated below the class body. Declared
    # here so type-checkers (and IDEs) see ``ResumeMode.AUTO`` /
    # ``ResumeMode.FORCE_RERUN`` as documented attributes.
    AUTO: ClassVar[ResumeMode]
    FORCE_RERUN: ClassVar[ResumeMode]

    @classmethod
    def auto(cls) -> ResumeMode:
        return cls(kind=_ResumeKind.AUTO)

    @classmethod
    def force_rerun(cls) -> ResumeMode:
        return cls(kind=_ResumeKind.FORCE_RERUN)

    @classmethod
    def from_fold(cls, n: int) -> ResumeMode:
        if not isinstance(n, int) or n < 0:
            raise ValueError(
                f"from_fold N must be a non-negative int, got {n!r}"
            )
        return cls(kind=_ResumeKind.RESUME_FROM_FOLD, from_fold_index=n)

    def should_force_rerun(self, fold_index: int) -> bool:
        """Whether fold ``fold_index`` must re-run regardless of any
        existing manifest."""
        if self.kind == _ResumeKind.FORCE_RERUN:
            return True
        if (
            self.kind == _ResumeKind.RESUME_FROM_FOLD
            and self.from_fold_index is not None
            and fold_index >= self.from_fold_index
        ):
            return True
        return False


# Convenience class-level constants matching the spec's reference names.
# The ``ClassVar`` declarations on the dataclass above tell type-
# checkers these exist; the assignments here populate them at import
# time. (``ClassVar`` keeps them out of ``__init__`` parameters.)
ResumeMode.AUTO = ResumeMode.auto()
ResumeMode.FORCE_RERUN = ResumeMode.force_rerun()


# ---------------------------------------------------------------------------
# Config fingerprint
# ---------------------------------------------------------------------------


_FINGERPRINT_EXCLUDE_FIELDS: frozenset[str] = frozenset({"output_dir"})


def compute_config_fingerprint(config: Any) -> str:
    """Return a short sha256 hex digest identifying the config.

    Excludes :data:`_FINGERPRINT_EXCLUDE_FIELDS` so renaming the
    output directory does NOT invalidate a resume. Any other field
    change (train_months, topk, model_type, …) produces a different
    fingerprint and triggers a re-run of all folds.
    """
    # ``is_dataclass`` is True for both classes AND instances; we
    # only want instances (``asdict`` raises on a class). Explicit
    # type narrowing satisfies mypy's strict mode and surfaces a
    # caller passing ``WalkForwardConfig`` (the class) instead of
    # ``WalkForwardConfig(...)`` (an instance).
    if not dataclasses.is_dataclass(config) or isinstance(config, type):
        raise TypeError(
            f"config must be a dataclass INSTANCE; got "
            f"{type(config).__name__}"
        )
    raw = dataclasses.asdict(config)
    for key in _FINGERPRINT_EXCLUDE_FIELDS:
        raw.pop(key, None)
    # Fold the namechange snapshot's CONTENT (not just its path) into the
    # fingerprint. Each fold's ST-masked metrics depend on the parquet's
    # contents, so a re-fetched all_namechanges.parquet at the SAME path MUST
    # invalidate resume rather than reuse stale ST-mask folds — the path string
    # alone (already in ``raw``) would not change (Codex P1 on #223). The
    # backtest's own provenance hashes the file after a fold runs, but this
    # resume/skip decision happens before that.
    namechange_path = raw.get("namechange_path")
    if isinstance(namechange_path, str) and namechange_path.strip():
        nc_file = Path(namechange_path)
        raw["namechange_content_sha256"] = (
            hashlib.sha256(nc_file.read_bytes()).hexdigest()
            if nc_file.is_file()
            else "MISSING"
        )
    payload = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# FoldManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldManifest:
    """One fold's resumable state.

    Persisted as ``output_dir/fold_{fold_index:02d}_manifest.json``.
    Carries enough metadata to reconstruct the engine's in-memory
    ``WalkForwardFold`` + the ensemble's ``prior_model_paths`` entry
    without re-running the fold.
    """

    version: int
    fold_index: int
    train_period: str
    valid_period: str
    test_period: str
    config_fingerprint: str
    model_path: str
    report_path: str
    predictions_path: str
    positions_path: str | None
    completed_at: str
    fold: WalkForwardFold

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_fold(
        cls,
        *,
        fold: WalkForwardFold,
        config: Any,
        model_path: str,
        report_path: str,
        predictions_path: str,
        positions_path: str | None,
    ) -> FoldManifest:
        # Store basenames only — paths are location-independent so a
        # ``output_dir`` rename between the original run and the resume
        # doesn't strand the manifest. The engine rebases via
        # :meth:`with_paths_rebased` (called by ``discover``). Codex P1
        # on PR #147: previously serialised absolute paths caused
        # ``apply_ensemble`` to load a stale (or nonexistent) pickle when
        # the run directory had been moved, silently dropping the prior
        # model from the ensemble.
        return cls(
            version=MANIFEST_VERSION,
            fold_index=fold.fold_index,
            train_period=fold.train_period,
            valid_period=fold.valid_period,
            test_period=fold.test_period,
            config_fingerprint=compute_config_fingerprint(config),
            model_path=Path(model_path).name,
            report_path=Path(report_path).name,
            predictions_path=Path(predictions_path).name,
            positions_path=(
                Path(positions_path).name if positions_path else None
            ),
            completed_at=datetime.now(tz=timezone.utc).isoformat(),
            fold=fold,
        )

    # ------------------------------------------------------------------
    # Path rebasing — see Codex P1 on PR #147
    # ------------------------------------------------------------------

    @staticmethod
    def _rebase_path(stored: str, output_dir: Path) -> str:
        """Join the basename of ``stored`` against ``output_dir``.

        Accepts both the new manifest shape (``stored`` is a basename)
        and legacy manifests written before the relativisation fix
        (``stored`` is an absolute path from the run that produced it).
        ``Path(...).name`` extracts the basename from either form, so
        rejoining against the current ``output_dir`` produces a path
        that points at the right file whether the run directory was
        renamed, moved, or left alone.
        """
        return str(output_dir / Path(stored).name)

    def with_paths_rebased(self, output_dir: Path | str) -> FoldManifest:
        """Return a copy with all file-path fields joined to ``output_dir``.

        ``discover`` calls this on every loaded manifest so the engine
        always sees absolute paths against the directory it is
        currently scanning — even if the manifest itself was written
        when that directory had a different name.
        """
        d = Path(output_dir)
        return dataclasses.replace(
            self,
            model_path=self._rebase_path(self.model_path, d),
            report_path=self._rebase_path(self.report_path, d),
            predictions_path=self._rebase_path(self.predictions_path, d),
            positions_path=(
                self._rebase_path(self.positions_path, d)
                if self.positions_path
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        fold_dict = dataclasses.asdict(self.fold)
        # WalkForwardFold has prediction_shape: tuple[int, ...]. JSON
        # has no tuple — round-trips through list. Normalize here so
        # the round-trip is byte-stable.
        if "prediction_shape" in fold_dict:
            fold_dict["prediction_shape"] = list(fold_dict["prediction_shape"])
        return {
            "version": self.version,
            "fold_index": self.fold_index,
            "train_period": self.train_period,
            "valid_period": self.valid_period,
            "test_period": self.test_period,
            "config_fingerprint": self.config_fingerprint,
            "model_path": self.model_path,
            "report_path": self.report_path,
            "predictions_path": self.predictions_path,
            "positions_path": self.positions_path,
            "completed_at": self.completed_at,
            "fold": fold_dict,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FoldManifest:
        fold_payload = dict(payload["fold"])
        if "prediction_shape" in fold_payload and isinstance(
            fold_payload["prediction_shape"], list
        ):
            fold_payload["prediction_shape"] = tuple(fold_payload["prediction_shape"])
        fold = WalkForwardFold(**fold_payload)
        return cls(
            version=int(payload["version"]),
            fold_index=int(payload["fold_index"]),
            train_period=str(payload["train_period"]),
            valid_period=str(payload["valid_period"]),
            test_period=str(payload["test_period"]),
            config_fingerprint=str(payload["config_fingerprint"]),
            model_path=str(payload["model_path"]),
            report_path=str(payload["report_path"]),
            predictions_path=str(payload["predictions_path"]),
            positions_path=(
                str(payload["positions_path"])
                if payload.get("positions_path")
                else None
            ),
            completed_at=str(payload["completed_at"]),
            fold=fold,
        )

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @staticmethod
    def path_for(output_dir: Path | str, fold_index: int) -> Path:
        return Path(output_dir) / f"fold_{fold_index:02d}_manifest.json"

    def save(self, output_dir: Path | str) -> Path:
        """Write the manifest atomically (tmp + rename) so a crash
        mid-write doesn't leave a half-written file that ``discover``
        would parse incorrectly."""
        target = self.path_for(output_dir, self.fold_index)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return target

    @classmethod
    def load(cls, output_dir: Path | str, fold_index: int) -> FoldManifest:
        path = cls.path_for(output_dir, fold_index)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def discover(
        cls,
        output_dir: Path | str,
        *,
        verify_artifacts: bool = True,
    ) -> dict[int, FoldManifest]:
        """Scan ``output_dir`` for ``fold_*_manifest.json`` files.

        Malformed JSON or schema-mismatched manifests are skipped with
        a WARNING log — they don't abort the resume scan, but they
        also don't contribute to the skip set, so the corresponding
        fold will re-run.

        When ``verify_artifacts`` is True (the default), manifests
        whose ``model_path`` / ``report_path`` / ``predictions_path``
        don't exist on disk are also skipped with a WARNING. Without
        this check, an operator who deleted or moved per-fold
        artifacts (cleanup script, accidental ``rm``, half-finished
        archive operation) would see the engine "resume" a fold whose
        model pickle no longer exists — the ensemble loader would
        then crash later, or worse, silently fall back to fewer
        models. Audit P2 regression.

        Tests that want to inspect the discover internals on a
        manifest without the artifact files can pass
        ``verify_artifacts=False``.
        """
        d = Path(output_dir)
        if not d.is_dir():
            return {}
        out: dict[int, FoldManifest] = {}
        for path in sorted(d.glob("fold_*_manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = cls.from_dict(payload)
            except (
                json.JSONDecodeError,
                KeyError,
                ValueError,
                TypeError,
            ) as exc:
                _log.warning(
                    "skipping malformed fold manifest %s: %s: %s",
                    path, type(exc).__name__, exc,
                )
                continue
            if manifest.version != MANIFEST_VERSION:
                _log.warning(
                    "skipping manifest %s with unsupported version %d "
                    "(expected %d)",
                    path, manifest.version, MANIFEST_VERSION,
                )
                continue
            # Rebase stored paths against ``output_dir`` so the engine
            # consumes absolute paths regardless of whether the run
            # directory was renamed between the original run and this
            # resume (Codex P1 on PR #147).
            rebased = manifest.with_paths_rebased(d)
            if verify_artifacts:
                missing = _missing_required_artifacts(rebased)
                if missing:
                    _log.warning(
                        "skipping fold %d manifest at %s — required "
                        "artifact(s) missing on disk: %s. The fold "
                        "will be re-run on the next resume.",
                        rebased.fold_index, path, ", ".join(missing),
                    )
                    continue
            out[manifest.fold_index] = rebased
        return out


def _missing_required_artifacts(manifest: FoldManifest) -> list[str]:
    """Return the names of any required artifact paths whose files are
    missing or unreadable. Used by ``discover(verify_artifacts=True)``.

    ``positions_path`` is **not** required — the backtest skips
    writing it when there are no positions, and the engine writes
    the manifest with ``positions_path=None`` in that case.
    ``predictions_path`` IS required for ensemble loading;
    ``model_path`` is required for prior-model averaging; ``report_path``
    is required for downstream comparisons / dashboards.
    """
    missing: list[str] = []
    for label, value in (
        ("model_path", manifest.model_path),
        ("report_path", manifest.report_path),
        ("predictions_path", manifest.predictions_path),
    ):
        if not value or not Path(value).is_file():
            missing.append(label)
    return missing


# ---------------------------------------------------------------------------
# Resume decision matrix — pure function so it's easy to test in isolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumeDecision:
    """Outcome of evaluating one fold against the resume policy."""

    fold_index: int
    skip: bool
    """True → load from manifest; False → run the fold normally."""
    manifest: FoldManifest | None
    """Populated when ``skip`` is True; None when run-fresh."""
    reason: str
    """Human-readable label for logging."""


def decide_fold(
    *,
    fold_index: int,
    train_period: str,
    test_period: str,
    config_fingerprint: str,
    discovered: Mapping[int, FoldManifest],
    resume_mode: ResumeMode,
    valid_period: str | None = None,
) -> ResumeDecision:
    """Apply the resume policy to one fold.

    Pure: no I/O. The engine constructs the inputs and calls this once
    per window; the returned :class:`ResumeDecision` drives whether
    the fold runs or is loaded from manifest.

    ``valid_period`` is optional for backward compatibility; when supplied
    it is included in the window-mismatch check. This matters since the
    embargo-gap change made ``valid_end`` calendar-dependent: a different
    bundle vintage can shift ``valid_end`` (hence ``valid_period``) while
    ``train_period`` / ``test_period`` stay equal, and without this check
    AUTO-resume would wrongly reuse a fold trained on the old valid window.
    """
    if resume_mode.should_force_rerun(fold_index):
        reason = (
            "force_rerun"
            if resume_mode.kind == _ResumeKind.FORCE_RERUN
            else f"resume_from_fold_{resume_mode.from_fold_index}"
        )
        return ResumeDecision(
            fold_index=fold_index, skip=False, manifest=None, reason=reason,
        )

    manifest = discovered.get(fold_index)
    if manifest is None:
        return ResumeDecision(
            fold_index=fold_index, skip=False, manifest=None,
            reason="no_manifest",
        )

    if manifest.config_fingerprint != config_fingerprint:
        return ResumeDecision(
            fold_index=fold_index, skip=False, manifest=None,
            reason=(
                f"fingerprint_mismatch:"
                f"manifest={manifest.config_fingerprint[:8]} "
                f"current={config_fingerprint[:8]}"
            ),
        )

    if (
        manifest.train_period != train_period
        or manifest.test_period != test_period
        or (valid_period is not None and manifest.valid_period != valid_period)
    ):
        return ResumeDecision(
            fold_index=fold_index, skip=False, manifest=None,
            reason=(
                f"window_mismatch:"
                f"manifest=({manifest.train_period},{manifest.valid_period},"
                f"{manifest.test_period}) "
                f"current=({train_period},{valid_period},{test_period})"
            ),
        )

    return ResumeDecision(
        fold_index=fold_index, skip=True, manifest=manifest,
        reason="resumed_from_manifest",
    )


__all__ = [
    "MANIFEST_VERSION",
    "FoldManifest",
    "ResumeDecision",
    "ResumeMode",
    "compute_config_fingerprint",
    "decide_fold",
]

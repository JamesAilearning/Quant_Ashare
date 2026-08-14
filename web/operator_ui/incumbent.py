"""Which model production is ACTUALLY serving — the one shared answer.

Lives at package level (beside ``bundle_health``/``anchor_health``) rather
than inside one page's helpers because MORE THAN ONE page asks the question.
Two copies of this logic could disagree — 今日推荐 naming one model while
生产运维 names another — and a page whose entire purpose is telling the
operator "whose advice is this" cannot be the thing that disagrees with
itself.

No Streamlit imports: plain, unit-testable Python.
"""

from __future__ import annotations

import os
import os.path
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from scripts import rotate_ensemble_member as _rotate

# The checkout — the executor's OWN constant, not a fourth derivation of the
# same directory (codex #431 r23/r26).
PROJECT_ROOT = _rotate.PROJECT_ROOT


def _is_absolute_under_either_convention(path: str) -> bool:
    """Absolute as Windows OR as POSIX reads it."""
    return (PureWindowsPath(path).is_absolute()
            or PurePosixPath(path).is_absolute())


# Whether a spelling is FULLY QUALIFIED on this host — drive + root on
# Windows, a leading `/` on POSIX. Bound once so tests can substitute the
# other platform's rule without mutating `os.path`, which on Windows IS
# `ntpath`: a global swap also rewrites the very function a test imports to
# restore Windows semantics, which made an "as POSIX" simulation silently
# unsound (codex #431 r31).
#
# NOT `os.path.isabs`. On Windows `ntpath.isabs("/srv/bundle")` is True even
# though the path is rooted on WHICHEVER DRIVE IS CURRENT — Streamlit started
# on `C:` inspects `C:\srvundle` while the command, run from a checkout on
# `D:`, reads `D:\srvundle`. Same page-says-one / command-runs-another
# split, one more spelling (codex #431 r32).
_HOST_PURE = PureWindowsPath if os.name == "nt" else PurePosixPath


def _host_is_fully_qualified(path: str) -> bool:
    """Names exactly one location on this host, from any working directory."""
    return _HOST_PURE(path).is_absolute()


# Message shown when a spelling names nothing resolvable on THIS host.
FOREIGN_ABSOLUTE_REASON = (
    "该路径在本机**不是完全限定**的写法——要么是另一套约定下的绝对路径"
    "(如 Windows 的 `D:/…` 出现在 POSIX 主机上),要么是 Windows 上缺盘符的"
    "`/srv/…`(按**当前盘**解析)。两种情况下,不同工作目录的读取器会解析到"
    "不同位置,本页无法让它只指一处"
)


def foreign_absolute_reason(path: str) -> str | None:
    """Why this spelling cannot be made to mean one place here — or None.

    ``os.path.isabs`` answers for the HOST. A value like
    ``D:/qlib_data/my_cn_data_pit`` is absolute to Windows and **relative** to
    POSIX, so on Linux it does not "simply fail to read" (what r28 claimed):
    it silently resolves against whatever directory the reader happens to be
    in. Measured — Streamlit started in ``/tmp`` reads
    ``/tmp/D:/qlib_data/…`` while the command, run from the instructed
    repository root, reads ``<checkout>/D:/qlib_data/…``. That is exactly the
    page-says-one-bundle / command-runs-another split r27 was about, wearing
    a different spelling (codex #431 r30).

    Anchoring it would make the two agree on a nonsense location and send the
    operator chasing a missing bundle; passing it through keeps them
    disagreeing. Neither is usable, so this is reported and the value is
    refused rather than silently used.
    """
    if not path.strip():
        return None
    if _host_is_fully_qualified(path):
        return None
    return (FOREIGN_ABSOLUTE_REASON
            if _is_absolute_under_either_convention(path) else None)


def anchored_to_repo(path: str) -> str:
    """A path the UI both READS and PRINTS, resolved where the command runs.

    A ``provider_uri`` (or model / manifest path) may legitimately be
    RELATIVE. The page then reads it against Streamlit's working directory,
    while the command it prints carries the same relative spelling to a
    terminal the page tells the operator to open **at the repository root**.
    Those are two different bundles, and nothing downstream can detect the
    swap: the page would describe one and the command would run on the other
    (codex #431 r27).

    So relative paths are resolved against the checkout — the CWD the machine
    will actually have, per the runbook and per this page's own instruction.

    ``~`` is EXPANDED, and the expansion is what gets both read and printed.
    Returning the raw ``~/model.pkl`` would be read as a literal ``~``
    directory here, while the printed command — single-quoted, because this
    page quotes unconditionally (r17) — hands that same literal to Python
    with no shell expansion either. Two wrong answers that happen to differ
    from what the operator meant; the expanded form is the one thing both
    sides can agree on (codex #431 r28).

    Everything already unambiguous is returned UNTOUCHED: inventing a
    normalization the CLI does not share is the mistake r23 and r24 were
    about. A blank value is likewise untouched — the command boundary
    refuses it (r21).

    A FOREIGN absolute (``D:/…`` on POSIX) is also returned untouched, but it
    is NOT usable: see :func:`foreign_absolute_reason`. It keeps the
    operator's own spelling so the refusal can quote what they configured
    (codex #431 r30).
    """
    if not path.strip():
        return path
    expanded = os.path.expanduser(path)
    if expanded.startswith("~"):
        # expanduser could not resolve it (no HOME, or `~unknownuser`). Do
        # not guess and do not anchor a `~` as if it were a directory name.
        return path
    if foreign_absolute_reason(expanded) is not None:
        return expanded
    if _host_is_fully_qualified(expanded):
        return expanded
    return os.path.normpath(os.path.join(PROJECT_ROOT, expanded))

# The RETIRED single model — still the incumbent on a deployment that
# explicitly opted out of the ensemble. Mirrors the CLI default
# (scripts/daily_recommend._DEFAULT_MODEL) and docs/operations-env-vars.md.
ENV_MODEL_PATH = "QUANT_MODEL_PATH"
DEFAULT_MODEL_PATH = "D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl"


def resolve_model_path() -> str:
    """The production model path, with the CLI's EXACT env semantics.

    Raw ``os.environ.get(VAR, DEFAULT)`` — no ``.strip()``, and ``""`` is
    NOT treated as unset, because ``scripts/daily_recommend._DEFAULT_MODEL``
    does neither. The ops cockpit prints this value as an explicit
    ``--model`` flag, so a UI-side normalization the CLI does not share would
    make the page hand out a command that runs against a DIFFERENT artifact
    than the one it is describing — the flag overrides, so the divergence
    would take effect rather than merely mislead (codex #431 r24, same class
    as the name-source normalization removed in r23).
    """
    return os.environ.get(ENV_MODEL_PATH, DEFAULT_MODEL_PATH)


# The INCUMBENT ensemble manifest, read-side only. Production switched to a
# 3-member csi800 N5 ensemble on 2026-08-05; before this pointer existed the
# banner had no way to know that and kept describing the retired single model
# (docs/operations-env-vars.md explains why the CLI deliberately does NOT
# inherit this default).
ENV_ENSEMBLE_MANIFEST = "QUANT_ENSEMBLE_MANIFEST"
# Default = the production manifest written by the 2026-08-05 cutover, per
# this repo's env-var convention ("each QUANT_* default equals the historical
# hardcoded path, so behaviour is unchanged where they are unset").
#
# Treating an UNSET pointer as "production is a single model" would be a
# fabricated fact (codex #430 r1): on any deployment that upgrades the UI
# without also setting a new variable, the page would both keep showing the
# retired model AND tell the operator not to use the CORRECT ensemble lists.
# Absence of configuration is not evidence about what production serves.
DEFAULT_ENSEMBLE_MANIFEST = (
    "D:/stock/phase_b_artifacts/csi800_n5_ensemble_manifest.json"
)
# The documented opt-out for a deployment that genuinely serves ONE model:
# an explicit statement, never an inference from a missing variable.
SINGLE_MODEL_SENTINEL = "none"


@dataclass(frozen=True)
class IncumbentIdentity:
    """Which model production is ACTUALLY serving, as far as the UI can tell.

    Three states, and the third is the point:

    * ``ensemble``     — a manifest the SERVING validator accepts. Reached by
      the documented default (unset pointer) or an explicit path.
    * ``single``       — reached ONLY by the explicit ``none`` opt-out. An
      unset pointer is NOT this state: "nobody configured this box" is not
      evidence that production retired the ensemble (codex #430 r1).
    * ``unresolvable`` — a pointer that resolves to something the validator
      refuses. It must never degrade to ``single``: that would show a model
      which may not be serving, the exact failure this page prevents.
    """

    kind: str                       # "ensemble" | "single" | "unresolvable"
    manifest_path: str | None = None
    manifest_sha256: str | None = None
    members: tuple[dict[str, str], ...] = ()
    error: str | None = None

    @property
    def is_ensemble(self) -> bool:
        return self.kind == "ensemble"


def load_ensemble_manifest_identity(manifest_path: str) -> IncumbentIdentity:
    """Read a manifest into a bannerable identity, or say why not.

    Delegates to the SERVING loader — the canonical validator
    (``src.inference.ensemble_serving.load_ensemble_manifest``). A
    hand-rolled parser here would be a second, weaker interpretation of
    the same file: it could call a manifest "current" that the actual
    serving path refuses (wrong schema version, wrong member count,
    broken hash chain, bad staggering), so the banner would vouch for a
    model production could not even load (codex #430). Reusing the
    validator also inherits its single-read digest — the digest is of
    the very bytes that were parsed, so a rotation mid-read cannot
    produce old windows carrying a new sha.
    """
    from src.inference.ensemble_serving import (  # noqa: PLC0415
        EnsembleServingError,
        load_ensemble_manifest,
    )

    try:
        members, manifest_sha = load_ensemble_manifest(manifest_path)
    except EnsembleServingError as exc:
        return IncumbentIdentity(
            kind="unresolvable", manifest_path=manifest_path, error=str(exc))
    except OSError as exc:  # pragma: no cover - defensive
        return IncumbentIdentity(
            kind="unresolvable", manifest_path=manifest_path,
            error=f"{type(exc).__name__}: {exc}")
    return IncumbentIdentity(
        kind="ensemble", manifest_path=manifest_path,
        manifest_sha256=manifest_sha,
        members=tuple({"fit_start": m.fit_start, "fit_end": m.fit_end}
                      for m in members))


def resolve_incumbent() -> IncumbentIdentity:
    """What is production serving right now — ensemble, single, or unknown.

    Unset falls back to the DOCUMENTED production manifest, not to
    "single model": production cut over on 2026-08-05, so a missing
    variable means "nobody configured this box", never "the ensemble was
    retired". The single-model state requires the explicit ``none``
    opt-out — a claim someone made, not one this code inferred.
    """
    pointer = os.environ.get(ENV_ENSEMBLE_MANIFEST, "").strip()
    if pointer.lower() == SINGLE_MODEL_SENTINEL:
        return IncumbentIdentity(kind="single")
    # Anchored BEFORE the read, so the manifest this identity was built from
    # is the same file the printed `--ensemble-manifest` will open (r27).
    target = anchored_to_repo(pointer or DEFAULT_ENSEMBLE_MANIFEST)
    foreign = foreign_absolute_reason(target)
    if foreign is not None:
        # Do NOT load it. On POSIX a `D:/…` pointer is a relative path, so a
        # matching `D:/…` tree happening to exist under Streamlit's working
        # directory would be loaded and reported as the production ensemble —
        # this page's single worst failure mode, reached silently
        # (codex #431 r31). "Unresolvable" is the honest state and both pages
        # already refuse to describe an incumbent in it.
        return IncumbentIdentity(
            kind="unresolvable", manifest_path=target, error=foreign)
    return load_ensemble_manifest_identity(target)

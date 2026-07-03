"""Git-provable pre-registration gate for run comparisons (PR-3b-ii).

The comparison methodology (``openspec`` change ``add-run-comparison-methodology``)
requires every comparison to carry a **pre-registered hypothesis as a COMMITTED
artifact**: a plan file — the single planned A-vs-B comparison, its expected
direction, and the full registered variant set — committed to git BEFORE the
compared runs exist. "The hypothesis preceded the experiment" is then provable
TOPOLOGICALLY, not trusted to a human or a forgeable timestamp:

* the plan's identity is the LAST commit that touched the plan file — editing
  the plan after the runs moves that commit past the runs' recorded
  ``git_commit`` and the ancestry check fails ("cannot change post-hoc" is
  machine-verified);
* each compared run records the commit of the code that produced it
  (``git_commit`` in ``walk_forward_report.json``, captured at run start,
  resolved across resumed folds — see ``src.core.git_provenance``); the plan
  commit must be an ANCESTOR of each run's commit
  (``git merge-base --is-ancestor``);
* a comparison whose variant is NOT in the plan's registered set is FLAGGED as
  an unregistered multiple comparison (design-time control — a Bonferroni/FDR
  correction is near-undetectable under the SE≈0.42 noise floor, so the
  discipline must be design-time).

FAIL-LOUD everywhere: a run without provenance (pre-#313, resumed across mixed
commits, dirty worktree) REFUSES the gate with an actionable message rather
than weakening the proof.
"""
from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

EXPECTED_DIRECTIONS = ("treatment_better", "treatment_worse")


class PreregistrationError(RuntimeError):
    """Raised when the pre-registration gate cannot PROVE the hypothesis preceded
    the experiment — fail-loud, never a weakened or assumed proof."""


@dataclass(frozen=True)
class PreregPlan:
    """A validated, committed pre-registration plan."""

    path: str                    # plan file path as given
    repo_root: str               # toplevel of the repo containing the plan
    commit: str                  # LAST commit that touched the plan file
    hypothesis: str
    expected_direction: str      # one of EXPECTED_DIRECTIONS
    baseline: str                # human-readable baseline identifier
    treatments: tuple[str, ...]  # the REGISTERED variant set


def _git(args: list[str], *, cwd: str | Path) -> str:
    """Run git, returning stripped stdout; PreregistrationError on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreregistrationError(
            f"git {' '.join(args)} failed to execute ({exc!r}) — the gate needs a "
            "working git to prove ancestry; refusing."
        ) from exc
    if completed.returncode != 0:
        raise PreregistrationError(
            f"git {' '.join(args)} exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}. Refusing."
        )
    return completed.stdout.strip()


def load_plan(path: str | Path) -> PreregPlan:
    """Load + validate a committed plan file. FAIL-LOUD on: missing/malformed file,
    missing fields, a plan that was never committed, or one with UNCOMMITTED edits
    (an uncommitted plan is not a registration — it can still be changed post-hoc)."""
    p = Path(path)
    if not p.is_file():
        raise PreregistrationError(f"Pre-registration plan not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PreregistrationError(f"Plan {p} is not valid YAML/JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PreregistrationError(
            f"Plan {p} must be a mapping with keys hypothesis / expected_direction / "
            f"baseline / treatments; got {type(raw).__name__}."
        )

    hypothesis = str(raw.get("hypothesis") or "").strip()
    direction = str(raw.get("expected_direction") or "").strip()
    baseline = str(raw.get("baseline") or "").strip()
    treatments_raw = raw.get("treatments")
    if not hypothesis:
        raise PreregistrationError(f"Plan {p}: 'hypothesis' is required and non-empty.")
    if direction not in EXPECTED_DIRECTIONS:
        raise PreregistrationError(
            f"Plan {p}: 'expected_direction' must be one of {EXPECTED_DIRECTIONS}; "
            f"got {direction!r}. Register the direction you EXPECT — it is recorded, "
            "not enforced, and a mismatch with the verdict is surfaced."
        )
    if not baseline:
        raise PreregistrationError(f"Plan {p}: 'baseline' is required and non-empty.")
    if (
        not isinstance(treatments_raw, list)
        or not treatments_raw
        or not all(isinstance(t, str) and t.strip() for t in treatments_raw)
    ):
        raise PreregistrationError(
            f"Plan {p}: 'treatments' must be a non-empty list of variant names — the "
            "FULL registered variant set, fixed at design time."
        )
    treatments = tuple(t.strip() for t in treatments_raw)

    repo_root = _git(["rev-parse", "--show-toplevel"], cwd=p.resolve().parent)
    try:
        rel = str(p.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError as exc:
        raise PreregistrationError(
            f"Plan {p} resolves outside its repo toplevel {repo_root!r} ({exc}) — "
            "symlinked or case-mismatched paths cannot anchor a registration. Use the "
            "plan's real in-repo path."
        ) from exc
    # An uncommitted (or locally edited) plan is not a registration: it can still be
    # changed after seeing results. The gate anchors to COMMITTED content only.
    porcelain = _git(["status", "--porcelain", "--", rel], cwd=repo_root)
    if porcelain:
        raise PreregistrationError(
            f"Plan {p} has UNCOMMITTED changes ({porcelain.splitlines()[0]!r}). Commit "
            "the plan first — the registration is the committed content, nothing else."
        )
    commit = _git(["log", "-n", "1", "--format=%H", "--", rel], cwd=repo_root)
    if not commit:
        raise PreregistrationError(
            f"Plan {p} is not committed (no commit touches {rel!r}). Commit the plan "
            "BEFORE producing the runs it registers."
        )
    return PreregPlan(
        path=str(p), repo_root=repo_root, commit=commit, hypothesis=hypothesis,
        expected_direction=direction, baseline=baseline, treatments=treatments,
    )


def run_commit_from_report(report: Mapping[str, Any], *, run_label: str) -> str:
    """Extract a PROVABLE run commit from a run's aggregate report. FAIL-LOUD when the
    run carries no usable provenance — the gate never weakens to a timestamp."""
    commit = report.get("git_commit")
    dirty = report.get("git_dirty")
    if not commit:
        raise PreregistrationError(
            f"{run_label}: the run records no git_commit (produced before provenance "
            "stamping, or resumed across MIXED commits, or git was unavailable). "
            "Ancestry cannot be proven — re-run all folds in one invocation on a clean "
            "checkout for a pre-registered comparison."
        )
    if dirty is not False:
        state = "a DIRTY worktree" if dirty else "UNKNOWN worktree cleanliness"
        raise PreregistrationError(
            f"{run_label}: the run was produced from {state} (git_dirty={dirty!r}), so "
            f"commit {commit[:12]} does not fully describe the code that ran. Ancestry "
            "against it proves nothing — re-run from a clean committed state."
        )
    return str(commit)


def is_ancestor(ancestor: str, descendant: str, *, repo_root: str | Path) -> bool:
    """True iff ``ancestor`` is an ancestor of (or equal to) ``descendant``."""
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreregistrationError(
            f"git merge-base --is-ancestor failed to execute ({exc!r}); refusing."
        ) from exc
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise PreregistrationError(
        f"git merge-base --is-ancestor {ancestor[:12]} {descendant[:12]} exited "
        f"{completed.returncode}: {completed.stderr.strip()}. (Unknown commit? The runs "
        "must come from THIS repository's history.) Refusing."
    )


def _st_handling(
    report: Mapping[str, Any], *, run_label: str,
) -> tuple[str, str | None]:
    """The run's ST-exclusion handling, derived from the report's EMBEDDED
    config: ``(st_mask_mode, concrete namechange input path or None)``.

    The CONCRETE path matters, not mere presence (codex P1 #323 round 2): two
    ST-on runs fed different namechange snapshots exclude DIFFERENT ST sets —
    that is an input change between baseline and treatment, not the
    registered variant. The path is separator-normalized (a pure string
    operation — deterministic on every platform, no filesystem access) so a
    slash-vs-backslash spelling of the same file doesn't refuse; case is NOT
    folded (a case-only difference refuses — the safe, fail-loud direction).

    FAIL-LOUD when the report carries no ``config`` block — parity cannot be
    proven, and an ST-on-vs-ST-off pair would contaminate an isolated
    experiment with the ST interaction (codex P1 on #323). A config that
    predates the ``st_mask_mode`` field reads as ``"required"`` (the only
    semantics the engine had then).
    """
    cfg = report.get("config")
    if not isinstance(cfg, Mapping):
        raise PreregistrationError(
            f"{run_label}: the run's report embeds no 'config' block, so "
            "ST-handling parity cannot be proven. Decision-grade comparisons "
            "need runs produced by the current engine (which embeds the full "
            "config in walk_forward_report.json) — re-run the walk-forward, "
            "or use --prereg <ref> for an exploratory, non-decision-grade "
            "comparison."
        )
    mode = str(cfg.get("st_mask_mode") or "required")
    raw = str(cfg.get("namechange_path") or "").strip()
    st_inputs = str(PurePosixPath(raw.replace("\\", "/"))) if raw else None
    return mode, st_inputs


def gate_comparison(
    plan: PreregPlan,
    *,
    baseline_report: Mapping[str, Any],
    treatment_report: Mapping[str, Any],
    variant: str,
) -> list[str]:
    """Verify the plan predates BOTH runs AND the runs are ST-comparable;
    return advisory flags (never silently).

    Raises ``PreregistrationError`` on any hard failure (missing/dirty
    provenance, plan not an ancestor, mismatched ST handling — one side ST-on
    and one ST-off measures the ST interaction, not the registered
    hypothesis). Returns a list of FLAG strings for advisory findings — per
    the spec an unregistered variant is FLAGGED, not refused, so the number of
    comparisons actually attempted stays visible instead of being driven
    underground.
    """
    base_commit = run_commit_from_report(baseline_report, run_label="baseline run")
    treat_commit = run_commit_from_report(treatment_report, run_label="treatment run")
    for label, run_commit in (("baseline", base_commit), ("treatment", treat_commit)):
        if not is_ancestor(plan.commit, run_commit, repo_root=plan.repo_root):
            raise PreregistrationError(
                f"Plan commit {plan.commit[:12]} is NOT an ancestor of the {label} "
                f"run's commit {run_commit[:12]} — the plan (or its latest edit) does "
                "not provably predate the run. A plan edited after the runs moves its "
                "last-touched commit past them; that is exactly the post-hoc change "
                "this gate exists to catch. Re-register and re-run."
            )
    base_st = _st_handling(baseline_report, run_label="baseline run")
    treat_st = _st_handling(treatment_report, run_label="treatment run")
    if base_st != treat_st:
        raise PreregistrationError(
            "ST-handling MISMATCH between the compared runs: baseline has "
            f"st_mask_mode={base_st[0]!r} (st inputs: {base_st[1] or 'NONE'}) "
            f"vs treatment st_mask_mode={treat_st[0]!r} (st inputs: "
            f"{treat_st[1] or 'NONE'}). An isolated experiment must hold ST "
            "handling constant on both sides — SAME mode AND the SAME "
            "namechange input (docs/run-comparison-runbook.md): ST-on vs "
            "ST-off measures the PR#223 ST interaction, and two different "
            "namechange snapshots exclude different ST sets — either way the "
            "pair measures an input change, not the registered hypothesis. "
            "Refusing the decision-grade verdict; re-run the mismatched side."
        )
    flags: list[str] = []
    if variant not in plan.treatments:
        flags.append(
            f"UNREGISTERED MULTIPLE COMPARISON: variant {variant!r} is not in the "
            f"pre-registered set {list(plan.treatments)} — this comparison exceeds the "
            "plan. Its verdict must not be read at face value (the plan's design-time "
            "multiple-comparison control does not cover it)."
        )
    return flags

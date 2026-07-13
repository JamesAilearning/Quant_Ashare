"""Gate-3 pre-registration gate — run BEFORE every decision-level run.

Checks (ALL must pass; any failure = REFUSE, no run):
  1. plan parse   — quality_profitability.yaml + ledger load and carry the
                    frozen protocol_id.
  2. plan frozen  — the plan file is COMMITTED (git-provable timestamp).
  3. clean tree   — `git status --porcelain` empty: no uncommitted drift.
  4. plan < run   — the plan's last commit time is BEFORE the run timestamp.
  5. manifest     — the raw store re-hashes EXACTLY to the frozen manifest.
  6. candidate    — the requested candidate id is in registered_candidates.
  7. PIT cases    — the PIT assertion battery passes (default: the view's
                    logic tests; rehearsal R6 injects a failing battery).
  8. run window   — --test-window-end must not pass the frozen dev
                    end_boundary; touching the holdout REFUSES unless the
                    ONE-TIME --final-adjudication flag is set (loud banner).
  9. ledger       — the DSR/PBO ledger status must be frozen_with_this_package.

Exit 0 = ACCEPT (prints plan commit + manifest aggregate for run metadata);
exit 1 = REFUSE with the reason. Rehearsed by
``scripts/research/rehearse_gate3_prereg_gate.py`` (R1-R8, 8/8 required).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

PLAN_REL = "docs/prereg/quality_profitability.yaml"
LEDGER_REL = "docs/prereg/quality_profitability_ledger.yaml"
MANIFEST_REL = "docs/prereg/quality_profitability_store_manifest.json"
DEFAULT_PIT_CASES = "tests/logic/test_financial_pit_view.py"

# EVERY frozen artifact counts toward the freeze timestamp (codex #352 P1):
# a later clean commit touching ANY of these moves the freeze time forward,
# so a run must postdate the LATEST change to the whole frozen package —
# the ledger/manifest/gate cannot be silently swapped after the plan froze.
FROZEN_ARTIFACTS = (
    PLAN_REL,
    LEDGER_REL,
    MANIFEST_REL,
    "docs/prereg/quality_profitability_rehearsal.md",
    "docs/prereg/quality_profitability_pit_preflight_gate3.md",
    "scripts/research/gate3_store_manifest.py",
    "scripts/research/gate3_prereg_gate.py",
    "scripts/research/rehearse_gate3_prereg_gate.py",
)


def _refuse(reason: str) -> int:
    print(f"GATE REFUSE: {reason}")
    return 1


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _resolve_overall_end(cfg_path: Path, depth: int = 0) -> object:
    """``overall_end`` from a walk-forward config, resolving the ``extends``
    chain (child overrides parent). Returns the raw value, or a string
    starting with ``REFUSE:`` on any failure — the gate never guesses a
    window it cannot derive."""
    if depth > 5:
        return "REFUSE:extends chain deeper than 5 — refusing to derive."
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure refuses
        return f"REFUSE:run config unreadable ({cfg_path}): {exc}"
    if not isinstance(data, dict):
        return f"REFUSE:run config is not a mapping: {cfg_path}"
    if "overall_end" in data:
        return data["overall_end"]
    ext = data.get("extends")
    if ext:
        return _resolve_overall_end((cfg_path.parent / str(ext)).resolve(),
                                    depth + 1)
    return (f"REFUSE:run config {cfg_path} has no overall_end anywhere in "
            "its extends chain — the gated window cannot be derived.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument("--candidate", required=True)
    p.add_argument("--store-dir", type=Path, required=True)
    p.add_argument("--run-ts", default=None,
                   help="ISO timestamp of the run being gated (default: now UTC).")
    p.add_argument("--pit-cases", default=DEFAULT_PIT_CASES,
                   help="pytest target(s) for the PIT battery.")
    p.add_argument("--run-config", type=Path, required=True,
                   help="the walk-forward config the run will ACTUALLY use — "
                        "the gated window is DERIVED from its overall_end "
                        "(extends chain resolved), never from a bare claim "
                        "(codex #352 r8 P1). ACCEPT echoes the config sha256 "
                        "so run provenance ties the run to the gated config.")
    p.add_argument("--test-window-end", default=None,
                   help="optional cross-check: if supplied it must EQUAL the "
                        "config-derived window end, else the gate refuses "
                        "(claim/config mismatch).")
    p.add_argument("--final-adjudication", action="store_true",
                   help="ONE-TIME holdout unblinding for the final verdict "
                        "run only; prints a loud banner and still requires "
                        "every other check.")
    # NOTE: no manifest override — the gate verifies ONLY against the frozen
    # manifest path (codex #352 P1: an override would let a re-ingested store
    # pass with a matching temp manifest, bypassing the data-version lock).
    args = p.parse_args(argv)
    repo = args.repo_root

    # 1. plan parse
    plan_path = repo / PLAN_REL
    try:
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        ledger = yaml.safe_load((repo / LEDGER_REL).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - any parse failure refuses
        return _refuse(f"plan/ledger unreadable: {exc}")
    if plan.get("protocol_id") != "quality_profitability_v1" \
            or ledger.get("protocol_id") != plan.get("protocol_id"):
        return _refuse("protocol_id mismatch between plan and ledger.")
    if ledger.get("status") != "frozen_with_this_package":
        # the trial-count ledger is part of the pre-registration boundary — a
        # draft/downgraded ledger must never gate-ACCEPT (codex #352 r3/r4 P1;
        # the r3 patch claimed this check but silently failed to land — caught
        # by codex r4, now enforced AND covered by rehearsal R7).
        return _refuse(f"ledger status {ledger.get('status')!r} is not "
                       "'frozen_with_this_package' — the DSR/PBO ledger must "
                       "be frozen before any decision-level run.")

    # 1b. the run's test window must respect the frozen dev boundary — the
    # default config_walk.yaml runs through 2025-12-31 and would silently
    # consume the 2025 holdout without this check (codex #352 r5 P1).
    # Covered by rehearsal R8.
    sd = plan["study_design"]
    end_boundary = sd["window"]["end_boundary"]
    if not isinstance(end_boundary, date):
        end_boundary = date.fromisoformat(str(end_boundary))
    # the gated window is DERIVED from the run config the run will actually
    # consume — a bare --test-window-end claim could say 2024-12-31 while the
    # config still runs through 2025-12-31 (codex #352 r8 P1).
    if not args.run_config.is_file():
        return _refuse(f"run config not found: {args.run_config}")
    overall_end = _resolve_overall_end(args.run_config)
    if isinstance(overall_end, str) and overall_end.startswith("REFUSE:"):
        return _refuse(overall_end[len("REFUSE:"):])
    if isinstance(overall_end, date):
        test_end = overall_end
    else:
        try:
            test_end = date.fromisoformat(str(overall_end))
        except ValueError:
            return _refuse(f"run config overall_end {overall_end!r} is not "
                           "a YYYY-MM-DD date.")
    if args.test_window_end is not None:
        try:
            claimed = date.fromisoformat(args.test_window_end)
        except ValueError:
            return _refuse(f"--test-window-end {args.test_window_end!r} is "
                           "not a YYYY-MM-DD date.")
        if claimed != test_end:
            return _refuse(
                f"claim/config mismatch: --test-window-end says "
                f"{claimed.isoformat()} but the run config derives "
                f"{test_end.isoformat()} ({args.run_config}) — the gate "
                "validates the CONFIG, not the claim.")
    holdout_window = str(sd["untouched_final_holdout"]["window"])
    try:
        holdout_start_s, holdout_end_s = (t.strip() for t in
                                          holdout_window.split("->"))
        holdout_end = date.fromisoformat(holdout_end_s)
    except (ValueError, AttributeError):
        return _refuse(f"frozen holdout window {holdout_window!r} is not "
                       "parseable as 'YYYY-MM-DD -> YYYY-MM-DD'.")
    if test_end > end_boundary:
        if not args.final_adjudication:
            return _refuse(
                f"run test window ends {test_end.isoformat()} which is AFTER "
                f"the frozen dev end_boundary {end_boundary.isoformat()} — it "
                f"would touch the untouched holdout ({holdout_window}). Dev "
                "runs must end at the boundary; the ONE-TIME final verdict "
                "run must pass --final-adjudication explicitly.")
        if test_end != holdout_end:
            # the verdict run must cover the FULL signed holdout EXACTLY —
            # beyond it (e.g. 2026H1) is outside the registered adjudication
            # scope (codex #352 r6 P1), and SHORT of it (e.g. 2025-06-30)
            # would be an interim PEEK at partial holdout results before the
            # one-time verdict (codex #352 r7 P1). Either way: refuse.
            return _refuse(
                f"final-adjudication test window ends {test_end.isoformat()} "
                f"but the ONE-TIME verdict must cover the signed holdout "
                f"EXACTLY (test_end == {holdout_end.isoformat()}, window "
                f"{holdout_window}) — no partial peek, no out-of-scope data.")
        print("=" * 68)
        print("!! FINAL ADJUDICATION — HOLDOUT UNBLINDING !!")
        print(f"!! test window end {test_end.isoformat()} enters the frozen "
              f"holdout {holdout_window}.")
        print("!! This is the ONE-TIME verdict run; record it in the ledger.")
        print("=" * 68)

    # 2. the WHOLE frozen package is committed; freeze time = the LATEST
    # commit across all frozen artifacts (codex #352 P1).
    plan_commit = _git(repo, "log", "-1", "--format=%H", "--", PLAN_REL)
    if not plan_commit:
        return _refuse(f"plan not committed: {PLAN_REL} has no git history "
                       "(freeze commit required before any run).")
    freeze_ts: datetime | None = None
    for rel in FROZEN_ARTIFACTS:
        if not (repo / rel).is_file():
            # a DELETED artifact still has git history (the deletion commit),
            # so the timestamp alone would treat it as frozen (codex #352 r2)
            # — the whole package must EXIST at the gated checkout.
            return _refuse(f"frozen artifact missing from checkout: {rel}")
        ts_raw = _git(repo, "log", "-1", "--format=%cI", "--", rel)
        if not ts_raw:
            return _refuse(f"frozen artifact not committed: {rel}")
        ts = datetime.fromisoformat(ts_raw)
        if freeze_ts is None or ts > freeze_ts:
            freeze_ts = ts
    assert freeze_ts is not None
    plan_ts = freeze_ts

    # 3. clean tree
    dirty = _git(repo, "status", "--porcelain")
    if dirty:
        return _refuse("dirty checkout — uncommitted changes present:\n  "
                       + "\n  ".join(dirty.splitlines()[:10]))

    # 4. plan committed BEFORE the run
    run_ts = (datetime.fromisoformat(args.run_ts) if args.run_ts
              else datetime.now(timezone.utc))
    if run_ts.tzinfo is None:
        run_ts = run_ts.replace(tzinfo=timezone.utc)
    if plan_ts >= run_ts:
        return _refuse(f"frozen package last committed at {plan_ts.isoformat()} "
                       f"which is NOT before the run at {run_ts.isoformat()} — "
                       "git-provable ordering violated.")

    # 5. manifest verification (re-hash the store against the FROZEN manifest)
    manifest_path = repo / MANIFEST_REL
    verify = subprocess.run(
        [sys.executable, str(repo / "scripts/research/gate3_store_manifest.py"),
         "--store-dir", str(args.store_dir), "--verify", str(manifest_path)],
        capture_output=True, text=True,
    )
    if verify.returncode != 0:
        return _refuse("store manifest mismatch:\n" + verify.stdout.strip())

    # 6. candidate registered
    registered = {c["id"] for c in plan["candidate_family"]["registered_candidates"]}
    if args.candidate not in registered:
        return _refuse(f"candidate {args.candidate!r} NOT in registered_candidates "
                       f"{sorted(registered)} — a new candidate means a NEW plan "
                       "+ a NEW untouched window (prohibited_variants).")

    # 7. PIT battery
    pit = subprocess.run(
        [sys.executable, "-m", "pytest", *args.pit_cases.split(), "-q",
         "--no-header", "-x"],
        capture_output=True, text=True, cwd=str(repo),
    )
    if pit.returncode != 0:
        tail = "\n".join(pit.stdout.strip().splitlines()[-5:])
        return _refuse(f"PIT case battery FAILED:\n{tail}")

    aggregate = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "aggregate_sha256"]
    print("GATE ACCEPT")
    print(f"  plan_commit: {plan_commit}")
    print(f"  frozen_package_committed_at: {plan_ts.isoformat()}")
    print(f"  candidate: {args.candidate}")
    cfg_sha = __import__("hashlib").sha256(
        args.run_config.read_bytes()).hexdigest()
    print(f"  run_config: {args.run_config} sha256={cfg_sha[:16]}...")
    print(f"  test_window_end(derived): {test_end.isoformat()}"
          + ("  [FINAL ADJUDICATION]" if args.final_adjudication else ""))
    print(f"  manifest_aggregate_sha256: {aggregate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

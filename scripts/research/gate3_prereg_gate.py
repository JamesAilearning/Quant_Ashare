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
  8. run window   — the window is DERIVED from the run config (extends
                    chain resolved) and must not pass the frozen dev
                    end_boundary; touching the holdout REFUSES unless the
                    ONE-TIME --final-adjudication flag is set (loud banner),
                    the verdict window must equal the holdout EXACTLY, and
                    the flag itself is REFUSED on a dev window (no fake
                    verdict provenance).
  9. ledger       — the DSR/PBO ledger status must be frozen_with_this_package.
 10. config chain — the run config must be repo-tracked and every file in
                    its resolved extends chain must itself be a frozen
                    artifact (self-contained frozen presets; no drift via
                    an unfrozen parent).
 11. one verdict  — ledger holdout_unblinded must be a boolean; once true
                    this plan is CONSUMED and EVERY decision-level run
                    (dev or final) is refused permanently.
 12. literal data — no ${...} env placeholder in any chain VALUE: the same
                    config sha256 must not resolve to different data
                    bundles at runtime (frozen-literal paths).
 13. binding      — the chain must declare gate3_candidate (child-first)
                    equal to --candidate: the gate accepts only the
                    candidate the run config actually evaluates.
 14. design stamp — the chain must carry the signed holding period
                    (quarterly: rebalance_cadence_days=63, fold_phase) and
                    the frozen universe stamp (gate3_universe ==
                    plan study_design.universe); daily-default or bare
                    csi300 must not masquerade as the signed design.

Exit 0 = ACCEPT (prints plan commit + manifest aggregate for run metadata);
exit 1 = REFUSE with the reason. Rehearsed by
``scripts/research/rehearse_gate3_prereg_gate.py`` (R1-R23, 23/23 required).
"""
from __future__ import annotations

import argparse
import hashlib
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
    "config/presets/quality_gate3_dev.yaml",
    "config/presets/quality_gate3_final_adjudication.yaml",
    "config/presets/quality_gate3_dev_c1_gpa.yaml",
    "config/presets/quality_gate3_dev_c2_prof.yaml",
    "config/presets/quality_gate3_dev_c3_cash_op.yaml",
    "config/presets/quality_gate3_final_adjudication_c1_gpa.yaml",
    "config/presets/quality_gate3_final_adjudication_c2_prof.yaml",
    "config/presets/quality_gate3_final_adjudication_c3_cash_op.yaml",
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


def _resolve_run_config(
    cfg_path: Path,
) -> str | tuple[object, list[Path], list[dict[str, object]]]:
    """Resolve the FULL ``extends`` chain (child-first) and derive
    ``overall_end``. Returns ``(overall_end_raw, chain_paths, chain_datas)``
    or a string starting with ``REFUSE:`` on any failure — the gate never
    guesses a window it cannot derive. The WHOLE chain (paths AND parsed
    mappings, child-first) is returned because every file in it shapes the
    run (model/universe/cost/fold parameters), not just the one holding
    ``overall_end`` (codex #352 r10/r11 P1)."""
    chain: list[Path] = []
    datas: list[dict[str, object]] = []
    cur = cfg_path
    for _ in range(6):
        try:
            data = yaml.safe_load(cur.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure refuses
            return f"REFUSE:run config unreadable ({cur}): {exc}"
        if not isinstance(data, dict):
            return f"REFUSE:run config is not a mapping: {cur}"
        chain.append(cur)
        datas.append(data)
        ext = data.get("extends")
        if not ext:
            break
        cur = (cur.parent / str(ext)).resolve()
    else:
        return "REFUSE:extends chain deeper than 5 — refusing to derive."
    for data in datas:  # child overrides parent
        if "overall_end" in data:
            return data["overall_end"], chain, datas
    return (f"REFUSE:run config {cfg_path} has no overall_end anywhere in "
            "its extends chain — the gated window cannot be derived.")


def _values_contain_placeholder(obj: object) -> bool:
    """True if any STRING VALUE in the parsed config carries a ``${``
    env placeholder. Mirrors the runner loader's expansion semantics
    (only string-typed values are expanded; comments are inert), so a
    frozen config whose comments merely MENTION ``${VAR}`` passes while
    any value-level indirection is caught (codex #352 r11 P1)."""
    if isinstance(obj, dict):
        return any(_values_contain_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_values_contain_placeholder(v) for v in obj)
    return isinstance(obj, str) and "${" in obj


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument("--candidate", required=True)
    p.add_argument("--store-dir", type=Path, required=True)
    p.add_argument("--run-ts", default=None,
                   help="ISO timestamp of the run being gated (default: now UTC).")
    p.add_argument("--extra-pit-case", default=None,
                   help="OPTIONAL extra pytest target APPENDED to the "
                        "canonical PIT battery (rehearsal R6 injects a "
                        "failing probe). The canonical battery ALWAYS runs "
                        "and cannot be replaced (codex #352 r9 P1).")
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
    unblinded = ledger.get("holdout_unblinded")
    if unblinded not in (True, False):
        # the one-time adjudication state must be machine-checkable — a
        # missing/non-boolean flag means the frozen ledger is malformed
        # (codex #352 r10 P1).
        return _refuse("ledger holdout_unblinded flag missing or non-boolean "
                       f"({unblinded!r}) — cannot prove the ONE-TIME verdict "
                       "has not already been consumed.")
    if unblinded:
        # GLOBAL terminal state (codex #352 r15 P2): once the ONE-TIME
        # verdict has fired, this pre-registration is CONSUMED — refusing
        # only repeat final adjudications would still allow post-verdict
        # dev-window "decision-level" runs, i.e. iteration on a family
        # whose holdout has already been spent. Refuse EVERYTHING under
        # this plan, ahead of the window branch.
        return _refuse(
            "holdout ALREADY UNBLINDED (ledger holdout_unblinded: true) — "
            "this pre-registration is CONSUMED: the ONE-TIME verdict has "
            "fired and no further decision-level run (dev or final) is "
            "permitted under this plan; any further work on this family = "
            "new plan + new untouched window (prohibited_variants).")

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
    cfg_resolved = args.run_config.resolve()
    repo_resolved = repo.resolve()
    if not cfg_resolved.is_relative_to(repo_resolved):
        # an out-of-repo config is not covered by the clean-tree / frozen
        # checks — a /tmp config could claim a dev window while the real run
        # uses another file (codex #352 r9 P1).
        return _refuse(f"run config {cfg_resolved} is NOT under the "
                       f"repository {repo_resolved} — the gated config must "
                       "be repo-tracked and git-provable.")
    cfg_rel = cfg_resolved.relative_to(repo_resolved).as_posix()
    tracked = _git(repo, "ls-files", "--", cfg_rel)
    if not tracked:
        return _refuse(f"run config {cfg_rel} is not git-tracked — an "
                       "untracked config is not git-provable; commit it "
                       "(the clean-tree check then pins its content).")
    resolved = _resolve_run_config(cfg_resolved)
    if isinstance(resolved, str):
        return _refuse(resolved[len("REFUSE:"):])
    overall_end, cfg_chain, cfg_datas = resolved
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
    if args.final_adjudication and test_end <= end_boundary:
        # the ONE-TIME verdict flag on a dev-window config would emit an
        # ACCEPT carrying [FINAL ADJUDICATION] provenance WITHOUT covering
        # the signed holdout or consuming the unblinding state — a dev run
        # masquerading as the verdict (codex #352 r13 P2). The flag is
        # valid ONLY when the derived window IS the holdout verdict window.
        return _refuse(
            f"--final-adjudication supplied but the run config derives a "
            f"DEV window (test end {test_end.isoformat()} <= frozen "
            f"boundary {end_boundary.isoformat()}) — the one-time verdict "
            f"flag is valid ONLY for the exact holdout window "
            f"({holdout_window}); a dev run must not carry verdict "
            "provenance.")
    if test_end > end_boundary:
        if not args.final_adjudication:
            return _refuse(
                f"run test window ends {test_end.isoformat()} which is AFTER "
                f"the frozen dev end_boundary {end_boundary.isoformat()} — it "
                f"would touch the untouched holdout ({holdout_window}). Dev "
                "runs must end at the boundary; the ONE-TIME final verdict "
                "run must pass --final-adjudication explicitly.")
        # NOTE: the unblinded terminal state is enforced GLOBALLY right
        # after the ledger parse (codex #352 r15: dev runs after the
        # verdict must refuse too, not only repeat adjudications), so
        # reaching this branch implies holdout_unblinded is false.
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
        # NOTE: the UNBLINDING banner is deliberately NOT printed here —
        # later checks (chain-frozen, dirty tree, ordering, manifest, PIT)
        # can still refuse, and a banner before a REFUSE could mislead the
        # operator into treating the holdout as fired / flipping the
        # ledger for a rejected run (codex #352 r16 P2). It prints
        # immediately before GATE ACCEPT, after ALL refusal paths passed.

    # 1c. the run config's FULL resolved chain must be frozen — a preset
    # that merely `extends` an unfrozen parent (e.g. config_walk.yaml)
    # would let a later clean commit to that parent silently change
    # model/universe/cost/fold parameters after freeze while the preset's
    # own hash stays identical (codex #352 r10 P1). Placed AFTER the
    # window checks so holdout-touching configs keep their specific
    # refusal reason; ACCEPT always requires BOTH.
    frozen_paths = {(repo_resolved / rel).resolve() for rel in FROZEN_ARTIFACTS}
    for link in cfg_chain:
        if link not in frozen_paths:
            return _refuse(
                f"run config resolves through {link} which is NOT part of "
                "the frozen package — every file in the extends chain "
                "shapes the run, so the FULL resolved config must be "
                "frozen (use the self-contained frozen presets).")

    # 1d. no env indirection in frozen run configs: the runner expands
    # ${QUANT_*} placeholders at runtime, so two runs could share this
    # chain's sha256 while training/backtesting against DIFFERENT
    # qlib/ST/delist bundles — defeating the data-version lock. Frozen
    # configs pin literal paths; any value-level ${...} refuses
    # (codex #352 r11 P1).
    for link, data in zip(cfg_chain, cfg_datas, strict=True):
        if _values_contain_placeholder(data):
            return _refuse(
                f"run config {link} carries a ${{...}} env placeholder in "
                "a value — env indirection lets the same config sha256 "
                "resolve to different data bundles at runtime; frozen run "
                "configs must pin literal paths.")

    # 1e. the candidate must be REGISTERED and BOUND by the config chain —
    # a bare --candidate flag is an unbound CLI claim: an operator could
    # gate C1_GPA and then run a config evaluating a different candidate
    # (or plain Alpha158) and still collect GATE ACCEPT (codex #352 r11
    # P1). The frozen chain must declare gate3_candidate (child-first)
    # and it must equal the claimed candidate.
    registered = {str(c["id"])
                  for c in plan["candidate_family"]["registered_candidates"]}
    if args.candidate not in registered:
        return _refuse(f"candidate {args.candidate!r} NOT in "
                       f"registered_candidates {sorted(registered)} — a new "
                       "candidate means a NEW plan + a NEW untouched window "
                       "(prohibited_variants).")
    cfg_candidate: str | None = None
    for data in cfg_datas:  # child overrides parent
        if "gate3_candidate" in data:
            cfg_candidate = str(data["gate3_candidate"])
            break
    if cfg_candidate is None:
        return _refuse(
            "run config chain declares no gate3_candidate — the frozen run "
            "config must BIND the candidate it evaluates (use the "
            "per-candidate frozen stubs, e.g. "
            "config/presets/quality_gate3_dev_c1_gpa.yaml).")
    if cfg_candidate != args.candidate:
        return _refuse(
            f"candidate binding mismatch: --candidate says "
            f"{args.candidate!r} but the run config chain binds "
            f"gate3_candidate {cfg_candidate!r} — the gate accepts only "
            "the candidate the run config actually evaluates.")

    # 1f. the frozen STUDY-DESIGN stamps must match the plan (codex #352
    # r14 P1): without these, an enabled Gate-4 run would silently produce
    # daily-rebalance metrics on the FULL csi300 while claiming the signed
    # quarterly ex-financial design.
    def _chain_value(key: str) -> object | None:
        for d in cfg_datas:  # child overrides parent
            if key in d:
                return d[key]
        return None

    holding_primary = str(plan["holding"]["primary"])
    if holding_primary != "quarterly_rebalance":
        return _refuse(f"plan holding.primary {holding_primary!r} has no "
                       "registered cadence mapping in this gate — refusing "
                       "rather than guessing the holding period.")
    expected_cadence, expected_anchor = 63, "fold_phase"
    got_cadence = _chain_value("rebalance_cadence_days")
    got_anchor = _chain_value("rebalance_anchor")
    if got_cadence != expected_cadence or got_anchor != expected_anchor:
        return _refuse(
            f"holding-period mismatch: plan freezes {holding_primary} "
            f"(rebalance_cadence_days={expected_cadence}, "
            f"rebalance_anchor={expected_anchor!r}) but the run config "
            f"chain derives cadence={got_cadence!r}, anchor={got_anchor!r} "
            "— an unset cadence defaults to DAILY rebalancing, producing "
            "turnover/cost metrics that are not the signed design.")
    plan_universe = str(sd["universe"])
    got_universe = _chain_value("gate3_universe")
    if got_universe != plan_universe:
        return _refuse(
            f"universe stamp mismatch: plan freezes {plan_universe!r} but "
            f"the run config chain declares gate3_universe="
            f"{got_universe!r} — the ex-financial exclusion is a Gate-4B "
            "wiring obligation and the stamp is what pins it; a bare "
            "csi300 run must not claim the frozen study universe.")

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

    # 6. candidate registration + config binding — enforced above in 1e
    # (before the freeze/dirty checks so binding rehearsals stay
    # temp-modifiable).

    # 7. PIT battery
    pit_targets = [DEFAULT_PIT_CASES]
    if args.extra_pit_case:
        pit_targets.append(args.extra_pit_case)
    pit = subprocess.run(
        [sys.executable, "-m", "pytest", *pit_targets, "-q",
         "--no-header", "-x"],
        capture_output=True, text=True, cwd=str(repo),
    )
    if pit.returncode != 0:
        tail = "\n".join(pit.stdout.strip().splitlines()[-5:])
        return _refuse(f"PIT case battery FAILED:\n{tail}")

    aggregate = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "aggregate_sha256"]
    if args.final_adjudication:
        # every refusal path has passed — only now may the unblinding
        # banner appear (codex #352 r16 P2: a banner before a REFUSE could
        # mislead the operator into flipping the ledger for a rejected
        # run). Reaching here with the flag implies test_end == holdout
        # end (window branch) and holdout_unblinded == false (global
        # terminal-state check).
        print("=" * 68)
        print("!! FINAL ADJUDICATION — HOLDOUT UNBLINDING !!")
        print(f"!! test window end {test_end.isoformat()} enters the frozen "
              f"holdout {holdout_window}.")
        print("!! This is the ONE-TIME verdict run. IMMEDIATELY after it")
        print("!! fires: flip ledger holdout_unblinded -> true, append the")
        print("!! unblinding entry, commit. The gate then refuses ANY")
        print("!! further decision-level run under this plan.")
        print("=" * 68)
    print("GATE ACCEPT")
    print(f"  plan_commit: {plan_commit}")
    print(f"  frozen_package_committed_at: {plan_ts.isoformat()}")
    print(f"  candidate: {args.candidate} (bound by run config chain)")
    print(f"  universe: {plan_universe} (stamped; ex-financial exclusion = "
          "Gate-4B wiring obligation)")
    print(f"  holding: {holding_primary} (cadence {expected_cadence}, "
          f"anchor {expected_anchor})")
    for link in cfg_chain:
        link_sha = hashlib.sha256(link.read_bytes()).hexdigest()
        print(f"  run_config: {link} sha256={link_sha[:16]}...")
    print(f"  test_window_end(derived): {test_end.isoformat()}"
          + ("  [FINAL ADJUDICATION]" if args.final_adjudication else ""))
    print(f"  manifest_aggregate_sha256: {aggregate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

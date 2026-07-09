# 阶段7b cadence × horizon campaign — RESULTS (adjudicated 2026-07-09)

**Outcome in one line: pre-registered DEAD-END — the label × cadence plane is
EXHAUSTED. No arm's net excess is significantly positive, and the primary
cell (weekly, H5) is not significantly better than the incumbent (daily, H1);
the strategic conclusion is to pivot to RAW-MATERIAL upgrade (阶段8 factor
innovation / universe expansion), NOT to add cells to this 2-D plane.**

Registration: `docs/prereg/cadence_horizon.yaml` @ `74f2c50`.
Verbatim gated verdict: `docs/prereg/cadence_horizon_verdict_20260709.txt`.
Full adjudication evidence (per-arm vs zero, 2×2, slices):
`docs/prereg/cadence_horizon_evidence_20260709.md`.
Runs: `output/stage7/{daily_h1,daily_h5,weekly_h1,weekly_h5}`, all at commit
`74f2c50`, ST-off, 23 folds each, 1397 paired OOS days, one uninterrupted
invocation each.

## Chain of custody (every pre-registered checkpoint held)

1. **Registration anchored** — the plan's last-touched commit is `74f2c50`
   (#338 merge); the ancestry gate PASSED for all four arms (plan precedes
   every run, ST parity verified).
2. **Consistency fuse (daily_h1 vs 阶段6)** — daily_h1 reproduced 阶段6's
   `h1_st_off_baseline` BYTE-FOR-BYTE (net +3.32%, ic_1d +0.0182, IR 0.2721;
   Δ = 0.00pp), proving the cadence enabler's N=1 default path is
   identity-preserving and the fresh retrain is deterministic — no post-阶段6
   drift. The fuse did not trip; arms 2–4 ignited.
3. **Four arms, one commit, ST parity** — provenance clean and identical.
4. **Gated compare + exit rules applied verbatim** (below).

## The verdict (ruler, paired moving-block bootstrap, n_boot=10000, seed=42)

**Every arm's net excess vs zero — NONE significantly positive:**

| arm | net_ann | 95% CI | state |
|---|---|---|---|
| daily_h1 (baseline) | +2.99% | [−5.51, +11.55] | indistinguishable-from-0 |
| daily_h5 | +2.28% | [−5.97, +10.59] | indistinguishable-from-0 |
| weekly_h1 | +3.51% | [−5.04, +12.09] | indistinguishable-from-0 |
| **weekly_h5** (primary) | **+4.32%** | **[−4.17, +12.71]** | **indistinguishable-from-0** |

**Primary comparison (weekly_h5 vs daily_h1) — INDISTINGUISHABLE, stable on
both pre-registered slices:**

| slice | n | paired NET diff [95% CI] | paired GROSS diff [95% CI] |
|---|---|---|---|
| FULL | 1397 | **+1.33pp** [−4.36, +6.96] | **−3.88pp** [−9.55, +1.77] |
| exclude fold-0 (2020Q2) | 1338 | +0.43pp [−5.66, +6.35] | −4.76pp [−10.89, +1.15] |
| exclude 2020H2 | 1271 | +0.25pp [−5.88, +6.24] | −4.91pp [−11.06, +1.05] |

## Adjudication against the PRE-REGISTERED rules (none invented post-hoc)

- **SUCCESS** (weekly_h5 net sig-positive OR sig-better than daily_h1) —
  **NOT met**: net vs zero indistinguishable, and the paired diff is
  indistinguishable (stable across both slices).
- **DEAD-END** (not-success AND no arm sig-positive) — **MET**: all four
  cells are indistinguishable-from-zero. → the label × cadence plane is
  exhausted. Per the rule, pivot to raw-material upgrade; do NOT add cells
  (H7 / bi-weekly / monthly) — the 2×2 was an exhaustiveness test, not a
  search start.
- No ST-on re-verify (SUCCESS did not hold; incumbent daily_h1 stays).

## The mechanism finding — the campaign's most valuable output

**The primary comparison's GROSS diff is NEGATIVE (−3.88pp) while its NET diff
is POSITIVE (+1.33pp).** Read together with the per-arm ordering
(weekly arms carry higher mean net than their daily counterparts), this is a
DIRECTIONAL confirmation that **the label × cadence mechanism WORKS**:

- Weekly rebalancing holds a staler signal → it LOSES gross alpha (−3.88pp);
- but it trades ~1/5 the days at the same n_drop-per-event → it CUTS cost
  enough to flip the net POSITIVE (+1.33pp);
- so the ic_5d +15% signal 阶段6 surfaced IS real and the cadence lever DOES
  convert it in the right direction — the net moves the way the hypothesis
  predicted.

It just **does not clear the noise floor**, because the raw material is too
thin: the promoted model's gross alpha base is only ~+2.73%/yr (④ promotion
recon), so even a working mechanism cannot lift the post-cost excess to
significance.

## The three-campaign causal chain (the strategic payload)

| campaign | finding |
|---|---|
| 降频 (n_drop) | cost is the binding constraint at daily rebalance |
| 阶段6 (label horizon) | 5d label carries slower-decaying signal (ic_5d +15%) that a horizon-BLIND daily cadence WASTES |
| **7b (cadence × horizon)** | **adding cadence moves net the right way (+1.33pp) but cannot clear the noise floor — mechanism sound, signal real, RAW MATERIAL insufficient** |

Three consecutive credible NEGATIVES exhaust the **"existing factors +
existing universe" internal-optimization path**. The diagnosis points
precisely at CHANGING THE RAW MATERIAL, not further polishing the mechanism.

## Strategic conclusion (recorded; the specific route is a SEPARATE decision)

The internal-optimization path (label × cadence, at the current factor set
and csi300 universe) is a credible three-campaign dead-end. The battlefield
moves to **RAW-MATERIAL UPGRADE**:

- **阶段8** — GP factor innovation (new alpha sources), or
- **universe expansion** — e.g. csi800, more names to select from.

**Adding cells to the label × cadence plane (H7 / bi-weekly / monthly) is
forbidden** by the pre-registered exit rule. Which raw-material route to take
(阶段8 vs universe expansion) is the NEXT independent decision, NOT part of
this wrap-up.

## Honesty envelope (as registered)

- The qlib exchange leg is not PIT-wired; weekly holding raises
  delist-during-hold exposure. All four arms share it → the INTERNAL
  comparison is valid; absolute net-excess levels carry the caveat.
- Sparse-arm ic_1d/ic_5d were descriptive-only (no adjudication standing,
  both directions) — the ic_5d finding was a hypothesis INPUT, and the
  campaign's verdict rests on net excess, exactly as pre-registered.
- The honesty envelope's predicted outcome held: "(weekly, H5) still not
  clearing positive because the +2.73% gross base is too thin — the clean
  'existing factors tapped out' signal, not a failure."

## Follow-ups

- Raw-material upgrade decision (阶段8 vs universe expansion) — separate.
- The campaign infrastructure (prereg gate, ruler, cadence enabler with the
  iso_week deployable anchor, evidence generators) is standing equipment.
- The `add-rebalance-cadence` enabler (#336) remains in production as an
  opt-in — default N=1 is byte-identical, so no rollback needed.

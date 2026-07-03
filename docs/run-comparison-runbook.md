# Run-comparison runbook (the trustworthy ruler + pre-registration gate)

How to compare two walk-forward runs so the verdict is worth believing.
Infrastructure: `src/core/comparison.py` (the statistics),
`src/core/preregistration.py` (the git-provable gate),
`scripts/compare_walk_forward_runs.py` (the operator CLI).
Spec: `openspec` change `add-run-comparison-methodology`.

## The workflow (in this order — the order IS the methodology)

1. **Write the plan file** (YAML), e.g. `docs/prereg/label_horizon.yaml`:

   ```yaml
   hypothesis: "5d label decays slower; treatment keeps more gross alpha at lower turnover"
   expected_direction: treatment_better   # what you EXPECT — recorded, not enforced
   baseline: canonical-2d
   treatments: ["5d"]                     # the FULL registered variant set, fixed NOW
   ```

   `treatments` is the design-time multiple-comparison control: every variant you
   might compare goes in **now**. Adding one later is what the gate exists to catch.

2. **Commit the plan.** The registration is the committed content — nothing else.

3. **Produce both runs** from a **clean checkout at (or after) the plan commit**,
   each in **one uninterrupted invocation**. The walk-forward engine records the
   code's `git_commit` + `git_dirty` at run start (`src/core/git_provenance.py`).

4. **Compare:**

   ```sh
   python scripts/compare_walk_forward_runs.py <baseline_dir> <treatment_dir> \
       --prereg-plan docs/prereg/label_horizon.yaml --variant 5d
   ```

   The output is: per-fold table → aggregate deltas → the gate block → the
   three-state verdict (paired moving-block-bootstrap CI on daily net excess)
   with pooled IR, seam bound, IC diagnostics, and the honesty-envelope caveats.

## What the gate REFUSES (fail-loud; no verdict is emitted)

| Condition | Why |
| --- | --- |
| Plan file uncommitted, or committed but locally edited | An uncommitted plan can still be changed post-hoc; the committed content is the registration. |
| Plan's **last-touched commit** is not a git **ancestor** of a run's `git_commit` | The plan (or its latest edit) does not provably predate the run. Editing a plan after the runs moves its last-touched commit past them — exactly the post-hoc change being caught. Re-register and re-run. |
| Run has `git_commit: null` | Pre-provenance run, **or a resume that mixed commits across folds**, or git was unavailable. Ancestry cannot be proven. Re-run all folds in one invocation. |
| Run has `git_dirty: true` (or unknown) | The commit does not fully describe the code that ran; ancestry against it proves nothing. Run from a clean committed state. |
| The two runs' embedded configs disagree on ST handling (`st_mask_mode` / ST-input presence) | One side ST-on and one ST-off measures the PR#223 ST interaction, not the registered hypothesis — "ST-off on both sides" must hold by machine check, not by care. Re-run the mismatched side. |
| A run's report embeds no `config` block | ST-handling parity cannot be proven. Re-run on the current engine, or use `--prereg <ref>` for an exploratory (non-decision-grade) comparison. |
| `--prereg-plan` without `--variant` | Without the claimed variant the unregistered-comparison check is vacuous. |

**Flagged, not refused:** a `--variant` not in the plan's `treatments` — the
verdict still prints, under an `UNREGISTERED MULTIPLE COMPARISON` flag, so extra
comparisons stay visible instead of being driven underground. A directional
verdict **opposite** the registered `expected_direction` is likewise surfaced
(`direction vs plan: OPPOSITE...`) — it cannot be quietly re-narrated.

`--prereg <ref>` (no plan file) still produces a verdict but it is NOT
decision-grade: the gate block is marked **RECORD-ONLY — NOT git-verified** and
the verdict line itself reads `VERDICT (EXPLORATORY — prereg NOT git-verified):`
— an excerpt of just that line still shows it was never verified. Use it for
exploratory reruns only; a decision-grade comparison goes through
`--prereg-plan`.

## Experiment-design constraints (the label-horizon campaign and after)

- **One variable at a time.** A treatment run changes exactly the registered
  variable (e.g. label horizon). Same universe, costs, benchmark, ST handling,
  fold windows. If the config diff is more than the variant, the comparison
  measures the diff, not the hypothesis.
- **ST-off for isolated label experiments.** Label-horizon runs compare with the
  ST mask OFF on both sides to avoid the PR#223 ST-drift interaction
  contaminating the label effect.
- **Winner re-verifies ST-on vs the REGEN-2 canonical** before any promotion
  talk: the isolated experiment picks a direction; the production-config rerun
  confirms it survives the real trading constraints and the replay anchor.
- **Power comes from the data side, not the ruler.** Under the ~SE≈0.42 noise
  floor a one-year OOS leaves small real edges "indistinguishable". The lever is
  a longer walk-forward span (more folds / longer test windows) — the ruler's
  math does not change.
- **"Indistinguishable" ≠ "equivalent".** The mandated diagnostics (gross vs
  net, IC verdict, direction) exist because a net-excess tie can mask a gross
  divergence — the n_drop sweep was exactly that trap.

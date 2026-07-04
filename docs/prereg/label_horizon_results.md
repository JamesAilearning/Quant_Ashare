# 阶段6 label-horizon campaign — RESULTS (adjudicated 2026-07-04)

**Outcome in one line: pre-registered NEGATIVE result — the 5d label is not
better than the production 2d label (net indistinguishable with a negative
point estimate; gross point estimate also negative), the 10d escalation rule
did not fire, and the label line CLOSES.**

Registration: `docs/prereg/label_horizon.yaml` @ `fa85ddcb4210` (band v2).
Verbatim gated verdict: `docs/prereg/label_horizon_verdict_20260704.txt`.
Runs (operator box, Python 3.11 / numpy 1.26.4, clean checkout `fa85ddc`,
one uninterrupted invocation each; ST-off both sides per the runbook):
`output/stage6/h1_st_off_baseline` and `output/stage6/h5_st_off_treatment`,
23 folds each, 1397 paired OOS days, 100% overlap.

## Chain of custody (every pre-registered checkpoint, in order)

1. **1-fold smoke** (H=5): 49s wall-clock; caught a real gate bug en route
   (ST provenance layout — fixed in #324 BEFORE any decision-grade use).
2. **Run 1 (first ignition, commit 863214a)**: structurally sane but
   breached band v1 by the letter → **ABORTED before run 2** per the
   pre-committed `on_breach`. Diagnosis: v1's anchor reference numbers were
   a different metric convention (recorded in `decisions_record`,
   `sanity-band-v1-convention-error`). Band re-registered as v2 (#325).
3. **Run 1 (re-run under v2, commit fa85ddc)**: band v2 ALL PASS —
   net Δ vs anchor +0.08pp (≤5pp), ic_1d Δ +0.0001 (≤0.01), pooled gross
   +9.44% (>0), cost wedge 6.62pp (∈[3,9]).
4. **Run 2 (H=5)** → gated compare (`--prereg-plan --variant 5d`):
   pre-registration GATE PASSED (ancestry + variant + ST parity).

## The verdict (ruler, paired moving-block bootstrap, n_boot=10000, seed=42)

| quantity | value |
|---|---|
| **VERDICT (net)** | **INDISTINGUISHABLE** |
| paired net annualized diff (t−b) | **−0.71pp**, 95% CI [−5.80, +4.44]pp, SE 2.63pp |
| paired gross annualized diff | −0.70pp, 95% CI [−5.80, +4.44]pp |
| pooled net IR | 0.283 → 0.215 |
| pooled gross IR | 0.948 → 0.877 |
| mean ic_1d | +0.0182 → +0.0177 (IC verdict: indistinguishable) |
| **mean ic_5d** | **+0.0288 → +0.0330 (+0.0042; CI low +0.0087→+0.0144)** |
| mean-of-folds net ann. | +3.32% → +2.62% |
| mean-of-folds IR | 0.272 → 0.351 |
| worst drawdown | −13.50% → −15.85% |
| direction vs plan | **treatment<baseline — OPPOSITE the registered expectation** |

**Pre-specified sensitivity slices (verdict STATE stable on both):**

| slice | n days | paired net diff [95% CI] | state |
|---|---|---|---|
| FULL | 1397 | −0.71pp [−5.80, +4.44] | indistinguishable |
| exclude fold-0 (2020Q2) | 1338 | −1.33pp [−6.67, +3.94] | indistinguishable |
| exclude 2020H2 | 1271 | −0.55pp [−6.02, +4.92] | indistinguishable |

## Adjudication against the PRE-REGISTERED rules (none invented post-hoc)

- `if_5d_wins_net` — **NOT met** (net indistinguishable, point negative).
  No ST-on re-verify: the incumbent (H=1) stays production; nothing to
  promote.
- `escalate_to_10d_iff` (gross delta positive AND net indistinguishable) —
  **NOT met**: the gross point estimate is NEGATIVE (−0.70pp; pooled gross
  IR down 0.948→0.877). The only sanctioned path to 10d did not open;
  **10d stays unrun.**
- `terminate_10d_line_iff` (gross not better AND IC decay isomorphic) —
  gross-not-better holds; the isomorphism clause is NOT cleanly met
  (ic_5d improved ~+15% with the CI low up — that IS slower-decay-flavored
  evidence at the signal level). Recorded verbatim rather than rounded off:
  the line closes via the escalation rule NOT firing, not via a claim that
  no signal-level effect exists.

## What the data actually says (for the record, feeding the next battles)

1. **The 5d label DOES carry signal-level value that the backtest cannot
   monetize.** ic_5d rose +15% (0.0288→0.0330) while gross alpha FELL. The
   hypothesis's cost-dilution channel ("longer holding → less turnover →
   keep more net") was structurally unavailable: the trading rule
   (topk=50 / n_drop=5, DAILY rebalance) is horizon-blind, so the cost
   wedge barely moved (gross−net diff ≈ 0.01pp between arms). A slower
   label fed into a daily-rebalance engine just trades the same amount on
   staler information.
2. **This is a direct, quantified handoff to 阶段7 (trade cadence):** the
   holding-period effect can only be monetized where the REBALANCE RULE
   changes, not the label alone. The 阶段7 design should treat
   "label horizon × rebalance cadence" as a JOINT variable.
3. Fold-level detail: the 5d arm is not uniformly worse — it wins 12/23
   folds on IR and improves several drawdown-heavy quarters (2023Q3
   −24.1%→−8.7%), but loses big in 2021Q1 (+3.8%→−19.5%) and 2024Q4
   (+14.7%→−7.8%). High per-fold variance, zero pooled edge — consistent
   with the SE≈2.6pp noise floor the runbook warned about.
4. Deeper drawdown on 5d (−15.85% vs −13.50%) — staler information holds
   losers longer under daily rebalance.

## Follow-ups

- 阶段7 (cadence) design: treat horizon×cadence jointly (batch-C session).
- 阶段8 (factor mining) unaffected; the ic_5d finding suggests horizon-aware
  factor evaluation is worth keeping in the harness.
- BacktestRunner PIT wiring (deferred post-campaign) is now unblocked — goes
  through the re-sign channel.
- The campaign infrastructure (prereg gate incl. ST parity + content-hash
  proof, sanity band, rehearsal harness, ST-off opt-out) is standing
  equipment for every future A/B campaign.

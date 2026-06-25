# Promote REGEN-2 (total-return) to the canonical walk-forward baseline

## Why

PR-1 (#277) landed the REGEN-2 deterministic frozen-score replay anchor
(`tests/regression/fixtures/regen2/walk_forward_baseline_metrics.json`), generated
and reproduced **CI-real** on the project's canonical dependency stack (the
pyproject pin: `numpy<2`, `scipy<1.14`, `pandas<2.3`). REGEN-2 measures
walk-forward excess against the official **SH000300TR total-return** index (vs the
SH000300 **price** index REGEN-A uses), and now satisfies BOTH halves of the
`v2-canonical-backtest-contract` *replay-anchored* requirement: it **is**
replay-anchored, and the total-return benchmark — previously **DEFERRED** — is now
**applied** and proven reproducible.

This change promotes REGEN-2 to canonical **atomically**. The contract couples the
canonical baseline, the benchmark default, and the governance value-pin: splitting
them creates an intermediate contract-violating state (canonical swapped but the
default / pin / contract not yet matching). So a-g land together:

1. **Swap** the canonical root baseline `fixtures/walk_forward_baseline_metrics.json`
   REGEN-A (0.4815 / SH000300) → REGEN-2 (0.278 / SH000300TR); **delete** the ②
   `fixtures/regen2_tr/` staging fixture (superseded by the PR-1 anchor; 0 tracked
   references).
2. **Migrate** the governance value-pin to a two-sided band `0.20 < IR < 0.35`
   bracketing the REGEN-2 canonical mean fold IR (~0.28) and excluding REGEN-A
   (0.48), the old T+2 (0.37), the off-pin ② figure (0.16), and 0 — with a comment
   carrying the honesty caveat below.
3. **Split** the REGEN-A replay test to PRESERVE a REGEN-A price-index control (not
   delete REGEN-A); repoint the canonical replay test at the REGEN-2/TR anchor.
4. **Flip** the canonical benchmark default `SH000300 → SH000300TR` across the 9
   config / default sites; keep REGEN-A's `SH000300` as a preserved PRICE control.
5. **Update this contract**: the total-return-benchmark "deferral note" → "applied".
6. **Machine consistency-guard**: assert the SEMANTIC invariants (canonical default
   == SH000300TR, the REGEN-A price control preserved, the `tr_price_pairs` pairing
   intact) so a future re-flip or new default site is caught red — not a hand-list
   of the 9 known sites.

This is **NOT** a guard relaxation: PR-1 established the replay anchor, so REGEN-2
genuinely satisfies the *anchored + total-return* requirements now; the contract is
updated to match the proven state, not loosened.

### Honest framing (carried from PR-1)

The REGEN-2 canonical mean fold IR (~0.28) is HIGHER than the off-pin ② figure
(0.16) ONLY because fold-0's single-fold IR flips −0.889 → +1.767 on the canonical
numpy<2 stack — a **degenerate-score** (~39 value-buckets / 261 ties at the topk=50
cutoff) sort-tie-break artifact, **NOT signal**. The variance rose with the mean
(SE ~0.43, t ~0.65, 95% CI [−0.59, 1.04] straddles zero), so the conclusion is
unchanged: **"unproven, not disproven"**. The value-pin band + comment record this
fragility; the signal-quality root cause (why 2020Q2 predictions degenerate) is a
phase-6 backlog item. REGEN-A is preserved as a price-index control, untouched.

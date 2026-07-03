# v2-run-comparison-methodology Specification

## Purpose
TBD - created by archiving change add-run-comparison-methodology. Update Purpose after archive.
## Requirements
### Requirement: A run SHALL persist per-fold daily excess-return and daily IC series

Every walk-forward run SHALL persist, as a first-class run artifact, each fold's
**daily excess-return series** (`return − bench − cost`, the values already produced
in `backtest_output.return_series`) and its **daily cross-sectional IC series** (the
per-day IC the `SignalAnalyzer` computes internally, not only the fold-mean). These
series are the ONLY substrate on which a pooled or paired comparison can be computed;
the pre-existing per-fold scalar metrics are unchanged and remain the fold report's
headline. Persistence SHALL be NaN-safe (`allow_nan=False`, matching
`write_fold_report`). A run produced before this contract (no daily series) SHALL be
readable but marked NON-COMPARABLE, so a comparison over it fails loud rather than
silently degrading to the fold-scalar bootstrap. That fail-loud SHALL be ACTIONABLE:
it names the specific run(s) missing the series and states how to backfill (re-run the
walk-forward to regenerate the daily series), because pre-existing runs — including the
committed REGEN-2 canonical baseline — will hit this on first comparison.

Adding this persistence SHALL be PURELY ADDITIVE: re-running an existing walk-forward
produces byte-identical pre-existing outputs (the per-fold scalar metrics and every
other artifact field), with only the new daily series added. A regression test SHALL
prove this zero-side-effect property, complementing the reconciliation guard.

#### Scenario: the comparison substrate is present after a run
- **WHEN** a walk-forward run completes
- **THEN** each fold artifact carries the daily excess-return series and the daily IC
  series, and a comparison tool can read them without replaying the backtest

#### Scenario: a legacy run without series fails loud with an actionable message
- **WHEN** a comparison is requested over a run that predates daily-series persistence
- **THEN** the tool fails loud — naming the non-comparable run and how to backfill it
  (re-run the walk-forward) — rather than falling back to the fold-scalar bootstrap

#### Scenario: adding the series changes nothing else
- **WHEN** an existing walk-forward run is re-run after this contract lands
- **THEN** the per-fold scalar metrics and all other artifact fields are byte-identical
  to before, with only the daily series newly present

### Requirement: Each fold's persisted daily series SHALL reconcile losslessly to that fold's scalars

The persisted daily excess-return series for EACH fold SHALL, when aggregated back to
that fold's annualized information ratio and maximum drawdown, equal the fold's
pre-existing scalar `information_ratio` / `max_drawdown` within a FIXED tight tolerance
(1e-6, the replay-anchor tier) held IN TEST SOURCE (not in the artifact). The check is
PER-FOLD (every fold, not aggregate-only): the daily series is the foundation, so a
sub-tolerance daily error must not slip through to be amplified by pooling/pairing.
This is a machine proof that persistence is LOSSLESS and shares the established scalar
convention — the ruler's root cannot be crooked. It is DISTINCT from the seam bound
below and does not stand in for it.

#### Scenario: every fold's daily series aggregates back to its scalars
- **WHEN** each persisted per-fold daily excess series is aggregated to IR and max drawdown
- **THEN** it matches that fold's stored scalar IR / max drawdown within 1e-6, for
  EVERY fold, else the reconciliation test fails

### Requirement: The pooling seam SHALL be quantified as a separate bounded check

The pooling seam SHALL be quantified as a check SEPARATE from per-fold reconciliation,
with an explicit reported upper bound on its impact on pooled IR. The seam is each
fold-backtest starting from cash — a fold-boundary day carries a full-book transition
that a single continuous run would not — so ONE guard proves persistence is lossless
(the requirement above) while a DISTINCT guard bounds the seam's economic effect on the
pooled statistic (e.g. pooled IR recomputed with fold-boundary days excluded vs
included). The two are not conflated: a lossless-persistence pass must never be read as
a seam-is-negligible pass.

#### Scenario: the seam impact is bounded and reported separately
- **WHEN** pooled IR is computed
- **THEN** the output reports the fold-boundary seam's upper-bound impact (pooled IR
  with boundary days excluded vs included) as a check DISTINCT from per-fold reconciliation

### Requirement: Comparison SHALL report a pooled IR over the true-concatenated daily excess series

The ruler SHALL define **pooled IR** as the information ratio of the TRUE
CONCATENATION of the per-fold daily excess-return series over the union of out-of-
sample days — NOT a per-fold re-standardization — because the realized walk-forward
strategy genuinely switches models at fold boundaries in production, so the switch is
part of the measured strategy, not an artifact to normalize away. The only seam (each
fold-backtest starting from cash) is a bounded, DOCUMENTED approximation whose size is
constrained by the seam-bound guard above. Pooled IR uses all N days directly and
therefore does not inherit the between-fold variance that dominates the mean-of-fold-
IRs bootstrap. The output SHALL annotate pooled IR as **the realized performance of
the walk-forward STUDY PROTOCOL** (which includes switching models at every fold
boundary and each fold starting from cash), explicitly distinct from "the return of a
single continuously-run production strategy" — so the number is never misread as
"what running this in production would earn".

#### Scenario: pooled IR is the concatenated-series IR, labelled as study-protocol
- **WHEN** the ruler computes pooled IR for a run
- **THEN** it is the IR of the concatenated per-fold daily excess-return series over
  all OOS days, and the output states BOTH the definition (true concatenation, not
  per-fold standardization) AND that it measures the WF study protocol, not a
  continuous production strategy

### Requirement: Comparison SHALL quantify the difference with a paired moving-block bootstrap

The ruler SHALL quantify the A-vs-B difference from the **paired daily difference**
`d_t = B_excess[t] − A_excess[t]` on the shared date set, resampled with a **moving-
block bootstrap** to honour autocorrelation (an i.i.d. bootstrap understates the SE of
an autocorrelated series). The block length SHALL default to the measured
autocorrelation-decay length of the difference series (not a holding-period proxy),
SHALL be configurable, and the chosen value together with how it was derived SHALL be
recorded in the result provenance. The output SHALL include the annualized paired
difference, its bootstrap standard error, and a 95% confidence interval.

#### Scenario: the paired CI reflects autocorrelation
- **WHEN** the paired difference series is autocorrelated
- **THEN** the moving-block bootstrap CI is at least as wide as the i.i.d. bootstrap
  CI, and the block length used is recorded in the output provenance

### Requirement: Paired comparison SHALL align on the date intersection and fail loud below an overlap floor

Because different label horizons yield different available dates, the ruler SHALL
compute the paired difference on the **date intersection** of the two series, SHALL
report the overlap fraction **measured as intersection ÷ the SHORTER series**, and
SHALL **fail loud** — refuse to emit a verdict — when that fraction falls below a
configurable floor (default 90%), so a comparison is never silently made on a biased
date subset. The floor is chosen so it does NOT block the headline use case: a
label-horizon comparison loses dates ONLY at the tail (the longer horizon needs more
future bars), so the longer-horizon series is NESTED inside the shorter-horizon one —
e.g. over the 231-day guard window a 2-day vs 10-day label yields intersection = 100%
of the shorter series (96.5% of the longer). The floor therefore catches a GROSS
mismatch (a wrong window, a bug), not the expected small label tail. A future
comparison whose legitimate design needs a lower floor SHALL set it explicitly with a
recorded justification (still rejecting excessive non-overlap), not silently.

#### Scenario: partial overlap is reported; large mismatch refuses a verdict
- **WHEN** A and B share most but not all dates
- **THEN** the comparison runs on the intersection and reports the overlap fraction;
- **WHEN** the overlap is below the configured floor
- **THEN** the ruler refuses to emit a verdict and states the overlap deficit

### Requirement: A comparison verdict SHALL be fail-loud and never rest on a point estimate

The ruler SHALL translate the paired CI into a verdict that can only be one of:
"indistinguishable at this power" when the CI includes zero; "B significantly better"
or "B significantly worse" when the CI lies strictly on one side. It SHALL NEVER
declare a winner from a point estimate whose CI straddles zero — the honest answer
under the noise floor is "indistinguishable", and the ruler SHALL say so plainly.

#### Scenario: a straddling CI yields "indistinguishable", not a winner
- **WHEN** the paired 95% CI of the A-vs-B difference includes zero
- **THEN** the verdict is "indistinguishable at this power", and no config/label is
  declared the winner on the point estimate alone

### Requirement: Backtest excess SHALL be the primary arbiter and any IC contradiction SHALL be flagged

The realized **backtest excess** SHALL be the primary arbiter of a comparison; daily
IC is a diagnostic. When the backtest verdict and the IC verdict disagree in sign or
conclusion, the ruler SHALL resolve to the backtest AND **flag the contradiction
explicitly** in the output — institutionalizing the gross/net-decomposition lesson so
that an "IC positive but backtest negative" pattern can never be silently read as a
win.

Furthermore, when the primary verdict is **"indistinguishable at this power"**, the
ruler SHALL NOT present it as "equally good — pick either". It SHALL MANDATORILY emit,
alongside that verdict, the diagnostic breakdown (gross vs net excess, IC, and the
sign/direction of each) AND an explicit warning that **"indistinguishable" ≠
"equivalent"** and that a divergence may be masked by the primary metric. This turns
the n_drop rescue — where net excess was "indistinguishable" across configs while the
gross-alpha decomposition exposed a −6.23% config — from an ad-hoc judgement into a
mandatory companion of every indistinguishable verdict.

#### Scenario: IC and backtest disagree
- **WHEN** the daily-IC comparison favours B but the backtest-excess comparison does
  not (or vice-versa)
- **THEN** the ruler reports the backtest as the decision AND surfaces an explicit
  contradiction flag naming both verdicts

#### Scenario: an "indistinguishable" verdict ships with its diagnostics
- **WHEN** the primary backtest verdict is "indistinguishable at this power"
- **THEN** the output mandatorily includes the gross-vs-net / IC / direction
  breakdown AND the explicit "indistinguishable ≠ equivalent — check for a masked
  divergence" warning, never a bare "pick either"

### Requirement: Each comparison SHALL carry a pre-registered hypothesis and its limitation envelope

Each comparison experiment SHALL carry a **pre-registered hypothesis** as a COMMITTED
ARTIFACT — the single planned A-vs-B comparison and its expected direction, committed
to git BEFORE the compared runs exist — and the comparison output SHALL record that
artifact's **git commit hash**, so that "the hypothesis preceded the experiment" is
provable from git history rather than trusted to a human ("cannot change post-hoc" is
machine-verifiable: the hypothesis commit must be an ancestor predating the run
artifacts). The ruler SHALL flag any comparison whose compared variant set exceeds the
pre-registered plan (design-time control of multiple comparisons; a Bonferroni/FDR
correction is only a backstop and is documented to be near-undetectable under SE≈0.42,
so the discipline must be design-time). Every output SHALL ALSO carry its statistical-
limitation envelope: the regime-heterogeneity / single-period caveat (the bootstrap
narrows sampling SE but does not resample structural regime uncertainty), the
block-length provenance, and the date-overlap fraction — pinned by a CI-runnable test
so a verdict cannot be emitted stripped of its honesty envelope.

#### Scenario: an unregistered extra variant is flagged
- **WHEN** a comparison run compares more variants than the pre-registered hypothesis
  named
- **THEN** the ruler flags the excess as an unregistered multiple comparison

#### Scenario: a verdict cannot shed its caveats
- **WHEN** a comparison output is produced
- **THEN** a CI-runnable test requires the regime-heterogeneity caveat, the
  block-length provenance, the pre-registration reference, and the date-overlap
  fraction to be present, else CI fails


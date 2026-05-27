## Context

The V2 canonical backtest pipeline runs unconstrained: the
``TopkDropoutStrategy`` from qlib produces positions; the
``BacktestRunner`` reports them and the resulting metrics
without any cap on per-name, per-board, or cash-buffer
exposure. Audit P0-1 ranked "no risk constraints" as the
top outstanding governance gap because the system could in
principle be pointed at live capital.

The challenge: qlib's ``Exchange``/``Strategy`` API does not
expose a clean "constrain my proposed positions" hook. The
realistic options are:

1. Pre-trade — fork ``TopkDropoutStrategy`` so it solicits
   constraints before emitting positions, OR pre-process
   predictions so qlib's strategy naturally respects the
   constraints (drop overweight names from the topk).
2. Post-trade — let qlib run, then validate the output
   positions map and either raise or log + replace with a
   clipped copy.

Pre-trade is correct: it changes WHAT TRADES qlib actually
executes. Post-trade is descriptive: it tells the operator what
constraints would have been violated, but the trades happened
anyway and ``return_series`` reflects them.

This PR ships **post-trade only**. The rationale is in the
design decisions below; the short version is: shipping the
constraint surface, the four defaults, the enforcement modes,
and the BacktestRunner wiring AT POST-TRADE is enough to
catch every existing portfolio that would have violated, and
delivers the smallest blast-radius integration. Pre-trade
clipping is a follow-up — it changes the qlib executor path
and needs its own design pass.

## Goals / Non-Goals

**Goals:**
* Replace the fail-closed-stub posture with a real, callable
  constraint engine that has documented defaults and emits
  precise violation reports.
* Make every BacktestRunner call EITHER use the engine OR emit
  a "no risk constraints active" WARN — no third state.
* Two enforcement modes so backtest-validation users (RAISE)
  and live-deployment users (WARN_AND_CLIP) get the right
  behaviour out of the box.
* Cover the four most-asked-for constraints: per-name,
  per-board, cash-buffer-min, max-leverage. All bounds are
  configurable; defaults match conservative long-only retail
  practice.
* Pin the public surface with a governance test so future
  refactors can't silently drop it.

**Non-Goals:**
* Pre-trade constraint enforcement (see Decisions below).
* Industry-level caps beyond the board heuristic (waits on
  real industry artifacts; see Phase E).
* Stop-loss / drawdown / turnover constraints (depend on
  P&L history; separate spec change).
* Removing or breaking ``RiskConstraintEngine`` — its
  fail-closed stub stays in place unchanged.
* Wiring risk constraints through ``PipelineConfig`` /
  ``WalkForwardConfig`` YAML. That's a follow-up — this PR
  ships the runtime surface; YAML-level config can land
  after the design has been validated by direct API use.

## Decisions

1. **Post-trade only, no pre-trade clipping.**
   - Decision: ``MinimalRiskConstraints.apply()`` runs
     AFTER qlib produces the positions map. The clipped map
     (in WARN_AND_CLIP mode) is informational, not
     retroactively rewritten back into qlib's executor.
   - Rationale: pre-trade clipping needs either (a) a
     constraint-aware strategy that replaces
     ``TopkDropoutStrategy``, or (b) a prediction-side
     pre-processor that drops names which would violate. Both
     have non-trivial design surface; (a) interacts with
     qlib's signal-to-trade flow, (b) interacts with the
     ``signal_to_execution_lag`` semantics.  Shipping
     post-trade first delivers the constraint surface + the
     observability AT MINIMUM RISK; pre-trade follows once
     the operator-facing API is stable.
   - Trade-off: in WARN_AND_CLIP mode the
     ``return_series`` / ``risk_analysis`` reflect what
     qlib actually ran (unclipped), while ``positions``
     reflects the clipped allocation. This is documented on
     the field; the operator gets both maps via the
     ``positions`` and a new ``positions_pre_clip`` field
     on ``CanonicalBacktestOutput``.

2. **``MinimalRiskConstraints`` is a frozen dataclass, not a
   subclass of the legacy stub.**
   - Decision: new class, lives in
     ``src/core/risk_constraints.py`` next to the existing
     ``RiskConstraintEngine`` stub. No inheritance.
   - Rationale: the stub is a "compat surface for any caller
     that ever lands here". Coupling the new engine to the
     stub would create awkward inheritance semantics
     ("subclass that doesn't raise"). Independent classes
     keep both surfaces explicit.

3. **Four constraints, two modes — minimal viable surface.**
   - Decision: ``max_per_name``, ``max_per_board``,
     ``cash_buffer_min``, ``max_leverage``. Two modes:
     ``RAISE``, ``WARN_AND_CLIP``.
   - Rationale: these four cover the immediate "single name
     blows up the portfolio" / "all-in on one sector" /
     "no cash for friction" / "accidental leverage" failure
     modes. Anything else (stop-loss, turnover, industry,
     factor exposure) is dynamic or needs a richer
     classifier. Two modes match the two real callers:
     backtest validation (fail-loud) and live deployment
     (don't-kill-the-run).

4. **Board classification reuses ``board_heuristic``.**
   - Decision: ``max_per_board`` aggregates weights by
     ``board_heuristic.classify_instrument``.
   - Rationale: ``board_heuristic`` is the only zero-cost
     classifier shipped in this repo. It's an APPROXIMATION
     ("board ≠ industry" — see audit P0-5 finding) but it's
     honest about it (every bucket is prefixed ``board_``
     and tagged with ``BOARD_HEURISTIC_TAXONOMY_ID``).
     The constraint message will say "board concentration",
     not "industry concentration".
   - Trade-off: an operator who genuinely cares about industry
     concentration (banks vs. semis vs. utilities, all on the
     Shanghai Main Board) has to wait for Phase E industry
     artifacts. Documented limitation.

5. **Long-only assumption baked into ``max_leverage``.**
   - Decision: ``max_leverage`` is defined as
     ``sum(abs(weight) for instrument in positions) <= max_leverage``.
     For a long-only portfolio with cash buffer, this is the
     same as "no leverage" — sum of weights ≤ 1 minus the
     cash buffer.
   - Rationale: the canonical backtest path uses
     ``TopkDropoutStrategy`` which is long-only by design.
     ``max_leverage`` could be relaxed to allow short positions
     later, but until the codebase has short-supporting
     strategies, "absolute-weight-sum ≤ N" is the right
     proxy.

6. **WARN-on-None instead of fail-closed-on-None.**
   - Decision: when ``BacktestRunner.run(risk_constraints=None)``
     is called, the run proceeds but emits a single WARN log
     ("backtest ran with NO risk constraints active").
   - Rationale: existing callers (every existing test and
     research script) don't pass risk_constraints. Failing
     them all closed would break the test suite. The WARN
     surfaces the omission so operators KNOW they're running
     without a safety net; the run still completes. A future
     governance change could escalate this to a hard fail once
     callers have migrated.

7. **Violation reports are list-of-records, not list-of-strings.**
   - Decision: ``MinimalRiskConstraints.apply()`` returns
     ``RiskConstraintsApplyResult`` carrying a
     ``violations: tuple[RiskConstraintViolation, ...]``
     where each violation is a frozen dataclass with
     ``(date, constraint_name, instrument_or_bucket, actual,
     limit, details)``.
   - Rationale: a string list would surface for human reading
     but a downstream tool (UI, report) can't filter or
     aggregate strings. Structured records compose with the
     existing pipeline_result_artifacts JSON writer.

8. **Clip semantics: proportional scale-down to the cap,
   redistribute to cash, not to other names.**
   - Decision: when a per-name weight exceeds ``max_per_name``,
     clip that name to the cap and add the difference to cash.
     Same for ``max_per_board``: each over-cap board is
     proportionally scaled until aggregate hits the cap.
     ``cash_buffer_min`` violation: scale every instrument
     weight down proportionally until cash share ≥ min.
   - Rationale: redistributing excess weight to OTHER names
     requires picking which names (alphabetically? by
     prediction rank?), each choice has its own bias. Adding
     to cash is the most neutral, conservative choice — it
     systematically under-runs the leverage cap which is the
     safe direction. Documented on the result type.

9. **No YAML / config integration in this PR.**
   - Decision: ``BacktestRunner.run`` accepts the
     ``risk_constraints`` kwarg; constructing a
     ``MinimalRiskConstraints`` instance is the caller's
     responsibility. Pipeline / WalkForward configs do NOT
     gain a ``risk_constraints`` block in this PR.
   - Rationale: keeps the PR focused on the runtime + the
     contract. Wiring through YAML adds three more
     decisions (which mode is the default? how do you
     express per-name overrides? does WalkForward share the
     constraints across folds?) — each is its own design
     surface and they should be considered together once
     the runtime contract has stabilised.

## Risks / Trade-offs

* **Post-trade clipping doesn't actually constrain the
  backtest.** A live operator using WARN_AND_CLIP must know
  the backtest result is for the unconstrained allocation;
  the clipped map is for live trades. Documented on the
  result fields and in the apply() docstring.
* **Board ≠ industry.** Operators who treat per-board cap as
  "industry diversification" will be surprised. The
  violation messages explicitly say "board" and reference
  ``BOARD_HEURISTIC_TAXONOMY_ID``; the design comment in
  ``board_heuristic.py`` already calls this out.
* **The four defaults are opinionated.** 5% / 40% / 1% / 1.0
  reflect conservative long-only A-share retail practice
  but are not universal. They are documented and easily
  overridden at construction time.
* **WARN-on-None creates log noise on existing test runs.**
  Every existing ``BacktestRunner.run`` call now logs one
  WARN. We accept this — the alternative is to silently let
  existing callers run unconstrained, which is exactly what
  this PR is trying to fix.

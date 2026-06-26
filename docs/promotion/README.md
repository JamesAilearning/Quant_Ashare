# ④ Production-model promotion — recon & criteria

A **freshness** promotion of the live stock-picking model (`alpha158_lgb_pit.pkl`). The
incumbent trains to **2023-12-20** and early-stopped on **2024** (so 2024 is NOT clean OOS
for it). The candidate adds that year to training; both are compared on a window neither
has touched.

> **What ④ does — and does not.** ④ promotes **time-freshness** (the candidate has seen one
> more year), NOT proven profitability. The incumbent's clean-year excess is **negative**
> (see below), so "candidate not worse than incumbent" can mean "candidate is also negative
> excess, just not more so". That negative excess is an **alpha-decay** signal for the
> Alpha158 + LGB + top-50 method in the current regime — refreshing data treats the symptom,
> not the cause; the cure is a **phase-6** item, not ④.

## The eval tool

`scripts/eval_frozen_model_oos.py` evaluates a **frozen** (already-trained) model over a
window WITHOUT retraining — load the pkl, build the Alpha158 dataset normalized to the
model's own fit window, predict, and compute IC / IR / backtest (ann return, IR, max
drawdown, turnover, holding concentration) + a per-date degeneracy scan. It reuses the
**exact** canonical signal + backtest config the WF / REGEN replay uses
(`replay_frozen_baseline` constants + `CanonicalBacktestInput`), so the incumbent baseline
and the candidate are computed identically (variable isolation). Real compute on the live
bundle (read-only), **not** a retrain — run FOREGROUND. `--guard-end` must stop ≥2 bars
short of the bundle tail (the backtest needs a T+1 bar to fill on).

## Comparison origin — the incumbent on the guard window

`incumbent_guard_baseline.json` fixes down the live model's metrics on the clean guard
window **2025-07-01 → 2026-06-12** (231 days; the bundle tail is 2026-06-17, trimmed 3 bars
for the T+1→T+2 label lookahead):

| metric | incumbent |
|---|---|
| IC(1d) / IC(5d) | **+0.0197 / +0.0387** (positive ratio 59%) |
| IC-IR(1d) | +0.147 |
| backtest excess (with cost, vs SH000300TR): annualized | **−8.9%** |
| information ratio | **−1.19** |
| max drawdown | −10.4% |
| daily turnover | 0.393 |
| concentration (median holdings / top-10 share / HHI) | 50 / 0.230 / 0.0202 (diffuse) |
| **degeneracy** | **0 degenerate days** (min unique 289 / 300) — clean |

The model has weak positive *ranking* signal (IC>0) but its top-50 selection **underperformed
the total-return benchmark by ~9%** over the most recent clean year.

## Candidate split (variable isolation)

- **train** 2018-01-02 → 2024-12-18 (incorporates the year the incumbent only early-stopped on)
- **valid (early-stop)** 2025-01-02 → 2025-06-30
- **guard window** 2025-07-01 → 2026-06-12 (clean OOS for BOTH: incumbent OOS=2025+, candidate
  valid ends 2025-06)
- Hyperparameters, Alpha158, csi300, T+1→T+2 label, ST-mask all UNCHANGED — the only variable
  is the shifted data window. Run: `eval_frozen_model_oos.py --fit-end 2024-12-18
  --valid-start 2025-01-02 --valid-end 2025-06-30 --guard-end 2026-06-12 --model <candidate>`.

## Promotion criteria (asymmetric — "freshness must not make it worse")

**Hard vetoes** (any red → stop, do not promote):
1. **Degeneracy** — the candidate's guard-window predictions must not collapse into few
   score buckets (the incumbent is clean: 0 degenerate days; a candidate-specific degenerate
   day is a candidate problem).
2. **Prediction sanity** — no large-area NaN / abnormal squeezed values.
3. **Behavioral guard** — turnover / holding-concentration / drawdown must not jump versus
   the incumbent, even if IC is fine.

**Asymmetric reference** (not a veto unless *significantly* worse):
4. IC / IR candidate vs incumbent on the guard window — the bar is **"not significantly
   lower"** (within the cross-fold noise floor, SE ≈ 0.42), NOT "must be higher". Closer to
   zero / positive → freshness helped (strongest rationale); flat → not-worse holds (and
   reinforces the alpha-decay read); significantly worse (e.g. IR −1.8) → veto + investigate
   why a year more data made it worse.

All hard vetoes green + IC/IR not significantly worse → the **operator manually promotes**
(rationale: freshness), saving the candidate separately, backing the incumbent up as
`_pre_promote`, and switching the config — reusing the existing promote/rollback machinery
(the incumbent itself is a `_candidate` promotion with a `_pre_promote` backup).

## Known caveat (applies equally to incumbent + candidate — fair comparison)

`SignalAnalyzer._fetch_returns` bypasses the PIT post-delist mask (audit P0-6): IC may
absorb stale / forward-filled closes for tickers delisted within the window. Same class as
the 副线2 P3+P7 items; worth fixing for all IC/excess computations, but symmetric here so the
incumbent-vs-candidate comparison is unaffected.

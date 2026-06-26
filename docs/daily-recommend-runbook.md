# Daily recommendation runbook (manual morning step)

Operator runbook for the **every-morning manual stock-recommendation run**. This
is the one step that stays in human hands: the scheduler (阶段5 PR-P) only ever
runs the **data update** (`scripts/daily_update.py`); it never runs recommend.

## Scope and red line

- **This step is manual, every morning, by the fund manager.** It is NOT
  scheduled and MUST NOT be put into any automated chain.
- **The output is a decision aid, not an order.** `daily_recommend` produces a
  ranked candidate buy list for the entry session after its decision day T (see
  [Which session is the list for?](#which-session-is-the-list-for) — by default
  that entry day is the bundle's most recent session, not a future one). It places
  no trades, sends nothing to a broker, and makes no sizing/portfolio decision. The
  buy/sell decision is always made by a human after reading the list.
- The run is **fail-closed**: every freshness / completeness / look-ahead guard
  refuses to emit rather than print a silently-wrong list. So **a run that exits
  0 with a buy list is a run whose guards all passed** — that is the core
  trust signal (see [How to judge the list](#how-to-judge-the-list)).

## Prerequisite: the data update must have completed first

`daily_recommend` scores on whatever the live qlib bundle holds; it does not
fetch. So the morning order is always **update first, recommend second**:

1. **Data update** — `scripts/daily_update.py` (run overnight/earlier, or by
   hand). It fetches tushare → rebuilds the qlib bins into `<provider>.new` →
   validates → atomic-swaps the live bundle. Its trading-calendar gate (PR-O)
   makes it a clean `exit 0` no-op **only on weekends** (no fetch/build/swap), so
   on a Saturday/Sunday there is no new bundle and the most recent list still
   stands. An A-share **weekday holiday** does NOT take that branch: the run
   proceeds through fetch → rebuild → swap, and although there is no new price
   bar, the aggregates (stock_basic / namechange / suspend_d) can still refresh
   and a rebuilt bundle can be swapped — so check the update's exit code / logs
   rather than assuming nothing changed.
2. **Recommend** — this runbook.

You do **not** need to clear any feature cache before recommending:
`daily_recommend` builds its as-of-T Alpha158 cross-section fresh on every run
(no `cache_dir`), so there is no stale-cache risk for this step.

Confirm the update actually landed before trusting the list — **read the printed
`entry_date` / bundle tail** (or check `daily_update`'s exit code and logs). The
recommend run's bundle-freshness guard only refuses when the bundle's last trading
day lags *today* by more than `--bundle-max-age-days` (default 14), so it catches a
grossly stale bundle (weeks/months behind) but NOT a single missed daily update: a
bundle 1–13 days behind still scores and exits 0 on yesterday's data. The guard is a
backstop against stale prices, not proof that today's update ran.

## The command

```sh
# Top 50, all defaults. Decision day T = the bundle's second-to-last trading day;
# entry T+1 = the bundle tail (see "Which session is the list for?" below).
python scripts/daily_recommend.py
```

`scripts/daily_recommend.py` (NOT `python -m`; the `__main__` guard +
`freeze_support()` are mandatory on Windows — a missing guard fork-bombs the
joblib spawn workers). Paths default to the Phase B clean-PIT layout and are
overridable per-flag or via the `QUANT_*` env vars documented in
[operations-env-vars.md](operations-env-vars.md); the same vars drive the YAML
configs, so setting e.g. `QUANT_PROVIDER_URI` once moves both. CLI flags take
precedence over the env defaults.

Common variations:

```sh
# A specific historical decision day, smaller list.
python scripts/daily_recommend.py --as-of 2025-06-30 --topk 30

# Intentional historical run on an older bundle (relaxes the 14-day freshness
# guard). Use ONLY when you deliberately want an old as-of; never to paper over
# a bundle that simply was not updated.
python scripts/daily_recommend.py --as-of 2025-01-31 --bundle-max-age-days 400
```

Flags you will actually reach for (defaults in parentheses):

| Flag | Default | Meaning |
|---|---|---|
| `--as-of` | latest PIT trading day with a following session | Decision day **T** (data cutoff); entry **T+1** = the next trading day in the bundle. With the default, T+1 is the bundle tail — see [Which session is the list for?](#which-session-is-the-list-for). |
| `--topk` | `50` | Buy-list size (tradable, non-ST names). |
| `--out-dir` | `output/daily_recommend` | Where the csv/json land. |
| `--instruments` | `csi300` | Universe. |
| `--bundle-max-age-days` | `14` | Max calendar days the bundle's last day may lag *today* before it is refused as stale. |
| `--st-max-age-days` | `7` | Max days the ST/active-stocks snapshot may lag T before it is refused as stale. |
| `--allow-holey-recommend` | off | **Last resort.** Recommend even on a bundle built from an incomplete fetch (or one with no integrity stamp). See the warnings below. |

`--model` / `--provider-uri` / `--delisted-registry` / `--name-source` /
`--fit-start` / `--fit-end` exist for non-default layouts; the fit window MUST
match the model that produced the artifact.

## Which session is the list for?

**Read the printed `entry_date` every time** — it tells you which session the list
is actually for, and with the **default** (no `--as-of`) that is *not* a future
session:

- **Default (no `--as-of`)**: the decision day **T** is the *second-to-last*
  trading day in the bundle and the entry day **T+1** is the **last** day in the
  bundle (its calendar tail). So if you updated the bundle through the most recent
  close, the entry day is that **already-completed** session — not tomorrow.
- **Why it cannot look further forward**: each pick is screened for tradability
  (suspension / one-price-lock) on the *entry* day, which requires that day's bars
  to be on disk. A not-yet-traded session has no bars, so the tool refuses to treat
  the bundle tail as a decision day (`--as-of <tail>` errors with "no next trading
  day (T+1) to enter on"). It cannot emit a list for a session not yet in the
  bundle.

Operationally: the picks are the model's ranking **as of T**, with entry-day
tradability validated against the bundle tail — a current signal to inform the
upcoming session's decision. But because the `entry_date` label is the bundle's
last session, do **not** read it as "buy at tomorrow's open" without applying your
own judgement for the actual forward session. To score a specific historical
decision day, pass `--as-of <that day>` (entry then resolves to the next trading
day after it that exists in the bundle).

## What it writes

Three files under `--out-dir`, stamped by the as-of date `T`
(`daily_recommendation_<T>.*`), plus a terminal summary:

- **`daily_recommendation_<T>.csv`** — the buy list (`utf-8-sig`, opens cleanly
  in Excel). Columns: `as_of_date, entry_date, rank, stock_code, stock_name,
  predicted_score, tradable_flag, unavailable_reason`.
- **`daily_recommendation_<T>.json`** — the same picks plus the run summary
  counts (`n_scored, n_masked, n_st_excluded`).
- **`daily_recommendation_<T>_scored_full.csv`** — the **full audit frame**:
  every scored name including the ones dropped, with `tradable_flag` and the
  `unavailable_reason` (`suspended` / `one_price_lock` / `unavailable` / `st`).
  Read this when you want to know *why* a name you expected is missing.

The terminal header echoes both time points and the funnel:

```
  as_of_date (data cutoff, T)   : <T>
  entry_date (suggested buy, T+1): <T+1>
  universe=csi300  scored=<n>  untradable_masked=<n>  st_excluded=<n>  buy_list=<k>
```

Note on display: if Chinese names look garbled in the terminal that is a console
code-page (GBK) display issue only — the csv/json are correct UTF-8.

## How to judge the list

The recommend path is a stack of fail-loud guards; **a clean `exit 0` already
means every one of them passed**. Concretely, a successful run guarantees:

- **No look-ahead.** Features for T use only data `≤ T` (handler `end_time=T`),
  normalization is fit on the *training* window, and an always-on guard refuses
  if any feature row is dated after T.
- **Fresh prices.** The bundle's last trading day is within
  `--bundle-max-age-days` (14) of today — so you are not ranking on weeks-old
  prices.
- **Same-cycle ST/name view.** The active-stocks snapshot exists, is schema-valid
  and non-empty, its embedded snapshot date is within `--st-max-age-days` (7) of
  T, AND within `--bundle-max-age-days` of the bundle tail — i.e. names + the ST
  set were refreshed in the same cycle as the prices. ST/\*ST names are excluded
  *before* the Top-K slice, so the list holds K tradable non-ST picks.
- **Complete-fetch provenance.** The bundle carries a `_fetch_integrity.json`
  stamp confirming it was built from a complete tushare fetch (unless you
  overrode — see below).
- **Tradable on the entry day.** Each pick is not suspended / one-price-locked on
  T+1 (the day it would actually fill), matching the backtest's execution-day
  masking.

Quick sanity read on the summary line:

- The funnel reconciles as `scored + untradable_masked + st_excluded ≈ universe
  size` (≈300 for csi300). `scored` alone is the **tradable, non-ST** pool, so on
  days with suspensions or ST names it is correctly *below* the universe size — a
  low `scored` with matching `untradable_masked` / `st_excluded` is normal, not an
  alarm. A shortfall the three counts don't account for is worth a look in the
  audit csv.
- `st_excluded` being a small non-zero number is the ST filter working; `0`
  every day on csi300 is plausible (csi300 rarely holds ST) but worth a glance
  at the snapshot date if it surprises you.
- `buy_list` should equal `--topk` unless the tradable pool was smaller.

**Treat the list as NOT trustworthy for trading when:**

- You had to pass **`--allow-holey-recommend`** — the bundle ranks on
  survivorship-incomplete data. Fine for research, not for a real buy decision.
- You raised **`--bundle-max-age-days`** beyond a normal holiday gap to get past
  a staleness refusal instead of updating the data — you are then ranking on
  knowingly stale prices.
- The entry date `T+1` is far from `T` (a long gap implies you ran against an old
  as-of).

## When it refuses — what each failure means

The run prints a domain error and exits `1` rather than emitting. The common
ones and their fix:

| Message (gist) | Cause | Fix |
|---|---|---|
| `Price/feature bundle is STALE: last trading day … lags today …` | The data update did not land (bundle behind > 14 cal days). | Re-run `scripts/daily_update.py`, then recommend. Only raise `--bundle-max-age-days` for a deliberate historical run. |
| `Bundle … has no fetch-integrity stamp` / `was BUILT FROM A HOLEY tushare fetch` | Incomplete or unstamped bundle. | Re-fetch to fill holes and rebuild the bins. `--allow-holey-recommend` only for an intentional research run. |
| `… UNREADABLE fetch-integrity stamp …` | Corrupt provenance. | Rebuild the bundle. This is NOT overridable by `--allow-holey-recommend` (the override accepts incompleteness, not corruption). |
| `ST snapshot … is stale` / `is INCONSISTENT with the price bundle` | `active_stocks.parquet` was not refreshed with the prices. | Re-fetch tushare `stock_basic` (refreshes the snapshot), then recommend. |
| `as-of date … is the last day in the PIT calendar; there is no next trading day (T+1)` | T has no entry session in the bundle. | Use an earlier `--as-of`, or extend the bundle. |
| `as-of date … is not a trading day` | Bad explicit `--as-of`. | Pass a real trading day. |

A refusal is the system doing its job — fix the underlying data and re-run, do
**not** reach for an override to silence it unless you genuinely intend a
research run on partial/old data.

## Decision stays with the human

The buy list is the model's ranked candidates with the untradable and ST names
already removed. Position sizing, risk limits, how many of the Top-K to act on,
and whether to act at all are the fund manager's call. Nothing in this pipeline
places or sizes a trade.

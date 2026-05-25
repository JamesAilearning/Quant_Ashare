# Add UI Training Date Guards

## Why

Operators can currently launch training jobs with date ranges that are invalid
for the selected provider. Recent failures were avoidable: validation dates
overlapped training dates, `test_end` touched the provider's final trading
date, and the selected instrument universe did not exist in the generated
provider bundle.

## What Changes

- Show provider coverage metadata in the Config & Run page when it can be
  discovered from adjacent `manifest.json` / `validation.json` artifacts.
- Validate pipeline train/valid/test ordering before launching a UI job.
- Validate provider coverage and require a tail trading-day buffer for
  backtest jobs when the provider calendar is available.
- Validate that named instrument universe files exist in the provider.

## Non-Goals

- Do not change Pipeline, WalkForward, BacktestRunner, qlib, or Tushare runtime
  behavior.
- Do not call qlib data APIs from the UI guard.
- Do not compute or alter official metrics.

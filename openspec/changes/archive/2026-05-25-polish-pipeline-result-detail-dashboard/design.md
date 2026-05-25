# Design: Pipeline Result Detail Polish

## Decisions

- The Results page remains read-only. Every new chart, filter, or export copies
  values from existing artifacts and never computes replacement official
  metrics.
- Header polish uses Streamlit-native controls plus CSS. Run IDs and run
  directories are exposed as copyable text fields because opening a local
  folder from a browser is not portable or safe.
- NAV and drawdown use the same filtered `nav.parquet` frame. The selector
  filters displayed rows only; stored artifacts remain unchanged.
- Monthly heatmap consumes `metrics.json["monthly_returns"]` when present and
  falls back to the existing table if Plotly is unavailable.
- Log search/severity filters operate on log text already read from job
  artifacts. Missing logs remain an empty state rather than an error.

## Governance

This change is UI-only and does not affect canonical backtest, pipeline, or
walk-forward semantics. No experimental or research behavior is promoted.

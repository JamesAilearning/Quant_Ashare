# Regime Feature Exploration

## Hypothesis
Cross-sectional dispersion of 5-day returns correlates negatively with
model IC — when the market is compressed (low dispersion), the model's
alpha signal has less predictive power.

## Plan
1. Compute rolling 60-day cross-section std of 5d returns for CSI300
2. Compute rolling 60-day CSI300 market return
3. Overlay with per-fold mean IC from walk-forward runs
4. If Pearson r > |0.4|, consider adding as feature to Alpha158-extended

## Status
Placeholder — not yet implemented. Requires a qlib bundle to query
close prices.

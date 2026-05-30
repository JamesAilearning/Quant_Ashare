"""Daily stock-recommendation inference (Phase B, ring 5).

Turns a trained Alpha158 + LGB model artifact into a dated, ranked,
tradability-filtered buy list for the next session. This package is the
production-inference endpoint of the link

    tushare -> PIT -> ML train -> daily recommendation.

Look-ahead safety is a first-class, test-enforced contract: for decision
date ``T`` the feature cross-section is built from data ``<= T`` only and
the forward-looking training label is never read. See
``openspec/changes/add-daily-stock-recommendation`` and
``docs/phase_b_result.md``.
"""

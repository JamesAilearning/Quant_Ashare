"""Tushare data-source integration (v1: industry classification only).

Why this module exists
----------------------
The qlib bundle covers OHLCV well but has no real industry classification —
the rest of the codebase falls back to ``board_heuristic.classify_instruments``
which buckets by listing-venue prefix (``board_SH_Main`` etc.). That is
fine for diagnostics; it is *not* a real industry taxonomy and the
attribution / risk-constraint layers are explicit about that limitation.

Tushare provides Shenwan industry classification via ``index_classify`` /
``index_member``. This package wires that data into V2's existing
:class:`TaxonomyArtifactPublisher` artifact path so:

- The Tushare integration is the only place that talks to the network.
- All downstream consumers (``risk_constraints``, ``performance_attribution``)
  read the same on-disk taxonomy artifact regardless of which source
  produced it. No vendor lock-in baked into the consumer layer.

Scope (v1)
----------
- Shenwan L2 (~120 industries) only.
- Static snapshot: one ``(instrument, industry_code)`` mapping per
  publish call. Time-varying classification (industry changes mid-period)
  is a v2 feature.
- Token via ``TUSHARE_TOKEN`` environment variable. Never read from YAML
  to avoid accidental secret commits.
"""

from __future__ import annotations

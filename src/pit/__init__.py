"""PIT (Point-in-Time) query layer for A-share survivorship correction.

Consumes the artifacts produced by ``src.data.pit`` (Phases A-B) and
exposes a query API that downstream factor mining / training / backtest
modules consume via :class:`PITDataProvider`.
"""

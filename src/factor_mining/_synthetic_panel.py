"""Shared synthetic OHLCV panel + forward return builder.

Previously copy-pasted into both ``miner.py`` and ``promote.py`` —
including the same qlib-label alignment convention that bug.md
audited as a bug (it wasn't; see P1-6 clarification comment below).
Consolidating here ensures the two callers never drift, and any
future change to the panel shape only has to happen in one place.
(bug.md P2-5.)

No qlib import, no pandas-side-effects. Importing this module is
cheap.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_synthetic_panel(
    n_tickers: int, n_dates: int, seed: int,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Deterministic synthetic OHLCV panel + noisy forward return.

    Used by the factor-mining CLI when no real PIT bundle is
    available — gives the GP something concrete (and
    determinism-bound) to mine without requiring a live qlib
    install.

    Returns ``(panel_dict, forward_return_df)``:

    * ``panel_dict``: ``{"$open", "$high", "$low", "$close",
      "$volume", "$money"}`` → ``pd.DataFrame`` keyed by
      (datetime, instrument).
    * ``forward_return_df``: the **realisable** one-day return —
      see the comment at the ``raw_return`` line below for why
      it's ``shift(-2)/shift(-1) - 1`` and not the naïve
      ``shift(-1)/x - 1``.
    """
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:04d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    # Random-walk close prices (positive, drift slightly upward).
    log_returns = rng.normal(0.0005, 0.02, size=(n_dates, n_tickers))
    close = np.exp(np.cumsum(log_returns, axis=0)) * 100.0
    high = close * (1 + np.abs(rng.normal(0, 0.005, size=close.shape)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, size=close.shape)))
    open_ = close * np.exp(rng.normal(0, 0.003, size=close.shape))
    volume = np.exp(rng.normal(12, 1.0, size=close.shape))
    money = volume * close

    def _df(arr):
        return pd.DataFrame(
            arr,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        )

    panel = {
        "$open": _df(open_),
        "$high": _df(high),
        "$low": _df(low),
        "$close": _df(close),
        "$volume": _df(volume),
        "$money": _df(money),
    }
    # Forward return = the one-day open-to-open return REALISED at
    # T+1→T+2, mirroring qlib's Alpha158 default label
    # ``Ref($close, -2)/Ref($close, -1) - 1`` (LABEL_LOOKAHEAD_DAYS=2):
    # at time T you decide based on data ≤ T, trade at T+1 open, and
    # earn the return from T+1 open to T+2 open. ``shift(-2)/shift(-1)``
    # is that — NOT a bug despite occasional audit-tool flags that
    # claim it should be ``shift(-1)/x``. The latter (T→T+1 return)
    # would be a 1-day lookahead because T's signal can't be acted on
    # before T+1's open. Plus a noisy volume-momentum signal so the GP
    # has something real to mine on top of the random walk.
    open_df = panel["$open"]
    raw_return = open_df.shift(-2) / open_df.shift(-1) - 1
    vol_signal = np.log(panel["$volume"]).rank(axis=1, pct=True) - 0.5
    fwd = (raw_return + 0.05 * vol_signal.shift(-1)).fillna(0.0)
    fwd.index.name = "datetime"
    fwd.columns.name = "instrument"
    return panel, fwd


__all__ = ["build_synthetic_panel"]

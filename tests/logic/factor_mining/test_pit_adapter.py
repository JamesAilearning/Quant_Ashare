"""Tests for FactorMiningDataView — the sole PIT data door.

Uses a lightweight ``_StubPITProvider`` stub instead of the real
``PITDataProvider`` to avoid requiring the PIT bundle on disk (per
``inventory.md`` §F.3 — the operator must build the bundle before
real-data testing can run).
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from src.factor_mining.pit_adapter import FactorMiningDataView


def _make_panel_frame(tickers, dates, fields, seed=0):
    """Build a synthetic (instrument, datetime) MultiIndex DataFrame
    that mimics ``PITDataProvider.get_features`` output."""
    rng = np.random.default_rng(seed)
    rows = []
    idx = []
    for t in tickers:
        for d in dates:
            row = {f: float(rng.normal(100, 5)) for f in fields}
            rows.append(row)
            idx.append((t, d))
    df = pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(idx, names=["instrument", "datetime"]),
    )
    return df


class _StubPITProvider:
    """Duck-typed stand-in for PITDataProvider.

    Mirrors the public ``get_features`` and ``get_universe_range``
    signatures; does NOT instantiate qlib. The signature is asserted
    against the real ``PITDataProvider`` in
    ``test_stub_matches_real_PITDataProvider_signature`` so drift is
    caught.
    """

    def __init__(self, tickers, dates):
        self._tickers = list(tickers)
        self._dates = list(dates)
        self.last_get_features_args: dict | None = None

    def get_features(
        self,
        fields,
        start,
        end,
        universe_name="all",
        align="universe",
        instruments=None,
    ):
        self.last_get_features_args = {
            "fields": list(fields),
            "start": start,
            "end": end,
            "universe_name": universe_name,
            "align": align,
            "instruments": list(instruments) if instruments is not None else None,
        }
        # Compute synthetic values for each requested field.
        rng = np.random.default_rng(hash(tuple(fields)) % (2**32))
        rows = []
        idx = []
        for t in self._tickers:
            for d in self._dates:
                row = {f: float(rng.normal(100, 5)) for f in fields}
                rows.append(row)
                idx.append((t, d))
        return pd.DataFrame(
            rows,
            index=pd.MultiIndex.from_tuples(idx, names=["instrument", "datetime"]),
        )

    def get_universe_range(self, start, end, universe_name="all"):
        # All tickers tradable on every day in this stub.
        return {d: list(self._tickers) for d in self._dates}


# ---------------------------------------------------------------------------
# Adapter behaviour
# ---------------------------------------------------------------------------


def test_load_panel_returns_dict_per_field():
    tickers = ["SH600001", "SH600002", "SH600003"]
    dates = pd.date_range("2024-01-02", periods=5, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-08")
    panel = view.load_panel()
    assert set(panel.keys()) == {
        "$open", "$high", "$low", "$close", "$volume", "$money",
    }
    for _field, df in panel.items():
        assert isinstance(df, pd.DataFrame)
        assert df.shape == (5, 3)


def test_load_panel_pivots_to_date_x_ticker():
    tickers = ["SH600001", "SH600002"]
    dates = pd.date_range("2024-01-02", periods=4, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-05")
    panel = view.load_panel()
    close = panel["$close"]
    assert close.index.name == "datetime"
    assert close.columns.name == "instrument"
    assert list(close.columns) == sorted(tickers)
    assert list(close.index) == sorted(dates)


def test_load_panel_passes_correct_args_to_provider():
    tickers = ["SH600001"]
    dates = pd.date_range("2024-01-02", periods=3, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(
        stub, "2024-01-02", "2024-01-04", universe_name="csi300",
    )
    view.load_panel()
    args = stub.last_get_features_args
    assert args is not None
    assert args["fields"] == ["$open", "$high", "$low", "$close", "$volume", "$money"]
    assert args["start"] == "2024-01-02"
    assert args["end"] == "2024-01-04"
    assert args["universe_name"] == "csi300"
    assert args["instruments"] is None


def test_load_panel_routes_explicit_instruments_through_provider():
    tickers = ["SH600001", "SH600002"]
    dates = pd.date_range("2024-01-02", periods=3, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(
        stub,
        "2024-01-02", "2024-01-04",
        instruments=["SH600001", "SH600002"],
    )
    view.load_panel()
    args = stub.last_get_features_args
    assert args is not None
    assert args["instruments"] == ["SH600001", "SH600002"]


def test_forward_return_uses_open_open_formula_horizon_1():
    tickers = ["SH600001", "SH600002"]
    dates = pd.date_range("2024-01-02", periods=3, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-04")
    _ = view.forward_return(horizon=1)
    args = stub.last_get_features_args
    assert args["fields"] == ["Ref($open, -2) / Ref($open, -1) - 1"]


def test_forward_return_uses_open_open_formula_horizon_5():
    tickers = ["SH600001"]
    dates = pd.date_range("2024-01-02", periods=3, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-04")
    _ = view.forward_return(horizon=5)
    args = stub.last_get_features_args
    assert args["fields"] == ["Ref($open, -6) / Ref($open, -1) - 1"]


def test_forward_return_returns_date_x_ticker_dataframe():
    tickers = ["SH600001", "SH600002"]
    dates = pd.date_range("2024-01-02", periods=4, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-05")
    fwd = view.forward_return(horizon=1)
    assert isinstance(fwd, pd.DataFrame)
    assert fwd.shape == (4, 2)
    assert fwd.index.name == "datetime"
    assert fwd.columns.name == "instrument"


def test_forward_return_rejects_zero_horizon():
    stub = _StubPITProvider(["SH600001"], pd.date_range("2024-01-02", periods=3))
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-04")
    with pytest.raises(ValueError, match="horizon"):
        view.forward_return(horizon=0)


def test_universe_mask_returns_boolean_date_x_ticker():
    tickers = ["SH600001", "SH600002"]
    dates = pd.date_range("2024-01-02", periods=3, freq="D")
    stub = _StubPITProvider(tickers, dates)
    view = FactorMiningDataView(stub, "2024-01-02", "2024-01-04")
    mask = view.universe_mask()
    assert mask.shape == (3, 2)
    assert all(mask.dtypes.apply(lambda dt: dt == np.bool_ or dt is np.dtype(bool)))
    assert mask.values.all()


def test_load_panel_raises_on_unexpected_index_shape():
    """A future PIT change that drifts the MultiIndex shape must surface
    loudly, not silently."""

    class _BadStub:
        def get_features(self, **kwargs):
            return pd.DataFrame({"$close": [1.0, 2.0]}, index=[0, 1])

        def get_universe_range(self, start, end, universe_name="all"):
            return {}

    view = FactorMiningDataView(_BadStub(), "2024-01-02", "2024-01-04")
    with pytest.raises(ValueError, match="unexpected index"):
        view.load_panel()


# ---------------------------------------------------------------------------
# D5 strict gate + signature drift guard
# ---------------------------------------------------------------------------


def test_pit_adapter_does_not_import_qlib_directly():
    import src.factor_mining.pit_adapter as mod

    src = inspect.getsource(mod)
    assert "from qlib" not in src
    assert "qlib.data" not in src
    assert "qlib.init" not in src


def test_stub_matches_real_PITDataProvider_signature():
    """If the real ``PITDataProvider`` API drifts, this test fails so
    the stub (and the adapter) can be updated in the same PR."""
    from src.pit.query import PITDataProvider

    real_init = inspect.signature(PITDataProvider.__init__)
    real_get_features = inspect.signature(PITDataProvider.get_features)
    real_get_universe_range = inspect.signature(PITDataProvider.get_universe_range)

    # init signature checks
    init_params = list(real_init.parameters.keys())
    assert init_params[:3] == ["self", "provider_uri", "delisted_registry_path"]

    # get_features parameter names must match what the stub exposes
    expected_get_features = {
        "self", "fields", "start", "end", "universe_name", "align", "instruments",
    }
    assert set(real_get_features.parameters.keys()) == expected_get_features

    # get_universe_range parameter names
    expected_gur = {"self", "start", "end", "universe_name"}
    assert set(real_get_universe_range.parameters.keys()) == expected_gur

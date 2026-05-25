"""Miner orchestrator + CLI entry.

Reads a YAML config, builds the OHLCV panel (synthetic or real-PIT),
runs the GP engine, and saves the factor pool + GP history under the
configured output directory.

Run via:

    python -m src.factor_mining.miner config/factor_mining/smoke.yaml

No qlib direct import. The real-PIT branch routes everything through
``FactorMiningDataView`` (Phase 2's pit_adapter), preserving the D5
strict gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .factor_pool import FactorPool
from .fitness import FitnessConfig
from .gp_engine import GenerationStats, GPConfig, GPEngine

# ---------------------------------------------------------------------------
# Config types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataConfig:
    mode: str = "synthetic"
    # Synthetic-mode knobs
    synthetic_n_tickers: int = 30
    synthetic_n_dates: int = 100
    synthetic_seed: int = 1234
    # Real-PIT-mode knobs
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    universe_name: str = "csi300"
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"
    forward_horizon: int = 1


@dataclass(frozen=True)
class MinerConfig:
    data: DataConfig
    gp: GPConfig
    fitness: FitnessConfig
    output_dir: Path
    run_id: str | None = None
    pool_top_k: int | None = None
    """If set, ``run_mining`` saves only the top-K pool entries
    (by ``fitness`` desc, hash-tie-broken). ``None`` (default) saves
    the entire post-GP pool.

    Rationale: a large GP run on real PIT data routinely produces
    O(10³) factors that pass validity. Feeding O(10³) features into
    qlib's ``StaticDataLoader`` / ``DataHandlerLP`` triggers two
    failure modes on Windows: (1) the LightGBM trainer overfits at
    the high feature-to-sample ratio; (2) qlib's multiprocessed
    backtest fork hits ``[Errno 22]`` when re-importing scipy in
    the worker. Truncating to the top-K (typical: 30-100) keeps the
    downstream model training stable AND the backtest single-process.
    """


@dataclass(frozen=True)
class RunResult:
    run_id: str
    output_dir: Path
    pool: FactorPool
    history: list[GenerationStats] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> MinerConfig:
    """Parse a YAML config into a typed ``MinerConfig``."""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    data = DataConfig(**(raw.get("data") or {}))
    gp = GPConfig(**(raw.get("gp") or {}))
    fitness = FitnessConfig(**(raw.get("fitness") or {}))
    out_dir = Path(raw.get("output_dir", "research/mined_factors"))
    run_id = raw.get("run_id")
    pool_top_k_raw = raw.get("pool_top_k")
    pool_top_k: int | None
    if pool_top_k_raw is None:
        pool_top_k = None
    else:
        # Reject types that ``int(...)`` would silently coerce — ``bool``
        # (``True`` → 1), ``float`` (``1.9`` → 1), ``str`` (``"5"`` → 5),
        # etc. ``pool_top_k`` is a hard cap on the persisted factor pool;
        # a typo'd type can quietly shrink experimental results without
        # the operator noticing. (Codex P2 on PR #150.) ``bool`` is
        # explicitly rejected because it is an ``int`` subclass.
        if isinstance(pool_top_k_raw, bool) or not isinstance(pool_top_k_raw, int):
            raise ValueError(
                "pool_top_k must be a positive integer or null, got "
                f"{type(pool_top_k_raw).__name__} ({pool_top_k_raw!r})"
            )
        pool_top_k = pool_top_k_raw
        if pool_top_k <= 0:
            raise ValueError(
                f"pool_top_k must be a positive integer or null, got {pool_top_k_raw!r}"
            )
    return MinerConfig(
        data=data, gp=gp, fitness=fitness, output_dir=out_dir,
        run_id=run_id, pool_top_k=pool_top_k,
    )


# ---------------------------------------------------------------------------
# Panel building
# ---------------------------------------------------------------------------


def _build_synthetic_panel(
    n_tickers: int, n_dates: int, seed: int,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Deterministic synthetic OHLCV panel + noisy forward return."""
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:04d}" for i in range(n_tickers)]
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    # Random-walk close prices (positive, drift slightly upward).
    log_returns = rng.normal(0.0005, 0.02, size=(n_dates, n_tickers))
    close = np.exp(np.cumsum(log_returns, axis=0)) * 100.0
    # Intraday range proxied as 1 % around close.
    high = close * (1 + np.abs(rng.normal(0, 0.005, size=close.shape)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, size=close.shape)))
    open_ = close * np.exp(rng.normal(0, 0.003, size=close.shape))
    volume = np.exp(rng.normal(12, 1.0, size=close.shape))
    money = volume * close

    def _df(arr):
        df = pd.DataFrame(
            arr,
            index=pd.Index(dates, name="datetime"),
            columns=pd.Index(tickers, name="instrument"),
        )
        return df

    panel = {
        "$open": _df(open_),
        "$high": _df(high),
        "$low": _df(low),
        "$close": _df(close),
        "$volume": _df(volume),
        "$money": _df(money),
    }
    # Forward return = open-to-open one-day return, plus a noisy signal
    # tied to volume (so the GP can find a small but real factor).
    open_df = panel["$open"]
    raw_return = open_df.shift(-2) / open_df.shift(-1) - 1
    # Mild "volume momentum" signal so the GP has something to mine.
    vol_signal = np.log(panel["$volume"]).rank(axis=1, pct=True) - 0.5
    fwd = (raw_return + 0.05 * vol_signal.shift(-1)).fillna(0.0)
    fwd.index.name = "datetime"
    fwd.columns.name = "instrument"
    return panel, fwd


def _build_pit_panel(config: DataConfig):
    if not config.pit_provider_uri or not config.delisted_registry_path:
        raise ValueError(
            "data.mode == 'pit' requires both pit_provider_uri and "
            "delisted_registry_path; see docs/factor_mining/inventory.md §F.3 "
            "for the PIT-bundle build instructions."
        )
    # Local imports — only used in PIT mode so synthetic-mode users don't
    # need a built PIT bundle on disk to invoke the CLI.
    from src.pit.query import PITDataProvider  # noqa: PLC0415

    from .pit_adapter import FactorMiningDataView  # noqa: PLC0415

    provider = PITDataProvider(
        provider_uri=config.pit_provider_uri,
        delisted_registry_path=config.delisted_registry_path,
    )
    view = FactorMiningDataView(
        provider,
        start=config.start_date,
        end=config.end_date,
        universe_name=config.universe_name,
    )
    panel = view.load_panel()
    fwd = view.forward_return(horizon=config.forward_horizon)
    return panel, fwd


def build_panel(config: MinerConfig):
    if config.data.mode == "synthetic":
        return _build_synthetic_panel(
            n_tickers=config.data.synthetic_n_tickers,
            n_dates=config.data.synthetic_n_dates,
            seed=config.data.synthetic_seed,
        )
    if config.data.mode == "pit":
        return _build_pit_panel(config.data)
    raise ValueError(
        f"Unknown data.mode {config.data.mode!r}; expected 'synthetic' or 'pit'"
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _autogenerate_run_id(seed: int) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{seed}"


def _truncate_pool_to_top_k(pool: FactorPool, k: int) -> FactorPool:
    """Build a new pool containing only the top-K entries by fitness.

    Deterministic: ``FactorPool.top_k`` sorts by fitness desc (the
    underlying ``sort`` is stable, and ``add()`` preserves insertion
    order in the dict), so two calls with identical pools and ``k``
    produce byte-identical saved artefacts.
    """
    truncated = FactorPool()
    for entry in pool.top_k(k, by="fitness"):
        truncated.add(entry)
    return truncated


def run_mining(config: MinerConfig) -> RunResult:
    """Execute the full miner pipeline: build panel → run GP → save pool.

    When ``config.pool_top_k`` is set, the saved pool is truncated to
    the top-K entries by fitness BEFORE persistence. The returned
    ``RunResult.pool`` reflects the saved (truncated) pool so callers
    inspecting ``result.pool`` see the same entries that downstream
    consumers (handler, walk-forward) will load.
    """
    panel, fwd = build_panel(config)
    engine = GPEngine(config.gp, config.fitness)
    pool = engine.run(panel, fwd)

    full_pool_size = len(pool)
    if config.pool_top_k is not None and full_pool_size > config.pool_top_k:
        pool = _truncate_pool_to_top_k(pool, config.pool_top_k)

    run_id = config.run_id or _autogenerate_run_id(config.gp.seed)
    run_dir = Path(config.output_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pool.save(run_dir)

    # GP history
    history_path = run_dir / "gp_history.json"
    history_path.write_text(
        json.dumps([asdict(s) for s in engine.history], indent=2),
        encoding="utf-8",
    )
    # Reproducibility: dump the resolved config
    config_path = run_dir / "config.yaml"
    config_dump = {
        "run_id": run_id,
        "output_dir": str(config.output_dir),
        "pool_top_k": config.pool_top_k,
        "full_pool_size_pre_truncation": full_pool_size,
        "saved_pool_size": len(pool),
        "data": asdict(config.data),
        "gp": asdict(config.gp),
        "fitness": asdict(config.fitness),
    }
    config_path.write_text(
        yaml.safe_dump(config_dump, sort_keys=False),
        encoding="utf-8",
    )
    return RunResult(
        run_id=run_id, output_dir=run_dir, pool=pool, history=list(engine.history),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Factor Mining GP search")
    parser.add_argument(
        "config",
        type=Path,
        help="path to a miner YAML config (e.g. config/factor_mining/smoke.yaml)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    config = load_config(args.config)
    result = run_mining(config)
    if config.pool_top_k is not None:
        print(
            f"Run complete: {result.run_id} | pool size: {len(result.pool)} "
            f"(top-{config.pool_top_k} by fitness)"
        )
    else:
        print(f"Run complete: {result.run_id} | pool size: {len(result.pool)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

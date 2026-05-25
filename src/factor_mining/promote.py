"""Promotion CLI — validates a Phase 3 run and copies survivors to production.

Per ``decisions.md`` D4 ("Manual gated"), this CLI runs ONLY when
the operator invokes it. The CLI never auto-promotes; ``--dry-run``
prints the report without writing.

Run via::

    python -m src.factor_mining.promote --run <run_dir> --to <version> \\
        [--config <yaml>] [--dry-run]

No qlib import, no ``src.pit`` import. PIT-mode data flows through
``FactorMiningDataView`` like Phase 3's miner.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .factor_pool import FactorPool
from .validator import (
    FactorValidationResult,
    ValidationCriteria,
    filter_correlated,
    validate_pool,
)

# ---------------------------------------------------------------------------
# Config types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionDataConfig:
    """Mirror of ``miner.DataConfig`` so promote can use the same panels."""

    mode: str = "synthetic"
    synthetic_n_tickers: int = 30
    synthetic_n_dates: int = 200
    synthetic_seed: int = 7
    pit_provider_uri: str = ""
    delisted_registry_path: str = ""
    universe_name: str = "csi300"
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"
    forward_horizon: int = 1


@dataclass(frozen=True)
class PromotionConfig:
    run_dir: Path
    production_dir: Path
    version: str
    criteria: ValidationCriteria
    data: PromotionDataConfig


@dataclass(frozen=True)
class PromotionReport:
    run_dir: Path
    output_dir: Path | None
    version: str
    n_pool: int
    n_passed_individual: int
    n_kept_after_correlation: int
    results: tuple[FactorValidationResult, ...] = field(default_factory=tuple)


class PromotionError(RuntimeError):
    """Raised on bad config, missing run dir, or overwrite refusal."""


# ---------------------------------------------------------------------------
# Panel building (mirrors miner.build_panel for synthetic / PIT modes)
# ---------------------------------------------------------------------------


# Consolidated into ``src.factor_mining._synthetic_panel`` (bug.md
# P2-5). Identical implementation previously lived in this file and
# ``miner.py`` (including the qlib LABEL_LOOKAHEAD_DAYS=2 comment
# added in #165's P1-6 clarification, which now lives at the
# canonical implementation site). Both now share one source so any
# change to the panel shape happens in one place.
from src.factor_mining._synthetic_panel import (  # noqa: E402
    build_synthetic_panel as _build_synthetic_panel,
)


def _build_pit_panel(config: PromotionDataConfig):
    if not config.pit_provider_uri or not config.delisted_registry_path:
        raise PromotionError(
            "data.mode == 'pit' requires both pit_provider_uri and "
            "delisted_registry_path; see docs/factor_mining/inventory.md §F.3 "
            "for PIT-bundle build instructions."
        )
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
    return view.load_panel(), view.forward_return(horizon=config.forward_horizon)


def _build_panel(data: PromotionDataConfig):
    if data.mode == "synthetic":
        return _build_synthetic_panel(
            data.synthetic_n_tickers, data.synthetic_n_dates, data.synthetic_seed,
        )
    if data.mode == "pit":
        return _build_pit_panel(data)
    raise PromotionError(
        f"Unknown data.mode {data.mode!r}; expected 'synthetic' or 'pit'"
    )


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


def promote_run(
    config: PromotionConfig, *, dry_run: bool = False,
) -> PromotionReport:
    """Validate the run and (unless ``dry_run``) write the production dir."""
    if not config.run_dir.exists():
        raise PromotionError(f"run_dir does not exist: {config.run_dir!r}")
    target_dir = config.production_dir / config.version
    if target_dir.exists() and not dry_run:
        raise PromotionError(
            f"production version directory already exists: {target_dir!r}. "
            "Choose a new version label or remove the existing one manually."
        )

    pool = FactorPool.load(config.run_dir)
    panel, fwd = _build_panel(config.data)

    results = validate_pool(pool, panel, fwd, config.criteria)
    n_passed_individual = sum(1 for r in results if r.passes)

    filtered = filter_correlated(results, panel, config.criteria, pool)
    survivors = [r for r in filtered if r.passes]
    n_kept = len(survivors)

    output_dir: Path | None = None
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        survivor_pool = FactorPool()
        entries_by_hash = {e.expr_hash: e for e in pool.all_entries()}
        for res in survivors:
            entry = entries_by_hash.get(res.expr_hash)
            if entry is not None:
                survivor_pool.add(entry)
        survivor_pool.save(target_dir)
        # Promotion report
        report_payload = {
            "run_dir": str(config.run_dir),
            "production_dir": str(config.production_dir),
            "version": config.version,
            "n_pool": len(pool),
            "n_passed_individual": n_passed_individual,
            "n_kept_after_correlation": n_kept,
            "criteria": asdict(config.criteria),
            "results": [
                {
                    "expr_hash_hex": format(r.expr_hash & 0xFFFFFFFFFFFFFFFF, "016x"),
                    "expr_str": r.expr_str,
                    "fitness": r.fitness,
                    "passes": r.passes,
                    "reasons": list(r.reasons),
                    "is_ir": _json_safe(r.is_ir),
                    "is_rank_ic_mean": _json_safe(r.is_rank_ic_mean),
                    "is_n_obs": r.is_n_obs,
                    "oos_ir": _json_safe(r.oos_ir),
                    "oos_rank_ic_mean": _json_safe(r.oos_rank_ic_mean),
                    "oos_n_obs": r.oos_n_obs,
                }
                for r in filtered
            ],
        }
        (target_dir / "promotion_report.json").write_text(
            json.dumps(report_payload, indent=2),
            encoding="utf-8",
        )
        output_dir = target_dir

    return PromotionReport(
        run_dir=config.run_dir,
        output_dir=output_dir,
        version=config.version,
        n_pool=len(pool),
        n_passed_individual=n_passed_individual,
        n_kept_after_correlation=n_kept,
        results=tuple(filtered),
    )


def _json_safe(x: float) -> float | None:
    if not np.isfinite(x):
        return None
    return float(x)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_config(
    config_path: Path | None,
    run_dir: Path,
    production_dir: Path,
    version: str,
) -> PromotionConfig:
    if config_path is not None and config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    data = PromotionDataConfig(**(raw.get("data") or {}))
    crit_kwargs = dict(raw.get("criteria") or {})
    if "is_oos_split_date" not in crit_kwargs:
        # Compute a sensible default from synthetic panel dates.
        # 80 / 20 split: dates 0..n*0.8-1 are IS, the rest OOS.
        n = data.synthetic_n_dates
        split_idx = int(0.8 * n)
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        crit_kwargs["is_oos_split_date"] = dates[split_idx].strftime("%Y-%m-%d")
    criteria = ValidationCriteria(**crit_kwargs)
    return PromotionConfig(
        run_dir=run_dir,
        production_dir=production_dir,
        version=version,
        criteria=criteria,
        data=data,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Promote a factor-mining run to production")
    parser.add_argument(
        "--run", type=Path, required=True,
        help="path to a Phase 3 miner run directory",
    )
    parser.add_argument(
        "--to", dest="version", required=True,
        help="production version label (becomes production/<version>/)",
    )
    parser.add_argument(
        "--production-dir", type=Path,
        default=Path("research/mined_factors/production"),
        help="root directory under which versioned production lives",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="optional YAML config for criteria + data spec",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the report without writing to disk",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        config = _load_config(args.config, args.run, args.production_dir, args.version)
        report = promote_run(config, dry_run=args.dry_run)
    except PromotionError as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    # Use ASCII arrow ("->") in stdout so Windows cp1252 consoles do not
    # raise UnicodeEncodeError when this CLI prints (the Phase 6 PR's
    # initial round caught this on the windows-latest CI matrix).
    if args.dry_run:
        print(
            f"Promotion (dry-run): {report.n_kept_after_correlation}/{report.n_pool} "
            f"factors would be kept -> production/{report.version}/"
        )
    else:
        print(
            f"Promotion complete: {report.n_kept_after_correlation}/{report.n_pool} "
            f"factors kept -> {report.output_dir}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

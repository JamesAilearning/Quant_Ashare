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
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .factor_pool import FactorPool
from .miner import DataConfig, build_panel_for_data
from .validator import (
    FactorValidationResult,
    ValidationCriteria,
    filter_correlated,
    validate_pool,
)

# ---------------------------------------------------------------------------
# Config types
# ---------------------------------------------------------------------------
#
# There is deliberately NO promotion-side data config. The data definition
# (mode, fields, forward-return price, window, universe) is READ from the
# candidate run's resolved ``config.yaml`` and re-used verbatim, so the OOS
# validation runs on exactly the panel the factors were mined on. The
# previous ``PromotionDataConfig`` "mirror" had already drifted (it lacked
# ``fields`` and ``forward_return_price``): a campaign config raised
# ``TypeError`` when handed to promote, and a hand-written one would have
# re-validated on the full V1 terminal set and the open→open label — a
# different experiment wearing the run's name (external finding, 2026-08-10).


@dataclass(frozen=True)
class PromotionConfig:
    run_dir: Path
    production_dir: Path
    version: str
    criteria: ValidationCriteria
    data: DataConfig
    # sha256 of the canonical JSON of ``data`` — recorded in the promotion
    # report so downstream consumers (qlib handler bridge, audits) can
    # verify which data definition the survivors were validated under.
    data_definition_sha256: str


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
    try:
        panel, fwd = build_panel_for_data(config.data)
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc

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
            # The data definition the survivors were validated under — read
            # from the run's own resolved config.yaml, never operator-supplied
            # — plus its canonical hash for downstream verification.
            "data": asdict(config.data),
            "data_definition_sha256": config.data_definition_sha256,
            "data_source": "run_dir/config.yaml",
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


def _load_run_data_config(run_dir: Path) -> tuple[DataConfig, str]:
    """The data definition the run was MINED with, plus its canonical hash.

    Read from the resolved ``config.yaml`` the miner dumps into every run
    directory — the single source of truth for mode / fields /
    forward-return price / window / universe. A run without it (or without
    a ``data:`` section) cannot prove what it was mined on and is refused.
    """
    config_path = run_dir / "config.yaml"
    if not config_path.exists():
        raise PromotionError(
            f"run_dir has no resolved config.yaml: {config_path!r}. The "
            "miner writes one into every run directory; a run that cannot "
            "prove its data definition cannot be promoted."
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data_section = raw.get("data")
    if not isinstance(data_section, dict) or not data_section:
        raise PromotionError(
            f"run_dir config.yaml has no data section: {config_path!r} — "
            "cannot bind the promotion to the mined data definition."
        )
    try:
        data = DataConfig(**data_section)
    except TypeError as exc:
        raise PromotionError(
            f"run_dir config.yaml data section does not parse as the "
            f"miner's DataConfig ({exc}) — the run predates or diverges "
            "from the current data schema; re-mine before promoting."
        ) from exc
    digest = hashlib.sha256(
        json.dumps(asdict(data), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return data, digest


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
    if "data" in raw:
        raise PromotionError(
            "the promotion config must not carry a data: section — the data "
            "definition is bound to the mined run (read from "
            "run_dir/config.yaml) so the OOS validation runs on exactly the "
            "panel the factors were mined on. Remove the data: section; the "
            "operator config supplies criteria only."
        )
    data, data_sha = _load_run_data_config(run_dir)
    crit_kwargs = dict(raw.get("criteria") or {})
    if "is_oos_split_date" not in crit_kwargs:
        if data.mode == "pit":
            # A real-data split date is an EXPERIMENT DESIGN choice; deriving
            # one from synthetic defaults would silently adjudicate OOS on an
            # arbitrary boundary (external finding, 2026-08-10).
            raise PromotionError(
                "criteria.is_oos_split_date is required for a PIT-mode run — "
                "promotion refuses to infer an OOS boundary. Set it "
                "explicitly in the promotion config (criteria section)."
            )
        # Synthetic smoke path: 80/20 split over the synthetic date range.
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
        data_definition_sha256=data_sha,
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

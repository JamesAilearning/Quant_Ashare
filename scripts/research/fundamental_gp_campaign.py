"""Fundamental GP campaign orchestration — the injection layer.

The miner and the promotion CLI live in ``src/factor_mining/`` and must
not import ``src.research.*`` (research-isolation gate); the fundamental
panel bridge lives in ``src/research/`` and is the ONLY sanctioned
producer of report-period-provenanced panels. This script is the one
layer that sees both sides, so it owns the seam: it builds the canonical
panel factory and injects it into ``run_mining`` / ``promote_run``.

Subcommands::

    python -m scripts.research.fundamental_gp_campaign mine \
        --config config/factor_mining/<campaign>.yaml

    python -m scripts.research.fundamental_gp_campaign record-baseline \
        --run <run_dir> --end-date <validation_end> --out <baseline.json>

    python -m scripts.research.fundamental_gp_campaign promote \
        --run <run_dir> --to <version> [--config <yaml>] \
        [--baseline <baseline.json>] [--dry-run]

``record-baseline`` is the "independent trusted process" of the
extension-baseline contract: it rebuilds the EFFECTIVE (extended) window
panel through the canonical bridge, digests the full output (values,
evidence, periods) with the miner's own trusted digest, and writes the
expected digest to disk BEFORE any promotion runs. Promotion then
requires the injected factory to reproduce that digest bit-for-bit on
the extension window — a baseline the promoted callable cannot issue to
itself.

The starter-three-factor link-through (GP/A, asset growth, the pure-BS
accrual) runs through ``mine`` with a campaign config whose
``fundamental_fields`` cover those factors' inputs; igniting it on real
data is an operator action, not a CI one.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from src.data.trading_calendar import StaticTradingCalendar
from src.factor_mining.miner import (
    DataConfig,
    build_panel_for_data,
    data_definition_sha256,
    load_config,
    run_mining,
)
from src.factor_mining.panel_digest import fundamental_output_sha256
from src.factor_mining.promote import (
    PromotionError,
    _load_config,
    _load_run_data_config,
    promote_run,
)
from src.research.financial_pit_view import FinancialPITDataView
from src.research.fundamental_panel import build_fundamental_panel

_log = logging.getLogger(__name__)


def _load_calendar(path: str) -> StaticTradingCalendar:
    """Trading calendar from a one-ISO-date-per-line file (qlib day.txt)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    dates = [date.fromisoformat(ln.strip()[:10]) for ln in lines if ln.strip()]
    if not dates:
        raise ValueError(f"calendar file {path} contains no dates.")
    return StaticTradingCalendar(dates)


PanelTriple = tuple[
    dict[str, pd.DataFrame], dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
]
PanelFactory = Callable[
    [DataConfig, pd.DatetimeIndex, Sequence[str]], PanelTriple]


def build_panel_factory() -> PanelFactory:
    """The canonical fundamental panel factory for the injection seam.

    Consumes ONLY the run-persisted ``DataConfig`` plus the geometry the
    seam owner hands it — no ambient state — so promotion can re-invoke
    it on the recorded inputs and compare behavior. Returns the
    documented flat triple ``(values, evidence, periods)`` including the
    ``__prior`` generation.
    """

    def factory(
        data: DataConfig, trade_dates: pd.DatetimeIndex,
        instruments: Sequence[str],
    ) -> PanelTriple:
        view = FinancialPITDataView(
            Path(data.fundamental_store_root),
            _load_calendar(data.fundamental_calendar_path),
            financial_issuers=data.financial_exclusions,
        )
        # The exclusion cross-check REPORTS disagreements, never resolves
        # them (spec: v2-financial-pit-contract): a financial-listed
        # issuer that does report oper_cost, or a non-excluded issuer
        # that never does, each gets a visible line. Runs on every
        # factory invocation (mining and promotion alike) so the signed
        # list is re-examined against the store the run actually reads.
        disagreements = view.cross_check_exclusion(list(instruments))
        if disagreements:
            _log.warning(
                "financial-exclusion cross-check: %d disagreement(s) "
                "between the signed industry list and oper_cost "
                "reporting behavior:", len(disagreements))
            for d in disagreements:
                _log.warning("  %s: %s", d.ts_code, d.kind)
        panel = build_fundamental_panel(
            view,
            list(data.fundamental_fields),
            list(trade_dates),
            list(instruments),
            include_prior_period=True,
        )
        values, evidence, periods = panel.flatten()
        # The bridge's columns are the union of SERVED names — the
        # financial issuers the view excludes have no column at all. The
        # seam contract demands the exact requested geometry, so the
        # excluded names come back as all-NA columns here; their cells
        # are simultaneously removed from the coverage DENOMINATOR by
        # the same persisted exclusion set (miner's universe mask), so
        # an all-NA column never counts against any candidate.
        dates = pd.DatetimeIndex(trade_dates)
        cols = list(instruments)

        def _on_geometry(mapping: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
            return {k: f.reindex(index=dates, columns=cols)
                    for k, f in mapping.items()}

        return _on_geometry(values), _on_geometry(evidence), _on_geometry(
            periods)

    return factory


def _cmd_mine(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    result = run_mining(
        config, fundamental_panel_factory=build_panel_factory())
    print(f"Run complete: {result.run_id} | pool size: {len(result.pool)}")
    return 0


def _cmd_record_baseline(args: argparse.Namespace) -> int:
    run_data, _run_sha = _load_run_data_config(Path(args.run))
    if not run_data.fundamental_store_root:
        print(
            "record-baseline: the run records no fundamental leg — "
            "nothing to baseline.", file=sys.stderr)
        return 1
    effective = replace(run_data, end_date=str(args.end_date))
    _panel, fwd = build_panel_for_data(effective)
    factory = build_panel_factory()
    values, evidence, periods = factory(
        effective, fwd.index, list(fwd.columns))
    payload = {
        "purpose": "fundamental-extension-baseline",
        "run_dir": str(args.run),
        "validation_end_date": str(args.end_date),
        "data_definition_sha256": data_definition_sha256(effective),
        "trade_dates": [str(d.date()) for d in fwd.index],
        "instruments": [str(c) for c in fwd.columns],
        "output_sha256": fundamental_output_sha256(
            values, evidence, periods),
    }
    out = Path(args.out)
    try:
        # Exclusive create ("x"), not exists()+write: a baseline is a
        # pre-authorization — silently replacing one that a pending
        # promotion may already reference would let a second recording
        # rewrite what was authorized, and a racing pair of recorders
        # could both pass a separate pre-check. The filesystem enforces
        # the no-overwrite contract atomically (same fix class as the
        # exclusion exporter, codex #441 r3).
        with out.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
    except FileExistsError:
        print(
            f"record-baseline: {out} already exists — refusing to "
            "overwrite a recorded authorization; pick a fresh path.",
            file=sys.stderr)
        return 1
    print(f"Baseline recorded: {out}")
    print(f"  output_sha256: {payload['output_sha256']}")
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    try:
        config = _load_config(
            args.promotion_config, args.run, args.production_dir,
            args.version,
            fundamental_baseline_path=args.baseline,
        )
        report = promote_run(
            config, dry_run=args.dry_run,
            fundamental_panel_factory=build_panel_factory(),
        )
    except (PromotionError, FileNotFoundError) as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"Promotion (dry-run): "
            f"{report.n_kept_after_correlation}/{report.n_pool} factors "
            f"would be kept -> production/{report.version}/"
        )
    else:
        print(
            f"Promotion complete: "
            f"{report.n_kept_after_correlation}/{report.n_pool} factors "
            f"kept -> {report.output_dir}"
        )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fundamental GP campaign orchestration")
    sub = parser.add_subparsers(dest="command", required=True)

    mine = sub.add_parser("mine", help="mine with the fundamental leg")
    mine.add_argument("--config", type=Path, required=True)
    mine.set_defaults(func=_cmd_mine)

    rb = sub.add_parser(
        "record-baseline",
        help="record the pre-authorized extension baseline")
    rb.add_argument("--run", type=Path, required=True)
    rb.add_argument("--end-date", required=True,
                    help="validation end date (the governed extension)")
    rb.add_argument("--out", type=Path, required=True)
    rb.set_defaults(func=_cmd_record_baseline)

    pr = sub.add_parser("promote", help="promote with the injected factory")
    pr.add_argument("--run", type=Path, required=True)
    pr.add_argument("--to", dest="version", required=True)
    pr.add_argument(
        "--production-dir", type=Path,
        default=Path("research/mined_factors/production"))
    pr.add_argument("--config", dest="promotion_config", type=Path,
                    default=None)
    pr.add_argument("--baseline", type=Path, default=None,
                    help="pre-authorized extension baseline JSON")
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=_cmd_promote)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Cross-check disagreements arrive via logging.warning — without a
    # configured handler Python's lastResort prints them, but INFO-level
    # progress would vanish; mirror the miner CLI's explicit config so
    # "visible output" is a property of the command, not of the caller.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        force=True,
    )
    args = _parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())

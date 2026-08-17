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

    python -m scripts.research.fundamental_gp_campaign starter-check \
        --run <run_dir>

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

The starter-three-factor link check is a TWO-step sequence: ``mine``
proves the GP path (panelization, merged terminals, provenance-masked
evaluation inside the search), then ``starter-check`` proves the three
frozen factors themselves — the GP's random population cannot construct
C3, so its deterministic evaluation is a separate, run-bound step.
Running ``mine`` alone is NOT a completed link check. Igniting on real
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
import yaml

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
    _verify_pit_binding,
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


# The starter three factors as DETERMINISTIC ASTs (frozen source:
# docs/prereg/quality_profitability.yaml + the asset-growth Δ form).
# The link check cannot rely on the GP's random population to construct
# them — C3 alone needs four income terms, five current/prior deltas and
# the coalesce pair, far beyond any small-run depth — so the OpenSpec
# "starter three-factor end-to-end" obligation is discharged by
# evaluating these expressions EXPLICITLY through the canonical
# evaluator (values, forward returns, report-period provenance and the
# terminal-level alignment mask all on the same path the GP uses).
# Δx = sub(x, x__prior); the coalesce pair merges per period BEFORE
# differencing, exactly as the frozen formula requires.
_STARTER_EXPRESSIONS: dict[str, str] = {
    "C1_GPA": (
        "cs_rank(div_safe(sub($revenue, $oper_cost), $total_assets))"
    ),
    "asset_growth": (
        "cs_rank(div_safe(sub($total_assets, $total_assets__prior), "
        "$total_assets__prior))"
    ),
    "C3_cash_based_OP": (
        "cs_rank(div_safe("
        "add(add(sub(sub(sub("
        "sub(sub(sub($revenue, $oper_cost), $sell_exp), $admin_exp), "
        "sub($accounts_receiv, $accounts_receiv__prior)), "
        "sub($inventories, $inventories__prior)), "
        "sub($prepayment, $prepayment__prior)), "
        "sub($accounts_pay, $accounts_pay__prior)), "
        "sub(coalesce($adv_receipts, $contract_liab), "
        "coalesce($adv_receipts__prior, $contract_liab__prior))), "
        "$total_assets))"
    ),
}


def _cmd_starter_check(args: argparse.Namespace) -> int:
    """Evaluate the three frozen starter factors against a MINED RUN.

    Bound to a run directory, not to a mutable config path: the run
    snapshot's data definition is digest-verified on load, the factory
    is re-invoked on the run's RECORDED geometry, and its output digest
    must reproduce the recorded identity — so the starter record
    describes exactly the panel the run mined, auditable after the
    fact. A starter factor with NO evaluable observations is a broken
    leg, not a completed check: refused, nothing written.
    """
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.fitness import FitnessConfig
    from src.factor_mining.gp_engine import GPConfig, GPEngine
    from src.factor_mining.miner import (
        MinerConfig,
        build_universe_mask,
        load_baseline_predictions,
        search_definition_sha256,
    )

    run_dir = Path(args.run)
    try:
        run_data, run_sha = _load_run_data_config(run_dir)
    except PromotionError as exc:
        print(f"starter-check failed: {exc}", file=sys.stderr)
        return 1
    if not run_data.fundamental_store_root:
        print("starter-check: the run records no fundamental leg — "
              "nothing to check.", file=sys.stderr)
        return 1
    binding_path = run_dir / "fundamental_binding.json"
    if not binding_path.is_file():
        print("starter-check: run has no fundamental_binding.json — it "
              "predates factory-identity recording; re-mine.",
              file=sys.stderr)
        return 1
    if run_data.mode == "pit":
        # Geometry alone cannot see an in-place PIT bundle refresh:
        # prices can move under unchanged dates x instruments, and the
        # starter metrics would then describe data the mining run never
        # used. The run records content fingerprints; verify them like
        # promotion does (codex #441 r6 P1).
        try:
            _verify_pit_binding(run_dir, run_data)
        except PromotionError as exc:
            print(f"starter-check failed: {exc}", file=sys.stderr)
            return 1
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    dates = pd.DatetimeIndex(
        [pd.Timestamp(d) for d in binding["trade_dates"]])
    instruments = [str(c) for c in binding["instruments"]]

    factory = build_panel_factory()
    values, evidence, periods = factory(run_data, dates, instruments)
    got = fundamental_output_sha256(values, evidence, periods)
    if got != binding["output_sha256"]:
        print("starter-check: factory output does not reproduce the "
              f"run's recorded identity (recorded "
              f"{binding['output_sha256']!r}, got {got!r}) — the store "
              "changed or the builder drifted since mining; the starter "
              "record would describe a different panel. Refusing.",
              file=sys.stderr)
        return 1

    panel, fwd = build_panel_for_data(run_data)
    if ([str(d.date()) for d in fwd.index] != list(binding["trade_dates"])
            or [str(c) for c in fwd.columns] != instruments):
        print("starter-check: the rebuilt price-volume panel geometry "
              "does not match the run's recorded geometry — the pv "
              "inputs moved since mining. Refusing.", file=sys.stderr)
        return 1
    merged = {**panel, **values}
    # The run's OWN gp/fitness configuration scores the starter factors:
    # "panel -> GP -> marginal contribution" means the number the GP
    # search itself would assign (fitness composition included), not a
    # bare evaluator metric bundle.
    raw_snapshot = yaml.safe_load(
        (run_dir / "config.yaml").read_text(encoding="utf-8"))
    try:
        gp_config = GPConfig(**raw_snapshot.get("gp", {}))
        fitness_config = FitnessConfig(**raw_snapshot.get("fitness", {}))
    except TypeError as exc:
        print(f"starter-check: run snapshot gp/fitness sections do not "
              f"parse ({exc}); re-mine.", file=sys.stderr)
        return 1
    # The data section has been digest-bound since #415; the gp/fitness
    # sections get the same treatment (codex #441 r8): an edited
    # snapshot must not let this check claim "the run's own criteria"
    # while scoring with something else.
    recorded_search = raw_snapshot.get("search_definition_sha256")
    if not recorded_search:
        print("starter-check: run snapshot carries no "
              "search_definition_sha256 — the gp/fitness sections "
              "cannot be verified; re-mine.", file=sys.stderr)
        return 1
    recomputed_search = search_definition_sha256(gp_config, fitness_config)
    if recorded_search != recomputed_search:
        print("starter-check: gp/fitness sections do not match the "
              f"digest recorded at mining time (recorded "
              f"{recorded_search!r}, recomputed {recomputed_search!r}) "
              "— the snapshot was edited after mining; refusing to "
              "score with criteria the run never used.", file=sys.stderr)
        return 1
    engine = GPEngine(gp_config, fitness_config)
    miner_config = MinerConfig(
        data=run_data, gp=gp_config, fitness=fitness_config,
        output_dir=run_dir)
    universe_mask = build_universe_mask(miner_config)
    # The run's orthogonality baseline joins the scoring context: with
    # w_orthogonality configured, a None baseline silently zeroes the
    # penalty and the reported fitness is NOT the run's composition
    # (codex #441 r8 P1). The canonical loader re-verifies the sidecar;
    # the digest must also equal the bytes mining actually consumed.
    try:
        baseline = load_baseline_predictions(miner_config)
    except ValueError as exc:
        print(f"starter-check: baseline load failed ({exc}); the run's "
              "fitness composition cannot be reproduced.", file=sys.stderr)
        return 1
    recorded_baseline_sha = raw_snapshot.get("baseline_preds_sha256")
    if baseline is not None:
        got_sha = baseline.attrs.get("baseline_preds_sha256")
        if recorded_baseline_sha != got_sha:
            print("starter-check: baseline bytes differ from what "
                  f"mining consumed (recorded {recorded_baseline_sha!r}, "
                  f"loaded {got_sha!r}); refusing.", file=sys.stderr)
            return 1
    elif recorded_baseline_sha:
        print("starter-check: the run recorded a baseline "
              "(baseline_preds_sha256) but none can be loaded now — the "
              "fitness composition cannot be reproduced; refusing.",
              file=sys.stderr)
        return 1

    def _finite(x: float) -> float | None:
        # Bare NaN is not JSON; a strict consumer downstream would
        # reject the whole record. None is the honest spelling of
        # "metric undefined here" (empty/zero-variance IC series).
        import math
        return float(x) if math.isfinite(x) else None

    report: dict[str, dict[str, float | int | str | None]] = {}
    for name, text in _STARTER_EXPRESSIONS.items():
        fitness, result = engine.score_expression(
            parse_expression(text), merged, fwd,
            universe_mask=universe_mask, periods=periods,
            baseline=baseline)
        if result is None:
            print(f"starter-check: {name} returned no evaluation "
                  "bundle (cache hit on a fresh engine is impossible; "
                  "scoring failed) — refusing.", file=sys.stderr)
            return 1
        if result.coverage <= 0.0 or result.n_obs_per_day_min < 1:
            # A starter leg with nothing evaluable means a required
            # field is missing/empty or alignment masked everything —
            # the link is NOT verified for this factor.
            print(f"starter-check: {name} produced no evaluable "
                  f"observations (coverage={result.coverage!r}) — a "
                  "broken leg is not a completed check. Refusing; "
                  "nothing written.", file=sys.stderr)
            return 1
        entry: dict[str, float | int | str | None] = {
            "expression": text,
            "fitness": _finite(fitness),
            "rank_ic_mean": _finite(result.rank_ic_mean),
            "rank_ic_std": _finite(result.rank_ic_std),
            "rank_ir": _finite(result.rank_ir),
            "coverage": float(result.coverage),
            "turnover_daily": _finite(result.turnover_daily),
            "n_obs_per_day_min": int(result.n_obs_per_day_min),
        }
        report[name] = entry
        print(f"{name}: fitness={entry['fitness']} "
              f"rank_ic_mean={entry['rank_ic_mean']} "
              f"rank_ir={entry['rank_ir']} "
              f"coverage={entry['coverage']:.4f}")

    if run_data.mode == "pit":
        # Before/after stability, same as mining and promotion: the
        # entry check cannot see a refresh that starts DURING the panel
        # rebuild or the scoring window — re-verify after all PIT reads,
        # before anything is persisted (codex #441 r7 P1).
        try:
            _verify_pit_binding(run_dir, run_data)
        except PromotionError as exc:
            print(f"starter-check failed after evaluation: {exc} — "
                  "the PIT inputs moved mid-check; nothing written.",
                  file=sys.stderr)
            return 1

    out = (Path(args.out) if args.out is not None
           else run_dir / "starter_factor_report.json")
    try:
        with out.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "purpose": "starter-three-factor-link-check",
                "run_dir": str(run_dir),
                "data_definition_sha256": run_sha,
                "search_definition_sha256": recorded_search,
                "fundamental_output_sha256": got,
                "adjudication_standing": "none — link verification only",
                "scoring_path": "GPEngine.score_expression (the search's "
                                "own fitness composition)",
                "factors": report,
            }, indent=2, allow_nan=False))
    except FileExistsError:
        print(f"starter-check: {out} already exists — refusing to "
              "overwrite an earlier record; pick a fresh path.",
              file=sys.stderr)
        return 1
    print(f"Starter report -> {out}")
    return 0


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

    sc = sub.add_parser(
        "starter-check",
        help="deterministically evaluate the three frozen starter "
             "factors against a mined run")
    sc.add_argument("--run", type=Path, required=True,
                    help="run directory produced by the mine subcommand")
    sc.add_argument("--out", type=Path, default=None,
                    help="report path (default: "
                         "<run>/starter_factor_report.json)")
    sc.set_defaults(func=_cmd_starter_check)

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

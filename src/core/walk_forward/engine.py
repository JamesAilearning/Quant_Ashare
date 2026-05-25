"""Walk-forward engine orchestration and fold-level execution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.core.attribution_industry_loader import (
    PURPOSE_ATTRIBUTION,
    IndustryTaxonomyLoadError,
    resolve_industry_taxonomy,
)
from src.core.backtest_runner import BacktestRunner
from src.core.canonical_backtest_contract import (
    CanonicalAccountConfig,
    CanonicalBacktestInput,
    CanonicalBacktestOutput,
    CanonicalExchangeConfig,
    CanonicalExchangeCostModel,
)
from src.core.logger import get_logger
from src.core.model_config_projection import build_model_train_config
from src.core.model_trainer import ModelTrainer
from src.core.performance_attribution import (
    AttributionConfig,
    AttributionResult,
    PerformanceAttribution,
    PerformanceAttributionError,
)
from src.core.qlib_runtime import is_canonical_qlib_initialized
from src.core.signal_analyzer import (
    SignalAnalysisConfig,
    SignalAnalyzer,
)
from src.core.walk_forward._resume import (
    FoldManifest,
    ResumeMode,
    compute_config_fingerprint,
    decide_fold,
)
from src.core.walk_forward._types import WalkForwardFold, WalkForwardResult
from src.core.walk_forward.aggregate import (
    attribution_section_for_fold,
    build_aggregate_report,
    build_fold_report,
    compute_aggregate,
    extract_cost_metrics,
    write_aggregate_report,
    write_fold_report,
    write_positions,
)
from src.core.walk_forward.config import WalkForwardConfig, WalkForwardError
from src.core.walk_forward.ensemble import (
    apply_ensemble,
    write_prediction_artifact,
)
from src.data.feature_dataset_builder import FeatureDatasetBuilder, FeatureDatasetConfig

_logger = get_logger(__name__)


class WalkForwardEngine:
    """Orchestrates rolling train/predict/backtest across time."""

    @classmethod
    def run(
        cls,
        config: WalkForwardConfig,
        *,
        resume_mode: ResumeMode | None = None,
    ) -> WalkForwardResult:
        if not is_canonical_qlib_initialized():
            raise WalkForwardError(
                "Canonical qlib runtime must be initialized before walk-forward."
            )

        from pathlib import Path

        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(tz=timezone.utc).isoformat()

        # Resume policy — default AUTO (resume any matching manifest).
        # See src/core/walk_forward/_resume.py for the contract.
        effective_resume_mode = resume_mode if resume_mode is not None else ResumeMode.AUTO
        config_fingerprint = compute_config_fingerprint(config)
        discovered_manifests = FoldManifest.discover(output_dir)

        # Generate fold windows
        windows = cls._generate_windows(config)
        if not windows:
            raise WalkForwardError(
                "No valid fold windows could be generated with the given config. "
                "Check that overall period is long enough for train+valid+test windows."
            )

        _logger.info("=" * 60)
        _logger.info("WALK-FORWARD BACKTEST")
        _logger.info("=" * 60)
        _logger.info("Overall window: %s ~ %s", config.overall_start, config.overall_end)
        _logger.info(
            "Training months: %d | Validation months: %d | Test months: %d",
            config.train_months, config.valid_months, config.test_months,
        )
        _logger.info("Ensemble window: %d | Total folds: %d", config.ensemble_window, len(windows))
        _logger.info("Output directory: %s", output_dir)
        if discovered_manifests:
            _logger.info(
                "Resume mode: %s | Discovered %d manifest(s): %s",
                effective_resume_mode.kind.value,
                len(discovered_manifests),
                sorted(discovered_manifests.keys()),
            )
        else:
            _logger.info("Resume mode: %s | No manifests in output_dir",
                         effective_resume_mode.kind.value)
        _logger.info("=" * 60)

        # Run the fold loop
        # Per-fold error isolation: each fold runs inside a try/except.
        # A single transient failure (data fetch, OOM, etc.) produces a
        # NaN-placeholder fold instead of aborting the entire run.
        folds: list[WalkForwardFold] = []
        prior_model_paths: list[tuple[int, str]] = []

        for i, (train_s, train_e, valid_s, valid_e, test_s, test_e) in enumerate(windows):
            decision = decide_fold(
                fold_index=i,
                train_period=f"{train_s} ~ {train_e}",
                test_period=f"{test_s} ~ {test_e}",
                config_fingerprint=config_fingerprint,
                discovered=discovered_manifests,
                resume_mode=effective_resume_mode,
            )

            if decision.skip and decision.manifest is not None:
                _logger.info(
                    "Fold %d: skipped (resumed_from_manifest); "
                    "completed_at=%s, IC(1d)=%.4f, IR=%.3f",
                    i, decision.manifest.completed_at,
                    decision.manifest.fold.ic_1d,
                    decision.manifest.fold.information_ratio,
                )
                fold = decision.manifest.fold
                folds.append(fold)
                if fold.prediction_shape != (0,):
                    prior_model_paths.append((i, decision.manifest.model_path))
                continue

            if decision.reason.startswith(("fingerprint_mismatch", "window_mismatch")):
                _logger.warning(
                    "Fold %d: %s — re-running and overwriting prior manifest",
                    i, decision.reason,
                )

            _logger.info(
                "Fold %d: train=%s~%s, valid=%s~%s, test=%s~%s",
                i, train_s, train_e, valid_s, valid_e, test_s, test_e,
            )
            try:
                fold = cls._run_single_fold(
                    config=config,
                    fold_index=i,
                    train_start=train_s, train_end=train_e,
                    valid_start=valid_s, valid_end=valid_e,
                    test_start=test_s, test_end=test_e,
                    output_dir=output_dir,
                    prior_model_paths=tuple(prior_model_paths),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "Fold %d failed (%s: %s) — replacing with NaN placeholder "
                    "so the aggregate report is still produced.",
                    i, type(exc).__name__, exc,
                )
                fold = WalkForwardFold(
                    fold_index=i,
                    train_period=f"{train_s} ~ {train_e}",
                    valid_period=f"{valid_s} ~ {valid_e}",
                    test_period=f"{test_s} ~ {test_e}",
                    ic_1d=float("nan"),
                    ic_5d=float("nan"),
                    annualized_return=float("nan"),
                    max_drawdown=float("nan"),
                    information_ratio=float("nan"),
                    prediction_shape=(0,),
                )
            folds.append(fold)

            # Placeholder fold (prediction_shape=(0,)) means
            # _run_single_fold raised — the model pickle may be
            # missing or partial. A real successful fold whose IC
            # happens to be NaN (short validation period) still has
            # a valid pickle and should contribute to the ensemble.
            if fold.prediction_shape != (0,):
                model_path = str(output_dir / f"model_fold{i}.pkl")
                prior_model_paths.append((i, model_path))
                # Persist the resume manifest only for successful folds
                # — NaN-placeholder folds (the engine caught an
                # exception) deliberately have no manifest so the next
                # resume attempt re-runs them.
                try:
                    manifest = FoldManifest.from_fold(
                        fold=fold,
                        config=config,
                        model_path=model_path,
                        report_path=str(output_dir / f"fold_{i:02d}_report.json"),
                        predictions_path=str(
                            output_dir / f"fold_{i:02d}_predictions.pkl"
                        ),
                        positions_path=(
                            str(output_dir / f"fold_{i:02d}_positions.json")
                            if (output_dir / f"fold_{i:02d}_positions.json").exists()
                            else None
                        ),
                    )
                    manifest.save(output_dir)
                except Exception:  # noqa: BLE001
                    # Manifest persistence is best-effort; never abort
                    # a successful fold because the manifest couldn't
                    # be written. The fold's actual artifacts are
                    # authoritative.
                    _logger.warning(
                        "Fold %d: failed to write resume manifest",
                        i, exc_info=True,
                    )

            _logger.info(
                "  IC(1d)=%.4f | Return=%.2f%% | MaxDD=%.2f%%",
                fold.ic_1d, fold.annualized_return * 100, fold.max_drawdown * 100,
            )

        # Aggregate
        aggregate = compute_aggregate(folds, seed=config.seed)

        _logger.info("=" * 60)
        _logger.info("AGGREGATE RESULTS")
        _logger.info("=" * 60)
        for key, val in aggregate.items():
            _logger.info("  %s: %.4f", key, val)
        _logger.info("=" * 60)

        aggregate_path = output_dir / "walk_forward_report.json"
        write_aggregate_report(
            path=aggregate_path,
            config=config,
            folds=folds,
            aggregate_metrics=aggregate,
        )
        _logger.info("Aggregate report: %s", aggregate_path)

        # Best-effort run catalog: append one JSONL line so operators
        # can query historical runs without find + jq. Non-fatal on
        # failure — the per-run report is the authoritative artifact.
        try:
            import math
            from dataclasses import asdict

            from src.core.run_catalog import append_run_record
            from src.core.run_catalog import build_record as build_catalog_record
            has_any_nan = any(
                math.isnan(f.ic_1d) or math.isnan(f.ic_5d)
                for f in folds
            )
            import hashlib
            config_json = json.dumps(asdict(config), sort_keys=True, default=str)
            fingerprint = hashlib.sha256(config_json.encode()).hexdigest()[:12]
            record = build_catalog_record(
                engine="walk_forward",
                status="partial" if has_any_nan else "ok",
                started_at=started_at,
                config_fingerprint=fingerprint,
                config_summary={
                    "instruments": config.instruments,
                    "feature_handler": config.feature_handler,
                    "model_type": config.model_type,
                    "ensemble_window": config.ensemble_window,
                    "topk": config.topk,
                    "overall_start": config.overall_start,
                    "overall_end": config.overall_end,
                },
                headline_metrics={
                    "num_folds": aggregate.get("num_folds"),
                    "mean_ic_1d": aggregate.get("mean_ic_1d"),
                    "annualized_return": aggregate.get("mean_annualized_return"),
                    "worst_drawdown": aggregate.get("worst_drawdown"),
                    "mean_information_ratio": aggregate.get("mean_information_ratio"),
                },
                report_path=str(aggregate_path),
                output_dir=str(output_dir),
            )
            append_run_record(record)
        except Exception:  # noqa: BLE001
            _logger.debug("Run catalog append skipped.", exc_info=True)

        return WalkForwardResult(
            folds=folds,
            aggregate_metrics=aggregate,
            num_folds=len(folds),
            report_path=str(aggregate_path),
        )

    # ── real methods ──────────────────────────────────────────────

    @classmethod
    def _generate_windows(cls, config: WalkForwardConfig) -> list[tuple[str, ...]]:
        """Generate (train_s, train_e, valid_s, valid_e, test_s, test_e) tuples."""
        from dateutil.relativedelta import relativedelta

        start = date.fromisoformat(config.overall_start)
        end = date.fromisoformat(config.overall_end)

        windows = []
        cursor = start

        while True:
            train_s = cursor
            train_e = train_s + relativedelta(months=config.train_months) - relativedelta(days=1)
            valid_s = train_e + relativedelta(days=1)
            valid_e = valid_s + relativedelta(months=config.valid_months) - relativedelta(days=1)
            test_s = valid_e + relativedelta(days=1)
            test_e = test_s + relativedelta(months=config.test_months) - relativedelta(days=1)

            if test_e > end:
                # Try fitting partial last fold up to overall_end
                test_e = end
                if test_s >= test_e:
                    break
                # +1 because both start and end dates are inclusive
                if (test_e - test_s).days + 1 < 10:
                    break

            windows.append((
                train_s.isoformat(), train_e.isoformat(),
                valid_s.isoformat(), valid_e.isoformat(),
                test_s.isoformat(), test_e.isoformat(),
            ))

            cursor = cursor + relativedelta(months=config.step_months)

            # Safety: if test_e already reached overall_end, stop
            if test_e >= end:
                break

        return windows

    @classmethod
    def _run_single_fold(
        cls,
        config: WalkForwardConfig,
        fold_index: int,
        train_start: str, train_end: str,
        valid_start: str, valid_end: str,
        test_start: str, test_end: str,
        output_dir: Path,
        prior_model_paths: Sequence[tuple[int, str]] = (),
    ) -> WalkForwardFold:
        """Execute one fold: feature build → train → ensemble → signal
        → backtest → attribution.  Returns a ``WalkForwardFold`` with
        headline metrics for the aggregate report.

        A heavy ``except Exception`` at the caller (:meth:`run`) isolates
        each fold from failures in the others — if this method raises,
        the caller replaces the partially-built fold with a NaN
        placeholder and continues with the remaining windows. We keep
        the try/except *out of this method* because:

        1. Each step below already handles its own expected errors
           (e.g. FeatureDatasetBuilder raises on bad config shapes,
           ModelTrainer raises on unsupported model types).
        2. Wrapping the entire method body in ``except Exception`` would
           hide a local defect — like a typo in a config key — behind a
           NaN fold instead of surfacing it loudly.
        3. The caller's per-fold ``except Exception`` serves as the
           safety net for unpredictable failures (transient OOM, qlib
           data fetch timeouts) while this method's own guards stop the
           predictable ones at source.
        """

        _logger.info("  Fold %d: features...", fold_index)
        feature_result = FeatureDatasetBuilder.build(FeatureDatasetConfig(
            instruments=config.instruments,
            feature_handler=config.feature_handler,
            train_start=train_start,
            train_end=train_end,
            valid_start=valid_start,
            valid_end=valid_end,
            test_start=test_start,
            test_end=test_end,
        ))

        # Train model
        model_path = str(output_dir / f"model_fold{fold_index}.pkl")
        model_result = ModelTrainer.train_and_predict(
            config=build_model_train_config(config),
            dataset=feature_result.dataset,
            model_artifact_path=model_path,
        )

        # Optionally average current fold's predictions with prior
        # fold models' predictions on this dataset. Returns the
        # possibly-replaced predictions plus an ``ensemble_meta`` dict
        # that lands on the fold report so the operator can audit
        # which folds contributed.
        predictions, ensemble_meta = apply_ensemble(
            current_predictions=model_result.predictions,
            current_dataset=feature_result.dataset,
            prior_model_paths=prior_model_paths,
            ensemble_window=config.ensemble_window,
            current_fold_index=fold_index,
        )
        prediction_artifact_path = output_dir / f"fold_{fold_index:02d}_predictions.pkl"
        prediction_artifact_sha = write_prediction_artifact(
            prediction_artifact_path, predictions,
        )
        ensemble_meta = {
            **ensemble_meta,
            "current_model_ref": model_path,
            "prediction_artifact_path": str(prediction_artifact_path),
            "prediction_artifact_sha256": prediction_artifact_sha,
        }

        # Signal analysis
        signal_result = SignalAnalyzer.analyze(
            predictions=predictions,
            config=SignalAnalysisConfig(forward_periods=(1, 5), topk=config.topk),
        )
        # Structural: both periods we asked for must come back. Missing keys
        # signal an analyzer-layer bug, not a bad model — fall-through to
        # ``0.0`` here used to mask analyzer regressions as "this fold had
        # no IC".  Values themselves may be NaN (insufficient data) and
        # propagate through to the fold result honestly.
        missing = [p for p in (1, 5) if p not in signal_result.ic_summary]
        if missing:
            raise WalkForwardError(
                f"Fold {fold_index}: SignalAnalyzer did not return IC for "
                f"forward period(s) {missing}. Keys present: "
                f"{sorted(signal_result.ic_summary.keys())}."
            )

        ic_1d = float(signal_result.ic_summary[1]["mean_ic"])
        ic_5d = float(signal_result.ic_summary[5]["mean_ic"])

        # Backtest
        _logger.info("  Fold %d: backtest...", fold_index)
        backtest_request = CanonicalBacktestInput(
            predictions_ref=str(prediction_artifact_path),
            evaluation_start=test_start,
            evaluation_end=test_end,
            account_config=CanonicalAccountConfig(
                init_cash=config.init_cash,
            ),
            exchange_config=CanonicalExchangeConfig(
                freq="day",
                execution_price_kind=config.execution_price_kind,
                cost_model=CanonicalExchangeCostModel(
                    commission_rate=config.commission_rate,
                    stamp_tax_bps=config.stamp_tax_bps,
                    slippage_bps=config.slippage_bps,
                    min_cost=config.min_cost,
                ),
                limit_threshold=config.limit_threshold,
            ),
            adjust_mode=config.adjust_mode,
            signal_to_execution_lag=config.signal_to_execution_lag,
            benchmark_code=config.benchmark_code,
        )
        backtest_output = BacktestRunner.run(
            request=backtest_request,
            predictions=predictions,
            topk=config.topk,
            n_drop=config.n_drop,
        )

        ann_ret, max_dd, ir = extract_cost_metrics(backtest_output.risk_analysis, fold_index)
        _logger.info(
            "  Fold %d: AnnRet=%.2f%% | MaxDD=%.2f%% | IR=%.3f",
            fold_index, ann_ret * 100, max_dd * 100, ir,
        )

        # Persist a per-fold report and the positions artifact. Previously
        # the only fold-level artefact written was the model pickle, so a
        # walk-forward run produced N pkl files with no IC / return /
        # backtest detail accessible after the fact. Dashboards or diff
        # tools cannot compare two runs from the in-memory ``WalkForwardFold``
        # alone — they need the file on disk.
        positions_path: Path | None = None
        if backtest_output.positions:
            positions_path = output_dir / f"fold_{fold_index:02d}_positions.json"
            write_positions(positions_path, backtest_output.positions)

        # Per-fold performance attribution. Runs after backtest so the
        # attribution engine sees the real positions / return series.
        # Same skip-but-disclose pattern as ``Pipeline.run``: degenerate
        # inputs (e.g. all-zero positions, all-non-positive predictions)
        # raise ``PerformanceAttributionError`` from the engine; we
        # downgrade to "skip + WARN + status in fold report" so a single
        # bad fold does not abort the entire walk-forward run.
        attribution_result, attribution_skipped_reason = (
            cls._run_attribution_for_fold(
                config=config,
                fold_index=fold_index,
                test_start=test_start, test_end=test_end,
                predictions=predictions,
                backtest_output=backtest_output,
            )
        )

        report_path = output_dir / f"fold_{fold_index:02d}_report.json"
        write_fold_report(
            report_path=report_path,
            fold_index=fold_index,
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
            test_start=test_start, test_end=test_end,
            model_artifact_path=model_path,
            model_result=model_result,
            signal_result=signal_result,
            backtest_output=backtest_output,
            positions_path=positions_path,
            ic_1d=ic_1d, ic_5d=ic_5d,
            annualized_return=ann_ret,
            max_drawdown=max_dd,
            information_ratio=ir,
            attribution_result=attribution_result,
            attribution_skipped_reason=attribution_skipped_reason,
            ensemble_meta=ensemble_meta,
        )

        return WalkForwardFold(
            fold_index=fold_index,
            train_period=f"{train_start} ~ {train_end}",
            valid_period=f"{valid_start} ~ {valid_end}",
            test_period=f"{test_start} ~ {test_end}",
            ic_1d=ic_1d,
            ic_5d=ic_5d,
            annualized_return=ann_ret,
            max_drawdown=max_dd,
            information_ratio=ir,
            prediction_shape=model_result.prediction_shape,
            report_path=str(report_path),
        )

    @classmethod
    def _run_attribution_for_fold(
        cls,
        *,
        config: WalkForwardConfig,
        fold_index: int,
        test_start: str, test_end: str,
        predictions: Any,
        backtest_output: CanonicalBacktestOutput,
    ) -> tuple[AttributionResult | None, str | None]:
        """Run per-fold performance attribution; return ``(result, reason)``.

        Mirrors ``Pipeline.run`` step 7 layering exactly:

        - ``run_attribution=False`` → return ``(None,
          "disabled_by_config")``.
        - Backtest produced no positions → return ``(None,
          "no_positions_from_backtest")`` — refusing to silently fall
          back to a prediction-score proxy.
        - Industry artifact configured → resolve via the shared loader;
          a load failure aborts the run with :class:`WalkForwardError`
          (vs the soft skip for engine errors below) because it
          indicates a config / file mismatch the operator must fix
          before any fold can produce trustworthy attribution.
        - Engine raises :class:`PerformanceAttributionError` (degenerate
          inputs) → return ``(None, "engine_error: ...")`` with a
          WARNING log. This matches Pipeline's "skip + WARN" path so
          downstream comparison tools (PR #29 walk-forward-compare)
          can flag the degraded fold without aborting the rest.
        """
        if not config.run_attribution:
            return None, "disabled_by_config"

        if not backtest_output.positions:
            _logger.warning(
                "Fold %d: skipping attribution — backtest produced no "
                "positions. Refusing to fall back to prediction-score "
                "attribution (no implicit fallback).",
                fold_index,
            )
            return None, "no_positions_from_backtest"

        attribution_overrides: dict[str, Any] = {}
        if config.industry_artifact_path:
            # ``purpose=PURPOSE_ATTRIBUTION`` is the explicit "this is
            # post-hoc analysis, not training" declaration. The shared
            # loader uses the purpose enum to decide whether the
            # temporal-leakage check fires; we no longer rely on
            # ``reference_date=None`` as the implicit signal. See the
            # ``purpose`` parameter docstring in
            # :func:`resolve_industry_taxonomy` for the full
            # rationale.
            try:
                resolution = resolve_industry_taxonomy(
                    artifact_path=str(config.industry_artifact_path),
                    manifest_path=str(config.industry_manifest_path),
                    taxonomy_id=str(config.industry_taxonomy_id).strip(),
                    temporal_mode=config.industry_temporal_mode,
                    purpose=PURPOSE_ATTRIBUTION,
                )
            except IndustryTaxonomyLoadError as exc:
                # Industry-artifact load failures are config / file
                # problems — every fold will hit the same error. Promote
                # to a hard ``WalkForwardError`` rather than skipping
                # silently so the operator fixes the root cause once.
                raise WalkForwardError(
                    f"Fold {fold_index}: industry taxonomy load failed: {exc}"
                ) from exc
            for warning in resolution.warnings:
                _logger.warning(
                    "Fold %d industry taxonomy contract warning: %s",
                    fold_index, warning,
                )
            attribution_overrides["industry_map_override"] = resolution.industry_map
            attribution_overrides["industry_taxonomy_id"] = resolution.taxonomy_id

        attr_config = AttributionConfig(
            start_date=test_start,
            end_date=test_end,
            **attribution_overrides,
        )

        try:
            result = PerformanceAttribution.analyze(
                return_series=backtest_output.return_series,
                # Use the ensemble-aware predictions (same series the
                # backtest received) so attribution's universe and the
                # backtest's universe are guaranteed to match.
                predictions=predictions,
                config=attr_config,
                positions=backtest_output.positions,
            )
        except PerformanceAttributionError as exc:
            _logger.warning(
                "Fold %d: attribution skipped — engine raised %s: %s. "
                "Backtest and risk_analysis remain valid; only the "
                "sector-attribution block is absent from this fold's report.",
                fold_index, type(exc).__name__, exc,
            )
            return None, f"engine_error: {type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Fold %d: attribution skipped due to unexpected error %s: %s. "
                "Backtest and risk_analysis remain valid; only the "
                "sector-attribution block is absent from this fold's report.",
                fold_index, type(exc).__name__, exc,
            )
            return None, f"unexpected_error: {type(exc).__name__}: {exc}"

        return result, None

    # ── thin wrappers ─────────────────────────────────────────────

    @classmethod
    def _build_aggregate_report(cls, *, config, folds, aggregate_metrics):
        """Backward-compat facade — see :func:`aggregate.build_aggregate_report`."""
        return build_aggregate_report(
            config=config, folds=folds,
            aggregate_metrics=aggregate_metrics,
        )

    @classmethod
    def _write_aggregate_report(cls, *, path, config, folds, aggregate_metrics):
        """Backward-compat facade — see :func:`aggregate.write_aggregate_report`."""
        return write_aggregate_report(
            path=path, config=config, folds=folds,
            aggregate_metrics=aggregate_metrics,
        )

    @classmethod
    def _compute_aggregate(cls, folds, *, seed=42):
        """Backward-compat facade — see :func:`aggregate.compute_aggregate`."""
        return compute_aggregate(folds, seed=seed)

    @classmethod
    def _maybe_apply_ensemble(cls, *, current_predictions, current_dataset,
                               prior_model_paths, ensemble_window,
                               current_fold_index):
        """Backward-compat facade — see :func:`ensemble.apply_ensemble`."""
        return apply_ensemble(
            current_predictions=current_predictions,
            current_dataset=current_dataset,
            prior_model_paths=prior_model_paths,
            ensemble_window=ensemble_window,
            current_fold_index=current_fold_index,
        )

    @staticmethod
    def _write_prediction_artifact(path, predictions):
        """Backward-compat facade — see :func:`ensemble.write_prediction_artifact`."""
        return write_prediction_artifact(path, predictions)

    @classmethod
    def _write_positions(cls, path, positions):
        """Backward-compat facade — see :func:`aggregate.write_positions`."""
        write_positions(path, positions)

    @classmethod
    def _build_fold_report(cls, **kwargs):
        """Backward-compat facade — see :func:`aggregate.build_fold_report`."""
        return build_fold_report(**kwargs)

    @classmethod
    def _attribution_section_for_fold(cls, attribution_result, skipped_reason):
        """Backward-compat facade — see :func:`aggregate.attribution_section_for_fold`."""
        return attribution_section_for_fold(attribution_result, skipped_reason)

    @classmethod
    def _write_fold_report(cls, **kwargs):
        """Backward-compat facade — see :func:`aggregate.write_fold_report`."""
        write_fold_report(**kwargs)

    @staticmethod
    def _extract_cost_metrics(risk_analysis, fold_index):
        """Backward-compat facade — see :func:`aggregate.extract_cost_metrics`."""
        return extract_cost_metrics(risk_analysis, fold_index)

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
import hashlib
import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .factor_pool import FactorPool
from .fitness import FitnessConfig
from .gp_engine import GenerationStats, GPConfig, GPEngine

_log = logging.getLogger(__name__)

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
    # Terminal whitelist for the panel. Empty = the full
    # ``FeatureRegistry.V1`` twelve (legacy). A campaign whose frozen
    # protocol admits only a subset MUST list it here (codex #401 r9):
    # otherwise the view loads valuation/market-cap terminals the
    # pre-registration explicitly excludes and the GP can breed on
    # forbidden inputs.
    fields: tuple[str, ...] = ()
    # Forward-return price field: "open" (legacy open→open, D1) or
    # "close" (pv_incremental_v1's frozen close_exec_to_close_next).
    # Breeding against a different target than the one that
    # adjudicates is the same class of defect as a different metric.
    forward_return_price: str = "open"
    # Campaign baseline predictions (wide parquet, date × instrument)
    # for the orthogonality penalty. Plain pandas read — no qlib, no
    # PIT, so the D5 gate is untouched. Loading REQUIRES the exporter's
    # provenance sidecar (see ``load_baseline_predictions``): the
    # penalty is the campaign's only incremental criterion, so an ad
    # hoc parquet must never key it.
    baseline_preds_path: str = ""
    baseline_model: str = ""


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
    """Parse a YAML config into a typed ``MinerConfig``.

    Goes through the repository's environment-aware loader so
    ``${QUANT_PROVIDER_URI:-/default}`` placeholders expand exactly as
    they do for every other config in the repo (codex #401 r11: a bare
    ``yaml.safe_load`` handed the literal ``${...}`` string to
    ``PITDataProvider``, so a campaign config using the documented
    env-var convention could not ignite at all). The loader is pure
    ``os``/``re``/``yaml`` — no qlib, no PIT, so the D5 gate is
    untouched.
    """
    p = Path(path)
    from src.core._yaml_loader import (  # noqa: PLC0415
        load_yaml_with_inheritance,
    )
    raw = load_yaml_with_inheritance(p)
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


# Consolidated into ``src.factor_mining._synthetic_panel`` (bug.md
# P2-5) — the body lived in both this file and ``promote.py`` with
# identical contracts (including the qlib LABEL_LOOKAHEAD_DAYS=2
# comment added in #165's P1-6 clarification, which now lives at
# the canonical implementation site). Re-export under the
# leading-underscore name so call sites in this module don't change.
from src.factor_mining._synthetic_panel import (  # noqa: E402
    build_synthetic_panel as _build_synthetic_panel,
)


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
        # Frozen terminal whitelist when a campaign declares one
        # (codex #401 r9); None keeps the legacy full V1 registry.
        fields=list(config.fields) if config.fields else None,
    )
    panel = view.load_panel()
    fwd = view.forward_return(horizon=config.forward_horizon,
                              price=config.forward_return_price)
    return panel, fwd


def load_baseline_predictions(config: MinerConfig):
    """Load the campaign baseline predictions, provenance-bound.

    Returns ``None`` when no baseline path is configured (the legacy /
    synthetic path — the orthogonality penalty stays inert).

    The parquet MUST be accompanied by the exporter's sidecar
    ``<parquet>.provenance.json`` binding THIS file: ``model`` equal to
    the configured ``baseline_model``, ``file_sha256`` equal to the
    bytes on disk, and non-empty ``run_config_sha256`` / ``source_git``
    — the same contract the OOS evaluator enforces. The orthogonality
    penalty is the campaign's ONLY incremental criterion, so a stale or
    ad hoc parquet keying it would silently invalidate every fitness
    score in the run. Fail loud instead.

    Plain pandas + json + hashlib: no qlib, no ``src.pit`` (D5).
    """
    import hashlib

    import pandas as pd

    path_str = config.data.baseline_preds_path
    if not path_str:
        # The symmetric failure to a namespace mismatch (codex #401
        # r2): a campaign config that ENABLES the orthogonality
        # penalty but forgets to bind the exported baseline would
        # breed with a zero penalty on every candidate — no
        # incremental criterion at all — while looking exactly like a
        # healthy legacy run. Refuse before any scoring.
        if config.fitness.w_orthogonality != 0.0:
            raise ValueError(
                "fitness.w_orthogonality is enabled "
                f"({config.fitness.w_orthogonality}) but "
                "data.baseline_preds_path is empty — the campaign's "
                "only incremental criterion would silently contribute "
                "nothing to every score; bind the exported baseline "
                "or disable the penalty; refusing."
            )
        return None
    if config.fitness.w_orthogonality == 0.0:
        # Inverse mismatch: a baseline is bound but the penalty is
        # off. Not silently wrong (no candidate is mis-certified), but
        # the intent and the behaviour disagree — say so out loud.
        _log.warning(
            "baseline_preds_path is set but fitness.w_orthogonality "
            "is 0.0 — the baseline will be loaded and IGNORED; no "
            "orthogonality penalty will apply to this run."
        )
    path = Path(path_str)
    if not path.is_file():
        raise ValueError(
            f"baseline_preds_path {path} does not exist — the campaign "
            "orthogonality penalty has no baseline to measure against; "
            "refusing."
        )
    if not config.data.baseline_model:
        raise ValueError(
            "baseline_preds_path is set but baseline_model is empty — "
            "the run must declare WHICH baseline model it binds to so "
            "the sidecar can be verified; refusing."
        )
    raw = path.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    sidecar = path.with_name(path.name + ".provenance.json")
    if not sidecar.is_file():
        raise ValueError(
            f"baseline provenance sidecar {sidecar.name} not found next "
            f"to {path.name} — the incremental criterion must bind to a "
            "ledgered baseline run; refusing."
        )
    prov = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(prov, dict):
        raise ValueError(
            f"baseline provenance sidecar {sidecar.name} is not a JSON "
            "object; refusing."
        )
    if prov.get("model") != config.data.baseline_model:
        raise ValueError(
            f"baseline provenance declares model {prov.get('model')!r} "
            f"but the run binds to {config.data.baseline_model!r}; "
            "refusing."
        )
    if prov.get("file_sha256") != file_sha:
        raise ValueError(
            "baseline provenance file_sha256 does not match the parquet "
            "on disk — stale/mismatched sidecar; refusing."
        )
    for key in ("run_config_sha256", "source_git"):
        val = prov.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(
                f"baseline provenance is missing {key!r} — full run "
                "provenance is required before the baseline may key the "
                "incremental criterion; refusing."
            )
    frame = pd.read_parquet(path)
    if frame.empty:
        raise ValueError(f"baseline predictions {path} are empty; refusing.")
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    # Record WHICH bytes were consumed (codex #402 r4): the config
    # only carries a path, which resolves against the miner's launch
    # directory — a downstream consumer searching for that path can
    # hash a different same-named file. The digest is the identity.
    frame.attrs["baseline_preds_sha256"] = file_sha
    frame.attrs["baseline_preds_resolved_path"] = str(path.resolve())
    return frame


def pit_binding_fingerprints(data: DataConfig) -> dict[str, str]:
    """Content fingerprints of the PIT inputs a run was actually mined on.

    The data-definition digest hashes CONFIG VALUES (paths included), so an
    in-place refresh of the bundle or the registry between mining and
    promotion would pass every config check while the panel bytes changed
    underneath (external finding on #415 r4). Recorded at mining time and
    re-verified by promote:

    * the bundle's calendar-file content hash (the repo's standard bundle
      identity — ``compute_bundle_content_hash``, cheap by design);
    * the delisted registry's file sha256.
    """
    from src.data.bundle_manifest import (  # noqa: PLC0415
        compute_bundle_content_hash,
    )
    registry = Path(data.delisted_registry_path)
    return {
        "pit_bundle_content_hash":
            compute_bundle_content_hash(data.pit_provider_uri),
        "delisted_registry_sha256":
            hashlib.sha256(registry.read_bytes()).hexdigest(),
    }


def data_definition_sha256(data: DataConfig) -> str:
    """Canonical digest of a data definition — ONE implementation.

    Written into every run's ``config.yaml`` at mining time and recomputed
    by ``promote`` at validation time; keeping both sides on this single
    function is what makes the comparison meaningful (a second hand-rolled
    canonicalization is exactly what would drift).
    """
    return hashlib.sha256(
        json.dumps(asdict(data), sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_panel_for_data(data: DataConfig):
    """Build (panel, forward_returns) for a ``DataConfig``.

    Public seam shared with ``promote``: the promotion CLI re-validates a
    mined pool on the SAME data definition the run was mined with (fields,
    forward-return price, window, universe), so both entry points must
    dispatch through one function — a hand-maintained mirror is exactly
    what drifted (external finding: ``PromotionDataConfig`` lacked
    ``fields`` / ``forward_return_price`` and silently re-validated on the
    open→open label).
    """
    if data.mode == "synthetic":
        return _build_synthetic_panel(
            n_tickers=data.synthetic_n_tickers,
            n_dates=data.synthetic_n_dates,
            seed=data.synthetic_seed,
        )
    if data.mode == "pit":
        return _build_pit_panel(data)
    raise ValueError(
        f"Unknown data.mode {data.mode!r}; expected 'synthetic' or 'pit'"
    )


def build_panel(config: MinerConfig):
    return build_panel_for_data(config.data)


def build_universe_mask(config: MinerConfig):
    """Universe-membership mask (date × ticker bool) for the run, or None.

    PIT mode returns the boolean membership frame from
    ``FactorMiningDataView.universe_mask`` so the evaluator can measure
    coverage members-only (see ``evaluator._coverage``) — without it, a
    survivorship-corrected panel's legitimate non-member NaNs make
    ``coverage_min`` unsatisfiable and every GP candidate is rejected.
    Synthetic mode has no membership concept (dense panel) and returns
    None, which the evaluator treats as the legacy all-cells denominator.
    """
    if config.data.mode != "pit":
        return None
    from src.pit.query import PITDataProvider  # noqa: PLC0415

    from .pit_adapter import FactorMiningDataView  # noqa: PLC0415

    data = config.data
    provider = PITDataProvider(
        provider_uri=data.pit_provider_uri,
        delisted_registry_path=data.delisted_registry_path,
    )
    view = FactorMiningDataView(
        provider,
        start=data.start_date,
        end=data.end_date,
        universe_name=data.universe_name,
    )
    return view.universe_mask()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _autogenerate_run_id(seed: int) -> str:
    # Timestamp + seed alone is NOT unique: two same-seed launches in one
    # second (or a same-second retry) collided and, with the old
    # exist_ok=True mkdir, silently OVERWROTE the earlier run's pool /
    # history / config — replaceable provenance for whatever later got
    # promoted (external finding #3, 2026-08-10). The random suffix makes
    # every invocation's directory unique by construction.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{seed}-{uuid.uuid4().hex[:8]}"


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
    # Reserve the run directory FIRST (codex P2 on #418): a duplicate
    # pinned run_id must be refused before the expensive part — panel
    # build, baseline load and the full GP run — not after burning it.
    run_id = config.run_id or _autogenerate_run_id(config.gp.seed)
    run_dir = Path(config.output_dir) / "runs" / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise RuntimeError(
            f"run directory already exists: {run_dir} — refusing to "
            "overwrite an existing run's pool / history / config (the "
            "promotion chain treats them as provenance). Pick a fresh "
            "run_id (or leave run_id null to autogenerate a unique one)."
        ) from None

    try:
        panel, fwd = build_panel(config)
        universe_mask = build_universe_mask(config)
        baseline = load_baseline_predictions(config)
        engine = GPEngine(config.gp, config.fitness)
        pool = engine.run(panel, fwd, universe_mask=universe_mask,
                          baseline=baseline)
    except BaseException:
        # Release the reservation on failure — rmdir only removes an
        # EMPTY directory, so if anything ever lands in run_dir before
        # this point it is deliberately kept for post-mortem.
        try:
            run_dir.rmdir()
        except OSError:
            pass  # fallback-ok: cleanup of an empty reservation is
            # best-effort; the mining error below is the real signal.
        raise

    full_pool_size = len(pool)
    if config.pool_top_k is not None and full_pool_size > config.pool_top_k:
        pool = _truncate_pool_to_top_k(pool, config.pool_top_k)

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
        # Recorded AT MINING TIME (codex P1 on #415): promotion recomputes
        # the digest from the data section and refuses a mismatch, so a
        # post-mining hand-edit of this snapshot (that does not also forge
        # the hash) is caught instead of silently re-binding the pool to a
        # panel it was never mined on.
        "data_definition_sha256": data_definition_sha256(config.data),
        "gp": asdict(config.gp),
        "fitness": asdict(config.fitness),
    }
    if config.data.mode == "pit":
        # Bind the run to the CONTENT of its PIT inputs, not just their
        # paths (external finding on #415 r4) — promote re-verifies these.
        config_dump.update(pit_binding_fingerprints(config.data))
    if baseline is not None:
        # Operator decision A: the baseline keeps the production
        # walk-forward fold geometry, whose first out-of-fold date is
        # later than the IS window start — so some expressions are
        # scored with NO baseline overlap and carry no orthogonality
        # penalty. That gap is DISCLOSED here (run-level counts + the
        # baseline's own date span), never silently absorbed into the
        # scores.
        config_dump["baseline_preds_sha256"] = baseline.attrs.get(
            "baseline_preds_sha256")
        config_dump["baseline_preds_resolved_path"] = baseline.attrs.get(
            "baseline_preds_resolved_path")
        config_dump["baseline_orthogonality"] = {
            "baseline_first_date": str(baseline.index.min())[:10],
            "baseline_last_date": str(baseline.index.max())[:10],
            "baseline_n_days": int(baseline.shape[0]),
            "expressions_scored": engine._orthogonality_scored,
            "expressions_without_baseline_overlap":
                engine._orthogonality_uncovered,
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
    # The documented CLI runs unattended for hours; without an explicit
    # logging config Python's WARNING default swallows the engine's
    # per-generation INFO progress — the operator's only feed (codex
    # #419; the 31h invisible batch would have survived its own fix).
    # force=True so an import-time handler cannot demote it.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        force=True,
    )
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

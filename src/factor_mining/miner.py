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
from datetime import date, datetime, timezone
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
    # --- Fundamental-panel campaign (opt-in; all empty = no fundamental
    # leg, byte-identical legacy behavior). These are the inputs the
    # injected panel factory consumes, recorded HERE so promotion can
    # rebuild the same panel from the run's persisted contract instead of
    # unrecorded external state. They enter ``data_definition_sha256``
    # via ``asdict`` like every other field — hash not covering them
    # would let two different panels count as the same run.
    fundamental_store_root: str = ""
    fundamental_calendar_path: str = ""
    # Charter field names (bare, no ``$``) the factory panels.
    fundamental_fields: tuple[str, ...] = ()
    # The SIGNED financial-sector exclusion set (qlib tickers). The view
    # drops these issuers from the panel, so the universe mask must drop
    # them from the coverage DENOMINATOR too — recorded here (not
    # re-derived at mask time) so mining and promotion cannot drift.
    financial_exclusions: tuple[str, ...] = ()


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


def normalize_yaml_dates(payload: dict) -> dict:
    """ISO-stringify YAML date/datetime values in a config section.

    Unquoted YAML dates (``end_date: 2022-12-31``) parse into
    ``datetime.date`` objects, which ``DataConfig`` happily carries until
    ``data_definition_sha256``'s ``json.dumps`` raises ``TypeError`` — at
    config-dump time, AFTER a potentially multi-hour GP run (codex P1 on
    #415 r6). Normalized at every yaml→config boundary (miner and
    promote), so construction is the guarantee, not the dump.
    """
    return {
        key: value.isoformat() if isinstance(value, (date, datetime))
        else value
        for key, value in payload.items()
    }


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

    def _section(name: str) -> dict:
        # ``or {}`` would launder falsy non-mappings (``data: false``,
        # ``gp: []``) into all-default sections and silently mine a
        # different experiment (same class as codex P2 on #415 r8);
        # only an ABSENT/null section legitimately means defaults.
        section = raw.get(name)
        if section is None:
            return {}
        if not isinstance(section, dict):
            raise ValueError(
                f"config section {name!r} must be a YAML mapping or "
                f"absent, got {type(section).__name__}."
            )
        return section

    data = DataConfig(**normalize_yaml_dates(_section("data")))
    gp = GPConfig(**_section("gp"))
    fitness = FitnessConfig(**_section("fitness"))
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

    * the bundle's full data digest — every byte under ``calendars/``,
      ``instruments/`` and ``features/``. The calendar-only bundle
      identity is NOT enough here: prices, fundamentals, or membership
      can be corrected in place under an unchanged calendar (external
      finding on #415 r5). A full read costs on the order of a minute —
      paid twice per multi-hour mining run and once per promotion,
      nowhere else;
    * the delisted registry's file sha256.
    """
    from src.data.bundle_manifest import (  # noqa: PLC0415
        compute_bundle_data_sha256,
    )
    registry = Path(data.delisted_registry_path)
    return {
        "pit_bundle_data_sha256":
            compute_bundle_data_sha256(data.pit_provider_uri),
        "delisted_registry_sha256":
            hashlib.sha256(registry.read_bytes()).hexdigest(),
    }


def fundamental_binding_fingerprints(data: DataConfig) -> dict[str, str]:
    """Content fingerprints of the fundamental inputs a run mines on.

    The financial-statement store and the trading calendar can be
    refreshed IN PLACE — path and config identity unchanged while the
    panel bytes move — so, exactly like the PIT side
    (``pit_binding_fingerprints``), the binding is to CONTENT: every
    byte of every file under the store root, plus the calendar file.
    Taken before the build and re-taken after mining; promotion takes
    its own pair around its evaluation window.

    Pure hashlib + pathlib — no qlib, no PIT, no research import (D5).
    """
    store_root = Path(data.fundamental_store_root)
    if not store_root.is_dir():
        raise ValueError(
            f"fundamental_store_root {store_root} is not a directory — "
            "the fundamental leg cannot be content-bound; refusing."
        )
    h = hashlib.sha256()
    files = sorted(
        p for p in store_root.rglob("*") if p.is_file()
    )
    if not files:
        raise ValueError(
            f"fundamental_store_root {store_root} contains no files — an "
            "empty store cannot be what the campaign mines on; refusing."
        )
    for p in files:
        h.update(p.relative_to(store_root).as_posix().encode("utf-8"))
        h.update(b"\x1f")
        h.update(p.read_bytes())
        h.update(b"\x1e")
    calendar = Path(data.fundamental_calendar_path)
    if not calendar.is_file():
        raise ValueError(
            f"fundamental_calendar_path {calendar} is not a file — the "
            "fundamental leg cannot be content-bound; refusing."
        )
    return {
        "fundamental_store_sha256": h.hexdigest(),
        "fundamental_calendar_sha256":
            hashlib.sha256(calendar.read_bytes()).hexdigest(),
    }


def data_definition_sha256(
    data: DataConfig, *, restrict_to=None,
) -> str:
    """Canonical digest of a data definition — ONE implementation.

    Written into every run's ``config.yaml`` at mining time and recomputed
    by ``promote`` at validation time; keeping both sides on this single
    function is what makes the comparison meaningful (a second hand-rolled
    canonicalization is exactly what would drift).
    """
    payload = asdict(data)
    if restrict_to is not None:
        # Schema-migration verification (promote): a snapshot written
        # under an OLDER DataConfig recorded its digest over the fields
        # that existed THEN. Recomputing over today's full field set
        # would refuse every pre-extension run with a misleading
        # "edited after mining" — restricting to the snapshot's own key
        # set reproduces the original canonicalization exactly, while
        # any edit to a recorded VALUE still changes the digest.
        payload = {k: v for k, v in payload.items() if k in restrict_to}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
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


# ---------------------------------------------------------------------------
# Fundamental panel seam
# ---------------------------------------------------------------------------
#
# The fundamental bridge lives in ``src/research/`` and this package must
# not import it (the research-isolation gate refuses any non-research
# ``src.*`` module importing ``src.research.*``). The panel factory is
# therefore INJECTED by the campaign scripts under ``scripts/research/``
# — the one layer that sees both sides. The factory consumes the run's
# persisted ``DataConfig`` (store root, calendar, fields, exclusions) and
# the panel geometry the seam owner hands it, and returns the documented
# flat triple ``(values, evidence, periods)`` whose keys are terminal
# names (``$revenue``, ``$revenue__prior``, ...). The factory callable is
# the ONE input the persisted contract cannot carry, so its identity is
# recorded as a digest of its OUTPUT (see ``panel_digest``) — never as
# self-reported metadata.


def _verify_fundamental_triple(triple, fwd, panel):
    """Validate the factory's output against the seam contract.

    Returns the verified ``(values, evidence, periods)``. Refuses:
    anything but a 3-tuple of mappings; key sets that disagree between
    the three sections; frames whose geometry differs from the run's
    (trade date × instrument) panel; and keys that collide with the
    price-volume panel (a factory must never shadow a qlib terminal).
    """
    try:
        values, evidence, periods = triple
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "fundamental panel factory must return the documented "
            "(values, evidence, periods) triple; got "
            f"{type(triple).__name__}."
        ) from exc
    sections = {"values": values, "evidence": evidence, "periods": periods}
    for name, mapping in sections.items():
        if not hasattr(mapping, "keys"):
            raise RuntimeError(
                f"fundamental factory {name} is not a mapping "
                f"({type(mapping).__name__})."
            )
    keysets = {name: set(m.keys()) for name, m in sections.items()}
    if not (keysets["values"] == keysets["evidence"] == keysets["periods"]):
        raise RuntimeError(
            "fundamental factory sections disagree on their key sets — "
            "every terminal must carry values, evidence AND periods: "
            f"values={sorted(keysets['values'])}, "
            f"evidence={sorted(keysets['evidence'])}, "
            f"periods={sorted(keysets['periods'])}."
        )
    if not keysets["values"]:
        raise RuntimeError(
            "fundamental factory returned an empty panel — a configured "
            "fundamental leg that contributes nothing is a wiring "
            "failure, not a valid run."
        )
    collisions = keysets["values"] & set(panel.keys())
    if collisions:
        raise RuntimeError(
            "fundamental factory keys collide with the price-volume "
            f"panel: {sorted(collisions)} — a factory must never shadow "
            "a qlib terminal."
        )
    for name, mapping in sections.items():
        for key, frame in mapping.items():
            if not frame.index.equals(fwd.index) or not frame.columns.equals(
                fwd.columns
            ):
                raise RuntimeError(
                    f"fundamental factory {name}[{key!r}] geometry does "
                    "not match the run panel (trade dates × instruments) "
                    "— the seam hands the factory the exact geometry and "
                    "the bridge reindexes onto it; a mismatch means the "
                    "factory is not the canonical bridge."
                )
    return values, evidence, periods


def _build_fundamental_leg(data: DataConfig, factory, fwd, panel):
    """Call the injected factory and verify + digest its output.

    Returns ``(values, evidence, periods, output_sha256)``.
    """
    from .panel_digest import fundamental_output_sha256  # noqa: PLC0415

    triple = factory(data, fwd.index, list(fwd.columns))
    values, evidence, periods = _verify_fundamental_triple(triple, fwd, panel)
    digest = fundamental_output_sha256(values, evidence, periods)
    return values, evidence, periods, digest


def fundamental_leg_declared(data: DataConfig) -> bool:
    """Whether this data definition declares a fundamental leg — strictly.

    Declaration is ANY populated field of the fundamental quartet, not
    just the store root: ``financial_exclusions`` alone would slip past
    a store-root-only flag and still cut the coverage denominator in
    ``build_universe_mask`` while mining a price-volume-only panel — a
    different experiment silently wearing a legacy config. A PARTIAL
    quartet is therefore refused here (used by mining AND promotion),
    never interpreted: the required trio must arrive together;
    ``financial_exclusions`` alone stays optional WITHIN a declared leg.
    """
    populated = {
        name for name, value in (
            ("fundamental_store_root", data.fundamental_store_root),
            ("fundamental_calendar_path", data.fundamental_calendar_path),
            ("fundamental_fields", data.fundamental_fields),
            ("financial_exclusions", data.financial_exclusions),
        ) if value
    }
    if not populated:
        return False
    required_missing = {
        "fundamental_store_root", "fundamental_calendar_path",
        "fundamental_fields",
    } - populated
    if required_missing:
        raise ValueError(
            "fundamental configuration is PARTIAL: "
            f"{sorted(populated)} set but {sorted(required_missing)} "
            "empty — a partial quartet cannot be interpreted (e.g. "
            "financial_exclusions alone would still cut the coverage "
            "denominator while mining a price-volume-only panel: a "
            "different experiment, not a config default). Populate the "
            "fundamental leg fully or clear it."
        )
    return True


def _check_fundamental_coherence(data: DataConfig, factory) -> None:
    """The factory and the fundamental config must arrive together.

    A factory without recorded inputs has nothing promotion could
    rebuild from; recorded inputs without a factory would mine a panel
    that silently lacks the leg its config declares. Both directions
    are wiring failures — refuse before any expensive work.
    """
    declared = fundamental_leg_declared(data)
    if declared and factory is None:
        raise ValueError(
            "DataConfig declares a fundamental leg "
            f"(fundamental_store_root={data.fundamental_store_root!r}) "
            "but no fundamental_panel_factory was injected — the run "
            "would mine WITHOUT the leg its config records. Drive the "
            "run through the campaign script that injects the factory."
        )
    if factory is not None and not declared:
        raise ValueError(
            "a fundamental_panel_factory was injected but the DataConfig "
            "records no fundamental inputs (fundamental_store_root is "
            "empty) — promotion could never rebuild this panel from the "
            "run's persisted contract; record the inputs in DataConfig."
        )


def _apply_financial_exclusions(mask, exclusions: tuple[str, ...]):
    """Drop the signed financial exclusions from the universe mask.

    The view removes financial issuers from the fundamental panel, so
    leaving them in the mask keeps their cells in the coverage
    DENOMINATOR forever-uncovered — depressing coverage, which feeds
    candidate admission and fitness. Uses the run's PERSISTED exclusion
    set so mining and promotion apply exactly the same cut.
    """
    if mask is None or not exclusions:
        return mask
    present = [t for t in exclusions if t in mask.columns]
    absent = sorted(set(exclusions) - set(present))
    if absent:
        _log.info(
            "financial_exclusions: %d name(s) not in the universe mask "
            "(never members in this window): %s",
            len(absent), absent[:10],
        )
    if present:
        mask = mask.copy()
        mask.loc[:, present] = False
        _log.info(
            "financial_exclusions: %d member column(s) excluded from the "
            "coverage denominator.", len(present),
        )
    return mask


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
    return _apply_financial_exclusions(
        view.universe_mask(), data.financial_exclusions)


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


def run_mining(
    config: MinerConfig, *, fundamental_panel_factory=None,
) -> RunResult:
    """Execute the full miner pipeline: build panel → run GP → save pool.

    When ``config.pool_top_k`` is set, the saved pool is truncated to
    the top-K entries by fitness BEFORE persistence. The returned
    ``RunResult.pool`` reflects the saved (truncated) pool so callers
    inspecting ``result.pool`` see the same entries that downstream
    consumers (handler, walk-forward) will load.

    ``fundamental_panel_factory`` is the injection seam for the
    fundamental campaign (see the seam section above): a callable
    ``(data_config, trade_dates, instruments) -> (values, evidence,
    periods)`` injected by ``scripts/research/``. It must arrive
    together with the fundamental ``DataConfig`` fields (both-or-
    neither), its output is verified and content-digested here, and the
    run records that digest plus the exact geometry it was computed on
    so promotion can re-derive the factory's identity from BEHAVIOR —
    never from self-reported metadata. ``None`` (default) is the legacy
    price-volume path, byte-identical to before.
    """
    _check_fundamental_coherence(config.data, fundamental_panel_factory)
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
        # Compute the canonical digest BEFORE mining (codex P1 on #415
        # r6): a data definition the canonical serializer cannot hash
        # (e.g. a programmatic caller passing datetime.date objects)
        # must fail in seconds — with the reservation released — not at
        # config-dump time after the GP burn, stranding a run directory
        # with artifacts but no config.yaml.
        definition_sha = data_definition_sha256(config.data)

        # Fingerprint the PIT inputs BEFORE anything reads them (external
        # finding on #415 r5): captured after the panel build, an ingest
        # refresh during the build would record the NEW identity for a
        # pool mined on the OLD bytes.
        fingerprints: dict[str, str] | None = None
        if config.data.mode == "pit":
            fingerprints = pit_binding_fingerprints(config.data)
        # The fundamental inputs get the SAME before/after stability
        # check as the PIT inputs: taken before anything reads them,
        # re-taken after mining, mismatch refused before any artifact —
        # captured only after the build, a store refresh during it would
        # record the NEW identity for a pool mined on the OLD bytes.
        fundamental_fingerprints: dict[str, str] | None = None
        if fundamental_panel_factory is not None:
            fundamental_fingerprints = fundamental_binding_fingerprints(
                config.data)

        panel, fwd = build_panel(config)
        fundamental_output_sha: str | None = None
        periods = None
        if fundamental_panel_factory is not None:
            values, _evidence, periods, fundamental_output_sha = (
                _build_fundamental_leg(
                    config.data, fundamental_panel_factory, fwd, panel))
            # Merge AFTER verification: keys are collision-checked and
            # geometry-checked against the run panel, so from here on the
            # engine sees one flat mapping and derives its terminal
            # whitelist from it (the panel IS the contract).
            panel = {**panel, **values}
        universe_mask = build_universe_mask(config)
        baseline = load_baseline_predictions(config)
        engine = GPEngine(config.gp, config.fitness)
        pool = engine.run(panel, fwd, universe_mask=universe_mask,
                          baseline=baseline, periods=periods)

        if fingerprints is not None and (
            pit_binding_fingerprints(config.data) != fingerprints
        ):
            # Re-verified AFTER mining, before ANY artifact is persisted:
            # a refresh mid-run means the pool was mined on bytes that no
            # longer exist, so recording either identity would be a lie.
            raise RuntimeError(
                "PIT inputs changed while mining was running — the bundle "
                "or delisted registry was refreshed mid-run, so the mined "
                "pool no longer corresponds to any on-disk data identity. "
                "Re-run mining against stable inputs."
            )
        if fundamental_fingerprints is not None and (
            fundamental_binding_fingerprints(config.data)
            != fundamental_fingerprints
        ):
            raise RuntimeError(
                "fundamental inputs changed while mining was running — "
                "the financial-statement store or the trading calendar "
                "was refreshed mid-run, so the mined pool no longer "
                "corresponds to any on-disk data identity. Re-run mining "
                "against stable inputs."
            )
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
        "data_definition_sha256": definition_sha,
        "gp": asdict(config.gp),
        "fitness": asdict(config.fitness),
    }
    if fingerprints is not None:
        # Bind the run to the CONTENT of its PIT inputs, not just their
        # paths (external finding on #415 r4) — promote re-verifies these.
        # These are the PRE-BUILD values, already re-verified above.
        config_dump.update(fingerprints)
    if fundamental_fingerprints is not None:
        config_dump.update(fundamental_fingerprints)
        # The factory's behavioral identity: a content digest over its
        # full output (values, evidence AND periods), computed by THIS
        # module — never self-reported. Promotion re-invokes whatever
        # factory it is handed on the exact recorded geometry and
        # refuses a digest mismatch, so swapping the callable while
        # every config value still matches is caught by behavior.
        config_dump["fundamental_output_sha256"] = fundamental_output_sha
        (run_dir / "fundamental_binding.json").write_text(
            json.dumps({
                "trade_dates": [str(d.date()) for d in fwd.index],
                "instruments": [str(c) for c in fwd.columns],
                "output_sha256": fundamental_output_sha,
            }, indent=2),
            encoding="utf-8",
        )
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

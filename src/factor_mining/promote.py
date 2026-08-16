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
from dataclasses import asdict, dataclass, field, replace
from dataclasses import fields as dataclass_fields
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .factor_pool import FactorPool
from .miner import (
    DataConfig,
    _build_fundamental_leg,
    build_panel_for_data,
    data_definition_sha256,
    fundamental_binding_fingerprints,
    normalize_yaml_dates,
)
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
    # The EFFECTIVE data definition the validation panel is built from:
    # the run's own snapshot, with at most ``end_date`` extended to
    # ``validation_end_date`` (the one governed deviation — see below).
    data: DataConfig
    # sha256 the MINER recorded for the run's data definition — verified
    # against the run snapshot at the production-writing boundary and
    # written into the promotion report for downstream verification.
    data_definition_sha256: str
    # Governed OOS window (codex P1 on #415): a PIT campaign's mining
    # panel deliberately ENDS at the IS cutoff (pv_incremental_v1 stops at
    # 2022-12-31 to keep OOS unseen), so a fully-bound panel could never
    # contain genuine OOS data — a 2023+ split yields zero OOS
    # observations, an earlier one "validates" on data the GP already saw.
    # The ONLY permitted deviation from the mined definition is extending
    # ``end_date`` forward, explicitly, to this value. None = no extension
    # (the synthetic path).
    validation_end_date: str | None = None
    # Pre-authorized extension baseline for a FUNDAMENTAL run (see
    # ``_verify_fundamental_extension_baseline``): the mined run only
    # ever covers the original window, so extending it needs an output
    # digest recorded by an independent trusted process BEFORE
    # promotion. None is valid only when the run has no fundamental leg
    # or the window is not extended.
    fundamental_baseline_path: Path | None = None


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


# The one tolerated schema gap in old run snapshots: the fundamental
# quartet's defaults are inert, so a pre-extension snapshot still
# describes the exact panel it mined (see ``_load_run_data_config``).
_FUNDAMENTAL_SCHEMA_KEYS = frozenset({
    "fundamental_store_root", "fundamental_calendar_path",
    "fundamental_fields", "financial_exclusions",
})


def _load_yaml_mapping(path: Path, what: str) -> dict:
    """Read ``path`` as a YAML MAPPING, refusing everything else loudly.

    A truncated file raises ``yaml.YAMLError`` and a top-level list makes
    the subsequent ``.get`` raise ``AttributeError`` — neither is caught
    by the CLI's ``PromotionError`` / ``FileNotFoundError`` branches, so a
    malformed run snapshot surfaced as a traceback instead of the
    controlled refusal this module promises (codex P2 on #415 r5). One
    guard, used by every YAML read in this module.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PromotionError(
            f"{what} {path} is not valid YAML ({exc}) — refusing to "
            "promote on an unreadable configuration."
        ) from exc
    if raw is None:
        return {}  # an empty document is the one legitimate empty case
    # ``or {}`` here would launder falsy non-mappings — ``[]``, ``false``,
    # ``0`` — into "no overrides" and promote on default criteria
    # (codex P2 on #415 r8); only YAML null means empty.
    if not isinstance(raw, dict):
        raise PromotionError(
            f"{what} {path} must be a YAML mapping, got "
            f"{type(raw).__name__} — refusing to guess its meaning."
        )
    return raw


def _check_pit_window(
    mined_end_date: str,
    validation_end: str | None,
    split: str | None,
) -> None:
    """The PIT OOS invariants — ONE implementation for both entry paths.

    ``_load_config`` enforces these for the CLI, and ``promote_run``
    re-enforces them at the production-writing boundary (codex P1 on #415
    r2): a programmatic caller constructing ``PromotionConfig`` directly
    must not be able to reach ``validate_pool`` with no extension, or with
    a split that grades GP-visible data as OOS.
    """
    if not split:
        raise PromotionError(
            "criteria.is_oos_split_date is required for a PIT-mode run — "
            "promotion refuses to infer an OOS boundary. Set it "
            "explicitly in the promotion config (criteria section)."
        )
    if not validation_end:
        raise PromotionError(
            "validation.end_date is required for a PIT-mode run: the "
            f"mining panel ends at {mined_end_date!r} (the IS cutoff), "
            "so without a governed extension the OOS segment would be "
            "empty — or, worse, carved out of data the GP already saw. "
            "Set validation.end_date beyond the mined end_date."
        )
    # Compare PARSED dates, not strings (codex P2 on #415 r4): a valid
    # but non-zero-padded "2022-9-30" orders lexically after "2022-12-31",
    # so string comparison would wave GP-visible dates through. Malformed
    # dates fail loud here instead of mis-ordering silently.
    def _parse(label: str, value: str) -> pd.Timestamp:
        try:
            ts = pd.Timestamp(str(value))
        except (ValueError, TypeError) as exc:
            raise PromotionError(
                f"{label} {value!r} is not a parseable date."
            ) from exc
        if ts.tzinfo is not None:
            # REFUSED outright, not normalized (codex P2 on #415 r9):
            # dropping the tz here only fixed THIS comparison — the
            # aware original stayed in the effective config, and
            # validate_pool's reparse would hit the panel's naive
            # DatetimeIndex with a TypeError. The window governs
            # wall-clock dates, and an aware timestamp's wall date is
            # genuinely ambiguous across zones — supply a plain date.
            raise PromotionError(
                f"{label} {value!r} is timezone-bearing — the governed "
                "window is defined on wall-clock dates and a tz-aware "
                "value would be reparsed downstream against a naive "
                "panel index. Supply a plain date (YYYY-MM-DD)."
            )
        return ts

    mined_ts = _parse("mined end_date", mined_end_date)
    validation_ts = _parse("validation.end_date", str(validation_end))
    split_ts = _parse("criteria.is_oos_split_date", str(split))
    if validation_ts <= mined_ts:
        raise PromotionError(
            f"validation.end_date {validation_end!r} must lie strictly "
            f"AFTER the mined end_date {mined_end_date!r} — the governed "
            "deviation is an OOS extension, never a rewrite."
        )
    # STRICTLY after the cutoff (codex P1 on #415 r4): the validator's OOS
    # is ``date >= split``, so a split ON the mined end_date would grade
    # that day — which the GP saw — as out-of-sample.
    if not (mined_ts < split_ts < validation_ts):
        raise PromotionError(
            f"criteria.is_oos_split_date {split!r} must lie within "
            f"(mined end_date {mined_end_date!r}, validation.end_date "
            f"{validation_end!r}) — OOS is date >= split, so a split at or "
            "before the mining cutoff grades GP-visible data as OOS, and "
            "one at/after the extension leaves no OOS observations."
        )


def _check_pit_embargo(
    trading_index: pd.DatetimeIndex,
    mined_end_date: str,
    split: str,
    horizon: int,
) -> None:
    """The split must clear the label-lookahead embargo, in TRADING days.

    ``forward_return`` labels date T with prices at T+1 .. T+horizon+1
    (``Ref(price, -horizon-1)/Ref(price, -1)`` — both price modes), so
    the labels of the last mined days consume prices horizon+1 trading
    days PAST the mining cutoff. A split merely after the calendar
    ``end_date`` can therefore still grade GP-consumed prices as OOS
    (codex P1 on #415 r11: with the H=1 campaign mined through
    2022-12-31, the 2022-12-30 label consumes the 2023-01-03 and
    2023-01-04 prices — a 2023-01-03 split is contaminated). Calendar
    arithmetic cannot see trading days; only the panel's own index can,
    so this check lives at the panel boundary in ``promote_run`` — the
    coarse calendar ordering in ``_check_pit_window`` stays as the
    early, panel-free screen.

    The first clean OOS day is the one strictly AFTER the last price a
    mining label consumed: position(last mined day) + horizon + 2.
    """
    mined_ts = pd.Timestamp(str(mined_end_date))
    split_ts = pd.Timestamp(str(split))
    last_mined_pos = int(trading_index.searchsorted(mined_ts, side="right")) - 1
    if last_mined_pos < 0:
        raise PromotionError(
            f"mined end_date {mined_end_date!r} lies before the panel's "
            "first trading day — the run snapshot and the panel disagree."
        )
    first_clean_pos = last_mined_pos + horizon + 2
    if first_clean_pos >= len(trading_index):
        raise PromotionError(
            f"the governed extension ends before the label-lookahead "
            f"embargo clears: mining labels consume prices through "
            f"{horizon + 1} trading day(s) past the mined end_date "
            f"{mined_end_date!r}, and the extended panel has no trading "
            "day beyond that. Extend validation.end_date further."
        )
    first_oos_pos = int(trading_index.searchsorted(split_ts, side="left"))
    if first_oos_pos < first_clean_pos:
        first_clean = trading_index[first_clean_pos].date().isoformat()
        raise PromotionError(
            f"criteria.is_oos_split_date {split!r} violates the "
            f"label-lookahead embargo: labels of the mined panel consume "
            f"prices through "
            f"{trading_index[last_mined_pos + horizon + 1].date().isoformat()} "
            f"(horizon {horizon} → {horizon + 1} trading days past the "
            f"cutoff), so OOS days before {first_clean} would be graded "
            "on prices the GP already consumed. Move the split to "
            f"{first_clean} or later."
        )


def _verify_pit_binding(run_dir: Path, run_data: DataConfig) -> None:
    """The PIT inputs' CONTENT must still be what the run was mined on.

    The data-definition digest covers config values (paths included), so an
    in-place refresh of the bundle or the registry between mining and
    promotion passes every config check while the panel bytes change
    underneath (codex P1 on #415 r4). The miner records content
    fingerprints at mining time; this recomputes them from the paths as
    they are NOW and refuses any drift — and refuses a PIT run that
    predates fingerprint recording (re-mine; mining is cheap, unverifiable
    provenance is not).
    """
    from src.data.bundle_manifest import BundleManifestError  # noqa: PLC0415

    from .miner import pit_binding_fingerprints  # noqa: PLC0415

    raw = _load_yaml_mapping(run_dir / "config.yaml", "run snapshot")
    recorded = {
        key: raw.get(key)
        for key in ("pit_bundle_data_sha256", "delisted_registry_sha256")
    }
    if not all(recorded.values()):
        raise PromotionError(
            "run_dir config.yaml carries no PIT content fingerprints "
            "(pit_bundle_data_sha256 / delisted_registry_sha256) — the "
            "run predates content binding and its inputs cannot be "
            "verified; re-mine before promoting."
        )
    # BundleManifestError is a ValueError, not an OSError — the bundle
    # fingerprint wraps its filesystem failures in it (codex P2 on #415
    # r5); catching only OSError would let a missing calendar escape as a
    # traceback instead of a controlled refusal.
    try:
        current = pit_binding_fingerprints(run_data)
    except (OSError, BundleManifestError) as exc:
        raise PromotionError(
            f"cannot fingerprint the PIT inputs ({exc}) — the bundle or "
            "registry the run was mined on is not readable at its recorded "
            "path."
        ) from exc
    for key, recorded_value in recorded.items():
        if current[key] != recorded_value:
            raise PromotionError(
                f"{key} mismatch: recorded {recorded_value!r} at mining "
                f"time, but the path now yields {current[key]!r} — the "
                "bundle/registry was refreshed in place after mining, so "
                "the pool would be validated on data it was not mined on. "
                "Re-mine against the current inputs."
            )


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------



def _verify_fundamental_identity(
    run_dir: Path, run_data: DataConfig, factory,
) -> tuple[dict[str, str], str]:
    """Bind the injected factory to the run — by BEHAVIOR, not by claim.

    Two checks, in order:

    1. **Content fingerprints (take #1).** The financial-statement store
       and calendar as they exist NOW must match what mining recorded —
       an in-place refresh passes every config check while the panel
       bytes move (same class as the PIT binding). This read doubles as
       the promotion-side "before reading the panel" stability anchor;
       ``promote_run`` re-takes the fingerprints after evaluation and
       refuses drift before touching production.
    2. **Original-window behavioral recompute.** The factory callable is
       the one input the persisted contract cannot carry, and a swapped
       callable can agree with every recorded config value. Mining
       recorded a digest of the factory's FULL output (values, evidence,
       periods) on an exact geometry; the factory promotion was handed
       is re-invoked HERE on that recorded geometry and must reproduce
       the digest bit-for-bit. Identity is what the factory DOES — a
       name/version pair would let two different factories claim each
       other's runs.

    Returns ``(current_fingerprints, recorded_output_sha)``. Refuses a
    run that predates factory-identity recording (re-mine; an
    unverifiable seam is worse than a re-run).
    """
    from .miner import _verify_fundamental_triple  # noqa: PLC0415
    from .panel_digest import fundamental_output_sha256  # noqa: PLC0415

    raw = _load_yaml_mapping(run_dir / "config.yaml", "run snapshot")
    recorded_fp = {
        key: raw.get(key)
        for key in ("fundamental_store_sha256", "fundamental_calendar_sha256")
    }
    recorded_output = raw.get("fundamental_output_sha256")
    binding_path = run_dir / "fundamental_binding.json"
    if (not all(recorded_fp.values()) or not recorded_output
            or not binding_path.is_file()):
        raise PromotionError(
            "run_dir declares a fundamental leg but carries no complete "
            "factory-identity record (fundamental_store_sha256 / "
            "fundamental_calendar_sha256 / fundamental_output_sha256 / "
            "fundamental_binding.json) — the run predates identity "
            "recording, so the panel builder that mined it cannot be "
            "verified; re-mine before promoting."
        )
    try:
        current_fp = fundamental_binding_fingerprints(run_data)
    except (OSError, ValueError) as exc:
        raise PromotionError(
            f"cannot fingerprint the fundamental inputs ({exc}) — the "
            "store or calendar the run was mined on is not readable at "
            "its recorded path."
        ) from exc
    for key, recorded_value in recorded_fp.items():
        if current_fp[key] != recorded_value:
            raise PromotionError(
                f"{key} mismatch: recorded {recorded_value!r} at mining "
                f"time, but the path now yields {current_fp[key]!r} — the "
                "fundamental store/calendar was refreshed in place after "
                "mining, so the pool would be validated on data it was "
                "not mined on. Re-mine against the current inputs."
            )
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        trade_dates = pd.DatetimeIndex(
            [pd.Timestamp(d) for d in binding["trade_dates"]])
        instruments = [str(c) for c in binding["instruments"]]
        binding_output = binding["output_sha256"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PromotionError(
            f"run_dir fundamental_binding.json is unreadable or malformed "
            f"({exc}) — the recorded geometry is the ground the identity "
            "recompute stands on; re-mine before promoting."
        ) from exc
    if binding_output != recorded_output:
        raise PromotionError(
            "fundamental_binding.json and config.yaml disagree on the "
            "recorded fundamental_output_sha256 — the run's identity "
            "record was edited after mining; re-mine before promoting."
        )
    skeleton = pd.DataFrame(np.nan, index=trade_dates, columns=instruments)
    triple = factory(run_data, trade_dates, instruments)
    try:
        values, evidence, periods = _verify_fundamental_triple(
            triple, skeleton, {})
    except RuntimeError as exc:
        raise PromotionError(str(exc)) from exc
    got = fundamental_output_sha256(values, evidence, periods)
    if got != recorded_output:
        raise PromotionError(
            "the injected fundamental panel factory does not reproduce "
            "the run's recorded behavioral identity on the mined window "
            f"(recorded {recorded_output!r}, recomputed {got!r}) — a "
            "different builder (or a drifted implementation) would "
            "adjudicate the pool on a panel it was never mined on; "
            "refusing."
        )
    return current_fp, recorded_output


def _verify_fundamental_extension_baseline(
    config: PromotionConfig, fwd, effective_output_sha: str,
) -> str:
    """An extended window needs a baseline promotion could not self-issue.

    Mining only ever covers the original window, so its recorded digest
    knows nothing about the extension dates — the exact dates the OOS
    adjudication is FOR. And hashing the promoted factory's own
    effective-window output would let a swapped callable certify itself.
    The only admissible baseline is one recorded by an independent
    trusted process (the campaign's ``record-baseline`` step, which runs
    the canonical bridge directly) and written to disk BEFORE promotion;
    this verifier binds it to the exact effective definition and
    geometry, then requires the promoted factory to reproduce its output
    digest on the effective window bit-for-bit. Returns the verified
    baseline digest for the report.
    """
    path = config.fundamental_baseline_path
    if path is None:
        raise PromotionError(
            "extended-window promotion of a fundamental run requires a "
            "pre-authorized extension baseline "
            "(fundamental_baseline_path): the mining run only covers the "
            "original window, so its recorded digest cannot certify "
            "dates it never saw — and a baseline produced by the "
            "promoted factory itself would be self-certification. Record "
            "one with the campaign script's record-baseline step, then "
            "pass its path."
        )
    if not Path(path).is_file():
        raise PromotionError(
            f"fundamental extension baseline {path} does not exist — "
            "record it (campaign record-baseline step) before promoting."
        )
    try:
        baseline = json.loads(Path(path).read_text(encoding="utf-8"))
        purpose = baseline["purpose"]
        base_sha = baseline["data_definition_sha256"]
        base_dates = [str(d) for d in baseline["trade_dates"]]
        base_instruments = [str(c) for c in baseline["instruments"]]
        base_output = baseline["output_sha256"]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PromotionError(
            f"fundamental extension baseline {path} is unreadable or "
            f"malformed ({exc}); refusing."
        ) from exc
    if purpose != "fundamental-extension-baseline":
        raise PromotionError(
            f"fundamental extension baseline {path} declares purpose "
            f"{purpose!r}, not 'fundamental-extension-baseline' — a "
            "repurposed artifact must not key the extension; refusing."
        )
    effective_sha = data_definition_sha256(config.data)
    if base_sha != effective_sha:
        raise PromotionError(
            "fundamental extension baseline was recorded for a different "
            f"effective data definition (baseline {base_sha!r}, this "
            f"promotion {effective_sha!r}) — the baseline binds the "
            "store, fields, exclusions AND the extended window; record "
            "one for exactly this promotion."
        )
    got_dates = [str(d.date()) for d in fwd.index]
    got_instruments = [str(c) for c in fwd.columns]
    if base_dates != got_dates or base_instruments != got_instruments:
        raise PromotionError(
            "fundamental extension baseline geometry does not match the "
            "effective validation panel (trade dates x instruments) — "
            "the baseline must be recorded on exactly the panel this "
            "promotion evaluates; re-record it."
        )
    if base_output != effective_output_sha:
        raise PromotionError(
            "the promoted factory's effective-window output digest does "
            f"not match the pre-authorized baseline (baseline "
            f"{base_output!r}, factory {effective_output_sha!r}) — on "
            "the extension dates the injected builder diverges from the "
            "canonical bridge; refusing."
        )
    return base_output


def _refuse_fundamental_pool_in_production(
    survivor_pool: FactorPool, target_dir: Path,
) -> None:
    """Refuse to write a pool containing FINANCIAL-STATEMENT terminals.

    The production materialization path evaluates a promoted pool against a
    qlib panel with NO report-period provenance. A fundamental pool arriving
    there would either fail on unresolvable financial terminals or — if a panel
    were injected — materialize WITHOUT the terminal-level alignment mask that
    decided its promotion. A factor adjudicated under one definition and served
    under another is the same defect class as adjudicating on a different
    metric.

    Wiring that consumer is a separate change. Until it lands this boundary is
    an EXECUTABLE refusal rather than a documented caveat — a note in a design
    doc stops nobody.
    """
    from .expression import feature_terminals  # noqa: PLC0415
    from .grammar import FeatureRegistry  # noqa: PLC0415

    # The blocklist is REGISTRY MINUS DEFAULT, not a hand-picked group list:
    # every registered terminal outside the default set is by definition one
    # the qlib production panel does not carry ($X__prior included), and a
    # hand-picked list would silently miss the next opt-in group too.
    financial = set(FeatureRegistry.ALL_REGISTERED) - set(FeatureRegistry.V1)
    offenders: dict[str, list[str]] = {}
    for entry in survivor_pool.all_entries():
        hit = sorted(feature_terminals(entry.expr) & financial)
        if hit:
            offenders[entry.expr.to_qlib_string()] = hit
    if not offenders:
        return
    listed = "\n".join(f"    {expr}  -> {terms}"
                        for expr, terms in sorted(offenders.items()))
    raise PromotionError(
        f"refusing to write a FUNDAMENTAL pool into production ({target_dir}): "
        f"{len(offenders)} survivor(s) reference financial-statement "
        f"terminals:\n{listed}\n"
        "The production materialization path (src/data/mined_factor_handler.py) "
        "evaluates without report-period provenance, so these factors would be "
        "served WITHOUT the cross-endpoint alignment mask that decided their "
        "promotion — adjudicated under one definition, served under another. "
        "Land the fundamental-panel wiring for that consumer first; nothing was "
        "written and the version label is untouched."
    )

def promote_run(
    config: PromotionConfig, *, dry_run: bool = False,
    fundamental_panel_factory=None,
) -> PromotionReport:
    """Validate the run and (unless ``dry_run``) write the production dir.

    ``fundamental_panel_factory`` is the promotion side of the miner's
    injection seam. A run mined WITH a fundamental leg must be promoted
    with a factory (and that factory must reproduce the run's recorded
    behavioral identity — see ``_verify_fundamental_identity``); a run
    mined WITHOUT one must not be handed a factory. Swapping the builder
    moves the panel exactly like swapping the data, so both directions
    are refused with the same severity as a data-definition mismatch.
    """
    if not config.run_dir.exists():
        raise PromotionError(f"run_dir does not exist: {config.run_dir!r}")
    target_dir = config.production_dir / config.version
    if target_dir.exists() and not dry_run:
        raise PromotionError(
            f"production version directory already exists: {target_dir!r}. "
            "Choose a new version label or remove the existing one manually."
        )

    # Verify the run binding HERE, at the production-writing boundary — not
    # only in _load_config (codex P1 on #415): a programmatic caller that
    # constructs PromotionConfig directly could otherwise validate on a
    # different panel while the report still claims data_source =
    # run_dir/config.yaml. The run snapshot is re-loaded and must match the
    # caller's config verbatim, hash included, so the report's claim is
    # true by construction for every entry path.
    run_data, run_sha = _load_run_data_config(config.run_dir)
    fundamental_declared = bool(run_data.fundamental_store_root)
    if fundamental_declared and fundamental_panel_factory is None:
        raise PromotionError(
            "this run was mined WITH a fundamental panel leg "
            f"(fundamental_store_root="
            f"{run_data.fundamental_store_root!r}) but no "
            "fundamental_panel_factory was injected — validating without "
            "the leg would adjudicate the pool on a panel it was never "
            "mined on. Drive the promotion through the campaign script "
            "(scripts/research/fundamental_gp_campaign.py), which "
            "injects and identity-binds the factory; the bare CLI cannot "
            "promote a fundamental run."
        )
    if fundamental_panel_factory is not None and not fundamental_declared:
        raise PromotionError(
            "a fundamental_panel_factory was injected but the run "
            "records no fundamental leg — the factory would add "
            "terminals the pool was never mined on. Promote "
            "price-volume runs without a factory."
        )
    expected = run_data
    if run_data.mode == "pit":
        # Re-enforce the PIT OOS invariants HERE (codex P1 on #415 r2) —
        # _load_config alone cannot bind a programmatic caller, and a pit
        # config with no extension (or a split outside the extension)
        # would otherwise reach validate_pool grading GP-visible data.
        _check_pit_window(
            run_data.end_date, config.validation_end_date,
            config.criteria.is_oos_split_date,
        )
        _verify_pit_binding(config.run_dir, run_data)
    elif config.validation_end_date is not None:
        # _load_config refuses this for the CLI; the boundary must refuse
        # it for programmatic callers too (codex P2 on #415 r4) — the
        # synthetic panel ignores calendar dates, so an "extension" here
        # would decorate the report without affecting the validated panel.
        raise PromotionError(
            "validation_end_date applies to PIT-mode runs only — the "
            "synthetic panel ignores calendar dates, so an extension "
            "would be a no-op pretending to be governance."
        )
    if config.validation_end_date is not None:
        # Ordering was already enforced — PARSED — by _check_pit_window
        # above (validation_end_date is refused outright for non-PIT
        # runs). A second lexical comparison here contradicted it on
        # valid unpadded dates ("2022-9-30" orders after "2022-12-31");
        # one implementation, no duplicate (codex P2 on #415 r5).
        expected = replace(run_data, end_date=config.validation_end_date)
    if config.data != expected or config.data_definition_sha256 != run_sha:
        raise PromotionError(
            "PromotionConfig.data does not match the run's own resolved "
            f"config.yaml (caller sha {config.data_definition_sha256!r}, "
            f"run sha {run_sha!r}) — promotion validates on exactly the "
            "panel the factors were mined on (plus at most the governed "
            "validation_end_date extension). Build the config via "
            "promote._load_config, or pass the run snapshot verbatim."
        )

    fundamental_fp_entry: dict[str, str] | None = None
    recorded_output_sha: str | None = None
    if fundamental_declared:
        # Take #1 of the promotion-side stability pair happens inside
        # (before anything reads the panel), together with the
        # original-window behavioral recompute of the injected factory.
        fundamental_fp_entry, recorded_output_sha = (
            _verify_fundamental_identity(
                config.run_dir, run_data, fundamental_panel_factory))

    pool = FactorPool.load(config.run_dir)
    try:
        panel, fwd = build_panel_for_data(config.data)
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc

    periods = None
    effective_output_sha: str | None = None
    verified_baseline_sha: str | None = None
    if fundamental_declared:
        try:
            values, _evidence, periods, effective_output_sha = (
                _build_fundamental_leg(
                    config.data, fundamental_panel_factory, fwd, panel))
        except RuntimeError as exc:
            raise PromotionError(str(exc)) from exc
        panel = {**panel, **values}
        if config.validation_end_date is None:
            # Same window as mining: a deterministic factory must
            # reproduce the recorded digest verbatim — no independent
            # baseline exists or is needed.
            if effective_output_sha != recorded_output_sha:
                raise PromotionError(
                    "the factory's output on the validation panel does "
                    "not match the digest recorded at mining time "
                    f"(recorded {recorded_output_sha!r}, got "
                    f"{effective_output_sha!r}) despite an unextended "
                    "window — the builder is nondeterministic or "
                    "drifted; refusing."
                )
        else:
            verified_baseline_sha = _verify_fundamental_extension_baseline(
                config, fwd, effective_output_sha)

    if run_data.mode == "pit":
        _check_pit_embargo(
            fwd.index, run_data.end_date,
            config.criteria.is_oos_split_date, run_data.forward_horizon,
        )

    results = validate_pool(pool, panel, fwd, config.criteria,
                            periods=periods)
    n_passed_individual = sum(1 for r in results if r.passes)

    filtered = filter_correlated(results, panel, config.criteria, pool,
                                 periods=periods)
    survivors = [r for r in filtered if r.passes]
    n_kept = len(survivors)

    output_dir: Path | None = None
    survivor_pool = FactorPool()
    entries_by_hash = {e.expr_hash: e for e in pool.all_entries()}
    for res in survivors:
        entry = entries_by_hash.get(res.expr_hash)
        if entry is not None:
            survivor_pool.add(entry)
    if fundamental_fp_entry is not None:
        # Take #2 of the stability pair: the promotion evaluated its own
        # panel over a window nobody else was watching — a store or
        # calendar refresh DURING that window means the adjudication
        # numbers came from mixed or vanished bytes while every recorded
        # identity still matches. Refused here, before the production
        # boundary is touched (same edge as "refusal before mkdir").
        try:
            fundamental_fp_now = fundamental_binding_fingerprints(
                config.data)
        except (OSError, ValueError) as exc:
            raise PromotionError(
                f"cannot re-fingerprint the fundamental inputs ({exc}) "
                "after evaluation — refusing before touching production."
            ) from exc
        if fundamental_fp_now != fundamental_fp_entry:
            raise PromotionError(
                "fundamental inputs changed while promotion was "
                "evaluating — the store or calendar was refreshed "
                "mid-promotion, so the adjudication numbers do not "
                "correspond to any on-disk data identity. Nothing was "
                "written; re-run against stable inputs."
            )

    # POLICY runs in BOTH modes; only the filesystem mutation is conditional.
    # A dry run is the operator's preview — skipping the refusal there would
    # report "would be kept" for a pool whose identical non-dry invocation is
    # guaranteed to fail, i.e. a preview that lies about production
    # eligibility.
    #
    # And in the real run: BEFORE mkdir, not merely before save — the directory
    # is created first, so a refusal placed after it would leave an empty
    # production version directory behind and the next attempt would fail on
    # "already exists". A REFUSED promotion must not mutate production at all,
    # nor consume the version label.
    _refuse_fundamental_pool_in_production(survivor_pool, target_dir)
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
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
            # Two SELF-CONSISTENT provenance pairs (codex P2 on #415 r2 —
            # one digest next to a different definition defeats downstream
            # recomputation). Each sha256 verifies over the dict beside it,
            # via the same miner.data_definition_sha256 canonicalization:
            #   * mined_data / mined_data_sha256 — the run's own snapshot
            #     (read from run_dir/config.yaml, recorded at mining time);
            #   * data / data_sha256 — the EFFECTIVE definition the
            #     validation panel was built from: identical to mined_data
            #     except for the governed validation_end_date extension.
            "mined_data": asdict(run_data),
            "mined_data_sha256": config.data_definition_sha256,
            "mined_data_source": "run_dir/config.yaml",
            "data": asdict(config.data),
            "data_sha256": data_definition_sha256(config.data),
            "validation_end_date": config.validation_end_date,
            **(
                {
                    "fundamental_output_sha256_mined": recorded_output_sha,
                    "fundamental_output_sha256_effective":
                        effective_output_sha,
                    "fundamental_extension_baseline_sha256":
                        verified_baseline_sha,
                    "fundamental_extension_baseline_path": (
                        str(config.fundamental_baseline_path)
                        if config.fundamental_baseline_path is not None
                        else None
                    ),
                    **fundamental_fp_entry,
                }
                if fundamental_declared and fundamental_fp_entry is not None
                else {}
            ),
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

    The digest is not merely recomputed: the miner RECORDS it at mining
    time (``data_definition_sha256`` in the same file), and a mismatch
    between the recorded value and the recomputation over today's data
    section means the snapshot was edited after mining — refused (codex P1
    on #415; recomputing alone would happily hash the edited values).
    """
    config_path = run_dir / "config.yaml"
    if not config_path.exists():
        raise PromotionError(
            f"run_dir has no resolved config.yaml: {config_path!r}. The "
            "miner writes one into every run directory; a run that cannot "
            "prove its data definition cannot be promoted."
        )
    raw = _load_yaml_mapping(config_path, "run snapshot")
    data_section = raw.get("data")
    if not isinstance(data_section, dict) or not data_section:
        raise PromotionError(
            f"run_dir config.yaml has no data section: {config_path!r} — "
            "cannot bind the promotion to the mined data definition."
        )
    try:
        # Unquoted YAML dates arrive as datetime.date — normalized here
        # exactly like the miner's own loader (codex P1 on #415 r6), so
        # digest recomputation and dataclass equality both see strings.
        data = DataConfig(**normalize_yaml_dates(data_section))
    except TypeError as exc:
        raise PromotionError(
            f"run_dir config.yaml data section does not parse as the "
            f"miner's DataConfig ({exc}) — the run predates or diverges "
            "from the current data schema; re-mine before promoting."
        ) from exc

    recorded = raw.get("data_definition_sha256")
    if not recorded:
        raise PromotionError(
            f"run_dir config.yaml carries no data_definition_sha256: "
            f"{config_path!r} — the run predates mining-time digest "
            "recording and its data definition cannot be verified; re-mine "
            "before promoting."
        )
    recomputed = data_definition_sha256(data)
    if recorded != recomputed:
        # Schema migration, one tolerated shape: a snapshot dumped by the
        # pre-fundamental miner recorded its digest over the field set
        # that existed THEN — today's full-payload recompute cannot
        # match it even though nothing was edited. When the snapshot's
        # missing keys are EXACTLY within the fundamental quartet (whose
        # defaults are inert, so the parsed config still describes the
        # very panel the run mined), reproduce the original
        # canonicalization by restricting to the snapshot's own keys.
        # Any other gap fails both recomputes and is refused below — a
        # run predating a behavior-carrying schema change cannot prove
        # what it mined.
        section_keys = frozenset(data_section)
        missing = (
            {f.name for f in dataclass_fields(DataConfig)} - section_keys)
        if missing and missing <= _FUNDAMENTAL_SCHEMA_KEYS:
            recomputed = data_definition_sha256(
                data, restrict_to=section_keys)
    if recorded != recomputed:
        raise PromotionError(
            "run_dir config.yaml data section does not match the digest "
            f"recorded at mining time (recorded {recorded!r}, recomputed "
            f"{recomputed!r}) — either the snapshot was edited after "
            "mining, or the run predates the current data schema; the "
            "pool cannot be re-bound to a panel it was not mined on. "
            "Re-mine before promoting."
        )
    return data, recorded


def _load_config(
    config_path: Path | None,
    run_dir: Path,
    production_dir: Path,
    version: str,
    fundamental_baseline_path: Path | None = None,
) -> PromotionConfig:
    if config_path is not None and config_path.exists():
        raw = _load_yaml_mapping(config_path, "promotion config")
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
    def _mapping_section(name: str) -> dict:
        # ``validation: typo`` / ``criteria: 42`` would make ``dict(...)``
        # raise ValueError/TypeError past main()'s PromotionError branch
        # (codex P2 on #415 r7) — same refusal shape as the file-level
        # guard, one level down.
        section = raw.get(name)
        if section is None:
            return {}
        if not isinstance(section, dict):
            raise PromotionError(
                f"promotion config section {name!r} must be a YAML "
                f"mapping, got {type(section).__name__} — refusing to "
                "guess its meaning."
            )
        return dict(section)

    crit_kwargs = normalize_yaml_dates(_mapping_section("criteria"))
    validation_raw = normalize_yaml_dates(_mapping_section("validation"))
    unknown = set(validation_raw) - {"end_date"}
    if unknown:
        raise PromotionError(
            f"unknown validation key(s) {sorted(unknown)} — the governed "
            "deviation is validation.end_date only."
        )
    def _date_override(section: dict, key: str, where: str) -> str | None:
        # Truthiness would let an explicitly malformed override — false,
        # 0, [], {} — silently fall back to the default behavior (codex
        # P2 on #415 r10). A PRESENT key must carry a date string;
        # explicit null means "as if absent".
        value = section.get(key)
        if key in section and value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise PromotionError(
                f"{where}.{key} {value!r} is not a usable date — an "
                "explicit override must be a date string; use null or "
                "omit the key for the default behavior."
            )
        return value

    validation_end = _date_override(validation_raw, "end_date", "validation")
    _date_override(crit_kwargs, "is_oos_split_date", "criteria")
    if ("is_oos_split_date" in crit_kwargs
            and crit_kwargs["is_oos_split_date"] is None):
        # An explicit null split means "absent" — including for the
        # synthetic auto-split below, which keys off key presence.
        del crit_kwargs["is_oos_split_date"]

    if data.mode == "pit":
        # A PIT campaign's mining panel deliberately ENDS at the IS cutoff
        # (codex P1 on #415): genuine OOS only exists BEYOND it, so the
        # extension and the split are both experiment-design choices the
        # operator must state — and the split must land inside the
        # extension, so OOS is entirely data the GP never saw.
        _check_pit_window(
            data.end_date, validation_end,
            crit_kwargs.get("is_oos_split_date"),
        )
        data = replace(data, end_date=str(validation_end))
    else:
        if validation_end:
            raise PromotionError(
                "validation.end_date applies to PIT-mode runs only — the "
                "synthetic panel is generated from synthetic_n_dates and "
                "ignores calendar dates, so an extension would be a no-op "
                "pretending to be governance."
            )
        if "is_oos_split_date" not in crit_kwargs:
            # Synthetic smoke path: 80/20 split over the synthetic range.
            n = data.synthetic_n_dates
            split_idx = int(0.8 * n)
            dates = pd.date_range("2024-01-01", periods=n, freq="D")
            crit_kwargs["is_oos_split_date"] = (
                dates[split_idx].strftime("%Y-%m-%d"))
    criteria = ValidationCriteria(**crit_kwargs)
    return PromotionConfig(
        run_dir=run_dir,
        production_dir=production_dir,
        version=version,
        criteria=criteria,
        data=data,
        data_definition_sha256=data_sha,
        validation_end_date=str(validation_end) if validation_end else None,
        fundamental_baseline_path=fundamental_baseline_path,
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

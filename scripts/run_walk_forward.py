"""Walk-forward CLI runner.

Usage:
    python scripts/run_walk_forward.py [config_walk.yaml]
                                        [--resume-from-fold N | --no-resume]

Reads a YAML mapping into :class:`WalkForwardConfig` and runs
:meth:`WalkForwardEngine.run`. Mirrors :mod:`main.py` for the single-fold
pipeline; kept as a separate script because the walk-forward engine
has its own config dataclass with different fields.

When the YAML selects ``feature_handler: "MinedFactor"``, this script
also binds the MinedFactor handler (per Phase 5's lazy-bind contract
in ``v2-feature-handler-registry``) before the engine runs. The
required top-level YAML keys for that path are documented in
``docs/factor_mining/user_guide.md`` and listed below in
``_MINED_FACTOR_YAML_KEYS``.

Resume flags (per ``openspec/changes/add-walk-forward-fold-resume``):

* ``--resume-from-fold N``  Re-run fold N and beyond; reuse manifests
  for folds 0..N-1 if they match the current config.
* ``--no-resume``           Ignore any existing manifests; re-run every
  fold. Output artifacts overwritten in place.
* (default)                 AUTO — load any matching manifest and
  re-run the rest. Same behaviour as legacy on a fresh output_dir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Allow ``python scripts/run_walk_forward.py`` from the repo root —
# ensure the project root is on sys.path before importing src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402
from src.core.canonical_backtest_contract import (  # noqa: E402
    stamp_tax_schedule_migration_snippet,
)
from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.core.qlib_runtime import (  # noqa: E402
    QlibRuntimeConfig,
    init_qlib_canonical,
    provider_uri_guard_message,
)
from src.core.walk_forward import (  # noqa: E402
    ResumeMode,
    WalkForwardConfig,
    WalkForwardEngine,
)
from src.data.bundle_manifest import (  # noqa: E402
    validate_test_end_against_bundle,
)
from src.data.mined_factor_handler import (  # noqa: E402
    MinedFactorBundle,
    register_mined_factor_handler,
)

_logger = get_logger(__name__)


# Top-level YAML keys consumed by this script's MinedFactor wiring.
# These are allowed in any walk-forward YAML (so an operator template
# can prefill them) but are only *required* when feature_handler is
# "MinedFactor". See docs/factor_mining/user_guide.md and the spec
# v2-factor-mining-walk-forward.
_MINED_FACTOR_YAML_KEYS: tuple[str, ...] = (
    "mined_factor_pool_dir",
    "mined_factor_delisted_registry_path",
    "mined_factor_pit_provider_uri",
    "mined_factor_universe_name_override",
)


def _load_config(
    path: str, raw: dict[str, Any] | None = None,
) -> tuple[WalkForwardConfig, QlibRuntimeConfig]:
    """Load walk-forward + qlib runtime config from a YAML mapping.

    The YAML may carry a top-level ``provider_uri`` and ``region``
    used to initialise qlib; the four ``mined_factor_*`` keys
    (consumed separately by ``_maybe_build_mined_factor_bundle``);
    everything else is funnelled into :class:`WalkForwardConfig`.
    Unknown keys raise a hard error (mirrors ``main.py``'s behaviour).

    ``raw`` may be a pre-parsed YAML mapping (the CLI entry points parse
    it once for their own logging and pass it back in) so the inheritance
    loader does not run twice; ``None`` parses ``path`` here.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if raw is None:
        raw = load_yaml_with_inheritance(config_path)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}"
        )

    # Legacy ``stamp_tax_bps`` scalar was replaced by
    # ``stamp_tax_schedule`` (audit P0-4 / add-stamp-tax-schedule).
    # Surface a precise migration error BEFORE the generic
    # unknown-key check so the operator sees the fix snippet.
    if "stamp_tax_bps" in raw:
        raise ValueError(
            f"Config {config_path} uses the legacy scalar key "
            "``stamp_tax_bps``. CN A-share stamp tax was halved on "
            "2023-08-28 (10 bps → 5 bps); walk-forward windows that "
            "span the reform must carry a TIME-ORDERED schedule, not "
            "a single scalar. Replace the line with:\n\n"
            f"{stamp_tax_schedule_migration_snippet()}"
            "\nOr omit the key entirely (the default canonical CN "
            "schedule is applied automatically). See "
            "openspec/changes/add-stamp-tax-schedule for the design."
        )

    # Stage-8 Gate-3 prereg binding key (codex #352 r11+r12): the frozen
    # decision-level presets bind the registered candidate they evaluate via
    # ``gate3_candidate`` (checked by scripts/research/gate3_prereg_gate.py).
    # Until the Gate-4B augmentation wiring actually CONSUMES the key, this
    # runner must refuse such configs outright — silently dropping the key
    # would execute the plain Alpha158 parent config and produce metrics
    # masquerading as a candidate-specific run under a GATE ACCEPT.
    if "gate3_candidate" in raw:
        raise ValueError(
            f"Config {config_path} binds gate3_candidate="
            f"{raw['gate3_candidate']!r}, but the Gate-4B augmentation "
            "wiring is not implemented yet — running this config today "
            "would train/backtest plain Alpha158 while claiming a "
            "candidate-specific run. Refusing (fail-loud guard, codex "
            "#352 r12); the Gate-4B wiring must replace this guard by "
            "actually consuming and honouring the key."
        )

    valid_fields = {f.name for f in WalkForwardConfig.__dataclass_fields__.values()}
    qlib_keys = {"provider_uri", "region"}
    mined_factor_keys = set(_MINED_FACTOR_YAML_KEYS)
    unknown = sorted(set(raw) - valid_fields - qlib_keys - mined_factor_keys)
    if unknown:
        # Reject unknown keys hard. Previously we logged a WARNING and
        # silently dropped them, which masked typos like ``top_k`` /
        # ``ensemble_window_size`` etc. — the run continued with default
        # values, producing official metrics that bore no relation to
        # the YAML the operator thought they had set.
        raise ValueError(
            f"Unknown config keys in {config_path}: {unknown}. "
            f"Valid WalkForwardConfig fields: {sorted(valid_fields)}; "
            f"plus qlib runtime keys: {sorted(qlib_keys)}; "
            f"plus mined-factor keys: {sorted(mined_factor_keys)}. "
            "Refusing to run with potentially-typo'd keys."
        )

    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    wf_config = WalkForwardConfig(**filtered)
    provider_uri = raw.get("provider_uri")
    if not str(provider_uri or "").strip():
        raise ValueError(
            f"Config file {config_path} must set provider_uri explicitly. "
            "Walk-forward official metrics cannot use a machine-local "
            "fallback data bundle."
        )
    qlib_cfg = QlibRuntimeConfig(
        provider_uri=str(provider_uri),
        region=raw.get("region", "cn"),
        data_adjust_mode=wf_config.adjust_mode,
    )
    return wf_config, qlib_cfg


def _maybe_build_mined_factor_bundle(
    raw: dict[str, Any],
    wf_config: WalkForwardConfig,
    provider_uri: str,
) -> MinedFactorBundle | None:
    """Extract a ``MinedFactorBundle`` from the raw YAML when the
    handler is ``"MinedFactor"``; else return ``None``.

    Raises ``ValueError`` when ``feature_handler == "MinedFactor"``
    and one of the two required keys (``mined_factor_pool_dir``,
    ``mined_factor_delisted_registry_path``) is missing or empty.
    Logs a WARNING when ``mined_factor_pit_provider_uri`` is set to a
    value distinct from the top-level ``provider_uri``.
    """
    if wf_config.feature_handler != "MinedFactor":
        return None
    pool_dir = str(raw.get("mined_factor_pool_dir") or "").strip()
    registry_path = str(raw.get("mined_factor_delisted_registry_path") or "").strip()
    if not pool_dir:
        raise ValueError(
            "feature_handler='MinedFactor' requires the YAML to set "
            "mined_factor_pool_dir to the directory of a promoted factor "
            "pool (e.g. research/mined_factors/production/v1). See "
            "docs/factor_mining/user_guide.md for the bind workflow."
        )
    if not registry_path:
        raise ValueError(
            "feature_handler='MinedFactor' requires the YAML to set "
            "mined_factor_delisted_registry_path to the PIT delisted "
            "registry parquet. See docs/factor_mining/user_guide.md."
        )
    pit_uri_raw = str(raw.get("mined_factor_pit_provider_uri") or "").strip()
    pit_uri = pit_uri_raw or provider_uri
    if pit_uri_raw and pit_uri_raw != provider_uri:
        _logger.warning(
            "mined_factor_pit_provider_uri (%s) differs from the walk-forward "
            "provider_uri (%s). The MinedFactor handler will re-evaluate "
            "factors on a different PIT vintage than the qlib runtime uses "
            "for training data. This is legitimate for cross-vintage "
            "comparison but is usually an operator mistake.",
            pit_uri_raw, provider_uri,
        )
    universe_override = raw.get("mined_factor_universe_name_override")
    universe_override_norm = (
        str(universe_override).strip() if universe_override else None
    )
    return MinedFactorBundle(
        pool_dir=Path(pool_dir),
        pit_provider_uri=pit_uri,
        delisted_registry_path=registry_path,
        universe_name_override=universe_override_norm or None,
    )


def _parse_cli(argv: list[str]) -> tuple[str, ResumeMode, str | None]:
    """Parse CLI flags. Returns ``(config_path, resume_mode, dataset_cache_dir_override)``.

    Preserves the legacy positional form ``python
    scripts/run_walk_forward.py [config.yaml]`` — the config path is a
    positional with a sensible default. Resume flags are mutually
    exclusive; passing both raises ``SystemExit(2)`` via argparse.

    ``--dataset-cache-dir DIR`` overrides whatever the YAML sets for
    ``dataset_cache_dir`` (and pre-empts the ``QLIB_DATASET_CACHE_DIR``
    env var fallback). Pass an empty string to explicitly disable the
    cache even if the YAML asks for it.
    """
    parser = argparse.ArgumentParser(
        prog="run_walk_forward.py",
        description="Run a walk-forward backtest from a YAML config.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config_walk.yaml",
        help="Path to walk-forward YAML (default: config_walk.yaml)",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume-from-fold",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Re-run fold N and beyond; reuse manifests for folds 0..N-1 "
            "if they match the current config."
        ),
    )
    resume_group.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help=(
            "Ignore existing manifests; re-run every fold. "
            "Output artifacts are overwritten in place."
        ),
    )
    parser.add_argument(
        "--dataset-cache-dir",
        type=str,
        default=None,
        metavar="DIR",
        help=(
            "Override WalkForwardConfig.dataset_cache_dir. The feature-"
            "dataset cache (see openspec/changes/add-feature-dataset-cache) "
            "is consulted by FeatureDatasetBuilder.build() before "
            "instantiating Alpha158 / MinedFactor — a cache hit skips "
            "30-90s of handler init + 3x prepare() per fold. Pass an "
            "empty string to disable the cache even if the YAML enables it."
        ),
    )
    ns = parser.parse_args(argv)
    if ns.no_resume:
        resume = ResumeMode.FORCE_RERUN
    elif ns.resume_from_fold is not None:
        if ns.resume_from_fold < 0:
            parser.error("--resume-from-fold N must be ≥ 0")
        resume = ResumeMode.from_fold(ns.resume_from_fold)
    else:
        resume = ResumeMode.AUTO
    return ns.config, resume, ns.dataset_cache_dir


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    config_file, resume_mode, ds_cache_override = _parse_cli(
        argv if argv is not None else sys.argv[1:],
    )
    _logger.info("Loading walk-forward config from %s", config_file)
    raw_yaml = load_yaml_with_inheritance(Path(config_file))
    wf_config, qlib_config = _load_config(config_file, raw=raw_yaml)

    # Fail loud on a missing / misconfigured bundle BEFORE qlib (or the mined
    # bundle build, or the staleness check below) touches it: a non-existent
    # provider_uri — e.g. QUANT_PROVIDER_URI unset on a non-Windows box so
    # config_walk.yaml's ${...:-D:/...} default resolves to a path that isn't
    # there — otherwise reaches qlib and crashes obscurely. Mirrors the guard in
    # recommend() / Pipeline.run() (the official rolling engine gets the same
    # protection).
    guard_message = provider_uri_guard_message(qlib_config.provider_uri)
    if guard_message is not None:
        raise ValueError(guard_message)
    # --dataset-cache-dir overrides whatever the YAML sets. The CLI
    # value is forwarded verbatim — including the empty-string sentinel,
    # which WalkForwardConfig.dataset_cache_dir interprets as "explicit
    # disable, do not fall back to QLIB_DATASET_CACHE_DIR". Converting
    # ``""`` to ``None`` here would let the env var re-enable caching
    # behind the operator's back — exactly what the documented "stamp
    # off" semantic forbids.
    if ds_cache_override is not None:
        import dataclasses
        wf_config = dataclasses.replace(
            wf_config, dataset_cache_dir=ds_cache_override,
        )
    mined_factor_bundle = _maybe_build_mined_factor_bundle(
        raw_yaml, wf_config, qlib_config.provider_uri,
    )

    # Validate the bundle covers wf_config.overall_end BEFORE qlib
    # opens any data files. A stale-bundle config would otherwise
    # fail deep inside FeatureDatasetBuilder with an opaque "empty
    # dataset" message after many seconds of qlib loading; this
    # check turns it into an upfront, named exception. See
    # src/data/bundle_manifest.py for the QLIB_SKIP_BUNDLE_VALIDATION
    # opt-out and the missing-manifest behaviour.
    validate_test_end_against_bundle(
        qlib_config.provider_uri,
        wf_config.overall_end,
    )

    _logger.info("Initialising qlib runtime (provider_uri=%s)", qlib_config.provider_uri)
    init_qlib_canonical(qlib_config)

    if mined_factor_bundle is not None:
        _logger.info(
            "Binding MinedFactor handler (pool_dir=%s)",
            mined_factor_bundle.pool_dir,
        )
        # replace=True so re-runs in the same Python process re-bind
        # the registry slot to the new bundle without raising
        # "already registered". The spec
        # v2-feature-handler-registry's "registered via explicit bind"
        # requirement names this script as an authorised bind site.
        register_mined_factor_handler(mined_factor_bundle, replace=True)

    result = WalkForwardEngine.run(wf_config, resume_mode=resume_mode)

    _logger.info("")
    _logger.info("Walk-forward complete: %d folds", result.num_folds)
    _logger.info("Output directory: %s", wf_config.output_dir)
    if result.report_path:
        _logger.info("Aggregate report:  %s", result.report_path)


if __name__ == "__main__":
    main()

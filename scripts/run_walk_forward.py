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

# Allow ``python scripts/run_walk_forward.py`` from the repo root —
# ensure the project root is on sys.path before importing src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402
from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical  # noqa: E402
from src.core.walk_forward import (  # noqa: E402
    ResumeMode,
    WalkForwardConfig,
    WalkForwardEngine,
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


def _load_config(path: str) -> tuple[WalkForwardConfig, QlibRuntimeConfig]:
    """Load walk-forward + qlib runtime config from a YAML mapping.

    The YAML may carry a top-level ``provider_uri`` and ``region``
    used to initialise qlib; the four ``mined_factor_*`` keys
    (consumed separately by ``_maybe_build_mined_factor_bundle``);
    everything else is funnelled into :class:`WalkForwardConfig`.
    Unknown keys raise a hard error (mirrors ``main.py``'s behaviour).
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = load_yaml_with_inheritance(config_path)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}"
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
    raw: dict,
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


def _parse_cli(argv: list[str]) -> tuple[str, ResumeMode]:
    """Parse CLI flags. Returns ``(config_path, resume_mode)``.

    Preserves the legacy positional form ``python
    scripts/run_walk_forward.py [config.yaml]`` — the config path is a
    positional with a sensible default. Resume flags are mutually
    exclusive; passing both raises ``SystemExit(2)`` via argparse.
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
    ns = parser.parse_args(argv)
    if ns.no_resume:
        return ns.config, ResumeMode.FORCE_RERUN
    if ns.resume_from_fold is not None:
        if ns.resume_from_fold < 0:
            parser.error("--resume-from-fold N must be ≥ 0")
        return ns.config, ResumeMode.from_fold(ns.resume_from_fold)
    return ns.config, ResumeMode.AUTO


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    config_file, resume_mode = _parse_cli(
        argv if argv is not None else sys.argv[1:],
    )
    _logger.info("Loading walk-forward config from %s", config_file)
    raw_yaml = load_yaml_with_inheritance(Path(config_file))
    wf_config, qlib_config = _load_config(config_file)
    mined_factor_bundle = _maybe_build_mined_factor_bundle(
        raw_yaml, wf_config, qlib_config.provider_uri,
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

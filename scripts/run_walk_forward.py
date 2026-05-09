"""Walk-forward CLI runner.

Usage:
    python scripts/run_walk_forward.py [config_walk.yaml]

Reads a YAML mapping into :class:`WalkForwardConfig` and runs
:meth:`WalkForwardEngine.run`. Mirrors :mod:`main.py` for the single-fold
pipeline; kept as a separate script because the walk-forward engine
has its own config dataclass with different fields.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Allow ``python scripts/run_walk_forward.py`` from the repo root —
# ensure the project root is on sys.path before importing src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402
from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.core.qlib_runtime import QlibRuntimeConfig, init_qlib_canonical  # noqa: E402
from src.core.walk_forward import (  # noqa: E402
    WalkForwardConfig,
    WalkForwardEngine,
)

_logger = get_logger(__name__)


def _load_config(path: str) -> tuple[WalkForwardConfig, QlibRuntimeConfig]:
    """Load walk-forward + qlib runtime config from a YAML mapping.

    The YAML may carry a top-level ``provider_uri`` and ``region``
    used to initialise qlib; everything else is funnelled into
    :class:`WalkForwardConfig`. Unknown keys raise a hard error
    (mirrors ``main.py``'s behaviour).
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
    unknown = sorted(set(raw) - valid_fields - qlib_keys)
    if unknown:
        # Reject unknown keys hard. Previously we logged a WARNING and
        # silently dropped them, which masked typos like ``top_k`` /
        # ``ensemble_window_size`` etc. — the run continued with default
        # values, producing official metrics that bore no relation to
        # the YAML the operator thought they had set.
        raise ValueError(
            f"Unknown config keys in {config_path}: {unknown}. "
            f"Valid WalkForwardConfig fields: {sorted(valid_fields)}; "
            f"plus qlib runtime keys: {sorted(qlib_keys)}. "
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


def main() -> None:
    setup_logging()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config_walk.yaml"
    _logger.info("Loading walk-forward config from %s", config_file)
    wf_config, qlib_config = _load_config(config_file)

    _logger.info("Initialising qlib runtime (provider_uri=%s)", qlib_config.provider_uri)
    init_qlib_canonical(qlib_config)

    result = WalkForwardEngine.run(wf_config)

    _logger.info("")
    _logger.info("Walk-forward complete: %d folds", result.num_folds)
    _logger.info("Output directory: %s", wf_config.output_dir)
    if result.report_path:
        _logger.info("Aggregate report:  %s", result.report_path)


if __name__ == "__main__":
    main()

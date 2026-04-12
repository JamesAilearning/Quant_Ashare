"""V2 Quantitative Trading Pipeline — end-to-end entry point.

Usage:
    python main.py                     # uses config.yaml
    python main.py config.yaml         # explicit config file
    python main.py my_strategy.yaml    # custom config
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from src.core.logger import get_logger, setup_logging
from src.core.pipeline import Pipeline, PipelineConfig

_logger = get_logger(__name__)


def _load_config(path: str) -> PipelineConfig:
    """Load a PipelineConfig from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

    # Only pass keys that PipelineConfig accepts
    valid_fields = {f.name for f in PipelineConfig.__dataclass_fields__.values()}
    unknown = set(raw) - valid_fields
    if unknown:
        _logger.warning("Unknown config keys ignored: %s", sorted(unknown))

    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    return PipelineConfig(**filtered)


def main() -> None:
    setup_logging()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    _logger.info("Loading config from %s", config_file)
    config = _load_config(config_file)
    Pipeline.run(config)


if __name__ == "__main__":
    main()

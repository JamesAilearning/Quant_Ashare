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

    # Reject unknown keys hard. The previous WARNING was easy to miss
    # in a noisy log and silently masked typos like ``top_k`` (which
    # has no effect — ``topk`` stays at the default 50). Hard-fail so
    # the run aborts before training starts and the operator gets a
    # clear list of invalid keys.
    valid_fields = {f.name for f in PipelineConfig.__dataclass_fields__.values()}
    unknown = sorted(set(raw) - valid_fields)
    if unknown:
        raise ValueError(
            f"Unknown config keys in {config_path}: {unknown}. "
            f"Valid PipelineConfig fields: {sorted(valid_fields)}. "
            "Refusing to run with potentially-typo'd keys; the previous "
            "default was a WARNING that hid silent reverts to defaults."
        )

    return PipelineConfig(**raw)


def main() -> None:
    setup_logging()
    config_file = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    _logger.info("Loading config from %s", config_file)
    config = _load_config(config_file)
    Pipeline.run(config)


if __name__ == "__main__":
    main()

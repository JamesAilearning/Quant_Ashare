"""V2 Quantitative Trading Pipeline — end-to-end entry point.

Usage:
    python main.py                     # uses config.yaml
    python main.py config.yaml         # explicit config file
    python main.py my_strategy.yaml    # custom config
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.core._yaml_loader import load_yaml_with_inheritance
from src.core.canonical_backtest_contract import (
    stamp_tax_schedule_migration_snippet,
)
from src.core.logger import get_logger, setup_logging
from src.core.pipeline import Pipeline, PipelineConfig

_logger = get_logger(__name__)


def _load_config(path: str) -> PipelineConfig:
    """Load a PipelineConfig from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Resolve ``${VAR:-default}`` env-var references (provider_uri and the
    # other parameterized paths use them as of ops Phase 1) and any ``extends``
    # chain — the SAME loader scripts/run_walk_forward.py uses. A plain
    # ``yaml.safe_load`` would leave the literal ``${...}`` placeholder in
    # provider_uri and qlib would initialise against an invalid path. The loader
    # raises (YamlInheritanceError) if the YAML root is not a mapping, so no
    # separate isinstance guard is needed here.
    raw = load_yaml_with_inheritance(config_path)

    # Legacy scalar ``stamp_tax_bps`` was replaced by
    # ``stamp_tax_schedule`` as part of the audit-P0-4 cost-model
    # change. Detect the legacy key BEFORE the generic "unknown
    # keys" check so the operator gets a precise migration message
    # (the snippet + the why) rather than a generic "unknown key"
    # error that buries the actual fix.
    if "stamp_tax_bps" in raw:
        raise ValueError(
            f"Config {config_path} uses the legacy scalar key "
            "``stamp_tax_bps``. CN A-share stamp tax was halved on "
            "2023-08-28 (10 bps → 5 bps), so backtest windows that "
            "span the reform must use a TIME-ORDERED schedule, not a "
            "single scalar. Replace the line with the canonical "
            "default:\n\n"
            f"{stamp_tax_schedule_migration_snippet()}"
            "\nOr omit ``stamp_tax_schedule`` entirely (None / "
            "missing key → CN_STAMP_TAX_SCHEDULE_DEFAULT applied "
            "automatically). See "
            "openspec/changes/add-stamp-tax-schedule for the "
            "design + accepted shape."
        )

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

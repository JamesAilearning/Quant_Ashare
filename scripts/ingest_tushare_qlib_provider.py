"""CLI to publish an opt-in qlib provider bundle from Tushare OHLCV data.

Usage:

    python scripts/ingest_tushare_qlib_provider.py config_tushare_qlib_provider.yaml

The config file must not contain the Tushare token. The publisher reads
``TUSHARE_TOKEN`` from the environment via ``TushareClient.from_environment``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.provider_bundle import (  # noqa: E402
    TushareQlibProviderBundleConfig,
    TushareQlibProviderBundleError,
    TushareQlibProviderPublisher,
)

_logger = get_logger(__name__)


def _load_config(path: str) -> TushareQlibProviderBundleConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return TushareQlibProviderBundleConfig.from_mapping(raw)


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = list(argv) if argv is not None else sys.argv[1:]
    config_file = args[0] if args else "config_tushare_qlib_provider.yaml"
    _logger.info("Loading Tushare qlib provider config from %s", config_file)
    try:
        config = _load_config(config_file)
        result = TushareQlibProviderPublisher.publish(config)
    except (FileNotFoundError, TushareQlibProviderBundleError) as exc:
        _logger.error("Tushare qlib provider publish failed: %s", exc)
        sys.exit(1)

    _logger.info("")
    _logger.info("Tushare qlib provider bundle published.")
    _logger.info("  Output dir:        %s", result.output_dir)
    _logger.info("  Manifest:          %s", result.manifest_path)
    _logger.info("  Validation:        %s", result.validation_path)
    _logger.info("  Health:            %s", result.validation_profile.health)
    _logger.info("  Instruments:       %d", result.validation_profile.instrument_count)
    _logger.info("  Rows:              %d", result.validation_profile.row_count)
    _logger.info("  Coverage:          %s -> %s",
                 result.validation_profile.coverage_start_date,
                 result.validation_profile.coverage_end_date)
    if result.comparison_path:
        _logger.info("  Comparison:        %s", result.comparison_path)
    _logger.info("")
    _logger.info("To train on this bundle, explicitly set provider_uri to:")
    _logger.info("  %s", result.output_dir)
    _logger.info("and set data_adjust_mode/adjust_mode to:")
    _logger.info("  %s", result.manifest.data_adjust_mode)


if __name__ == "__main__":
    main()

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
from src.data.bundle_manifest import (  # noqa: E402
    BundleManifestError,
    compute_bundle_content_hash,
    save_manifest,
)
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

    # Emit the walk-forward freshness manifest (PR8 contract). The
    # walk-forward CLI reads this to catch stale-bundle configs upfront
    # instead of failing deep inside FeatureDatasetBuilder with an opaque
    # "empty dataset". A missing coverage_end_date (publisher couldn't
    # compute it) skips this with a WARNING — better to publish without
    # the manifest than to fail the ingest at the last step.
    tail = result.validation_profile.coverage_end_date
    if tail:
        # Two distinct failure modes have to be separated here so they
        # get treated differently. Codex P2 on PR #175.
        #
        # (a) ``compute_bundle_content_hash`` raises => the publisher
        #     reported success AND coverage_end_date is set, but
        #     ``calendars/day.txt`` is missing or unreadable. That is
        #     a corrupt bundle — the calendar is a required qlib
        #     provider artifact, so the published output is unusable
        #     as a qlib provider regardless of whether we emit a
        #     manifest. Downgrading to "no manifest, walk-forward
        #     will figure it out" silently leaves the operator with
        #     a broken bundle that fails much later inside qlib
        #     with an opaque error. Fail loudly here instead.
        #
        # (b) ``save_manifest`` raises BundleManifestError => the
        #     ``validation_profile`` data was rejected by the
        #     manifest schema (e.g. a non-int ``instrument_count``).
        #     The bundle bytes on disk are still fine; the failure
        #     is just in the metadata sidecar. Falling back to "no
        #     manifest" is honest here — walk-forward will warn and
        #     proceed.
        try:
            content_hash = compute_bundle_content_hash(result.output_dir)
        except BundleManifestError as exc:
            _logger.error(
                "Tushare publish reported success and coverage_end_date "
                "is set, but compute_bundle_content_hash failed: %s. "
                "The bundle is missing calendars/day.txt (or it is "
                "unreadable), which is a required qlib provider artifact "
                "— the output is NOT a usable qlib provider in this "
                "state. Aborting before writing a misleading manifest. "
                "Investigate the publisher; re-run ingest after fixing.",
                exc,
            )
            sys.exit(1)

        try:
            manifest_path = save_manifest(
                result.output_dir,
                tail_date=tail,
                instrument_count=result.validation_profile.instrument_count,
                content_hash=content_hash,
            )
            _logger.info("  Bundle manifest:   %s", manifest_path)
            _logger.info("  Content hash:      %s", content_hash)
        except BundleManifestError as exc:
            _logger.warning(
                "Skipped bundle_manifest.json emit (validation_profile data "
                "was rejected by save_manifest): %s. The bundle itself is "
                "fine; walk-forward will fall back to 'no manifest = no "
                "freshness validation'.",
                exc,
            )
    else:
        _logger.warning(
            "Skipped bundle_manifest.json emit — "
            "validation_profile.coverage_end_date is None. "
            "The publisher couldn't determine a tail_date; the bundle "
            "is still usable but walk-forward freshness validation will "
            "fall back to the legacy 'no manifest, no check' path.",
        )

    _logger.info("")
    _logger.info("To train on this bundle, explicitly set provider_uri to:")
    _logger.info("  %s", result.output_dir)
    _logger.info("and set data_adjust_mode/adjust_mode to:")
    _logger.info("  %s", result.manifest.data_adjust_mode)


if __name__ == "__main__":
    main()

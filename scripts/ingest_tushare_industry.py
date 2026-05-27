"""CLI to publish a Shenwan L2 industry artifact from Tushare.

Usage::

    export TUSHARE_TOKEN='...'
    python scripts/ingest_tushare_industry.py [config_tushare.yaml]

Reads paths and snapshot date from a YAML mapping, calls
:meth:`TushareIndustryPublisher.publish`, and reports the resulting
artifact paths + row counts. The YAML deliberately does NOT carry the
Tushare token — the publisher picks it up from ``TUSHARE_TOKEN`` so
the secret never lands in committed config.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

# Allow ``python scripts/ingest_tushare_industry.py`` from the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.industry_publisher import (  # noqa: E402
    DEFAULT_SHENWAN_SRC,
    SW_L2_TAXONOMY_NAME,
    TushareIndustryPublisher,
    TushareIndustryPublisherError,
)

_logger = get_logger(__name__)


_DEFAULT_CONFIG: dict[str, Any] = {
    "artifact_path": "output/taxonomy/sw_l2.csv",
    "manifest_path": "output/taxonomy/sw_l2.json",
    "level": "L2",
    "shenwan_src": DEFAULT_SHENWAN_SRC,
    "taxonomy_name": SW_L2_TAXONOMY_NAME,
}

_REQUIRED_KEYS = ("snapshot_at",)
_TOKEN_KEY_FORBIDDEN = "tushare_token"


def _load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}"
        )

    # Refuse to read a token from YAML — secrets-in-config is the
    # exact failure mode this whole module is structured to avoid.
    if _TOKEN_KEY_FORBIDDEN in raw:
        raise ValueError(
            f"Config key {_TOKEN_KEY_FORBIDDEN!r} is forbidden. "
            "Tushare tokens must come from the TUSHARE_TOKEN environment "
            "variable so they never land in committed config."
        )

    merged = {**_DEFAULT_CONFIG, **raw}

    missing = [k for k in _REQUIRED_KEYS if not merged.get(k)]
    if missing:
        raise ValueError(
            f"Config missing required keys: {missing}. "
            "Provide them in the YAML or set them via overrides."
        )
    return merged


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    args = list(argv) if argv is not None else sys.argv[1:]
    config_file = args[0] if args else "config_tushare.yaml"
    _logger.info("Loading Tushare ingest config from %s", config_file)
    config = _load_config(config_file)

    # Make sure the destination dir exists; publisher creates leaf
    # files but not intermediate dirs (its docstring says it does, but
    # belt-and-braces).
    Path(config["artifact_path"]).parent.mkdir(parents=True, exist_ok=True)

    try:
        result = TushareIndustryPublisher.publish(
            artifact_path=config["artifact_path"],
            manifest_path=config["manifest_path"],
            snapshot_at=config["snapshot_at"],
            level=config["level"],
            shenwan_src=config["shenwan_src"],
            taxonomy_name=config["taxonomy_name"],
        )
    except TushareIndustryPublisherError as exc:
        _logger.error("Industry publish failed: %s", exc)
        sys.exit(1)

    _logger.info("")
    _logger.info("Tushare industry artifact published.")
    _logger.info("  Industries fetched:    %d", result.industries_fetched)
    _logger.info("  Instruments classified: %d", result.instruments_classified)
    _logger.info("  Artifact:              %s", result.taxonomy_result.artifact_path)
    _logger.info("  Manifest:              %s", result.taxonomy_result.manifest_path)


if __name__ == "__main__":
    main()

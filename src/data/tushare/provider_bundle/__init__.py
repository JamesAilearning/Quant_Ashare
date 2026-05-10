"""Tushare → qlib binary bundle publisher.

Split from a single 1491-LOC module into a sub-package with zero
behavior change and zero caller-side import changes.
"""

from src.data.tushare.provider_bundle._types import (
    TushareQlibProviderBundleError,
    TushareQlibProviderValidationProfile,
    TushareQlibProviderManifest,
    TushareProviderComparisonReport,
    TushareQlibProviderPublishResult,
    TushareStagedMarketData,
)
from src.data.tushare.provider_bundle._types import (
    DEFAULT_MANIFEST_NAME,
    DEFAULT_VALIDATION_NAME,
    DEFAULT_COMPARISON_NAME,
    PUBLISHER_VERSION,
    MANIFEST_SCHEMA_VERSION,
    VALIDATION_SCHEMA_VERSION,
    SOURCE_NAME,
    SOURCE_APIS,
    INDEX_SOURCE_API,
    FORBIDDEN_CONFIG_KEYS,
)
from src.data.tushare.provider_bundle.config import TushareQlibProviderBundleConfig
from src.data.tushare.provider_bundle.fetcher import TushareMarketDataFetcher
from src.data.tushare.provider_bundle.publisher import TushareQlibProviderPublisher
from src.data.tushare.provider_bundle.comparison import compare_provider_bundles

__all__ = (
    "TushareQlibProviderBundleError",
    "TushareQlibProviderBundleConfig",
    "TushareQlibProviderValidationProfile",
    "TushareQlibProviderManifest",
    "TushareProviderComparisonReport",
    "TushareQlibProviderPublishResult",
    "TushareStagedMarketData",
    "TushareMarketDataFetcher",
    "TushareQlibProviderPublisher",
    "compare_provider_bundles",
    "DEFAULT_MANIFEST_NAME",
    "DEFAULT_VALIDATION_NAME",
    "DEFAULT_COMPARISON_NAME",
    "PUBLISHER_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "VALIDATION_SCHEMA_VERSION",
    "SOURCE_NAME",
    "SOURCE_APIS",
    "INDEX_SOURCE_API",
    "FORBIDDEN_CONFIG_KEYS",
)

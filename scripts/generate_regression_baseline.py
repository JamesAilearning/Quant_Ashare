"""Generate the regression baseline fixtures for the walk-forward
aggregate test (audit FU-5).

Runs ``WalkForwardEngine.run`` against a known-good YAML config and
writes the headline aggregate metrics to
``tests/regression/fixtures/walk_forward_baseline_metrics.json``.

Usage
-----
::

    RUN_E2E=1 python scripts/generate_regression_baseline.py [config.yaml]

The output JSON is intentionally **NOT auto-committed**. The
project's reference-data workflow is:

    I pull, you eyeball, you sign off, I commit.

So this script:

1. Runs the walk-forward end-to-end with the supplied config.
2. Writes the resulting metrics to the fixtures directory.
3. Prints a one-line summary + the file path.
4. **Stops there.** Operators must:
   a. Open the file.
   b. Eyeball the headline numbers (IR / IC / ann-return / max-DD).
   c. Confirm the run completed cleanly (``num_folds`` matches
      what the config produces; ``valid_folds_*`` counts are
      sensible; no spurious NaN).
   d. ``git add tests/regression/fixtures/walk_forward_baseline_metrics.json``
   e. Commit with a message referencing the bundle vintage + config
      hash (e.g. "regression: refresh baseline for csi300 walk_walk
      against bundle 2026-03-06").

After commit, ``tests/regression/test_walk_forward_aggregate_baseline``
will run on ``RUN_E2E=1`` invocations and assert headline metrics
stay within ±5% of this baseline.

Refresh cadence
---------------
Refresh the baseline whenever:

* A merged PR intentionally changes the headline metric (e.g. PR1
  fixed the rank-IC double-counting; baselines from before that PR
  are no longer comparable).
* The qlib bundle is re-ingested with a new coverage window.
* Tushare publishes corrected historical data (rare).

Do NOT refresh because the baseline is "slightly off" — that's
exactly the regression the test exists to surface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._yaml_loader import load_yaml_with_inheritance  # noqa: E402
from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.core.qlib_runtime import init_qlib_canonical  # noqa: E402
from src.core.walk_forward import WalkForwardEngine  # noqa: E402

_logger = get_logger(__name__)

DEFAULT_OUTPUT = (
    PROJECT_ROOT / "tests" / "regression" / "fixtures"
    / "walk_forward_baseline_metrics.json"
)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = list(argv) if argv is not None else sys.argv[1:]
    config_file = args[0] if args else "config_walk.yaml"

    _logger.info("Loading walk-forward config from %s", config_file)
    # Reuse the CLI's loader so the regression baseline matches what
    # the user-facing walk-forward CLI actually does.
    from scripts.run_walk_forward import _load_config  # noqa: PLC0415

    wf_config, qlib_config = _load_config(config_file)
    raw_yaml = load_yaml_with_inheritance(Path(config_file))

    _logger.info(
        "Initialising qlib runtime (provider_uri=%s)",
        qlib_config.provider_uri,
    )
    init_qlib_canonical(qlib_config)

    _logger.info("Running walk-forward to collect baseline aggregates…")
    result = WalkForwardEngine.run(wf_config)

    # Headline metrics + provenance. The provenance keys help future
    # readers tell "is this baseline still valid?" without grepping
    # git log.
    payload = {
        "_provenance": {
            "config_file": str(config_file),
            "config_keys": sorted(raw_yaml.keys()) if isinstance(raw_yaml, dict) else [],
            "provider_uri": qlib_config.provider_uri,
            "overall_start": wf_config.overall_start,
            "overall_end": wf_config.overall_end,
            "feature_handler": wf_config.feature_handler,
            "ensemble_window": wf_config.ensemble_window,
            "num_folds": result.num_folds,
        },
        "aggregate_metrics": dict(result.aggregate_metrics),
    }

    output = DEFAULT_OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )

    _logger.info("")
    _logger.info("Baseline written:  %s", output)
    _logger.info("")
    _logger.info("HEADLINE METRICS (please eyeball):")
    for key in (
        "mean_information_ratio",
        "mean_ic_1d",
        "mean_annualized_return",
        "worst_drawdown",
    ):
        value = result.aggregate_metrics.get(key)
        if value is not None:
            _logger.info("  %s: %s", key, value)
    _logger.info("")
    _logger.info(
        "Per the project's reference-data workflow:"
    )
    _logger.info(
        "  1. Open the file above."
    )
    _logger.info(
        "  2. Confirm headline numbers are within the range you expect."
    )
    _logger.info(
        "  3. ``git add`` + ``git commit`` with a message naming the "
        "bundle vintage."
    )
    _logger.info(
        "  4. CI's RUN_E2E=1 invocations will then enforce ±5% drift."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

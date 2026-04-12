"""Guard for E2E tests that load full qlib datasets.

E2E tests (csi300 full features, model training, backtest) consume significant
memory and CPU. They are skipped by default and only run when the environment
variable ``RUN_E2E=1`` is set.

Usage in test files::

    from tests.e2e_guard import skip_unless_e2e

    @skip_unless_e2e
    class MyHeavyE2ETest(unittest.TestCase):
        ...

To run E2E tests::

    RUN_E2E=1 python -m unittest tests.logic.test_pipeline
"""

from __future__ import annotations

import os
import unittest

_RUN_E2E = os.environ.get("RUN_E2E", "").strip() in ("1", "true", "yes")

skip_unless_e2e = unittest.skipUnless(
    _RUN_E2E,
    "E2E tests skipped (set RUN_E2E=1 to enable)",
)

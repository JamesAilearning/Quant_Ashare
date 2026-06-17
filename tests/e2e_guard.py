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

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_flag_enabled(value: str | None) -> bool:
    """Return True iff ``value`` is a recognised truthy flag.

    Case-INSENSITIVE: a CI / shell that exports ``RUN_E2E=True`` or ``RUN_E2E=YES``
    must enable E2E just like ``RUN_E2E=1``. The previous case-sensitive check
    silently treated ``True``/``YES`` as off — a footgun that could either run
    the machine-freezing E2E suite unexpectedly or, worse, silently skip it when
    the operator believed it was on.
    """
    return (value or "").strip().lower() in _TRUTHY


def run_e2e_enabled() -> bool:
    """Single source of truth for the RUN_E2E gate.

    Every E2E gate in the suite (``skip_unless_e2e`` plus the standalone
    ``skipif`` checks in the regression / inference tests) must route through
    this so a given ``RUN_E2E`` spelling enables ALL of them or none — otherwise
    a value like ``True`` would start the heavy qlib tests while other gates
    silently skip, giving a misleading partial E2E run.
    """
    return _env_flag_enabled(os.environ.get("RUN_E2E"))


_RUN_E2E = run_e2e_enabled()

skip_unless_e2e = unittest.skipUnless(
    _RUN_E2E,
    "E2E tests skipped (set RUN_E2E=1 to enable)",
)

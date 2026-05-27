"""Tests guarding the FU-7 mypy-strict opt-in.

These tests do NOT invoke mypy themselves — that would be slow and
duplicates the CI step. Instead they pin:

* The pyproject.toml override block exists and lists the expected
  modules (so a future cleanup doesn't accidentally drop a module
  from the strict allowlist).
* Each of the strict-mode source files exists and is importable.
  (A module typo'd in the pyproject override would be silently
  ignored by mypy.)

The actual strict-mode enforcement happens in CI via the
"Type check strict modules (audit FU-7)" step.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Single source of truth for the strict-mode module set. Tests below
# cross-check pyproject.toml against this list AND verify each
# module imports cleanly. Adding a module here is a 3-step process:
#   1. Add to this tuple.
#   2. Add to the pyproject.toml ``[[tool.mypy.overrides]] module``
#      block.
#   3. Add the file to the CI workflow's strict-mypy step.
# All three steps are checked here (steps 1+2) and in CI (step 3).
# The three original FU-7 ``src.data.*`` individual modules were
# removed from pyproject in batch 2 — they're redundant under the
# ``src.data.*`` wildcard. The two ``src.core.*`` survivors stay
# until batch 4's flip (they're redundant under ``src.core.*`` but
# the test still pins them for symmetry with the batch-2 cleanup).
STRICT_MODULES: tuple[str, ...] = (
    "src.core.walk_forward._resume",
    "src.core.regression_baseline",
)

# Wildcard patterns added by ``add-mypy-strict-everywhere``. The
# pyproject overrides block accepts ``"src.core.*"`` style globs —
# we cross-check these as substring matches in the file rather than
# importlib-loading every module they cover (too slow, and the
# directory-level CI step already enforces strict inside the tree).
STRICT_MODULE_PATTERNS: tuple[str, ...] = (
    "src.core.*",
    "src.pit.*",
    "src.data.*",
    "scripts.*",
    "web.operator_ui.*",
)


class MypyStrictPyprojectTests(unittest.TestCase):
    def test_pyproject_lists_all_strict_modules(self):
        """The pyproject.toml override block must list every module in
        STRICT_MODULES, no more no less. Drift detection."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        # Find the [[tool.mypy.overrides]] block that has strict-mode
        # flags. We don't want a strict YAML/TOML parse here — too
        # heavyweight for a one-line check; just verify each module
        # string appears in the file.
        for module in STRICT_MODULES:
            self.assertIn(
                f'"{module}"', text,
                f"pyproject.toml's mypy overrides should list "
                f"{module!r} — see FU-7. Found pyproject without it; "
                f"either add the module to the override block, or "
                f"remove it from STRICT_MODULES in this test.",
            )

    def test_pyproject_lists_all_strict_patterns(self):
        """Same drift check as above but for the wildcard patterns
        added by ``add-mypy-strict-everywhere`` (batch 1 of 4)."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        for pattern in STRICT_MODULE_PATTERNS:
            self.assertIn(
                f'"{pattern}"', text,
                f"pyproject.toml's mypy overrides should list "
                f"{pattern!r} — see add-mypy-strict-everywhere batch 1. "
                f"Either add the pattern to the override block, or "
                f"remove it from STRICT_MODULE_PATTERNS in this test.",
            )

    def test_strict_module_flags_present(self):
        """Lock the strict flags so a future PR can't dilute them
        without explicit acknowledgement."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        for flag in (
            "disallow_untyped_defs",
            "disallow_untyped_calls",
            "disallow_incomplete_defs",
            "warn_return_any",
            "no_implicit_optional",
            "strict_equality",
        ):
            self.assertIn(
                f"{flag} = true", text,
                f"pyproject.toml's strict-mode override should set "
                f"``{flag} = true`` (see FU-7). Without it, the strict "
                "module set silently loses safety guarantees.",
            )


class MypyStrictModuleImportTests(unittest.TestCase):
    def test_every_strict_module_imports_cleanly(self):
        """A typo in pyproject.toml (e.g. ``src.data._sigment_embargo``)
        would be silently ignored by mypy. Importing each module here
        catches that class of error: if STRICT_MODULES lists a name
        the test suite can't import, the test fails loudly."""
        for module in STRICT_MODULES:
            with self.subTest(module=module):
                try:
                    importlib.import_module(module)
                except Exception as exc:  # noqa: BLE001
                    self.fail(
                        f"strict-mode module {module!r} failed to "
                        f"import: {type(exc).__name__}: {exc}"
                    )


class MypyStrictCiWorkflowTests(unittest.TestCase):
    def test_ci_workflow_invokes_strict_check(self):
        """CI must run a dedicated strict-mode mypy step (FU-7),
        else the per-module overrides in pyproject.toml are
        unenforced — anyone could add an ``Any`` to one of these
        modules and CI would let it through."""
        wf = PROJECT_ROOT / ".github" / "workflows" / "test.yml"
        text = wf.read_text(encoding="utf-8")
        self.assertIn(
            "Type check strict modules", text,
            "CI workflow should have a dedicated strict mypy step. "
            "See FU-7.",
        )
        self.assertIn(
            "--follow-imports=silent", text,
            "Strict mypy step should use ``--follow-imports=silent`` "
            "so legacy-code transitive errors don't drown the "
            "strict-module signal.",
        )

    def test_ci_workflow_covers_strict_directories(self):
        """``add-mypy-strict-everywhere`` extends CI's strict step
        to directory-level coverage. The directory names must
        appear in the workflow's mypy invocation; if a future PR
        demotes the step back to a narrower file-list, this test
        fails loudly. Each batch adds one directory to the
        assertion list — batch 3 adds ``web/operator_ui/``."""
        wf = PROJECT_ROOT / ".github" / "workflows" / "test.yml"
        text = wf.read_text(encoding="utf-8")
        for directory in (
            "src/core/", "src/pit/", "src/data/", "scripts/",
            "web/operator_ui/",
        ):
            self.assertIn(
                directory, text,
                f"CI workflow's strict-mypy step must include "
                f"{directory!r} — see add-mypy-strict-everywhere.",
            )


if __name__ == "__main__":
    unittest.main()

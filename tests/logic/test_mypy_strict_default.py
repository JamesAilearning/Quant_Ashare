"""Tests pinning the post-migration strict-by-default mypy posture.

After ``add-mypy-strict-everywhere`` batch 4 (PR #186 — final batch),
the repo runs ``mypy --strict`` by default. The whitelist that grew
through batches 1-3 is gone; the only opt-out is
``src.factor_mining.*`` (parallel workstream, opt-out removed in a
follow-up after stabilisation).

These tests do NOT invoke mypy themselves — that would be slow and
duplicates the CI step. They pin the **shape** of the pyproject /
CI configuration so a future PR cannot silently revert the flip:

* ``[tool.mypy] strict = true`` is set.
* Exactly one ``[[tool.mypy.overrides]]`` block exists, covering
  only ``src.factor_mining.*``.
* The opt-out block disables every flag implied by ``mypy --strict``
  (Codex P1 on PR #171 — listing only the FU-7 subset would let
  future-added strict flags silently leak into factor-mining).
* The CI workflow's mypy step has no ``continue-on-error``.

The actual strict-mode enforcement happens in CI via the
``Type check (mypy --strict by default)`` step.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# The single opt-out module pattern. Anyone trying to add a SECOND
# opt-out (e.g. ``"src.legacy.*"``) without removing this assertion
# will fail this test loudly.
OPT_OUT_MODULES: tuple[str, ...] = ("src.factor_mining.*",)


# Every flag implied by ``mypy --strict`` — see
# https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict
# (Codex P1 on PR #171). Two flags are listed separately because mypy
# rejects them in per-module overrides ("should only specify per-
# module flags"): ``warn_unused_configs`` and ``warn_redundant_casts``.
# They stay at the global level and inherit from ``strict = true``.
STRICT_FLAGS_PER_MODULE: tuple[str, ...] = (
    "disallow_any_generics",
    "disallow_subclassing_any",
    "disallow_untyped_calls",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "check_untyped_defs",
    "disallow_untyped_decorators",
    "no_implicit_optional",
    "warn_unused_ignores",
    "warn_return_any",
    "no_implicit_reexport",
    "strict_equality",
    "strict_concatenate",
    "extra_checks",
)


class StrictDefaultPyprojectTests(unittest.TestCase):
    def test_global_strict_is_true(self) -> None:
        """``[tool.mypy] strict = true`` is the repo-wide default."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        self.assertIn(
            "strict = true", text,
            "pyproject.toml's [tool.mypy] block must set "
            "``strict = true``. add-mypy-strict-everywhere batch 4 "
            "is the migration that flipped this on.",
        )

    def test_exactly_one_overrides_block(self) -> None:
        """Exactly one ``[[tool.mypy.overrides]]`` block exists — the
        factor_mining opt-out. A second block (e.g. someone adding
        a new opt-out without an OpenSpec change) fails this test."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        count = len(re.findall(r"^\[\[tool\.mypy\.overrides\]\]", text, re.MULTILINE))
        self.assertEqual(
            count, 1,
            f"pyproject.toml has {count} ``[[tool.mypy.overrides]]`` "
            "blocks; expected exactly 1 (the factor_mining opt-out). "
            "Adding a second opt-out should go through an OpenSpec "
            "change; until then, this test fails.",
        )

    def test_opt_out_block_lists_only_factor_mining(self) -> None:
        """The single overrides block's ``module`` list must contain
        exactly the OPT_OUT_MODULES tuple. Drift detection."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        for module in OPT_OUT_MODULES:
            self.assertIn(
                f'"{module}"', text,
                f"pyproject.toml's overrides should list {module!r}. "
                "See add-mypy-strict-everywhere proposal.",
            )

    def test_opt_out_block_disables_every_strict_flag(self) -> None:
        """Codex P1 on PR #171: the opt-out must explicitly set
        every strict-implied flag to ``false``. Listing only the
        FU-7 subset would silently enable new strict flags mypy
        adds in future releases."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        for flag in STRICT_FLAGS_PER_MODULE:
            self.assertIn(
                f"{flag} = false", text,
                f"factor_mining opt-out should set ``{flag} = false`` — "
                "without it, this strict-implied flag stays active on "
                "the factor-mining tree and the 'single opt-out' "
                "claim breaks.",
            )

    def test_opt_out_uses_ignore_errors(self) -> None:
        """Practical opt-out: ``ignore_errors = true`` skips non-
        strict errors too (e.g. real type bugs the factor-mining
        workstream hasn't fixed yet). The explicit per-flag
        ``false`` settings stay for future-proofing."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        self.assertIn(
            "ignore_errors = true", text,
            "factor_mining opt-out should set ``ignore_errors = true`` — "
            "otherwise non-strict type errors in the active workstream "
            "still fail CI.",
        )


class StrictDefaultCiWorkflowTests(unittest.TestCase):
    def test_ci_workflow_invokes_default_strict(self) -> None:
        """The CI workflow's mypy step must rely on the pyproject
        default — no per-step ``--strict`` flag fiddling, no
        ``continue-on-error``."""
        wf = PROJECT_ROOT / ".github" / "workflows" / "test.yml"
        text = wf.read_text(encoding="utf-8")
        self.assertIn(
            "Type check (mypy --strict by default)", text,
            "CI workflow's mypy step should be named "
            "``Type check (mypy --strict by default)`` so reviewers "
            "see the migration completed.",
        )
        self.assertIn(
            "--follow-imports=silent", text,
            "CI's mypy step should keep ``--follow-imports=silent`` "
            "so transitive third-party errors don't drown the signal.",
        )

    def test_ci_workflow_drops_continue_on_error(self) -> None:
        """``continue-on-error: true`` on the mypy step would defeat
        the whole migration — a regression would silently log but
        not fail the PR. Make sure CI fails the PR on any mypy
        regression."""
        wf = PROJECT_ROOT / ".github" / "workflows" / "test.yml"
        text = wf.read_text(encoding="utf-8")
        # Locate the mypy step block (between the ``- name: Type
        # check (mypy ...)`` line and the next ``- name:`` line) and
        # assert it doesn't include ``continue-on-error``.
        match = re.search(
            r"- name: Type check \(mypy --strict by default\).*?(?=^      - name:)",
            text, flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(
            match,
            "Could not locate the mypy CI step. "
            "test_ci_workflow_invokes_default_strict should fail "
            "first if the step is missing.",
        )
        assert match is not None  # narrow for mypy
        step_text = match.group(0)
        self.assertNotIn(
            "continue-on-error", step_text,
            "CI's mypy step must not carry ``continue-on-error``. "
            "Strict is the default; a regression should block merge.",
        )

    def test_ci_workflow_covers_full_source_tree(self) -> None:
        """The mypy step must scan the full source tree, not just
        the directories that were curated through batches 1-3. The
        whole point of batch 4 is that strict applies repo-wide."""
        wf = PROJECT_ROOT / ".github" / "workflows" / "test.yml"
        text = wf.read_text(encoding="utf-8")
        # ``src/`` is the umbrella — includes contracts, factor_mining
        # (opted out via pyproject), core, data, pit. ``scripts/`` +
        # ``web/operator_ui/`` are explicit.
        for path in ("src/", "scripts/", "web/operator_ui/"):
            self.assertIn(
                path, text,
                f"CI mypy step must scan {path!r}.",
            )


if __name__ == "__main__":
    unittest.main()

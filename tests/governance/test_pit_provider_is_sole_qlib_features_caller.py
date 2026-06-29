"""Governance: ``PITDataProvider`` is the sole intended caller of
``qlib.data.D.features`` inside ``src/``.

Why this guard exists
---------------------
Direct ``D.features(...)`` calls bypass the §4.3.2 post-delist mask
that lives inside ``src/pit/query.py:_mask_post_delist``. The mask
sets every ``(ticker, date)`` position past ``delist_date`` to NaN —
qlib's default operator semantics let stale / forward-filled values
slip through window operators (e.g. ``Mean($close, 20)``), so any
non-PIT consumer that reads close prices can silently absorb
post-delist data into IC, attribution, or backtest baselines.

The allowlist below is the **only** approved set of direct-call
sites. Each entry pins both the file and the exact number of
``D.features(...)`` calls in it. The test fails when:

* a file NOT in the allowlist contains ``D.features(...)`` (new
  bypass introduced without governance review), OR
* a file IN the allowlist no longer contains ``D.features(...)``
  (stale allowlist entry — the file migrated to PIT and the
  allowlist should be tightened to drop it), OR
* the call count in an allowed file differs from the recorded one
  (a NEW direct-call site was added inside an already-allow-listed
  file; the engineer must confirm the new bypass was intentional
  and bump the count explicitly).

How to add a new allowlist entry
--------------------------------
1. Add a WARN log next to the new ``D.features(...)`` call so
   operators see the bypass at runtime. Reference the audit:
   "Audit P0-6". Mirror the WARN copy already in
   ``backtest_runner._compute_equalweight_baseline`` /
   ``factor_analyzer._fetch_close_panel``.
2. Add the file to ``PIT_FEATURES_BYPASS_ALLOWLIST`` with a
   justification comment.
3. If the call site is going to be migrated to PIT later, leave a
   ``TODO(P0-6 follow-up)`` in the source.

Audit P0-6.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
# Each entry: ``relative-path-from-repo-root: expected-D.features-count``.
# The justification belongs in the comment block above each entry — keep
# it as code-adjacent prose so a future contributor reading the
# allowlist knows why the bypass is OK without digging through git
# history.
PIT_FEATURES_BYPASS_ALLOWLIST: dict[str, int] = {
    # PITDataProvider is the PIT layer itself. ``_fetch_qlib_features``
    # MUST call D.features to get raw bytes that ``_mask_post_delist``
    # then masks. Routing this call through ``PITDataProvider`` would
    # be infinite recursion.
    "src/pit/query.py": 1,

    # ``pit_validator`` validates the on-disk bin contents BEFORE the
    # post-delist mask is applied. Routing through ``PITDataProvider``
    # would apply the very mask the validator is checking for, and
    # the validator would tautologically "pass". The validator MUST
    # bypass PIT. (Was 5; the dead legacy survivorship bridge with its
    # own D.features call was removed in Q4.)
    "src/data/pit/pit_validator.py": 4,

    # ``benchmark_artifact_publisher`` is a PUBLISHER — it fetches
    # benchmark index closes (e.g. SH000300) to write to a CSV
    # artifact. It does not consume features for downstream
    # computation, and the post-delist mask is irrelevant for an
    # index that does not delist.
    "src/data/benchmark_artifact_publisher.py": 1,

    # Call 1: has ``pit_provider`` opt-in — the ``D.features`` call only
    # fires when the caller did NOT pass a provider; a WARN log
    # ("Audit P0-6") makes the bypass observable. TODO listed in the
    # source: thread ``pit_provider`` through ``Pipeline`` /
    # ``WalkForwardEngine`` and retire this legacy branch.
    # Call 2 (PR-D): the round-lot preflight probes ``$factor``/``$close``
    # availability across the run's own candidate universe — a pure
    # diagnostic (warning only, never feeds metrics) where the post-delist
    # mask is irrelevant; WARN log ("Audit P0-6") at the call site.
    # Call 3 (PR-J): ``_validate_consumed_benchmark`` loads the benchmark /
    # total-return INDEX close for fail-loud value-level validation before the
    # backtest consumes it for excess-return. The post-delist mask is
    # irrelevant for an index (an index does not delist), so PITDataProvider
    # would add nothing; WARN log at the call site makes the bypass observable.
    "src/core/backtest_runner.py": 3,

    # Same pattern as ``backtest_runner``: ``pit_provider`` opt-in
    # plus WARN-on-fallback. Same TODO follow-up.
    "src/core/factor_analyzer.py": 1,

    # No ``pit_provider`` opt-in yet — the public ``analyze`` method
    # would need a contract change. WARN log added at the call site
    # so operators reading attribution reports see the bypass on
    # every run. TODO(P0-6 follow-up): thread ``pit_provider``
    # through ``PerformanceAttribution.analyze``.
    "src/core/performance_attribution.py": 1,

    # Same as ``performance_attribution`` — no opt-in yet, WARN-only.
    # TODO(P0-6 follow-up): thread ``pit_provider`` through
    # ``SignalAnalyzer.analyze``.
    "src/core/signal_analyzer.py": 1,

    # ``microstructure_mask`` (audit P0-3 /
    # add-microstructure-mask): has ``pit_provider`` opt-in. The
    # direct ``D.features`` call only fires when the caller did
    # NOT pass a provider; the ``compute_unavailable_mask``
    # function carries the ``pit-bypass-ok`` marker in its
    # docstring.
    "src/core/microstructure_mask.py": 1,
}


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _count_d_features_calls_in_source(text: str) -> int:
    """Count AST ``Call`` nodes that match ``D.features(...)``.

    We deliberately walk the AST instead of regex-grepping so:

    * matches inside docstrings / comments are ignored,
    * matches against a different ``D`` (e.g. a local variable
      shadowing the qlib symbol) are still counted — defensive: if
      you shadowed the name on purpose, you should still call out
      the bypass.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:  # pragma: no cover - all src/ must parse
        raise AssertionError(f"src/ file failed to AST-parse: {exc}") from exc
    n = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "features"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "D"
        ):
            n += 1
    return n


def _scan_src() -> dict[str, int]:
    """Return ``{relative-posix-path: call-count}`` for every src/ file
    with at least one ``D.features(...)`` call.
    """
    out: dict[str, int] = {}
    src_root = _PROJECT_ROOT / "src"
    for py in sorted(src_root.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        n = _count_d_features_calls_in_source(text)
        if n > 0:
            rel = py.relative_to(_PROJECT_ROOT).as_posix()
            out[rel] = n
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class PitProviderIsSoleQlibFeaturesCallerTests(unittest.TestCase):
    """Three orthogonal checks. Splitting them keeps the failure
    message specific: a regression in one dimension does not muddy
    the other two."""

    def test_no_unexpected_callers_outside_allowlist(self) -> None:
        actual = _scan_src()
        unexpected = sorted(set(actual) - set(PIT_FEATURES_BYPASS_ALLOWLIST))
        msg = (
            "New file(s) call ``D.features(...)`` directly without "
            "being in PIT_FEATURES_BYPASS_ALLOWLIST:\n  "
            + "\n  ".join(unexpected)
            + "\n\nEither route the call through "
            "PITDataProvider.get_features, or add the file to the "
            "allowlist with a justification + WARN log. See the "
            "module docstring for the procedure. Audit P0-6."
        )
        self.assertEqual(unexpected, [], msg=msg)

    def test_no_stale_allowlist_entries(self) -> None:
        """A file in the allowlist that no longer has any
        ``D.features(...)`` calls means it migrated to PIT (great!)
        and should be removed from the allowlist so the next bypass
        regression there fails this test loudly."""
        actual = _scan_src()
        stale = sorted(set(PIT_FEATURES_BYPASS_ALLOWLIST) - set(actual))
        msg = (
            "Allowlist entry/entries no longer present in src/:\n  "
            + "\n  ".join(stale)
            + "\n\nThese files have been migrated to PIT — please "
            "remove them from PIT_FEATURES_BYPASS_ALLOWLIST so this "
            "test guards against future bypass regressions there."
        )
        self.assertEqual(stale, [], msg=msg)

    def test_allowlist_counts_match_exactly(self) -> None:
        """Adding a NEW D.features call inside an already-allow-listed
        file FAILS this test until the engineer explicitly bumps the
        count — forces governance review of every new bypass site,
        not just first-time-in-file bypasses."""
        actual = _scan_src()
        mismatches: list[str] = []
        for fpath, expected_n in PIT_FEATURES_BYPASS_ALLOWLIST.items():
            actual_n = actual.get(fpath, 0)
            if actual_n != expected_n:
                mismatches.append(
                    f"  {fpath}: allowlist={expected_n}, actual={actual_n}"
                )
        msg = (
            "D.features(...) call count mismatch for allow-listed file(s):\n"
            + "\n".join(mismatches)
            + "\n\nIf you added a NEW call: confirm the new bypass site "
            "was intentional, add the WARN log next to it, and bump the "
            "allowlist count. If you removed one: lower the count. "
            "Audit P0-6."
        )
        self.assertEqual(mismatches, [], msg=msg)


class PitProviderAstCounterUnitTests(unittest.TestCase):
    """Sanity-check the AST counter so a future refactor of the
    scanner does not silently mis-count and let bypass regressions
    slip past the three governance tests above."""

    def test_counts_simple_D_features_call(self) -> None:
        src = "from qlib.data import D\nclose = D.features(['A'], ['$close'])\n"
        self.assertEqual(_count_d_features_calls_in_source(src), 1)

    def test_counts_multiple_calls(self) -> None:
        src = (
            "from qlib.data import D\n"
            "a = D.features(['A'], ['$close'])\n"
            "b = D.features(['B'], ['$open'])\n"
        )
        self.assertEqual(_count_d_features_calls_in_source(src), 2)

    def test_ignores_d_features_in_docstring(self) -> None:
        """Docstrings / comments mentioning ``D.features`` MUST NOT
        count — otherwise a comment update would mysteriously fail
        the governance test."""
        src = (
            '"""See ``D.features(...)`` for details."""\n'
            "# fallback: D.features(...)\n"
            "x = 1\n"
        )
        self.assertEqual(_count_d_features_calls_in_source(src), 0)

    def test_ignores_method_call_on_other_object(self) -> None:
        """``provider.features(...)`` is NOT a qlib bypass — only the
        bare ``D`` name receives this guard."""
        src = (
            "provider.features(['A'])\n"
            "obj.D.features(['A'])\n"  # attribute access, not Name
        )
        self.assertEqual(_count_d_features_calls_in_source(src), 0)

    def test_counts_when_D_is_local_shadow(self) -> None:
        """Even if ``D`` was reassigned locally, we still count —
        defensive: a deliberate shadow that calls ``.features`` should
        still surface as a bypass for review."""
        src = (
            "D = object()\n"
            "D.features(['A'])\n"
        )
        self.assertEqual(_count_d_features_calls_in_source(src), 1)


if __name__ == "__main__":
    unittest.main()

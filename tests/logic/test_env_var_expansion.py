"""Tests for ``${VAR}`` / ``${VAR:-default}`` expansion in the YAML loader.

Dimensional coverage matrix from
``openspec/changes/add-config-robustness``:

- ``${VAR}`` with VAR set → returns value
- ``${VAR}`` with VAR unset, no default → raises with VAR name + path
- ``${VAR:-default}`` with VAR set → returns VAR's value (not default)
- ``${VAR:-default}`` with VAR unset → returns default
- ``${VAR:-}`` (empty default) with VAR unset → returns ""
- ``"prefix-${VAR}-suffix"`` (nested) → keeps the bracketing text
- Multiple references in one string → all expanded
- env var inside a YAML *key* → NOT substituted (keys pass through)
- non-string scalars (int/bool/float/None) → pass through unchanged
- Literal-path YAML with no ``${...}`` → loads identically
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core._yaml_loader import (  # noqa: E402
    YamlEnvVarError,
    expand_env_vars,
    load_yaml_with_inheritance,
)


def _write_yaml(dirpath: Path, name: str, body: str) -> Path:
    """Write a YAML file under *dirpath* and return its path."""
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return p


def _set_env(name: str, value: str | None) -> None:
    """Set or unset an env var (None deletes)."""
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


# A namespace prefix kept unique to this test module so we never
# collide with real env vars an operator might have set in their
# shell while debugging.
_PREFIX = "TEST_CONFIG_ROBUSTNESS_"


class ExpandEnvVarsHelperTests(unittest.TestCase):
    """Direct unit tests on the ``expand_env_vars`` helper."""

    def setUp(self) -> None:
        # Snapshot env so each test can mutate freely
        self._env_snapshot = {
            k: v for k, v in os.environ.items() if k.startswith(_PREFIX)
        }
        # And wipe our namespace before each test
        for k in list(os.environ.keys()):
            if k.startswith(_PREFIX):
                del os.environ[k]

    def tearDown(self) -> None:
        for k in list(os.environ.keys()):
            if k.startswith(_PREFIX):
                del os.environ[k]
        os.environ.update(self._env_snapshot)

    def test_env_var_set_returns_value(self) -> None:
        _set_env(_PREFIX + "PROVIDER", "/data/bundle")
        self.assertEqual(
            expand_env_vars("${" + _PREFIX + "PROVIDER}"),
            "/data/bundle",
        )

    def test_env_var_unset_no_default_raises(self) -> None:
        with self.assertRaises(YamlEnvVarError) as ctx:
            expand_env_vars(
                "${" + _PREFIX + "MISSING}",
                source_path=Path("config_walk.yaml"),
            )
        msg = str(ctx.exception)
        self.assertIn(_PREFIX + "MISSING", msg)
        self.assertIn("config_walk.yaml", msg)

    def test_env_var_default_syntax_with_value_set(self) -> None:
        _set_env(_PREFIX + "PROVIDER", "/real")
        self.assertEqual(
            expand_env_vars("${" + _PREFIX + "PROVIDER:-/fallback}"),
            "/real",
        )

    def test_env_var_default_syntax_with_value_unset(self) -> None:
        self.assertEqual(
            expand_env_vars("${" + _PREFIX + "UNSET:-/fallback}"),
            "/fallback",
        )

    def test_env_var_default_syntax_empty_default(self) -> None:
        self.assertEqual(
            expand_env_vars("${" + _PREFIX + "UNSET:-}"),
            "",
        )

    def test_env_var_nested_in_larger_string(self) -> None:
        _set_env(_PREFIX + "ROOT", "/data")
        self.assertEqual(
            expand_env_vars("prefix-${" + _PREFIX + "ROOT}-suffix"),
            "prefix-/data-suffix",
        )

    def test_env_var_multiple_references_in_one_string(self) -> None:
        _set_env(_PREFIX + "A", "alpha")
        _set_env(_PREFIX + "B", "beta")
        self.assertEqual(
            expand_env_vars(
                "${" + _PREFIX + "A}/${" + _PREFIX + "B}"
            ),
            "alpha/beta",
        )

    def test_plain_string_without_dollar_is_unchanged(self) -> None:
        # Regression for "we don't accidentally rewrite literal strings".
        self.assertEqual(
            expand_env_vars("D:/qlib_data/my_cn_data"),
            "D:/qlib_data/my_cn_data",
        )

    def test_default_with_slashes_and_dashes(self) -> None:
        # Defaults often contain path separators and ISO dates — make
        # sure the regex doesn't accidentally swallow them.
        self.assertEqual(
            expand_env_vars(
                "${" + _PREFIX + "UNSET:-D:/qlib_data/my_cn_data}"
            ),
            "D:/qlib_data/my_cn_data",
        )

    def test_quant_production_default_keeps_drive_letter_colon(self) -> None:
        # Phase 1 P1-1: the production placeholders use a Windows ``D:/`` default.
        # The drive-letter colon is the ONLY real risk of the ${VAR:-default}
        # mechanism — lock that an unset env var expands to the FULL path with
        # the ``D:`` colon intact (and that a set var overrides it).
        # QUANT_PROVIDER_URI is a REAL (non-prefixed) var, so this class's
        # _PREFIX-only fixture would NOT restore it. Save + restore by hand
        # via try/finally so a process that starts with QUANT_PROVIDER_URI set
        # (a local / E2E run pointing at a non-default bundle) does not leak as
        # unset into later tests. ``os.environ.pop`` captures the original (the
        # helper can't return it); ``_set_env(name, saved)`` restores it —
        # None -> re-delete, str -> set back. (codex P3 on PR #229.)
        saved = os.environ.pop("QUANT_PROVIDER_URI", None)
        try:
            self.assertEqual(
                expand_env_vars("${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}"),
                "D:/qlib_data/my_cn_data_pit",
            )
            _set_env("QUANT_PROVIDER_URI", "E:/elsewhere/bundle")
            self.assertEqual(
                expand_env_vars("${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}"),
                "E:/elsewhere/bundle",
            )
        finally:
            _set_env("QUANT_PROVIDER_URI", saved)


class LoadYamlWithInheritanceEnvVarTests(unittest.TestCase):
    """End-to-end tests that go through the full YAML loader."""

    def setUp(self) -> None:
        self._env_snapshot = {
            k: v for k, v in os.environ.items() if k.startswith(_PREFIX)
        }
        for k in list(os.environ.keys()):
            if k.startswith(_PREFIX):
                del os.environ[k]

    def tearDown(self) -> None:
        for k in list(os.environ.keys()):
            if k.startswith(_PREFIX):
                del os.environ[k]
        os.environ.update(self._env_snapshot)

    def test_env_var_set_resolves_in_loaded_yaml(self) -> None:
        _set_env(_PREFIX + "PROVIDER", "/data/bundle")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "${' + _PREFIX + 'PROVIDER}"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(cfg, {"provider_uri": "/data/bundle"})

    def test_env_var_unset_no_default_raises_with_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "${' + _PREFIX + 'MISSING}"\n',
            )
            with self.assertRaises(YamlEnvVarError) as ctx:
                load_yaml_with_inheritance(p)
        msg = str(ctx.exception)
        self.assertIn(_PREFIX + "MISSING", msg)
        # The source path appears in the message
        self.assertIn("cfg.yaml", msg)

    def test_default_syntax_falls_back_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "${'
                + _PREFIX
                + 'UNSET:-D:/qlib_data/my_cn_data}"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(
            cfg, {"provider_uri": "D:/qlib_data/my_cn_data"}
        )

    def test_default_syntax_prefers_env_value(self) -> None:
        _set_env(_PREFIX + "PROVIDER", "/override")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "${'
                + _PREFIX
                + 'PROVIDER:-D:/qlib_data/my_cn_data}"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(cfg, {"provider_uri": "/override"})

    def test_env_var_nested_in_larger_string(self) -> None:
        _set_env(_PREFIX + "ROOT", "/data")
        _set_env(_PREFIX + "VINTAGE", "2026-03-06")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "${'
                + _PREFIX
                + 'ROOT}/csi300/${'
                + _PREFIX
                + 'VINTAGE}"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(
            cfg, {"provider_uri": "/data/csi300/2026-03-06"}
        )

    def test_env_var_no_substitution_for_keys(self) -> None:
        """Env-var syntax in YAML *keys* MUST pass through unchanged.

        We never want to rewrite keys, because the strict-unknown-key
        check in scripts/run_walk_forward.py depends on a stable
        key set. Allowing key rewriting would also encourage configs
        that look correct but resolve differently depending on env.
        """
        _set_env(_PREFIX + "SHOULD_NOT_RESOLVE", "evil")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                '"${'
                + _PREFIX
                + 'SHOULD_NOT_RESOLVE}": "value"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        # The literal ${...} key is preserved
        self.assertEqual(
            list(cfg.keys()),
            ["${" + _PREFIX + "SHOULD_NOT_RESOLVE}"],
        )
        self.assertEqual(
            cfg["${" + _PREFIX + "SHOULD_NOT_RESOLVE}"], "value"
        )

    def test_env_var_no_substitution_for_non_string_scalars(self) -> None:
        """Int, bool, float, None must pass through unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                "num_boost_round: 1000\n"
                "run_attribution: true\n"
                "learning_rate: 0.005\n"
                "industry_artifact_path: null\n",
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(cfg["num_boost_round"], 1000)
        self.assertIs(cfg["run_attribution"], True)
        self.assertEqual(cfg["learning_rate"], 0.005)
        self.assertIsNone(cfg["industry_artifact_path"])

    def test_literal_path_yaml_loads_identically(self) -> None:
        """Regression: a YAML with no ``${...}`` is parsed as before."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'provider_uri: "D:/qlib_data/my_cn_data"\n'
                'region: "cn"\n'
                'instruments: "csi300"\n'
                "topk: 50\n",
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(
            cfg,
            {
                "provider_uri": "D:/qlib_data/my_cn_data",
                "region": "cn",
                "instruments": "csi300",
                "topk": 50,
            },
        )

    def test_env_var_expansion_in_nested_dict_value(self) -> None:
        """Walk descends into nested mappings."""
        _set_env(_PREFIX + "VAL", "deep")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'outer:\n  inner: "${' + _PREFIX + 'VAL}"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(cfg, {"outer": {"inner": "deep"}})

    def test_env_var_expansion_in_list_element(self) -> None:
        """Walk descends into lists too."""
        _set_env(_PREFIX + "TICKER", "SH600519")
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_yaml(
                Path(tmp),
                "cfg.yaml",
                'tickers:\n  - "${' + _PREFIX + 'TICKER}"\n  - "SH600036"\n',
            )
            cfg = load_yaml_with_inheritance(p)
        self.assertEqual(cfg, {"tickers": ["SH600519", "SH600036"]})

    def test_env_var_with_extends_chain(self) -> None:
        """Each YAML file's values are expanded against its own path,
        and then the child layer is merged over the parent layer."""
        _set_env(_PREFIX + "PROVIDER", "/from_env")
        with tempfile.TemporaryDirectory() as tmp:
            parent = _write_yaml(
                Path(tmp),
                "parent.yaml",
                'provider_uri: "${' + _PREFIX + 'PROVIDER}"\nregion: "cn"\n',
            )
            child = _write_yaml(
                Path(tmp),
                "child.yaml",
                'extends: "parent.yaml"\nregion: "us"\n',
            )
            cfg = load_yaml_with_inheritance(child)
            self.assertEqual(parent.exists(), True)  # written
        self.assertEqual(
            cfg, {"provider_uri": "/from_env", "region": "us"}
        )


# ---------------------------------------------------------------------------
# Codex PR #149 P2 regression: extends-chain error attribution.
#
# Pre-fix: expansion ran once at the outermost call with
# source_path=child.yaml, so an unresolved ${VAR} from parent.yaml
# was reported as if it had come from child.yaml. After the fix each
# file's values are expanded against its own path before merging,
# so the YamlEnvVarError names the file that actually contains the
# unresolved placeholder.
# ---------------------------------------------------------------------------


class ExtendsErrorAttributionTests(unittest.TestCase):
    def test_unresolved_var_in_parent_names_parent_path(self) -> None:
        """Codex P2 anchor: ``${MISSING}`` in parent.yaml ⇒ error
        message must reference parent.yaml, not child.yaml."""
        var_name = _PREFIX + "DEFINITELY_NOT_SET_PARENT"
        os.environ.pop(var_name, None)
        with tempfile.TemporaryDirectory() as tmp:
            parent = _write_yaml(
                Path(tmp), "parent.yaml",
                f'data_root: "${{{var_name}}}"\nregion: "cn"\n',
            )
            child = _write_yaml(
                Path(tmp), "child.yaml",
                'extends: "parent.yaml"\nregion: "us"\n',
            )
            # ``load_yaml_with_inheritance`` calls ``Path.resolve()`` on
            # its inputs, which on Windows expands an 8.3 short-name
            # tempdir (``RUNNER~1``) to its full form (``runneradmin``).
            # Compare against the resolved form so the assertion is
            # stable across platforms.
            parent_resolved = str(parent.resolve())
            child_resolved = str(child.resolve())
            with self.assertRaises(YamlEnvVarError) as ctx:
                load_yaml_with_inheritance(child)
            msg = str(ctx.exception)
        self.assertIn(var_name, msg)
        self.assertIn(parent_resolved, msg)
        # Pre-fix the message named the child; the regression is that
        # the parent path must be present AND the child path must not
        # be the attributed source.
        self.assertNotIn(
            f"referenced by {child_resolved}", msg,
            "error should name parent.yaml as the source, not child.yaml",
        )

    def test_unresolved_var_in_child_names_child_path(self) -> None:
        """The mirror case: ``${MISSING}`` in child.yaml ⇒ error
        message names child.yaml. Anchors that the fix doesn't
        accidentally always blame the parent."""
        var_name = _PREFIX + "DEFINITELY_NOT_SET_CHILD"
        os.environ.pop(var_name, None)
        with tempfile.TemporaryDirectory() as tmp:
            parent = _write_yaml(
                Path(tmp), "parent.yaml",
                'region: "cn"\n',
            )
            child = _write_yaml(
                Path(tmp), "child.yaml",
                'extends: "parent.yaml"\n'
                f'data_root: "${{{var_name}}}"\n',
            )
            parent_resolved = str(parent.resolve())
            child_resolved = str(child.resolve())
            with self.assertRaises(YamlEnvVarError) as ctx:
                load_yaml_with_inheritance(child)
            msg = str(ctx.exception)
        self.assertIn(var_name, msg)
        self.assertIn(child_resolved, msg)
        self.assertNotIn(parent_resolved, msg)

    def test_three_level_chain_names_innermost_offender(self) -> None:
        """For ``grandchild → child → grandparent``, an unresolved
        ``${VAR}`` in ``grandparent.yaml`` must name the grandparent."""
        var_name = _PREFIX + "DEFINITELY_NOT_SET_GP"
        os.environ.pop(var_name, None)
        with tempfile.TemporaryDirectory() as tmp:
            gp = _write_yaml(
                Path(tmp), "grandparent.yaml",
                f'data_root: "${{{var_name}}}"\n',
            )
            ch = _write_yaml(
                Path(tmp), "child.yaml",
                'extends: "grandparent.yaml"\nregion: "cn"\n',
            )
            gc = _write_yaml(
                Path(tmp), "grandchild.yaml",
                'extends: "child.yaml"\nregion: "us"\n',
            )
            gp_resolved = str(gp.resolve())
            ch_resolved = str(ch.resolve())
            gc_resolved = str(gc.resolve())
            with self.assertRaises(YamlEnvVarError) as ctx:
                load_yaml_with_inheritance(gc)
            msg = str(ctx.exception)
        self.assertIn(gp_resolved, msg)
        self.assertNotIn(f"referenced by {ch_resolved}", msg)
        self.assertNotIn(f"referenced by {gc_resolved}", msg)


if __name__ == "__main__":
    unittest.main()

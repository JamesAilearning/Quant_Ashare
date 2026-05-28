"""Regression tests for operator UI preset helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web.operator_ui.config_presets import (
    BUILT_IN_PRESET_NAMES,
    CUSTOM_PRESET_NAME,
    clear_preset_caches,
    list_preset_names,
    load_preset,
    sanitise_preset_name,
)


class ConfigPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test starts with a fresh cache so the others' on-disk
        # fixtures don't leak through the LRU lookup.
        clear_preset_caches()

    def test_list_preset_names_includes_saved_presets_before_custom(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "default.yaml").write_text("mode: pipeline\n", encoding="utf-8")
            (presets_dir / "my_preset.yaml").write_text("mode: pipeline\n", encoding="utf-8")

            self.assertEqual(
                list_preset_names(presets_dir),
                (*BUILT_IN_PRESET_NAMES, "my_preset", CUSTOM_PRESET_NAME),
            )

    def test_load_preset_sanitises_name_before_path_lookup(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "escape.yaml").write_text("mode: pipeline\n", encoding="utf-8")

            self.assertEqual(load_preset(presets_dir, "../escape"), {"mode": "pipeline"})

    def test_sanitise_preset_name_rejects_empty_names(self) -> None:
        self.assertEqual(sanitise_preset_name("../../"), "")


class PresetCacheHitTests(unittest.TestCase):
    """Pin the UI review P1-4 fix: repeated calls with unchanged inputs
    MUST be served from the in-process LRU cache rather than re-reading
    YAML from disk on every Streamlit rerun."""

    def setUp(self) -> None:
        clear_preset_caches()

    def test_load_preset_reads_file_once_for_repeated_identical_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "default.yaml").write_text(
                "mode: pipeline\ntopk: 50\n", encoding="utf-8"
            )

            # Spy on the read_text call BEFORE invoking the loader so we
            # see every disk read. We don't need to fake the result —
            # the spy delegates to the real implementation.
            real_read_text = Path.read_text
            calls: list[str] = []

            def _spy(self: Path, *args: object, **kwargs: object) -> str:
                if self.suffix == ".yaml":
                    calls.append(str(self))
                return real_read_text(self, *args, **kwargs)

            with patch.object(Path, "read_text", _spy):
                first = load_preset(presets_dir, "default")
                second = load_preset(presets_dir, "default")
                third = load_preset(presets_dir, "default")

        self.assertEqual(first, {"mode": "pipeline", "topk": 50})
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        # Exactly one disk read across three calls.
        self.assertEqual(
            len(calls), 1,
            f"expected 1 read for 3 cached calls; saw {len(calls)}: {calls}",
        )

    def test_list_preset_names_does_not_reglob_when_directory_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "default.yaml").write_text("mode: pipeline\n", encoding="utf-8")

            real_glob = Path.glob
            calls: list[str] = []

            def _spy(self: Path, pattern: str) -> object:
                if pattern == "*.yaml":
                    calls.append(str(self))
                return real_glob(self, pattern)

            with patch.object(Path, "glob", _spy):
                first = list_preset_names(presets_dir)
                second = list_preset_names(presets_dir)
                third = list_preset_names(presets_dir)

        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(
            len(calls), 1,
            f"expected 1 glob for 3 cached calls; saw {len(calls)}: {calls}",
        )

    def test_load_preset_returns_distinct_dict_instances_per_call(self) -> None:
        """Caller MUST get a fresh dict per call so mutating the
        returned mapping cannot pollute the cache. The helper hands
        back a tuple-of-items from the LRU layer and rebuilds a dict
        each time."""

        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            (presets_dir / "default.yaml").write_text(
                "mode: pipeline\n", encoding="utf-8"
            )

            first = load_preset(presets_dir, "default")
            second = load_preset(presets_dir, "default")
            self.assertIsNot(first, second)
            first["mode"] = "MUTATED"
            # Mutation does NOT leak into the next call — the underlying
            # tuple-of-items cache is immutable, and ``dict(items)``
            # rebuilds a fresh mapping each invocation.
            third = load_preset(presets_dir, "default")
        self.assertEqual(third["mode"], "pipeline")


class PresetCacheInvalidationTests(unittest.TestCase):
    """Cache MUST invalidate when the on-disk source changes — file
    mtime bump (operator saved a preset, edited a YAML, etc.) or
    deletion. No TTL guess: the cache key includes the mtime."""

    def setUp(self) -> None:
        clear_preset_caches()

    def test_load_preset_rereads_when_file_mtime_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            target = presets_dir / "default.yaml"
            target.write_text("topk: 50\n", encoding="utf-8")

            first = load_preset(presets_dir, "default")
            self.assertEqual(first, {"topk": 50})

            # Rewrite with new content + bump mtime so the cache key
            # changes. ``utime`` makes the mtime change observable
            # without waiting for clock granularity.
            target.write_text("topk: 100\n", encoding="utf-8")
            new_mtime = target.stat().st_mtime + 5.0
            os.utime(target, (new_mtime, new_mtime))

            second = load_preset(presets_dir, "default")
        self.assertEqual(second, {"topk": 100})

    def test_list_preset_names_picks_up_new_saved_preset(self) -> None:
        """When the operator saves a new custom preset, the next call
        to ``list_preset_names`` MUST include it — the directory mtime
        bump invalidates the cached glob (UI review P1-4 expectation:
        no 60-second TTL wait)."""

        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)
            first = list_preset_names(presets_dir)
            self.assertNotIn("new_preset", first)

            # Add a new preset. Bump dir mtime explicitly in case the
            # filesystem's mtime granularity hides the change otherwise.
            (presets_dir / "new_preset.yaml").write_text(
                "topk: 7\n", encoding="utf-8"
            )
            new_mtime = presets_dir.stat().st_mtime + 5.0
            os.utime(presets_dir, (new_mtime, new_mtime))

            second = list_preset_names(presets_dir)
        self.assertIn("new_preset", second)

    def test_load_preset_treats_missing_file_consistently(self) -> None:
        """Asking for a non-existent preset returns ``{}``; same input
        is cached, but creating the file with a later mtime breaks
        the cache as expected."""

        with TemporaryDirectory() as tmp:
            presets_dir = Path(tmp)

            self.assertEqual(load_preset(presets_dir, "future"), {})
            # Same call again hits the cache. (Implementation detail
            # we care about: it doesn't matter whether the second call
            # stats the file or not, as long as the next ``stat`` shows
            # the file appearing and that bumps the mtime.)
            self.assertEqual(load_preset(presets_dir, "future"), {})

            target = presets_dir / "future.yaml"
            target.write_text("topk: 99\n", encoding="utf-8")
            new_mtime = target.stat().st_mtime + 5.0
            os.utime(target, (new_mtime, new_mtime))

            # File now exists; mtime changed; cache should refresh.
            self.assertEqual(
                load_preset(presets_dir, "future"),
                {"topk": 99},
            )


if __name__ == "__main__":
    unittest.main()

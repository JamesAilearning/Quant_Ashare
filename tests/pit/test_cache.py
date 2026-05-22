"""Tests for ``src.pit.cache.LRUCache``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pit.cache import LRUCache  # noqa: E402


class LRUCacheTests(unittest.TestCase):

    def test_get_missing_returns_none(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=4)
        self.assertIsNone(c.get("absent"))

    def test_put_then_get(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=4)
        c.put("k", 7)
        self.assertEqual(c.get("k"), 7)
        self.assertEqual(len(c), 1)

    def test_eviction_when_over_capacity(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=3)
        for k in ("a", "b", "c", "d"):
            c.put(k, ord(k))
        self.assertEqual(len(c), 3)
        # 'a' was inserted first and never touched -> evicted
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), ord("b"))

    def test_get_marks_recently_used(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=3)
        c.put("a", 1)
        c.put("b", 2)
        c.put("c", 3)
        # Touch 'a' so 'b' becomes LRU
        self.assertEqual(c.get("a"), 1)
        c.put("d", 4)
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("b"))  # evicted
        self.assertEqual(c.get("c"), 3)
        self.assertEqual(c.get("d"), 4)

    def test_put_updates_existing(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=3)
        c.put("k", 1)
        c.put("k", 2)
        self.assertEqual(len(c), 1)
        self.assertEqual(c.get("k"), 2)

    def test_contains(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=3)
        c.put("k", 1)
        self.assertIn("k", c)
        self.assertNotIn("absent", c)

    def test_clear(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=3)
        c.put("a", 1)
        c.put("b", 2)
        c.clear()
        self.assertEqual(len(c), 0)
        self.assertNotIn("a", c)

    def test_zero_maxsize_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "maxsize must be >= 1"):
            LRUCache(maxsize=0)

    def test_maxsize_property(self) -> None:
        c: LRUCache[str, int] = LRUCache(maxsize=42)
        self.assertEqual(c.maxsize, 42)


if __name__ == "__main__":
    unittest.main()

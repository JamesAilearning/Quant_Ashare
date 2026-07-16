"""Unit tests for the mechanical-move drift detector (backlog #1).

Coverage matrix (>=1 case per dimension, each mirroring a REAL incident
class from AGENTS.md):
  content diff   — decorator row lost in the move shows up as ONLY-IN-OLD
                   (the awk-by-symbol blind spot); identical move = empty.
  AST: decorator — lost @dataclass(frozen=True) flagged (provider_bundle
                   incident, 17 broken tests); ADDED @cache flagged too,
                   even in line-tolerant merge mode (codex #364 r5).
  rename+extract — R target + A helper reconstructed from the rename
                   residual and certified strict (codex #364 r5).
  AST: except    — newly added broad `except Exception` flagged
                   (walk_forward _run_attribution_for_fold incident).
  AST: signature — dropped keyword-only marker / swapped params flagged.
  AST: lost def  — a function dropped in the split flagged.
  split concat   — a clean two-file split of one module passes both checks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.verify_mechanical_move import (  # noqa: E402
    compare_module_texts,
    content_diff,
)

_OLD = '''\
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    name: str


def run(a, *, flag=False):
    try:
        return a
    except ValueError:
        raise


def helper(x):
    return x + 1
'''


def test_clean_identical_move_passes_both_checks():
    only_old, only_new = content_diff(_OLD, [_OLD])
    assert only_old == [] and only_new == []
    assert compare_module_texts(_OLD, _OLD) == []


def test_clean_two_file_split_passes():
    part1 = ("from dataclasses import dataclass\n\n\n"
             "@dataclass(frozen=True)\nclass Config:\n    name: str\n")
    part2 = ("def run(a, *, flag=False):\n    try:\n        return a\n"
             "    except ValueError:\n        raise\n\n\n"
             "def helper(x):\n    return x + 1\n")
    only_old, only_new = content_diff(_OLD, [part1, part2])
    assert only_old == [] and only_new == []
    assert compare_module_texts(_OLD, part1 + "\n" + part2) == []


def test_lost_frozen_dataclass_decorator_flagged():
    new = _OLD.replace("@dataclass(frozen=True)\n", "")
    only_old, _ = content_diff(_OLD, [new])
    assert any("@dataclass(frozen=True)" in line for line in only_old)
    findings = compare_module_texts(_OLD, new)
    assert any("DECORATOR drift" in f and "Config" in f for f in findings)


def test_added_decorator_flagged_even_in_merge_mode():
    # codex #364 r5 P1: @cache added on a moved function is a behavior
    # change; the AST layer must fail it even where merge mode tolerates
    # ONLY-IN-NEW lines.
    from scripts.verify_mechanical_move import _verify_one
    new = _OLD.replace("def run(a, *, flag=False):",
                       "@cache\ndef run(a, *, flag=False):")
    findings = compare_module_texts(_OLD, new)
    assert any("DECORATOR drift" in f and "run" in f for f in findings)
    assert _verify_one("merge-with-cache", _OLD, [new],
                       fail_on_only_new=False) == 1


def test_new_broad_except_flagged():
    new = _OLD.replace("    except ValueError:\n        raise\n",
                       "    except Exception:\n        return None\n")
    findings = compare_module_texts(_OLD, new)
    assert any("NEW broad except" in f for f in findings)


def test_signature_drift_flagged():
    # dropped keyword-only marker: flag becomes positional
    new = _OLD.replace("def run(a, *, flag=False):", "def run(a, flag=False):")
    findings = compare_module_texts(_OLD, new)
    assert any("SIGNATURE changed" in f and "run" in f for f in findings)


def test_lost_function_flagged():
    new = _OLD.replace("def helper(x):\n    return x + 1\n", "")
    findings = compare_module_texts(_OLD, new)
    assert any("LOST def/class: helper" in f for f in findings)


def test_find_split_destinations_matches_three_way_split():
    # codex #364 r3 P1: a 1->3 split falls below -M50% per destination;
    # the overlap matcher must recover all three from the added set.
    from scripts.verify_mechanical_move import find_split_destinations
    part1 = ("from dataclasses import dataclass\n\n\n"
             "@dataclass(frozen=True)\nclass Config:\n    name: str\n")
    part2 = ("def run(a, *, flag=False):\n    try:\n        return a\n"
             "    except ValueError:\n        raise\n")
    part3 = ("def helper(x):\n    return x + 1\n")
    added = {"a.py": part1, "b.py": part2, "c.py": part3,
             "unrelated.py": "def other():\n    return 42\n"}
    dests = find_split_destinations(_OLD, added)
    assert dests == ["a.py", "b.py", "c.py"]   # unrelated excluded


def test_rename_plus_extract_residual_matches_helper():
    # codex #364 r5 P1: `R old.py main.py` + `A helpers.py` — the residual
    # the rename target does not cover must recover helpers.py, and the
    # reconstructed strict verify must certify the clean split.
    from scripts.verify_mechanical_move import (
        _verify_one,
        filtered_lines,
        find_split_destinations,
    )
    main_part = _OLD.replace("def helper(x):\n    return x + 1\n", "")
    helper_part = "def helper(x):\n    return x + 1\n"
    residual = filtered_lines(_OLD) - filtered_lines(main_part)
    dests = find_split_destinations(
        residual, {"helpers.py": helper_part,
                   "unrelated.py": "def other():\n    return 1\n"})
    assert dests == ["helpers.py"]
    assert _verify_one("rename+extract", _OLD, [main_part, helper_part],
                       fail_on_only_new=True) == 0


def test_find_split_destinations_genuine_deletion_matches_nothing():
    from scripts.verify_mechanical_move import find_split_destinations
    added = {"unrelated.py": "def totally_new():\n    return 'x'\n"}
    assert find_split_destinations(_OLD, added) == []


def test_split_with_duplicate_future_imports_certifies_clean():
    # codex #364 r4 P2: each destination legally starts with
    # `from __future__ import annotations`; concatenation would be a
    # SyntaxError — per-file parsing must certify the clean split.
    old = ("from __future__ import annotations\n"
           "def a(x):\n    return x\n\n"
           "def b(y):\n    return y\n")
    p1 = "from __future__ import annotations\ndef a(x):\n    return x\n"
    p2 = "from __future__ import annotations\ndef b(y):\n    return y\n"
    from scripts.verify_mechanical_move import compare_module_texts as cmt
    assert cmt(old, [p1, p2]) == []


def test_verify_one_merge_move_tolerates_preexisting_lines():
    # codex #364 r4 P2: a merge destination is a MODIFIED existing module;
    # its pre-existing lines are expected ONLY-IN-NEW, not drift — but
    # lost lines / AST findings still fail.
    from scripts.verify_mechanical_move import _verify_one
    merged = _OLD + "\n\ndef preexisting(z):\n    return z * 2\n"
    assert _verify_one("merge", _OLD, [merged],
                       fail_on_only_new=False) == 0
    assert _verify_one("rename", _OLD, [merged],
                       fail_on_only_new=True) == 1
    lossy = merged.replace("def helper(x):\n    return x + 1\n", "")
    assert _verify_one("lossy-merge", _OLD, [lossy],
                       fail_on_only_new=False) == 1


def test_unparsable_input_fails_loud():
    from scripts.verify_mechanical_move import VerifyError
    with pytest.raises(VerifyError, match="cannot parse"):
        compare_module_texts("def broken(:", "x = 1\n")

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
  merge / mixed  — MODIFIED destination's own base subtracted; strict
                   proof survives an A+M union (codex #364 r6).
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


def test_added_decorator_flagged():
    # codex #364 r5 P1: @cache added on a moved function is a behavior
    # change — AST DECORATOR drift, same severity as a lost decorator.
    from scripts.verify_mechanical_move import _verify_one
    new = _OLD.replace("def run(a, *, flag=False):",
                       "@cache\ndef run(a, *, flag=False):")
    findings = compare_module_texts(_OLD, new)
    assert any("DECORATOR drift" in f and "run" in f for f in findings)
    assert _verify_one("with-cache", _OLD, [(new, None)]) == 1


def test_decorator_shuffle_caught_by_ast_when_line_layer_blind():
    # the merge base already contains an identical `@cache` row that the
    # destination moved onto the migrated function — the line-layer
    # delta cancels exactly, so only the AST layer can catch the drift.
    from scripts.verify_mechanical_move import _verify_one
    base = "@cache\ndef preexisting(z):\n    return z * 2\n"
    merged = (_OLD.replace("def run(a, *, flag=False):",
                           "@cache\ndef run(a, *, flag=False):")
              + "\n\ndef preexisting(z):\n    return z * 2\n")
    assert _verify_one("cache-shuffle", _OLD, [(merged, base)]) == 1


def test_new_broad_except_flagged():
    new = _OLD.replace("    except ValueError:\n        raise\n",
                       "    except Exception:\n        return None\n")
    findings = compare_module_texts(_OLD, new)
    assert any("NEW broad except" in f for f in findings)


def test_tuple_form_broad_except_flagged():
    # codex #364 r8 P2: `except (ValueError, Exception):` is a catch-all
    # too — tuple form (nested included) must count per scope.
    new = _OLD.replace("    except ValueError:\n",
                       "    except (ValueError, Exception):\n")
    findings = compare_module_texts(_OLD, new)
    assert any("NEW broad except" in f and "run" in f for f in findings)
    nested = _OLD.replace("    except ValueError:\n",
                          "    except ((ValueError, Exception),):\n")
    assert any("NEW broad except" in f
               for f in compare_module_texts(_OLD, nested))


def test_qualified_broad_except_flagged():
    # codex #364 r9 P2: `except builtins.Exception:` is a catch-all too —
    # the attribute form must count in the per-scope accounting.
    new = _OLD.replace("    except ValueError:\n",
                       "    except builtins.Exception:\n")
    findings = compare_module_texts(_OLD, new)
    assert any("NEW broad except" in f and "run" in f for f in findings)


def test_broad_handler_relocation_between_functions_flagged():
    # codex #364 r7 P2: alpha Exception->TypeError while beta goes the
    # opposite way — filtered-line multiset, signatures and the AGGREGATE
    # broad count are all unchanged; per-scope accounting must still flag
    # beta's newly gained catch-all (and must NOT flag alpha).
    old = ("def alpha(x):\n    try:\n        return x\n"
           "    except Exception:\n        raise\n\n\n"
           "def beta(y):\n    try:\n        return y\n"
           "    except TypeError:\n        raise\n")
    new = ("def alpha(x):\n    try:\n        return x\n"
           "    except TypeError:\n        raise\n\n\n"
           "def beta(y):\n    try:\n        return y\n"
           "    except Exception:\n        raise\n")
    only_old, only_new = content_diff(old, [new])
    assert only_old == [] and only_new == []   # the line layer is blind
    findings = compare_module_texts(old, new)
    assert any("NEW broad except" in f and "beta" in f for f in findings)
    assert not any("alpha" in f for f in findings)


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
    assert _verify_one("rename+extract", _OLD,
                       [(main_part, None), (helper_part, None)]) == 0


def test_rename_target_excluded_from_residual_candidates():
    # codex #364 r10: two functions sharing duplicate call rows — an
    # UNFILTERED candidate map would pull the rename target itself in as
    # an extra (self-double-count -> false ONLY-IN-NEW); the call site
    # excludes new_path, and the reconstructed verify stays clean.
    from scripts.verify_mechanical_move import (
        _verify_one,
        filtered_lines,
        find_split_destinations,
    )
    fa = "def a():\n    setup()\n    log()\n    return 1\n"
    fb = "def b():\n    setup()\n    log()\n    return 2\n"
    old = fa + "\n\n" + fb
    residual = filtered_lines(old) - filtered_lines(fa)
    hazard = find_split_destinations(
        residual, {"main.py": fa, "helpers.py": fb})
    assert "main.py" in hazard   # the hazard is real when unfiltered
    assert find_split_destinations(
        residual, {"helpers.py": fb}) == ["helpers.py"]
    assert _verify_one("rename+extract", old,
                       [(fa, None), (fb, None)]) == 0
    assert _verify_one("self-double-count", old,
                       [(fa, None), (fb, None), (fa, None)]) == 1


def test_find_split_destinations_genuine_deletion_matches_nothing():
    from scripts.verify_mechanical_move import find_split_destinations
    added = {"unrelated.py": "def totally_new():\n    return 'x'\n"}
    assert find_split_destinations(_OLD, added) == []


def test_delete_and_merge_with_cancelling_rows_probed_by_def_names():
    # codex #364 r12 P2: every moved ROW already existed in the merge
    # destination's base (dedup-merge: `other` replaced by the moved
    # `helper`) — the line delta cancels to below the floor, so the
    # def-name fallback must find the destination and verification must
    # run LOUD instead of silently reporting a genuine deletion.
    from scripts.verify_mechanical_move import (
        _verify_one,
        filtered_lines,
        find_move_destinations_by_new_defs,
        find_split_destinations,
    )
    old = "def helper(x):\n    validate()\n    return x\n"
    base = "def other(w):\n    validate()\n    return x\n"
    new = "def helper(x):\n    validate()\n    return x\n"
    delta = filtered_lines(new) - filtered_lines(base)
    assert find_split_destinations(old, {"mod.py": delta}) == []  # blind
    assert find_move_destinations_by_new_defs(
        old, {"mod.py": new}, {"mod.py": base}) == ["mod.py"]
    # dedup-merge is not line-provable: the verify runs and fails LOUD
    # (operator justifies via --old/--new or the PR body), never silent.
    assert _verify_one("dedup-merge", old, [(new, base)]) == 1


def test_def_name_fallback_ignores_preexisting_same_name_def():
    # a same-name def already in the candidate's BASE is not move
    # evidence — otherwise the r11 false-positive reopens.
    from scripts.verify_mechanical_move import (
        find_move_destinations_by_new_defs,
    )
    old = "def helper(x):\n    return x\n"
    unchanged = "def helper(y):\n    return y * 2\n\n\nDONE = True\n"
    assert find_move_destinations_by_new_defs(
        old, {"mod.py": unchanged}, {"mod.py": unchanged}) == []


def test_genuine_deletion_probed_against_modified_delta_not_full_text():
    # codex #364 r11 P2: an unrelated MODIFIED file whose BASE already
    # shares two rows with a deleted 3-row helper — probing full text
    # fakes split coverage (then base subtraction fails the "move");
    # probing the base-subtracted delta reports a genuine deletion.
    from scripts.verify_mechanical_move import (
        filtered_lines,
        find_split_destinations,
    )
    deleted = "def tiny(q):\n    setup()\n    log()\n"
    base = "def other(w):\n    setup()\n    log()\n    return w\n"
    modified = base + "\n\ndef added(v):\n    return v\n"
    assert find_split_destinations(
        deleted, {"mod.py": modified}) == ["mod.py"]   # the hazard
    delta = filtered_lines(modified) - filtered_lines(base)
    assert find_split_destinations(deleted, {"mod.py": delta}) == []


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


def test_merge_destination_base_subtracted():
    # codex #364 r4+r6: a merge destination is a MODIFIED existing
    # module; its OWN base lines are subtracted (never blanket-tolerated)
    # — clean merge passes, lost lines still fail, and without the base
    # the pre-existing lines correctly read as drift.
    from scripts.verify_mechanical_move import _verify_one
    base = "def preexisting(z):\n    return z * 2\n"
    merged = _OLD + "\n\n" + base
    assert _verify_one("merge", _OLD, [(merged, base)]) == 0
    assert _verify_one("rename", _OLD, [(merged, None)]) == 1
    lossy = merged.replace("def helper(x):\n    return x + 1\n", "")
    assert _verify_one("lossy-merge", _OLD, [(lossy, base)]) == 1


def test_mixed_merge_keeps_fresh_destination_strict():
    # codex #364 r6 P1: union of a MODIFIED merge destination and a
    # fresh ADDED file — a side effect smuggled into the FRESH file must
    # still fail; only the modified file's own base is subtracted.
    from scripts.verify_mechanical_move import _verify_one
    base = "def preexisting(z):\n    return z * 2\n"
    merged = base + "\n\ndef helper(x):\n    return x + 1\n"
    clean_fresh = _OLD.replace("def helper(x):\n    return x + 1\n", "")
    assert _verify_one("mixed-clean", _OLD,
                       [(clean_fresh, None), (merged, base)]) == 0
    sneaky = clean_fresh + "\nSNEAKY_SIDE_EFFECT = object()\n"
    assert _verify_one("mixed-sneaky", _OLD,
                       [(sneaky, None), (merged, base)]) == 1


def test_merge_base_broad_except_not_counted_as_new():
    # a merge destination whose BASE already had a broad handler must not
    # read as a newly added one (base-delta accounting, codex #364 r6).
    base = ("def legacy(q):\n    try:\n        return q\n"
            "    except Exception:\n        return None\n")
    merged = _OLD + "\n\n" + base
    assert compare_module_texts(_OLD, [merged], base_texts=[base]) == []
    assert any("NEW broad except" in f
               for f in compare_module_texts(_OLD, [merged]))


def test_unparsable_input_fails_loud():
    from scripts.verify_mechanical_move import VerifyError
    with pytest.raises(VerifyError, match="cannot parse"):
        compare_module_texts("def broken(:", "x = 1\n")

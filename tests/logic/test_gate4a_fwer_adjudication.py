"""Unit tests for the Gate-4A full-batch FWER adjudication mechanics.

Coverage matrix (>=1 case per dimension):
  series loading    — primary-only extraction; duplicate fold fails loud.
  exclude_fold_0    — derived from C1 minus fold 0; missing fold 0 refuses.
  observed t        — hand-checked value; sliver/zero-variance refuse.
  joint bootstrap   — deterministic under the pinned seed; sparse trials
                      (annual-like index sets) survive via redraws.
  adjudication      — a planted strong positive family PASSES both the
                      bootstrap bar and the 2.85 floor; an all-negative
                      family returns CLEAN_NEGATIVE; a positive-but-weak
                      family is stopped by the floor.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.gate4a_fwer_adjudication import (  # noqa: E402
    FwerError,
    adjudicate,
    derive_exclude_fold0,
    load_trial_series,
    mbb_max_t,
    observed_t,
)


def test_load_trial_series_primary_only_and_duplicate_guard(tmp_path):
    good = {"folds": [
        {"fold": 0, "stamp_kind": "primary", "rank_ic": 0.1},
        {"fold": 1, "stamp_kind": "tail", "rank_ic": 9.9},
        {"fold": 1, "stamp_kind": "primary", "rank_ic": -0.2},
    ]}
    p = tmp_path / "result.json"
    p.write_text(json.dumps(good), encoding="utf-8")
    assert load_trial_series(p) == {0: 0.1, 1: -0.2}
    dup = {"folds": [
        {"fold": 0, "stamp_kind": "primary", "rank_ic": 0.1},
        {"fold": 0, "stamp_kind": "primary", "rank_ic": 0.2},
    ]}
    p2 = tmp_path / "dup.json"
    p2.write_text(json.dumps(dup), encoding="utf-8")
    with pytest.raises(FwerError, match="duplicate primary fold"):
        load_trial_series(p2)


def test_derive_exclude_fold0():
    c1 = {0: 0.4, 1: 0.1, 2: -0.1}
    assert derive_exclude_fold0(c1) == {1: 0.1, 2: -0.1}
    with pytest.raises(FwerError, match="fold 0"):
        derive_exclude_fold0({1: 0.1, 2: 0.2, 3: 0.3})


def test_observed_t_hand_value_and_guards():
    series = {0: 0.05, 1: 0.03, 2: -0.01, 3: 0.05}
    v = np.array([0.05, 0.03, -0.01, 0.05])
    expected = v.mean() / (v.std(ddof=1) / 2)
    assert observed_t(series) == pytest.approx(float(expected))
    with pytest.raises(FwerError, match="only 1"):
        observed_t({0: 0.1})
    with pytest.raises(FwerError, match="zero-variance"):
        observed_t({0: 0.1, 1: 0.1, 2: 0.1})


def _family(strong: float) -> dict[str, dict[int, float]]:
    rng = np.random.default_rng(7)
    fam: dict[str, dict[int, float]] = {}
    for k in range(3):
        fam[f"noise{k}"] = {i: float(x) for i, x in
                            enumerate(rng.normal(0.0, 0.05, 19))}
    fam["signal"] = {i: float(strong + x) for i, x in
                     enumerate(rng.normal(0.0, 0.01, 19))}
    fam["sparse_annual"] = {i: float(x) for i, x in
                            zip([0, 4, 8, 12],
                                rng.normal(0.0, 0.05, 4), strict=True)}
    return fam


def test_mbb_is_deterministic_and_handles_sparse_trials():
    fam = _family(0.0)
    d1, r1 = mbb_max_t(fam, n_boot=200, seed=123)
    d2, r2 = mbb_max_t(fam, n_boot=200, seed=123)
    assert np.array_equal(d1, d2) and r1 == r2
    d3, _ = mbb_max_t(fam, n_boot=200, seed=124)
    assert not np.array_equal(d1, d3)


def test_adjudicate_pass_negative_and_floor():
    # planted strong signal: mean 0.08, sd~0.01 over 19 folds -> t ~ 35
    res = adjudicate(_family(0.08))
    assert res["family_verdict_input"] == "PASS"
    assert res["passing_trials"] == ["signal"]
    # all-noise family: clean negative
    res2 = adjudicate(_family(0.0))
    assert res2["family_verdict_input"] == "CLEAN_NEGATIVE"
    assert res2["passing_trials"] == []
    # positive but weak (t < 2.85 floor even if it edges the bootstrap bar)
    fam3 = _family(0.0)
    fam3["weak"] = {i: float(0.004 + x) for i, x in
                    enumerate(np.random.default_rng(9).normal(0, 0.03, 19))}
    res3 = adjudicate(fam3)
    assert "weak" not in res3["passing_trials"]

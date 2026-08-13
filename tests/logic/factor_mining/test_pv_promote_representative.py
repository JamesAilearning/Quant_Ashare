"""Tests for the PV-DP-7 representative promotion tool.

The tool's whole job is to refuse: everything except an
operator-chosen candidate that actually survived the frozen FWER
mechanism, drawn from the very registration that was adjudicated,
must fail closed and write nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.research.pv_incremental_promote_representative import (  # noqa: E402
    PVPromoteError,
    load_verdict,
    locate_pool_entry,
    select_registered_candidate,
)
from scripts.research.pv_incremental_register_candidates import (  # noqa: E402
    candidate_id_for,
)
from src.factor_mining.expression import OperatorCall, Terminal  # noqa: E402
from src.factor_mining.factor_pool import (  # noqa: E402
    FactorPool,
    PoolEntry,
)

_EXPR = OperatorCall("cs_demean", (Terminal("$turnover_rate"),))
_EXPR_STR = _EXPR.to_qlib_string()
_SURVIVOR_ID = candidate_id_for(1, _EXPR_STR)


def _sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload) -> Path:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="")
    return path


def _manifest(tmp_path: Path, *, expression: str = _EXPR_STR,
              candidate_id: str = _SURVIVOR_ID) -> Path:
    return _write_json(tmp_path / "candidates.json", [
        {"candidate_id": candidate_id,
         "expression": expression,
         "orientation": -1},
        {"candidate_id": candidate_id_for(2, "cs_rank($volume)"),
         "expression": "cs_rank($volume)",
         "orientation": 1},
    ])


def _verdict(tmp_path: Path, *, verdict: str = "survivors",
             survivors=(_SURVIVOR_ID,),
             manifest_sha: str | None = None) -> Path:
    payload = {
        "protocol_id": "pv_incremental_v1",
        "verdict": verdict,
        "survivors": list(survivors),
    }
    if manifest_sha is not None:
        payload["registration_manifest_sha256"] = manifest_sha
    return _write_json(tmp_path / "fwer_verdict.json", payload)


def _pool_dir(tmp_path: Path) -> Path:
    pool = FactorPool()
    pool.add(PoolEntry(
        expr=_EXPR, fitness=0.0269, ic_mean=0.02, ic_std=0.1, ir=0.2,
        rank_ic_mean=0.033, rank_ic_std=0.1, rank_ir=0.3,
        turnover_daily=0.1, coverage=0.95, n_obs_per_day_min=300,
        expr_size=3, expr_hash=hash(_EXPR), orientation=-1))
    d = tmp_path / "run"
    d.mkdir()
    pool.save(d)
    return d


# ---------------------------------------------------------------------------
# Verdict gate
# ---------------------------------------------------------------------------


def test_verdict_digest_mismatch_refuses(tmp_path):
    path = _verdict(tmp_path)
    with pytest.raises(PVPromoteError, match="ledger records"):
        load_verdict(path, "0" * 64)


def test_non_survivors_verdict_refuses(tmp_path):
    path = _verdict(tmp_path, verdict="clean_negative", survivors=[])
    with pytest.raises(PVPromoteError, match="not 'survivors'"):
        load_verdict(path, _sha256_of(path))


def test_unpinned_verdict_refuses(tmp_path):
    # codex #422 r1: output/ is gitignored, so an OPTIONAL digest pin is
    # no pin — a locally regenerated verdict could authorize the bundle
    # and then supply its own digest as the provenance.
    path = _verdict(tmp_path)
    for absent in (None, "", "not-a-digest"):
        with pytest.raises(PVPromoteError, match="64-hex sha256"):
            load_verdict(path, absent)


def test_survivors_verdict_loads(tmp_path):
    path = _verdict(tmp_path)
    loaded = load_verdict(path, _sha256_of(path))
    assert loaded["survivors"] == [_SURVIVOR_ID]
    assert loaded["_actual_sha256"] == _sha256_of(path)


# ---------------------------------------------------------------------------
# Survivorship + registration binding
# ---------------------------------------------------------------------------


def test_non_survivor_id_refuses(tmp_path):
    manifest = _manifest(tmp_path)
    other = candidate_id_for(2, "cs_rank($volume)")
    with pytest.raises(PVPromoteError, match="survivor list"):
        select_registered_candidate(manifest, other, [_SURVIVOR_ID], None)


def test_manifest_that_was_not_adjudicated_refuses(tmp_path):
    manifest = _manifest(tmp_path)
    with pytest.raises(PVPromoteError, match="adjudicated against"):
        select_registered_candidate(
            manifest, _SURVIVOR_ID, [_SURVIVOR_ID], "a" * 64)


def test_expression_swapped_under_a_surviving_id_refuses(tmp_path):
    # The id stays on the survivor list but its expression was edited:
    # re-deriving the id from the expression catches it.
    manifest = _manifest(tmp_path, expression="cs_rank($close)")
    with pytest.raises(PVPromoteError, match="edited after registration"):
        select_registered_candidate(
            manifest, _SURVIVOR_ID, [_SURVIVOR_ID], None)


def test_survivor_selection_carries_expression_and_orientation(tmp_path):
    selection = select_registered_candidate(
        _manifest(tmp_path), _SURVIVOR_ID, [_SURVIVOR_ID], None)
    assert selection["expression"] == _EXPR_STR
    assert selection["orientation"] == -1


# ---------------------------------------------------------------------------
# Pool lookup — by expression string, never by expr_hash
# ---------------------------------------------------------------------------


def test_pool_entry_located_by_expression(tmp_path):
    entry = locate_pool_entry(_pool_dir(tmp_path), _EXPR_STR)
    assert entry.expr.to_qlib_string() == _EXPR_STR
    assert entry.orientation == -1


def test_expression_absent_from_pool_refuses(tmp_path):
    with pytest.raises(PVPromoteError, match="matches 0 pool entries"):
        locate_pool_entry(_pool_dir(tmp_path), "cs_rank($close)")


def test_incomplete_run_dir_refuses(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(PVPromoteError, match="factor_pool.parquet"):
        locate_pool_entry(d, _EXPR_STR)

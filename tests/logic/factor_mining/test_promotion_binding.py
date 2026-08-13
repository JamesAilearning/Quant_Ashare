"""Tests for the ledger-anchored promotion binding (codex #422 r2).

The invariant under test: authority comes from the COMMITTED ledger,
never from the invocation that wants to use it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.factor_mining.promotion_binding import (  # noqa: E402
    PROMOTION_LEDGER_ENTRY,
    REPRESENTATIVE_LEDGER_ENTRY,
    PromotionBindingError,
    ledger_representative,
    ledger_verdict_sha256,
    pool_identity_string,
    verify_promoted_bundle,
    verify_verdict_against_ledger,
)

_LEDGER = _PROJECT_ROOT / "docs" / "prereg" / "pv_incremental_ledger.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_REGISTERED_ID = "pv001_2789e60e"
_REGISTERED_EXPR = "cs_demean(abs($turnover_rate))"


def _fake_ledger(tmp_path: Path, digest: str, *, entry_id: str = "E007",
                 representative: str = _REGISTERED_ID,
                 expression: str = _REGISTERED_EXPR) -> Path:
    p = tmp_path / "ledger.yaml"
    p.write_text(yaml.safe_dump({
        "protocol_id": "pv_incremental_v1",
        "entries": [
            {
                "id": entry_id, "when": "2026-08-12", "kind": "result",
                "what": "x",
                "artifacts": [f"output/x/fwer_verdict.json#sha256={digest}"],
            },
            {
                "id": "E008", "when": "2026-08-12", "kind": "intent",
                "what": "y",
                "numbers": {"representative": representative,
                            "representative_expression": expression},
            },
        ],
    }), encoding="utf-8")
    return p


def _write_real_pool(pool_dir: Path, expression: str) -> None:
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.factor_pool import FactorPool, PoolEntry

    expr = parse_expression(expression)
    pool = FactorPool()
    pool.add(PoolEntry(
        expr=expr, fitness=0.02, ic_mean=0.01, ic_std=0.1, ir=0.1,
        rank_ic_mean=0.03, rank_ic_std=0.1, rank_ir=0.2,
        turnover_daily=0.1, coverage=0.9, n_obs_per_day_min=300,
        expr_size=3, expr_hash=hash(expr), orientation=-1))
    pool.save(pool_dir)


def _verdict_file(tmp_path: Path, payload=None) -> Path:
    p = tmp_path / "fwer_verdict.json"
    p.write_text(json.dumps(payload or {"verdict": "survivors"}),
                 encoding="utf-8", newline="")
    return p


def _bundle(tmp_path: Path, *, verdict_sha: str, pool_sha: str | None = None,
            expressions_sha: str | None = None,
            candidate_id: str = _REGISTERED_ID,
            expression: str = _REGISTERED_EXPR) -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    # A REAL single-entry pool: verification loads what the handler would
    # execute (codex #422 r5), so opaque bytes no longer pass.
    _write_real_pool(d, expression)
    actual_pool = hashlib.sha256((d / "factor_pool.parquet").read_bytes()).hexdigest()
    actual_expr = hashlib.sha256(
        (d / "factor_expressions.json").read_bytes()).hexdigest()
    (d / "promotion_provenance.json").write_text(json.dumps({
        "candidate_id": candidate_id,
        "expression": expression,
        "fwer_verdict_sha256": verdict_sha,
        "promoted_pool_sha256": pool_sha if pool_sha is not None else actual_pool,
        "promoted_expressions_sha256": (
            expressions_sha if expressions_sha is not None else actual_expr),
    }), encoding="utf-8")
    return d


# --- the real ledger anchors the real entry -------------------------------


def test_real_ledger_records_one_verdict_digest_for_the_entry():
    digest = ledger_verdict_sha256(_LEDGER, entry_id=PROMOTION_LEDGER_ENTRY)
    assert len(digest) == 64


# --- verdict verification --------------------------------------------------


def test_verdict_not_vouched_by_ledger_refuses(tmp_path):
    v = _verdict_file(tmp_path)
    ledger = _fake_ledger(tmp_path, "b" * 64)
    with pytest.raises(PromotionBindingError, match="not the adjudication"):
        verify_verdict_against_ledger(v, ledger, entry_id="E007")


def test_self_supplied_digest_cannot_override_the_ledger(tmp_path):
    # The whole point of r2: passing a matching (file, digest) PAIR is
    # not authority — the ledger has to vouch for that digest.
    v = _verdict_file(tmp_path)
    ledger = _fake_ledger(tmp_path, "b" * 64)
    with pytest.raises(PromotionBindingError):
        verify_verdict_against_ledger(
            v, ledger, entry_id="E007", expect_sha256=_sha(v))


def test_verdict_vouched_by_ledger_passes(tmp_path):
    v = _verdict_file(tmp_path)
    ledger = _fake_ledger(tmp_path, _sha(v))
    assert verify_verdict_against_ledger(
        v, ledger, entry_id="E007", expect_sha256=_sha(v)) == _sha(v)


def test_missing_ledger_entry_refuses(tmp_path):
    v = _verdict_file(tmp_path)
    ledger = _fake_ledger(tmp_path, _sha(v), entry_id="E999")
    with pytest.raises(PromotionBindingError, match="expected exactly 1"):
        verify_verdict_against_ledger(v, ledger, entry_id="E007")


# --- bundle verification ---------------------------------------------------


def test_bundle_without_sidecar_refuses(tmp_path):
    d = tmp_path / "raw_pool"
    d.mkdir()
    (d / "factor_pool.parquet").write_bytes(b"x")
    ledger = _fake_ledger(tmp_path, "c" * 64)
    with pytest.raises(PromotionBindingError, match="promotion_provenance"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_bundle_promoted_under_another_verdict_refuses(tmp_path):
    d = _bundle(tmp_path, verdict_sha="d" * 64)
    ledger = _fake_ledger(tmp_path, "e" * 64)
    with pytest.raises(PromotionBindingError, match="unadjudicated"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_bundle_whose_pool_changed_after_promotion_refuses(tmp_path):
    d = _bundle(tmp_path, verdict_sha="f" * 64, pool_sha="0" * 64)
    ledger = _fake_ledger(tmp_path, "f" * 64)
    with pytest.raises(PromotionBindingError, match="changed after promotion"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def _with_inputs(identity, tmp_path):
    """Merge the PIT-input identity, as the runner does."""
    from src.factor_mining.promotion_binding import mined_input_identity

    registry = tmp_path / "registry.parquet"
    registry.write_bytes(b"registry-bytes")
    bundle = tmp_path / "fake_bundle"
    (bundle / "calendars").mkdir(parents=True, exist_ok=True)
    (bundle / "calendars" / "day.txt").write_text(
        "2024-01-01" + chr(10), encoding="utf-8")
    identity.update(mined_input_identity(
        pit_provider_uri=str(bundle),
        delisted_registry_path=str(registry)))
    return identity


def test_unhashable_pit_bundle_refuses(tmp_path):
    # codex #422 r5: a path is reusable provenance. If the bundle cannot
    # even be hashed, the treatment features are tied to no vintage at
    # all — that must refuse, not record "unknown".
    from src.factor_mining.promotion_binding import mined_input_identity

    with pytest.raises(PromotionBindingError, match="content hash"):
        mined_input_identity(pit_provider_uri=str(tmp_path / "nope"),
                             delisted_registry_path=str(tmp_path / "r.parquet"))


def test_bundle_executing_another_expression_refuses(tmp_path):
    # The three bundle files can be fabricated TOGETHER and satisfy every
    # digest, because all the claimed digests come from the same sidecar.
    # The check that cannot be gamed that way: what the pool will execute.
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    _write_real_pool(d, "cs_rank($volume)")   # swap the executable AST
    prov = json.loads((d / "promotion_provenance.json").read_text(encoding="utf-8"))
    prov["promoted_pool_sha256"] = hashlib.sha256(
        (d / "factor_pool.parquet").read_bytes()).hexdigest()
    prov["promoted_expressions_sha256"] = hashlib.sha256(
        (d / "factor_expressions.json").read_bytes()).hexdigest()
    (d / "promotion_provenance.json").write_text(json.dumps(prov), encoding="utf-8")
    ledger = _fake_ledger(tmp_path, "a" * 64)
    with pytest.raises(PromotionBindingError, match="would execute"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_stamp_refuses_when_the_pit_inputs_were_not_merged(tmp_path):
    # codex #422 r4: the PIT inputs decide the feature values. A stamp
    # missing them would still satisfy the gate's prefix check while the
    # data vintage went unrecorded, so building one must refuse.
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    ledger = _fake_ledger(tmp_path, "a" * 64)
    identity = verify_promoted_bundle(d, ledger, entry_id="E007")
    with pytest.raises(PromotionBindingError, match="pit_provider_uri"):
        pool_identity_string(identity)


def test_verified_bundle_yields_a_stampable_identity(tmp_path):
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    ledger = _fake_ledger(tmp_path, "a" * 64)
    identity = _with_inputs(
        verify_promoted_bundle(d, ledger, entry_id="E007"), tmp_path)
    text = pool_identity_string(identity)
    assert "pv001_2789e60e" in text
    assert "cs_demean(abs($turnover_rate))" in text
    assert identity["pool_sha256"] in text
    assert "a" * 64 in text


# --- r3: both artifacts, and the PRE-REGISTERED representative ------------


def test_real_ledger_pre_registers_the_representative():
    reg = ledger_representative(_LEDGER, entry_id=REPRESENTATIVE_LEDGER_ENTRY)
    assert reg["candidate_id"]
    assert reg["expression"]


def test_swapped_expressions_json_refuses(tmp_path):
    # codex #422 r3: FactorPool takes its EXECUTABLE ast from the JSON
    # and never cross-checks it against the parquet's randomised
    # expr_hash, so hashing the parquet alone leaves the expression that
    # actually runs unbound.
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    ledger = _fake_ledger(tmp_path, "a" * 64)
    (d / "factor_expressions.json").write_bytes(b"swapped-bytes")
    with pytest.raises(PromotionBindingError, match="changed after promotion"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_bundle_without_expressions_digest_refuses(tmp_path):
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    prov = json.loads((d / "promotion_provenance.json").read_text(encoding="utf-8"))
    del prov["promoted_expressions_sha256"]
    (d / "promotion_provenance.json").write_text(json.dumps(prov), encoding="utf-8")
    ledger = _fake_ledger(tmp_path, "a" * 64)
    with pytest.raises(PromotionBindingError,
                       match="promoted_expressions_sha256"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_another_survivor_cannot_bind_as_the_registered_variant(tmp_path):
    # A pv002 bundle is a legitimate promotion (E007 lists 50 survivors)
    # but it is NOT the registered treatment — binding it would earn a
    # decision-grade "alpha158-plus-pv001" verdict.
    d = _bundle(tmp_path, verdict_sha="a" * 64,
                candidate_id="pv002_7bb41ced", expression="cs_rank($volume)")
    ledger = _fake_ledger(tmp_path, "a" * 64)
    with pytest.raises(PromotionBindingError, match="pre-registered"):
        verify_promoted_bundle(d, ledger, entry_id="E007")


def test_identity_string_carries_both_digests(tmp_path):
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    ledger = _fake_ledger(tmp_path, "a" * 64)
    text = pool_identity_string(_with_inputs(
        verify_promoted_bundle(d, ledger, entry_id="E007"), tmp_path))
    assert "expressions_sha256=" in text
    assert "E007+E008" in text
    assert "registry_sha256=" in text
    assert "|pit=" in text
    # The bundle's CONTENT identity, not just its path: a re-ingested
    # bundle must change the stamp (codex #422 r5).
    assert "pit_content_hash=sha256:" in text

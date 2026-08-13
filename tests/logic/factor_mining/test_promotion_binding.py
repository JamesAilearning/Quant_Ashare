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
    (d / "factor_pool.parquet").write_bytes(b"pool-bytes")
    (d / "factor_expressions.json").write_bytes(b"expr-bytes")
    actual_pool = hashlib.sha256(b"pool-bytes").hexdigest()
    actual_expr = hashlib.sha256(b"expr-bytes").hexdigest()
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


def test_verified_bundle_yields_a_stampable_identity(tmp_path):
    d = _bundle(tmp_path, verdict_sha="a" * 64)
    ledger = _fake_ledger(tmp_path, "a" * 64)
    identity = verify_promoted_bundle(d, ledger, entry_id="E007")
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
    text = pool_identity_string(
        verify_promoted_bundle(d, ledger, entry_id="E007"))
    assert "expressions_sha256=" in text
    assert "E007+E008" in text

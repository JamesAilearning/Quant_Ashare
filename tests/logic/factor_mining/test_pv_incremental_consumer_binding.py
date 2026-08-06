"""pv_incremental_v1 consumer-side registration binding.

Recording a registration is not enforcing it (codex #402 r6): without
these checks the OOS evaluator would preflight an edited manifest, and
would score incrementality against whatever baseline it was handed —
so a batch could be bred against baseline A and adjudicated against
baseline B, and the "frozen" family could change between registration
and adjudication unnoticed.

Dimensions: sidecar presence × protocol identity × digest validity ×
manifest tamper detection × baseline identity × end-to-end against a
manifest produced by the real registrar.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.research.pv_incremental_register_candidates as rg  # noqa: E402
import scripts.research.pv_incremental_registration as reg  # noqa: E402
from tests.logic.factor_mining.test_pv_incremental_register import (  # noqa: E402
    _CSF,
    _entry,
    _make_run,
)


def _registered_batch(tmp: Path) -> tuple[Path, dict]:
    """Produce a REAL registration with the registrar, so these tests
    bind to the artifact the campaign will actually consume rather
    than to a hand-written stand-in."""
    run = _make_run(tmp / "run", [_entry(_CSF, 0.05)])
    out = tmp / "out"
    rc = rg.main(["--run-dir", str(run), "--out-dir", str(out),
                  "--top-k", "1", "--when", "2026-08-06"])
    assert rc == 0, rc
    manifest = out / "candidates.json"
    sidecar = reg.sidecar_path_for(manifest)
    return manifest, json.loads(sidecar.read_text(encoding="utf-8"))


class LoadRegistrationTests(unittest.TestCase):
    def test_real_registration_loads(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            loaded = reg.load_registration(manifest)
            self.assertEqual(payload["manifest_sha256"],
                             loaded["manifest_sha256"])
            self.assertEqual("pv_incremental_v1", loaded["protocol_id"])

    def test_unregistered_manifest_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "candidates.json"
            manifest.write_text("[]", encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.load_registration(manifest)
            self.assertIn("no registration sidecar", str(ctx.exception))

    def test_edited_manifest_refuses(self) -> None:
        # THE point of the freeze: exclusive creation stops a second
        # registrar, not a later edit.
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            body = json.loads(manifest.read_text(encoding="utf-8"))
            body[0]["orientation"] = -body[0]["orientation"]
            manifest.write_text(json.dumps(body, indent=2) + "\n",
                                encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.load_registration(manifest)
            self.assertIn("modified after registration",
                          str(ctx.exception))

    def test_whitespace_only_edit_still_refuses(self) -> None:
        # The digest is over BYTES — a semantically identical rewrite
        # is still not the registered artifact.
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            body = json.loads(manifest.read_text(encoding="utf-8"))
            manifest.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError):
                reg.load_registration(manifest)

    def test_foreign_protocol_and_bad_digest_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            sidecar = reg.sidecar_path_for(manifest)
            for label, mutate in (
                    ("foreign protocol", {"protocol_id": "other_v1"}),
                    ("no digest", {"manifest_sha256": None}),
                    ("short digest", {"manifest_sha256": "abc"})):
                bad = dict(payload)
                bad.update(mutate)
                sidecar.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(reg.PVRegistrationError,
                                       msg=label):
                    reg.load_registration(manifest)


class BaselineIdentityTests(unittest.TestCase):
    def test_matching_baseline_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, payload = _registered_batch(Path(d))
            recorded = payload["gp_input_sha256"][
                "baseline_preds.parquet"]
            self.assertEqual(
                recorded,
                reg.assert_baseline_matches_registration(
                    payload, recorded))

    def test_different_baseline_refuses(self) -> None:
        # Two provenance-valid exports of the same frozen model are
        # each legitimate — which is exactly why identity must be by
        # digest, not by model name.
        with tempfile.TemporaryDirectory() as d:
            _, payload = _registered_batch(Path(d))
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.assert_baseline_matches_registration(
                    payload, "b" * 64)
            self.assertIn("bred against", str(ctx.exception))

    def test_registration_without_input_digests_refuses(self) -> None:
        with self.assertRaises(reg.PVRegistrationError):
            reg.assert_baseline_matches_registration(
                {"manifest_sha256": "a" * 64}, "a" * 64)

    def test_invalid_recorded_digest_refuses(self) -> None:
        with self.assertRaises(reg.PVRegistrationError):
            reg.assert_baseline_matches_registration(
                {"gp_input_sha256": {"baseline_preds.parquet": "nope"}},
                "a" * 64)


class ConsumerWiringTests(unittest.TestCase):
    """Both consumers must route through the binding — a check that
    exists but is never called protects nothing."""

    def test_evaluator_enforces_registration(self) -> None:
        import inspect

        import scripts.research.pv_incremental_eval as ev
        src = inspect.getsource(ev.main)
        self.assertIn("load_registration", src)
        self.assertIn("assert_baseline_matches_registration", src)

    def test_adjudicator_enforces_registration(self) -> None:
        import inspect

        import scripts.research.pv_incremental_fwer_adjudication as fw
        src = inspect.getsource(fw.main)
        self.assertIn("load_registration", src)

    def test_adjudicator_records_the_registration_digest(self) -> None:
        import inspect

        import scripts.research.pv_incremental_fwer_adjudication as fw
        self.assertIn("registration_manifest_sha256",
                      inspect.getsource(fw.main))


class LedgerShapeTests(unittest.TestCase):
    def test_registration_ledger_entry_is_appendable(self) -> None:
        # The operator appends this to the campaign ledger before the
        # OOS run; it must parse as a one-element YAML list.
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            entry = yaml.safe_load(
                (manifest.parent / "ledger_entry.yaml")
                .read_text(encoding="utf-8"))
            self.assertEqual(1, len(entry))
            self.assertEqual("intent", entry[0]["kind"])
            self.assertIn("manifest_sha256", entry[0]["gp_provenance"])
            # The digest in the ledger is the one on disk.
            self.assertEqual(
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
                entry[0]["gp_provenance"]["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()

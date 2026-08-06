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


def _authorised_ledger(tmp: Path, manifest: Path) -> Path:
    """A COMMITTED campaign ledger recording this manifest.

    The authority is the committed bytes (codex #403 r4), so the
    fixture builds a real throwaway git repo rather than dropping a
    file next to the artifacts — a working-tree-only ledger is
    precisely what must NOT authorise a batch.
    """
    import subprocess
    entry = yaml.safe_load(
        (manifest.parent / "ledger_entry.yaml").read_text(
            encoding="utf-8"))
    repo = tmp / "ledger_repo"
    (repo / "docs" / "prereg").mkdir(parents=True, exist_ok=True)
    path = repo / "docs" / "prereg" / "pv_incremental_ledger.yaml"
    path.write_text(
        yaml.safe_dump({"protocol_id": "pv_incremental_v1",
                        "plan": "docs/prereg/pv_incremental.yaml",
                        "entries": entry},
                       sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.com"],
                ["git", "config", "user.name", "t"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "ledger"]):
        subprocess.run(cmd, cwd=str(repo), check=True,
                       capture_output=True)
    # Point the module's repo root at this throwaway checkout so the
    # committed-bytes read resolves there.
    reg._REPO_ROOT = repo
    return path


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


_REAL_REPO_ROOT = reg._REPO_ROOT


class _RestoresRepoRoot(unittest.TestCase):
    def tearDown(self) -> None:
        reg._REPO_ROOT = _REAL_REPO_ROOT


class LoadRegistrationTests(_RestoresRepoRoot):
    def test_real_registration_loads(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            loaded = reg.load_registration(manifest, _authorised_ledger(
                Path(d), manifest))
            self.assertEqual(payload["manifest_sha256"],
                             loaded["manifest_sha256"])
            self.assertEqual("pv_incremental_v1", loaded["protocol_id"])

    def test_unregistered_manifest_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest = Path(d) / "candidates.json"
            manifest.write_text("[]", encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                # No sidecar at all — refused before the ledger is
                # even consulted.
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
                reg.load_registration(manifest, _authorised_ledger(
                Path(d), manifest))
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
                reg.load_registration(manifest, _authorised_ledger(
                Path(d), manifest))

    def test_foreign_protocol_and_bad_digest_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            sidecar = reg.sidecar_path_for(manifest)
            ledger = _authorised_ledger(Path(d), manifest)
            for label, mutate in (
                    ("foreign protocol", {"protocol_id": "other_v1"}),
                    ("no digest", {"manifest_sha256": None}),
                    ("short digest", {"manifest_sha256": "abc"})):
                bad = dict(payload)
                bad.update(mutate)
                sidecar.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(reg.PVRegistrationError,
                                       msg=label):
                    reg.load_registration(manifest, ledger)


class LedgerAuthorityTests(_RestoresRepoRoot):
    """codex #403 r3: the sidecar and the manifest sit in the same
    directory and are equally writable, so their agreement proves only
    self-consistency — anyone could recompute the digest into the
    sidecar without ever running the registrar. The append-only
    campaign ledger (in git, reviewed) is the independent authority."""

    def test_forged_pair_without_ledger_entry_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            # A hand-made "registration": manifest + a self-consistent
            # sidecar, never registered anywhere.
            manifest = Path(d) / "candidates.json"
            body = json.dumps([{"candidate_id": "pv001_deadbeef",
                                "expression": _CSF,
                                "orientation": 1}], indent=2) + chr(10)
            manifest.write_text(body, encoding="utf-8", newline="")
            reg.sidecar_path_for(manifest).write_text(
                json.dumps({
                    "protocol_id": "pv_incremental_v1",
                    "manifest_sha256": hashlib.sha256(
                        manifest.read_bytes()).hexdigest(),
                    "gp_input_sha256": {
                        "baseline_preds.parquet": "a" * 64},
                }), encoding="utf-8")
            empty_ledger = Path(d) / "ledger.yaml"
            empty_ledger.write_text(
                yaml.safe_dump({"protocol_id": "pv_incremental_v1",
                                "entries": []}),
                encoding="utf-8")
            # A ledger that is not committed inside the repository
            # cannot authorise anything — that is the r4 point.
            with self.assertRaises(reg.PVRegistrationError):
                reg.load_registration(manifest, empty_ledger)

    def test_uncommitted_ledger_edit_refuses(self) -> None:
        # codex #403 r4: a writable checkout defeats a working-tree
        # read — append a digest, run, revert. The authority must be
        # the COMMITTED bytes.
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            ledger = _authorised_ledger(Path(d), manifest)
            doc = yaml.safe_load(ledger.read_text(encoding="utf-8"))
            doc["entries"].append(
                {"kind": "intent",
                 "gp_provenance": {"manifest_sha256": "f" * 64}})
            ledger.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.load_registration(manifest, ledger)
            self.assertIn("differs from its committed state",
                          str(ctx.exception))

    def test_doctored_sidecar_baseline_refuses(self) -> None:
        # codex #403 r4: a legitimately registered manifest plus a
        # sidecar whose baseline digest was swapped must not let the
        # incremental comparison run against another baseline.
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            ledger = _authorised_ledger(Path(d), manifest)
            sidecar = reg.sidecar_path_for(manifest)
            doctored = dict(payload)
            doctored["gp_input_sha256"] = dict(
                payload["gp_input_sha256"],
                **{"baseline_preds.parquet": "e" * 64})
            sidecar.write_text(json.dumps(doctored), encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.load_registration(manifest, ledger)
            self.assertIn("do not equal the committed ledger",
                          str(ctx.exception))

    def test_truncated_or_retyped_sidecar_map_refuses(self) -> None:
        # codex #403 r5: dropping the key, retyping it, or deleting
        # selected digests must not slip past a surviving-keys check.
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            ledger = _authorised_ledger(Path(d), manifest)
            sidecar = reg.sidecar_path_for(manifest)
            partial = {k: v for k, v
                       in payload["gp_input_sha256"].items()
                       if k != "baseline_preds.parquet"}
            for label, inputs in (("dropped key", None),
                                  ("retyped", ["not", "a", "map"]),
                                  ("truncated", partial)):
                doctored = dict(payload)
                if inputs is None:
                    doctored.pop("gp_input_sha256", None)
                else:
                    doctored["gp_input_sha256"] = inputs
                sidecar.write_text(json.dumps(doctored),
                                   encoding="utf-8")
                with self.assertRaises(reg.PVRegistrationError,
                                       msg=label):
                    reg.load_registration(manifest, ledger)

    def test_returned_provenance_is_the_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, payload = _registered_batch(Path(d))
            ledger = _authorised_ledger(Path(d), manifest)
            loaded = reg.load_registration(manifest, ledger)
            self.assertEqual(payload["manifest_sha256"],
                             loaded["manifest_sha256"])
            self.assertIn("gp_input_sha256", loaded)

    def test_ledger_entry_authorises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            ledger = _authorised_ledger(Path(d), manifest)
            self.assertEqual(
                "pv_incremental_v1",
                reg.load_registration(manifest, ledger)["protocol_id"])

    def test_foreign_or_missing_ledger_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            missing = Path(d) / "nope.yaml"
            with self.assertRaises(reg.PVRegistrationError):
                reg.load_registration(manifest, missing)
            foreign = Path(d) / "foreign.yaml"
            foreign.write_text(
                yaml.safe_dump({"protocol_id": "other_v1",
                                "entries": []}), encoding="utf-8")
            with self.assertRaises(reg.PVRegistrationError):
                reg.load_registration(manifest, foreign)


class BaselineIdentityTests(_RestoresRepoRoot):
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


class DirectCliInvocationTests(unittest.TestCase):
    """codex #403 r1: the documented invocation is
    ``python scripts/research/<tool>.py``, which puts scripts/research
    on sys.path — NOT the repo root. A shared absolute import then
    dies with ModuleNotFoundError. pytest hides this (the root is
    already importable), so the check has to run the real CLI."""

    def test_every_tool_imports_under_direct_execution(self) -> None:
        import os
        import subprocess
        # Inherit the real environment but strip PYTHONPATH — that is
        # precisely the condition under test (and keeps this portable:
        # the CI runners are Linux).
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        for tool in ("pv_incremental_eval.py",
                     "pv_incremental_fwer_adjudication.py",
                     "pv_incremental_register_candidates.py"):
            proc = subprocess.run(
                [sys.executable,
                 str(_PROJECT_ROOT / "scripts" / "research" / tool),
                 "--help"],
                capture_output=True, text=True,
                cwd=str(_PROJECT_ROOT), env=env)
            self.assertEqual(0, proc.returncode,
                             f"{tool}: {proc.stderr[-400:]}")


class ArtifactBaselineBindingTests(unittest.TestCase):
    def test_adjudicator_binds_artifact_baseline(self) -> None:
        # codex #403 r1: check_run_identity only proves artifacts and
        # their completion stamp agree WITH EACH OTHER — a
        # self-consistent batch scored against another baseline must
        # not be adjudicated against this registration.
        import inspect

        import scripts.research.pv_incremental_fwer_adjudication as fw
        src = inspect.getsource(fw.main)
        self.assertIn("assert_baseline_matches_registration", src)
        self.assertIn("baseline_preds_sha256", src)

    def test_mismatched_artifact_baseline_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, payload = _registered_batch(Path(d))
            # The completion stamp's digest is what the adjudicator
            # passes in; a batch evaluated against another baseline
            # refuses.
            with self.assertRaises(reg.PVRegistrationError) as ctx:
                reg.assert_baseline_matches_registration(
                    payload, "c" * 64)
            self.assertIn("bred against", str(ctx.exception))


class OosDataProtectionTests(_RestoresRepoRoot):
    """codex #403 r2: the 2023-2024 window is a ONE-SHOT evaluation,
    so an unregistered / tampered / wrong-baseline batch must be
    refused BEFORE any of it is read — otherwise the invalid batch has
    already consumed the protected data."""

    def test_registration_gate_precedes_provider_and_panel(self) -> None:
        import inspect

        import scripts.research.pv_incremental_eval as ev
        src = inspect.getsource(ev.main)
        gate = src.index("load_registration(")
        preflight = src.index("preflight_candidates(candidates")
        provider = src.index("build_pit_provider(")
        panel = src.index("view.load_panel()")
        self.assertLess(gate, provider)
        self.assertLess(preflight, provider)
        self.assertLess(provider, panel)

    def test_tampered_manifest_refuses_without_touching_data(self) -> None:
        # Drive the real CLI: if the gate ran after the provider, the
        # run would fail on qlib/bundle access instead of the
        # registration refusal (and would have read the window).
        with tempfile.TemporaryDirectory() as d:
            manifest, _ = _registered_batch(Path(d))
            body = json.loads(manifest.read_text(encoding="utf-8"))
            body[0]["expression"] = "cs_rank(ts_delta($high, 5))"
            manifest.write_text(
                json.dumps(body, indent=2) + chr(10),
                encoding="utf-8")
            import scripts.research.pv_incremental_eval as ev
            baseline = Path(d) / "run" / "baseline_preds.parquet"
            # Give the baseline a VALID provenance sidecar so that
            # gate passes — otherwise it refuses first and this test
            # would not exercise the registration gate at all.
            plan = ev.load_frozen_plan()
            baseline.with_name(
                baseline.name + ".provenance.json").write_text(
                json.dumps({
                    "model": plan["fitness"]["baseline"]["model"],
                    "file_sha256": hashlib.sha256(
                        baseline.read_bytes()).hexdigest(),
                    "run_config_sha256": "ab" * 32,
                    "source_git": "c" * 40,
                }), encoding="utf-8")
            rc = ev.main([
                "--candidates", str(manifest),
                "--baseline-preds", str(baseline),
                "--out-dir", str(Path(d) / "artifacts"),
                "--window-start", "2023-01-01",
                "--window-end", "2024-12-31",
                "--provider", str(Path(d) / "no-such-bundle"),
            ])
            # 1 = the evaluator's classified refusal. If the gate ran
            # AFTER the provider, this would have died on the
            # nonexistent bundle instead — and would have read the
            # protected window in the real case.
            self.assertEqual(1, rc)


class LedgerShapeTests(_RestoresRepoRoot):
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

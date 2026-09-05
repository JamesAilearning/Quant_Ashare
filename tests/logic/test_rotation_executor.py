"""Rotation executor end-to-end states (PR-B') — a scratch git repo
plays origin/main, stub files play members; NO qlib, NO real models
(the executor moves a manifest file after the gates said yes — model
loading was proven by the gate artifacts' binding).

States (codex #389 r2/r3/r4/r11 + tasks §PR-B'):
  legal rotation full chain (backup written + single-step rollback) /
  gate artifact missing refused / gate artifact FAIL refused /
  LOSE frozen / expired WIN frozen / absent status artifact refused /
  tampered candidate (plan-integrity) refused — each refusal with
  manifest bytes untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.retrain_gate_lib import (  # noqa: E402
    GATE_SCHEMA_VERSION,
    SCOPE_ENSEMBLE,
    SCOPE_MEMBER,
)
from scripts.rotate_ensemble_member import main as rotate_main  # noqa: E402
from scripts.rotation_lib import (  # noqa: E402
    RECERT_STATUS_PATH,
    RECERT_STATUS_SCHEMA_VERSION,
)

# Staggered quarterly windows satisfying the serving-loader pins.
# Dated relative to _NOW below: a quarterly retrain at T trains
# through ~T-3m and validates on the 3 months up to T, so the newest
# member's fit_end sits about a quarter behind the rotation instant
# (codex #391 r19 binds the gates' measured windows to this shape).
_CURRENT_WINDOWS = [
    ("2023-08-02", "2025-08-01"),
    ("2023-11-01", "2025-10-31"),
    ("2024-01-31", "2026-01-30"),
]
_NEW_WINDOW = ("2024-05-02", "2026-05-01")   # +91d gap, 729d span
_NOW = "2026-08-01T00:00:00+00:00"
# The member IC gate's valid window: promptly after the training
# window, ~3 months, ending just before the rotation instant.
_MEMBER_VALID = ("2026-05-05", "2026-07-30")
# The ensemble gates' trailing-quarter dry run.
_TRAILING_QUARTER = ("2026-05-01", "2026-07-31")

# Synthetic committed registration, independent of live models and local YAML.
_BASE_FAMILY = {
    "feature_handler": "Alpha158", "model_type": "LGBModel",
    "num_boost_round": 1000, "early_stopping_rounds": 50,
    "learning_rate": 0.005, "max_depth": 6, "num_leaves": 64,
    "lambda_l1": 0.0, "lambda_l2": 1.0, "min_data_in_leaf": 50,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
    "topk": 50, "n_drop": 5, "provider_uri": "/registered/provider",
}
_FAMILY_DEFAULTS = {
    "label_horizon_days": 1, "seed": 42, "region": "cn", "adjust_mode": "pre_adjusted",
}
_PRESET_FAMILY = {
    "instruments": "csi800", "benchmark_code": "SH000906TR",
    "attribution_sleeve_grouping": True, "risk_constraints_enabled": True,
    "risk_constraints_calibration": "campaign_v1", "compute_device": "gpu",
}
_REGISTERED_PATHS = tuple(
    f"config/presets/csi800_n5_bootstrap_m{i}.yaml" for i in (1, 2, 3)
)


class _StubModel:
    """Pickle-able member stub with the .predict serving requires."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def predict(self, dataset, segment="infer"):  # noqa: ANN001
        return None


def _write_member_files(tmp: Path, name: str, window: tuple[str, str],
                        valid_window: tuple[str, str] | None = None,
                        source_commit: str | None = None,
                        ) -> dict:
    """A REAL loadable member (pkl + trainer sidecar with the version
    chain the serving loader enforces) — execute's member-chain
    re-validation (codex #391 r9) unpickles these."""
    import hashlib
    import pickle

    import lightgbm

    run_dir = tmp / name
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    config = run_dir / "config.yaml"
    config.write_text(json.dumps({
        **_BASE_FAMILY, **_FAMILY_DEFAULTS, **_PRESET_FAMILY,
        "train_start": window[0], "train_end": window[1],
        "test_start": "2026-08-04", "test_end": "2026-08-31",
        **({"valid_start": valid_window[0], "valid_end": valid_window[1]}
           if valid_window is not None else {}),
    }), encoding="utf-8")
    pkl = run_dir / "artifacts" / "model.pkl"
    pkl.write_bytes(pickle.dumps(_StubModel(name)))
    meta = pkl.with_suffix(".pkl.meta.json")
    meta.write_text(json.dumps({
        "schema_version": "v1", "model_type": "LGBModel",
        "best_iteration": 321, "num_boost_round": 1000,
        "lightgbm_version": lightgbm.__version__,
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
        "run_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        **({"source_git_commit": source_commit, "source_git_dirty": False}
           if source_commit is not None else {}),
    }), encoding="utf-8")
    return {
        "pkl_path": str(pkl),
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
        "meta_path": str(meta),
        "meta_sha256": hashlib.sha256(meta.read_bytes()).hexdigest(),
        "fit_start": window[0], "fit_end": window[1],
    }


# Pinned committer instant — the 15-month validity clock in these
# tests must not depend on the machine's real clock.
_COMMIT_INSTANT = "2026-07-01T10:00:00+08:00"


def _git(repo: Path, *args: str) -> None:
    import os

    env = dict(os.environ)
    env["GIT_COMMITTER_DATE"] = _COMMIT_INSTANT
    env["GIT_AUTHOR_DATE"] = _COMMIT_INSTANT
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t",
         "-c", "user.name=t", *args],
        check=True, capture_output=True, env=env)


def _make_mainline_repo(repo: Path, *, status: dict | None) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)],
                   check=True, capture_output=True)
    if status is not None:
        target = repo / RECERT_STATUS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(status, indent=2),
                          encoding="utf-8")
    else:
        (repo / "README.md").write_text("no status", encoding="utf-8")
    (repo / "config.yaml").write_text(json.dumps({
        **_BASE_FAMILY,
        "provider_uri": "${QUANT_PROVIDER_URI:-/registered/provider}",
    }), encoding="utf-8")
    for relpath in _REGISTERED_PATHS:
        preset = repo / relpath
        preset.parent.mkdir(parents=True, exist_ok=True)
        preset.write_text(json.dumps({
            "extends": "../../config.yaml", **_PRESET_FAMILY,
            "train_start": "2024-04-01", "train_end": "2026-04-01",
            "valid_start": "2026-04-07", "valid_end": "2026-07-07",
            "test_start": "2026-07-10", "test_end": "2026-07-31",
        }), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "state")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _win_status() -> dict:
    return {
        "schema_version": RECERT_STATUS_SCHEMA_VERSION,
        "verdict": "WIN",
        "verdict_sidecar_path":
            "docs/research/csi800_cadence_verdict.json",
        "verdict_sidecar_sha256": "6a" * 32,
        "evidence_anchor_commit": "3f" * 20,
        "note": "bootstrap WIN for executor tests",
    }


class RotationExecutorStates(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.repo = self.tmp / "mainline"
        _make_mainline_repo(self.repo, status=_win_status())
        self.source_commit = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()

        # Current production manifest. The OLDEST member (dropped by
        # the rotation) may be a phantom entry — execute's member-chain
        # re-validation runs over the CANDIDATE members only; members
        # 1/2 survive into the candidate and must be real loadable
        # stubs (codex #391 r9).
        members = [{
            "pkl_path": "Z:/prod/member_0.pkl",
            "pkl_sha256": "00" * 32,
            "meta_path": "Z:/prod/member_0.pkl.meta.json",
            "meta_sha256": "0a" * 32,
            "fit_start": _CURRENT_WINDOWS[0][0],
            "fit_end": _CURRENT_WINDOWS[0][1],
        }]
        self.kept_members = [
            _write_member_files(self.tmp, f"member_{i}",
                                _CURRENT_WINDOWS[i])
            for i in (1, 2)
        ]
        members.extend(self.kept_members)
        self.manifest = self.tmp / "production_manifest.json"
        self.manifest.write_text(json.dumps({
            "schema_version": "csi800_n5_ensemble_manifest_v1",
            "members": members}, indent=2), encoding="utf-8")
        self.original_bytes = self.manifest.read_bytes()

        # The incoming member: a REAL loadable stub (hashed by `plan`,
        # unpickled by execute's member-chain re-validation).
        new_files = _write_member_files(self.tmp, "new_member",
                                        _NEW_WINDOW, _MEMBER_VALID, self.source_commit)
        self.new_pkl = Path(new_files["pkl_path"])
        self.new_meta = Path(new_files["meta_path"])

        self.candidate = self.tmp / "candidate_manifest.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.candidate),
        ])
        self.assertEqual(0, rc, "plan must succeed")
        self.new_pkl_sha = json.loads(
            self.candidate.read_text(encoding="utf-8"),
        )["members"][-1]["pkl_sha256"]
        import hashlib
        self.candidate_sha = hashlib.sha256(
            self.candidate.read_bytes()).hexdigest()

        self.new_meta_sha = json.loads(
            self.candidate.read_text(encoding="utf-8"),
        )["members"][-1]["meta_sha256"]
        self.member_gate = self.tmp / "member_gate.json"
        self.ensemble_gate = self.tmp / "ensemble_gate.json"
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha,
                          "fit_start": _NEW_WINDOW[0],
                          "fit_end": _NEW_WINDOW[1]})
        self._write_gate(self.ensemble_gate, SCOPE_ENSEMBLE,
                         {"manifest_sha256": self.candidate_sha})

    _GATES_BY_SCOPE = {
        SCOPE_MEMBER: ("trainer_integrity", "ic_direction"),
        SCOPE_ENSEMBLE: ("degeneracy", "constraint_dry_run",
                         "serving_veto"),
    }

    _DEFAULT_WINDOW = {
        SCOPE_MEMBER: {"valid_start": _MEMBER_VALID[0],
                       "valid_end": _MEMBER_VALID[1]},
        SCOPE_ENSEMBLE: {"window_start": _TRAILING_QUARTER[0],
                         "window_end": _TRAILING_QUARTER[1]},
    }

    def _write_gate(self, path: Path, scope: str, subject: dict,
                    overall: str = "PASS",
                    profile: str = "csi800_n5",
                    window: dict | None = None) -> None:
        path.write_text(json.dumps({
            "schema_version": GATE_SCHEMA_VERSION,
            "profile": profile,
            "scope": scope, "subject": subject,
            "window": (self._DEFAULT_WINDOW[scope] if window is None
                       else window),
            "gates": {name: {"verdict": overall}
                      for name in self._GATES_BY_SCOPE[scope]},
            "overall": overall}), encoding="utf-8")

    def _execute(self, *, now: str = _NOW,
                 repo: Path | None = None) -> int:
        return rotate_main([
            "execute",
            "--manifest", str(self.manifest),
            "--candidate", str(self.candidate),
            "--member-gate", str(self.member_gate),
            "--ensemble-gate", str(self.ensemble_gate),
            "--repo", str(repo or self.repo),
            "--now", now,
        ])

    def _assert_manifest_untouched(self) -> None:
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())
        self.assertEqual([], list(self.tmp.glob("*.pre_rotation_*")))
        # The private staging copy (unique mkstemp name) must not
        # survive a refusal either.
        self.assertEqual([], list(self.tmp.glob("*.swap*")))

    def _rebind_incoming_sidecar(self, payload: dict) -> None:
        """Keep all other gates valid when testing source eligibility."""
        import hashlib

        self.new_meta.write_text(json.dumps(payload), encoding="utf-8")
        meta_sha = hashlib.sha256(self.new_meta.read_bytes()).hexdigest()
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["members"][-1]["meta_sha256"] = meta_sha
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")
        member_gate = json.loads(self.member_gate.read_text(encoding="utf-8"))
        member_gate["subject"]["meta_sha256"] = meta_sha
        self.member_gate.write_text(json.dumps(member_gate), encoding="utf-8")
        ensemble_gate = json.loads(self.ensemble_gate.read_text(encoding="utf-8"))
        ensemble_gate["subject"]["manifest_sha256"] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.ensemble_gate.write_text(json.dumps(ensemble_gate), encoding="utf-8")

    def _assert_source_refused_before_loading(self) -> None:
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        gates = {p: p.read_bytes() for p in (self.member_gate, self.ensemble_gate)}
        error = io.StringIO()
        with redirect_stderr(error), patch.object(rotation, "load_member_models") as loader:
            self.assertEqual(1, self._execute())
            loader.assert_not_called()
        self.assertIn("source", error.getvalue())
        self._assert_manifest_untouched()
        # The lockfile intentionally persists to avoid inode races; prove
        # the lock was released by acquiring it again after refusal.
        with rotation._ManifestLock(self.manifest):
            pass
        for path, raw in gates.items():
            self.assertEqual(raw, path.read_bytes())

    def _rebind_incoming_config(self, config: dict) -> None:
        """A truthful changed training run, not a broken digest-chain test."""
        import hashlib

        path = self.new_pkl.parent.parent / "config.yaml"
        path.write_text(json.dumps(config), encoding="utf-8")
        meta = json.loads(self.new_meta.read_text(encoding="utf-8"))
        meta["run_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self._rebind_incoming_sidecar(meta)

    def _assert_family_refused_before_loading(self) -> None:
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        gates = {p: p.read_bytes() for p in (self.member_gate, self.ensemble_gate)}
        error = io.StringIO()
        with redirect_stderr(error), patch.object(rotation, "load_member_models") as loader:
            self.assertEqual(1, self._execute())
            loader.assert_not_called()
        self.assertIn("family", error.getvalue())
        self._assert_manifest_untouched()
        with rotation._ManifestLock(self.manifest):
            pass
        for path, raw in gates.items():
            self.assertEqual(raw, path.read_bytes())

    def test_truthful_retuned_member_is_refused_before_loading(self) -> None:
        config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
        config["learning_rate"] = 0.05
        self._rebind_incoming_config(config)
        self._assert_family_refused_before_loading()

    def test_truthful_wrong_universe_is_refused_before_loading(self) -> None:
        config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
        config["instruments"] = "csi300"
        self._rebind_incoming_config(config)
        self._assert_family_refused_before_loading()

    def test_environment_cannot_register_a_different_provider(self) -> None:
        from unittest.mock import patch

        config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
        config["provider_uri"] = "/wrong/provider"
        self._rebind_incoming_config(config)
        # A local config and env override agree with the wrong trained family.
        (self.repo / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        with patch.dict(os.environ, {"QUANT_PROVIDER_URI": "/wrong/provider"}):
            self._assert_family_refused_before_loading()

    def test_every_fixed_family_field_rejects_drift_or_missing_actual(self) -> None:
        config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
        for key, value in {**_BASE_FAMILY, **_FAMILY_DEFAULTS, **_PRESET_FAMILY}.items():
            changed = not value if isinstance(value, bool) else (
                value + 1 if isinstance(value, (int, float)) else value + "_retuned")
            for case in ("changed", "missing", "null"):
                with self.subTest(key=key, case=case):
                    candidate = dict(config)
                    if case == "missing":
                        candidate.pop(key)
                    else:
                        candidate[key] = changed if case == "changed" else None
                    self._rebind_incoming_config(candidate)
                    self._assert_family_refused_before_loading()

    def _commit_registration(self) -> None:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "registration fixture change")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    def test_each_registered_file_must_exist_in_pinned_revision(self) -> None:
        for relpath in ("config.yaml", *_REGISTERED_PATHS):
            with self.subTest(path=relpath):
                path = self.repo / relpath
                original = path.read_bytes()
                path.unlink()
                self._commit_registration()
                # Even a restored worktree copy cannot repair the pinned evidence.
                path.write_bytes(original)
                self._assert_family_refused_before_loading()
                self._commit_registration()

    def test_malformed_registered_yaml_is_a_classified_family_refusal(self) -> None:
        for relpath in ("config.yaml", _REGISTERED_PATHS[0]):
            path = self.repo / relpath
            original = path.read_bytes()
            for raw in (b"\xff", b"bad: [", b"[]", b"null", b"a: 2026-02-30"):
                with self.subTest(path=relpath, raw=raw):
                    path.write_bytes(raw)
                    self._commit_registration()
                    self._assert_family_refused_before_loading()
            path.write_bytes(original)
            self._commit_registration()

    def test_missing_base_family_keys_do_not_gain_implicit_defaults(self) -> None:
        path = self.repo / "config.yaml"
        original = json.loads(path.read_text(encoding="utf-8"))
        for key in _BASE_FAMILY:
            with self.subTest(key=key):
                config = dict(original)
                config.pop(key)
                path.write_text(json.dumps(config), encoding="utf-8")
                self._commit_registration()
                self._assert_family_refused_before_loading()

    def test_registered_base_overrides_are_not_replaced_by_pinned_defaults(self) -> None:
        path = self.repo / "config.yaml"
        base = json.loads(path.read_text(encoding="utf-8"))
        for key, value in _FAMILY_DEFAULTS.items():
            with self.subTest(key=key):
                changed = value + 1 if isinstance(value, int) else value + "_new"
                path.write_text(json.dumps({**base, key: changed}), encoding="utf-8")
                self._commit_registration()
                self._assert_family_refused_before_loading()

    def test_incomplete_presets_cannot_all_drop_the_same_family_field(self) -> None:
        originals = {p: json.loads((self.repo / p).read_text(encoding="utf-8"))
                     for p in _REGISTERED_PATHS}
        for key in (*_PRESET_FAMILY, "extends"):
            with self.subTest(key=key):
                for relpath, original in originals.items():
                    config = dict(original)
                    config.pop(key)
                    (self.repo / relpath).write_text(json.dumps(config), encoding="utf-8")
                self._commit_registration()
                self._assert_family_refused_before_loading()

    def test_registered_presets_cannot_select_a_different_base(self) -> None:
        for relpath in _REGISTERED_PATHS:
            path = self.repo / relpath
            config = json.loads(path.read_text(encoding="utf-8"))
            config["extends"] = "../../config_walk.yaml"
            path.write_text(json.dumps(config), encoding="utf-8")
        self._commit_registration()
        self._assert_family_refused_before_loading()

    def test_committed_presets_must_agree_without_majority_voting(self) -> None:
        path = self.repo / _REGISTERED_PATHS[1]
        config = json.loads(path.read_text(encoding="utf-8"))
        config["compute_device"] = "cpu"
        path.write_text(json.dumps(config), encoding="utf-8")
        self._commit_registration()
        self._assert_family_refused_before_loading()

    def test_future_declared_preset_fields_are_not_silently_ignored(self) -> None:
        for relpath in _REGISTERED_PATHS:
            path = self.repo / relpath
            config = json.loads(path.read_text(encoding="utf-8"))
            config["future_registered_option"] = True
            path.write_text(json.dumps(config), encoding="utf-8")
        self._commit_registration()
        self._assert_family_refused_before_loading()

    def test_provider_requires_a_nonempty_committed_default(self) -> None:
        from unittest.mock import patch

        path = self.repo / "config.yaml"
        original = json.loads(path.read_text(encoding="utf-8"))
        for raw in ("${QUANT_PROVIDER_URI}", "${QUANT_PROVIDER_URI:-}", "", " ", None, []):
            with self.subTest(provider=raw):
                path.write_text(json.dumps({**original, "provider_uri": raw}), encoding="utf-8")
                self._commit_registration()
                with patch.dict(os.environ, {"QUANT_PROVIDER_URI": _BASE_FAMILY["provider_uri"]}):
                    self._assert_family_refused_before_loading()

    def test_equivalent_provider_spelling_and_all_six_rolling_dates_are_accepted(self) -> None:
        from unittest.mock import patch

        config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
        preset = json.loads((self.repo / _REGISTERED_PATHS[2]).read_text(encoding="utf-8"))
        for key in ("train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end"):
            self.assertNotEqual(preset[key], config[key])
        config["provider_uri"] = str(Path(_BASE_FAMILY["provider_uri"]).resolve()) + "/."
        self._rebind_incoming_config(config)
        with patch.dict(os.environ, {"QUANT_PROVIDER_URI": "/ignored/local/override"}):
            self.assertEqual(0, self._execute())

    def test_registered_family_uses_the_certification_pin_when_mainline_moves(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        path = self.repo / "config.yaml"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["learning_rate"] = 0.05
        path.write_text(json.dumps(config), encoding="utf-8")
        self._commit_registration()
        later = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.source_commit)
        original_git = rotation._git

        def move_after_pin(cmd, repo):
            result = original_git(cmd, repo)
            if cmd[1] == "rev-parse":
                _git(repo, "update-ref", "refs/remotes/origin/main", later)
            return result

        with patch.object(rotation, "_git", side_effect=move_after_pin), patch.object(
                rotation, "_registered_family_config", wraps=rotation._registered_family_config) as reads:
            self.assertEqual(0, self._execute())
        self.assertEqual(
            [(self.repo, self.source_commit, p) for p in ("config.yaml", *_REGISTERED_PATHS)],
            [call.args for call in reads.call_args_list])

    def test_family_reread_must_match_the_original_bound_sidecar(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        original_source = rotation._require_member_source

        def replace_config_and_sidecar_after_factual_check(sidecar, *, repo, rev):
            original_source(sidecar, repo=repo, rev=rev)
            config = json.loads((self.new_pkl.parent.parent / "config.yaml").read_text(encoding="utf-8"))
            # It is still a legal family, but these are not the gated bytes.
            config["test_end"] = "2026-09-01"
            self._rebind_incoming_config(config)

        with patch.object(rotation, "_require_member_source",
                          side_effect=replace_config_and_sidecar_after_factual_check):
            # The adversarial callback changes gate files; check refusal and
            # incumbent/staging only, not the normal immutable-gate assertion.
            with patch.object(rotation, "load_member_models") as loader:
                self.assertEqual(1, self._execute())
                loader.assert_not_called()
        self._assert_manifest_untouched()

    def test_family_reread_unavailable_is_a_classified_refusal(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        with patch.object(rotation, "read_member_run_config", return_value=None):
            self._assert_family_refused_before_loading()

    def test_family_git_failure_or_timeout_never_loads_or_installs(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        original_run = subprocess.run
        cases = (OSError("git unavailable"), subprocess.TimeoutExpired("git", 30),
                 subprocess.CompletedProcess([], 128, b"", b"unreadable \xff"))
        for failure in cases:
            with self.subTest(failure=type(failure).__name__):
                calls = []

                def fail_family_read(cmd, *, _failure=failure, _calls=calls, **kwargs):
                    if cmd[1] == "show" and cmd[-1].endswith(":config.yaml"):
                        _calls.append((cmd, kwargs))
                        if isinstance(_failure, Exception):
                            raise _failure
                        return _failure
                    return original_run(cmd, **kwargs)

                with patch.object(rotation.subprocess, "run", side_effect=fail_family_read):
                    self._assert_family_refused_before_loading()
                self.assertEqual(1, len(calls))
                self.assertEqual(["git", "show", f"{self.source_commit}:config.yaml"], calls[0][0])
                self.assertEqual(30, calls[0][1]["timeout"])

    def test_dirty_training_source_is_refused_before_loading(self) -> None:
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload["source_git_dirty"] = True
        self._rebind_incoming_sidecar(payload)
        self._assert_source_refused_before_loading()

    def test_missing_training_source_is_refused_before_loading(self) -> None:
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload.pop("source_git_commit")
        self._rebind_incoming_sidecar(payload)
        self._assert_source_refused_before_loading()

    def test_unmerged_training_source_is_refused_before_loading(self) -> None:
        _git(self.repo, "commit", "--allow-empty", "-q", "-m", "unmerged training source")
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload["source_git_commit"] = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self._rebind_incoming_sidecar(payload)
        self._assert_source_refused_before_loading()

    def test_malformed_training_source_is_refused_before_loading(self) -> None:
        original = json.loads(self.new_meta.read_text(encoding="utf-8"))
        cases = [
            ("source_git_dirty", None), ("source_git_dirty", 0),
            ("source_git_dirty", "False"), ("source_git_dirty", []),
            ("source_git_commit", None), ("source_git_commit", "abc123"),
            ("source_git_commit", "g" * 40), ("source_git_commit", "A" * 40),
            ("source_git_commit", 123), ("source_git_commit", []),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self._rebind_incoming_sidecar({**original, field: value})
                self._assert_source_refused_before_loading()
        for field in ("source_git_dirty", "source_git_commit"):
            with self.subTest(missing=field):
                payload = dict(original)
                payload.pop(field)
                self._rebind_incoming_sidecar(payload)
                self._assert_source_refused_before_loading()

    def test_unknown_training_commit_is_refused_before_loading(self) -> None:
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload["source_git_commit"] = "f" * 40
        self._rebind_incoming_sidecar(payload)
        self._assert_source_refused_before_loading()

    def test_divergent_training_branch_is_refused_before_loading(self) -> None:
        _git(self.repo, "commit", "--allow-empty", "-q", "-m", "unmerged source branch")
        unmerged = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        _git(self.repo, "switch", "-c", "registered", self.source_commit)
        _git(self.repo, "commit", "--allow-empty", "-q", "-m", "different mainline change")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload["source_git_commit"] = unmerged
        self._rebind_incoming_sidecar(payload)
        self._assert_source_refused_before_loading()

    def test_clean_training_ancestor_retains_legal_rotation(self) -> None:
        _git(self.repo, "commit", "--allow-empty", "-q", "-m", "new mainline revision")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(0, self._execute())
        self.assertEqual(self.candidate.read_bytes(), self.manifest.read_bytes())
        backups = list(self.tmp.glob("*.pre_rotation_*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(self.original_bytes, backups[0].read_bytes())

    def test_source_ancestry_uses_pinned_revision_when_mainline_moves(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        _git(self.repo, "commit", "--allow-empty", "-q", "-m", "later mainline source")
        later = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        payload = json.loads(self.new_meta.read_text(encoding="utf-8"))
        payload["source_git_commit"] = later
        self._rebind_incoming_sidecar(payload)
        original_git = rotation._git
        resolutions = []

        def move_after_pin(cmd, repo):
            result = original_git(cmd, repo)
            if cmd[1] == "rev-parse":
                resolutions.append(result.strip())
                _git(repo, "update-ref", "refs/remotes/origin/main", later)
            return result

        with patch.object(rotation, "_git", side_effect=move_after_pin):
            self._assert_source_refused_before_loading()
        self.assertEqual([self.source_commit], resolutions)
        self.assertEqual(later, subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "origin/main"], text=True).strip())

    def test_source_git_failures_are_classified_without_installation(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        original_run = subprocess.run
        cases = (OSError("git unavailable"),
                 subprocess.TimeoutExpired("git", 30),
                 subprocess.CompletedProcess([], 128, b"", b"bad history \xff"))
        for failure in cases:
            with self.subTest(failure=type(failure).__name__):
                ancestry_calls = []

                def fail_ancestry(cmd, *, _failure=failure, _calls=ancestry_calls, **kwargs):
                    if cmd[1:3] == ["merge-base", "--is-ancestor"]:
                        _calls.append((cmd, kwargs))
                        if isinstance(_failure, Exception):
                            raise _failure
                        return _failure
                    return original_run(cmd, **kwargs)

                with patch.object(rotation.subprocess, "run", side_effect=fail_ancestry):
                    self._assert_source_refused_before_loading()
                self.assertEqual(1, len(ancestry_calls))
                cmd, kwargs = ancestry_calls[0]
                self.assertEqual(["git", "merge-base", "--is-ancestor",
                                  self.source_commit, self.source_commit], cmd)
                self.assertEqual(self.repo, kwargs["cwd"])
                self.assertEqual(30, kwargs["timeout"])

    def test_legal_rotation_full_chain(self) -> None:
        import os
        import stat

        pre_stat = os.stat(self.manifest)
        pre_mode = stat.S_IMODE(pre_stat.st_mode)
        rc = self._execute()
        self.assertEqual(0, rc)
        # codex #391 r10/r11: the swap must not narrow the manifest's
        # permission bits to mkstemp's 0600 nor change its group (on
        # POSIX CI the mode check is a real 0644-vs-0600 assertion;
        # on Windows both are trivially equal).
        post_stat = os.stat(self.manifest)
        self.assertEqual(pre_mode, stat.S_IMODE(post_stat.st_mode))
        self.assertEqual(pre_stat.st_gid, post_stat.st_gid)
        self.assertEqual(pre_stat.st_uid, post_stat.st_uid)
        # Manifest now equals the candidate; oldest member is gone.
        rotated = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(
            self.candidate.read_bytes(), self.manifest.read_bytes())
        self.assertEqual(
            _NEW_WINDOW[1], rotated["members"][-1]["fit_end"])
        self.assertNotIn(
            "2024-12-18",
            [m["fit_end"] for m in rotated["members"]])
        # Backup carries the EXACT pre-rotation bytes; rollback is the
        # single step of restoring it.
        backups = list(self.tmp.glob(
            "production_manifest.json.pre_rotation_*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(self.original_bytes, backups[0].read_bytes())
        # codex #391 r15: the backup is the rollback artifact — it
        # must carry the live manifest's mode/owner/group, not the
        # executor's umask defaults.
        bstat = os.stat(backups[0])
        self.assertEqual(pre_mode, stat.S_IMODE(bstat.st_mode))
        self.assertEqual(pre_stat.st_gid, bstat.st_gid)
        self.assertEqual(pre_stat.st_uid, bstat.st_uid)
        backups[0].replace(self.manifest)          # single-step rollback
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())

    def test_missing_member_gate_refused(self) -> None:
        self.member_gate.unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_missing_ensemble_gate_refused(self) -> None:
        self.ensemble_gate.unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_failed_member_gate_refused(self) -> None:
        # codex #389 r11: the gate FAILED but the executor is invoked
        # anyway — the member must NOT enter production.
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha},
                         overall="FAIL")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_failed_ensemble_gate_refused(self) -> None:
        self._write_gate(self.ensemble_gate, SCOPE_ENSEMBLE,
                         {"manifest_sha256": self.candidate_sha},
                         overall="FAIL")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_unbound_member_gate_refused(self) -> None:
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": "de" * 32,
                          "meta_sha256": self.new_meta_sha})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_member_gate_meta_binding_mismatch_refused(self) -> None:
        # The gate judged a DIFFERENT sidecar than the one rotating in
        # (adversarial self-review: pkl-only binding would let a
        # regenerated sidecar ride an old artifact into production).
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": "de" * 32})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_lying_overall_gate_refused(self) -> None:
        # overall says PASS but a per-gate block says FAIL — refused.
        payload = json.loads(self.member_gate.read_text(encoding="utf-8"))
        payload["gates"]["ic_direction"]["verdict"] = "FAIL"
        self.member_gate.write_text(json.dumps(payload),
                                    encoding="utf-8")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_bad_manifest_inputs_refused_not_traceback(self) -> None:
        # codex #391 r4: typo'd/missing/corrupt manifest inputs are
        # ordinary precondition failures — classified refusal (exit 1),
        # never an escaping traceback.
        missing = self.tmp / "no_such_manifest.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(missing),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "x.json"),
        ])
        self.assertEqual(1, rc)
        # codex #391 r6: a live manifest with a wrong/missing schema
        # must not be silently converted into a fresh v1 candidate.
        for schema in ("v0", None):
            payload = json.loads(self.manifest.read_text(
                encoding="utf-8"))
            if schema is None:
                del payload["schema_version"]
            else:
                payload["schema_version"] = schema
            stale = self.tmp / f"stale_schema_{schema}.json"
            stale.write_text(json.dumps(payload), encoding="utf-8")
            rc = rotate_main([
                "plan",
                "--manifest", str(stale),
                "--new-pkl", str(self.new_pkl),
                "--new-meta", str(self.new_meta),
                "--fit-start", _NEW_WINDOW[0],
                "--fit-end", _NEW_WINDOW[1],
                "--out", str(self.tmp / f"s_{schema}.json"),
            ])
            self.assertEqual(1, rc, schema)
            self.assertFalse((self.tmp / f"s_{schema}.json").exists())
        corrupt = self.tmp / "corrupt_manifest.json"
        corrupt.write_text("not json {", encoding="utf-8")
        rc = rotate_main([
            "plan",
            "--manifest", str(corrupt),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "y.json"),
        ])
        self.assertEqual(1, rc)
        # Same class on execute: missing new-member artifacts refuse.
        self.new_pkl.unlink()
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.tmp / "z.json"),
        ])
        self.assertEqual(1, rc)

    def test_plan_out_aliasing_live_manifest_refused(self) -> None:
        # codex #391 r2: plan --out pointed at the live manifest would
        # overwrite production during PLANNING, before certification/
        # gates/backup — refused with the manifest untouched.
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(self.manifest),
        ])
        self.assertEqual(1, rc)
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())

    def test_illegal_plan_produces_no_candidate_file(self) -> None:
        # Adversarial self-review: an illegal rotation must never even
        # publish a candidate file at --out (a stale invalid candidate
        # is an attractive wrong input for the next session).
        bad_out = self.tmp / "bad_candidate.json"
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            # Same quarter as the current newest member — violates the
            # strictly-increasing fit_end pin in the serving loader.
            "--fit-start", _CURRENT_WINDOWS[-1][0],
            "--fit-end", _CURRENT_WINDOWS[-1][1],
            "--out", str(bad_out),
        ])
        self.assertEqual(1, rc)
        self.assertFalse(bad_out.exists())
        self.assertFalse(
            bad_out.with_suffix(bad_out.suffix + ".tmp").exists())

    def test_lose_status_freezes(self) -> None:
        lose = _win_status()
        lose["verdict"] = "LOSE"
        del lose["verdict_sidecar_path"]
        del lose["verdict_sidecar_sha256"]
        repo = self.tmp / "mainline_lose"
        _make_mainline_repo(repo, status=lose)
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_expired_win_freezes(self) -> None:
        # Commit instant pinned at 2026-07-01 (+15 months = 2027-10-01);
        # the day after that window the WIN is expired.
        self.assertEqual(
            1, self._execute(now="2027-10-02T00:00:00+00:00"))
        self._assert_manifest_untouched()

    def test_absent_status_artifact_refused(self) -> None:
        repo = self.tmp / "mainline_empty"
        _make_mainline_repo(repo, status=None)
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_non_utf8_status_bytes_refused(self) -> None:
        # codex #391 r5: a status artifact whose bytes are not UTF-8
        # is malformed certification state — classified refusal, not
        # an escaping UnicodeDecodeError traceback.
        repo = self.tmp / "mainline_binary"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(repo)],
                       check=True, capture_output=True)
        target = repo / RECERT_STATUS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xfe\x00corrupt")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "binary status")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(1, self._execute(repo=repo))
        self._assert_manifest_untouched()

    def test_wrong_profile_gate_refused(self) -> None:
        # codex #391 r12: a gate artifact measured under csi300_daily
        # semantics must not authorize a csi800_n5 rotation.
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha,
                          "fit_start": _NEW_WINDOW[0],
                          "fit_end": _NEW_WINDOW[1]},
                         profile="csi300_daily")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_fit_window_gate_mismatch_refused(self) -> None:
        # codex #391 r12: the member gate evaluated a different fit
        # window than the candidate installs — refuse (serving derives
        # the inference normalization window from these dates).
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha,
                          "fit_start": "2023-09-21",
                          "fit_end": _NEW_WINDOW[1]})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_concurrent_lock_holder_refuses_second_execute(self) -> None:
        # codex #391 r13: the exclusive manifest lock serializes
        # rotations — a second execute refuses at acquisition while
        # the lock is held, closing the recheck->replace window for
        # executor-vs-executor races.
        from scripts.rotate_ensemble_member import _ManifestLock

        with _ManifestLock(self.manifest):
            self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()
        # Lock released: the same rotation now succeeds.
        self.assertEqual(0, self._execute())

    def test_gate_measured_on_wrong_window_refused(self) -> None:
        # codex #391 r19: PASS artifacts whose DIGESTS bind correctly
        # but that were measured on an arbitrary (easier/stale) period
        # must not authorize a rotation. codex's reproduction: both
        # windows moved to 1900.
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha,
                          "fit_start": _NEW_WINDOW[0],
                          "fit_end": _NEW_WINDOW[1]},
                         window={"valid_start": "1900-01-01",
                                 "valid_end": "1900-03-31"})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_ensemble_gate_stale_window_refused(self) -> None:
        # The trailing quarter legitimately overlaps training data, so
        # it is bound by RECENCY: a two-year-old dry run cannot gate
        # today's rotation.
        self._write_gate(self.ensemble_gate, SCOPE_ENSEMBLE,
                         {"manifest_sha256": self.candidate_sha},
                         window={"window_start": "2024-05-01",
                                 "window_end": "2024-07-31"})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_member_gate_window_not_out_of_sample_refused(self) -> None:
        # The IC gate's valid window must FOLLOW the training window.
        self._write_gate(self.member_gate, SCOPE_MEMBER,
                         {"pkl_sha256": self.new_pkl_sha,
                          "meta_sha256": self.new_meta_sha,
                          "fit_start": _NEW_WINDOW[0],
                          "fit_end": _NEW_WINDOW[1]},
                         window={"valid_start": "2026-02-01",
                                 "valid_end": "2026-06-01"})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_missing_window_block_refused(self) -> None:
        payload = json.loads(
            self.ensemble_gate.read_text(encoding="utf-8"))
        del payload["window"]
        self.ensemble_gate.write_text(json.dumps(payload),
                                      encoding="utf-8")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_recert_reads_pinned_to_one_revision(self) -> None:
        # codex #391 r25: origin/main is a MOVING ref — the executor
        # resolves it once and every later read carries that commit
        # id, so a concurrent fetch cannot pair an old WIN body with a
        # newer commit's anchor date.
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        real_git = rem._git
        calls: list[list[str]] = []

        def spy(cmd, repo):  # noqa: ANN001
            calls.append(list(cmd))
            return real_git(cmd, repo)

        with patch.object(rem, "_git", side_effect=spy):
            rc = self._execute()
        self.assertEqual(0, rc)
        self.assertIn("rev-parse", calls[0])
        self.assertTrue(
            any("origin/main" in part for part in calls[0]),
            "the resolution step must name the mainline")
        # Every read AFTER the resolution is pinned to a commit id.
        for cmd in calls[1:]:
            self.assertNotIn("origin/main", " ".join(cmd), cmd)

    def test_unusable_lockfile_refused(self) -> None:
        # codex #391 r25: the lock path being a directory (or
        # otherwise unopenable) is an operator/filesystem precondition
        # — classified refusal, not a raw traceback.
        (self.tmp / "production_manifest.json.rotation.lock").mkdir()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_execute_staging_creation_failure_refused(self) -> None:
        # codex #391 r24: an operator path mistake (manifest under a
        # directory that does not exist) must surface as the
        # classified refusal, not a raw FileNotFoundError traceback.
        rc = rotate_main([
            "execute",
            "--manifest", str(self.tmp / "no_such_dir" / "m.json"),
            "--candidate", str(self.candidate),
            "--member-gate", str(self.member_gate),
            "--ensemble-gate", str(self.ensemble_gate),
            "--repo", str(self.repo),
            "--now", _NOW,
        ])
        self.assertEqual(1, rc)
        self._assert_manifest_untouched()

    def test_execute_staging_write_failure_refused(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        real_close = os.close

        def boom(fd, *a, **kw):  # noqa: ANN001, ANN002
            real_close(fd)
            raise OSError("no space left")

        with patch.object(rem.os, "fdopen", side_effect=boom):
            rc = self._execute()
        self.assertEqual(1, rc)
        self._assert_manifest_untouched()

    def test_plan_staging_is_unique_not_predictable(self) -> None:
        # codex #391 r22: a PREDICTABLE `<out>.tmp` that already
        # exists as a link to the live manifest would be followed and
        # truncated during the supposedly inert plan step. Staging is
        # now an exclusively-created unique file, so a pre-placed
        # `<out>.tmp` is left completely untouched.
        out = self.tmp / "unique_stage_candidate.json"
        decoy = out.with_suffix(out.suffix + ".tmp")
        decoy.write_bytes(b"decoy-must-not-be-touched")
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(out),
        ])
        self.assertEqual(0, rc)
        self.assertEqual(b"decoy-must-not-be-touched",
                         decoy.read_bytes())
        self.assertTrue(out.is_file())
        # No staging residue on the success path either.
        self.assertEqual([], list(self.tmp.glob("*.stage.*")))

    def test_plan_out_hardlinked_to_manifest_refused(self) -> None:
        # The alias guard is inode-level: a HARDLINK resolves to a
        # distinct path but names the same file (codex #391 r22).
        import os as _os

        linked = self.tmp / "hardlinked_out.json"
        try:
            _os.link(self.manifest, linked)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlinks unavailable here: {exc}")
        rc = rotate_main([
            "plan",
            "--manifest", str(self.manifest),
            "--new-pkl", str(self.new_pkl),
            "--new-meta", str(self.new_meta),
            "--fit-start", _NEW_WINDOW[0],
            "--fit-end", _NEW_WINDOW[1],
            "--out", str(linked),
        ])
        self.assertEqual(1, rc)
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())

    def test_plan_staging_write_failure_refused(self) -> None:
        # codex #391 r21: a staging write that fails partway
        # (ENOSPC/quota/late I/O) must refuse through the classified
        # path and clean up the partial file, not escape as a raw
        # OSError leaving an unnamed artifact.
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        real_close = os.close

        def boom(fd, *a, **kw):  # noqa: ANN001, ANN002
            # Model a write failure with the handle already gone (a
            # leaked fd would keep the file locked on Windows).
            real_close(fd)
            raise OSError("no space left")

        out = self.tmp / "staged_candidate.json"
        with patch.object(rem.os, "fdopen", side_effect=boom):
            rc = rotate_main([
                "plan",
                "--manifest", str(self.manifest),
                "--new-pkl", str(self.new_pkl),
                "--new-meta", str(self.new_meta),
                "--fit-start", _NEW_WINDOW[0],
                "--fit-end", _NEW_WINDOW[1],
                "--out", str(out),
            ])
        self.assertEqual(1, rc)
        self.assertFalse(out.exists())
        self.assertEqual([], list(self.tmp.glob("*.stage.*")))

    def test_plan_cleanup_failure_still_classified_refusal(self) -> None:
        # codex #391 r20: plan's staging cleanup follows the same
        # discipline — an unlink that itself fails must not mask the
        # classified refusal with a raw OSError.
        from unittest.mock import patch

        real_unlink = Path.unlink
        # Match the ACTUAL staging name (codex #391 r23: the stub used
        # to match the retired `<out>.tmp`, so it exercised the normal
        # cleanup path and would have missed a regression here).
        refused: list[str] = []

        def stubborn(self, *a, **kw):  # noqa: ANN001, ANN002
            if ".stage." in self.name:
                refused.append(self.name)
                raise OSError("locked staging file")
            return real_unlink(self, *a, **kw)

        bad_out = self.tmp / "bad_candidate.json"
        with patch.object(Path, "unlink", stubborn):
            rc = rotate_main([
                "plan",
                "--manifest", str(self.manifest),
                "--new-pkl", str(self.new_pkl),
                "--new-meta", str(self.new_meta),
                # Same fit_end as the current newest member — violates
                # the strictly-increasing pin, so validation refuses.
                "--fit-start", _CURRENT_WINDOWS[-1][0],
                "--fit-end", _CURRENT_WINDOWS[-1][1],
                "--out", str(bad_out),
            ])
        self.assertEqual(1, rc)
        self.assertFalse(bad_out.exists())
        # The stub MUST have fired — otherwise this test silently
        # stops covering the cleanup-failure path.
        self.assertTrue(refused, "cleanup-failure stub never fired")
        # The surviving staging file is the one the refusal names; the
        # test owns the temp dir, so remove it here.
        for leftover in self.tmp.glob("*.stage.*"):
            leftover.unlink()

    def test_cleanup_failure_still_classified_refusal(self) -> None:
        # codex #391 r19: a cleanup that itself raises (read-only
        # backup on Windows) must not mask the classified refusal with
        # a raw OSError — the executor still exits 1 and names the
        # surviving file.
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        real_unlink = Path.unlink
        refused: list[str] = []

        def stubborn(self, *a, **kw):  # noqa: ANN001, ANN002
            if ".pre_rotation_" in self.name:
                refused.append(self.name)
                raise OSError("read-only rollback artifact")
            return real_unlink(self, *a, **kw)

        with patch.object(rem.os, "replace",
                          side_effect=OSError("locked")), \
                patch.object(Path, "unlink", stubborn):
            rc = self._execute()
        self.assertEqual(1, rc)
        self.assertEqual(self.original_bytes,
                         self.manifest.read_bytes())
        # The stub MUST have fired (codex #391 r23: a stale predicate
        # would silently reduce this to the normal cleanup path).
        self.assertTrue(refused, "cleanup-failure stub never fired")
        for leftover in self.tmp.glob("*.pre_rotation_*"):
            leftover.unlink()

    def test_install_failure_refuses_with_zero_residue(self) -> None:
        # codex #391 r18: the final os.replace can fail (Windows/NFS
        # lock, permission drift, I/O). The manifest is then UNCHANGED,
        # so the just-written backup is meaningless residue — refuse
        # through the classified path and leave nothing behind.
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        with patch.object(rem.os, "replace",
                          side_effect=OSError("locked by another "
                                              "process")):
            rc = self._execute()
        self.assertEqual(1, rc)
        self._assert_manifest_untouched()

    def test_live_manifest_changed_mid_execute_refused(self) -> None:
        # codex #391 r12: a concurrent rotation/manual edit between
        # adjudication and swap must refuse, not be clobbered. The
        # member-chain re-validation hook is the deterministic
        # injection point for the mid-run edit.
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rem

        real = rem.load_member_models
        edited = json.dumps({"schema_version":
                             "csi800_n5_ensemble_manifest_v1",
                             "members": []})

        def sneaky(members):  # noqa: ANN001
            self.manifest.write_text(edited, encoding="utf-8")
            return real(members)

        with patch.object(rem, "load_member_models",
                          side_effect=sneaky):
            rc = self._execute()
        self.assertEqual(1, rc)
        # The intervening edit survives (not clobbered by the
        # candidate), and no backup/staging residue remains.
        self.assertEqual(edited,
                         self.manifest.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.tmp.glob("*.pre_rotation_*")))
        self.assertEqual([], list(self.tmp.glob("*.swap*")))

    def test_member_config_edited_after_gates_refuses_without_install(self) -> None:
        # Check both a surviving member and the incoming one. A previous
        # PASS gate cannot authorize config bytes changed after training.
        for pkl in (Path(self.kept_members[0]["pkl_path"]), self.new_pkl):
            with self.subTest(member=pkl):
                config = pkl.parent.parent / "config.yaml"
                original = config.read_bytes()
                try:
                    config.write_bytes(original + b"\n# post-gate edit\n")
                    self.assertEqual(1, self._execute())
                    self._assert_manifest_untouched()
                finally:
                    config.write_bytes(original)

    def test_member_config_missing_after_gates_refuses_without_install(self) -> None:
        (self.new_pkl.parent.parent / "config.yaml").unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_legal_but_wrong_member_valid_window_refuses_before_loading(self) -> None:
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        # Still satisfies every broad span/gap/recency rule, but this is
        # not the config-bound validation window actually used by training.
        artifact = json.loads(self.member_gate.read_text(encoding="utf-8"))
        artifact["window"] = {"valid_start": "2026-05-06", "valid_end": "2026-07-31"}
        self.member_gate.write_text(json.dumps(artifact), encoding="utf-8")
        with patch.object(rotation, "load_member_models", wraps=rotation.load_member_models) as loader:
            self.assertEqual(1, self._execute())
            loader.assert_not_called()
        self._assert_manifest_untouched()

    def test_rotation_missing_bound_valid_date_refuses_before_loading(self) -> None:
        import hashlib
        from unittest.mock import patch

        import scripts.rotate_ensemble_member as rotation

        # Rebind the whole fixture chain, so failure reaches missing valid
        # evidence instead of an earlier config/sidecar/manifest digest check.
        config = self.new_pkl.parent.parent / "config.yaml"
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload.pop("valid_end")
        config.write_text(json.dumps(payload), encoding="utf-8")
        meta = json.loads(self.new_meta.read_text(encoding="utf-8"))
        meta["run_config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
        self.new_meta.write_text(json.dumps(meta), encoding="utf-8")
        meta_sha = hashlib.sha256(self.new_meta.read_bytes()).hexdigest()
        candidate = json.loads(self.candidate.read_text(encoding="utf-8"))
        candidate["members"][-1]["meta_sha256"] = meta_sha
        self.candidate.write_text(json.dumps(candidate), encoding="utf-8")
        member_gate = json.loads(self.member_gate.read_text(encoding="utf-8"))
        member_gate["subject"]["meta_sha256"] = meta_sha
        self.member_gate.write_text(json.dumps(member_gate), encoding="utf-8")
        ensemble_gate = json.loads(self.ensemble_gate.read_text(encoding="utf-8"))
        ensemble_gate["subject"]["manifest_sha256"] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.ensemble_gate.write_text(json.dumps(ensemble_gate), encoding="utf-8")
        with patch.object(rotation, "load_member_models", wraps=rotation.load_member_models) as loader:
            self.assertEqual(1, self._execute())
            loader.assert_not_called()
        self._assert_manifest_untouched()

    def test_member_pkl_deleted_after_gates_refused(self) -> None:
        # codex #391 r9: the gates proved the members were valid when
        # they RAN — a pkl deleted since then must refuse at execute,
        # or serving would refuse the freshly installed manifest at
        # the next morning run.
        Path(self.kept_members[0]["pkl_path"]).unlink()
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_member_sidecar_replaced_after_gates_refused(self) -> None:
        # A sidecar rewritten after gate time breaks the digest chain
        # the candidate manifest declares — refuse, zero writes.
        Path(self.kept_members[1]["meta_path"]).write_text(
            json.dumps({"schema_version": "v1",
                        "model_type": "LGBModel"}), encoding="utf-8")
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()

    def test_tampered_candidate_refused(self) -> None:
        # Keep the serving-loader pins satisfied but break the plan
        # equality: slot 0 of the candidate is NOT current[1].
        payload = json.loads(self.candidate.read_text(encoding="utf-8"))
        payload["members"][0]["meta_sha256"] = "9b" * 32
        self.candidate.write_text(json.dumps(payload, indent=2),
                                  encoding="utf-8")
        import hashlib
        self._write_gate(
            self.ensemble_gate, SCOPE_ENSEMBLE,
            {"manifest_sha256": hashlib.sha256(
                self.candidate.read_bytes()).hexdigest()})
        self.assertEqual(1, self._execute())
        self._assert_manifest_untouched()


if __name__ == "__main__":
    unittest.main()

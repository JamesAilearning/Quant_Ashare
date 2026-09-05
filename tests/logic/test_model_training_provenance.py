"""Member gate evidence binding with synthetic runs and no live data/compute."""

from __future__ import annotations

import hashlib
import json
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def member_run(tmp_path):
    run = tmp_path / "run"
    (run / "artifacts").mkdir(parents=True)
    config = run / "config.yaml"
    dates = {
        "train_start": "2024-05-02", "train_end": "2026-05-01",
        "valid_start": "2026-05-05", "valid_end": "2026-07-30",
    }
    config.write_text(json.dumps(dates), encoding="utf-8")
    pkl = run / "artifacts" / "model.pkl"
    # Unpickling is stubbed; hashing/binding still use the actual byte buffer.
    pkl.write_bytes(b"synthetic member bytes")
    meta = pkl.with_suffix(".pkl.meta.json")
    sidecar = {
        "model_type": "LGBModel", "best_iteration": 10,
        "num_boost_round": 100, "final_valid_loss": 0.1,
        "pkl_sha256": hashlib.sha256(pkl.read_bytes()).hexdigest(),
        "run_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    meta.write_text(json.dumps(sidecar), encoding="utf-8")
    return SimpleNamespace(config=config, pkl=pkl, meta=meta, dates=dates,
                           sidecar=sidecar, out=tmp_path / "gate.json")


@pytest.fixture
def scoring_stubs(monkeypatch):
    import pandas as pd

    import scripts.retrain_gate as gate

    dataset = Mock(return_value=object())
    model = Mock()
    model.predict.return_value = pd.Series([0.1, 0.2])
    loads = Mock(return_value=model)
    analyzer = Mock(return_value=SimpleNamespace(ic_summary={1: {"mean_ic": 0.05}}))
    monkeypatch.setattr(gate, "_scoring_dataset", dataset)
    monkeypatch.setattr(pickle, "loads", loads)
    monkeypatch.setattr(gate.SignalAnalyzer, "analyze", analyzer)
    return SimpleNamespace(dataset=dataset, loads=loads, model=model, analyzer=analyzer)


def _member_cli(run, **date_overrides):
    import scripts.retrain_gate as gate

    dates = {
        "fit_start": run.dates["train_start"], "fit_end": run.dates["train_end"],
        "valid_start": run.dates["valid_start"], "valid_end": run.dates["valid_end"],
        **date_overrides,
    }
    argv = ["--scope", "member", "--member-pkl", str(run.pkl),
            "--member-meta", str(run.meta), "--out", str(run.out)]
    for key, value in dates.items():
        argv.extend(["--" + key.replace("_", "-"), value])
    return gate.main(argv)


@pytest.mark.parametrize("field,value", [
    ("fit_start", "2024-05-03"), ("fit_end", "2026-04-30"),
    ("valid_start", "2026-05-06"), ("valid_end", "2026-07-31"),
])
def test_member_gate_refuses_dates_not_used_by_training_run(member_run, scoring_stubs, field, value):
    assert _member_cli(member_run, **{field: value}) == 1
    artifact = json.loads(member_run.out.read_text(encoding="utf-8"))
    assert artifact["overall"] == "FAIL"
    assert artifact["gates"]["ic_direction"]["ic_1d"] is None
    assert "not measured" in " ".join(artifact["gates"]["ic_direction"]["reasons"])
    scoring_stubs.dataset.assert_not_called()
    scoring_stubs.loads.assert_not_called()


def _assert_unmeasured_failure(run, stubs):
    assert _member_cli(run) == 1
    artifact = json.loads(run.out.read_text(encoding="utf-8"))
    ic = artifact["gates"]["ic_direction"]
    assert artifact["overall"] == "FAIL"
    assert set(artifact["gates"]) == {"trainer_integrity", "ic_direction"}
    assert ic["verdict"] == "FAIL"
    assert ic["ic_1d"] is None
    assert "not measured" in " ".join(ic["reasons"])
    stubs.dataset.assert_not_called()
    stubs.loads.assert_not_called()
    stubs.analyzer.assert_not_called()
    return artifact


@pytest.mark.parametrize("case", [
    "missing_config", "config_directory", "shadow_config", "flat_model",
    "bad_yaml", "invalid_timestamp", "non_mapping", "bad_utf8", "edited_config",
    "missing_meta", "bad_json_meta", "non_object_meta", "wrong_pkl_digest",
    "missing_config_digest", "wrong_config_digest",
])
def test_unbindable_member_evidence_writes_fail_without_scoring(member_run, scoring_stubs, case):
    run = member_run
    if case in ("missing_config", "config_directory"):
        run.config.unlink()
        if case == "config_directory":
            run.config.mkdir()
    elif case == "shadow_config":
        (run.pkl.parent / "config.yaml").write_bytes(run.config.read_bytes())
    elif case == "flat_model":
        moved = run.pkl.parent.parent / "model.pkl"
        run.pkl.rename(moved)
        run.pkl = moved
    elif case in ("bad_yaml", "invalid_timestamp", "non_mapping", "bad_utf8"):
        run.config.write_bytes({
            "bad_yaml": b"start: [unfinished", "invalid_timestamp": b"start: 2026-02-30",
            "non_mapping": b"[]", "bad_utf8": b"\xff",
        }[case])
        run.sidecar["run_config_sha256"] = hashlib.sha256(run.config.read_bytes()).hexdigest()
        run.meta.write_text(json.dumps(run.sidecar), encoding="utf-8")
    elif case == "edited_config":
        run.config.write_bytes(run.config.read_bytes() + b"\n# post-training edit\n")
    elif case == "missing_meta":
        run.meta.unlink()
    elif case in ("bad_json_meta", "non_object_meta"):
        run.meta.write_text("{" if case == "bad_json_meta" else "[]", encoding="utf-8")
    else:
        if case == "wrong_pkl_digest":
            run.sidecar["pkl_sha256"] = "ff" * 32
        elif case == "missing_config_digest":
            run.sidecar.pop("run_config_sha256")
        else:
            run.sidecar["run_config_sha256"] = "ff" * 32
        run.meta.write_text(json.dumps(run.sidecar), encoding="utf-8")
    artifact = _assert_unmeasured_failure(run, scoring_stubs)
    if case == "missing_meta":
        assert artifact["subject"]["meta_sha256"] is None
        assert artifact["gates"]["trainer_integrity"]["verdict"] == "FAIL"


@pytest.mark.parametrize("field", ["valid_start", "valid_end"])
@pytest.mark.parametrize("value", [None, 20260505, True, "", "2026-02-30", "20260505", "2026-W19-2"])
def test_invalid_bound_validation_date_is_not_measured(member_run, scoring_stubs, field, value):
    payload = dict(member_run.dates)
    if value is None:
        payload.pop(field)
    else:
        payload[field] = value
    member_run.config.write_text(json.dumps(payload), encoding="utf-8")
    member_run.sidecar["run_config_sha256"] = hashlib.sha256(member_run.config.read_bytes()).hexdigest()
    member_run.meta.write_text(json.dumps(member_run.sidecar), encoding="utf-8")
    artifact = _assert_unmeasured_failure(member_run, scoring_stubs)
    assert field in " ".join(artifact["gates"]["ic_direction"]["reasons"])


@pytest.mark.parametrize("target,operation", [
    ("config", "is_file"), ("config", "read_bytes"), ("meta", "read_bytes"),
])
def test_member_evidence_permission_error_retains_fail_artifact(
        member_run, scoring_stubs, monkeypatch, target, operation):
    denied_path = getattr(member_run, target)
    original = getattr(Path, operation)

    def denied(path):
        if path == denied_path:
            raise PermissionError("evidence unreadable")
        return original(path)

    monkeypatch.setattr(Path, operation, denied)
    _assert_unmeasured_failure(member_run, scoring_stubs)


def test_bound_member_scores_exact_windows_and_each_evidence_buffer_once(member_run, scoring_stubs, monkeypatch):
    originals = {p: p.read_bytes() for p in (member_run.pkl, member_run.meta, member_run.config)}
    counts = dict.fromkeys(originals, 0)
    original = Path.read_bytes

    def single_read(path):
        if path in counts:
            counts[path] += 1
            assert counts[path] == 1, "evidence was re-read"
            return originals[path]
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", single_read)
    assert _member_cli(member_run) == 0
    artifact = json.loads(member_run.out.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "csi800_n5_retrain_gate_v1"
    assert artifact["overall"] == "PASS"
    assert artifact["gates"]["ic_direction"]["ic_1d"] == 0.05
    assert artifact["window"] == {k: member_run.dates[k] for k in ("valid_start", "valid_end")}
    assert artifact["subject"]["pkl_sha256"] == hashlib.sha256(originals[member_run.pkl]).hexdigest()
    assert artifact["subject"]["meta_sha256"] == hashlib.sha256(originals[member_run.meta]).hexdigest()
    assert list(counts.values()) == [1, 1, 1]
    scoring_stubs.loads.assert_called_once_with(originals[member_run.pkl])
    assert scoring_stubs.dataset.call_args.kwargs == {
        "fit_start": member_run.dates["train_start"], "fit_end": member_run.dates["train_end"],
        "score_start": member_run.dates["valid_start"], "score_end": member_run.dates["valid_end"],
    }


def test_missing_member_pickle_remains_tool_error_without_artifact(member_run, scoring_stubs):
    member_run.pkl.unlink()
    with pytest.raises(OSError):
        _member_cli(member_run)
    assert not member_run.out.exists()
    scoring_stubs.dataset.assert_not_called()
    scoring_stubs.loads.assert_not_called()


@pytest.mark.parametrize("model_type", [[], {}])
def test_corrupt_model_type_cannot_hide_unbindable_gate_failure(member_run, scoring_stubs, model_type):
    member_run.sidecar["model_type"] = model_type
    member_run.meta.write_text(json.dumps(member_run.sidecar), encoding="utf-8")
    member_run.config.unlink()
    artifact = _assert_unmeasured_failure(member_run, scoring_stubs)
    assert artifact["gates"]["trainer_integrity"]["verdict"] == "FAIL"

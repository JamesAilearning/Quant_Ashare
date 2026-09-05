"""Pure bootstrap-cutover logic (PR-C' of
2026-07-20-csi800-n5-production-promotion).

The FIRST production switch is a PROMOTION path, not the quarterly
maintenance path (codex #389 r8): its preconditions are the full
promotion gate set —

  1. campaign eligibility — the committed verdict sidecar passes
     ``csi800_campaign_certify.py --verify`` and carries
     ``promotion_eligible: true``;
  2. iso_week re-check anchor — the committed re-check evidence, read
     from the mainline at a pinned revision, binds the committed
     iso_week preset and re-derives a POSITIVE full-window net
     excess;
  3. bootstrap gates — three member-scope artifacts (one per
     staggered member) and one ensemble-scope artifact, every one
     PASS and bound to what it gated;
  4. rollback kit — pre-promote backup of the incumbent + a committed
     baseline record.

Any failure = the switch does not execute, the incumbent canonical
and its serving semantics stay unchanged, and the failure is filed
(R1-DP-C: the bootstrap has no "keep the old ensemble" branch — that
action belongs to the quarterly maintenance path).

Decision helpers (with lazy canonical provider normalization): the executor
(``scripts/bootstrap_ensemble_cutover.py``) wires git, the filesystem
and the serving loader around these decisions so every rule is
unit-testable without a bundle.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

# The PRE-REGISTERED bootstrap trio (R1-DP-C, windows frozen before
# ignition). Read at the pinned mainline revision: a locally edited
# preset must not be able to authorize a differently-windowed trio.
BOOTSTRAP_PRESET_PATHS = (
    "config/presets/csi800_n5_bootstrap_m1.yaml",
    "config/presets/csi800_n5_bootstrap_m2.yaml",
    "config/presets/csi800_n5_bootstrap_m3.yaml",
)

__all__ = [
    "BOOTSTRAP_MEMBER_COUNT",
    "RECERT_STATUS_SCHEMA_VERSION",
    "BASELINE_SCHEMA_VERSION",
    "CutoverRefusal",
    "check_cutover_paths",
    "check_write_targets",
    "check_evidence_provenance",
    "check_preregistered_windows",
    "check_preregistered_gate_windows",
    "SAME_FAMILY_KEYS",
    "SAME_FAMILY_DEFAULTS",
    "check_member_training_config",
    "check_campaign_eligibility",
    "check_isoweek_anchor",
    "build_initial_status",
    "build_baseline_record",
    "build_inference_meta",
]

BOOTSTRAP_MEMBER_COUNT = 3
# Written by THIS path only (first write; the quarterly executor reads
# it and never writes it — R1-DP-D).
RECERT_STATUS_SCHEMA_VERSION = "csi800_recert_status_v1"
BASELINE_SCHEMA_VERSION = "csi800_n5_bootstrap_baseline_v1"

_VERDICT_SCHEMA_VERSION = "csi800_cadence_verdict_v1"


class CutoverRefusal(RuntimeError):
    """A promotion precondition failed — ZERO production writes."""


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def check_cutover_paths(
    *, incumbent_exists: bool, manifest_out_exists: bool,
    status_exists: bool, baseline_exists: bool,
    incumbent: str, manifest_out: str, status_path: str,
    baseline: str,
) -> None:
    """Path preconditions, adjudicated with the OTHER gates — i.e.
    BEFORE any production write (adversarial self-review).

    The bootstrap is a once-ever switch, so:

    * the incumbent must exist (no rollback kit, no switch);
    * the production manifest must NOT exist yet — if it does, either
      a previous bootstrap already ran or a quarterly rotation owns
      it, and re-installing the bootstrap trio would silently revert
      production;
    * the certification-status artifact must NOT exist — its first
      write belongs to THIS path and a later state belongs to the
      annual re-certification flow (R1-DP-D);
    * the baseline record must NOT exist — a survivor from an aborted
      run (or a previous bootstrap) is not ours to truncate or to
      roll-back-delete (codex #392 r14).

    Checking these here (rather than mid-write) is what keeps
    ``--dry-run`` honest and the refusal zero-write: the prior art's
    rule is that every fallible check precedes the first byte
    (``rotate_ensemble_member``)."""
    if not incumbent_exists:
        raise CutoverRefusal(
            f"incumbent canonical not found: {incumbent} — refusing to "
            "switch without a rollback kit")
    if manifest_out_exists:
        raise CutoverRefusal(
            f"production manifest already exists: {manifest_out} — the "
            "bootstrap CREATES it once; an existing manifest means a "
            "previous bootstrap or a quarterly rotation owns "
            "production, and re-installing the bootstrap trio would "
            "silently revert it. Refusing.")
    if status_exists:
        raise CutoverRefusal(
            f"certification status artifact already exists: "
            f"{status_path} — the initial WIN is written ONCE by this "
            "bootstrap; a later state belongs to the annual "
            "re-certification flow. Refusing.")
    if baseline_exists:
        raise CutoverRefusal(
            f"baseline record already exists: {baseline} — either a "
            "previous bootstrap wrote it or an aborted run's rollback "
            "could not remove it (codex #392 r14); truncating it would "
            "destroy a canonical record this run does not own, and a "
            "later failure would roll-back-DELETE it. Refusing.")


def check_write_targets(targets: dict[str, str]) -> None:
    """Every artifact this cutover writes must have its OWN path
    (codex #392 r4).

    ``--manifest-out`` accidentally pointed at the status artifact (or
    the baseline, or a member's inference meta) would install the
    manifest there and then let the later write overwrite it — exiting
    0 with a valid-looking status file where the serving manifest was
    supposed to be, and no manifest for the morning run. The caller
    passes RESOLVED paths so ``./x`` and ``x`` collide."""
    seen: dict[str, str] = {}
    for label, path in targets.items():
        if path in seen:
            raise CutoverRefusal(
                f"write-target collision: {label} and {seen[path]} "
                f"both resolve to {path} — one write would silently "
                "overwrite the other, refusing")
        seen[path] = label


def check_campaign_eligibility(sidecar_text: str) -> dict[str, Any]:
    """Gate 1: the committed verdict sidecar must be a well-formed
    ``csi800_cadence_verdict_v1`` granting ``promotion_eligible``.

    ``--verify`` (byte/anchor re-validation) runs in the executor
    against the real repo; this is the CONTENT half — a sidecar that
    verifies but does not grant eligibility must not promote."""
    try:
        payload = json.loads(sidecar_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CutoverRefusal(
            f"verdict sidecar is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CutoverRefusal("verdict sidecar top level is not an object")
    if payload.get("schema_version") != _VERDICT_SCHEMA_VERSION:
        raise CutoverRefusal(
            f"verdict sidecar schema "
            f"{payload.get('schema_version')!r} != "
            f"{_VERDICT_SCHEMA_VERSION!r}")
    verdict = payload.get("verdict")
    if not isinstance(verdict, dict):
        raise CutoverRefusal("verdict sidecar carries no verdict block")
    if verdict.get("promotion_eligible") is not True:
        raise CutoverRefusal(
            f"verdict sidecar promotion_eligible is "
            f"{verdict.get('promotion_eligible')!r}, not true — the "
            "campaign did not grant promotion eligibility")
    net = verdict.get("conservative_net_annualized")
    if not _finite(net):
        raise CutoverRefusal(
            f"verdict sidecar conservative_net_annualized {net!r} is "
            "not a finite number")
    anchors = payload.get("anchors")
    if not isinstance(anchors, dict):
        raise CutoverRefusal("verdict sidecar carries no anchors block")
    for key in ("pair_anchor", "evidence_anchor", "n1_anchor"):
        value = anchors.get(key)
        if (not isinstance(value, str) or len(value) != 40
                or any(c not in "0123456789abcdef" for c in value.lower())):
            raise CutoverRefusal(
                f"verdict sidecar anchors.{key} {value!r} is not a "
                "40-hex commit id")
    return payload


def check_preregistered_windows(
    member_windows: list[tuple[str, str]],
    preset_windows: list[tuple[str, str]],
) -> None:
    """The installed trio must be the PRE-REGISTERED one (codex #392
    r6).

    Every other gate checks internal consistency — the manifest binds
    its own gate artifacts, the serving pins accept any legal
    quarterly stagger. None of that distinguishes the trio whose
    windows were frozen before ignition from a differently-windowed
    (e.g. one-day-shifted, or simply re-tuned) run that also satisfies
    the broad arithmetic. Pre-registration is the whole point of
    ``跑前钉死``, so the switch compares the manifest's fit windows,
    in order, against the committed bootstrap presets."""
    if len(member_windows) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"expected {BOOTSTRAP_MEMBER_COUNT} manifest members, got "
            f"{len(member_windows)}")
    if len(preset_windows) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"expected {BOOTSTRAP_MEMBER_COUNT} pre-registered preset "
            f"windows, got {len(preset_windows)}")
    for i, (actual, expected) in enumerate(
            zip(member_windows, preset_windows, strict=True)):
        if actual != expected:
            raise CutoverRefusal(
                f"manifest member[{i}] train window "
                f"{actual[0]}..{actual[1]} != the pre-registered "
                f"bootstrap preset {expected[0]}..{expected[1]} — this "
                "is not the trio whose windows were frozen before "
                "ignition, refusing")


def check_preregistered_gate_windows(
    member_gate_windows: list[tuple[Any, Any]],
    preset_valid_windows: list[tuple[str, str]],
    ensemble_gate_window: tuple[Any, Any],
    expected_ensemble_window: tuple[str, str],
) -> None:
    """The gates must have been MEASURED on the pre-registered windows
    (codex #392 r7).

    Binding the train windows alone leaves the measurement windows
    free: a member gate re-run on a different (still span/gap-legal)
    valid window, or a dry run over a different quarter, would still
    authorize the switch. The bootstrap froze those windows too, so
    the switch compares them verbatim."""
    if len(member_gate_windows) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"expected {BOOTSTRAP_MEMBER_COUNT} member gate windows, "
            f"got {len(member_gate_windows)}")
    if len(preset_valid_windows) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"expected {BOOTSTRAP_MEMBER_COUNT} pre-registered valid "
            f"windows, got {len(preset_valid_windows)}")
    for i, (actual, expected) in enumerate(
            zip(member_gate_windows, preset_valid_windows,
                strict=True)):
        if (str(actual[0]), str(actual[1])) != expected:
            raise CutoverRefusal(
                f"member[{i}] gate was measured on "
                f"{actual[0]}..{actual[1]}, not the pre-registered "
                f"valid window {expected[0]}..{expected[1]} — refusing")
    if ((str(ensemble_gate_window[0]), str(ensemble_gate_window[1]))
            != expected_ensemble_window):
        raise CutoverRefusal(
            f"the ensemble dry run was measured on "
            f"{ensemble_gate_window[0]}..{ensemble_gate_window[1]}, "
            f"not the pre-registered trailing quarter "
            f"{expected_ensemble_window[0]}.."
            f"{expected_ensemble_window[1]} — refusing")


# The "same-family configuration" surface (R1-DP-A): everything that
# defines WHAT was trained, beyond the windows. Values come from the
# mainline base config; a member trained with a locally retuned
# hyperparameter is not the pre-registered protocol.
SAME_FAMILY_KEYS = (
    "feature_handler", "model_type", "num_boost_round",
    "early_stopping_rounds", "learning_rate", "max_depth",
    "num_leaves", "lambda_l1", "lambda_l2", "min_data_in_leaf",
    "feature_fraction", "bagging_fraction", "bagging_freq",
    "topk", "n_drop",
    # Training-affecting fields the mainline base config OMITS (codex
    # #392 r10): the pipeline resolves them from PipelineConfig
    # defaults, so "absent from base" must mean "compare against the
    # pinned default", never "skip" — a five-day-label or reseeded
    # member reusing the pinned dates is not the frozen protocol.
    "label_horizon_days", "seed",
    # The DATA configuration (codex #392 r12): a member trained
    # against a different bundle, region or adjustment mode keeps the
    # pinned windows and hyperparameters yet is a different model
    # family. provider_uri is compared AFTER the executor expands the
    # base config's env-var template with the same loader function
    # the pipeline uses.
    "provider_uri", "region", "adjust_mode",
)
# Pinned PipelineConfig defaults for same-family keys the base config
# may legitimately omit (governance cross-pins these against the real
# dataclass).
SAME_FAMILY_DEFAULTS: dict[str, Any] = {
    "label_horizon_days": 1,
    "seed": 42,
    "region": "cn",
    "adjust_mode": "pre_adjusted",
    # provider_uri has NO default on purpose: it must come from the
    # mainline base config (expanded by the executor) — a base config
    # without it is unadjudicable and refuses.
}


def _expand_registered_default(raw: str, what: str) -> str:
    """Expand a ``${VAR:-default}`` template using ONLY the committed
    default (codex #392 r15). The live environment is mutable process
    state: the same wrong ``QUANT_PROVIDER_URI`` that mis-trained a
    member would also fabricate the expected value at cutover time,
    collapsing the comparison into a tautology. The default committed
    at the pinned revision IS the pre-registered bundle identity; a
    template with no default is unadjudicable — refuse."""
    from src.core._yaml_loader import _ENV_VAR_PATTERN

    def _take_default(match: re.Match[str]) -> str:
        default = match.group("default")
        if default is None:
            raise CutoverRefusal(
                f"{what}: template ${{{match.group('name')}}} declares "
                "no committed default — there is no pre-registered "
                "bundle identity to bind, refusing")
        return default

    return _ENV_VAR_PATTERN.sub(_take_default, raw)


def _canonicalize_provider_uri(config: Any) -> Any:
    """Normalize ``config["provider_uri"]`` in place to the canonical
    runtime spelling (codex #392 r13). Training persisted whatever
    spelling the run was launched with — ``~/bundle`` vs its absolute
    path, a symlink vs its target, Windows drive-letter/separator
    variants. Both sides of the family binding go through the SAME
    normalizer ``init_qlib_canonical`` applies, so equality after
    normalization is exactly "qlib treated them as the same bundle"
    — and inequality is a genuinely different bundle, refused."""
    if isinstance(config, dict) and isinstance(
            config.get("provider_uri"), str):
        from src.core.qlib_runtime import _normalize_provider_uri

        config["provider_uri"] = _normalize_provider_uri(
            config["provider_uri"])
    return config


def _same_family_value(actual: Any, expected: Any) -> bool:
    """Keep boolean identity distinct from ordinary numeric equivalence."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    return bool(actual == expected)


def check_member_training_config(
    label: str, run_config: Any, preset_declared: dict[str, Any],
    base_config: dict[str, Any],
) -> None:
    """Bind a member to the FROZEN configuration, not just its dates
    (codex #392 r8).

    Reducing the pinned presets to date pairs left the semantics free:
    a member trained on the same windows but a different universe, no
    guard trio, or retuned hyperparameters would install as official
    production. Every training run persists its fully resolved config
    beside the model, so the switch compares it against

    * every key the pinned preset DECLARES (universe, benchmark, the
      csi800 guard trio, windows, device) — verbatim; and
    * the same-family keys from the mainline BASE config
      (handler/model/hyperparameters/topk) — so nothing was retuned
      locally.

    A run whose config cannot be read at all refuses: an unbindable
    member cannot become production."""
    if not isinstance(run_config, dict):
        raise CutoverRefusal(
            f"{label}: training run config is unreadable — an "
            "unbindable member cannot become production, refusing")
    for key, expected in preset_declared.items():
        if key == "extends":
            continue
        actual = run_config.get(key, _MISSING)
        if not _same_family_value(actual, expected):
            raise CutoverRefusal(
                f"{label}: training config {key}={actual!r} != the "
                f"pre-registered preset's {expected!r} — this member "
                "was not trained under the frozen configuration, "
                "refusing")
    for key in SAME_FAMILY_KEYS:
        if key in base_config:
            expected = base_config[key]
        elif key in SAME_FAMILY_DEFAULTS:
            expected = SAME_FAMILY_DEFAULTS[key]
        else:
            # A frozen-family key with NEITHER a base value nor a
            # pinned default cannot be adjudicated — skipping it is
            # the hole codex #392 r10 found, so refuse instead.
            raise CutoverRefusal(
                f"{label}: same-family key {key!r} is absent from the "
                "mainline base config and has no pinned default — "
                "cannot adjudicate the frozen semantics, refusing")
        actual = run_config.get(key, _MISSING)
        if not _same_family_value(actual, expected):
            raise CutoverRefusal(
                f"{label}: training config {key}={actual!r} != the "
                f"frozen protocol's {expected!r} — retuned "
                "same-family semantics are not the pre-registered "
                "protocol, refusing")


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<absent>"


_MISSING = _Missing()


def check_run_config_provenance(
    label: str, *, run_config_sha256: str, sidecar: Any,
) -> None:
    """Keep the cutover error boundary over the shared digest validator."""
    from src.data.model_training_provenance import (
        ModelTrainingProvenanceError,
    )
    from src.data.model_training_provenance import (
        check_run_config_provenance as check_digest,
    )

    try:
        check_digest(label, run_config_sha256=run_config_sha256, sidecar=sidecar)
    except ModelTrainingProvenanceError as exc:
        raise CutoverRefusal(str(exc)) from exc


def check_member_source_provenance(label: str, sidecar: Any) -> str:
    """The member's training CODE must be registered, clean source
    (codex #392 r15): a config that matches every pre-registered
    value proves nothing about a locally modified feature builder or
    trainer. The run stamps ``source_git_commit``/``source_git_dirty``
    into the sidecar verbatim from its run-start capture; here the
    tree must be EXPLICITLY clean and the commit well-formed — the
    executor then requires the commit to be mainline ancestry.
    Returns the commit for that ancestry check."""
    if not isinstance(sidecar, dict):
        raise CutoverRefusal(
            f"{label}: trainer sidecar is not an object — cannot bind "
            "the training source, refusing")
    dirty = sidecar.get("source_git_dirty")
    if dirty is not False:
        raise CutoverRefusal(
            f"{label}: source_git_dirty={dirty!r} — the member must "
            "be trained from an explicitly CLEAN tree; a dirty or "
            "unknown checkout is unregistered implementation "
            "semantics, refusing")
    commit = sidecar.get("source_git_commit")
    if (not isinstance(commit, str) or len(commit) != 40
            or any(c not in "0123456789abcdef" for c in commit)):
        raise CutoverRefusal(
            f"{label}: source_git_commit={commit!r} is not a full "
            "40-hex commit — cannot adjudicate the training source, "
            "refusing")
    return commit


def check_evidence_provenance(aggregate: Any) -> None:
    """The anchored run's provenance must be EXPLICITLY clean (codex
    #392 r5).

    Refusing only a literal ``true`` would let a report with the field
    missing, ``null``, or a non-boolean truthy value authorize
    production on unbound provenance. The repo's other provenance
    consumers fail closed on anything that is not ``False``; so does
    this promotion gate."""
    if not isinstance(aggregate, dict):
        raise CutoverRefusal(
            "iso_week aggregate report is not an object")
    dirty = aggregate.get("git_dirty")
    if dirty is not False:
        raise CutoverRefusal(
            f"anchored iso_week run declares git_dirty={dirty!r} — "
            "promotion requires an EXPLICITLY clean-tree provenance "
            "(False); anything else leaves the evidence unbound, "
            "refusing")


def check_isoweek_anchor(
    aggregate: Any, *, expected_config_sha256: str,
    actual_config_sha256: str,
) -> dict[str, Any]:
    """Gate 2: the ANCHORED iso_week re-check evidence.

    Two independent bindings (spec: 晋升门第 2 条):

    * the run's embedded config must be the committed iso_week
      re-check preset (content hash equality — the caller supplies
      both digests so this stays pure);
    * the full-window net excess must be RE-DERIVED positive from the
      anchored aggregate, never taken from an operator assertion.
    """
    if actual_config_sha256 != expected_config_sha256:
        raise CutoverRefusal(
            f"iso_week re-check run's embedded config sha256 "
            f"{actual_config_sha256} != the committed preset "
            f"{expected_config_sha256} — the anchored evidence does "
            "not bind the certified serving semantics, refusing")
    if not isinstance(aggregate, dict):
        raise CutoverRefusal(
            "iso_week aggregate report is not an object")
    metrics = aggregate.get("aggregate_metrics")
    if not isinstance(metrics, dict):
        raise CutoverRefusal(
            "iso_week aggregate carries no aggregate_metrics block")
    raw_net = metrics.get("mean_annualized_return")
    if not _finite(raw_net):
        raise CutoverRefusal(
            f"iso_week aggregate mean_annualized_return {raw_net!r} is "
            "not a finite number — corrupted anchor evidence")
    serialized = float(raw_net)  # type: ignore[arg-type]  # _finite narrows

    # RE-DERIVE the promotion net from the fold rows (codex #392 r1):
    # this number is the net authority for the switch, so a torn or
    # hand-edited report whose summary stayed positive while its folds
    # went missing / duplicated / negative must not promote. On the
    # committed evidence the fold mean reproduces the serialized value
    # exactly.
    folds = aggregate.get("folds")
    num_folds = aggregate.get("num_folds")
    if not isinstance(folds, list) or not folds:
        raise CutoverRefusal(
            "iso_week aggregate declares no folds — cannot re-derive "
            "the promotion net, refusing")
    if not isinstance(num_folds, int) or num_folds != len(folds):
        raise CutoverRefusal(
            f"iso_week aggregate declares num_folds={num_folds!r} but "
            f"carries {len(folds)} fold rows — torn evidence, refusing")
    valid_folds = metrics.get("valid_folds_annualized_return")
    if valid_folds != num_folds:
        raise CutoverRefusal(
            f"iso_week aggregate scored only {valid_folds!r} of "
            f"{num_folds} folds for annualized return — a partial "
            "aggregate cannot authorize promotion, refusing")
    seen: set[int] = set()
    values: list[float] = []
    for i, row in enumerate(folds):
        if not isinstance(row, dict):
            raise CutoverRefusal(
                f"iso_week fold row[{i}] is not an object — refusing")
        idx = row.get("fold_index")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx in seen:
            raise CutoverRefusal(
                f"iso_week fold row[{i}] fold_index {idx!r} is missing "
                "or duplicated — torn evidence, refusing")
        seen.add(idx)
        value = row.get("annualized_return")
        if not _finite(value):
            raise CutoverRefusal(
                f"iso_week fold {idx} annualized_return {value!r} is "
                "not a finite number — corrupted anchor evidence")
        values.append(float(value))  # type: ignore[arg-type]
    if seen != set(range(num_folds)):
        raise CutoverRefusal(
            f"iso_week fold indexes are not 0..{num_folds - 1} — torn "
            "evidence, refusing")
    rederived = math.fsum(values) / len(values)
    if not math.isclose(rederived, serialized, rel_tol=1e-9,
                        abs_tol=1e-12):
        raise CutoverRefusal(
            f"iso_week aggregate mean_annualized_return "
            f"{serialized!r} disagrees with the fold rows' mean "
            f"{rederived!r} — the summary was not produced by these "
            "folds, refusing")
    if rederived <= 0.0:
        raise CutoverRefusal(
            f"iso_week re-check net excess {rederived:.4%} <= 0 — the "
            "production anchor (iso-week) does not reproduce the "
            "certified winner's edge, refusing to switch")
    return {"net_annualized": rederived,
            "net_annualized_serialized": serialized,
            "num_folds": num_folds}


def build_initial_status(
    *, verdict_sidecar_path: str, verdict_sidecar_sha256: str,
    evidence_anchor_commit: str, note: str,
) -> dict[str, Any]:
    """The FIRST write of the single monotonic certification-state
    artifact (R1-DP-D / codex #389 r7). Its absence is why the
    quarterly executor would freeze the first rotation; its presence
    starts the 15-month validity window — which is exactly why it is
    written HERE, at the cutover, and nowhere earlier."""
    if (not isinstance(verdict_sidecar_sha256, str)
            or len(verdict_sidecar_sha256) != 64):
        raise CutoverRefusal(
            "initial status needs the verdict sidecar's 64-hex content "
            "hash")
    if (not isinstance(evidence_anchor_commit, str)
            or len(evidence_anchor_commit) != 40):
        raise CutoverRefusal(
            "initial status needs a 40-hex evidence anchor commit")
    if not isinstance(note, str) or not note.strip():
        raise CutoverRefusal("initial status needs an adjudication note")
    return {
        "schema_version": RECERT_STATUS_SCHEMA_VERSION,
        "verdict": "WIN",
        "verdict_sidecar_path": verdict_sidecar_path,
        "verdict_sidecar_sha256": verdict_sidecar_sha256,
        "evidence_anchor_commit": evidence_anchor_commit,
        "note": note.strip(),
    }


def build_baseline_record(
    *, manifest_path: str, manifest_sha256: str,
    manifest_mode: str = "",
    members: list[dict[str, Any]], incumbent_backup: dict[str, str],
    campaign: dict[str, Any], isoweek: dict[str, Any],
    gate_artifacts: dict[str, dict[str, str]], generated_at: str,
) -> dict[str, Any]:
    """The committed rollback/baseline record (DP-4, ④ precedent):
    what production was BEFORE, what it became, and every piece of
    evidence that authorized the change."""
    if len(members) != BOOTSTRAP_MEMBER_COUNT:
        raise CutoverRefusal(
            f"baseline needs exactly {BOOTSTRAP_MEMBER_COUNT} members")
    # Every gate record binds CONTENT, not just a pathname (codex
    # #392 r13): the gate files live on the operator's disk and are
    # mutable/replaceable after the cutover — the committed baseline
    # must be able to establish which exact bytes authorized
    # production.
    for name, entry in gate_artifacts.items():
        path = (str(entry.get("path", "")).strip()
                if isinstance(entry, dict) else "")
        sha = entry.get("sha256") if isinstance(entry, dict) else None
        if (not path or not isinstance(sha, str) or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)):
            raise CutoverRefusal(
                f"gate artifact record '{name}' must carry a path AND "
                "the 64-hex sha256 of the adjudicated bytes — a bare "
                "pathname cannot establish what authorized production")
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "serving": {
            "mode": "ensemble_manifest",
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
            # The permission bits the manifest was installed with —
            # mirrored from the incumbent so the serving account keeps
            # its read access (codex #392 r2).
            "manifest_mode": manifest_mode,
            "members": members,
        },
        "incumbent_backup": dict(incumbent_backup),
        "authorized_by": {
            "campaign": dict(campaign),
            "isoweek_recheck": dict(isoweek),
            "gate_artifacts": {k: dict(v)
                               for k, v in gate_artifacts.items()},
        },
    }


def build_inference_meta(
    *, model_path: str, fit_start: str, fit_end: str,
    model_type: str, promoted_at: str,
) -> dict[str, Any]:
    """Per-member inference meta (``<model>.meta.json``, ④ precedent).

    Serving derives its normalization window from these values, so the
    fit window written here is the member's TRAINING window verbatim —
    the same pair the manifest declares and the member gate bound."""
    for name, value in (("fit_start", fit_start), ("fit_end", fit_end),
                        ("model_type", model_type),
                        ("promoted_at", promoted_at)):
        if not isinstance(value, str) or not value.strip():
            raise CutoverRefusal(
                f"inference meta needs a non-empty {name}")
    return {
        "model_path": model_path,
        "model_type": model_type,
        "fit_start_for_inference": fit_start,
        "fit_end_for_inference": fit_end,
        "train_window": f"{fit_start}..{fit_end}",
        "promoted_at": promoted_at,
    }

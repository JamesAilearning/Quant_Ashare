"""Canonical-backtest provenance projected into comparison-safe report evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

_COMPARISON_PROVENANCE_CONFIG_FIELDS = (
    "benchmark_code",
    "signal_to_execution_lag",
    "account_config",
    "st_mask",
    "adjust_mode",
    "exchange_config",
    "runtime",
)
_ST_MASK_INPUT_FIELDS = ("namechange_path", "namechange_sha256")
_ST_MASK_PROVENANCE_FIELDS = frozenset((*_ST_MASK_INPUT_FIELDS, "n_st_masked"))


def _comparison_st_mask_identity(value: Any) -> dict[str, Any] | None:
    """Project stable ST inputs while excluding a fold-specific outcome count.

    ``BacktestRunner`` records ``n_st_masked`` beside the path and digest for
    auditability. That number describes the fold's prediction dates, not the
    ST input artifact, so it cannot determine whether folds share comparison
    semantics. Unknown future fields fail closed rather than being silently
    treated as either an input or an outcome.
    """
    if not isinstance(value, Mapping) or not set(value).issubset(
        _ST_MASK_PROVENANCE_FIELDS
    ):
        return None
    identity = {
        field: value[field]
        for field in _ST_MASK_INPUT_FIELDS
        if field in value
    }
    return identity or None


def _comparison_provenance_candidate(
    backtest_provenance: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract stable comparison evidence from one canonical backtest output.

    A canonical request legitimately changes prediction references and
    evaluation windows for walk-forward folds.  This projection deliberately
    selects only the producer-recorded fields that establish comparison
    semantics; it never reconstructs an absent value from engine config.
    """
    if backtest_provenance is None:
        return None
    config = backtest_provenance.get("config")
    if not isinstance(config, Mapping) or any(
        field not in config for field in _COMPARISON_PROVENANCE_CONFIG_FIELDS
    ):
        return None
    st_mask_identity = _comparison_st_mask_identity(config.get("st_mask"))
    if st_mask_identity is None:
        return None
    if any(
        field not in backtest_provenance
        for field in (
            "execution_timing_semantics",
            "price_limit_semantics",
            "official_backtest_path",
        )
    ):
        return None
    return {
        "execution_timing_semantics": backtest_provenance[
            "execution_timing_semantics"
        ],
        "price_limit_semantics": backtest_provenance["price_limit_semantics"],
        # This producer-written value is deliberately resolved across every
        # fold too. An aggregate metric has no single top-level fold path;
        # accepting it without matching canonical-path evidence would let an
        # official-looking walk-forward rank bypass the canonical runtime.
        "official_backtest_path": backtest_provenance["official_backtest_path"],
        "config": {
            field: st_mask_identity if field == "st_mask" else config[field]
            for field in _COMPARISON_PROVENANCE_CONFIG_FIELDS
        },
    }


def resolve_backtest_comparison_provenance(
    backtest_provenances: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Publish only evidence identical across every supplied backtest output.

    A pipeline has one canonical backtest output; a walk-forward report has
    one per fold.  ``mixed`` means no arbitrary fold is selected, and
    ``unavailable`` means missing or malformed producer evidence.  Both are
    deliberate comparison blocks.
    """
    candidates = [
        _comparison_provenance_candidate(backtest_provenance)
        for backtest_provenance in backtest_provenances
    ]
    if not candidates or any(candidate is None for candidate in candidates):
        return {"status": "unavailable"}

    try:
        canonical_candidates = {
            json.dumps(candidate, sort_keys=True, separators=(",", ":"))
            for candidate in candidates
        }
    except (TypeError, ValueError):
        # The report is evidence.  An unrepresentable value is unavailable
        # evidence, never a reason to fill a value from current defaults.
        return {"status": "unavailable"}
    if len(canonical_candidates) != 1:
        return {"status": "mixed"}
    return {
        "status": "consistent",
        **json.loads(next(iter(canonical_candidates))),
    }

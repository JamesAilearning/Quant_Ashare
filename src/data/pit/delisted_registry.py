"""Build the delisted-stock registry from Tushare raw dumps.

Pipeline
--------
::

    <tushare_dir>/delisted_stocks.parquet  +  tests/pit/reference_cases.yaml
       -> delisted_registry.parquet
       -> validation: every pure_delisting / batch_delisting reference row
          is present with matching delist_date; no active_control row is
          present.

Schema (per docs/pit/pit_universe_design.md §4.1)
-------------------------------------------------

================  =======  ================================================
column            type     notes
================  =======  ================================================
ticker            string   market code, e.g. SH600087
list_date         date     first trading day
delist_date       date     last trading day (NEVER NULL in this registry)
last_company_name string   Tushare's ``name`` at delisting (often suffixed
                           with ``(退)``)
delist_reason     string   one of: financial, major_violation, voluntary,
                           par_value, restructure_failure, other
================  =======  ================================================

``delist_reason`` classification + manual overrides
---------------------------------------------------
Tushare's ``stock_basic`` does NOT include a structured ``delist_reason``
field. To avoid agent fabrication (see project memory file
``feedback_agent_curated_data.md``), this builder takes the following
honest approach, in precedence order:

1. **Manual overrides** (``data/manual_delistings.yaml``, optional).
   When a path is supplied via ``manual_overrides_path`` and the YAML
   lists an entry for a ticker, its fields take precedence over Tushare
   and the reference cases. This is the design §13 q2 mechanism for
   correcting Tushare drift (wrong ``delist_date``) or reclassifying
   ``delist_reason`` once an exchange announcement has been read.
   Each override row MUST carry a ``cite_url`` field naming the public
   source (announcement URL, Wind / choice export, etc.) so a reviewer
   can spot-check before merge.
2. **Reference cases** — tickers in
   ``reference_cases.yaml::pure_delisting_cases`` (or inside
   ``batch_delisting_cases``) carry user-verified ``delist_reason``.
3. **Default ``"other"``** for every other ticker. We do NOT infer
   reasons from the company name pattern (e.g. ``*ST → 退``) because
   the heuristic is unreliable and would mask real classification
   gaps from downstream consumers.

Manual override YAML schema::

    overrides:
      - ticker: SH600087
        delist_reason: major_violation
        # delist_date is optional — supply only when Tushare is wrong
        delist_date: 2014-06-05
        cite_url: https://www.sse.com.cn/...   # REQUIRED
        note: "SSE notice cites major violation; Tushare returns financial"

Out of scope for Phase A.2
--------------------------
- No name-pattern heuristics for ``delist_reason`` inference.
- No borrow-shell detection (the design's borrow-shell tickers are
  active, not delisted, so they never appear here — handled in Phase
  B.2 bin-builder continuity assertions).
- No NaN-after-delist bin writing (Phase B.2).
- No qlib provider creation (Phase B.1 / B.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core.logger import get_logger

_logger = get_logger(__name__)


VALID_REASONS: tuple[str, ...] = (
    "financial",
    "major_violation",
    "voluntary",
    "par_value",
    "restructure_failure",
    "other",
)

REGISTRY_COLUMNS: tuple[str, ...] = (
    "ticker",
    "list_date",
    "delist_date",
    "last_company_name",
    "delist_reason",
)


# Consolidated into ``src.data.pit._common`` (bug.md P2-4) — the
# original 4-way copy/paste left a TODO to dedupe; this re-export
# preserves the leading-underscore name so internal call sites
# don't need touching.
from src.data.pit._common import to_qlib_ticker as _to_qlib_ticker  # noqa: E402


class DelistedRegistryError(RuntimeError):
    """Raised when registry construction or validation fails. Distinct
    from generic IOError / KeyError so callers can react to "registry
    invalid" vs "filesystem broken" separately."""


@dataclass(frozen=True)
class DelistedRegistryBuildResult:
    """Returned by :meth:`DelistedRegistryBuilder.build`."""

    output_path: Path
    row_count: int
    reference_rows_matched: int
    active_controls_checked: int


class DelistedRegistryBuilder:
    """Build ``delisted_registry.parquet`` from Tushare + reference YAML.

    Construction is cheap; ``build()`` does the work. ``build()`` is
    idempotent — writing the same input twice produces a byte-identical
    output (parquet timestamps notwithstanding).
    """

    def __init__(
        self,
        tushare_dir: Path,
        reference_cases_path: Path,
        output_path: Path,
        manual_overrides_path: Path | None = None,
    ) -> None:
        self._tushare_dir = tushare_dir
        self._reference_cases_path = reference_cases_path
        self._output_path = output_path
        self._manual_overrides_path = manual_overrides_path

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------

    def build(self) -> DelistedRegistryBuildResult:
        delisted_raw = self._load_delisted_stocks()
        active_raw = self._load_active_stocks()
        references = self._load_reference_cases()
        overrides = self._load_manual_overrides()

        registry = self._build_registry_df(delisted_raw, references, overrides)

        ref_matched = self._validate_reference_delistings(registry, references)
        controls_checked = self._validate_active_controls(
            registry, active_raw, references,
        )
        self._validate_invariants(registry)

        self._atomic_write_parquet(registry, self._output_path)
        _logger.info(
            "Wrote %d delisted rows to %s (reference rows matched: %d, "
            "active controls checked: %d)",
            len(registry), self._output_path, ref_matched, controls_checked,
        )
        return DelistedRegistryBuildResult(
            output_path=self._output_path,
            row_count=len(registry),
            reference_rows_matched=ref_matched,
            active_controls_checked=controls_checked,
        )

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _load_delisted_stocks(self) -> pd.DataFrame:
        path = self._tushare_dir / "delisted_stocks.parquet"
        if not path.exists():
            raise DelistedRegistryError(
                f"Missing {path}; run Phase A.1 (01_fetch_tushare.py) "
                "with --endpoints stock_basic first."
            )
        df = pd.read_parquet(path)
        required = {"ts_code", "name", "list_date", "delist_date"}
        missing = required - set(df.columns)
        if missing:
            raise DelistedRegistryError(
                f"{path} missing required columns: {sorted(missing)}"
            )
        if df.empty:
            raise DelistedRegistryError(
                f"{path} is empty — Tushare returned no delisted stocks. "
                "Verify the pull was successful (expected ~325 rows as of 2026)."
            )
        return df

    def _load_active_stocks(self) -> pd.DataFrame:
        path = self._tushare_dir / "active_stocks.parquet"
        if not path.exists():
            raise DelistedRegistryError(
                f"Missing {path}; needed to verify active-control reference "
                "rows are not in the delisted bucket. Run Phase A.1 with "
                "--endpoints stock_basic first."
            )
        df = pd.read_parquet(path)
        # Guard the schema explicitly — without this, a Tushare schema drift
        # (or a corrupted snapshot) would surface as a raw KeyError from the
        # active-control validator rather than the intended controlled
        # DelistedRegistryError path. Codex review on PR #100.
        if "ts_code" not in df.columns:
            raise DelistedRegistryError(
                f"{path} missing required column 'ts_code' "
                f"(found columns: {sorted(df.columns)})"
            )
        return df

    def _load_reference_cases(self) -> dict[str, Any]:
        path = self._reference_cases_path
        if not path.exists():
            raise DelistedRegistryError(
                f"Reference cases file not found: {path}. Phase 0.2 seed "
                "is required before building the registry."
            )
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise DelistedRegistryError(
                f"{path}: expected a YAML mapping at top level, got {type(data).__name__}"
            )
        return data

    def _load_manual_overrides(self) -> list[dict[str, Any]]:
        """Read ``data/manual_delistings.yaml`` if a path was supplied.

        The file is optional: when no path is configured OR the file
        does not exist, we return an empty list (no overrides applied,
        registry falls back to reference + Tushare values).

        Schema validation happens here so per-row errors surface BEFORE
        any registry mutation. Each entry MUST carry ``ticker`` and
        ``cite_url``; ``delist_reason`` (if present) MUST be in
        :data:`VALID_REASONS`; ``delist_date`` (if present) MUST be
        parseable by ``pd.Timestamp``.
        """
        path = self._manual_overrides_path
        if path is None:
            return []
        if not path.exists():
            _logger.info(
                "Manual overrides path %s does not exist; "
                "registry built without overrides.", path,
            )
            return []
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise DelistedRegistryError(
                f"{path}: expected a YAML mapping at top level, got "
                f"{type(data).__name__}"
            )
        # Validate the raw value BEFORE any coercion. The previous
        # `data.get("overrides") or []` silently swallowed falsy non-list
        # types (``overrides: {}``, ``overrides: ""``) — an operator typo
        # would coerce to empty list and the build proceeded without
        # applying the intended overrides. Codex P1 on PR #107.
        raw_entries = data.get("overrides")
        if raw_entries is None:
            raw_entries = []
        elif not isinstance(raw_entries, list):
            raise DelistedRegistryError(
                f"{path}: 'overrides' must be a YAML list (or null / missing "
                f"for no overrides), got {type(raw_entries).__name__}"
            )

        validated: list[dict[str, Any]] = []
        seen_tickers: set[str] = set()
        for i, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                raise DelistedRegistryError(
                    f"{path}: overrides[{i}] is not a mapping"
                )
            ticker = entry.get("ticker")
            if not ticker or not isinstance(ticker, str):
                raise DelistedRegistryError(
                    f"{path}: overrides[{i}] missing required 'ticker' field"
                )
            if ticker in seen_tickers:
                raise DelistedRegistryError(
                    f"{path}: overrides[{i}] has duplicate ticker {ticker!r}"
                )
            seen_tickers.add(ticker)
            if not entry.get("cite_url"):
                raise DelistedRegistryError(
                    f"{path}: overrides[{i}] for {ticker!r} missing required "
                    "'cite_url' field. Each override MUST cite the public "
                    "announcement / source (design §13 q2)."
                )
            if "delist_reason" in entry:
                reason = entry["delist_reason"]
                if reason not in VALID_REASONS:
                    raise DelistedRegistryError(
                        f"{path}: overrides[{i}] for {ticker!r} has invalid "
                        f"delist_reason {reason!r}; valid: {VALID_REASONS}"
                    )
            if "delist_date" in entry:
                try:
                    pd.Timestamp(entry["delist_date"])
                except (ValueError, TypeError) as exc:
                    raise DelistedRegistryError(
                        f"{path}: overrides[{i}] for {ticker!r} has invalid "
                        f"delist_date {entry['delist_date']!r}: {exc}"
                    ) from exc
            validated.append(entry)

        _logger.info(
            "Loaded %d manual override(s) from %s", len(validated), path,
        )
        return validated

    # ------------------------------------------------------------------
    # Registry construction
    # ------------------------------------------------------------------

    def _build_registry_df(
        self,
        delisted_raw: pd.DataFrame,
        references: dict[str, Any],
        overrides: list[dict[str, Any]] | None = None,
    ) -> pd.DataFrame:
        """Project Tushare delisted_stocks → registry schema.

        Resolution order for ``delist_reason`` (and optional
        ``delist_date`` override):

            1. ``data/manual_delistings.yaml`` (per-ticker, design §13 q2)
            2. ``reference_cases.yaml`` pure / batch delisting rows
            3. Default ``"other"`` for ``delist_reason``; Tushare value
               for ``delist_date``.
        """
        reason_map = self._collect_reasons_from_references(references)

        out = pd.DataFrame({
            "ticker": delisted_raw["ts_code"].astype(str).map(_to_qlib_ticker),
            "list_date": pd.to_datetime(
                delisted_raw["list_date"], format="%Y%m%d", errors="coerce",
            ),
            "delist_date": pd.to_datetime(
                delisted_raw["delist_date"], format="%Y%m%d", errors="coerce",
            ),
            "last_company_name": delisted_raw["name"].astype(str),
        })
        out["delist_reason"] = out["ticker"].map(reason_map).fillna("other")

        # Apply manual overrides AFTER reference cases so an exchange-
        # announcement-cited override wins over an automated Tushare
        # classification, but BEFORE invariant validation so the
        # post-override registry is what gets schema-checked.
        if overrides:
            registry_tickers = set(out["ticker"])
            for entry in overrides:
                ticker = entry["ticker"]
                if ticker not in registry_tickers:
                    raise DelistedRegistryError(
                        f"manual override for ticker {ticker!r} but the "
                        "ticker is not in the Tushare delisted bucket. "
                        "Either the override is stale or Tushare drift "
                        "dropped the row — investigate before merging."
                    )
                row_mask = out["ticker"] == ticker
                if "delist_reason" in entry:
                    out.loc[row_mask, "delist_reason"] = entry["delist_reason"]
                if "delist_date" in entry:
                    out.loc[row_mask, "delist_date"] = pd.Timestamp(
                        entry["delist_date"]
                    )

        # Surface any row Tushare returned without a parseable delist_date
        # — we cannot silently default these to "other" because they will
        # break Phase B.2 bin writes which gate NaN-after-delist on the date.
        missing_delist = out[out["delist_date"].isna()]
        if not missing_delist.empty:
            raise DelistedRegistryError(
                f"{len(missing_delist)} delisted rows have unparseable "
                f"delist_date (sample: {missing_delist['ticker'].head(3).tolist()}). "
                "Tushare returned a malformed value; investigate before proceeding."
            )
        # list_date NaT slips past `delist_date < list_date` (NaT comparisons
        # are False), so unparseable list_date rows would silently land in
        # the registry and corrupt downstream date logic. Codex review on
        # PR #100.
        missing_list = out[out["list_date"].isna()]
        if not missing_list.empty:
            raise DelistedRegistryError(
                f"{len(missing_list)} delisted rows have unparseable "
                f"list_date (sample: {missing_list['ticker'].head(3).tolist()}). "
                "Tushare returned a malformed value; investigate before proceeding."
            )

        # Order columns canonically, sort for determinism
        out = out[list(REGISTRY_COLUMNS)].sort_values("ticker").reset_index(drop=True)
        return out

    @staticmethod
    def _collect_reasons_from_references(references: dict[str, Any]) -> dict[str, str]:
        """Flatten reference cases into ``{ticker: reason}``.

        Both ``pure_delisting_cases`` (each carries its own reason) and
        ``batch_delisting_cases`` (whole batch shares the same delist event;
        per-ticker reason inherits from the batch — but batch entries in
        the seed do NOT carry a reason field, so we leave them as
        ``"other"`` unless the user later annotates).
        """
        reasons: dict[str, str] = {}
        for case in references.get("pure_delisting_cases") or []:
            ticker = case.get("ticker")
            reason = case.get("delist_reason", "other")
            if reason not in VALID_REASONS:
                raise DelistedRegistryError(
                    f"reference_cases.yaml: pure_delisting_cases ticker "
                    f"{ticker!r} has invalid delist_reason {reason!r}; "
                    f"valid: {VALID_REASONS}"
                )
            if ticker:
                reasons[ticker] = reason
        return reasons

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    def _validate_reference_delistings(
        self, registry: pd.DataFrame, references: dict[str, Any],
    ) -> int:
        """Every pure_delisting + batch_delisting reference row MUST be
        present with matching delist_date. Mismatches raise so the
        operator notices Tushare drift early.
        """
        registry_lookup: dict[str, pd.Timestamp] = dict(
            zip(registry["ticker"], registry["delist_date"], strict=False)
        )
        errors: list[str] = []
        matched = 0

        for case in references.get("pure_delisting_cases") or []:
            ticker = case.get("ticker")
            expected = pd.Timestamp(case.get("delist_date"))
            actual = registry_lookup.get(ticker)
            if actual is None:
                errors.append(f"  pure_delisting ticker {ticker!r} missing from registry")
            elif actual != expected:
                errors.append(
                    f"  pure_delisting ticker {ticker!r} delist_date mismatch: "
                    f"reference={expected.date()}, registry={actual.date()}"
                )
            else:
                matched += 1

        for batch in references.get("batch_delisting_cases") or []:
            batch_date = pd.Timestamp(batch.get("batch_date"))
            for entry in batch.get("tickers") or []:
                ticker = entry.get("ticker")
                actual = registry_lookup.get(ticker)
                if actual is None:
                    errors.append(
                        f"  batch ticker {ticker!r} (batch {batch_date.date()}) "
                        "missing from registry"
                    )
                elif actual != batch_date:
                    errors.append(
                        f"  batch ticker {ticker!r} delist_date mismatch: "
                        f"reference={batch_date.date()}, registry={actual.date()}"
                    )
                else:
                    matched += 1

        if errors:
            raise DelistedRegistryError(
                "Reference cases failed validation against built registry:\n"
                + "\n".join(errors)
            )
        return matched

    def _validate_active_controls(
        self, registry: pd.DataFrame, active: pd.DataFrame, references: dict[str, Any],
    ) -> int:
        """Every active_control reference row MUST be in the active bucket
        AND NOT in the delisted registry. False-positive delistings
        (e.g. agent-fabricated KNOWN_DELISTED rows) get caught here.
        """
        registry_tickers = set(registry["ticker"])
        active_tickers = set(active["ts_code"].astype(str).map(_to_qlib_ticker))
        errors: list[str] = []
        checked = 0

        for case in references.get("active_control_cases") or []:
            ticker = case.get("ticker")
            if not ticker:
                continue
            if ticker in registry_tickers:
                errors.append(
                    f"  active control {ticker!r} ({case.get('name')!r}) "
                    "appears in delisted registry — false positive!"
                )
            elif ticker not in active_tickers:
                errors.append(
                    f"  active control {ticker!r} ({case.get('name')!r}) "
                    "not in active_stocks bucket either — registry source is stale"
                )
            else:
                checked += 1

        if errors:
            raise DelistedRegistryError(
                "Active control validation failed:\n" + "\n".join(errors)
            )
        return checked

    def _validate_invariants(self, registry: pd.DataFrame) -> None:
        """Schema-level invariants from design §4.1."""
        # ticker uniqueness
        dup = registry[registry.duplicated("ticker", keep=False)]
        if not dup.empty:
            raise DelistedRegistryError(
                f"Duplicate tickers in registry: {dup['ticker'].tolist()}"
            )
        # delist_date >= list_date
        bad = registry[registry["delist_date"] < registry["list_date"]]
        if not bad.empty:
            raise DelistedRegistryError(
                f"{len(bad)} rows have delist_date < list_date "
                f"(sample: {bad['ticker'].head(3).tolist()})"
            )
        # delist_date never NULL (already enforced in _build_registry_df,
        # but assert again here so future refactors don't drop the check)
        nulls = registry[registry["delist_date"].isna()]
        if not nulls.empty:
            raise DelistedRegistryError(
                f"{len(nulls)} rows have NULL delist_date — schema violation"
            )

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        df.to_parquet(tmp_path, index=False)
        tmp_path.replace(path)

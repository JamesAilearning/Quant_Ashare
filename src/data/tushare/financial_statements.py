"""Versioned raw ingest of financial statements (阶段8 Gate-2, PR-1).

Fetches tushare ``income`` / ``balancesheet`` / ``cashflow`` per instrument and
appends them to a VERSION-PRESERVING raw store. This is research-side data
ingestion; it computes NO factor and is consumed ONLY through the (PR-2)
``FinancialPITDataView`` — never by the canonical runtime.

Store contract (spec ``v2-financial-pit-contract``)
---------------------------------------------------
* Physically **append-only**: every fetch writes its rows tagged with a
  ``_content_hash`` (sha256 over the record's data values) and a
  ``_fetch_batch`` (the ingest run id). Nothing is deduplicated, overwritten,
  or collapsed in place.
* BOTH ``update_flag=0`` (as-originally-reported) and ``update_flag=1``
  (revised) rows for a ``(instrument, end_date)`` are preserved.
* A re-fetch whose content differs from what is stored for the SAME logical key
  ``(instrument, end_date, update_flag)`` is DETECTED (new ``_content_hash``)
  and RECORDED as a new batch — never replaces the prior version in place. An
  identical re-fetch is idempotent (no new row).
* ``(instrument, end_date, update_flag)`` is the LOGICAL current key resolved at
  READ time (latest batch wins); it is NOT a physical uniqueness constraint —
  enforcing physical uniqueness would reject the changed re-fetch the contract
  is required to keep.

The store layout is ``<store_dir>/<endpoint>/<TSCODE>.parquet``.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from src.core.logger import get_logger
from src.data._atomic_io import atomic_write_parquet

_logger = get_logger(__name__)

FINANCIAL_ENDPOINTS: tuple[str, ...] = ("income", "balancesheet", "cashflow")

# PIT columns every financial endpoint returns (Gate-1 confirmed) — always
# fetched; ``update_flag`` distinguishes original(0)/revised(1); the ann dates
# drive the availability contract (PR-1 contract layer).
_PIT_COLS: tuple[str, ...] = ("end_date", "ann_date", "f_ann_date", "update_flag")

# Charter-required data fields per endpoint (Gate-1 §1 坐实). These are the
# columns hashed for change detection and exposed (raw) to the Gate-3 view.
# ``adv_receipts`` AND ``contract_liab`` are BOTH kept raw — the 2020 预收→合同
# 负债 reclassification is documented, the coalesce is Gate-3 factor logic.
DATA_FIELDS: dict[str, tuple[str, ...]] = {
    "income": (
        "revenue", "total_revenue", "oper_cost", "sell_exp", "admin_exp",
        "rd_exp", "int_exp", "fin_exp",
    ),
    "balancesheet": (
        "total_assets", "total_hldr_eqy_inc_min_int",
        "total_hldr_eqy_exc_min_int", "accounts_receiv", "inventories",
        "prepayment", "accounts_pay", "adv_receipts", "contract_liab",
    ),
    "cashflow": ("n_cashflow_act",),
}

# Provenance columns the ingest stamps on every stored row (leading underscore
# so they never collide with a tushare field name).
COL_SOURCE_ENDPOINT = "_source_endpoint"
COL_FETCH_BATCH = "_fetch_batch"
COL_CONTENT_HASH = "_content_hash"
PROVENANCE_COLS: tuple[str, ...] = (
    COL_SOURCE_ENDPOINT, COL_FETCH_BATCH, COL_CONTENT_HASH,
)

# The physical part of the logical current key. The FULL versioned identity is
# (ts_code, end_date, update_flag, EFFECTIVE announcement) — the effective
# announcement token (``f_ann_date``, falling back to ``ann_date`` when
# ``f_ann_date`` is blank; see :func:`effective_announcement_series`) is the
# fourth dimension (spec fix-financial-ingest-ambiguous-duplicates). Keying on
# the EFFECTIVE day — not the raw column pair — means an ``ann_date``-only
# provider correction under a present ``f_ann_date`` is a changed re-fetch of
# the SAME disclosure (latest batch wins), never a phantom second disclosure
# that would tie on the served announcement day (codex #351 r5).
LOGICAL_KEY: tuple[str, ...] = ("ts_code", "end_date", "update_flag")

# The key columns that must be NON-BLANK on every row. The announcement dates
# are deliberately NOT here — a missing announcement date is legitimate (the
# contract layer marks such rows unavailable); blank rows simply share one
# NA effective-announcement value.
NONBLANK_KEY_COLS: tuple[str, ...] = ("ts_code", "end_date", "update_flag")

# Name of the DERIVED effective-announcement key column (in-memory only —
# never written to the store; both layers re-derive it from the raw columns).
EFF_ANN_COL = "_eff_ann_key"


def effective_announcement_series(frame: pd.DataFrame) -> pd.Series:
    """The canonical EFFECTIVE announcement token per row: ``f_ann_date`` when
    non-blank, else ``ann_date`` (mirroring the contract's announcement
    resolution), both via :func:`canon_announcement_token`."""
    f_ann = frame["f_ann_date"].map(canon_announcement_token)
    ann = frame["ann_date"].map(canon_announcement_token)
    return f_ann.where(f_ann.notna(), ann)


class FinancialIngestError(RuntimeError):
    """Raised when the ingest cannot proceed honestly (bad endpoint, malformed
    provider frame, store I/O failure). Fail-loud — never a silent partial."""


def canon_announcement_token(value: Any) -> Any:
    """Canonicalize an announcement-date KEY value (codex #351 r3/r4).

    Blank spellings (None / NaN / 'nan' / '<NA>' / '') -> ``pd.NA``; an exact
    ``.0`` float coercion ('20220331.0') strips to the digits; any other shape
    passes through untouched — the CONTRACT layer fail-louds on malformed
    dates, keying must not pre-judge them. Shared by the ingest (new fetches
    AND the in-memory view of already-stored rows) and by
    ``build_contract_frame`` (read-time), so a legacy store spelling can never
    mint a phantom second disclosure of the same announcement."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NA
    try:
        if bool(pd.isna(value)):
            return pd.NA
    except (TypeError, ValueError):
        pass
    token = str(value).strip()
    if not token or token in _BLANK_TOKENS:
        return pd.NA
    if "." in token:
        int_part, _, frac = token.partition(".")
        if int_part.isdigit() and frac.strip("0") == "":
            return int_part
    return token


_BLANK_TOKENS = frozenset({"", "None", "nan", "NaT", "<NA>"})


class _CallableClient(Protocol):
    def call(self, api_name: str, **params: Any) -> Any: ...


def _fields_arg(endpoint: str) -> str:
    """The tushare ``fields=`` string for an endpoint: ts_code + PIT + data."""
    cols = ("ts_code",) + _PIT_COLS + DATA_FIELDS[endpoint]
    return ",".join(cols)


def content_hash(row: pd.Series, data_fields: Sequence[str]) -> str:
    """sha256 over the record's DATA values (PIT cols + data fields), stable and
    order-independent, so a re-fetch whose numbers changed produces a different
    hash. Provenance columns are excluded (they are metadata about the fetch,
    not the record's content). ``NaN`` normalizes to the literal ``"NA"`` so a
    missing value hashes consistently across fetches.
    """
    parts: list[str] = []
    for col in sorted((*_PIT_COLS, *data_fields)):
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
            token = "NA"
        else:
            token = str(val)
        parts.append(f"{col}={token}")
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class IngestResult:
    endpoint: str
    ts_code: str
    rows_fetched: int
    rows_new: int         # content-new rows appended this batch
    rows_changed: int     # of rows_new, those that are a CHANGED re-fetch
    rows_unchanged: int   # identical content already stored (idempotent skip)


class FinancialStatementIngestor:
    """Version-preserving ingest of one financial endpoint for one instrument.

    Injectable ``client`` (anything with ``.call(api, **params)``) so tests run
    without real tushare. ``store_dir`` is the raw store root.
    """

    def __init__(self, client: _CallableClient, store_dir: str | Path) -> None:
        self._client = client
        self._store_dir = Path(store_dir)

    def _store_path(self, endpoint: str, ts_code: str) -> Path:
        return self._store_dir / endpoint / f"{ts_code}.parquet"

    def fetch(self, endpoint: str, ts_code: str) -> pd.DataFrame:
        """Pull one instrument's full history for ``endpoint`` (all periods)."""
        if endpoint not in FINANCIAL_ENDPOINTS:
            raise FinancialIngestError(
                f"unknown financial endpoint {endpoint!r}; "
                f"valid: {FINANCIAL_ENDPOINTS}"
            )
        df = self._client.call(
            endpoint, ts_code=ts_code, fields=_fields_arg(endpoint),
        )
        if df is None:
            # None = a transport / quota / rate-limit failure, NOT "no data".
            # Recording it as an empty fetch would silently drop this issuer's
            # statements and count no hole (codex #340 r3 P2). Fail loud so the
            # CLI records it as a hole to re-fetch.
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: provider returned None — a "
                "transport/quota failure, not an empty result; refusing to "
                "record it as a successful empty fetch."
            )
        if not isinstance(df, pd.DataFrame):
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: provider returned "
                f"{type(df).__name__}, expected DataFrame."
            )
        return df

    def ingest(
        self, endpoint: str, ts_code: str, *, fetch_batch: str,
    ) -> IngestResult:
        """Fetch + append-only merge into the store, preserving all versions."""
        fetched = self.fetch(endpoint, ts_code)
        n_fetched = len(fetched)

        data_fields = DATA_FIELDS[endpoint]
        # Required COLUMNS = ts_code + PIT cols + every charter data field.
        # Validated even for an EMPTY frame (codex #340 r9): a bad-fields query
        # or schema regression can come back as an empty DataFrame with missing
        # columns, which must fail loud rather than pass as "no data". A
        # genuinely empty result still carries the requested columns (tushare
        # returns the fields with 0 rows — probe-confirmed), so real no-data is
        # accepted just below. A per-row NA is legitimate missingness, but a
        # missing COLUMN is a provider schema regression (codex #340 P1 + r3 P2).
        required_cols = ("ts_code", *_PIT_COLS, *data_fields)
        missing = [c for c in required_cols if c not in fetched.columns]
        if missing:
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: provider frame missing column(s) "
                f"{missing} — a schema regression, not per-row NA; refusing to "
                "store (would silently break the PIT / coverage contract)."
            )
        if n_fetched == 0:
            return IngestResult(endpoint, ts_code, 0, 0, 0, 0)

        # The returned ts_code must MATCH the requested one (codex #340 r10): a
        # provider bug or query-param regression that returned another issuer's
        # rows would otherwise be written under THIS issuer's store file
        # (<store>/<endpoint>/<ts_code>.parquet), corrupting it.
        returned_codes = set(fetched["ts_code"].astype(str).str.strip())
        if returned_codes != {ts_code}:
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: provider returned ts_code(s) "
                f"{sorted(returned_codes)} != requested {ts_code!r} — refusing to "
                "write another issuer's data under this issuer's store."
            )

        # The logical-key VALUES must be non-null (codex #340 r6 P2): end_date
        # is the PIT report period, update_flag the original/revised flag,
        # ts_code the identity. A blank here corrupts PIT dating and
        # current-version resolution, so refuse the row rather than store it.
        # (ann_date / f_ann_date MAY be per-row NA — a legitimately unavailable
        # filing; those are handled by the contract layer, not refused here.)
        _BLANKS = frozenset({"", "None", "nan", "NaT", "<NA>"})
        for key_col in NONBLANK_KEY_COLS:
            values = fetched[key_col]
            blank = values.isna() | values.astype(str).str.strip().isin(_BLANKS)
            n_blank = int(blank.sum())
            if n_blank:
                raise FinancialIngestError(
                    f"{endpoint} for {ts_code}: {n_blank} row(s) have a blank/NA "
                    f"logical-key value in {key_col!r} — it defines the PIT "
                    "report period / identity / revision flag; refusing to store "
                    "(would corrupt dating and current-version resolution)."
                )

        # update_flag must normalize to EXACTLY "0"(original) / "1"(revised) —
        # the contract distinguishes them by exact string match, so a stray
        # "2"/"Y" would be neither yet still form a separate current-version key,
        # and a float-coerced "0.0" would look like a third flag; both corrupt
        # revision semantics. Canonicalize here (before hashing, so "0" and
        # "0.0" hash identically) and fail loud on anything else (codex #340 r7).
        def _canon_flag(value: Any) -> str:
            token = str(value).strip()
            if token in ("0", "1"):
                return token
            try:
                as_float = float(token)
            except (TypeError, ValueError):
                as_float = float("nan")
            if as_float == 0.0:
                return "0"
            if as_float == 1.0:
                return "1"
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: update_flag has a non-0/1 value "
                f"{value!r} — the contract recognizes only original(0)/revised(1); "
                "refusing to store (would corrupt revision semantics)."
            )

        # The announcement-date KEY columns must be canonical before
        # hashing/keying (codex #351 r3): a re-fetch spelling the SAME
        # announcement differently (None vs '<NA>' vs 'nan', or a float-coerced
        # '20220331.0' vs '20220331') would otherwise mint a DIFFERENT logical
        # key — the same disclosure would duplicate across batches.
        fetched = fetched.copy()
        fetched["update_flag"] = fetched["update_flag"].map(_canon_flag)
        fetched["f_ann_date"] = fetched["f_ann_date"].map(canon_announcement_token)
        fetched["ann_date"] = fetched["ann_date"].map(canon_announcement_token)
        fetched[COL_CONTENT_HASH] = fetched.apply(
            lambda r: content_hash(r, data_fields), axis=1,
        )
        fetched[COL_SOURCE_ENDPOINT] = endpoint
        fetched[COL_FETCH_BATCH] = fetch_batch

        # Ambiguity is judged on the EFFECTIVE announcement day — f_ann_date,
        # falling back to ann_date when f_ann_date is blank (mirroring the
        # contract's announcement resolution). Two same-triple rows announced
        # on DIFFERENT days are distinct, dated disclosure events — both
        # retained, NOT ambiguous (the Step-A ingest lost 27
        # instrument/endpoints to that false ambiguity). A double content on
        # ONE effective announcement day is true ambiguity — the announcement
        # day cannot order them into a record (an identity dimension the key
        # does not carry, e.g. report_type / comp_type; codex #340 r8 +
        # codex #351 r2: a same-f_ann_date pair differing only in ann_date
        # must NOT slip through the raw-column key). dropna=False: fully
        # undated rows share one NA key, so an undated double-content pair
        # still trips the check.
        fetched[EFF_ANN_COL] = effective_announcement_series(fetched)
        per_key = fetched.groupby(
            [*LOGICAL_KEY, EFF_ANN_COL], dropna=False,
        )[COL_CONTENT_HASH].nunique()
        ambiguous = per_key[per_key > 1]
        if len(ambiguous):
            raise FinancialIngestError(
                f"{endpoint} for {ts_code}: effective announcement identity "
                f"{ambiguous.index[0]} has {int(ambiguous.iloc[0])} DIFFERENT "
                "statement contents in one fetch — a provider variant collision "
                "(an identity dimension the announcement day cannot order, e.g. "
                "report_type / comp_type). Refusing an ambiguous collapse; "
                "disambiguate the query so each identity is a single statement."
            )

        path = self._store_path(endpoint, ts_code)
        existing = pd.read_parquet(path) if path.is_file() else None

        if existing is None or existing.empty:
            self._write(path, fetched.drop(columns=[EFF_ANN_COL]))
            # First ingest: every row is new; nothing pre-existed to be
            # "unchanged" (codex #340 P3 — rows_unchanged is 0 here).
            return IngestResult(endpoint, ts_code, n_fetched, n_fetched, 0, 0)

        # Compare each re-fetched row against the LATEST stored version for its
        # logical key — NOT any historical hash (codex #340 r5 P1). A value
        # that changes then reverts (100->200->100) must re-append the third
        # fetch so latest-batch resolution stops exposing the stale 200;
        # matching against all-history hashes would skip the revert and leave
        # 200 current. The STORED rows' announcement-key columns are
        # canonicalized IN MEMORY for this matching (codex #351 r4): a legacy
        # store spelling ('20220331.0', None) must key-match its canonical
        # re-fetch, not mint a phantom second disclosure. The store bytes are
        # not rewritten; read-time resolution canonicalizes the same way in
        # build_contract_frame.
        existing_keyed = existing.copy()
        existing_keyed[EFF_ANN_COL] = effective_announcement_series(existing_keyed)
        key_cols = [*LOGICAL_KEY, EFF_ANN_COL]
        latest = existing_keyed.sort_values(COL_FETCH_BATCH).drop_duplicates(
            subset=key_cols, keep="last",
        )
        stored_latest: dict[tuple[str, ...], str] = {
            tuple(str(v) for v in row[:-1]): str(row[-1])
            for row in latest[[*key_cols, COL_CONTENT_HASH]].itertuples(
                index=False, name=None,
            )
        }

        def _key(row: Any) -> tuple[str, ...]:
            return tuple(str(row[c]) for c in key_cols)

        is_new = fetched.apply(
            lambda r: stored_latest.get(_key(r)) != str(r[COL_CONTENT_HASH]),
            axis=1,
        )
        new_rows = fetched[is_new]
        n_new = len(new_rows)
        n_unchanged = n_fetched - n_new
        # a "changed" re-fetch = a new row whose logical key already existed
        # (a true change OR a revert); a brand-new key is first-seen, not changed.
        n_changed = sum(1 for _, r in new_rows.iterrows() if _key(r) in stored_latest)

        if n_new:
            merged = pd.concat(
                [existing, new_rows.drop(columns=[EFF_ANN_COL])],
                ignore_index=True,
            )
            self._write(path, merged)
        return IngestResult(
            endpoint, ts_code, n_fetched, n_new, n_changed, n_unchanged,
        )

    def _write(self, path: Path, df: pd.DataFrame) -> None:
        try:
            atomic_write_parquet(df, path)
        except OSError as exc:
            raise FinancialIngestError(
                f"failed to write financial store {path}: {exc}"
            ) from exc

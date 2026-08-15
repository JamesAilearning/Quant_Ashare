"""Fundamental date×instrument panel for GP, built only from already-available
disclosures (阶段8 基本面方向 · openspec ``add-fundamental-gp-panel-bridge``).

Research-only, and deliberately a LEAF: this module reads the PIT financial view
and returns plain frames. It is never imported by ``src/factor_mining/`` — the
isolation gate forbids that — so mining and promotion receive the builder
through an injection seam supplied from ``scripts/research/`` instead. Data
flows into GP as a PARAMETER, not as an import dependency, which is why neither
the D5 gate nor the financial-PIT isolation gate needs re-signing.

What the panel guarantees
-------------------------
* **Availability, not report period.** A cell at trade date ``T`` carries the
  disclosure-of-record value of the LATEST report period whose
  ``available_from_trade_date`` is on or before ``T`` — the view's own as-of
  rule. While a newer filing is pending, the cell keeps serving the latest
  period that IS available; it goes NA only when nothing has become available
  yet. "Missing stays missing" forbids IMPUTING an absent value, not serving a
  genuinely available older one.
* **Machine-verifiable evidence.** Every value frame is accompanied by an
  evidence frame of the SAME shape carrying the availability date of the record
  each cell serves, obtained from the SAME view call that produced the value —
  never reconstructed here from raw store reads or inferred from sampled value
  changes, because inference is exactly what evidence has to exclude.
* **One namespace.** Instrument labels are emitted in qlib form (``SH600000``)
  to match the GP panel and forward-return frames. The view speaks ts_code
  (``600000.SH``); the two do not intersect, so a bridge that forwarded the
  view's labels unchanged would produce a panel that silently fails to join.
* **Terminal-form keys.** Panel keys are ``$revenue``-style terminal names,
  because the evaluator resolves only ``$``-prefixed names and looks them up
  verbatim; the view accepts only the bare charter name. The mapping between
  the two is part of this contract, not an accident of naming.

What it deliberately does NOT do
--------------------------------
Cross-endpoint same-period enforcement does not happen here. This builder runs
BEFORE any expression exists, so it cannot tell whether a candidate combines
endpoints: masking globally would discard valid same-endpoint expressions, and
masking not at all leaves mixed-quarter ratios reachable. The panel therefore
CARRIES the report period per field and the enforcement happens at the
expression-aware layer, at the terminals, from the expression's endpoint set.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import NamedTuple

import pandas as pd

from src.data.pit._common import to_qlib_ticker
from src.research.financial_pit_view import (
    FinancialPITDataView,
    FinancialPITViewError,
)

# GP terminals are ``$``-prefixed; the view speaks bare charter field names.
# Registering a terminal does not by itself create a route between the two, so
# the mapping is spelled out here and asserted end to end by the tests.
TERMINAL_PREFIX = "$"

# Suffix for adjacent-prior-period terminals. Must match the grammar's
# ``FeatureRegistry.PRIOR_SUFFIX`` — pinned by a governance test rather
# than by hope, since the two live on opposite sides of the isolation gate.
PRIOR_SUFFIX = "__prior"


class FundamentalPanelError(RuntimeError):
    """Raised when a panel cannot be built HONESTLY.

    Always a refusal, never a repaired or partially-evidenced panel: an
    unverifiable panel is indistinguishable from a leaking one.
    """


def to_terminal(field: str) -> str:
    """Charter field name -> GP terminal name (``revenue`` -> ``$revenue``)."""
    return f"{TERMINAL_PREFIX}{field}"


def to_field(terminal: str) -> str:
    """GP terminal name -> charter field name (``$revenue`` -> ``revenue``).

    Two separate refusals, because they are two different mistakes:

    * not in terminal form — a caller passed a charter name where a terminal
      was expected, and the resulting panel key would never resolve;
    * in terminal form but naming NO charter field — a misspelled or
      mismatched registered terminal (``$not_a_field``, or a bare ``$``).
      Stripping the prefix and returning the remainder would let it travel on
      as if it were mapped, and it would surface much later as a confusing
      lookup miss instead of failing here at the bridge boundary.

    Membership is checked against the VIEW's own field table, so this cannot
    drift from the set of fields that can actually be served.
    """
    if not terminal.startswith(TERMINAL_PREFIX):
        raise FundamentalPanelError(
            f"{terminal!r} is not a terminal name (expected a "
            f"{TERMINAL_PREFIX!r} prefix) — the evaluator resolves only "
            "terminal-form keys, so an unprefixed key would never resolve."
        )
    field = terminal[len(TERMINAL_PREFIX):]
    if field not in _view_field_endpoints():
        raise FundamentalPanelError(
            f"terminal {terminal!r} maps to no charter field — the bridge "
            "refuses it here rather than passing an unmapped key downstream. "
            f"Valid fields: {sorted(_view_field_endpoints())}"
        )
    return field


def _view_field_endpoints() -> Mapping[str, str]:
    """The view's field -> endpoint table (imported lazily to keep this a leaf).

    Deliberately not a private copy: a second table would be free to drift from
    the one that actually serves the values.
    """
    from src.research.financial_pit_view import _FIELD_ENDPOINT  # noqa: PLC0415

    return _FIELD_ENDPOINT


class FundamentalPanel(NamedTuple):
    """The bridge's output: values, their evidence, and their report periods.

    A tuple so callers may unpack ``(panels, evidence, periods)``, named so the
    provenance halves cannot be dropped by accident. ``prior_*`` are populated
    only when the caller asks for adjacent-period provenance.

    Every mapping is keyed by TERMINAL name and every frame has an identical
    (trade date × instrument) shape, so a caller can align values with their
    evidence cell-by-cell without a join.
    """

    panels: dict[str, pd.DataFrame]
    evidence: dict[str, pd.DataFrame]
    periods: dict[str, pd.DataFrame]
    prior_panels: dict[str, pd.DataFrame]
    prior_evidence: dict[str, pd.DataFrame]
    prior_periods: dict[str, pd.DataFrame]

    def as_evaluation_mapping(self) -> tuple[
        dict[str, pd.DataFrame], dict[str, pd.DataFrame],
    ]:
        """``(values, periods)`` in the shape the evaluator consumes.

        Prior-period frames are folded in under ``$field__prior`` keys rather
        than left as side objects. The evaluator resolves terminals by NAME
        against the value mapping, so a prior value parked beside the panel is
        unreachable from any AST — the Δ-shaped starter factors (asset growth,
        the pure-balance-sheet accrual) could not be WRITTEN at all.

        The period frames are folded in the same way, because the alignment
        mask reads them by the same keys. A prior terminal's period is
        SUPPOSED to differ from its current counterpart; the masking groups
        terminals by period generation so that intended difference is not
        mistaken for a cross-endpoint violation.
        """
        values = dict(self.panels)
        periods = dict(self.periods)
        for terminal, frame in self.prior_panels.items():
            values[f"{terminal}{PRIOR_SUFFIX}"] = frame
        for terminal, frame in self.prior_periods.items():
            periods[f"{terminal}{PRIOR_SUFFIX}"] = frame
        return values, periods


# A resolver mapping (trade_date, instruments) -> group labels. Reserved for the
# PIT-industry artifact (a separate change): the signature is fixed NOW so
# wiring it later cannot change the bridge's shape or its defenses.
GroupResolver = Callable[[date, Sequence[str]], Mapping[str, str]]


def build_fundamental_panel(
    view: FinancialPITDataView,
    fields: Sequence[str],
    trade_dates: Sequence[date],
    instruments: Sequence[str],
    *,
    include_prior_period: bool = False,
    group_resolver: GroupResolver | None = None,
) -> FundamentalPanel:
    """Build the fundamental panel for ``fields`` over ``trade_dates``.

    ``fields`` are CHARTER names (``revenue``); the returned mappings are keyed
    by TERMINAL names (``$revenue``). ``instruments`` may be given in either
    namespace; the output is always qlib-form, matching the GP panels.

    ``group_resolver`` is accepted and must be ``None`` today — the PIT-industry
    artifact it will consume belongs to a later change. It is NOT defaulted to a
    current-snapshot fallback: labelling 2018 cross-sections with 2026 industry
    membership is systematic future information, and GP would amplify it. A
    non-None resolver is refused rather than silently ignored.
    """
    if group_resolver is not None:
        raise FundamentalPanelError(
            "group_resolver is reserved for the PIT-industry artifact (a later "
            "change) and must be None today. There is deliberately NO "
            "current-snapshot fallback: grouping past cross-sections by today's "
            "industry labels is future information, and GP amplifies it."
        )
    if isinstance(fields, str) or isinstance(instruments, str):
        raise FundamentalPanelError(
            "fields and instruments must be COLLECTIONS, not single strings — "
            "a str iterates into characters."
        )
    if not fields:
        raise FundamentalPanelError("fields is empty — nothing to build.")
    if not trade_dates:
        raise FundamentalPanelError("trade_dates is empty — nothing to build.")
    if not instruments:
        # An empty universe would "succeed": every view call returns an empty
        # response, the evidence assertions are vacuous, and the caller gets a
        # zero-column panel that LOOKS valid — a missing or misassembled
        # research universe silently becomes a successful build.
        raise FundamentalPanelError("instruments is empty — nothing to build.")
    if len(set(fields)) != len(list(fields)):
        # Duplicate fields collapse into one terminal-keyed accumulator while
        # the build loop processes both occurrences, and the view returns
        # duplicate-named columns so `served[field]` goes two-dimensional —
        # construction would die with an incidental pandas shape error instead
        # of this contract's documented refusal.
        dupes = sorted({f for f in fields if list(fields).count(f) > 1})
        raise FundamentalPanelError(
            f"fields contains duplicates {dupes} — each charter field maps to "
            "exactly one terminal key; deduplicate the request.")

    ordered_dates = sorted(set(trade_dates))
    field_list = list(fields)
    terminals = [to_terminal(f) for f in field_list]

    value_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}
    evidence_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}
    period_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}
    prior_value_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}
    prior_evidence_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}
    prior_period_rows: dict[str, list[pd.Series]] = {t: [] for t in terminals}

    for td in ordered_dates:
        # ONE call yields value, availability evidence and report period
        # together. Splitting them across calls would reintroduce exactly the
        # "evidence obtained separately from the value" hole the contract bans.
        try:
            served = view.as_of(
                td, field_list, instruments,
                include_report_periods=True,
                include_prior_period=include_prior_period,
                include_availability=True,
            )
        except FinancialPITViewError as exc:  # pragma: no cover - passthrough
            raise FundamentalPanelError(
                f"view refused to serve {td}: {exc}") from exc
        served = served.rename(index=_as_qlib)
        for field, terminal in zip(field_list, terminals, strict=True):
            endpoint = _endpoint_of(view, field)
            value_rows[terminal].append(served[field])
            evidence_rows[terminal].append(served[f"_available_from__{endpoint}"])
            period_rows[terminal].append(served[f"_report_period__{endpoint}"])
            if include_prior_period:
                prior_value_rows[terminal].append(served[f"{field}__prior"])
                prior_evidence_rows[terminal].append(
                    served[f"_available_from_prior__{endpoint}"])
                prior_period_rows[terminal].append(
                    served[f"_report_period_prior__{endpoint}"])

    panels = {t: _frame(rows, ordered_dates) for t, rows in value_rows.items()}
    evidence = {t: _frame(rows, ordered_dates) for t, rows in evidence_rows.items()}
    periods = {t: _frame(rows, ordered_dates) for t, rows in period_rows.items()}
    prior_panels = {t: _frame(r, ordered_dates)
                    for t, r in prior_value_rows.items()} if include_prior_period else {}
    prior_evidence = {t: _frame(r, ordered_dates)
                      for t, r in prior_evidence_rows.items()} if include_prior_period else {}
    prior_periods = {t: _frame(r, ordered_dates)
                     for t, r in prior_period_rows.items()} if include_prior_period else {}

    if any(frame.shape[1] == 0 for frame in panels.values()):
        # The RAW request was nonempty, but the view dropped every name (all
        # financial-excluded): the evidence checks below would pass vacuously
        # and the caller would receive a zero-column panel that LOOKS valid —
        # the same misconfigured-universe failure as an empty request, one
        # layer later.
        raise FundamentalPanelError(
            "no effective instruments: every requested name was excluded by "
            "the view (financial issuers?) — refusing a zero-column panel "
            "rather than letting vacuous evidence checks pass it.")
    _assert_evidence_gates_every_cell(panels, evidence, "")
    _assert_evidence_is_monotonic(evidence, "")
    if include_prior_period:
        _assert_evidence_gates_every_cell(prior_panels, prior_evidence, "prior ")
        # NO monotonicity on the prior leg: it legitimately CHANGES ROLES as
        # the current period advances, so its evidence can go backwards with no
        # leakage at all. Chronology: Q4 current on Apr-1; its late-filed Q3
        # prior arrives Apr-29 (prior evidence = Apr-29); Q1 becomes current on
        # May-5 and the prior leg SWITCHES to Q4 — whose evidence is Apr-1, a
        # decrease. Monotonicity is an as-of carry-forward invariant, and the
        # prior leg is not a carry-forward series; the availability gate above
        # (evidence <= trade date) is the invariant that does hold for it.

    return FundamentalPanel(
        panels=panels, evidence=evidence, periods=periods,
        prior_panels=prior_panels, prior_evidence=prior_evidence,
        prior_periods=prior_periods,
    )


def _as_qlib(instrument: str) -> str:
    """ts_code -> qlib label, idempotent for labels already in qlib form."""
    try:
        return to_qlib_ticker(instrument)
    except Exception:  # noqa: BLE001 - already qlib-form, or not convertible
        return instrument


def _endpoint_of(view: FinancialPITDataView, field: str) -> str:
    """The endpoint serving ``field``, resolved through the VIEW's own mapping.

    Deliberately not a private copy: a second table would be free to drift from
    the one that actually served the value, and the evidence column we then read
    would describe a different endpoint's record.
    """
    try:
        return _view_field_endpoints()[field]
    except KeyError as exc:
        raise FundamentalPanelError(
            f"unknown charter field {field!r} — it has no endpoint in the "
            "view's field table, so no panel key could be mapped to it."
        ) from exc


def _frame(rows: list[pd.Series], index: Sequence[date]) -> pd.DataFrame:
    """Stack per-date rows into a (date × instrument) frame.

    Columns are the union across dates, sorted, so a name that enters or leaves
    the universe mid-window still has a column (NA where it is absent) — the
    survivorship-correct shape the GP panels already use.
    """
    frame = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
    frame = frame.reindex(columns=sorted(frame.columns))
    frame.index.name = "datetime"
    frame.columns.name = "instrument"
    return frame


def _assert_evidence_gates_every_cell(
    panels: Mapping[str, pd.DataFrame],
    evidence: Mapping[str, pd.DataFrame],
    label: str,
) -> None:
    """Refuse the panel unless every cell is provably announcement-gated.

    Keyed on the EVIDENCE, not on the value: a served record whose requested
    field is NA still carries its availability date, so checking only non-NA
    VALUES would leave exactly those records unchecked — an early-announced
    record with an NA value could then carry future-dated evidence and survive
    construction, which is the same defect the canaries exist to catch.

    A value present WITHOUT evidence is refused too. That combination means the
    builder produced a number it cannot attribute to a dated disclosure, and an
    unverifiable panel is indistinguishable from a leaking one.
    """
    for terminal, values in panels.items():
        ev = evidence[terminal]
        if ev.shape != values.shape or not ev.index.equals(values.index) \
                or not ev.columns.equals(values.columns):
            raise FundamentalPanelError(
                f"{label}evidence for {terminal} does not align with its values "
                f"({ev.shape} vs {values.shape}) — cells cannot be attributed."
            )
        unevidenced = values.notna() & ev.isna()
        if bool(unevidenced.to_numpy().any()):
            where = _first_true(unevidenced)
            raise FundamentalPanelError(
                f"{label}{terminal} has a value with NO availability evidence at "
                f"{where} — refusing to return a panel whose cells cannot be "
                "attributed to a dated disclosure."
            )
        # Compare as YYYYMMDD strings: the evidence is rendered in that form
        # precisely so this check is a lexicographic comparison with no parsing
        # step that could silently coerce a bad value.
        stamps = ev.apply(lambda col: col.map(_stamp, na_action="ignore"))
        day = pd.Series(
            [pd.Timestamp(d).strftime("%Y%m%d") for d in ev.index],
            index=ev.index,
        )
        violated = stamps.notna() & stamps.gt(day, axis=0)
        if bool(violated.to_numpy().any()):
            where = _first_true(violated)
            raise FundamentalPanelError(
                f"{label}{terminal} carries evidence dated AFTER its trade date "
                f"at {where} — the panel saw a filing before it was available. "
                "Refusing; never silently dropping or repairing the cell."
            )


def _assert_evidence_is_monotonic(
    evidence: Mapping[str, pd.DataFrame], label: str,
) -> None:
    """Refuse a panel whose evidence goes BACKWARDS as trade dates advance.

    As-of carry-forward can only hold the current period or move to a newer
    already-available one, so a per-instrument evidence series must be
    NON-DECREASING (holding is fine — this is not strict monotonicity). A
    decrease means some later trade date served an EARLIER disclosure, which is
    the structural signature of back-filled information.

    This is a second, independent knife: it needs no view, no store and no
    understanding of what the values MEAN, and it catches panels where every
    single cell individually satisfies ``available_from <= trade_date``. A
    diagnostic that only lived in the test suite would not be a defense at all,
    so it runs here, before the panel is ever returned.
    """
    for terminal, ev in evidence.items():
        for inst in ev.columns:
            seen = ev[inst].dropna()
            if seen.is_monotonic_increasing:
                continue
            # Pair each stamp with its predecessor and DROP the first row
            # before comparing: a boolean guard would not help, because
            # `Series.lt` evaluates fully and raises on the str-vs-None pair
            # rather than reporting the defect we are looking for.
            pairs = pd.DataFrame({"cur": seen, "prev": seen.shift()}).dropna()
            drops = pairs[pairs["cur"] < pairs["prev"]]
            when = drops.index[0]
            raise FundamentalPanelError(
                f"{label}{terminal} evidence for {inst} goes BACKWARDS at "
                f"{when:%Y-%m-%d} ({drops.iloc[0]['prev']} -> "
                f"{drops.iloc[0]['cur']}) — a later trade date served an "
                "earlier disclosure, the signature of back-filled information. "
                "Refusing the panel."
            )


def _stamp(value: object) -> str:
    return str(value)


def _first_true(mask: pd.DataFrame) -> str:
    """A concrete (date, instrument) for the error message — a refusal that
    does not say WHERE costs an afternoon."""
    stacked = mask.stack()
    hits = stacked[stacked]
    if hits.empty:  # pragma: no cover - only called when a hit exists
        return "<none>"
    when, who = hits.index[0]
    return f"({when:%Y-%m-%d}, {who})"

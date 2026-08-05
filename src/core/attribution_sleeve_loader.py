"""CSI800 sleeve-grouping loader for attribution (expansion (b) Step 3).

Why this module exists
----------------------
A csi800 run mixes two very different books: the csi300 large-cap sleeve
and the csi500 mid-cap sleeve. Aggregate excess return over SH000906TR
cannot show WHERE alpha (or slippage inflation) lives — the honest
anti-inflation report is a Brinson decomposition whose buckets are the
SLEEVES, not industries. The attribution engine already accepts an
arbitrary ``{instrument: group}`` mapping via
``AttributionConfig.industry_map_override`` + ``industry_taxonomy_id``;
this module builds that mapping from the bundle's PIT membership span
files (``instruments/csi300.txt`` / ``instruments/csi500.txt``, produced
by ``IndexMembershipResolver``), so the sleeve report reuses the whole
existing Brinson path instead of growing a parallel engine.

Semantics
---------
- Membership is resolved AS-OF one date (the attribution period's first
  day, mirroring the ``market_cap`` bench-weight as-of-T0 convention):
  Brinson consumes ONE static grouping per run, so a mid-window index
  rebalance cannot be represented anyway — the as-of date is stamped on
  the resolution for honest reporting.
- The ``2099-12-31`` end date is a SYNTHETIC "active at the last
  snapshot" convention written by ``IndexMembershipResolver``, not
  knowledge of the future — an ``as_of`` past the snapshot horizon would
  silently resolve to STALE composition (codex P1 on #366). The loader
  derives a CONSERVATIVE coverage bound from the span data itself (the
  last date membership demonstrably changed: max over all span STARTs
  and all non-sentinel ENDs) and refuses any ``as_of`` beyond it. The
  bound is PER SLEEVE and every sleeve must individually cover ``as_of``
  (codex #366 r2: the files may be re-resolved separately via
  ``--indices``, so a global max would let the staler sleeve pass). The
  bound deliberately under-approximates: a churn-free covered tail after
  the last change is also refused (fail-loud beats silently-stale;
  re-resolving membership snapshots extends the bound).
- The span files are PIT products (membership intervals, re-entries as
  separate rows); resolving them as-of a historical date is not a
  lookahead risk — grouping feeds post-hoc analysis only, never signals.
- An instrument in BOTH sleeves as-of the same date is a data-integrity
  violation (CSI300 and CSI500 are disjoint by construction) and FAILS
  LOUD rather than silently picking a side.
- Instruments outside both sleeves are NOT labeled here: the attribution
  engine's documented fallback buckets unmapped instruments as
  ``"unknown"`` — for a csi800 run that bucket should be ~empty, and a
  visibly fat ``unknown`` row in the report is itself a loud signal.

This is STEP-3 PREPARATION: no pipeline / walk-forward config plumbing
(that wiring belongs to a future csi800 campaign spec). The Step-4 probe
brief consumes this loader post-hoc.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# The resolver's synthetic "active at the last snapshot" end date —
# imported (not duplicated) so a convention change cannot drift apart.
from src.data.pit._common import QLIB_OPEN_END_DATE

_OPEN_END_SENTINEL = date.fromisoformat(QLIB_OPEN_END_DATE)

SLEEVE_TAXONOMY_ID = "csi800_sleeve_v1"
SLEEVE_CSI300 = "csi300_sleeve"
SLEEVE_CSI500 = "csi500_sleeve"

_SLEEVE_FILES: tuple[tuple[str, str], ...] = (
    ("csi300.txt", SLEEVE_CSI300),
    ("csi500.txt", SLEEVE_CSI500),
)


class SleeveResolutionError(RuntimeError):
    """Raised on any failure while building the sleeve grouping map."""


@dataclass(frozen=True)
class SleeveResolution:
    """Frozen result: the grouping map + provenance for honest reporting."""

    sleeve_map: dict[str, str]
    taxonomy_id: str
    as_of: str
    # BINDING bound: min across per-sleeve demonstrated coverage —
    # the resolver's stamp where present, else the legacy last-change
    # proxy (2026-08-05-membership-coverage-stamp).
    coverage_end: str
    n_csi300: int
    n_csi500: int


def _parse_iso(value: str, context: str) -> date:
    # date.fromisoformat accepts the compact "YYYYMMDD" form on 3.11+;
    # the span files and run configs are dashed — enforce the dashed
    # shape so a compact/typo'd date cannot silently parse.
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise SleeveResolutionError(
            f"{context}: {value!r} is not an ISO date (YYYY-MM-DD)."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SleeveResolutionError(
            f"{context}: {value!r} is not an ISO date (YYYY-MM-DD)."
        ) from exc


def _parse_spans(path: Path) -> list[tuple[str, date, date]]:
    """Parse one span file into ``(instrument, start, end)`` rows."""
    if not path.is_file():
        raise SleeveResolutionError(
            f"membership span file missing: {path} — the bundle must carry "
            "the PIT membership products (IndexMembershipResolver output) "
            "before a sleeve report can be built."
        )
    spans: list[tuple[str, date, date]] = []
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SleeveResolutionError(
                f"{path}:{lineno}: expected 'INSTRUMENT\\tSTART\\tEND', "
                f"got {raw!r}."
            )
        inst, start_s, end_s = parts
        start = _parse_iso(start_s, f"{path}:{lineno} START")
        end = _parse_iso(end_s, f"{path}:{lineno} END")
        if start > end:
            raise SleeveResolutionError(
                f"{path}:{lineno}: span start {start_s} > end {end_s}."
            )
        spans.append((inst, start, end))
    return spans


def _coverage_bound(spans: list[tuple[str, date, date]]) -> date | None:
    """Last date membership DEMONSTRABLY changed: max over span starts
    and non-sentinel ends. The ``2099-12-31`` sentinel is excluded — it
    is the resolver's synthetic "active at the last snapshot" marker,
    not evidence of coverage (codex P1 on #366). This is the LEGACY
    per-sleeve bound; when the resolver has persisted a
    demonstrated-coverage stamp (2026-08-05-membership-coverage-stamp)
    the stamp's last-snapshot date supersedes it — the resolver
    provably SAW snapshots through that date, so a churn-free tail
    inside it is covered fact, not synthesis."""
    real_dates = [s for _, s, _ in spans]
    real_dates += [e for _, _, e in spans if e != _OPEN_END_SENTINEL]
    return max(real_dates) if real_dates else None


# Written by src.data.pit.index_membership (the resolver OWNS the
# artifact); names imported here would invert the layering, so the two
# constants are deliberately duplicated and pinned equal by test.
_COVERAGE_STAMP_FILENAME = "membership_coverage.json"
_COVERAGE_STAMP_SCHEMA = "membership_coverage_v1"


def _load_coverage_stamp(root: Path) -> dict[str, date]:
    """Parse the demonstrated-coverage stamp into ``{label: date}``.

    An ABSENT stamp is legitimate legacy (returns ``{}`` — the caller
    falls back to the last-change bound). A stamp that exists but is
    malformed REFUSES: silently falling back would launder corruption
    into conservatism, and the repair path (re-run
    ``03_resolve_index_membership``) is cheap and explicit."""
    path = root / _COVERAGE_STAMP_FILENAME
    # ABSENT and NOT-A-REGULAR-FILE are different states (codex #394
    # r2): a directory or dangling/dir-pointing symlink at this path
    # is a PRESENT-but-malformed artifact — treating it as absent
    # would silently fall back to legacy bounds, laundering the
    # malformation the moment the legacy bound happens to cover as_of.
    if not path.exists() and not path.is_symlink():
        return {}
    if not path.is_file():
        raise SleeveResolutionError(
            f"coverage stamp path exists but is not a regular file: "
            f"{path} — remove it and re-resolve membership "
            "(03_resolve_index_membership) to rebuild the stamp."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SleeveResolutionError(
            f"coverage stamp unreadable: {path} ({exc}) — re-resolve "
            "membership (03_resolve_index_membership) to rebuild it."
        ) from exc
    if (not isinstance(payload, dict)
            or payload.get("schema_version") != _COVERAGE_STAMP_SCHEMA
            or not isinstance(payload.get("sleeves"), dict)):
        raise SleeveResolutionError(
            f"coverage stamp has an unexpected shape: {path} — expected "
            f"schema_version {_COVERAGE_STAMP_SCHEMA!r} with a "
            "'sleeves' object; re-resolve membership to rebuild it."
        )
    stamps: dict[str, date] = {}
    for filename, label in _SLEEVE_FILES:
        entry = payload["sleeves"].get(filename)
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(
                entry.get("last_snapshot"), str):
            raise SleeveResolutionError(
                f"coverage stamp entry for {filename} is malformed in "
                f"{path} — re-resolve membership to rebuild it."
            )
        stamps[label] = _parse_iso(
            entry["last_snapshot"],
            f"{path} sleeves[{filename}].last_snapshot")
    return stamps


def sleeve_turnover(
    positions: Mapping[str, Mapping[str, float]],
    sleeve_map: Mapping[str, str],
    unknown_label: str = "unknown",
) -> dict[str, dict[str, float]]:
    """Per-sleeve ONE-WAY turnover from a daily positions map
    (``{date_str: {instrument: weight}}`` — the authoritative
    ``CanonicalBacktestOutput.positions`` / persisted positions series).

    For each consecutive date pair the one-way turnover of a sleeve is
    ``0.5 * Σ|Δw|`` over the instruments the sleeve owns (unmapped
    instruments aggregate under ``unknown_label`` — same honest-bucket
    convention as the Brinson report). Returns
    ``{sleeve: {"total_oneway": x, "daily_mean_oneway": y,
    "n_transitions": n}}``. This is the guard-2 building block the
    ignition tooling uses for the per-sleeve turnover diagnostic; it is
    pure and deterministic (dates processed in sorted order)."""
    dates = sorted(positions)
    totals: dict[str, float] = {}
    n_transitions = max(0, len(dates) - 1)
    for prev_d, next_d in zip(dates, dates[1:], strict=False):
        prev_w, next_w = positions[prev_d], positions[next_d]
        for inst in set(prev_w) | set(next_w):
            sleeve = sleeve_map.get(inst, unknown_label)
            delta = abs(next_w.get(inst, 0.0) - prev_w.get(inst, 0.0))
            totals[sleeve] = totals.get(sleeve, 0.0) + 0.5 * delta
    return {
        sleeve: {
            "total_oneway": total,
            "daily_mean_oneway": (total / n_transitions
                                  if n_transitions else 0.0),
            "n_transitions": float(n_transitions),
        }
        for sleeve, total in sorted(totals.items())
    }


def resolve_sleeve_map(provider_dir: Path | str,
                       as_of: str) -> SleeveResolution:
    """Build ``{instrument: sleeve}`` as-of ``as_of`` from the bundle's
    PIT membership span files. Feed the result into
    ``AttributionConfig(industry_map_override=resolution.sleeve_map,
    industry_taxonomy_id=resolution.taxonomy_id)``.

    Fails loud on: missing span files, malformed rows, an ``as_of``
    beyond the membership data's demonstrated coverage (open-ended
    sentinel rows would silently resolve STALE composition — codex P1 on
    #366), an instrument in both sleeves (disjointness violation), or an
    empty sleeve (an as-of before the membership coverage is a
    misconfiguration, not an empty index)."""
    root = Path(provider_dir) / "instruments"
    as_of_date = _parse_iso(as_of, "as_of")
    spans_by_label = {label: _parse_spans(root / filename)
                      for filename, label in _SLEEVE_FILES}
    # PER-SLEEVE coverage (codex #366 r2): the span files may be
    # re-resolved SEPARATELY (03_resolve_index_membership --indices), so
    # a global max would let the STALER sleeve silently resolve outdated
    # composition. Every sleeve must individually cover as_of.
    change_bounds = {label: _coverage_bound(spans)
                     for label, spans in spans_by_label.items()}
    stamps = _load_coverage_stamp(root)
    bounds: dict[str, date | None] = {}
    for label, changed in change_bounds.items():
        stamped = stamps.get(label)
        if stamped is not None and changed is not None and stamped < changed:
            raise SleeveResolutionError(
                f"coverage stamp for {label} claims last_snapshot "
                f"{stamped.isoformat()} but the span file shows a "
                f"membership change on {changed.isoformat()} — the "
                "artifact contradicts itself; re-resolve membership "
                "(03_resolve_index_membership) to rebuild both."
            )
        # Stamp = what the resolver demonstrably SAW (supersedes the
        # last-change proxy — a churn-free tail inside it is covered
        # fact); absent stamp = legacy last-change semantics.
        bounds[label] = stamped if stamped is not None else changed
    stale = sorted(label for label, b in bounds.items()
                   if b is None or as_of_date > b)
    if stale:
        detail = ", ".join(
            f"{label}: {b.isoformat() if b else 'none'}"
            + (" (stamp)" if label in stamps else " (last change)")
            for label, b in sorted(bounds.items()))
        raise SleeveResolutionError(
            f"as_of {as_of} is beyond the demonstrated membership coverage "
            f"of {', '.join(stale)} (per-sleeve bound: {detail}) "
            f"— the {QLIB_OPEN_END_DATE} end date is a synthetic 'active at "
            "the last snapshot' marker, not knowledge of the future; "
            "resolving past a sleeve's coverage would silently attribute "
            "with STALE composition. Re-resolve membership snapshots "
            "(03_resolve_index_membership) for the stale sleeve(s) first — "
            "the resolver stamps its demonstrated snapshot coverage, so a "
            "churn-free tail it has actually seen is admitted; without a "
            "stamp the bound stays the conservative last CHANGE."
        )
    coverage = min(b for b in bounds.values() if b is not None)
    sleeve_map: dict[str, str] = {}
    counts: dict[str, int] = {}
    seen: dict[str, str] = {}
    for filename, label in _SLEEVE_FILES:
        members = {inst for inst, start, end in spans_by_label[label]
                   if start <= as_of_date <= end}
        if not members:
            raise SleeveResolutionError(
                f"no {label} members as-of {as_of} in {root / filename} — "
                "an as-of outside the membership coverage is a "
                "misconfiguration, not an empty index."
            )
        for inst in members:
            if inst in seen:
                raise SleeveResolutionError(
                    f"{inst} is a member of BOTH {seen[inst]} and {label} "
                    f"as-of {as_of} — CSI300/CSI500 are disjoint by "
                    "construction; refusing to silently pick a side."
                )
            seen[inst] = label
            sleeve_map[inst] = label
        counts[label] = len(members)
    return SleeveResolution(
        sleeve_map=sleeve_map,
        taxonomy_id=SLEEVE_TAXONOMY_ID,
        as_of=as_of,
        coverage_end=coverage.isoformat(),
        n_csi300=counts[SLEEVE_CSI300],
        n_csi500=counts[SLEEVE_CSI500],
    )

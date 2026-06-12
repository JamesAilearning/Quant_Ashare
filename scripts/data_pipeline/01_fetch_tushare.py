"""CLI: Phase A.1 — fetch raw Tushare data for A-share survivorship correction.

Reads ``TUSHARE_TOKEN`` from the environment via
:class:`TushareClient.from_environment`. The token MUST NOT appear in
any CLI argument, config file, or log line.

Usage::

    # Smoke test: pull stock_basic only, dry-run
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic --dry-run \\
        --output-dir /tmp/tushare_dry

    # Real fetch of stock_basic + namechange (fast — 3 calls total)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic,namechange \\
        --output-dir D:/qlib_data/tushare_raw

    # Full backfill (long pole — 12-24h at 5000-point tier)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --output-dir D:/qlib_data/tushare_raw

Resume
------
Aggregate endpoints (stock_basic / namechange / suspend_d / index_weight)
use per-file existence as their checkpoint, pierced by ``--refresh-current``
(aggregates only) and by prior-manifest holes. Per-``(ticker, year)``
endpoints (daily / adj_factor / daily_basic) use the P3-7b FRESHNESS rule
instead of bare existence: an existing year file is skipped only when its
``max(trade_date)`` reaches everything this run's range can expect of it
(bounded by the year, the requested end date, and the ticker's listing
window); a stale or suspicious-empty file is re-pulled for the whole year
(one API call). Past years already attested by the previous manifest's
coverage are not re-scanned — ``--verify-all-years`` forces a full sweep.

Manifest red line (P3-7b): the fetch NEVER deletes ``fetch_manifest.json``
on its own. Every failure path exits non-zero and leaves the manifest
exactly as it was; a deliberate fresh start is ``--reset-manifest``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402
from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    FetchManifestError,
    build_manifest,
    clear_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetcher import (  # noqa: E402
    DEFAULT_INDICES,
    DEFAULT_RATE_LIMIT_SLEEP_MS,
    ENDPOINTS,
    FetchHole,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)

# `setup_logging` only attaches a handler to the ``src.*`` logger
# namespace; using ``__name__`` here (which resolves to ``__main__`` when
# the script is run via ``python scripts/...``) would silently drop log
# output. Pin under ``src.scripts.*`` so handlers attach correctly.
_logger = get_logger("src.scripts.data_pipeline.fetch_tushare")


def _log_hole_report(holes: tuple[FetchHole, ...]) -> None:
    """Print a per-endpoint hole report (no-op when there are no holes).

    Called on BOTH the completed-with-holes path AND the hard-abort path so a
    recorded hole is never silently lost — even when a later prerequisite
    failure aborts the run, the holes accumulated before it are surfaced.
    """
    if not holes:
        return
    _logger.error("")
    _logger.error("=== HOLES (%d) — fetch is INCOMPLETE ===", len(holes))
    by_endpoint: dict[str, int] = {}
    for h in holes:
        by_endpoint[h.endpoint] = by_endpoint.get(h.endpoint, 0) + 1
    for endpoint, count in sorted(by_endpoint.items()):
        _logger.error("  %-14s  holes=%5d", endpoint, count)
    for h in holes[:20]:
        _logger.error(
            "    - %s [%s] (%s): %s",
            h.endpoint, h.unit, h.reason_class, h.last_error,
        )
    if len(holes) > 20:
        _logger.error("    ... and %d more", len(holes) - 20)
    _logger.error(
        "Re-run with the same --output-dir to fill the holes "
        "(existing files are skipped; only the missing units are re-fetched)."
    )


def _reset_manifest(manifest_path: Path) -> int:
    """Explicit fresh start (``--reset-manifest``): the ONLY path that deletes
    the manifest (P3-7b red line — no failure path auto-clears it). Loud, and
    fail-loud if the file cannot be removed."""
    try:
        clear_manifest(manifest_path)
    except OSError as exc:
        _logger.error(
            "--reset-manifest could not remove %s: %s", manifest_path, exc,
        )
        return 1
    _logger.warning(
        "--reset-manifest: removed %s. Coverage and hole records start from "
        "scratch this run; the freshness rule will re-verify every year file "
        "it scans.", manifest_path,
    )
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch raw Tushare data for the A-share survivorship "
                    "correction pipeline (Phase A.1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write parquet dumps into. Existing files are "
             "skipped (resume semantics).",
    )
    p.add_argument(
        "--start-date", default="20000101",
        help="YYYYMMDD inclusive (default: 20000101).",
    )
    p.add_argument(
        "--end-date", default="20251231",
        help="YYYYMMDD inclusive (default: 20251231).",
    )
    p.add_argument(
        "--endpoints", default=",".join(ENDPOINTS),
        help=f"Comma-separated endpoint names. Default: all 7. Valid: {','.join(ENDPOINTS)}",
    )
    p.add_argument(
        "--indices", default=",".join(DEFAULT_INDICES),
        help=f"Comma-separated index codes for index_weight. Default: {','.join(DEFAULT_INDICES)}",
    )
    p.add_argument(
        "--rate-limit-sleep-ms", type=int, default=DEFAULT_RATE_LIMIT_SLEEP_MS,
        help=f"Sleep (ms) between Tushare calls. Default {DEFAULT_RATE_LIMIT_SLEEP_MS}ms "
             "= 300 calls/min; tune for your account tier.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Do not write any files; log what would happen.",
    )
    p.add_argument(
        "--refresh-current", action="store_true",
        help="Ignore resume's exists-skip for the AGGREGATE units a daily "
             "update must bring current: stock_basic (both buckets) and the "
             "namechange / suspend_d aggregates (P3-6a). The per-ticker "
             "endpoints (daily / adj_factor / daily_basic) no longer need "
             "this: their year files are re-pulled exactly when stale, by the "
             "P3-7b max(trade_date) freshness rule. index_weight is not "
             "refreshed.",
    )
    p.add_argument(
        "--verify-all-years", action="store_true",
        help="Scan EVERY year file of the requested range for freshness, "
             "ignoring the previous manifest's coverage watermark (P3-7b). "
             "Use after suspected external mutation of the dump, or once "
             "over a dump whose manifest predates the freshness rule.",
    )
    p.add_argument(
        "--reset-manifest", action="store_true",
        help="Deliberate fresh start: delete fetch_manifest.json before the "
             "run (P3-7b). This is the ONLY way the manifest is ever removed "
             "— no failure path auto-clears it.",
    )
    p.add_argument(
        "--snapshot-date", default=None,
        help="YYYYMMDD to stamp stock_basic's embedded snapshot_date with, "
             "instead of the system date at write time. The daily-update "
             "orchestrator freezes ONE run date and passes it here so a fetch "
             "spanning midnight stamps the planned date, not the rollover "
             "date (P3-6a).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    endpoints = tuple(e.strip() for e in args.endpoints.split(",") if e.strip())
    indices = tuple(i.strip() for i in args.indices.split(",") if i.strip())

    snapshot_now: date | None = None
    if args.snapshot_date is not None:
        try:
            snapshot_now = datetime.strptime(args.snapshot_date, "%Y%m%d").date()
        except ValueError:
            _logger.error(
                "--snapshot-date must be YYYYMMDD, got %r", args.snapshot_date,
            )
            return 2

    manifest_path = args.output_dir / MANIFEST_FILENAME
    if args.reset_manifest:
        rc = _reset_manifest(manifest_path)
        if rc != 0:
            return rc

    # Prior-manifest holes force their units past the exists-skip this run
    # (codex P1): a refresh failure leaves yesterday's file on disk plus a
    # hole; the freshness rule re-attempts stale files wherever it scans, but
    # a holed unit must be re-attempted even in years the scan scope skips. A
    # corrupt manifest stops the run — and per the P3-7b red line it is LEFT
    # IN PLACE for inspection: only an explicit --reset-manifest removes it.
    try:
        prev_manifest = read_manifest(manifest_path)
    except FetchManifestError as exc:
        _logger.error(
            "Fetch manifest unreadable at run start: %s — refusing to run "
            "against unreadable provenance. The file was left untouched for "
            "inspection; pass --reset-manifest for a deliberate fresh start.",
            exc,
        )
        return 1
    force_retry_units = (
        frozenset(
            (h.endpoint, h.unit)
            for ep in prev_manifest.endpoints.values()
            for h in ep.holes
        )
        if prev_manifest is not None
        else frozenset()
    )
    if force_retry_units:
        _logger.info(
            "Prior manifest records %d hole(s); forcing those units past the "
            "exists-skip this run.", len(force_retry_units),
        )
    # P3-7b scan scope: years whose whole expected slice lies inside the prior
    # manifest's attested (start, end) range are not re-scanned for freshness;
    # the final requested year always is. Both ends matter (codex P1 on #240:
    # an end-only watermark would trust never-attested years BEFORE the prior
    # coverage start on a backward backfill). --verify-all-years overrides.
    assume_verified_ranges = (
        {
            ep_name: (ep.coverage_start_date, ep.coverage_end_date)
            for ep_name, ep in prev_manifest.endpoints.items()
            if ep.coverage_start_date and ep.coverage_end_date
        }
        if prev_manifest is not None
        else {}
    )

    try:
        config = TushareFetcherConfig(
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            endpoints=endpoints,
            indices=indices,
            rate_limit_sleep_ms=args.rate_limit_sleep_ms,
            dry_run=args.dry_run,
            refresh_current=args.refresh_current,
            now=snapshot_now,
            force_retry_units=force_retry_units,
            assume_verified_ranges=assume_verified_ranges,
            verify_all_years=args.verify_all_years,
        )
    except TushareFetcherError as exc:
        _logger.error("Config invalid: %s", exc)
        return 2

    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _logger.error("Cannot construct Tushare client: %s", exc)
        return 1

    fetcher = TushareFetcher(client, config)
    try:
        results = fetcher.fetch()
    except (TushareFetcherError, TushareClientError) as exc:
        _logger.error("Fetch failed: %s", exc)
        # A hard abort still surfaces any holes recorded before it, so a
        # recorded hole is never silently lost (the abort dominates the exit
        # code — this stays the hard-abort path, return 1).
        _log_hole_report(fetcher.holes)
        # P3-7b red line: the manifest is LEFT EXACTLY AS IT WAS. A mid-run
        # abort can leave partial output the manifest does not describe yet —
        # but the prior manifest still truthfully describes the units IT
        # recorded, this run's exit 1 stops any orchestrated build (EXIT 11),
        # and the freshness rule + force-retry re-attempt whatever this run
        # touched or holed on the next run. Deleting it (the old behavior)
        # destroyed the hole ledger the guards exist to keep.
        if not config.dry_run:
            _logger.error(
                "fetch_manifest.json was left untouched (it describes the dir "
                "as of the LAST completed run; this aborted run's partial "
                "progress is not yet recorded). Re-run to complete and "
                "re-record; --reset-manifest only for a deliberate fresh start."
            )
        return 1

    _logger.info("")
    _logger.info("=== Summary ===")
    total_written = 0
    total_rows = 0
    total_skipped = 0
    for r in results:
        _logger.info(
            "  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
            r.endpoint, r.files_written, r.rows_total, r.skipped,
        )
        total_written += r.files_written
        total_rows += r.rows_total
        total_skipped += r.skipped
    _logger.info("  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
                 "TOTAL", total_written, total_rows, total_skipped)

    # P3-4b: persist this run's coverage + holes to fetch_manifest.json, merged
    # with the prior run so a unit re-fetched this run self-heals its hole (and a
    # still-failing unit's hole stays). Skipped under --dry-run (no side effects).
    # Downstream gating on a holey manifest is P3-4c; this only records.
    if not config.dry_run:
        manifest_path = config.output_dir / MANIFEST_FILENAME
        # The whole read → build → merge → write is fail-loud: read_manifest
        # rejects an unusable prior manifest, merge_manifest refuses a
        # narrower-scope merge (both FetchManifestError), and write_manifest can
        # raise OSError (disk full / permissions / rename failure). All of these
        # MUST surface as a clean non-zero exit, not an escaping traceback after
        # the fetch already ran (codex P2).
        try:
            prev_manifest = read_manifest(manifest_path)
            current_manifest = build_manifest(
                results, fetcher.holes, config.start_date, config.end_date,
            )
            write_manifest(manifest_path, merge_manifest(prev_manifest, current_manifest))
        except (FetchManifestError, OSError) as exc:
            _logger.error("Fetch manifest update failed: %s", exc)
            # P3-7b red line: a refused merge (narrower / disjoint scope) or a
            # failed write exits 1 with the manifest LEFT BYTE-FOR-BYTE as it
            # was. The prior manifest still truthfully describes the units it
            # recorded — a refused merge means THIS run's scope could not
            # extend it, not that it became wrong; this run's writes only made
            # recorded units fresher (over-recorded holes are re-attempted by
            # force-retry and self-heal on the next full-range run). The old
            # auto-clear turned a refusal whose purpose is to PRESERVE hole
            # records into the deletion of those records (fail-forget).
            _logger.error(
                "fetch_manifest.json was left untouched. Re-run the full "
                "range to extend it, or pass --reset-manifest for a "
                "deliberate fresh start."
            )
            return 1
        _logger.info("Wrote fetch manifest: %s", manifest_path)

    # Continue-on-error (P3-4a): the fetch finished, but any unit whose call
    # exhausted its retryable retries (or a per-ticker endpoint skipped because
    # stock_basic holed) was recorded as a hole instead of aborting the whole
    # run. A holey dump MUST NOT be mistaken for a complete one — report the
    # holes loudly and exit non-zero so an orchestrator (and the operator) treat
    # this as "completed with holes", never "success". Re-run with the same
    # --output-dir to fill them (file-existence resume re-fetches only the
    # missing units).
    holes = fetcher.holes
    if holes:
        _log_hole_report(holes)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

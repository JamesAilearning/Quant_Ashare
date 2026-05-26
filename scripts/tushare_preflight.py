"""Tushare API preflight check.

Runs the smallest possible Tushare interaction that proves the v1
ingest will succeed before you commit to the full ~2-minute run:

1. ``TUSHARE_TOKEN`` env var present + non-empty.
2. ``tushare`` module importable.
3. ``index_classify(level='L2', src='SW2021')`` returns a non-empty
   DataFrame with the columns the publisher's parser expects.
4. ``index_member(index_code=<one>)`` returns a non-empty DataFrame
   with the columns the publisher's parser expects (this is the
   2000-points API and the most likely failure point).
5. The publisher's row parsers can extract usable rows from the
   responses — i.e. column names match what
   :class:`TushareIndustryPublisher` assumes.

On success: prints a summary table and a wall-clock estimate for the
full ingest. On any failure: prints which step failed and why, then
exits with status 1.

Privacy
-------
- The token itself is never printed: only its length and a short
  head/tail snippet (``head=abc123..., tail=...789f``).
- Per-industry member instrument lists are never printed; only counts
  and column metadata. This keeps the output safe to paste back for
  diagnostics.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Repo root on sys.path so ``import src.*`` works whether you launch
# the script from the repo root or from anywhere else.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import setup_logging  # noqa: E402
from src.data.tushare.client import (  # noqa: E402
    _TOKEN_ENV_VAR,
    TushareClient,
    TushareClientError,
)
from src.data.tushare.industry_publisher import (  # noqa: E402
    DEFAULT_SHENWAN_SRC,
    TushareIndustryPublisher,
)


def _line(label: str, ok: bool, detail: str = "") -> None:
    """Print one PASS/FAIL row with optional indented detail block.

    Tag width is fixed at 4 chars (``PASS`` / ``FAIL``) so columns line
    up regardless of which result you got.
    """
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {label}")
    for d_line in detail.splitlines():
        if d_line:
            print(f"          {d_line}")


def _summarise_token(token: str) -> str:
    """Return a non-revealing description of a token.

    Always discloses length so the user can sanity-check that the
    env var actually contains the full token (Tushare tokens are
    typically 32-40 chars). For short / empty strings we degrade
    gracefully — never index into nothing.
    """
    n = len(token)
    if n == 0:
        return "len=0 (empty)"
    if n < 6:
        return f"len={n} (truncated by safety check; this is too short to be a real token)"
    return f"len={n}, head={token[:6]}..., tail=...{token[-4:]}"


def _run_preflight() -> int:
    """Execute the 5-step check sequence and return an exit code.

    Returns 0 on every step passing, 1 on any failure. Splits cleanly
    so :func:`main` can install logging and forward the exit code,
    while unit tests exercise the step logic directly.
    """
    print("=" * 60)
    print("TUSHARE PREFLIGHT")
    print("=" * 60)

    # ---- Step 1: env var present ------------------------------------
    token = os.environ.get(_TOKEN_ENV_VAR, "").strip()
    if not token:
        _line(
            f"Step 1/5: {_TOKEN_ENV_VAR} env var", False,
            f"{_TOKEN_ENV_VAR} is unset or empty.\n"
            f"Set it first:\n"
            f'  export {_TOKEN_ENV_VAR}=\'your_pro_token_here\'  (Bash)\n'
            f"  $env:{_TOKEN_ENV_VAR} = 'your_pro_token_here'  (PowerShell)",
        )
        return 1
    _line(f"Step 1/5: {_TOKEN_ENV_VAR} env var", True, _summarise_token(token))

    # ---- Step 2: tushare importable ---------------------------------
    try:
        import tushare as ts
        ts_version = getattr(ts, "__version__", "unknown")
    except ImportError:
        _line(
            "Step 2/5: tushare module import", False,
            "tushare is not installed. Run from the repo root:\n"
            "  python -m pip install -e \".[tushare]\"",
        )
        return 1
    _line("Step 2/5: tushare module import", True, f"version={ts_version}")

    # Build the client. Failures here are token / Pro-tier issues.
    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _line("Tushare client construction", False, str(exc))
        return 1

    # ---- Step 3: index_classify -------------------------------------
    print("  ... fetching L2 industry list ...")
    t0 = time.perf_counter()
    try:
        industry_df = client.call(
            "index_classify", level="L2", src=DEFAULT_SHENWAN_SRC,
        )
    except TushareClientError as exc:
        _line(
            f"Step 3/5: index_classify(level=L2, src={DEFAULT_SHENWAN_SRC})",
            False,
            f"{type(exc).__name__}: {exc}\n"
            "index_classify needs ~600 Tushare points. Check your account tier.",
        )
        return 1
    elapsed_classify = time.perf_counter() - t0

    industries = TushareIndustryPublisher._parse_industry_list(
        industry_df, level="L2",
    )
    if not industries:
        _line(
            "Step 3/5: index_classify",
            False,
            f"rows={len(industry_df)}, columns={list(industry_df.columns)}\n"
            "Tushare returned a DataFrame but the publisher's parser found "
            "zero L2 industries. Either columns shifted (see Step 5) or "
            "your account doesn't carry SW2021 access.",
        )
        return 1
    sample_idx_code, sample_name = industries[0]
    _line(
        f"Step 3/5: index_classify(level=L2, src={DEFAULT_SHENWAN_SRC})", True,
        f"rows={len(industry_df)}, columns={list(industry_df.columns)}\n"
        f"parsed {len(industries)} L2 industries (elapsed {elapsed_classify:.2f}s)\n"
        f"sample: {sample_idx_code} / {sample_name}",
    )

    # ---- Step 4: index_member (the 2000-points API) -----------------
    print(f"  ... fetching members for {sample_name} ({sample_idx_code}) ...")
    t0 = time.perf_counter()
    try:
        members_df = client.call("index_member", index_code=sample_idx_code)
    except TushareClientError as exc:
        _line(
            f"Step 4/5: index_member({sample_idx_code})", False,
            f"{type(exc).__name__}: {exc}\n"
            "index_member needs ~2000 Tushare points. The v1 ingest cannot "
            "run if this call is rejected — every industry needs one.",
        )
        return 1
    elapsed_member = time.perf_counter() - t0

    members = TushareIndustryPublisher._parse_active_members(members_df)
    if not members:
        _line(
            f"Step 4/5: index_member({sample_idx_code})", False,
            f"rows={len(members_df)}, columns={list(members_df.columns)}\n"
            "Tushare returned rows but the parser extracted zero active "
            "members. Likely cause: column rename in Tushare (see Step 5) "
            "or every member is flagged is_new='N'.",
        )
        return 1
    _line(
        f"Step 4/5: index_member({sample_idx_code})", True,
        f"rows={len(members_df)}, columns={list(members_df.columns)}\n"
        f"parsed {len(members)} active members of {sample_name} "
        f"(elapsed {elapsed_member:.2f}s)",
    )

    # ---- Step 5: column-name compatibility --------------------------
    # The publisher's parsers depend on a small fixed set of columns.
    # If Tushare ever renames any of these the ingest fails opaquely —
    # checking up front turns it into a clear preflight failure.
    classify_required = {"index_code", "industry_name"}
    member_required = {"con_code"}
    missing_classify = classify_required - set(industry_df.columns)
    missing_member = member_required - set(members_df.columns)
    if missing_classify or missing_member:
        _line(
            "Step 5/5: column-name compatibility", False,
            f"missing from index_classify: {sorted(missing_classify)}\n"
            f"missing from index_member:   {sorted(missing_member)}\n"
            "Tushare may have renamed columns. Update the parser in "
            "src/data/tushare/industry_publisher.py to match.",
        )
        return 1
    _line(
        "Step 5/5: column-name compatibility", True,
        "publisher's parsers will work as-is",
    )

    # ---- Summary ----------------------------------------------------
    print("=" * 60)
    print("SUMMARY: 5/5 steps passed")
    estimated_seconds = len(industries) * elapsed_member
    print(
        f"Estimated full ingest: {len(industries)} industry calls "
        f"x ~{elapsed_member:.1f}s/call ~= {estimated_seconds:.0f}s "
        f"({estimated_seconds / 60:.1f} min)"
    )
    print("You're clear to run:")
    print("  python scripts/ingest_tushare_industry.py config_tushare.yaml")
    print("=" * 60)
    return 0


def main() -> None:
    setup_logging()
    sys.exit(_run_preflight())


if __name__ == "__main__":
    main()

"""
Verify whether the qlib provider data includes delisted stocks.

Three possible outcomes:
1. GOOD: Delisted stocks present, data ends at delisting date
2. BAD:  Delisted stocks present, data extends beyond delisting (data error)
3. UGLY: Delisted stocks absent (survivorship bias — proceed with caution)

KNOWN_DELISTED reference list — correction history
--------------------------------------------------
The prior version of this list contained 7 entries that were built from
agent-fabricated facts. Cross-checking against Tushare
``stock_basic(list_status="D")`` on 2026-05-22 showed:

- 4 of 7 entries (SH600615 丰华股份, SH600753 "庞大集团", SZ000010 美丽生态,
  SH600268 国电南自) were active stocks (``list_status='L'``) that had
  never been delisted, only renamed through ST/*ST cycles. The
  ``SH600753 = 庞大集团`` claim was the most clearly fabricated row:
  600753's namechange history shows the ticker continuously listed since
  1996 under 冰熊股份 → 东方银星 → 庚星股份 → *ST海钦, and was never named
  "庞大集团" (the real 庞大集团 trades under ticker 601258).
- The remaining 3 entries (SH600087, SH600247, SZ000023) ARE delisted but
  had ``delist_date`` wrong by 1.5-5 years compared with Tushare.

The corrected list below contains only the 3 verified delistings with
their true delist_date and post-delisting Tushare display name. See PR
``add-ashare-survivorship-correction`` for the cross-check evidence and
the updated PIT design baseline.
"""

import sys

import pandas as pd
import qlib
from qlib.data import D

# ============================================
# CONFIG
# ============================================
PROVIDER_URI = "D:/qlib_data/my_cn_data"
REGION = "cn"

# Tushare-verified delisted stocks (cross-checked 2026-05-22).
# Each entry: (ticker, delist_date_from_tushare, "display_name_at_delist (era)").
KNOWN_DELISTED = [
    ("SH600087", "2014-06-05", "退市长油 (pre-2020 financial delisting)"),
    ("SH600247", "2021-03-22", "*ST成城退 (2020-2022 *ST → 退市 mainstream era)"),
    ("SZ000023", "2024-09-02", "*ST深天退 (2024+ post-退市新规 strict era)"),
]

TEST_START = "2014-01-01"
TEST_END = "2025-12-31"


def main():
    qlib.init(provider_uri=PROVIDER_URI, region=REGION)

    print("=" * 60)
    print("SURVIVORSHIP BIAS VERIFICATION")
    print("=" * 60)
    print(f"Provider: {PROVIDER_URI}")
    print(f"Test range: {TEST_START} to {TEST_END}")
    print(f"Checking {len(KNOWN_DELISTED)} Tushare-verified delisted stocks")
    print()

    # Bucket semantics:
    #   good          — last data point is within +/- BOUNDARY_DAYS of
    #                   the known delist_date (correct boundary).
    #   bad_extended  — last data point is >= +BOUNDARY_DAYS past delist
    #                   (stale local bin OR mislabelled delisting).
    #   truncated     — last data point is <= -BOUNDARY_DAYS BEFORE the
    #                   known delist date (local bin missing the
    #                   delisting tail; previously misreported as GOOD
    #                   when days_past was a large negative number).
    #   missing       — no data for the ticker at all (survivorship).
    #   error         — query raised.
    BOUNDARY_DAYS = 90
    results = {
        "good": [],
        "bad_extended": [],
        "truncated": [],
        "missing": [],
        "error": [],
    }

    for code, expected_delist_date, reason in KNOWN_DELISTED:
        try:
            data = D.features(
                [code], ["$close"],
                TEST_START, TEST_END
            )

            if data.empty or data["$close"].dropna().empty:
                results["missing"].append((code, reason))
                print(f"MISSING: {code}  ({reason})")
                continue

            valid_data = data["$close"].dropna()
            last_date = valid_data.index.get_level_values("datetime").max()
            expected_dt = pd.Timestamp(expected_delist_date)
            days_past = (last_date - expected_dt).days

            if -BOUNDARY_DAYS <= days_past < BOUNDARY_DAYS:
                results["good"].append((code, last_date, expected_dt))
                print(f"GOOD:  {code}  data ends {last_date.date()}, "
                      f"delisted {expected_dt.date()} ({days_past:+d}d)  "
                      f"[{reason}]")
            elif days_past >= BOUNDARY_DAYS:
                results["bad_extended"].append((code, last_date, expected_dt))
                print(f"BAD:   {code}  data ends {last_date.date()}, "
                      f"delisted {expected_dt.date()} "
                      f"({days_past:+d}d — extended too long!)  "
                      f"[{reason}]")
            else:
                # days_past <= -BOUNDARY_DAYS — data truncated before delist
                results["truncated"].append((code, last_date, expected_dt))
                print(f"TRUNC: {code}  data ends {last_date.date()}, "
                      f"delisted {expected_dt.date()} "
                      f"({days_past:+d}d — data ends BEFORE delisting!)  "
                      f"[{reason}]")

        except Exception as e:
            results["error"].append((code, str(e)))
            print(f"ERROR: {code}  {e}")

    print()
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)

    n_good = len(results["good"])
    n_extended = len(results["bad_extended"])
    n_truncated = len(results["truncated"])
    n_missing = len(results["missing"])
    n_total = len(KNOWN_DELISTED)

    print(f"  Properly delisted:    {n_good}/{n_total}")
    print(f"  Data extended (bad):  {n_extended}/{n_total}")
    print(f"  Data truncated (bad): {n_truncated}/{n_total}")
    print(f"  Missing from dataset: {n_missing}/{n_total}")
    print(f"  Errors:               {len(results['error'])}/{n_total}")
    print()

    if n_extended > n_total / 2:
        print("VERDICT: BAD — data extends past delisting dates.")
        print("   Local bin is stale; see add-ashare-survivorship-correction.")
        return 2
    elif n_truncated > n_total / 2:
        print("VERDICT: BAD — data truncated before delisting dates.")
        print("   Local bin is missing recent history for delisted tickers;")
        print("   rebuild required (do NOT trust GOOD verdict from prior runs)." )
        return 2
    elif n_missing > n_total / 2:
        print("VERDICT: SURVIVORSHIP BIAS — most delisted stocks missing.")
        print("   Local bin lacks delisted-ticker coverage; rebuild required.")
        return 1
    elif n_good == n_total:
        print("VERDICT: GOOD — data correctly handles delisted stocks.")
        return 0
    else:
        print("VERDICT: UNCLEAR — mixed results. Inspect manually.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

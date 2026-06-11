# Tasks: fetch-manifest-truthfulness

## 1. Freshness rule (fetcher)
- [x] `_expected_year_file_end` (module fn): clipped year slice ∩ listing
      window, floored to the last weekday; `None` ⇒ no data can exist.
- [x] `_read_file_max_trade_date`: single-column parquet read; empty /
      missing-column / unreadable ⇒ `None` (⇒ re-pull, which also self-heals
      a corrupt file by overwriting).
- [x] `_load_ticker_windows`: per-ticker `(list_date, delist_date)` from the
      stock_basic parquets; malformed cells degrade to `(None, None)` (more
      re-pulls, never a silent skip).
- [x] Per-ticker loop: exists-skip → freshness decision (force-retry checked
      first and piercing everything); `refresh_year` blind re-pull retired;
      `refresh_current` docstring + 01 `--refresh-current` help + daily_update
      stage notes updated to aggregates-only.
- [x] Scan scope: final year always; past years when `floor(year) >`
      per-endpoint watermark (`assume_verified_through`, wired by the CLI
      from the prior manifest's `coverage_end_date`), when no watermark, or
      under `--verify-all-years`.

## 2. Manifest red line (01 CLI)
- [x] `_invalidate_manifest` removed; merge-refusal / write-OSError / hard
      abort / corrupt-at-start all exit 1 with the manifest untouched and an
      explanatory error naming `--reset-manifest`.
- [x] `--reset-manifest`: the ONLY clear path (explicit fresh start, loud,
      fail-loud on OSError); `--verify-all-years` flag added.

## 3. Merge truthfulness (fetch_manifest)
- [x] Disjoint-range merge refused (`_days_between` gap > 1 day either side);
      adjacent / overlapping merge unchanged.
- [x] `_min_/_max_yyyymmdd` treat "" as "no value"; narrower-scope guard
      compares only ESTABLISHED current coverage.
- [x] Established-nothing endpoint preserves the prior record verbatim
      (holes intact) instead of dropping them as self-healed.

## 4. Tests
- [x] 半截年文件 + 扩 end_date → 补全（整年一次调用，文件补到年末）。
- [x] 已完整边界文件 → 跳过（crash 重跑 resume 价值；含周末 floor 场景）。
- [x] 刷新失败 → 旧文件逐字节保留 + 记洞 + 下轮自动重试（无需额外簿记）。
- [x] “明天再跑”：今天建档明天 end+1 必须抓到新一天；当日重跑跳过。
- [x] 上市窗口边界：退市年文件止于退市日 → 跳过；上市前空占位 → 跳过；
      年中上市后的空占位 → 重抓。
- [x] 水位线：已证年份不再扫描（容忍既有陈旧内容）；`--verify-all-years`
      强制全扫并补齐。
- [x] merge：不相交两侧均拒绝、相邻/重叠正常合并、"" 哨兵不再毒化 min/max、
      未建立覆盖的 run 保留 prev 端点（含洞）原样。
- [x] CLI 红线回归：merge 被拒 / 写失败 / 硬中止（有洞、无洞）/ 启动损坏 →
      exit 1 且 manifest **逐字节不变**；`--reset-manifest` 清除并重建；
      clear 自身 OSError → exit 1。
- [x] 既有测试按新语义校准（夹具补 `trade_date`：complete 文件跳过、
      force-retry 穿透、resume 测试改用有效完整 parquet）。

## 5. Verification
- [x] `python -m unittest tests.data_pipeline.{test_fetcher, test_fetch_manifest,
      test_client, test_daily_update, test_qlib_bin_builder}` — 158 tests green.

# Operations: environment variables

The environment variables an operator sets to run this system on a
non-default layout. Each `QUANT_*` default equals the historical hardcoded
path, so behaviour is unchanged where they are unset.

| Variable | Consumed by | Default | Meaning |
|---|---|---|---|
| `QUANT_PROVIDER_URI` | `scripts/daily_recommend.py`, operator UI (`config.yaml` `${…}` expansion, bundle-health banner, 数据检视, 生产运维 新鲜度卡) | `D:/qlib_data/my_cn_data_pit` | The LIVE qlib provider bundle the system scores from. |
| `QUANT_MODEL_PATH` | `scripts/daily_recommend.py`, operator UI (今日推荐 model-meta banner) | `D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl` | The trained model artifact. |
| `QUANT_ENSEMBLE_MANIFEST` | operator UI ONLY (今日推荐 现任身份横幅 + ensemble 工件交叉核对;生产运维 驾驶舱现任卡与重训窗推导 — 两页共用 `web/operator_ui/incumbent.py` 的同一解析器,不存在第二份实现) | `D:/stock/phase_b_artifacts/csi800_n5_ensemble_manifest.json` | Points the UI at the **incumbent** production ensemble manifest so the page can say WHICH model is serving and whether a shown artifact came from it. Default = the manifest the 2026-08-05 cutover wrote, per this file's "default equals the historical hardcoded path" rule — an UNSET variable means "nobody configured this box", never "production went back to one model", so it must not be read as evidence of a single-model incumbent. A deployment that genuinely serves one model sets `none` (explicit opt-out; the single-model banner then applies). Set but unreadable → the page WARNs and refuses to name any incumbent; it never falls back to the single-model banner. **Read-side only, on purpose**: `scripts/daily_recommend.py --ensemble-manifest` stays explicitly required and MUST NOT inherit this default — the side that PRODUCES a list must never pick its model implicitly (a wrong pick is a wrong order list); the side that only CROSS-CHECKS may. |
| `QUANT_DELISTED_REGISTRY` | `scripts/daily_recommend.py`, `config_walk.yaml` (`delisted_registry_path` — PIT-masked IC/attribution, audit P2) | `D:/qlib_data/tushare_raw/delisted_registry.parquet` | Delisted registry (PIT survivorship layer). |
| `QUANT_NAME_SOURCE` | `scripts/daily_recommend.py`, `src/inference/daily_recommend.py` (`RecommendationConfig.name_source_parquet`) | `D:/qlib_data/tushare_raw/active_stocks.parquet` | Active-stocks snapshot: display names + the current-ST exclusion set (carries the embedded `snapshot_date`, P3-5). |
| `QUANT_NAMECHANGE_PATH` | `config.yaml` / `config_walk.yaml` (`namechange_path` — ST/改名历史), operator UI (生产运维 门命令文本) | `D:/qlib_data/tushare_raw/all_namechanges.parquet` | Name-change history used to mask ST/renamed instruments. **`scripts/retrain_gate.py` does NOT read this variable** — its `--namechange` is an argparse default, and the gate artifact records no data path at all, so a gate run without the flag can PASS on a different bundle than the deployment and later authorize a rotation undetected. The 生产运维 page therefore prints the resolved value into both gate commands explicitly. |
| `QUANT_DECISION_JOURNAL_DIR` | `web/operator_ui/decision_journal.py` (今日推荐 decision journal) | `D:/stock/operator_journal` | Append-only operator decision journal (JSONL). Lives OUTSIDE the repo's disposable `output/` tree; web-layer state — NEVER an input to official metrics / backtests / training (src/ zero-reference is test-enforced). |
| `PV_BASELINE_PREDS` | `config/factor_mining/pv_incremental_v1.yaml` (`data.baseline_preds_path`) | `output/factor_mining/pv_incremental_v1/baseline/baseline_preds.parquet` (repo-relative) | The exported Alpha158+LGB baseline the pv_incremental_v1 GP breeds against. The default IS the exporter's standard out-dir, so ignition edits no tracked file — set this only when the export lives elsewhere. A missing/unbound baseline is a REFUSAL (the orthogonality penalty is the campaign's only incremental criterion), never a silent zero. |
| `TUSHARE_TOKEN` | `src/data/tushare/client.py` (`TushareClient.from_environment`) | — (required for any fetch) | Tushare API token. NEVER goes in a config file — secrets-in-config is prohibited. |

CLI flags always take precedence over the env default
(`--provider-uri` > `QUANT_PROVIDER_URI` > the hardcoded default).

The pipeline scripts (`scripts/data_pipeline/01–06`) and the daily-update
orchestrator (`scripts/daily_update.py`) read NO environment variables for
paths — every path is an explicit CLI argument (P3-6a). The variables above
serve the RECOMMEND side and the UI.

## PowerShell: setting them

```powershell
# Current session only:
$env:QUANT_PROVIDER_URI = "E:/data/my_cn_data_pit"
$env:TUSHARE_TOKEN      = "<your token>"

# Persist for future sessions (user scope):
[Environment]::SetEnvironmentVariable("QUANT_PROVIDER_URI", "E:/data/my_cn_data_pit", "User")
```

## The `${…}` trap

Tracked YAML configs reference env vars as `${QUANT_PROVIDER_URI}` or with a
default, `${QUANT_PROVIDER_URI:-D:/qlib_data/my_cn_data_pit}`. Two things bite
here:

1. **That substitution happens in OUR config loader**
   (`src/core/_yaml_loader.py`), not in the shell. Echoing the YAML or loading
   it with a plain YAML parser shows the literal `${…}` — that is expected. A
   bare `${VAR}` whose variable is missing (and has no `:-default`) fails LOUD
   at load time rather than silently producing the literal string.
2. **PowerShell's own `${…}` is a different thing.** In PowerShell,
   `"${QUANT_PROVIDER_URI}"` interpolates a *PowerShell variable* of that name
   (usually empty), NOT the environment variable — the correct PowerShell
   spelling is `$env:QUANT_PROVIDER_URI`. Don't "test" a config's `${…}`
   reference by pasting it into a PowerShell string: it will look empty even
   when the env var is set.

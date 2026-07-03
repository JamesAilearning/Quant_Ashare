# Operations: environment variables

The five environment variables an operator sets to run this system on a
non-default layout. Each `QUANT_*` default equals the historical hardcoded
path, so behaviour is unchanged where they are unset.

| Variable | Consumed by | Default | Meaning |
|---|---|---|---|
| `QUANT_PROVIDER_URI` | `scripts/daily_recommend.py`, operator UI (`config.yaml` `${…}` expansion, bundle-health banner, 数据检视) | `D:/qlib_data/my_cn_data_pit` | The LIVE qlib provider bundle the system scores from. |
| `QUANT_MODEL_PATH` | `scripts/daily_recommend.py` | `D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl` | The trained model artifact. |
| `QUANT_DELISTED_REGISTRY` | `scripts/daily_recommend.py`, `config_walk.yaml` (`delisted_registry_path` — PIT-masked IC/attribution, audit P2) | `D:/qlib_data/tushare_raw/delisted_registry.parquet` | Delisted registry (PIT survivorship layer). |
| `QUANT_NAME_SOURCE` | `scripts/daily_recommend.py`, `src/inference/daily_recommend.py` (`RecommendationConfig.name_source_parquet`) | `D:/qlib_data/tushare_raw/active_stocks.parquet` | Active-stocks snapshot: display names + the current-ST exclusion set (carries the embedded `snapshot_date`, P3-5). |
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

# 审计重定位底单（audit @ cf9cf41 → main @ acf70e0，2026-06-11）

2026-06-11 全仓审计（8 路并行静态审查 + 人工复核）完成于 `cf9cf41`；随后
`#235`(P3-4c) / `#236`(P3-5) / `#237`(P3-6a) 三个 PR 落地。本文逐条把审计发现
映射到新 main，分两桶：**已关闭**（注明关闭证据）与**依然成立**（行号重定位 +
后续批次归属）。本文是「基线重置批」「元数据闭环批」等后续工作的底单。

行号基于 `acf70e0`。未被 #235-#237 触碰的文件，原审计行号原样有效。

---

## Step 0 例行核证（2026-06-11，只读）

**五件套**（全部在位）：

| 项 | 证据 |
|---|---|
| daily_update.py 在 | `scripts/daily_update.py`（CLI）+ `src/data_pipeline/daily_update.py:247-338`（编排器，exit code 0/2/10-16 分阶段） |
| build 闸读 manifest + `--allow-holey-fetch` | `src/data/pit/qlib_bin_builder.py:178-237`（`build()` 进场即读 `fetch_manifest.json`，洞/缺失/必需端点未确认 → `QlibBinBuilderError`；override 仅接受不完整，不接受损坏）；CLI 旗标 `scripts/data_pipeline/05_build_qlib_bins.py:52-58`；编排器接线 `src/data_pipeline/daily_update.py:176-177` |
| recommend 完整性戳闸 + `--allow-holey-recommend` | 戳写入 `src/data/pit/qlib_bin_builder.py:300-307`（staging 内原子晋级）；戳模块 `src/data/pit/bundle_integrity.py:48-142`；闸 `src/inference/daily_recommend.py:361-410`（`_assert_bundle_fetch_complete`：缺戳/洞戳均拒，损坏戳不可 override）；CLI 旗标 `scripts/daily_recommend.py:98-103` |
| snapshot_date 写 + 守卫 | 写：`src/data/tushare/fetcher.py:321-327`（每行盖 YYYYMMDD，`config.now` 可注入）+ `scripts/data_pipeline/01_fetch_tushare.py:162-169`（`--snapshot-date`）；读/守卫：`src/data/active_stocks_snapshot.py:31-82`（缺列/混值/格式错全 fail-loud）；消费：`src/data_pipeline/daily_update.py:204-244`（快照陈旧 → EXIT 13）、`src/inference/daily_recommend.py:332-358`（快照↔bundle 日历尾一致性） |
| 双段换仓 + 启动态检查 | 换仓 `src/data_pipeline/bundle_swap.py:102-136`（live→.bak、.new→live 两次同卷 rename）；启动修复 `src/data_pipeline/bundle_swap.py:46-99`（四种崩溃残局各有确定性处置）；编排器 Stage 0 调用 `src/data_pipeline/daily_update.py:275-282` |

**首跑专项**：

- (a) **csi300/csi500/csi800.txt 存活 ✅**：编排顺序 02→05→03→04
  （`src/data_pipeline/daily_update.py:303-310`），03/04 的 `--output-dir`
  指向 staging `<provider>.new`（`build_plan` :186-195），即在 05 的
  staging-promote **之后**写入同一 staging，最终整目录 rename 上线。不停轨。
- (b) **2025 边界年完好**：`D:/qlib_data/tushare_raw` 下 daily/adj_factor/
  daily_basic 三端点 ×5 样本票 max(trade_date)=20251231（243 交易日）；
  随机 30 票：29×20251231 + 1 空占位（合法）。dump 以 end=20251231 干净收尾。
- (c) **index_weight 快照至 2025-12-31**（000300/000905/000906）；
  `--refresh-current` 明确豁免 index_weight（`src/data/tushare/fetcher.py:406-409`
  注释），**首跑不会拉新快照**（by design）。注意：CSI 六月调仓（约 2026-06-15
  生效）后成分将漂移，需专门刷新或等 P3-6b 节奏化。
- 附带：`fetch_manifest.json` 当前**缺失**、`active_stocks.parquet` 无
  `snapshot_date` 列（P3-5 前旧文件）——首跑均自愈（manifest 由 01 跑后生成且
  build 闸在同跑 fetch 之后才读；stock_basic 被 refresh-current 强制重拉并盖戳）。

---

## 桶 1：已被 4c/5/6 关闭

| 审计编号 | 原发现 | 关闭证据 |
|---|---|---|
| P0-3 之「洞账本只写不读」 | fetch_manifest 无任何下游消费者 | 4c 两层闸：build 闸 `qlib_bin_builder.py:178-237` + recommend 闸 `daily_recommend.py:361-410`；编排器 fetch exit 3 即停（`daily_update.py:286-293`，EXIT 12） |
| P1「05 原子交换吞掉 03 的指数成分文件」 | 05 staging-promote 摧毁 csi*.txt，runbook 顺序必踩 | P3-6a 编排顺序 02→05→03→04 全部写入 staging（见首跑 (a)）。**残留**：手工按旧 runbook 直接对 live 目录跑 05 仍会吞（文档级风险，归「元数据闭环批」顺手改 runbook） |
| P1「.bak→staging 第二次 rename 失败窗口 / 异常分支删新构建」 | 05 直接对 live 目录换名的窗口 | 编排路径下 05 的输出是 staging（非 live），live 仅经 `bundle_swap.swap` 两段 rename + `check_and_repair` 启动修复（`bundle_swap.py:46-136`）。**残留**：05 直跑 live 的遗留路径仍有旧窗口（同上归档处理） |
| P1「ST 快照陈旧检查用 mtime（弱代理）」（审计 P2 群）| mtime 可被同步工具刷新 | P3-5 嵌入式 snapshot_date：`active_stocks_snapshot.py` + `daily_recommend.py:465-486` 改读嵌入列，pre-P3-5 文件 fail-loud |
| P2「bundle 新鲜度/一致性无凭据」部分 | — | recommend 侧新增快照↔bundle 日历尾一致性守卫 `daily_recommend.py:332-358` |
| P1「边界年冻结」之**日更路径** | resume 按文件存在跳过，当年文件永不回补 | `--refresh-current` 盲拉最终年 `fetcher.py:546-570` + 洞强制重试 `force_retry_units`（`fetcher.py:285-291`、`01_fetch_tushare.py:190-216`）。**残留**（→PR-B）：非最终年的历史截断、手工加宽 end_date、盲拉最终年的浪费 |

## 桶 2：依然成立（行号重定位）

### A. 基线重置批（回测语义，成对修 + 基线重置一次）

| # | 发现 | 位置（acf70e0） | 严重度 |
|---|---|---|---|
| A1 | ~~执行时点双重位移：`_apply_lag(lag=1)` 与 qlib `TopkDropoutStrategy` 内部 shift=1 叠加 → 实际 T+2 成交；掩码按 T+1 过滤错位一天~~ **已修（PR-C / openspec 2026-06-12-fix-execution-timing-t1）**：lag 重映射为总延迟（lag=1 → 无外部 restamp），掩码按真实成交日键控，语义版本号进 provenance/resume 指纹；Step-0 复现还揭示旧 restamp 令每折最后一天信号整体蒸发 | `src/core/backtest_runner.py`（lag 映射 + 掩码键控）；探针 `tests/logic/test_backtest_execution_timing.py` | ~~P0~~ 关闭 |
| A2 | ~~涨跌停约束对生产 bundle 失效：float 模式依赖 `$change` bin，builder 明确不产，qlib 缺字段 NaN 比较 → 限价检查静默失效~~ **已修（PR-D / openspec 2026-06-12-price-limit-expression-mode）**：runner 把合同 float 幅度统一翻译为 qlib 表达式元组（`$close/Ref($close,1)-1 > / < ±thr`，不依赖 $change），float 模式永不触达 qlib；$factor 缺失→整手失效响亮警告；探针复现：未修复代码涨停日照买/跌停日照卖 | `src/core/backtest_runner.py`（表达式翻译 + 预检）；探针 `tests/logic/test_backtest_execution_timing.py`；运行时钉 `test_limit_threshold_reaches_qlib_as_expression_tuple` | ~~P0~~ 关闭 |
| A3 | ~~`mean_ic_1d` 口径 T→T+1，与标签（T+1→T+2）和执行口径双双不齐~~ **已修（PR-C）**：头条 IC 标签对齐（entry_offset=1），旧口径降级为标注的 `mean_ic_stamp_day` | `src/core/signal_analyzer.py`（`_compute_daily_ic` entry_offset） | ~~P1~~ 关闭 |
| A4 | 单一全局 limit_threshold 无法表达多板制度（修 A2 时用表达式元组顺带解决） | `src/core/canonical_backtest_contract.py:609-654` | P2 |

### B. PR-A（本批，错误分类）

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| B1 | client 统一注入 "rate limit/network" 文案 → fetcher 子串判定永远「可重试」→ token/权限/参数错不再快速中止（4a 设计的 abort 路径不可达），全市场跑出数万洞 | `src/data/tushare/client.py:131-148`；`src/data/tushare/fetcher.py:723-790`（`_is_retryable_error`）、`:617-684`（`_safe_call`） | P1 |
| B2 | 现有测试用裸消息构造异常，测不出包装污染（盲区） | `tests/data_pipeline/test_fetcher.py`（直接构造 `TushareClientError`） | P2 |

### C. PR-B（本批，manifest 如实性）

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| C1 | 边界年冻结（通用腿）：(ticker,year) 文件存在即跳过，半截年文件永不回补；manifest 仍把 coverage 记到新末日 → 「complete 但文件停在旧日期」 | `src/data/tushare/fetcher.py:557-563`（`refresh_year` 仅盲拉最终年）；`src/data/tushare/fetch_manifest.py:124-139`（established 语义） | P0 残留 |
| C2 | merge 被拒/写失败/启动损坏 → CLI 自动 `clear_manifest`，洞账本被删（fail-loud → fail-forget） | `scripts/data_pipeline/01_fetch_tushare.py:197-202`、`:244-261`、`:291-306`（`_invalidate_manifest` :100-115） | P1 |
| C3 | 不相交范围 union 虚构覆盖（prev[2000-2010]+cur[2020-2025] → "complete 2000-2025"，9 年缺口无洞） | `src/data/tushare/fetch_manifest.py:192-198` | P2 |
| C4 | 空串「未建立」哨兵进 `_min_yyyymmdd` 字典序永远获胜 → coverage_start 卡空 | `src/data/tushare/fetch_manifest.py:286-297`（prev 侧仍可达） | P2 |

### D. 元数据闭环批（候选下一批）

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| D1 | `bundle_manifest.json` 无人再写：`save_manifest` 零生产调用 → `read_bundle_tag` 恒 "unknown" → **特征缓存跨 bundle 重建仍命中旧数据**（含为修数据而重建）；`run_walk_forward` bundle 校验 no-op；UI 健康横幅读的文件名无人产 | `src/data/bundle_manifest.py:388`；`src/data/_feature_dataset_cache.py:55-95`；`web/operator_ui/training_guards.py:122-131` | P1 |
| D2 | resume 指纹不含 bundle 身份 → 跨数据版次续跑混折 | `src/core/walk_forward/_resume.py:116-153` | P2 |
| D3 | 日历为厂商日期并集，无交易所参照交叉验证 | `src/data/pit/qlib_bin_builder.py:610-618`（原 :520-528） | P3 |
| D4 | instruments 写入端静默丢弃畸形 list_date 行、无 active∩delisted 重叠检查 | `src/data/pit/qlib_bin_builder.py:633-651` | P2 |
| D5 | index_membership：缺月快照虚构进出 + 最新快照开口到 2099 无陈旧断言（首跑 (c) 证实现实影响：快照停在 2025-12，六月调仓后漂移） | `src/data/pit/index_membership.py:234-263` | P2 |
| D6 | 三个 artifact 发布器 csv+manifest 非原子写 | `universe_artifact_publisher.py:269,284`；`benchmark_artifact_publisher.py:319,327`；`taxonomy_artifact_publisher.py:238,246` | P2 |
| D7 | 05 直跑 live 的遗留路径仍有旧交换窗口 + 无构建锁；migration_guide 旧 runbook 顺序未更新 | `src/data/pit/qlib_bin_builder.py:283-315`；`docs/pit/migration_guide.md:87-100` | P2 |

### E. 宇宙/口径一致性批

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| E1 | ST 过滤三路不一致：canonical（config.yaml 无 namechange_path，WARN 即过）/walk-forward（开）/实盘（硬性）→ 单折指标含 ST、两种回测不可比 | `src/core/backtest_runner.py:336-341`；`config.yaml` | P1 |
| E2 | ~~基准疑似价格指数 vs 含分红策略收益 → 超额年化虚高 ~2-2.5%~~ **已坐实并修机制（PR-E / openspec 2026-06-12-benchmark-total-return）**：tushare `000300.SH` 2025-12-31 收 4629.9395 与 bundle `sh000300` **逐位吻合**→确为价格指数；全收益 `H00300.CSI` 同期 6826.62（比值 1.474，累计股息拖累 ~47%）。新 `benchmark_index_ingest` + `07_ingest_benchmark` 把价格+全收益指数作为**构建期 staging 产物**摄入（被原子交换保住，修 xlsx-写-live-被抹病灶）；退役 `ingest_sh000300_benchmark`。默认 benchmark_code 暂留 SH000300，翻转 `SH000300TR`+重抓+重基线归 REGEN（预期超额年化下修 ~2-2.5pp）。真数据端到端验证通过 | `src/data/pit/benchmark_index_ingest.py`；`scripts/data_pipeline/07_ingest_benchmark.py` | ~~P1~~ 机制就位（REGEN 激活）|
| E3 | 行业分类今日快照套历史（归因层） | `src/data/tushare/industry_publisher.py:38-47` | P2 |

### F. 因子挖掘协议批（GP 重启前置）

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| F1 | GP 搜索与「OOS 验证」同窗自证；门 abs() 无符号一致性、无多重检验、OOS 可无限复用（D6 已搁置 GP → 潜伏） | `src/factor_mining/miner.py:245-248`；`promote.py:151-158`；`validator.py:77-133,219-222` | P0 潜伏 |
| F2 | 挖掘因子列名 = 进程加盐 hash → 跨进程改名 + 特征缓存错位风险 | `src/data/mined_factor_handler.py:119-121`；`src/factor_mining/expression.py:134-135,211-219` | P1 |
| F3 | walk-forward 不查矿池挖掘窗与测试折重叠；embargo 验证器对非 Alpha158 handler 豁免 | `src/core/walk_forward/engine.py:537-549`；`src/data/feature_dataset_builder.py:548-549` | P1 |

### G. UI 批（Windows 实际平台）

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| G1 | widget 实例化后写其 session_state key → 点击即 StreamlitAPIException：快捷日期 ×5、筛选清除、批量清理（删除已执行后才崩）、GPU 一键修复 | `web/operator_ui/pages/jobs.py:228-234 vs :200-205`、`:678 vs :658`；`pages/config_run.py:593-602 vs :453` | P1 |
| G2 | Windows 无 CREATE_NEW_PROCESS_GROUP → Ctrl+C 关 UI 连带杀训练任务，job.json 永久 "running"；Stop 按 pid 强杀无身份校验 | `web/operator_ui/job_manager.py:132-145`、`:175-228`、`:260-271` | P1 |
| G3 | CI 不装 `[ui]` → 全部 UI 测试静默 skip；ruff 不查 web/ | `.github/workflows/test.yml:39,43` | P2 |
| G4 | 其余 UI P2/P3（preset 覆盖内置、walk_forward 重跑丢日期、UTC 裸显、全量 re-zip、整库扫描、CLI 行死链等）| 原审计行号原样有效（web/ 未被 #235-237 触及，仅新增 data_inspect.py） | P2/P3 |

### H. 测试/契约治理批

| # | 发现 | 位置 | 严重度 |
|---|---|---|---|
| H1 | E2E 防线漏洞：`pytest.mark.skipif` 装饰 unittest.TestCase + 文件带 `unittest.main()` → 经 unittest 跑裸执行真实 bundle 重测试 | `tests/logic/inference/test_daily_recommend.py:50,565-566,724`（原 :507 漂移） | P1 |
| H2 | 契约层 3/5 孤儿（run_artifact/benchmark/universe 零生产调用；仅 taxonomy 接线）；全家族无值级校验 | `src/contracts/*`（原审计行号有效） | P1 |
| H3 | fold0 回归基线休眠（fixtures 缺失永远 skip）+ 容差由 fixture 自带 | `tests/regression/test_fold0_baseline.py:42,64,121` | P2 |
| H4 | qlib-mock 反模式残留、RUN_E2E 解析两处不一致等 | 原审计行号有效 | P2/P3 |

### I. 文档/卫生（散件）

- CLAUDE.md：`src/data_pipeline/` 描述与现实不符（现为 daily_update/bundle_swap，发布器在 `src/data/`）；环境变量应为 `QUANT_PROVIDER_URI`（文档误写 `QLIB_PROVIDER_URI`）；D5 措辞与「仅禁顶层导入」的实际门不符。
- `.gitignore` 根级缺 `.env` / `my_*.yaml`；`ruamel.yaml` 死依赖、`lightgbm` 未声明；`scripts/ingest_sh000300_benchmark.py:68` 与 `scripts/data_quality/verify_survivorship.py:41` 裸硬编码 `D:/qlib_data`（且指向旧非 PIT bundle）。
- `01_fetch_tushare.py:134` `--end-date` 默认仍是写死的 `20251231`（手工裸跑的脚枪；编排器显式传参不受影响）。
- openspec/changes 下大量已交付未归档提案（含 4c/5/6 自己的三个）。
- `daily_recommend._load_model`（`src/inference/daily_recommend.py:489`）仍不验已有的 `.meta.json` sha256 sidecar。

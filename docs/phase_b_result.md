# 阶段 B 结果报告 — 补环 5：日频荐股推理

> **worktree**：`D:/stock/worktrees/Quant_Ashare_phase_b`（分支 `phase-b/daily-recommend-20260529`，从 `origin/main @ 1c03cad` 切出）
> **模式**：只新增"荐股推理"代码；不碰 WF embargo bug、不碰 D3 死键、不回填 decisions.md、不重构其他模块。
> **状态**：Step 0（提案）✅ ｜ Step 1（模型）✅ ｜ Step 2（脚本）✅ ｜ Step 3（前视测试+验证）✅。未 commit/push。

系统第一次能端到端产出"今日该买哪些股票"的名单：clean PIT 数据 → 已训练 Alpha158+LGB 模型 → as-of-T 截面 → 预测 → 可交易性过滤 → Top-K 名单（T+1 进场）。

---

## 1. 模型来源

| 项 | 值 |
|---|---|
| 路径 | `D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl`（+ `.meta.json` + ModelTrainer sidecar）|
| 类型 | `qlib.contrib.model.gbdt.LGBModel`（pickle）|
| 特征集 | **Alpha158**（158 列）|
| label 定义 | `Ref($close,-2)/Ref($close,-1)-1`（T+1→T+2 收益）→ 打分 = T+1 进场信号 |
| 数据源 | PIT `D:/qlib_data/my_cn_data_pit`（后复权 bins）|
| universe | csi300 |
| 训练窗 | train **2018-01-02→2023-12-20** / valid 2024-01-02→2024-12-18（embargo-safe）|
| 推理 fit 窗（归一化）| `fit_start=2018-01-02, fit_end=2023-12-20`（= 训练期）|
| 模型参数 | LGBModel，**GPU**，lr 0.0421 / leaves 210 / depth 8 / 1000 轮 early-stop 50 |

复用 Phase A 单折 baseline 同窗口同参数（已刻画 OOS：RankIC(1d) 0.0205、含成本 IR 0.363）。磁盘上既有的 model.pkl 全部 predate Phase A、训练于脏 legacy provider，**不可复用**，故新训一个。

---

## 2. 脚本设计（src/inference 包 + scripts 薄 CLI）

```
src/inference/__init__.py
src/inference/daily_recommend.py     # 核心，可测
scripts/daily_recommend.py           # 薄 CLI（__main__ + freeze_support）
tests/logic/inference/test_daily_recommend.py
```

**核心函数（`src/inference/daily_recommend.py`）**：
- `resolve_dates(as_of_date, calendar=None) -> (T, T+1)` —— 纯逻辑（可注入 calendar 测试）；默认 T=**日历中最后一个仍有后继交易日的日期**（即数据截止日的前一交易日，使无参 CLI 可用、T+1 存在）；显式传入最后一天（无 T+1）仍**报错**。
- `prepare_asof_features(config, T) -> DataFrame` —— Alpha158 `end_time=T`、`fit_end=训练期`、`DK_I` + `col_set="feature"`。
- `assert_no_lookahead(frame, T)` —— 纯，断言 `max(datetime) <= T`，否则抛错。
- `build_recommendation(...)` —— 纯，排序/Top-K/可交易性/reason 标注。
- `recommend(config) -> DailyRecommendationResult` —— 编排：init qlib → resolve → 加载模型 → as-of 特征 → predict → dropna(**score**) → `compute_unavailable_mask` 过滤 → `build_recommendation`。
- `write_outputs(result, out_dir)` —— csv + json + 全量审计 csv（utf-8-sig）。

**复用（import，未改动）**：`compute_unavailable_mask`（停牌/一字板，权威 untradable 集）、`PITDataProvider`、qlib `Alpha158`/`DatasetH`、canonical qlib runtime。

**输出字段**：`as_of_date`(T 数据截止) + `entry_date`(T+1 进场) + `rank` + `stock_code` + `stock_name`(best-effort 当前名) + `predicted_score` + `tradable_flag` + `unavailable_reason`。两个时点在 csv/json/终端三处都明确。

---

## 3. 前视偏差防护（红线）—— 5 条合约 + 测试覆盖

| # | 合约 | 落地 | 测试 |
|---|---|---|---|
| 1 | 特征只用 ≤T 数据 | Alpha158 `end_time=T` → qlib 不加载 >T bar | E2E `test_asof_frame_has_no_future_rows` + 运行时 `assert_no_lookahead` |
| 2 | 归一化统计量来自训练期、不偷看 T 之后 | `fit_end_time=训练期 fit_end` | **E2E `test_normalization_does_not_peek_at_future`（强化版红线）** |
| 3 | label 不参与 | 只取 `col_set="feature"` + `DK_I`（`DropnaLabel` 是 LEARN 处理器，不删最新日行）| 最新日实跑非空（见 §5）+ score-only dropna |
| 4 | 运行时兜底 | `recommend` 内 `assert_no_lookahead` 拒绝出名单 | 单元 `AssertNoLookaheadTests`（max==T 通过 / >T 抛错 / 空抛错）|
| 5 | T+1 不可知风险诚实标注 | 可交易性用 T 日微结构；T+1 开盘能否成交决策时点不可知 | 文档化（非 bug，见 §7 TODO）|

**强化版红线测试（用户特别要求）实测通过**：对同一个 T=2025-06-30，分别用 `end_time=T`（无未来数据）和 `end_time=T+5`（含未来数据）构造 Alpha158 INFER 特征，断言 T 行的归一化特征值**逐元素相等**（NaN 位置一致 + 有限值 `rtol=0,atol=0` 全等）。`2 passed in 164.69s`（`RUN_E2E=1`）。这证明归一化没有偷看 T 之后的分布。

**测试分两层（遵循 AGENTS.md "E2E + 合成单元孪生"，与本仓 qlib-feature 测试一致地 RUN_E2E 门控真实 qlib）**：
- **always-on 单元孪生（无 qlib/无 bundle，CI 常跑）**：`resolve_dates`（5 例）、`assert_no_lookahead`（3 例）、`build_recommendation`（排序/Top-K/mask/reason/稳定排序，3 例）。
- **RUN_E2E 真 bundle 测试**：上面两条前视红线，跑真实 PIT bundle。

---

## 4. 测试结果

- `pytest tests/logic/inference/`（默认，不含 E2E）：**10 passed, 2 skipped**（2 skipped = RUN_E2E 门控的真 bundle 测试）。
- `RUN_E2E=1 pytest ...RealBundleLookaheadTests`：**2 passed**（164s）—— 前视红线（含强化版归一化不偷看）通过。
- 全量回归 `pytest tests/logic tests/governance`：见报告末尾汇总（确认未污染既有套件）。

---

## 5. 验证

### 历史日验证（2025-06-30）+ 人工 spot-check
`python scripts/daily_recommend.py --as-of 2025-06-30 --topk 30`：
- as_of=2025-06-30，entry=2025-07-01，scored=300，**masked=0**，buy-list=30。
- **独立核对**（直接查 PIT bundle OHLCV）：前 5 名 SH600415/SH600000/SH600104/SH600941/SH601012 当日 **volume 巨大、high≠low → 全部真实可交易**；整个 csi300 当日 **suspended=0、one_price_lock=0**，故 recommender 报 masked=0 **正确**，无"当日停牌却被推荐"破绽。

### 最新可操作日（2025-12-30，entry 2025-12-31）Top-10 样例
> 名称存储为正确 UTF-8（csv 用 utf-8-sig）；以下示代码+分值，名称见 csv。

```
 1  SZ300014  0.02442
 2  SZ000063  0.01342   (中兴通讯)
 3  SZ000408  0.00398
 4  SH600183  0.00398
 5  SH600875  0.00398
 6  SZ300502  0.00398
 7  SH600438  0.00215
 8  SH600938  0.00169
 9  SH600036  0.00169   (招商银行)
10  SH600066  0.00169
```
- as_of=2025-12-30，entry=2025-12-31，scored=287，**masked=2**（SZ002049、SH688012 当日停牌，score 本可进榜但被正确剔除），buy-list=50（**非空 ✓**，NaN-label 陷阱已规避）。

---

## 6. OpenSpec change 状态

- `openspec/changes/add-daily-stock-recommendation/`：proposal.md / design.md / tasks.md / specs/v2-daily-stock-recommendation/spec.md，`openspec validate --strict` **通过**。
- 实现 + 测试已完成（等同 apply 完成）。**建议**：保持 proposed/applied 状态，**archive 留到合并 PR 后**（README 规定"Archive only after validation [merge]"）。本阶段不 archive。

---

## 7. 已知 TODO / 记账项

| 项 | 性质 | 备注 |
|---|---|---|
| **ST 过滤未做** | 本阶段 TODO | ST 标记不在 PIT bins；`name` 里能看出 ST 前缀但未做硬过滤。需单独接 daily_basic/stock_basic 的 ST 标志。 |
| **best_iter=1 模型质量** | 记账（Phase A 决策沿用）| lr 0.0421/leaves 210 → LGB 第 1 轮即 early-stop，top-K 只有 ~7 个不同分值、并列多、榜内名次部分任意。可换 `config_walk.yaml` 正则化参数（lr 0.005/leaves 64/lambda_l2=1）重训改善。**未动**。 |
| **Phase A `pre_adjusted` 标签误标** | 记账（Phase A 发现）| `data_adjust_mode` 不传给 `qlib.init`、只是溯源标签、不改值；PIT bins 实为后复权。本阶段推理统一标 `post_adjusted`（匹配真实 bins + PITDataProvider）。Phase A 训练标签 pre 对数值无影响。**未动 Phase A。** |
| **WF embargo P0 回归** | 记账（Phase A 发现）| walk-forward baseline 在 main 上整折跑不起来，需改 src/，**留下一阶段**。 |
| **T+1 开盘不可成交不可知** | 固有属性 | 收盘后荐股器无法在 T 预知 T+1 涨停封板等；可交易性用 T 日微结构作最佳近似。 |
| **单一信号源** | 范围限定 | 仅 Alpha158+LGB；GP 未验证，明确 out of scope。 |
| **不下单/不定仓位** | 范围限定 | 输出名单，非订单。 |

---

## 8. 观察

环 5 从"不存在"变成"端到端能产出名单"。链路首次打通：tushare → PIT(净) → ML 训练 → **日频荐股**。前视偏差这关用"运行时兜底 + always-on 单元孪生 + RUN_E2E 真 bundle 强化测试"三层守住，强化版归一化不偷看测试实测通过。当前名单的**实用价值受限于 best_iter=1 模型**（分值并列严重），但这是已记账的模型质量问题，不是推理链路问题——换更深的模型即可改善，推理脚本无需改动。

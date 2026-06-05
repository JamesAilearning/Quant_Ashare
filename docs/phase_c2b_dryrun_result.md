# Phase C2-b Step 2 — GP vs Alpha158 公平 OOS 对比(DRY RUN)

**目的**:用 smoke 已挖的 2001 因子池(`c2b_smoke_2018_2021`,挖矿期
2018-2021)做一次公平 OOS 对比的 DRY RUN,方向性回答「GP 在干净同窗口
OOS 下打不打得过 Alpha158」。**不重新挖矿、不烧 25k。结论属初步。**

**状态:已闭环(2026-06-05)。** 方案经确认 → Step 1-3 全部跑完 → 裁决见
§3。**结论:GP 封存,不进生产荐股路径**(决策固化为 `decisions.md` D6)。
本文档是该决策的证据记录;下文各节按 dry-run 推进顺序写成。实验在 worktree
`factor-mining/c2b-fair-compare` 上完成。

---

## 1. 接入机制确认 + 两条线吃 PIT 确认(Step 0,只查)

### 接入机制:现成,无需写代码

`scripts/run_walk_forward.py` 已内建 MinedFactor 接入(`_maybe_build_mined_factor_bundle`,L149-195):当 YAML 设 `feature_handler: "MinedFactor"` 时,它从四个 `mined_factor_*` 键自动构建 `MinedFactorBundle` 并 `register_mined_factor_handler`。所以:

- GP 这条线 = 写一个 `config_walk_mined_oos.yaml`(指向冻结的 top-50 池
  + PIT delisted registry)→ `python scripts/run_walk_forward.py <cfg>`。
- **不需要任何接入代码,不需要 OpenSpec。** handler 评估的是**冻结表达式**
  (不重挖),满足「只消费现有池」铁律。

### 两条线吃 PIT(实测 PIT 日历切片)

`config_walk.yaml` 顶层 `provider_uri: "D:/qlib_data/my_cn_data_pit"`,
两条 OOS config 都 `extends` 它;MinedFactor 的 `mined_factor_pit_provider_uri`
留空 → 默认 = 顶层 provider_uri(PIT)。实测 PIT bundle 日历切片:

| 区间 | PIT 交易日 | 首 / 末 |
|---|---|---|
| 挖矿 2018-2021 | 973 | 2018-01-02 / 2021-12-31 |
| **GAP 2022** | **242** | 2022-01-04 / 2022-12-30 |
| OOS 2023-2025 | 727 | 2023-01-03 / 2025-12-31 |

→ **两条线都吃 PIT**(日历来自 `my_cn_data_pit`,非脏 `my_cn_data`);
**挖矿↔OOS 之间隔了整整 242 个交易日(全 2022)**,GP 选因子(≤2021)
物理上看不到 OOS 测试期(≥2023)。红线 1、2 满足。

---

## 2. 对比方案(DRY RUN)

### 唯一变量 = 特征来源

GP 和 Alpha158 走**同一** WalkForwardEngine(#212 之后)+ backtest_runner
+ 同一 LGB 配置 + 同 PIT + 同 #179 风险约束 + #181 微结构 mask。两条 config
都 `extends config_walk.yaml`,只改 `overall_start` + `feature_handler`
(+ GP 的池路径)。**唯一变量 = feature handler。**

### top-50 选取(守公平:只用 IS 信息)

- 加载 `c2b_smoke_2018_2021` 池(2001 因子),按**挖矿期 IS fitness**
  取 **top 50**(`FactorPool.top_k(50, by="fitness")`),存到一个冻结目录
  (如 `output/mined_factors/c2b_top50_frozen/`,gitignored)。
- 这是纯**数据操作**(消费+截断+另存),不改 GP 算法/grammar/参数。
- top-50 只用 2018-2021 的 IS fitness 排序 → **不含任何 OOS 信息**,公平。
- top-50 现状:fitness [-0.0915..-0.0244],\|rank_ic\| 最高仅 **0.0103**
  (弱 —— 小预算池的预期,见 §4 caveat)。

### OOS 折范围(2023-2025,2022 作 gap)

`overall_start: 2020-10-01`,`overall_end: 2025-12-31`,沿用 `config_walk.yaml`
的 24m train + 3m valid + 3m test、step 3m。首个 test = start + 27m =
2023-Q1。**12 个 OOS 折**:

| 折 | train(24m) | valid(3m) | test(3m) |
|---|---|---|---|
| 1 | 2020-10~2022-09 | 2022-10~12 | **2023-01~03** |
| … | … | … | 每 3m 推进 |
| 12 | 2023-07~2025-06 | 2025-07~09 | **2025-10~12** |

- 全部 test ∈ [2023-Q1, 2025-Q4],**与挖矿期 2018-2021 之间隔着整个 2022**。
- **2022 的角色(已采用)**:2022 是 **GP 挖矿↔OOS 测试** 的隔离带
  —— GP 选因子时(≤2021)没碰过它。但早期折的 **LGB 会把 2022 用作
  train/valid 数据**(滚动 24m 窗口)。这**不是泄露**:GP 因子已冻结(选自
  2018-2021),2022 既非 GP 选择期、也非 OOS 测试期;LGB 在 2022 上学权重、
  在 2023+ 上测试是标准 WF。要让 2022 连 LGB 都完全不碰,就得 train 起点
  ≥2023 → 只剩 ~1 折,不可行。**所以方案是「2022 不入 OOS 测试、不入 GP
  选择,但可入 LGB 训练」。(更严格的「2022 完全不碰」会把折数压到 ~1、
  不可行,故未采用。)**
- 折内 train→valid→test 的 label 前视由 #212 的 embargo gap 自动覆盖
  (handler 无关,两条线都有)。
- ⚠️ 末折 test 末日 = 2025-12-31(PIT 末日),可能触发 #213 的「末日无
  T+1 bar」—— 全期 baseline 同样末折,引擎应已处理(或自动回拉);Step 1
  跑时确认,必要时末折回拉一季。

### 两条线的精确配置(本次实际所用)

**GP**(`config_walk_mined_oos.yaml`):
```yaml
extends: config_walk.yaml
overall_start: "2020-10-01"
feature_handler: "MinedFactor"
mined_factor_pool_dir: "output/mined_factors/c2b_top50_frozen"
mined_factor_delisted_registry_path: "D:/qlib_data/tushare_raw/delisted_registry.parquet"
output_dir: "output/walk_forward_mined_oos"
```

**Alpha158**(`config_walk_oos.yaml`,同窗口重算):
```yaml
extends: config_walk.yaml
overall_start: "2020-10-01"
output_dir: "output/walk_forward_oos"
```

### Alpha158 同窗口重算(红线 3)

跑 `config_walk_oos.yaml` → Alpha158 在**同一 12 折**(2023-2025)的 OOS IR。
**绝不复用全期 0.301**(那是全 ~22 折,非同窗口)。两份 report 覆盖同一批
折 → `scripts/compare_factor_handlers.py` 直接出 per-metric diff。

### 命令序列(Step 1-3)
```sh
# Step 1：冻结 top-50 + GP OOS
python <truncate top-50 script>          # 数据操作:2001 池 → top-50 冻结目录
python scripts/run_walk_forward.py config_walk_mined_oos.yaml
# Step 2：Alpha158 同窗口
python scripts/run_walk_forward.py config_walk_oos.yaml
# Step 3：对比
python scripts/compare_factor_handlers.py \
    output/walk_forward_oos/walk_forward_report.json \
    output/walk_forward_mined_oos/walk_forward_report.json \
    --out output/walk_forward_compare_oos/compare.json
```

---

## 3. Step 1-3 执行 + 同窗口对比表 + 裁决

### 执行记录
- **top-50 冻结**:从 2001 因子按 IS fitness 取 top-50 →
  `output/mined_factors/c2b_top50_frozen/`(仅用 IS 信息,公平)。
- **撞到并修了一个集成 bug**:`config_walk.yaml` 的 `adjust_mode` 默认
  `pre_adjusted`,但 `PITDataProvider`(MinedFactor handler 重估因子所用)
  硬编码 `post_adjusted` → 单 canonical qlib runtime 容不下两种 →
  MinedFactor 首跑 12 折全挂(`QlibRuntimeInitError`,Alpha158 不碰 PIT
  provider 故无事)。修法:两条 OOS config 都加 `adjust_mode: "post_adjusted"`
  (GP 因子本就在 POST 上挖,一致;两条线同设,仍只差 feature_handler)。
  ⚠️ **潜在 bug**:仓库现成的 `config_walk_mined.yaml` 同样缺此设置 →
  任何人按文档跑 MinedFactor WF 都会撞同一冲突,值得单独修。
- **同窗口**:两条线均 `overall_start 2020-10` + POST + 同 LGB/风险/mask +
  同 PIT。12 折中 **Fold 11(test 2025-Q4)两条线都掉**(末日 2025-12-31
  无 T+1 bar,#213)→ **同 11 个有效折**(test 2023-Q1..2025-Q3),
  apples-to-apples。

### 对比表(同 11 个 OOS 折,2023-Q1 .. 2025-Q3,post_adjusted,同 pipeline)

| 指标 | Alpha158 | GP top-50 | 谁赢 |
|---|---|---|---|
| mean_information_ratio | **+0.188** | **−0.0996** | Alpha158 |
| mean_ic_1d | **+0.0355**(稳定正) | **+0.0004**(≈0) | Alpha158 |
| mean_annualized_return | **+3.44%** | **−0.85%**(亏) | Alpha158 |
| worst_drawdown | −12.05% | **−5.16%** | GP(见下) |
| design_doc IR 阈值(GP ≥ 1.10×Alpha158) | — | **FAIL** | — |

candidate_better=1(仅回撤),baseline_better=3,IR rel_delta = **−152.9%**。

### 裁决:GP top-50 在公平同窗口 OOS 下【明显输】Alpha158

- Alpha158:IC ~3.5%(稳定正)、IR +0.19、年化 +3.4%。
- GP:IC ~**0.04%**(逐折 +0.014/−0.017/+0.018/… 正负乱跳,均值≈0 →
  **无 OOS 预测力**)、IR **−0.10**、年化 **−0.85%**。
- GP 唯一"赢"的回撤,是因为信号≈噪声 → 选股近随机/仓位弱 → 回撤小但也
  不赚钱,**不是优势**。
- → 按判据「明显输 → 可封存 GP」:**这一池(小预算)GP 没有 OOS edge。**

⚠️ **更深的信号**:GP 因子**在样本内就已很弱**(top-50 IS \|rankic\| ≤ 0.011、
fitness 全负),OOS IC 直接塌到 ≈0。这不只是"OOS 衰减",而是**当前 GP
配置(grammar / fitness / 算子)连样本内都挖不出强信号** —— 这比"预算不足"
更值得警惕:单纯把 200×20 加到 25k(~1.5-2 天)**未必能解决**,可能需要先
重新审视 GP 的 grammar/fitness 设计。是否仍要上 25k,由你定。

---

## 4. ⚠️ DRY RUN 三条限定(裁决必须据此解读)

1. **小预算池**:因子来自 200×20 smoke(4000 evals),**非 25k 全量**。
   top-50 的 IS \|rank_ic\| 最高仅 0.0103,本就弱。
2. **单次冻结挖矿**:2018-2021 挖一次冻结,**非逐折重挖**(方案 B,更严谨
   但贵)。因子集静态,不随 OOS 时间自适应。
3. **单一 OOS 窗口**:仅 2023-2025 共 **12 折**,少于全期 ~22 折;且
   2023-2025 是 A 股较弱的一段,绝对数可能偏低。

**裁决解读**:明显输 → 可封存 GP(连小池都不行,真挖矿性价比低);
接近 / 赢 → 才值得上真正的 25k 挖矿。**这不是最终裁决,是方向性信号。**

---

## 结论与落地
- Step 1-3 全部完成(见 §3):GP top-50 公平同窗口 OOS **明显输** Alpha158。
- 决策:**GP 封存,不进生产荐股路径**,固化为 `decisions.md` D6。
- coverage 修复(让 GP 挖矿在 PIT 上 coverage 正确)已随 #217 合入 main;
  GP 子系统保留在树中(正确、有测试),仅不进生产。
- 旧 `empirical_results_b_std.md` 的「GP 跑输」结论基于污染对比,已作废;
  以本文档的干净裁决为准(见该文件顶部 SUPERSEDED 注记)。

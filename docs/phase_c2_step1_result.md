# Phase C2 Step 1 + Step 2 结果报告

**范围**：C2-a 修 `config/factor_mining/default.yaml` 死键 + 回填
`decisions.md` D3（Step 1，可写）；GP 线 label 前视防护只读调查
（Step 2，零代码改动）。

**worktree**：`D:/stock/worktrees/Quant_Ashare_phase_c2`
分支 `fix/factor-mining-default-deadkey`（基于 origin/main，含 #212 的
walk-forward embargo gap 修复）。

**未提交**：本报告 + 以下两个文件的改动均为 working-tree 状态，等 review，
未 commit / 未 push。

---

## 1. 死键修复（C2-a）

### 病灶

`config/factor_mining/default.yaml` 的 `data:` 块里有一个
`features:`（6 个 OHLCV 字段）键，它是**双重死键**：

1. **会 crash**：`DataConfig`（`src/factor_mining/miner.py:36-49`）根本
   没有 `features` 字段。`load_config` 走
   `DataConfig(**(raw.get("data") or {}))`，YAML 里多出来的 `features`
   会让 `DataConfig(**...)` 抛 `TypeError: __init__() got an unexpected
   keyword argument 'features'`。也就是说这个 canonical config **根本
   load 不出来**。
2. **就算能 load 也没人读**：GP 文法的终端宇宙是
   `grammar.py` 里硬编码的 `FeatureRegistry.V1`
   （`pit_adapter._default_fields()` → `tuple(FeatureRegistry.V1)`），
   GP 从头到尾读的是那个 registry，**从不读 config 的 `features`**。

### 修复

删掉 `features:` 块（6 行），换成一段注释，讲清楚“终端宇宙在
`grammar.py` 的 `FeatureRegistry.V1`，不在这里配”，避免下一个人再加回来。

diff（`config/factor_mining/default.yaml`）：

```yaml
   start_date: "2018-01-01"
   end_date: "2025-12-31"
-  features:                 # ← 删除：DataConfig 无此字段 + 从不被读
-    - $open
-    - $high
-    - $low
-    - $close
-    - $volume
-    - $money
+  # NOTE: the terminal feature universe is NOT configured here — it is the
+  # hardcoded ``FeatureRegistry.V1`` in src/factor_mining/grammar.py (the GP
+  # grammar reads that, never this config). A ``features:`` key here was a
+  # dead key on two counts: ``DataConfig`` has no ``features`` field (so
+  # ``load_config`` raised ``TypeError``), and it was never read. Removed.
   forward_horizon: 1                  # T+1 buy / T+2 sell per decisions.md D1
```

**只删死键，没动 GP 算法 / 文法 / 参数。**

> ⚠️ 顺带发现（不在本步范围，未改）：`default.yaml` 的
> `pit_provider_uri` 和 `delisted_registry_path` 仍是空串
> （`OPERATOR-FILL`）。Phase A 在 canonical *训练* config 里填过 PIT
> 路径，但那次没碰 `config/factor_mining/`，所以 GP 这个 config 的 PIT
> 路径仍待填。`_build_pit_panel`（miner.py:141）对空串是 fail-closed
> （会抛 `ValueError`），所以这不是隐患、只是“要跑 GP 真实 PIT 前
> operator 得先填”。留给 C2-b。

---

## 2. `decisions.md` D3 回填

`docs/factor_mining/decisions.md` 的 D3（特征宇宙）原文写的是
“6 个 PIT bin 字段 ✅ Final”，但归档的 OpenSpec change
`extend-feature-universe-with-daily-basic`（#187, 2026-05-27）早已把终端
宇宙从 6 扩到 **12 字段**（6 OHLCV/money + 6 `daily_basic`：
`$pe $pb $ps $turnover_rate $circ_mv $total_mv`）。decisions.md 与活的
`FeatureRegistry.V1` 不一致 —— 文档落后于代码。

回填了两处（**只改文档措辞，不改任何 registry / 代码**）：

1. **D3 摘要行**：从 “6 PIT bin 字段 ✅ Final” 改成
   “superseded → 12 字段（6 OHLCV/money + 6 `daily_basic`）；见 D3
   amendment / ⚠️ 6 个基本面字段 PENDING C2 GP 验证”。
2. **D3 节首加 AMENDMENT 块**：指明活的 source of truth 是
   `FeatureRegistry.V1`（12 字段），并**明确这不等于“12 字段是对的”**
   —— 6 个 `daily_basic` 基本面字段是否真有用，**PENDING C2 验证**
   （要在 C1 无泄露的 embargo-gap 折上做一次干净的 GP-vs-Alpha158
   对比，对照 Alpha158 基线 mean IR ≈ 0.30）。

这把“文档 vs 代码”的账对平了，同时把“12 字段是否合理”作为悬而未决项
显式挂到 C2，而不是默认认账。

---

## 3. GP 线 label 前视调查（Step 2，只读）

> 目标：在动手跑 “GP vs Alpha158（IR 0.30）” 对比之前，先把 GP 这条线的
> 前视防护现状摸清楚 —— 不是默认它干净，而是逐项查证。

调查把前视拆成**两条独立的轴**。结论先行：
**轴 1（label 时间前视）干净；轴 2（因子选择期 IS/OOS 污染）是公平对比
的真正拦路虎，当前 GP 不满足。**

### 轴 1 — label 时间前视（intra-split）：✅ 干净

这是 C1 给 WalkForwardEngine 修的那种泄露（train 的 label 偷看 valid
期）。逐项查证 GP 线：

| 环节 | 查证 | 结论 |
|---|---|---|
| 因子构造 | `operators.py` 全部 `ts_*` 用 `shift(n)`（正=向后）/ `rolling(n)`（尾部窗口）；`cs_*`（`cs_rank/zscore/demean/winsorize`）是同日截面。**无 `shift(-n)`、无前向窗口、V1 文法里没有任何前向算子** | T 日因子只读 ≤T 数据 |
| label 定义 | `pit_adapter.forward_return(horizon=1)` = `Ref($open,-2)/Ref($open,-1)-1` = T+1 开盘买 / T+2 开盘卖，收益实现于 [T+1, T+2]（严格未来） | 与 Alpha158 的 2 日 label lookahead 结构**完全同构** |
| IC 配对 | `evaluator.evaluate_factor` 把 factor(T) 与 fwd_ret(T)（= 未来 T+1→T+2 收益）逐日截面配对算 IC | 这是**标准预测式 IC，不是泄露**（“因子预测未来收益”本就该这么配） |
| 退市掩码 | label 经 `PITDataProvider.get_features` 取，post-delist NaN 掩码作用到 label | 退市后不会产生虚假 label |

→ **GP 因子在构造端零前视，label 配对是正规预测设置，与 Alpha158 同构。**

### 轴 1 在 WF 回测层（GP 因子喂进 WalkForwardEngine）：✅ 受保护，但非纵深防御

GP 因子真正跟 Alpha158 比 IR 时，是经
`MinedFactor` handler 走 `WalkForwardEngine.run()` 回测。查证 C1 的
embargo gap 对 MinedFactor 是否生效：

- **C1 的 gap 在 `_generate_windows`**（engine.py），是**折边界级、与
  handler 无关**的。所以 MinedFactor 拿到的折和 Alpha158 一样带
  gap = `LABEL_LOOKAHEAD_DAYS` = 2。
- MinedFactor 的 label lookahead = 2 天（`forward_horizon=1` → 读
  T+1,T+2），**正好被 gap=2 覆盖**。
- ⚠️ **但有一个非纵深防御的缺口**：
  `FeatureDatasetBuilder._validate_embargo`
  （feature_dataset_builder.py:548-549）**只对 `Alpha158` 断言**
  （`if config.feature_handler != "Alpha158": return`），MinedFactor
  在 build 期**没有 embargo 断言**。当前保护**只来自折的 gap，没有
  冗余 check 兜底**。隐患仅在“将来有人把 `forward_horizon` 调到 >1”时
  才暴露：那时 label lookahead > 2，gap=2 会**静默欠覆盖**，而
  `_validate_embargo` 因为跳过非 Alpha158 也**抓不到**。
  `forward_horizon=1`（现状）安全。

### 轴 2 — 因子选择期 IS/OOS 污染：❌ 当前不满足公平对比

**这才是公平对比的真正拦路虎，C1 的 gap 管不到它。**

查证（miner.py + evaluator.py）：

- `miner._build_pit_panel` 一次性加载**整段 panel**
  `[start_date, end_date]`（default = **2018-01-01 ~ 2025-12-31**）。
- `evaluator.evaluate_factor` 在**整段所有交易日**上算 IC 均值。
- **GP 挖矿内部没有任何 train/eval 切分** —— GP 是在整个 2018-2025 上
  最大化 IC 来**选**因子的。

对比的不对称性：

| | Alpha158 | GP |
|---|---|---|
| 特征来源 | 人工设计、**固定**，没在本数据上调过 | 在 2018-2025 上**最大化 IC 选出来的** |
| WF OOS IR | 0.30 是**诚实 OOS**（特征没见过测试期） | 若 OOS 折落在 2018-2025 内，因子**选的时候已经见过这些 OOS 期** |

→ 如果直接拿“在 2018-2025 挖出来的 GP 因子”去 WF OOS 折（也在
2018-2025 内）测 IR，GP 因子在**选择阶段**就偷看过那些 OOS 期 →
**选择偏差 → GP 的 IR 被抬高 → 对 GP 有利的不公平对比**。

这与 label 时间前视（轴 1）是**两回事**：轴 1 是单个 split 内 label
的时间错位（C1 gap 管这个）；轴 2 是**因子选择期与评估期重叠**
（gap 完全管不到）。

### 结论：要做公平的 “GP vs Alpha158（IR 0.30）”，得补什么

**轴 1 不用补**（GP 构造/label 干净；WF 折的 gap 已 handler 无关地覆盖
MinedFactor）。**轴 2 必须补**，否则对比偏向 GP：

1. **挖矿期与评估期必须不相交（GP 训练期严格早于 OOS 评估期）。**
   - **方案 A（冻结切分，最简单，建议先做）**：把挖矿
     `end_date` 设成训练截止（如 2022-12-31），挖完**冻结** factor
     pool，再用 WalkForwardEngine **只在截止+embargo 之后的折**
     （如 2023-2025）跑 OOS。
   - **方案 B（逐折重挖，最严谨）**：每个 WF 折只在该折的 train 窗口上
     重挖 GP，再在该折 test 窗口评估。最贴近“自适应特征集的
     walk-forward”，但**贵**（每折重挖一次），且当前 `run_mining`
     **没接这条线**（它一次性整段挖），要新写编排。
2. **两条线必须用同一个 OOS 窗口。** 现有 0.30 是
   **全period 23 折**的基线。GP 若只在 2023-2025 折上评，就必须**把
   Alpha158 的 IR 也在同一批 2023-2025 折上重算**做 apples-to-apples，
   不能拿“GP 在 2023-2025” 去比 “Alpha158 全period 0.30”。
3. **GP 因子要走 #212 之后的同一个 WalkForwardEngine**（让 MinedFactor
   折继承 gap=2）。这一条 handler 无关、现状已满足，跑的时候确认走的是
   patched engine 即可，无需额外改。
4. **（纵深防御，可选）把 `_validate_embargo` 扩展到对 MinedFactor 也
   断言** gap vs forward_horizon，免得将来 horizon 调大时静默欠覆盖。
   非阻塞项，`forward_horizon=1` 现状不需要。

> 一句话给 C2-b 用：**GP 的 label 前视是干净的，可以放心跑；但“整段
> 挖矿 + 整段 OOS 评估”会让 GP 占便宜，C2-b 必须先切出
> 训练期/OOS 期不相交的方案（建议方案 A 冻结切分），并把 Alpha158 的
> IR 在同一 OOS 窗口重算，才是公平对比。**

---

## 改动文件清单（均未提交）

| 文件 | 改动 | 范围 |
|---|---|---|
| `config/factor_mining/default.yaml` | 删 `features:` 死键 → 注释 | Step 1（C2-a） |
| `docs/factor_mining/decisions.md` | D3 摘要行 + AMENDMENT 块回填 | Step 1 |
| `docs/phase_c2_step1_result.md` | 本报告（新增） | Step 1+2 |

**未动**：任何 `src/` 下 Python、GP 算法 / 文法 / 参数、
`_segment_embargo.py`、`engine.py`。

**STOP**：报告完，等 review。不 commit、不改 GP 代码、不调参。

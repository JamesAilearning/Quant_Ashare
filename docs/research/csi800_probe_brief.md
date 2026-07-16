# CSI800 扩池只读 probe 简报（(b) Step 4 收官件）

> **性质**：只读勘探底数，**非决策依据**。单窗（2025-07-01..2026-06-12）、
> 单折、单种子、无预注册、无多重性控制——所有数字只作未来 (a) 方向
> （CSI800 正式战役 spec）的 Gate-0 输入。**只出底数，不定参、不下结论。**
> 操作人授权链：(b) 四步路线 2026-07-16 签署；Step 1 ingest 数字已签；
> Step 2 = PR #365、Step 3 = PR #366（均已合并）。

## 1. 设置

严格同窗同参对照，唯二变量 = 宇宙 + 其 canonical 全收益基准
（per-universe 映射，#365）：

| | Run A（对照） | Run B（探测） |
|---|---|---|
| config | `config.yaml` 原样（canonical csi300 复现窗） | `my_probe_csi800.yaml`（`extends: config.yaml`，只改两键） |
| instruments | csi300 | csi800 |
| benchmark | SH000300TR | SH000906TR |
| 窗口 | train 2018-01-02..2024-12-18 / valid 2025-01-02..2025-06-26 / **test 2025-07-01..2026-06-12（231 交易日）** | 同左 |
| 成本模型 | 佣金 5bps + 印花税 schedule（窗内 5bps 卖侧）+ 滑点平铺 5bps + min_cost 5 元 | 同左 |
| run id | `20260716_214037_510776_523f7038_74983fea5b2a` | `20260716_214232_514735_0d7e6c8e_f7424166fc4e` |

运行基线：main `e65ae6c`；bundle `D:/qlib_data/my_cn_data_pit`
（日历尾 2026-06-17）；两 run 串行、`Metric Status: official`、
`qlib.backtest.backtest` 路径。

**证据侧车**：[`csi800_probe_evidence.json`](csi800_probe_evidence.json)
（随本简报入库）——两 run 的**解析后配置全量键**（独立证实"唯二变量"
断言）、official_metrics / IC summary 原样、sleeve 分解数字、run 工件
SHA256 锚定（run 目录本体在 gitignored `output/runs/`，以哈希与 run id
可独立核对）、基准注册表与 PR-J 警告原文。

## 2. 头条底数（test 窗年化，超额对各自 canonical TR 基准）

| 指标 | csi300（A） | csi800（B） |
|---|---|---|
| 毛超额年化 | +1.26% | **+3.68%** |
| 毛超额 IR | 0.152 | **0.382** |
| 净超额年化 | −4.51% | **−2.03%** |
| 净超额 IR | −0.546 | −0.210 |
| 净超额 MDD | −9.41% | −6.92% |
| IC (h=1) | 0.0223 | 0.0162 |
| ICIR (h=1) | 0.149 | **0.166** |
| IC 正率 (h=1) | 56.7% | **58.4%** |
| IC (h=5) | 0.0399 | 0.0321 |

底数解读（描述不结论）：扩池后 IC **均值降、稳定性升**（池宽摊薄单票
信号但正率与 ICIR 上行）；毛/净超额双双好于 csi300，净仍为负。

## 3. 成本拖累底数

| | csi300 | csi800 |
|---|---|---|
| 毛−净拖累（年化） | 5.77pp | 5.71pp |

两边拖累几乎相同——**但这是"平铺 5bps 滑点"假设下的产物**。csi800 组合
实际把 61.8% 权重压进中盘腿（§4），中盘真实冲击成本大概率高于平铺
5bps，即 **csi800 的净超额在此假设下偏乐观**。中盘差异化滑点该取多少
是 (a) 的设计问题（本简报不定参）。pipeline 模式未产出逐笔换手
（`trades_status: not_produced`），换手底数留给 (a) 的 walk-forward。

## 4. Sleeve 分解（run B，post-hoc，`csi800_sleeve_v1`）

分组图：`attribution_sleeve_loader`（#366）as-of 2025-07-01
（coverage_end 2025-12-31）；基准腿权重 = 800 成员 `$circ_mv` as-of
自由流通市值占比（**近似**：非官方分级靠档）。

| sleeve | 组合权重 | 基准权重 | 组合腿收益 | 基准腿收益 |
|---|---|---|---|---|
| csi300_sleeve | 33.8% | 78.3% | +47.8% | +17.3% |
| csi500_sleeve | **61.8%** | 21.7% | +56.5% | +32.2% |
| unknown | 4.4% | 0% | +85.4% | — |

底数解读：
- **模型在 csi800 里天然重仓中盘**（61.8% vs 基准 21.7%）——alpha 的
  主战场在中盘腿；两腿的选股收益都显著为正（组合腿收益远超基准腿）。
- `unknown` 4.4% = 窗中调入成分（as-of T0 静态分组的诚实桶，#366 设计
  内行为），量级可控。
- **Brinson 效应分解只作方向参考**：单期近似对 231 日高换手组合的
  对账残差 −0.31（引擎自身响亮警告），权重与分腿收益是硬数、
  allocation/selection 拆分是软数。post-hoc 未挂 PITDataProvider
  （引擎已 WARN），退市票尾价可能污染分腿收益的个别分量。

## 5. 机制实证（顺带验证 Step 1-3 交付链）

- Run B 消费 `SH000906TR`，**canonical 配对维度零警告**（日志无
  `MIS-PAIRED` / `NOT one of the canonical` 行——#365 的 universe-aware
  配对检查静默通过）。值级校验的**两条预期警告**照常出现（面包屑
  必响的 consume-time 检查 + 价格兄弟 `SH000906` 缺席的 "cross-check
  skipped"，均为 PR-J 设计内行为，原文录 evidence 侧车）——机制证据
  应区分这两层，配对静默 ≠ 全程无警告。
- sleeve loader 真实 bundle 解析 300+500=800 零重叠、越界 as-of 拒绝
  （#366 双层守卫生效）。

## 6. 留给 (a) 的设计问题（不是结论）

1. 中盘差异化滑点定参（平铺 5bps 明显偏乐观，取多少需实证/文献支撑）。
2. 走 walk-forward 全窗（本 probe 单折单窗，2025H2-2026H1 恰逢中盘强势
   期，周期依赖未知）+ 预注册（阶段八式门与 FWER 纪律）。
3. 净超额仍负：扩池不解决"成本吃掉 alpha"的主命题，只改变了毛的高度
   ——降频（既有止血方向）× 扩池的交互是 (a) 的核心实验设计。
4. sleeve 报告是否进 runtime（walk-forward per-fold sleeve 分解）
   ——#366 预备件已就绪，接线属 (a) spec 义务。

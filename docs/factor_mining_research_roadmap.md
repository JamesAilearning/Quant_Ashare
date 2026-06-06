# Factor Mining — Research Roadmap

> **这份文档的定位**:把 27 篇研究论文的审核结论,收敛成一份可执行的 backlog,按「现在 / v2 / v3 / 放弃」分层。
>
> **它不是要你全做。** 恰恰相反——它存在的目的是明确「什么现在做、什么明确推迟」,防止「读了论文很兴奋 → 啥都想加 → scope creep」。27 篇论文如果每篇取一点,会累积成几十个新功能,把还没跑通的地基压垮。
>
> **来源**:`docs/research/paper_notes.md`(Claude Code 对 27 篇论文的结构化笔记)+ reviewer 审核(2026-05-26)。
>
> **审核者的核心动作**:把笔记里偏宽松的 Tier 1(8+ 篇、十几个 actionable 点)收敛到 6 个真正现在做的项,其中 5 项是零代码的文档/设计原则,只有 1 项是实际代码(且正好落在当前 Phase 1)。

---

## 0. 怎么用这份文档

- **「现在」项**:在对应 Phase 实现时,把该项的「论文依据」和「验收标准」作为参考。大部分是文档/设计原则,不改架构。
- **「v2 / v3」项**:明确推迟。**现在不要碰。** 记录在案是为了「好想法不丢」,不是「现在去做」。
- **「放弃」项**:明确不做,避免反复纠结。
- 每项都带论文依据(文件名 + 关键位置),实现时可回溯原文。

---

## 1. 核心原则(每次想加东西前重读)

1. **论文是读不完的。** 前沿研究成千上万篇,每篇都能给你一个「还能再加 X」的理由。没有过滤器 = 无止境的 scope creep。
2. **每个想法都过「现在 / v2 / v3」过滤器**,而不是问「这个能不能加」。绝大多数好想法的答案是 v2 或 v3——这不是否定,是承认你现在的优先级是把 GP + PIT 跑通。
3. **一个能跑通、数据干净、你完全理解的简单系统,胜过一个塞满前沿想法但你 hold 不住的复杂系统。**
4. **警惕「把别人场景的结论直接搬过来」。** 大多数论文是美股 / 机构 / 基本面 / 深度学习场景。你是 A股 / 个人 / 纯量价 / GP。结论不能线性外推。(典型反例:capacity 约束对机构是 Tier 1,对你现阶段是 v3——见 §4。)

---

## 2. 现在就做(Phase 1-3 + Phase 6 设计输入)

6 项。标注了 Phase 归属、是「代码」还是「文档/原则」、验收标准、论文依据。

### 2.1 算子库适度补充 【Phase 1 · 代码】

**做什么**:在 `operators.py` / 算子库里增加以下有明确依据的算子和派生量:

```
新增时序算子:
- ts_ema(x, n)              # 指数加权移动平均(Volume Shock 论文用 EMA;主流 operator set 都有)
- ts_skew(x, n)             # 滚动偏度
- ts_kurt(x, n)             # 滚动峰度
- ts_slope(x, n)            # 滚动线性回归斜率
- ts_idxmax(x, n)           # 滚动窗口内最大值的位置
- ts_idxmin(x, n)           # 滚动窗口内最小值的位置
- cs_mad / ts_mad           # 中位数绝对偏差(稳健离散度)

新增派生 primitive(用现有 6 字段就能算,建议像 $vwap 一样作为 derived feature):
- $vwap            = $money / $volume                # 成交均价
- overnight_return = $open / Ref($close, 1) - 1       # 隔夜收益
- intraday_return  = $close / $open - 1               # 日内收益
```

**为什么适度**:算子越多,GP 搜索空间越大。在你单 GPU(RTX 4060 Ti)+ A股低信噪比下,空间过大反而更难收敛。**只加上面这些有论文依据的,不要为了「对齐主流」无限扩充。**

**验收标准**:
- 每个新算子通过 §5.2/§5.3 的数值稳定性 + PIT-gap 单元测试(NaN/零/常量/空/带 NaN 洞)。
- `ts_ema` 的 PIT-gap 测试:序列中间有 NaN 洞时,EMA 不跨洞污染(min_periods 语义明确)。
- `overnight_return` / `intraday_return` 作为 derived primitive 注册进 FeatureRegistry,GP 可直接引用。

**论文依据**:
- `ssrn-5156605` (Volume Shocks & Overnight Returns):Volume Shock = `Volume / EMA(Volume) - 1` 与隔夜收益正相关、与日内收益无关 → 需要 EMA 算子 + overnight/intraday 分解。
- `自动挖因子1` (AlphaGen) / `1571_AlphaQCM` / `2508.13174` (AlphaEval):三篇的 operator set 高度一致(22-32 个),都含 Skew/Kurt/Slope/IdxMax/IdxMin/EMA/Mad。

**A股 caveat**:overnight_return 在 A股要用集合竞价 `$open`,但 A股无盘前盘后交易、T+1 制度,夜间信息扩散机制与美股不同。挖出这类因子后,Phase 6 要单独做 A股 sanity check,不要默认美股结论成立。

---

### 2.2 A股经济先验文档化 【design doc · 零代码】

**做什么**:在 `factor_mining_claude_code_design.md` 增加一节「A股经济先验」(或单开 `docs/factor_mining/a_share_priors.md`),记录以下经验事实,作为 **Phase 6 人工审核 mined factor 时的 sanity-check 依据**:

1. **A股 liquidity 类信号是 top predictor**(与美股 profitability 主导相反)。所以纯量价 + A股的 v1 设定,反而与 A股真实 alpha 来源高度对齐——这是你设计的一个隐性优势,不是缺陷。
2. **A股 size/value 与美股反向**:小市值估值反而「贵」,因为有「壳价值」(IPO 审核严 + 退市难 → 小盘=潜在借壳标的)。**注意时效性:2019 注册制 + 2024 大量小市值退市后,壳价值正在被显著削弱。** mined 出「小市值显著超额」的因子时,要警惕这是 shell-value premium 还是真 alpha,且这个 premium 正在消退。
3. **reversal 的 vol/turnover 条件性**:波动率↑→反转更快更强;换手率↓→反转更持久(美股微观结构结论)。但 A股 T+1 + 涨跌停改变了 inventory 时钟,散户主导下甚至可能反向——所以这是「为什么 mined reversal 因子 IC 不稳定」的解释线索,**不是可直接照搬的规律**。

**验收标准**:design doc 有这一节;Phase 6 promotion 的人工审核 checklist 引用它。

**论文依据**:
- `Machine-learning-in-the-Chinese-stock-market` (JFE 2022):A股 liquidity 是 top predictor。
- `ssrn-4385668` (Value premium in China):A股大市值估值更高(壳价值)。
- `ssrn-4339591` (Reversals & Liquidity):vol/turnover 条件性反转(美股,需 A股验证)。

---

### 2.3 evaluator 的 NaN 处理铁律 【Phase 2 · 设计原则】

**做什么**:在 evaluator 设计原则里明确一条**铁律**:

> **算 IC / RankIC 时,绝不做隐式 median fill。** 不把 NaN 替换为 0,不用横截面 median 填充。要么 listwise-drop NaN(并记录 drop 比例),要么明确文档化所用的 imputation 方法并评估其偏差。

**为什么**:这与你 PIT 系统的 NaN gap 处理一脉相承。PIT 在 entity 边界强制 NaN 是「诚实承认缺失」;如果 evaluator 在算 IC 前偷偷填值,等于把 PIT 的诚实又毁掉,引入选择偏差 / 乐观偏差。`phase1_preflight.md` 的数值稳定性一节已经提过,这篇论文从资产定价偏差角度强化了它。

**验收标准**:
- evaluator 代码中无任何隐式 fillna(0) / fillna(median) 在 IC 计算路径上。
- 一个 governance test:对 csi300 某一天,故意 mask 若干股票的某字段,验证 evaluator 不会因此对该因子的 IC 给出乐观估计。

**论文依据**:
- `数据补足收益暴增` / `ssrn-4106794` (Missing Financial Data,**两个文件是同一篇,请去重**):median 补缺严重扭曲 decile sort 的 Sharpe 和 cross-sectional regression 的 risk premium;listwise「要求多特征同时观测」也引入选择偏差。
- 注:论文的 B-XS 补缺算法本身对 v1 纯量价(6 字段,高度共变)用处不大,留待 v2 接基本面(P/E、P/B 等季频缺失多)时再用。

---

### 2.4 promotion gate 的 shrinkage 概念 【Phase 6 设计输入 · 现在只记不做】

**做什么**:现在不实现(Phase 6 才到),但**写进 promotion gate 的设计**:

> mined factor 的 OOS 预期表现会显著衰减。promotion 阈值按「IS 表现打 5-7 折」估算:`expected_OOS_IR ≈ IS_IR × 0.5~0.7`。不要用 IS 的 IR 直接当 promotion 标准。

**为什么**:ML 因子在三种现实摩擦(交易成本 + post-publication decay + 流动性提升)后,平均 Sharpe 衰减约 57%。你的因子虽然不是「已发表」,但 GP 在 25k 候选里挑 top = 严重的多重测试,过拟合导致 OOS 衰减是必然的。先把折扣写进设计,管理预期。

**验收标准**:Phase 6 promotion gate 的设计文档包含 shrinkage 系数;`decisions.md` D4 的晋级标准注明「阈值基于 shrunk IR」。

**论文依据**:
- `ssrn-4702406` (Expected Returns on ML Strategies):三摩擦后 ~57% Sharpe 衰减;LSTM(复杂信号)抗摩擦最强,反过来说 GP 浅层公式因子衰减更需警惕。

---

### 2.5 PFS 扰动测试 【Phase 1-2 · 一个轻量 governance test】

**做什么**:加**一个**轻量的鲁棒性测试(不是整个评估框架):

> 对输入 OHLCV 加小幅噪声扰动,重新计算 mined factor 的 IC,检查 IC 是否稳定。对扰动过度敏感的因子 = 过拟合信号,早期标记。

**明确边界**:**只取这一个 PFS perturbation 测试。** AlphaEval 的完整 5 维度评估框架(预测/稳定/鲁棒/可解释/多样性)对当前阶段太重、太早——你连 GP 都没跑通,不要引入一个大评估框架。其余 4 维留 v2。

**验收标准**:有一个 `test_factor_robustness_under_perturbation` 类的测试;对一个已知稳健因子(如 20 日反转)扰动后 IC 变化在容忍范围内,对一个人造过拟合因子能检出不稳定。

**论文依据**:
- `2508.13174` (AlphaEval):5 维度无回测评估框架,PFS(对 OHLCV 加噪声看 alpha 一致性)是其中最轻量、最早期可用的一维。有开源代码可参考实现。

---

### 2.6 correlation niche penalty 调松 【Phase 3 GP 设计 · 参数调整】

**做什么**:设计 GP 多样性机制时,**不要把 correlation niche penalty 调太严**。design doc §4.3/§4.4 原本的「diversity = hash dedup + correlation niche penalty」要重新审视惩罚强度。

**为什么**:AlphaGen 实证给了一个反例——两个 mutual IC 高达 0.9746 的因子(传统方法会判为「重复」直接丢弃),线性组合后仍贡献 IC 0.0458。说明「高相关 = 无用」这个直觉在因子组合层面不成立。niche penalty 调太狠会误杀「近似但互补」的因子。

**注意**:这条**不否定**你的 niche 设计(AlphaGen 没证明 GP+penalty 在你的设定下会失败),只是提醒「别调太严」。是参数调整,不是新功能。

**验收标准**:Phase 3 GP 设计文档讨论了这个反例;niche penalty 强度可配置,默认值偏宽松;后续可通过实验调。

**论文依据**:
- `自动挖因子1` (AlphaGen):Table 3 + §4.3,0.9746 mutual IC 因子组合后仍贡献增量 IC 的反例。

---

## 3. v2 Backlog(基本面数据 + RL 升级 —— 现在不做)

等 GP + PIT 基础跑通、且有了基本面数据后再启动。

| 项目 | 内容 | 论文依据 |
|------|------|---------|
| **搜索层升级 GP→RL** | 按 SearchStrategy 接口,依次实现 AlphaGen → AlphaForge → AlphaQCM。AlphaGen 开源、是第一步;AlphaQCM 用分布式 RL 处理非平稳性,是研究级改进。**架构已预留接口,不throw away GP。** | `自动挖因子1` (AlphaGen) / `自动挖因子2` (AlphaForge) / `1571_AlphaQCM` |
| **基本面因子库** | 接入 openassetpricing 完整 319 因子 + Profitability 系列,作为 benchmark / seed / novelty 基准的完整版 | `ssrn-3604626` / `ssrn-5178543` |
| **完整 AlphaEval 5 维评估** | PFS(2.5 已取)之外的 4 维:预测、稳定、可解释、多样性 | `2508.13174` |
| **动态因子组合** | 每日按滚动 ICIR 重选 + 重加权 mega-alpha(factor timing);PLS 组合多个相似 signal | `自动挖因子2` (AlphaForge) / `ssrn-4385668` (PLS) |
| **Missing Data 补缺** | B-XS / BF-XS 算法补季频基本面缺失(P/E、P/B、ROE) | `数据补足收益暴增` |
| **vol/turnover-conditional reversal 严格版** | 剥离 PEAD + industry momentum 的 IRRX(需 announcement + industry 数据) | `ssrn-4339591` |
| **bootstrap 多重检验 gate** | Harvey-Liu「强制 null + bootstrap」作为 promotion 的统计严格化 | `ssrn-2528780` (Lucky Factors) |
| **partial correlation novelty** | novelty test 时先对 market factor 正交化再算相关,避免被市场暴露掩盖 | `ssrn-5080998` |
| **label 选项 vwap-based** | forward_return 提供 vwap-to-vwap 选项(比 close-to-close 更贴近 T+1 实战);walk-forward 年度重训标准化 | `自动挖因子2` (AlphaForge) |
| **monotonicity 辅助指标** | decile sort 收益单调性作为 evaluator 辅助质量指标(csi300 样本小,噪声大,谨慎) | `ssrn-3604626` |
| **LLM 辅助(谨慎)** | GPT 生成公式作为 GP 种子候选 + Phase 6 经济解释辅助(绝不作 promotion 决策依据;论文实证严重存疑) | `ssrn-4560216` |

---

## 4. v3 Backlog(高级 / 另一条技术路线 —— 地基稳了再说)

这些大多是**与 GP 路线竞争的另一种范式**,或针对大资金机构的问题。记录在案,但要等系统成熟、且确实有对应需求时再考虑。

| 项目 | 内容 | 为什么是 v3 / 备注 | 论文依据 |
|------|------|------|---------|
| **深度学习潜因子** | latent factor 模型(GRU/VAE/Transformer + regime-switching) | 黑盒范式,与白盒公式因子是另一条赛道,不是 GP 升级 | `态势选因子` (RSAP-DFM) / `态势选因子3` (HireVAE) / `强化学习50因子` (AlphaPortfolio) |
| **GFlowNet 搜索** | 按 reward 比例采样多样化因子的搜索算法 | GP 的又一替代;先读正式版 **AlphaSAGE 2025**,别用那个 demo README | `自动挖因子3` (alpha-gfn,实为 GitHub README) |
| **capacity 约束** | fitness 加容量惩罚(容纳多大资金不被冲击成本吃掉) | **个人投资者现阶段几乎不是约束;这是大资金机构的问题。** 若未来要管别人的钱再提优先级 | `ssrn-5797502` |
| **regime-aware fitness** | 在不同市场态势下分别评 IC(在线聚类识别 regime) | 需要先有 fitness 调优需求 + regime 标注 | `态势选因子3` (HireVAE) / `市场态势选因子` |
| **拥挤套利 / 因子崩溃对冲** | 二阶 alpha:用波动率因子对冲 risk-on 崩溃;拥挤度量 | 组合/策略层的高级风险管理,远超因子挖掘本身 | (Navigating Factor Crowding,前次对话) |

---

## 5. 明确放弃(不做,别再纠结)

| 论文 | 为什么放弃 |
|------|-----------|
| `市场态势选因子` (Macro Perceptions & Anomalies) | 美股 + 基本面 + 多因子轮动,且需要 A股没有的高质量宏观预期调查数据 |
| `ssrn-5316487` (VIX ETN Trading) | 美股衍生品,不同 asset class |
| `2024.acl-long.402` (EFSA) | 中文金融 NLP 情感分析,与公式因子无关(若 v3 想做舆情因子才参考) |
| `2407.06567v3` (FINCON) | LLM 多 agent 交易决策,是 decision layer 不是 factor layer |
| `2502.15458v2` (Clustered Connectedness) | 跨市场指数 spillover 计量经济学,非个股因子 |

---

## 6. 几个跨论文的重要认知(校准你的预期)

这些不是「待办」,是读完 27 篇后**你应该建立的几个判断**,写下来防止以后忘:

1. **PIT 是你最大的差异化优势,代价是 IC 看起来更低。** 27 篇里没有一篇用 PIT-correct 数据——所有 A股因子文献(AlphaGen / AlphaForge / AlphaQCM / JFE 2022)都用 baostock 或 CSMAR 的非 PIT 数据,带 survivorship。**你的 IC 一定低于它们论文里的 number(比如 AlphaGen 的 0.0725),但你的是真实可信的,它们的是高估的。** 这条要写进用户文档,作为「baseline 预期校准」——否则你会误以为自己的系统「不如论文」。

2. **你的「纯量价 + A股」设定,意外地对齐了 A股的真实 alpha 来源。** A股 liquidity 类信号是 top predictor(美股是 profitability)。你 v1 用不了基本面,本来像是限制,但在 A股恰恰落在 alpha 最集中的子空间。这是隐性优势,别因为「论文都在用基本面」而焦虑。

3. **高相关 ≠ 无用。** mutual IC 0.97 的因子组合后仍可能贡献增量 IC。所以 niche penalty 别太严(已落到 §2.6)。

4. **多重检验是 GP mining 的真实威胁。** GP 在 25k 候选里挑 top,本质是海量重复测试,极易过拟合。GPT-factors 那篇 Sharpe 4.49 就是反面教材(15 个月样本 + 35 因子挑 top 1)。这是为什么 §2.4 的 shrinkage 和 §2.5 的扰动测试重要,以及为什么 v2 要上 bootstrap 多重检验 gate。

---

## 7. 顺带:D3 决策更新(Phase 0 已确认实际字段)

Phase 0 inventory 确认你的 PIT 实际字段是 **6 个**,不是设计文档早先假设的 8 个:

```
$open  $high  $low  $close  $volume  $money
```

**请把 `decisions.md` 的 D3 finalize 成这 6 字段。** 派生量按需计算:
- `$vwap = $money / $volume`
- `overnight_return = $open / Ref($close, 1) - 1`
- `intraday_return  = $close / $open - 1`

(早先设计里写的 `$amount`/`$turn` 若 PIT 没有,就不要硬造;`$money` 即成交额,等价于早先说的 `$amount`。)

---

## 附:这份 roadmap 砍掉了什么(审核留痕)

为了透明,记录 reviewer 相对 paper_notes.md 做的主要降级,便于日后回溯:

- **capacity penalty**:笔记列 Tier 1 最优先 → 降到 v3(个人投资者非约束)。
- **AlphaEval 完整框架**:笔记列 Phase 6 backbone → 现在只取 PFS 一维,其余 v2。
- **monotonicity**:笔记列现在 → 降到 v2(csi300 decile 样本小,噪声大)。
- **Lucky Factors bootstrap**:笔记列现在 → 降到 v2(99 页方法论,v1 阶段 ROI 一般)。
- **LLM/GPT seed**:笔记列现在可做 → 降到 v2 且标「谨慎」(论文实证严重存疑)。

核心收敛逻辑:**现在阶段(Phase 1-3)只做低成本、不改架构、明确对齐「A股 + 量价 + GP」的项;凡是需要基本面数据、需要 RL/DL 栈、或针对机构规模问题的,一律推迟。**

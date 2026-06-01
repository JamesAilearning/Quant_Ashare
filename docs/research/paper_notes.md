# 量化研究论文阅读笔记

> 阅读者: Claude (研究助理角色)
> 日期: 2026-05-26
> 任务边界: **只读、不改代码**。本文件是 27 篇论文的结构化提炼,供 reviewer + 人类判断纳入与否。
> 当前系统状态(用于判断"相关性"):
> - GP-based 因子挖掘,v1 纯量价(6 字段: `$open $high $low $close $volume $money`)
> - 数据走 `PITDataProvider`(A 股,后复权 + as-of-today adj_factor,需要 scale-invariance)
> - 30bps 双边交易成本,v1 不做行业中性化,手动晋级
> - Phase 1-3 在打基础(算子库 / 评估器 / GP 引擎)
> - 硬件 RTX 4060 Ti 16GB + i7 CPU

---

## 中文论文

> 注:中文文件名仅是收录者的标签,论文本体多为英文 academic paper。

### 市场态势选因子.pdf
- **标题 / 作者 / 年份 / 来源**:Macroeconomic Perceptions, Financial Constraints, and Anomalies / He, Su, Yu / 2024 / SSRN 4025761(此处文件实为该文)
- **一句话核心贡献**:用 SPF 工业生产增速(IPG)预期修正,预测一系列 anomaly 的时间变化——基于"融资约束更紧的企业受宏观情绪影响更大"的渠道。
- **方法与主要发现**:用美国 SPF 调查的 IPG 前瞻预期修正作 signal,把 1969-2019 美股分为"上修期 / 下修期"两态;发现盈利/质量/低风险/财务困境因子在上修期表现更好,价值/低投资/小盘在下修期更好;两组反转构建的因子轮动策略 Sharpe 0.71、FF5 alpha 1.93%/季,显著优于单组(0.29 / 0.22)。
- **数据与市场背景**:**美股**(CRSP/Compustat/IBES/TRACE),1969Q1-2019Q4 季频。SPF 是美国 SPF survey,A 股没有对等高质量调查序列。anomaly 用的是 BM, ROA, ROE, OP, FProb, O-Score, IVOL, IA 等——大部分需要基本面。
- **与当前系统的关系**:概念性影响,跨越 GP fitness 层和组合层。本质是"宏观条件→因子时变"的因子轮动,不影响因子挖掘本身。
- **具体 actionable 点**:无直接 actionable for v1。若 v2 接入基本面后做多因子组合,可以借鉴它的两态拆分方法学(把宏观预期修正离散为 up/down 两态,在每态下分别评估因子)。但 A 股需找等价的宏观预期 signal(中采 PMI 预期、央行调查问卷等),不是现成可拿。
- **采用的前提条件**:① 接入基本面数据(v2);② 找到 A 股可用的 PIT 宏观预期数据;③ 多因子组合层(目前 v1 只挖单因子)。
- **你的初步归类建议**:【不适用 / 长期概念】—— 美股 + 基本面 + 多因子轮动,v1 范围之外。最早 v3 才有可能讨论。
- **批判性意见**:① 美股结论不能直接搬到 A 股,A 股散户为主、卖空受限,情绪传导机制不同;② IPG 预期修正 ≠ 因子 alpha source,这是 timing/rotation 工具,不是 mining 工具;③ 论文核心因子(profitability/quality/distress)在 v1 量价空间根本无法表达;④ 文件命名"市场态势选因子"暗示研究者把它看成"选因子的态势",但其实它是"用态势预测因子表现",方向相反——存在被误用的风险。
- **关键页码/引用**:Abstract;Table 4(两态收益对比);Fig 3(rotation strategy);§4.1(financial-constraint 渠道理论)。

### 态势选因子.pdf
- **标题 / 作者 / 年份 / 来源**:RSAP-DFM: Regime-Shifting Adaptive Posterior Dynamic Factor Model for Stock Returns Prediction / Xiang, Chen, Sun, Jiang(复旦)/ IJCAI-24 / 会议论文
- **一句话核心贡献**:端到端深度学习因子模型,把"市场 regime"在**连续区间**上自适应识别,通过"双 regime shifting"(影响 factor return 和 factor loading)动态调整因子模型。
- **方法与主要发现**:用 GRU+多头注意力构建 latent factors;两个 regime 编码器(jumping/loading)分别影响因子收益和因子暴露;再用对抗学习构造 posterior factors 作为辅助任务,二级(bilevel)优化。在 CSI100/300/500 上 IC 0.077/0.086/0.085,Rank IC 0.087/0.096/0.103,超过 FactorVAE/TRA/TCTS 等基线。
- **数据与市场背景**:**A 股**,Alpha360 (Qlib),60 日 OHLC+VWAP+Volume(**6 字段,和我们 v1 几乎一致**)。训练 2008-2014、测试 2017-2020.8。
- **与当前系统的关系**:**整套模型范式不同**——这是深度学习黑盒"潜因子"模型(latent factor → return),v1 是公式化白盒(symbolic expression → factor → IC/IR);属于"另一条技术路线",不是 v1 GP 的组件升级。
- **具体ionable 点**:① latent regime concept 可以借鉴——把"市场态势"当作 evaluator 阶段的条件变量,做 regime-aware fitness(在不同 regime 下分别计算 IC);② 它用的 60 日 OHLC+VWAP+Volume 字段集和 v1 几乎相同,可作为 v1 mined factors 的 deep-learning baseline 对照;③ posterior factor via adversarial learning(给因子加扰动看预测稳定性)的想法可借鉴作"鲁棒性 fitness"。但都属 v2+ 想法,不是 v1 现在的事。
- **采用的前提条件**:需要先有 latent factor 路线本身(目前 v1 是公式因子,无 latent factor 层);regime-aware fitness 需要 regime 标注或在线识别能力;adversarial robustness 测试需要先有 GP→evaluator pipeline 跑通(Phase 3 之后)。
- **你的初步归类建议**:【v3 或不适用】—— 范式差异大。整套搬用属于"换一种实现",不是渐进改造。adversarial robustness 单点可以 v2 借鉴。
- **批判性意见**:① 测试期 2017-2020(含 2018 熊市 + 2020 疫情),"全市场最优"在 A 股很容易因为大盘 beta 暴露被放大,文中 IC 0.08 在 A 股深度学习 baseline 中并不离谱但 IR 还是受测试期巧合影响,**不一定可复现**;② 无 OOS walk-forward,仅单一 split;③ "连续 regime"的定义有点玄学——regime 本质就是离散态,论文用 Softmax 上的最大值再回到离散,所谓"连续"更像营销话术;④ 与 v1 路线完全无关,不要因为标题相似(我们项目里中文文件名)而误以为它是答案;⑤ Alpha360 用了 ranking 处理(看上去),与 PIT 系统的 within-ticker ratio 约束兼容性未知。
- **关键页码/引用**:Fig 1(整体架构);Eq. (4)(双 regime shifting 形式);Table 2(三个 universe 上的 IC);§4.5(bilevel optimization 算法)。

### 态势选因子3.pdf
- **标题 / 作者 / 年份 / 来源**:HireVAE: An Online and Adaptive Factor Model Based on Hierarchical and Regime-Switch VAE / Wei, Rao, Dai, Lin(港中文 + 上海 AI Lab)/ 2023 / arXiv 2306.02848
- **一句话核心贡献**:在线、自适应的 regime-switching 因子模型(VAE 派),用 hierarchical latent(市场 → 个股)+ 在线聚类算法识别 regime,然后在不同 regime 下用不同的 decoder 重建/预测收益。
- **方法与主要发现**:① market encoder 提取市场 latent variable;② linear stabilization clustering algorithm(在线对市场 latent 做 K 类聚类,K=3,中心用 EMA 平滑更新);③ K 个 regime-specific decoder。在 CSI300/500/1000/CNI2000/CSI All 上做组合构建,active return 28%/年(2015-2022 测试期),IR 5.92,优于 FactorVAE/CVAE/MLP/GRU/TRA 等。
- **数据与市场背景**:**A 股**,58 个 stock features + 多 modality market features(动量/相对成交量/波动率,不同 timeframe)。训练 2010-2014,测试 2015-2022。
- **与当前系统的关系**:与 RSAP-DFM 类似——整套深度学习 latent factor 范式,与 v1 GP 路线**不在同一个赛道**。但其中"market regime 在线识别"组件是独立可借鉴的概念。
- **具体 actionable 点**:① 在 v2/v3 阶段,如果 evaluator 要做 regime-conditional fitness,可以参考它的在线聚类思路(K 类、EMA 平滑、按 mean 排序保证 cluster 顺序一致)——但要先有 regime 概念融入 fitness 的设计前提;② 它的多 timeframe market features(动量/波动率/成交量分位)可以作为 v2 evaluator 阶段的 regime indicator 候选。
- **采用的前提条件**:① 先决定 v1 的 GP fitness 是否需要 regime 条件化(目前还在 Phase 2-3 基础阶段,这步太早);② regime indicator 需要 PIT 安全的计算(论文里 market index 包含全部成分股,A 股有指数变动问题——v1 的 csi300 universe 已经包含 PIT 处理,但 regime 计算的 market proxy 还要确认);③ 整体 latent factor 路线只能作为 v3 的 alternative architecture,不是 v1 升级。
- **你的初步归类建议**:【v3 或不适用】—— 同 RSAP-DFM。最 actionable 的"在线聚类做 regime"组件,也得等 v2 有了 fitness 调优需求才用得上。
- **批判性意见**:① "Active return 28%/年" 在 A 股 2015-2022 测试期(经历两次大牛市+一次熊市)很容易被小盘风格暴露偏差解释,论文没显示分年表现和风格归因;② Top-1/G 组合是 long-only,benchmark 是相应宽基指数——alpha 来源可能就是小盘风格,不是真正的"regime-aware" alpha;③ regime 数 K=3 是超参,论文未做 K 的稳健性测试;④ 与 v1 路线无关,价值在概念启发。
- **关键页码/引用**:Algorithm 1(linear stabilization clustering);Fig 2(架构图);Table 2(各指数上的 active return);§4.3(regime 识别细节)。

### 强化学习50因子.pdf
- **标题 / 作者 / 年份 / 来源**:AlphaPortfolio: Direct Construction Through Deep Reinforcement Learning and Interpretable AI / Cong, Tang, Wang, Zhang(康奈尔/清华/北航)/ 2022 / SSRN 3554486(标题里的"50因子"是中文收录者贴的标签,与原文无关——原文实际用了 ~600 维特征,12 月窗口)
- **一句话核心贡献**:**绕开"先预测因子,再组合"的两步法**,用深度强化学习(RL)直接优化组合目标(OOS Sharpe / α / 经理人薪酬等),架构是 Transformer Encoder + 跨资产注意力(CAAN) → "winner score" 排序 → softmax 取权重。
- **方法与主要发现**:① Transformer Encoder 处理每只股票 12 月时间序列;② CAAN 模块捕捉股票之间的横截面交互(创新点,放在 TE 之上);③ winner score → 长前 10% / 空后 10%;④ RL 用 policy gradient 直接最大化 12 月 OOS Sharpe(或其他目标),不需要 label;⑤ 美股 1965-2016,月度调仓,OOS Sharpe ≈ 2.0(全样本)、2.31(排除 10% 微盘),FF6 alpha ≈ 13-15%/年。"经济蒸馏"(Lasso 投影到多项式特征空间)挖出 ivc/Q/Idol_vol/Ret_max 等核心特征。
- **数据与市场背景**:**美股**(CRSP+Compustat),1965-2016。Freyberger-Neuhierl-Weber 风格的 51 类特征 × 12 月滞后 ≈ 612 维输入,涵盖动量/价值/盈利/无形资产/交易摩擦/基本面 6 大类。**远超 v1 的 6 字段量价**;尤其大量依赖基本面(B2M, ROE, ROA, ivc, free_cf 等)。
- **与当前系统的关系**:**范式差异巨大**——v1 是 GP 公式因子(white-box,可解释表达式),AlphaPortfolio 是 RL+Transformer 直接出权重(black-box)。两条路线不在同一赛道,但有 3 个组件级的启发:① "直接优化最终目标"思想——v1 fitness 已经在这么做(直接用 IC/IR 而非 MSE),只是 GP 而非 RL 来搜索;② CAAN 的"跨资产注意力"概念对应到 v1 是"截面 rank/normalize",方向一致但深度不同;③ "经济蒸馏"(post-hoc 用 Lasso/多项式拟合复杂模型)对应到 v1 GP 因子的辅助分析(可以用它评估 mined factor 的非线性/交互项贡献)。
- **具体 actionable 点**:① 论文 §5.1-5.2 的 polynomial sensitivity 思路可以借鉴,用于解释复杂的 mined factor 表达式(把表达式投影到一阶+二阶多项式空间看哪些 raw feature 贡献最大),作为 evaluator 的辅助质量指标;② 论文中"以 OOS Sharpe / 经理人薪酬 / 资金幸存"为目标的灵活性,对应到 v1 fitness 函数可学习的属性(目前是 IC/IR/turnover 加权),论文说明"目标可换"——但这只是概念肯定,我们已经在做。其余整套范式不直接 actionable for v1。
- **采用的前提条件**:整套搬用需要:基本面数据(v2)、Transformer/RL 训练栈、月频组合层、监督学习/RL 数据 pipeline。绝大多数都不属于 v1 范围。"经济蒸馏"组件只需要 mined factor 已经产出 + 一个 raw feature 库,门槛低。
- **你的初步归类建议**:【v3 或不适用】—— 整体范式跟 GP 路线竞争而非互补。"经济蒸馏"风格的辅助分析可以在 v2 evaluator 时考虑(轻量)。
- **批判性意见**:① 测试期 1965-2016 与当代差异很大,Sharpe 2.0 在 1990-2000 早期最高(图 4 趋势),近 10 年(2008-2016)Sharpe 已经接近 1.5,所以"OOS Sharpe 2"并不是稳态结果;② "经济蒸馏"中最重要的特征(ivc, free_cf, delta_so, Q)全是基本面,**v1 量价空间根本不可能复制**;③ 论文用 51 类特征,我们 v1 只有 6 字段。在如此大特征空间下取得的结果不能线性外推到稀疏特征环境;④ CAAN+TE 的训练成本远超我们 RTX 4060 Ti 的舒适区——单 GPU 训练全美股 + 27 年数据需要分布式并行(论文用 4× 1080Ti);⑤ "Economic Distillation" 实际上只是 Lasso + 二次特征,没有论文宣称的那么神秘——但确实是合理的可解释性工具;⑥ A 股可比性存疑(美股长样本含 OTC/小盘),A 股 IPO/退市/ST 节奏完全不同。
- **关键页码/引用**:Fig 1(整体架构);Fig 3(CAAN 结构);Table 1(主表 OOS Sharpe);Tables 9-12(扩展目标 transaction cost / fund survival / 经理人薪酬);Table 15 + Fig 5(主导特征及其轮转);§5.1(polynomial sensitivity 算法);Appendix A(RL 基础)。

### 数据补足收益暴增.pdf
- **标题 / 作者 / 年份 / 来源**:Missing Financial Data / Bryzgalova, Lerner, Lettau, Pelger(LBS/Stanford/Berkeley)/ 2024 / 工作论文(中文标题"收益暴增"是收录者夸张概括,原文核心是"缺失数据补足方法 + 对资产定价的影响")
- **一句话核心贡献**:系统性地证明了"基本面数据缺失"在美股中无处不在(>70% 公司任意时点至少有 1 个特征缺失,占总市值 50%),且**缺失不是 missing-at-random**;提出 B-XS / BF-XS 两个补缺方法(因子模型 + 时序),OOS 误差比 median 补缺低 50%,显著纠正资产定价偏差。
- **方法与主要发现**:① 文献综述了 45 个常用特征的缺失情况,刻画了"哪个特征缺失多/何时缺失/缺失是否系统化";② 提出 Local/Global B-XS 模型:对每个时点 t,用截面 PCA(带 ridge)在 partially observed 矩阵上估计 K=20 个因子,再用 AR(1) 模型补残差;③ 对比 median/industry median/last value/EM/Freyberger 等 baseline,B-XS 全面胜出;④ 在资产定价上,用 IPCA 在 imputed 全样本 vs fully observed 子样本上估计,前者 Sharpe 显著更高(选择偏差);⑤ median 补缺导致 factor-mimicking portfolio 严重失真,而 B-XS 几乎完全重建真实 portfolio。
- **数据与市场背景**:**美股**(CRSP+Compustat),1967-2020,45 个特征(价值/动量/盈利/intangibles/交易摩擦)。Rank-normalized,把每个特征转 0.5 中心化 quantile。
- **与当前系统的关系**:**数据层(直接相关)+ evaluator 层(选择偏差)**。这是 27 篇里**少数对 v1 直接有用**的一篇。具体关系:① v1 用 PITDataProvider,delist 后强制 NaN,这是"诚实地承认缺失",但 evaluator 在算 IC 时遇到 NaN 怎么办?(直接 drop NaN 行 = 选择偏差,正是这篇文章警告的);② 论文 §5.1.3 显示"要求多个特征同时观测"会大幅扭曲 decile sort 的 Sharpe ratio,直接对应到我们 GP fitness 里"要求多个 lookback 窗口同时有效"的影子;③ 论文 §5.2 的 median 补缺扭曲 cross-sectional regression 的 risk premium,对应到 v1 我们如果在 IC 计算前做 cross-sectional median fill,会引入相同 bias。
- **具体 actionable 点**:① **v1 evaluator 设计的一条铁律**:GP 因子算 IC 时,**不要做隐式 median fill**(包括把 NaN 替换为 0、用 cross-sectional median 填充等),要么 listwise-drop NaN,要么明确文档化所用的 imputation 方法并评估其偏差;② 写一个 governance test:对 csi300 的某一天,故意 mask 一些股票的某个字段,验证 evaluator 不会因此对该因子的 IC 给出乐观估计;③ 概念性:在 fitness 函数里加一个"覆盖率惩罚"(coverage penalty),避免 GP 优化出"只在小子样本表现好但 universe 覆盖率很低"的因子——这其实已经在 design doc §5.1 的 validity filters 中规划了;④ 论文 §3 的 B-XS 算法本身,对 v1 量价(6 字段)用处不大(那些字段本来就高度共变,缺失只发生在 entity 边界),但**v2 接入基本面后强相关**——可以直接采用 B-XS 补 P/E、P/B、ROE 等季频特征。
- **采用的前提条件**:① 现在(v1)就可以做的:在 evaluator/fitness 里明确处理 NaN 的策略文档化、在 governance test 里加入 selection-bias 测试;② v2 接入基本面后,可以引入 B-XS 算法补季频特征;③ 整套 IPCA-on-imputed-vs-fully-observed 的实证检验需要"先有 mined factor 库 + 多因子组合"——v1 单因子阶段还用不到。
- **你的初步归类建议**:【**现在(防御性使用) + v2(B-XS 算法落地)**】—— 现在就该把"避免隐式 median fill"写入 evaluator 的设计原则;B-XS 算法本身留待 v2 基本面接入时再落地。
- **批判性意见**:① 论文针对美股 CRSP/Compustat 的缺失模式,A 股有自己的缺失模式(停牌/ST/退市重组合并),不一定 1:1 适用;② 美股 45 个特征里的缺失率(70%+ 任意时点至少 1 个缺失)主要是基本面/历史窗口造成的,v1 纯量价 6 字段下,缺失率会**显著低**——这篇文章对 v1 的"实证 magnitude"会被高估;③ B-XS 用 PCA + ridge,需要选择 K=20 和 γ 两个超参,作者在论文里用 cross-validation 选,但 A 股做这个需要重新调;④ 论文里"median 比 B-XS 差一倍 OOS RMSE"是在"masking-then-evaluate"的实验设计下,真实场景中 ground truth 不可观测,误差大小可能被低估或高估;⑤ 论文不区分 PIT vs 非 PIT 数据,我们的项目已经在 PIT 层做了很多工作,部分"缺失"在 PIT 层已经被处理(delist 后 NaN);剩下的"中间缺失"才是真正需要 B-XS 处理的——边界容易混淆。
- **关键页码/引用**:Fig 1 panel F(70% 缺失率主要 finding);Table 3(B-XS vs median OOS RMSE);§2.1 Definition 1(Local XS Factor Model 算法);§5.1.3 + Fig 15(对 decile sort 的影响,Sharpe 扭曲);Fig 14(IPCA + 选择偏差对 Sharpe 的影响);Fig 16(median 补缺让 factor-mimicking portfolio 失真)。

### 自动挖因子1.pdf
- **标题 / 作者 / 年份 / 来源**:Generating Synergistic Formulaic Alpha Collections via Reinforcement Learning(俗称 **AlphaGen**)/ Yu, Xue, Ao, Pan, He, Tu, He(中科院计算所 + 华为)/ KDD 2023 / arXiv:2306.12964
- **一句话核心贡献**:**用 RL(PPO + LSTM)** 直接挖一组协同(synergistic)的公式因子,**reward 不是单因子 IC,而是新因子加入后线性组合"mega-alpha"的 IC 增量**——这与传统"先挖大量因子再用 mutual IC 过滤"的范式根本不同。
- **方法与主要发现**:① 公式用 RPN(逆波兰)token 序列表示,LSTM 自回归生成,invalid action masking 保证合法;② token 集合包括算子(Add/Log/TS-Mean/Cov/Corr 等 ~22 个)+ 特征(open/high/low/close/volume/vwap)+ 常数(±0.5/1/2/5/10/30 等)+ 时间窗(10d/20d/30d/40d/50d);③ Algorithm 1 维护一个 alpha pool(size G,实验中 10-100),每次新因子加入后重新训练线性组合权重,丢弃最小权因子;④ Algorithm 2:RL 训练循环,每步将新因子加入 pool 算 mega-alpha 的 IC 作为 episode 回报。**关键论点(§4.3)**:两个 mutual IC 0.97 的因子(被传统方法判为"重复")在线性组合后仍可能贡献显著增量 IC,因此 mutual IC 过滤是错的;⑤ CSI300 上 IC 0.0725 / Rank IC 0.0806,CSI500 上 IC 0.0438 / Rank IC 0.0727(pool size 选 10-100 中最优),显著优于 GP/PPO_top/PPO_filter/XGBoost/LightGBM/MLP。
- **数据与市场背景**:**A 股**(CSI300 + CSI500),数据来自 baostock(前复权),**6 字段:open/high/low/close/volume/vwap**——**与 v1 几乎一致**(我们多了 $money,他们多了 $vwap;但 v1 里 $vwap 可以由 $money/$volume 派生)。训练 2009-2018,验证 2019,测试 2020-2021。Label:Ref($close, -20)/$close - 1(20 日 close-to-close 远期收益)。
- **与当前系统的关系**:**这是 v1 路线的最直接对照实验**——任务/数据/字段都极接近。但搜索算法不同(他们 RL,我们 GP)、组合策略不同(他们一边挖一边组合,我们 v1 先挖单因子再交给训练 pipeline)。核心相关层:**搜索算法层(GP/RL 二选一)+ fitness 设计层(单因子 IC vs 组合增量 IC)**。
- **具体 actionable 点**:① **operator set 对齐**:他们的 22 个算子 + 我们 design doc §5.1 的算子高度重合,可以借鉴 token set 的设计(特别是 Greater/Less/Mad/WMA/EMA 这些 v1 没列的可能值得加);② **fitness 设计的重大启示**——他们证明 mutual IC 过滤不可靠,推荐用"组合增量 IC"。**但这对 v1 不直接适用**:v1 决策(`decisions.md` D4)是"手动晋级",mining 阶段只挖单因子;v2 才到多因子组合层。所以这条信息**记下来留给 v2**;③ **PPO + LSTM + invalid action masking + RPN** 是一个完整的 RL 替代实现路线——可以作为 v3 alternative architecture 的参考,但显著超出 v1 范围;④ **测试期 + label 选择**对 v1 有直接借鉴价值:他们用 20 日 close-to-close 远期收益作 label,与 v1 forward_return 的 5 日/20 日选择基本一致;⑤ **case study(Table 3 的 10 因子组合)** 显示 0.9746 的高 mutual IC 因子组合后仍贡献 IC 0.0458 ——这条对 v1 GP fitness 中的"correlation niche penalty"是一个**反例警告**:我们 v1 design doc §4.3 的"diversity = hash dedup + correlation niche penalty"可能把太多有用的"近似但互补"因子误杀。需要在 Phase 3 设计 GP 多样性机制时认真考虑这个反例。
- **采用的前提条件**:① 整套范式(RL+组合增量 reward)需要"先有组合层",而我们 v1 phase 1-3 还在打基础;② operator set 借鉴可以**现在就做**(写入 Phase 1 算子库设计);③ "组合增量 fitness"留待 v2 多因子阶段;④ "重新审视 correlation niche penalty"应在 Phase 3 GP 设计前讨论。
- **你的初步归类建议**:【**现在(算子集 + 对 correlation niche penalty 的警觉)+ v2(组合增量 fitness 概念)+ v3(RL 替代实现)**】—— 这是 27 篇里**与 v1 最相关的一篇之一**。
- **批判性意见**:① 论文**没用 PIT 数据**——用的是 baostock 前复权,即"以今天的 adj_factor 重算历史",存在 survivorship bias(回测期内已退市的股票根本没出现)。我们 PIT v1 的 IC 一定会**低于他们的 0.0725**,但是真实的;② "组合增量 IC > 单因子 IC + mutual IC 过滤"的论点是基于他们的线性组合 + 一个特定 pool size 的实验,**没有证明 GP+correlation penalty 在我们 v1 设定下会失败**。换言之,他们的结论不能直接否定 v1 的"diversity = corr penalty"设计——但提醒我们这条 niche 实在不够 robust;③ pool size 实验显示 size > 20 后 IC 提升放缓(Fig 4),论文宣称"scalable",但实际上 50 → 100 的提升很小,有点过度宣传;④ 测试期 2020-2021 含 2020 新冠暴跌 + 2021 抱团股行情,这两年 A 股风格变化很大,结果未必能外推到 2022 后;⑤ "PPO_top/PPO_filter 跑分极差(IC 接近 0)" 这种 baseline 看起来像是没调好,真实 GP 是这两年最强的 baseline 之一(华泰金工系列),不应被 PPO baseline 描述代表。
- **关键页码/引用**:Fig 2(整体架构);Algorithm 1(组合增量优化);Algorithm 2(RL 训练循环);Table 1(token 设计);Table 3 + §4.3(0.97 mutual IC 因子组合后增量 IC 的反例,**关键警示**);Fig 4(pool size 实验);Appendix A(完整算子表);Appendix C(RPN 合法性检查)。

### 自动挖因子2.pdf
- **标题 / 作者 / 年份 / 来源**:AlphaForge: A Framework to Mine and Dynamically Combine Formulaic Alpha Factors / Shi, Song, Zhang, Shi, Luo, Ao, Arian, Seco(中科院 + York + Toronto)/ AAAI 2025 / arXiv:2406.18394
- **一句话核心贡献**:在 AlphaGen 基础上做两个改进——① 用**生成-预测网络(generative-predictive)**替换 RL 做因子搜索(更高效的梯度搜索);② 引入**动态因子组合**——每个交易日按最近 ICIR 重新筛选 + 重新加权 mega-alpha 的成分因子("factor timing")。
- **方法与主要发现**:① 挖因子阶段:训练一个 Predictor 网络 P 拟合"公式 → fitness score"映射,再训练 Generator 网络 G 最大化 P(G(z)) 输出——通过 Gumbel-softmax 让公式 onehot 可微,在稀疏 fitness 景观下用梯度做搜索。fitness 包含三组要求:IC、ICIR、与现有因子的相关性上界 CORR';② 因子库 Z 一旦冻结,**每日**重新评估 Z 中每个因子的滚动 IC/ICIR,挑选前 N 个,用最近 n 天数据拟合线性回归权重,生成当日 mega-alpha;③ CSI300 上 IC 0.044 / Rank IC 0.059,CSI500 上 IC 0.028 / Rank IC 0.056,**优于 AlphaGen 的 static 版本**;④ Pool size 实验显示**最优 pool size = 10**,再大反而下降(因为同时只有 ~10 个因子在当下"有效");⑤ 真实账户(2024 起 9 个月,300 万 RMB,CSI500 标的)产生 21.68% 超额收益。
- **数据与市场背景**:**A 股**(CSI300 + CSI500),**6 字段同 AlphaGen**。训练区间 2010 起,每年用新数据**滚动重训练**(2018-2022 共 5 个测试年,每年用前一年验证)。Label:**Ref($vwap, -21)/Ref($vwap, -1) - 1**(T+1 至 T+21 vwap-to-vwap 远期收益,更贴近实战因为开盘日才能交易)。
- **与当前系统的关系**:与 AlphaGen 关系相同——任务/数据/字段都接近 v1,但搜索算法不同(他们 generative-predictive,我们 GP),组合策略不同(他们动态时变,我们 v1 没组合层)。核心相关层:**搜索算法层 + 组合层(v2)+ 评估方法学(滚动训练)**。
- **具体 actionable 点**:① **滚动训练设定**对 v1 walk_forward 模块直接相关——他们每年用前一年作验证,5 年测试,完全符合 walk-forward 设计。可以借鉴他们的"年度重训"作为 v1 walk_forward 的标准 schedule;② **label 选择(vwap-to-vwap, 20 天)**比 close-to-close 更贴近实战(因为我们买不到收盘价,T+1 vwap 才是真实成交价)——v1 forward_return 的实现应该提供两种选项,vwap-based 是更稳妥的默认;③ **"pool size 最优 = 10"** 这一发现很有价值——AlphaGen 说越多越好,AlphaForge 反驳说大池子里同时有效的只是少数。**对 v1 的启示**:即使我们最终挖出几百个因子,实际用于训练 pipeline 的可能只是其中 10-50 个,需要在 evaluator/promotion 层做严格筛选;④ **case study(Tables 2/3)**显示同一因子在 day 1 权重 -0.00014, day 2 权重 +0.00168,**符号变了**——说明因子有效性时变性很强。这对 v1 的 fitness 设计是个警示:单期 IC 评估可能高估 long-term 的 ICIR 稳定性;⑤ **生成-预测网络范式**作为 GP 的可比对照,留作 v3 alternative;⑥ **真实账户结果**(top 部分 of Fig 3)虽然只有 9 个月,但作为方法可行性的现实证据,有说服力。
- **采用的前提条件**:① 现在(v1)就可借鉴:label 选择(vwap)、walk-forward 标准化(年度重训)、对"pool size 最优远小于 mining 输出"的认知;② v2 多因子组合阶段:动态时变权重(daily re-ranking);③ v3 替代搜索算法。
- **你的初步归类建议**:【**现在(label 设计 + walk-forward 标准化)+ v2(动态组合)+ v3(generative-predictive 搜索)**】—— 与 AlphaGen 并列,是 27 篇里**与 v1 最相关的一篇之一**。
- **批判性意见**:① **同样没用 PIT 数据**——baostock 前复权,survivorship 偏差严重。他们的 IC 0.044 在我们 PIT 设定下会更低但更真实;② 真实账户 21.68% 超额(2024)+ 仅 9 个月 + 单只账户 = **样本太小不足以证明长期 alpha**,可能是回测期 + 风格切换的运气。这种"我有真钱回测"的论述方法在 A 股研究界常见,但应警惕;③ "factor timing" 看似先进,实际上是**因子轮动 / 因子时变权重**,这套理论 60 年代就有,这里只是给它套上深度学习外壳。本质创新不大;④ Pool size 实验只对比了 1/10/20/50/100,粒度太粗,"最优是 10"未必稳健;⑤ Generator-Predictor 范式可能存在 Predictor 过拟合的隐患,论文未做充分消融;⑥ 测试期 2018-2022 含 2018 大跌 + 2020 疫情 + 2021 抱团 + 2022 跌,他们 5 年回测有 ~50% 累计超额,这意味着 ~8.5%/年超额——在 A 股量化领域算中等水平,不是论文标题"暴增"那种。
- **关键页码/引用**:Fig 1(整体架构);Algorithm 1(generative-predictive 因子挖掘);Algorithm 2(daily 动态组合);Fig 2(pool size 实验,"最优 10");Tables 2/3(因子权重日间变化);Fig 3 top(真实账户结果);label 选择(§ Experiments settings,"Ref(VWAP, -21)/Ref(VWAP, -1) - 1")。

### 自动挖因子3.mht(已转 txt,实际是 GitHub README 而非论文)
- **标题 / 作者 / 年份 / 来源**:**alpha-gfn**: Mining formulaic alpha factors with generative flow networks / Ning Shen / 2024 / GitHub demo(项目地址 github.com/nshen7/alpha-gfn,引用了 2025 跟进论文 *AlphaSAGE: Structure-Aware Alpha Mining via GFlowNets for Robust Exploration*)
- **一句话核心贡献**:用 **GFlowNet(生成流网络)**作为公式因子的搜索算法,目标是**采样到一个高 reward 因子分布(而不是单个最优解)**,以保证多样性。
- **方法与主要发现**:① 把公式生成建模为 GFlowNet:trajectory 从 BEG token 开始,每步采样一个 token(算子/特征),终态是完整公式(RPN);② reward = squared IC × (1 - NaN%);③ trajectory balance loss(轨迹平衡损失);④ policy 网络:LSTM + 位置编码;⑤ 数据:S&P 500 2018-2019(2 年小 demo),5 个字段(open/high/low/close/volume);⑥ 算子集很小:abs/log/roll_std(unary), add/subtract/multiply/divide/roll_corr(binary);⑦ README 强调"NDA limited,仅 demo"——没有完整实证结果。
- **数据与市场背景**:**美股 S&P500**,小样本 2018-2019。但思想可迁移。
- **与当前系统的关系**:**搜索算法层**,与 GP / RL 并列的另一种探索策略(GFlowNet 的关键卖点是"按 reward 比例采样而非单点优化",天然提供多样性)。
- **具体 actionable 点**:① **概念性启示**——GFlowNet 是"按 reward 概率采样多样化样本"的工具,如果 v1 GP 的"correlation niche penalty"效果不好(参见 AlphaGen 的反例警告),GFlowNet 可以作为 v3 的备选搜索算法;② **AlphaSAGE 2025 论文**(README 引用)看起来是更正式的学术发表,如果未来需要深入研究 GFlowNet 路线,应该读那篇而非这个 demo README。
- **采用的前提条件**:① 整套 GFlowNet 实现需要 PyTorch + 训练栈,远超 v1 范围;② AlphaSAGE 论文如果可获取,应纳入下一轮文献调研。
- **你的初步归类建议**:【**v3 或不适用**】—— 当前 v1 已选 GP,GFlowNet 是 v3 替代方案。文档质量是 demo 级别,不是严肃实证。
- **批判性意见**:① **这不是论文,是 GitHub README**——收录者把它放进"自动挖因子"系列里给人误导。质量、严肃性、可信度都远低于另外两篇;② demo 用 S&P500 2 年数据,**完全不能证明方法的实际有效性**,只能说明"代码跑起来了";③ 算子集太小(unary 3 个 + binary 5 个),公式空间被严重压缩,无法直接对标 AlphaGen/AlphaForge;④ GFlowNet 的多样性优势在概念上吸引人,但代价是收敛速度慢 + 训练不稳——A 股低信噪比下能否落地存疑;⑤ 既然作者已经把这个项目升级成 2025 AAAI/KDD 的"AlphaSAGE",应该直接看那篇论文,这个 README 只有"路线指引"价值;⑥ 收录到论文清单里有点凑数,真正的研究价值偏低。
- **关键页码/引用**:整个 README 都是介绍性内容;关键概念在"What are GFlowNet models?"和"Reward"两节;引用 [6](Yu et al. 2023 = AlphaGen)说明这个项目本身就是受 AlphaGen 启发。最有价值的信息是引用了 AlphaSAGE 2025 paper,值得后续追踪。

---

## 英文论文(arXiv + SSRN)

### ssrn-2528780.pdf
- **标题 / 作者 / 年份 / 来源**:Lucky Factors / Campbell R. Harvey & Yan Liu(Duke + Purdue)/ 2019 / SSRN(早期版叫"How many factors?" / "Incremental factors")
- **一句话核心贡献**:提出一套**强制 null 假设的 bootstrap 多重检验框架**用于筛选 cross-sectional 风险因子,在 panel regression(个股而非 portfolio)中可以可靠回答"在已有 K 个因子之外,新因子是否真的增量贡献了 cross-section 的解释力"。
- **方法与主要发现**:① 把 augmented model M⁺ 在 baseline model M 下的 null 强加到样本上(把候选因子的 risk premium 完全 attribute 给已有因子)再做 bootstrap → 自然控制 family-wise error rate;② 个股(N>T 也可)直接做 panel,**绕开任意的 portfolio 排序**;③ 用绝对截距(mispricing)缩减量作为 test stat,而不是用 GRS 那套需要协方差矩阵估计的;④ 实证发现:market factor 是个股层面降低 mispricing 最关键的因子,SMB/HML 二阶;market 在 portfolio sort 上"被挤掉"是 GRS 框架的产物,不是经济结论;⑤ 用一个荒诞例子(把 Berkshire Hathaway 当因子,1965-2019 年化 20%)说明 high-Sharpe 因子选择技术容易把 BRK 误判为 pricing factor。
- **数据与市场背景**:**美股**,CRSP+Compustat。月频。Time-series long-short factor returns(MKT/SMB/HML/RMW/CMA/MOM 等)+ 个股 panel。**不需要基本面**作为输入(用既有 factor returns + 候选因子 returns),但实证里候选因子大都基于基本面。
- **与当前系统的关系**:**evaluator / 多重检验 / promotion 层** —— 这是方法论论文,不是因子论文。直接对应到 v1 GP mining 中的两个问题:① 25k 评估/run × 多 run 后,多重检验问题严重(p-hacking 风险);② 单因子 IC 显著不等于"对组合有增量贡献",这条与 AlphaGen(自动挖因子1)的"组合增量 IC > 单因子 IC + mutual IC 过滤"是**理论与实证两端的同一发现**。
- **具体 actionable 点**:① **v1 evaluator 加一个 bootstrap 多重检验 gate**——挖出的因子在 OOS 上做"打乱后 IC 分布"的 bootstrap test,只保留显著高于 null 分布的;② Phase 6 promotion gate 可借鉴 Harvey-Liu 的"强制 null"思路:候选因子 reduce 已有因子 alpha 的能力,在 bootstrap 下显著才晋级;③ Section 2.3 / E.2 的 stepwise forward selection 可作为多因子组合(v2)的因子加入算法基础,但 v1 单因子阶段用不到;④ "panel + individual stocks"的 panel regression 框架对 v1 的 SignalAnalyzer 现有路径(也是个股 panel)是兼容的。
- **采用的前提条件**:① bootstrap 框架本身需要在 evaluator 写一个 resample 实现,但因为只是用已有 panel 数据 resample,**v1 阶段就能做**;② Section 2 中的"强制 null"对 panel regression 来说是把候选因子对预定因子做正交化后再 panel 回归,实现简单;③ Section E.4 的"factor model misspecification"讨论需要在我们假设"v1 baseline 是某个简单 benchmark(如 MKT)"时考虑。
- **你的初步归类建议**:【**现在(bootstrap 多重检验思路)+ Phase 6(promotion gate 借鉴)**】—— 是 27 篇里**少数对 v1 evaluator 直接有用的方法论文**。
- **批判性意见**:① 论文核心实证在 US/portfolio 沟通逻辑,A 股 panel 上"market factor 是否同样降低 mispricing"未必成立(A 股 beta 因子在长 sample 上常被"反向"——大盘股小盘股节奏不同);② "强制 null + bootstrap"对**多 nested test**(我们 GP 每轮挑 top K 进入下一代)的 p-value 复合性问题没有彻底解决,§E.2 自己承认 overall p-value 是"个步骤 p-value 的复杂函数";③ 论文的 individual-stock panel 假设 fixed loading,A 股有显著时变 beta,可能高估稳定性;④ 实证用因子是 MKT/SMB/HML 等几个 published factor,**没有处理"GP 在 25k 候选里挑出的 top 因子"这种海量重复测试场景**,我们 v1 应用时要再扩展;⑤ 文章长 99 页,真正 v1 阶段有用的只有 §2 的方法部分,余下大量篇幅是 GRS 对比 + 仿真。
- **关键页码/引用**:Abstract;Page 1-2(BRK 反例,体现 high-Sharpe 不等于 pricing factor);§2(panel + bootstrap 主方法);§2.3(Fama-MacBeth adaptation);§E.2(stepwise selection 的多重检验复杂性,**重要 caveat**);§F FAQ(对常见反对意见的回答)。

### ssrn-3604626.pdf
- **标题 / 作者 / 年份 / 来源**:Open Source Cross-Sectional Asset Pricing / Andrew Y. Chen & Tom Zimmermann(Fed + Cologne)/ 2021 / SSRN(openassetpricing.com 数据集论文)
- **一句话核心贡献**:用统一公开代码复现学术文献的 319 个 cross-sectional 股票收益预测因子(来自 153 篇 paper),提供 reproducibility evidence + 开源数据 / 代码(WRDS + Stata)。
- **方法与主要发现**:① 把 319 个 firm-level characteristics 按其原文证据强弱分成 4 类:clear predictors(161)、likely(44)、indirect/HXZ variants(100)、not-predictors(14);② clear 类 161 个中 158 个达到 |t|>1.96,1 个 t=1.93,只 3 个真失败;② 复现 t-stat 对原文 t-stat 回归 slope=0.88, R²=82% —— 量化上对齐;③ 直接 contradict Hou-Xue-Zhang (2020) 的"50% anomalies 不可复现"——论点是 HXZ 把不同 rebalance 频率算成不同 anomaly,且把 borderline 的 likely 类当 clear 测;④ "post-publication decay 存在但仍正"复制 McLean-Pontiff (2016) 结果;⑤ 应用 NYSE 20th percentile 流动性筛选后,return 降 ~30%(20 bps/month)。
- **数据与市场背景**:**美股**(CRSP+Compustat),月度。**319 个特征绝大多数是基本面**(accruals, profitability, B/M, value, intangibles, distress);只有少量量价/microstructure 类(momentum, reversal, idiosyncratic vol, illiquidity, beta, size)。
- **与当前系统的关系**:**数据层 + evaluator + GP 种群初始化**。这是 27 篇里**对系统设计影响最广**的一篇,因为它给了一个 319 因子的 reference benchmark。
- **具体 actionable 点**:① **GP 种群 seed**:把 319 因子中的量价/microstructure 子集(estimated 30-40 个,如 mom_*/STREV/MaxRet/IVOL/Skew/Illiquidity/Amihud/zerotrade/Vol/BAVS 等)翻译成 v1 算子表达式,作为 Phase 3 GP 的初始种群(显著降低盲搜索);② **novelty / correlation baseline**:用 319 因子库整体作为"已知因子空间",mined 新因子要与该库 max correlation < 阈值才被 promotion 接受(D4 的"max correlation with existing < 0.6"现在有了具体的 existing 池);③ **monotonicity(decile sort 收益单调性)** 作为 evaluator 辅助质量指标——这是 §4 / Fig 2 的核心 finding,可避免 GP 出"非线性峰谷"的伪因子;④ 论文 §5.2 显示"NYSE 20% 流动性筛选 -30% mean return"是 A 股 csi300/500 universe 设计的可比经验值。
- **采用的前提条件**:① 量价/microstructure 子集**现在(v1)就能用**;② 完整 319 因子库需要 v2 接入基本面 + 行业数据;③ "monotonicity" 实现需要 evaluator 支持 decile sort(目前 IC/IR 是核心)——是 Phase 6 可加项。
- **你的初步归类建议**:【**现在(量价 seed + monotonicity 辅助指标)+ v2(完整 319 库做 novelty baseline)**】—— 高价值参考。
- **批判性意见**:① **绝大多数因子是美股基本面**,A 股 + 纯量价的 v1 用不了大部分,且美股因子直接搬到 A 股有效性存疑(投资者结构、卖空、T+1 制度差异显著);② 论文反复强调"reproduction" ≠ "implementable profit"——§7 引用 Chen-Velikov 表明加 bid-ask spread 后大部分 anomaly 收益消失,我们 v1 已有 30bps cost 处理,可比性需要核对;③ "monotonicity" 在 A 股小样本(csi300=300 只)上做 decile sort,每 decile 30 只,统计噪声大;④ 数据集是按 WRDS 路径构建的,需要 Compustat 子集——目前 v1 没有任何基本面 schema,即使想"翻译子集"也只能挑出量价类的几十个;⑤ "98% clear predictor 都显著"听起来非常乐观,但 HXZ 主张的"经流动性 / value-weighting 严格化后失败率 65%"也有合理性——选择 reference benchmark 时不要 100% 信这一边。
- **关键页码/引用**:Abstract;Tables 2-4(各类 predictor 的复现结果);Fig 2(decile monotonicity);§4 + §5.2(流动性筛选影响);§7 limitations。

### ssrn-4106794.pdf
- **标题 / 作者 / 年份 / 来源**:Missing Financial Data I / Bryzgalova, Lerner, Lettau, Pelger(LBS + Stanford + Berkeley + NBER)/ 2024-07 / SSRN(同为 [数据补足收益暴增](#数据补足收益暴增.pdf) 的另一文件名拷贝)
- **一句话核心贡献**:**与 [数据补足收益暴增.pdf](#数据补足收益暴增.pdf) 重复**——同篇论文。系统证明 firm fundamentals 缺失普遍且非随机,提出 latent factor + AR(1) 时序组合的 imputation 方法(B-XS / BF-XS),证明其 50% 优于 median 补缺,并量化对 risk premium 估计 / decile sort / factor portfolio 的偏差影响。
- **数据与市场背景**:美股 CRSP+Compustat 1967-2020,22,630 stocks,45 个特征(B2M/EP/MOM/OP/INV 等),rank-normalized 到 [-0.5, 0.5]。
- **与当前系统的关系**:见 [数据补足收益暴增.pdf](#数据补足收益暴增.pdf) 的完整笔记。
- **具体 actionable 点**:见前述笔记。**重复文件,可与 `ssrn-5797502(1).pdf` 一并视为同一去重项**。建议人类决策是否删除 `ssrn-4106794.pdf` 或 `数据补足收益暴增.pdf` 中的一个,避免双份占用研究资源。
- **采用的前提条件**:见前述笔记。
- **你的初步归类建议**:【**现在(防御性 NaN 处理)+ v2(B-XS 算法)**】——同前。
- **批判性意见**:**关键发现:这是重复文件**(两份 PDF 内容相同,只是文件名不同)。已在中文段笔记中详细批判,不重复。
- **关键页码/引用**:同前。注:本文件是 SSRN 直接下载版,标题带 "I" 暗示可能有 "II" 续篇——这与中文标签的版本同 abstract / 同方法 / 同实证,确认为同一篇。

### ssrn-4339591.pdf
- **标题 / 作者 / 年份 / 来源**:Reversals and the Returns to Liquidity Provision / Dai, Medhat, Novy-Marx, Rizova(Dimensional + Rochester)/ 2023-11 / SSRN(Novy-Marx 系列)
- **一句话核心贡献**:**对短期反转(reversal)做了"剥离信息驱动效应"的清洁分解**——证明标准 REV 收益弱(31bps/m, t=1.68)是因为它同时空了 PEAD(盈利公告漂移)和 industry momentum;剥离这两项后的 IRRX(industry-relative + announcement-adjusted reversal)收益 108bps/m, t=9.35,且与流动性指标(波动率 / 换手率)有可预测的横截面关系。
- **方法与主要发现**:① REV 可分解为 IRRX 多头 + PEAD/IMOM 空头(R²=87%);② **波动率↑ → 反转更快更强**(microstructure inventory-risk 解释);③ **换手率↓ → 反转更持久**(inventory duration 解释);④ Figure 2 显示在 size 4 quintile(去掉 microcap 后)反转主要由 vol/turnover 驱动,与 size 几乎无关;⑤ 美股长样本(1973-2021)+ 海外验证。
- **数据与市场背景**:**美股**(CRSP+Compustat),日频/月频混合。需要的字段:price/return(标准)、industry classification(FF49)、earnings announcement dates(I/B/E/S 风格)、volatility(63d std of daily returns)、turnover(63d avg of shares-traded%)。
- **与当前系统的关系**:**v1 fitness / 算子层(直接相关)** + **特征解释层**。本文是经典的"经济直觉指导因子设计"——它给出了 reversal 在不同 liquidity 区间下的预期效应,而 v1 GP 在挖反转类因子时如果不知道这些,会产生**符号反复的不稳定 mined factor**。
- **具体 actionable 点**:① **v1 fitness 设计反转因子时需关注 PEAD/IMOM 污染**——但 v1 没有 announcement / industry data 来做严格的 REVX → IRRX,所以**"剥离" 这一步在 v1 做不到**,只能作为"为何 mined reversal 因子的 IC 总是不如预期"的解释;② **vol-conditional / turnover-conditional reversal** 是非常有抓手的算子表达——v1 算子库可以确保 `ts_corr(volatility, ts_pctchange($close, n))` 这种"vol × reversal"交互项可表达,作为 GP 搜索的核心子空间之一;③ **microstructure intuition** 应该写入 design doc 的"经济先验"小节,用来 sanity-check mined factor(若 mined 出"vol↑ reverse 更弱"的反向因子,大概率是过拟合);④ 论文 §3.1 微观结构论证可以指导 Phase 6 promotion 时的"经济意义评估"——human-in-the-loop 审核标准的一项。
- **采用的前提条件**:① 严格剥离 PEAD/IMOM 在 v1 做不到(无 announcement 时间表 / industry data);② Vol/turnover conditional 表达只需要 OHLCV——**v1 现在就能做**;③ 完整复现需要 industry data + I/B/E/S,**v2 + v3**;④ A 股需要重新验证——A 股市场结构(T+1, 单日涨跌停)对 microstructure 假设是显著扰动。
- **你的初步归类建议**:【**现在(vol/turnover-conditional reversal 算子组合 + 经济先验文档化)+ v2(IRRX 严格复现)**】—— 直接对 v1 算子库 / 经济先验有用。
- **批判性意见**:① 论文核心实证是**美股**,A 股 reversal 的微观结构机制不同:T+1 制度本身就改变了 inventory turnover 的时钟;A 股小市值过度反转有"流动性陷阱"色彩,与论文的 inventory-risk 解释非线性;② "需要 announcement data" 这条对中国市场难以高质量获得(A 股盈利预告体系 vs US earnings release 有 disclosure 时机/口径差异),所以"剥离 PEAD"这一关键步骤在 A 股不容易复制;③ Figure 1 / Table 1 的统计 t=9.35 看着很强,但 sample 50 年,A 股仅 ~20 年高质量数据,统计 power 上限明显低;④ 论文未做 turnover-vol 联合排序,所以"vol vs turnover 哪个更重要"的结论是边际/控制变量结果,不是真正独立——v1 GP 在这两个维度做交互应小心 multicollinearity;⑤ 反转的"vol → faster reversal" 在散户主导的 A 股或反向(散户在高 vol 时容易追涨杀跌,反转可能反而被吃掉),需要 OOS 验证。
- **关键页码/引用**:Table 1(REV vs REVX);Table 2(REV 分解,β_IRRX=0.76, β_PEAD=-0.54, β_IMOM=-0.53);Figure 1(vol/turnover 对 reversal 的 dynamic 影响);§3.1(微观结构理论);Figure 2(size/vol/turnover quintiles)。

### ssrn-4385668.pdf
- **标题 / 作者 / 年份 / 来源**:Can investor sentiment predict value premium in China? / Zhaohui Jiang, Keith Anderson, Dimitrios Stafylas(York 等)/ 2023 / SSRN
- **一句话核心贡献**:研究 A 股 value premium 的特殊性——**与发达市场相反,A 股大市值股票的 BM/EP/SP/CFP 普遍高于小市值**(因 A 股小市值有"壳价值"),并构建 PLS 组合 value factor + PCA 投资者情绪指数,发现"低情绪时 value 表现更好"(也与 Baker-Wurgler 2006 美股结论相反)。
- **方法与主要发现**:① 上海/深圳 A 股 2000-2021,21 年,~3000 只(2000 年 777 → 2021 年 3127);用 4 个 value factor(BM/CFP/EP/SP)做 portfolio sort 比较;② Table 1 显示 A 股**最大 decile 的 BM=1:2.47,最小 decile BM=1:3.68**,即大市值估值更"便宜"——这与美国/欧洲完全相反,原因是 A 股严格 IPO 审核 + 难退市制度形成的"shell value"(小市值=潜在借壳标的);③ 用 PLS 把 BM/EP/SP 三个 value 信号根据"短/中/长期 future return"的预测权重组合成新 value factor;④ 5 个 sentiment proxy(CCI/CEFD/NIA/NIPO/RIPO+TURN+VIX 等)做 PCA 得到月度 sentiment index,**低 sentiment 时 value premium 显著高于高 sentiment 时**——与 Baker-Wurgler 美股"高情绪时 growth 被泡沫化,value 受益"逻辑相反。
- **数据与市场背景**:**A 股**(SHSE-A + SZSE-A),2000-2021。CSMAR + Choice 数据库。需要基本面(EP/BM/CFP/SP)+ sentiment proxies(CCI/CEFD/NIA/NIPO 等)。剔除上市不足 1 个月,剔除 STAR + 第二板。
- **与当前系统的关系**:**经济先验层 + universe 选择层**——这篇不是因子方法论,而是 A 股"value 与发达市场反向"的经验证据。直接影响:① v1 在 csi300 universe 内(大部分大市值),"小市值更安全"的现象会反向影响纯量价 mean-reversion 因子的表现;② v1 没有基本面,**纯量价无法直接表达 value factor**,这篇的 PLS combination 思路 v2 才用得上;③ sentiment proxies 部分是量价(turnover, IPO return),v1 可以引入,但需要"全市场 turnover" / "上市次新股的当日 return"作为指标——目前 v1 数据层没有这层加工。
- **具体 actionable 点**:① **记入 design doc 的"经济先验"小节**:A 股 size effect 与 US/EU 反向("小市值更安全"由壳价值驱动)——v1 mining 出"小市值显著超额"的因子时,要警惕这是 shell-value premium 而非 alpha;② **PLS 组合多个相似 signal** 的方法学留作 v2 多因子组合的备选(用 future return loading 加权而非等权);③ **PCA-based sentiment index** 不直接可用,但其中**全市场 turnover** 作为 sentiment proxy 的思路,v1 可以借鉴——把 cs_zscore(market_avg_turnover) 之类的"宏观时变变量"作为 evaluator 的条件变量(regime-aware fitness 的雏形);④ A 股 value 与 sentiment 反向的实证,v1 不直接用,但若 v2 多因子组合做 sentiment timing,这是基础参考。
- **采用的前提条件**:① v1 阶段只能借鉴**经济先验**,纯量价做不出 value factor;② v2 接基本面后,PLS 组合可作为多 value-signal 整合的备选;③ sentiment proxies 涉及非纯量价数据(IPO 数量、新开户数等),需要专门数据采集 pipeline。
- **你的初步归类建议**:【**现在(经济先验文档化)+ v2(PLS combo + sentiment 条件)**】—— 主要价值是 A 股 anchoring。
- **批判性意见**:① 论文的核心实证(2000-2021)有 ~3000 只股票池,**没用 PIT 数据**——A 股 2000 年代有大量并购重组 / ST 退市 / 借壳,他们的 BM/EP/SP 算法依赖财务数据,if backward-look 用今天股票池,survivorship 巨大;② **shell value** 在 A 股 2018+ 退市新规 / 2019 注册制后**正在被显著削弱**——他们 2021 截止的数据 + 注册制效应可能没有充分体现,小市值"更安全"在 2022-2025 已经不再成立(2024 大量小市值退市);③ Sentiment proxies 多数没有公开高质量 PIT 来源(NIA/CCI 数据延迟、修订),实战不容易用;④ PLS combination 在小数据 + 高 multicollinearity 场景下 unstable,Sharpe / IR 增益的样本大小不算大;⑤ 论文章节较短(46 页含表格,主要内容 ~20 页),实证深度有限——多个声称的"超越"未经全面 robustness;⑥ 没讨论 size × value 交互的 regime 切换(2020 抱团 vs 2022 价值股回归),A 股 size-value 关系在不同 regime 下可能反复颠倒;⑦ 与现有量化项目"小盘+反转"的常识结合后,这篇结论是"小盘 ≠ 便宜",理解需要二次推断。
- **关键页码/引用**:§2.2 / Table 1(A 股大市值估值反而高的关键证据);§3(PLS combined value factor);§4(sentiment index 构建 + 与 value premium 的关系);§5(投资策略示例)。

### ssrn-4560216.pdf
- **标题 / 作者 / 年份 / 来源**:GPT's Idea of Stock Factors / Yuhan Cheng, Ke Tang(山东大学 + 清华)/ 2023-09 / SSRN
- **一句话核心贡献**:用 **GPT-4** 当"金融领域专家",输入数据 schema(open/close/volume 等列名),让 GPT-4 直接给出因子公式 + Python 代码,作者把生成的 35 个因子在美股回测,**单因子最高年化 66.16% / Sharpe 4.49**,组合后年化 88%。
- **方法与主要发现**:① 工作流:把数据列结构告诉 GPT-4 → 让它生成因子公式 + Python 代码 → 作者在本地跑 → 不让 GPT 看实际数据(避免泄漏 + 限算力);② 35 个因子,US CRSP,**测试期 2021-10 到 2022-12(只 15 个月!)** + 训练期 2000 至 2021-09;③ 单因子日频 long top-50% / short bottom-50%,30/35 正回报,**peak 因子年化 66.16% Sharpe 4.49**;④ 一半以上因子 FF3 / FF5 alpha 显著;⑤ Multi-factor portfolio:35 因子简单 model averaging 后年化 88% Sharpe 2.46;⑥ 论文用大量"magisterial"、"watershed moment"等修辞,**全篇有 LLM 辅助写作风格**。
- **数据与市场背景**:**美股**(CRSP),2000-2022。日频。GPT-4 训练 cutoff 2021-09 之前都是"in-distribution",2021-10 之后才是真正 OOS——但 OOS 只有 15 个月。
- **与当前系统的关系**:**搜索算法层(LLM-based factor 生成 vs GP / RL)** + **概念性**。这是 LLM 作为"知识库 + 公式生成器"的替代搜索路线,与 GP / RL 并列。
- **具体 actionable 点**:① **LLM-prompted factor 生成可以作为 v1 GP 的种群 seed 来源之一**——把 GPT-4 生成的几十个公式翻译到 v1 算子表达式(类似 ssrn-3604626 的 seed 思路),增加 GP 起始多样性;② **LLM 给出"经济解释"的能力是 GP 没有的**——可作为 Phase 6 promotion 阶段"human-in-the-loop 审核"的辅助工具,把 mined factor 输入 GPT 让它评估"经济合理性"——但绝不能作为 promotion 决策依据;③ **方法论复制成本极低**——本质就是 prompt engineering,但实证可信度也极低。
- **采用的前提条件**:① 若想引入 LLM 作为 seed 来源,只需 API key + prompt 模板,**v1 现在就能做**,但应严格地把 LLM 因子作为"待 GP 进一步搜索的候选起点"而非"成品因子";② "GPT 解释 mined factor 的经济意义"应纳入 promotion gate 的 human-aid 工具,不作决策代理;③ 整套"LLM 直接当 factor source"的论文范式建议不要轻信。
- **你的初步归类建议**:【**现在(GP seed 候选)+ Phase 6 工具(LLM 辅助经济解释)+ 不适用(论文核心实证范式)**】—— **方法本身可借鉴,但论文实证结论严重存疑**。
- **批判性意见**:① **Sharpe 4.49 在 15 个月样本里完全不可信**——年化 Sharpe 4.49 是顶尖文艺复兴级别,15 个月数据 + 35 因子里挑出来的 top 1 = 显著的 multi-test inflation。Harvey-Liu (ssrn-2528780) 那套多重检验框架直接 reject 这种发现;② **测试期 2021-10 到 2022-12 含 2022 美股深度下跌**,long-short 在剧烈 regime shift 时容易出现 sample-specific alpha,代表性极差;③ **GPT 训练数据可能见过这些因子的标准答案**——GPT-4 的训练 cutoff 是 2021-09,GPT 当然知道过去 60 年学术界发表的因子,所谓"创新"很可能只是已发表因子的同义改写。论文的"manual scrutiny to affirm novelty"非常可疑;④ **每天调仓 + 没有真实交易成本** = 收益严重高估;⑤ **写作风格本身就是 LLM** —— purple prose 满篇("magisterial", "watershed moment", "pioneering trajectory"),作者大概率把 GPT 输出直接当论文写;⑥ Cong/Tang 等清华系作者近年的 LLM 金融论文质量参差,这篇质量明显属于偏低端;⑦ **结论 not reproducible without replicating GPT-4 query**,且不同 prompt 给不同因子;⑧ 论文不区分 "未来不可能再有 alpha 因子"(LLM 之后 efficient market 自我修复)与"我跑出来的就是 alpha 因子"——其实如果 LLM 真这么有效,Citadel/RenTec 早就把它做掉了。
- **关键页码/引用**:Abstract;§2(prompt 工作流);§3(数据 + 因子选择);Table 1 / §4.1(单因子 Sharpe);§4.3(35 因子 model averaging);Appendix(35 个因子公式) - 这些公式本身可以扫一眼看是否值得 seed 给 v1 GP。

### ssrn-4702406.pdf
- **标题 / 作者 / 年份 / 来源**:The Expected Returns on Machine-Learning Strategies / Vitor Azevedo, Christopher Hoegner, Mihail Velikov(RPTU + TUM + Penn State)/ 2025 / SSRN
- **一句话核心贡献**:**系统量化 ML 因子策略在三种现实摩擦后的真实预期收益**——交易成本、post-publication 收益衰减、post-decimalization(后小数化时代)的市场流动性提升,**三者共同造成 ~57% Sharpe 衰减**;LSTM1 模型仍是唯一在所有摩擦后仍稳健显著的策略(net Sharpe 0.84,FF6 alpha 1.26%/月 t=3.24)。
- **方法与主要发现**:① 9 个 ML 模型:OLS-H, ENET, FFNN2-5, LSTM1/2, Ensemble;② 输入是 Chen-Zimmermann 2022 的 320 个 anomaly signals(即 [ssrn-3604626](#ssrn-3604626.pdf) 数据集);③ 美股 OOS 始于 2000 年;④ 序贯增加三种约束:**(a)** 限制 sample 到 post-2005(后小数化时代,流动性更强 ≈ alpha 更难)→ 平均 -11% Sharpe;**(b)** 限制 anomalies 只用"已发表"的 → 平均 -26% Sharpe;**(c)** 减去交易成本(Chen-Velikov effective spread,TAQ 高频数据估算)→ 平均 -15% Sharpe;⑤ 三者累加 → 57% 平均 Sharpe 衰减;⑥ **LSTM 抵抗摩擦最强**,LSTM1 net Sharpe 0.84,LSTM2 在 recession 月收益 7.69%/月;⑦ Cost mitigation:延长 holding period 到 2 个月仅提升 7 bps/月,效果有限;⑧ 提出 Lo (2004) "adaptive market" 衍生的理论模型:ML 能从 complex signal 中提取的 alpha 持续时间更长,因为简单 signal 已被套利掉。
- **数据与市场背景**:**美股**(CRSP+Compustat),1925-2022(主要 OOS 2000+);320 个 anomaly signals(基本面 + 量价混合)。
- **与当前系统的关系**:**evaluator + fitness + promotion gate(直接相关)**。这是 27 篇里**对"如何评估 mined factor 真实可用性"贡献最具体的一篇**,直接对 v1 的 30bps 成本设定 + 手动晋级流程提供量化支撑。
- **具体 actionable 点**:① **v1 fitness 中的 cost 项可以"分解检验"**——分别看 cost-only / publication-decay-only / liquidity-regime-only 三个版本的 IC,定位 mined factor 是哪种摩擦最敏感(有些因子是 high-turnover 易死,有些是 publication-decayed,处理策略不同);② **Holding period extension 测试**——论文显示延长 holding period 收益提升有限但有限边际正——v1 可以在 evaluator 阶段加一个"hold-1d vs hold-5d vs hold-21d"的 sensitivity check,**至少作为 Phase 3-5 的 OOS gate**;③ **Buy-Hold-Spread**(进入 top decile 才买,退出 quintile 才卖,带 hysteresis)是 Novy-Marx-Velikov 建议的实战策略——v1 fitness 评估时可以加入"是否带 BHS"两种 turnover 报告;④ **post-publication decay** 对 v1 直接相关:论文说 "publication 后 anomaly 平均收益降 58%"——这对应到 v1 GP mined 一个因子,要假设**真实预期 IC 至少打 5-7 折**才是 sustainable 估计——可以写入 Phase 6 promotion gate 的"预期收益降级估算"中(promotion 阈值 = IS IR × 0.5-0.7);⑤ **LSTM 优势源于"处理 complex / nonlinear signal"**——这条对 v1 GP 是反向警示:GP 公式深度 ≤ 4-5 的 mined factor 大多在 linear / shallow nonlinear 表达力上不及 LSTM,所以"GP 因子比 ML 模型简单"是 feature 不是 bug——它给出"interpretable + 抗摩擦 weaker but not zero"的 niche。
- **采用的前提条件**:① "分解检验"和"holding period sensitivity"**在 v1 Phase 6 validator 里就能加**,工程量不大;② BHS / publication-decay 的 5-7 折估算需要 Phase 6 promotion gate 的设计有"shrinkage"概念,这是 OpenSpec 提案的好题目;③ LSTM 类对比基线远超 v1 范围,不要走这条路。
- **你的初步归类建议**:【**现在 + Phase 6(分解检验 / shrinkage / holding period)**】—— **强相关,直接落入 v1 Phase 6 validator 的设计输入**。
- **批判性意见**:① 论文以**美股**为样本,A 股的 post-decimalization 类比是 2015-2020 量化资金涌入(实证上 A 股 momentum 因子 IC 在 2017-2020 急速衰减),具体衰减比例需要 A 股 OOS 测;② 用 Chen-Zimmermann 320 anomaly 作为 input space,**80% 是基本面**,LSTM 跑出的"top signals 是 Earnings Event / Cash Flow Risk / Valuation"——v1 纯量价场景下结论 not transferable;③ Cost model 是 Chen-Velikov effective spread(基于 TAQ),A 股没有同质数据,论文 25bps/trade flat assumption(Blitz 2023)更接近 v1 的 30bps;④ 论文的 ML 模型都是**black-box 拟合**——与 v1 white-box 公式因子是不同 niche,直接对比 Sharpe 不公平;⑤ LSTM1 在 recession 月收益 7-8%/月听起来非常诱人,但 recession 月样本量小(2008-09 + 2020-Q2),统计 noise 大;⑥ "技术扩散"理论模型(Section 2)是论文较薄弱的一部分,实证证明不充分。
- **关键页码/引用**:Abstract;§1(三项摩擦的累计 57% Sharpe 衰减,**关键 finding**);Table 7/8(各 ML 模型的 gross vs net Sharpe);§5(LSTM 在 recession 期的强表现);§6(cost mitigation 各方法对比);§2(adaptive market 理论框架)。

### ssrn-5080998.pdf
- **标题 / 作者 / 年份 / 来源**:Machine Learning for Pairs Trading: a Clustering-based Approach / Francesco Rotondi, Federico Russo(Bocconi)/ 2025-01 / SSRN
- **一句话核心贡献**:用**无监督聚类**(三种距离度量:Euclidean SSD / PCA-Euclidean / **partial-correlation distance**)在 S&P 500 上做 pairs trading,partial-correlation 度量效果最好,Sharpe 0.29/月(年化 ~1.0),excess return 36-41 bps/月,扣除 cost 后仍显著。
- **方法与主要发现**:① S&P500 成分股 2000-2023,1098 只(动态含已退出指数的),日频价格 + bid-ask;② 三种 distance metric → KMeans / hierarchical 聚类 → 每类内挑 pair → 标准 Gatev-style pairs trading(z-score 偏离阈值开仓,均值回归平仓);③ Partial correlation 距离的关键定义:`PC(X,Y) = 1 - |ρ(rX,rY|rM)|`,即去除 market 暴露后的纯相关;④ Purity index(以 SIC 行业为 ground truth)显示 partial-correlation 聚类的行业纯度最高,**说明 stock 聚类的主要驱动还是行业**;⑤ Partial-correlation 策略的 FF5+momentum+liquidity alpha 显著,monthly Sharpe 0.29 = 年化 ~1.0;⑥ Robustness/sensitivity 检验稳健。
- **数据与市场背景**:**美股**(S&P 500),2000-2023。日频。需要 bid-ask spread + delisting code + SIC 行业。**与 v1 没有直接关系**(v1 不做 pairs trading,做横截面因子)。
- **与当前系统的关系**:**搜索 / 配对策略层(不直接相关)**。这是另一种交易范式(stat arb 的 pairs)而非 cross-sectional factor mining。
- **具体 actionable 点**:① **partial correlation distance** 的定义本身有借鉴价值:`ρ(X,Y|M)` 去除市场暴露后看股票间剩余相关——这个思路可借鉴到 v1 evaluator 的"novelty / correlation niche"环节:**v1 在算 mined factor 与已知因子的 max correlation 时,可以先对 market factor 正交化(partial correlation),避免被 universal market exposure 掩盖真正的 idiosyncratic alpha 相关性**;② 其余整套 pairs trading 范式与 v1 无关,且 A 股 short 受限实操困难。
- **采用的前提条件**:① "partial correlation in novelty test"可在 v1 Phase 6 promotion gate 加入,工程量小;② pairs trading 整套范式与 v1 路线无关。
- **你的初步归类建议**:【**现在(partial correlation 用于 novelty test)+ 其余不适用**】—— **借鉴价值有限,只一处实用细节**。
- **批判性意见**:① **Pairs trading 与 cross-sectional factor 是平行 universe,论文核心结论与 v1 几乎无 overlap**;② Sharpe 1.0(年化)在 long-only equity 是中等水平,在 pairs trading 中也只算 OK——并非论文重点;③ 论文标题强调"machine learning"但其实用的就是 KMeans / hierarchical clustering,标题略 oversold;④ A 股 short selling 受限(融券标的少 + 成本高),pairs trading 实操困难,这一点论文未讨论;⑤ "partial correlation 度量行业纯度最高"等于在说"它学到了 SIC",并不真的优于直接用行业分类做硬约束——论文的"distance metric matters"实际上是 epi-phenomenon;⑥ 整体研究质量 ok 但 niche,**收入"量化论文"列表算偏题**。
- **关键页码/引用**:§3.1.1(三种 distance metric 定义,**partial correlation 公式可借鉴**);§4(回测结果);Table 5/6(各 metric 的 Sharpe / alpha 对比);§4.4 robustness。

### ssrn-5156605.pdf
- **标题 / 作者 / 年份 / 来源**:Volume Shocks and Overnight Returns / Álvaro Cartea, Mihai Cucuringu, Qi Jin, Mungo Wilson(Oxford-Man Institute)/ 2025 / SSRN
- **一句话核心贡献**:发现 **Volume Shock(成交量相对 EMA 偏离度)与"后续夜间收益"正相关,与"次日盘中收益"无关**——挑战了"成交量异常 = 投资者注意 → 推高股价"的标准解释;且证明这条关系在 size 各分组都稳健,机器学习预测 Volume Shock 后即使没有完美 close 价信息也可构造可执行策略(线性模型已达 perfect-foresight 90% Sharpe)。
- **方法与主要发现**:① Volume Shock = `Volume_t / EMA(Volume_{t-1}) - 1`;② US CRSP 2000-2022 日频,所有 NYSE/AMEX/Nasdaq 普通股;③ 按 Volume Shock 排序构建 equal-weighted / value-weighted 组合,**overnight(close→open)** 段显著正收益,**intraday(open→close)** 段几乎为零;④ 控制 FF 5/6 + UMD + reversal 后 alpha 仍显著;⑤ Fama-MacBeth + pooled regression 一致显示 overnight 正风险溢价,**intraday 反而显著负**(尽管 portfolio 层面接近零)—— 这是夜/日不对称性的核心发现;⑥ 用 linear regression / LightGBM / TabNet 预测 Volume Shock,linear 即可达到 perfect-foresight Sharpe 的 90%。
- **数据与市场背景**:**美股**,2000-2022。需要的字段:OHLCV + **open price**(夜间 / 盘中分离需要)+ 日级 close。**A 股有重要差异**:A 股有 T+1 制度,昨日成交量 → 当日开盘前 / 当日盘中的"夜间窗"等价物是"昨晚 close → 今晨集合竞价"——A 股集合竞价价格(`$open`)是有的,所以 overnight return 可以同等定义,但 A 股没有美股那样的盘前盘后交易,夜间信息扩散机制不同。
- **与当前系统的关系**:**算子层 + 因子设计**——这条findings 对 v1 GP 算子库直接相关。v1 已有 `$open / $close / $volume`,**完全可以构造 overnight vs intraday 分解**:overnight_ret = `$open / Ref($close, 1) - 1`,intraday_ret = `$close / $open - 1`。这是 v1 当前不充分利用的信号源。
- **具体 actionable 点**:① **v1 算子库的"派生量"清单加入 overnight / intraday return**——这两个量 v1 现有字段就能算,但目前 design doc §4.1 没明确列入。建议:在算子设计文档加入 `overnight_return` 和 `intraday_return` 作为 derived primitive(类似 `$vwap = $money/$volume`),让 GP 直接使用;② **Volume Shock 自身是非常强的 single-factor 候选**——v1 GP 可以构造 `cs_rank(Volume_t / ts_mean($volume, 20))` 类表达式,但 EMA 的 v1 算子里没有(只有 ts_mean)——加 `ts_ema(x, n)` 算子到 v1 算子库(§5.1 design doc)是低成本高回报的扩展;③ **v1 fitness 可考虑 overnight-conditional IC**——即把 mined factor 与 next overnight return / next intraday return 分别评 IC,如果差异大说明该因子主要在某个时段有效,这能更细粒度地理解 mined factor 性质(目前 forward_return 只看 close-to-close);④ **A 股 OOS 验证**:这条 finding 的稳健性需要重新在 A 股上验证(A 股集合竞价机制不同,且无盘前盘后交易,夜间信息扩散路径与美股有本质差异)——挖出这种因子前先做 sanity check。
- **采用的前提条件**:① overnight/intraday return primitive + EMA 算子需要在 v1 Phase 1 算子库设计时确认加入,**现在(Phase 1 还未完全冻结)就该 raise**;② "overnight-conditional IC"需要 evaluator 支持多种 forward_return label,工程量小;③ A 股 OOS 验证需要先有 mined factor 才有意义,所以是"OOS 时回头检验"。
- **你的初步归类建议**:【**现在(算子库加 overnight / intraday primitives + EMA + Volume Shock-style 表达)**】—— **直接落到 v1 Phase 1 设计**,执行难度低。
- **批判性意见**:① 论文核心数据是美股,**A 股 T+1 + 无盘前盘后 → overnight 与 intraday 信息流动机制完全不同**,论文结论可能完全不可复制甚至反向。A 股的"夜间"实际只有集合竞价那一刻信息汇聚,信息没有连续扩散的窗口;② Volume Shock 用 EMA 平滑——半衰期参数没充分敏感性测试,作者用 default 但 cross-section 上每只股票的 volume 时间序列性质差异巨大;③ **作者团队是 Oxford-Man Institute(同 Cucuringu 和 [ssrn-5797502](#ssrn-5797502.pdf) 团队)**,后者论文显示"capacity constraint 是主要限制"——Volume Shock 这种 high-turnover 策略恐怕首先被 capacity 限制掉;④ "linear model 已达 90% perfect-foresight Sharpe"——但 perfect-foresight Sharpe 本身就受 capacity 限制大,所以这 90% 是"无 capacity 假设下的 90%",实战不可信;⑤ 论文未讨论 Volume Shock 与 PEAD / news event 的混杂——成交量异常很可能就是"消息驱动",在 A 股就是"利好/利空公告"的影子,与 Dai-Medhat-Novy-Marx [ssrn-4339591](#ssrn-4339591.pdf) 的 news / PEAD 解释一致——论文挑战了"investor attention"解释,但没真正排除"news-driven"。
- **关键页码/引用**:Eq. (1)(Volume Shock 定义);§4(portfolio sort + FM regression 主结果);§5(linear / LightGBM / TabNet 预测模型);Table 4(各 cap 区间的 overnight 显著性);§6(executable strategy)。

### ssrn-5178543.pdf
- **标题 / 作者 / 年份 / 来源**:Profitability Retrospective: What Have We Learned? / Mamdouh Medhat & Robert Novy-Marx(Dimensional + Rochester/NBER)/ 2025-10 / SSRN(综合并扩展了作者之前的 "Quality investing" 和 "Understanding defensive equity"两稿)
- **一句话核心贡献**:**Profitability(盈利能力)单一因子 subsume 了整个"quality"投资空间**——academic 和 industry 用的所有 quality 度量(ROE / EPS stability / leverage / Q-score / F-score / O-score / G-score / low-beta / low-vol)在控制 profitability 后都失去显著 alpha;profitability 还解释了所有"alternative value"策略的超额、以及 value 因子 2007 后下跌一半的原因。
- **方法与主要发现**:① PROF = `(REVT - COGS - (XSGA - XRD) - XINT) / (BE + MIB)`,即一种"将 R&D 视为投资而非费用"的 broad operating profitability;② US 1974-2023,monthly;③ 13 种 quality 度量构造 HML 式因子,**只有 PROF 有显著三因子 alpha(52 bps/月 t=7.59)**,其他 quality 因子三因子 alpha 都不显著或被 PROF subsume;④ Spanning tests 显示 PROF 对 quality 因子 alpha 显著正,反向都不显著——PROF expansion of the investment opportunity set 的能力远超 quality;⑤ Defensive equity(low-beta, low-vol)的所谓"alpha"主要来自"low-beta / low-vol 股票偶然 tilt 到 high-profitability 股票",直接控制 profitability 后消失;⑥ Alternative value(净payout-adjusted、intangible-adjusted、composite value 等)在控制 profitability 后都失去显著 alpha—— "更好的 value"其实只是 "value + profitability tilt"的伪装;⑦ Value 因子 2007 后表现差,**有一半归因于 PROF 自 2013 发表后超额 ↑ × value 与 PROF 负相关 = value 被打折**。
- **数据与市场背景**:**美股**,1974-2023,monthly。需要完整 Compustat 基本面(REVT/COGS/XSGA/XRD/XINT/BE/MIB)+ 价格 + Fama-French 5 因子。
- **与当前系统的关系**:**经济先验层 + v2 多因子规划** —— 这是因子文献的"重要更新",但**对 v1 纯量价没有任何直接 actionable**:profitability 需要 Compustat 基本面,v1 一项都没有。
- **具体 actionable 点**:① **写入 design doc 的"v2 roadmap"**:接基本面后,**profitability 是优先级最高的单一因子**——不要"先做完一堆 quality 再用 PROF subsume",而是先做 PROF,看其他 quality 因子是否还需要;② **批判性 reminder**:Phase 6 promotion 时,如果出现"low-vol mined factor 的 alpha 很好",**不要因此 promote**——本文证明 low-vol 的 alpha 全来自隐式 profitability tilt,我们 v1 量价空间根本无法表达 profitability,所以"low-vol alpha"很可能就是 confounded by 不可表达的 factor。理性的做法是**在 promotion gate 加一条"low-vol 类 mined factor 需要额外 sanity check"**;③ 其余整套结论都是 v2+ 范围。
- **采用的前提条件**:① 全部前提是接入基本面数据(v2);② Phase 6 的"low-vol caveat"现在可以写入 promotion checklist。
- **你的初步归类建议**:【**v2(主体)+ 现在(promotion gate 的 low-vol caveat)**】—— 整体不适用于 v1 因子挖掘本身,但**对 v1 promotion / 解释的设计有警示**。
- **批判性意见**:① 论文核心是 PROF 的"subsume 论",但 PROF 自己也是 monthly 重平衡的、依赖会计数据(滞后 1-3 季度发布),A 股财务披露时点 + 财务数据质量(粉饰、关联交易、再融资动机)使 PROF 不一定能复制美股结论;② "value 1/2 underperformance 由 PROF 解释"是一种 ex-post 的 attribution,不一定意味着 value 因子已无 alpha——它可能是"被 PROF 暂时遮蔽,未来 regime 切换时再现";③ 论文 R&D-as-investment 的 PROF 定义不是标准 FF RMW,作者自家"slight modification"在小样本上看上去更好的部分原因可能是数据 mining;④ 论文不涉及交易成本 + capacity,所以与 [ssrn-5797502](#ssrn-5797502.pdf) 的 capacity-constrained 结论结合后,PROF 的实战可用性可能也受限于"PROF 主要靠 long leg 在 large-cap 上"——这反而是好的,但需要论文外的实证支撑;⑤ A 股 profitability 因子的有效性已有相当多研究(Liu-Stambaugh-Yuan 2019 等),不全是美股的镜像,我们 v2 不能直接照搬 PROF 定义。
- **关键页码/引用**:Table 1(13 种 quality 度量列表);Fig 1-2(quality 因子 alpha + PROF 跨距 alpha);§3(defensive equity 分解);§4(alternative value 分解 + value 后期表现归因);Appendix A.1(PROF 详细构造)。

### ssrn-5316487.pdf
- **标题 / 作者 / 年份 / 来源**:The Volatility Edge: A Dual Approach for VIX ETNs Trading / Carlo Zarattini, Antonio Mele, Andrew Aziz(Concretum + USI Lugano + Peak Capital)/ 2025-06 / SSRN
- **一句话核心贡献**:面向个人投资者展示如何用 **VIX-linked ETN**(如 VXX/SVXY 等)而非期权,构建短波动率 / 波动率风险溢价(VRP)收割策略;最终 dual-signal + dynamic sizing 版本 2008-2025 年化 16.3%、Sharpe 1.0、与 SPY 相关性 ~15%。
- **方法与主要发现**:① 数据 2008-2025,日频,用 VIX ETN(short-vol)产品;② 四种规则递增复杂度:(a) 恒定 short-vol;(b) VRP 信号驱动的择时(VIX 与已实现波动率差距);(c) dual signal:VRP + VIX term structure slope(contango vs backwardation);(d) (c) + 按 VIX 水平动态调整仓位;③ 第四种策略 OOS 16.3%/年、Sharpe 1.0、最大回撤可控、与 SPY 相关性 15%;④ 与 SPY 配比后组合 Sharpe 提升约 20%;⑤ 包含 broker API 自动化执行逻辑。
- **数据与市场背景**:**美股 + 美国期权市场**(VIX、SPY、VIX ETN)。**与 A 股股票完全无关**。
- **与当前系统的关系**:**不相关** —— v1 是 A 股股票因子挖掘,不涉及波动率交易 / 衍生品 / ETN。
- **具体 actionable 点**:**无直接 actionable**。属概念性,可以推论:① "波动率自身可交易 + VIX term structure 这条 anomaly 是真实持续的"——如果未来扩展到 A 股 50ETF 期权 / 沪深 300 期权,这套 VRP 策略思路可借鉴,但当前 v1 范围内不适用;② "短波动率策略含尾部风险"是普遍提醒——任何 mined factor 包含"卖波动"性质的(如 mean-reversion 策略空波动),都要做"极端 vol 事件下的回撤"sanity check。
- **采用的前提条件**:整体需要 v3+ 跨资产 / 跨工具扩展。当前不可用。
- **你的初步归类建议**:【**不适用**】—— A 股股票因子挖掘 ≠ 美股 VIX 交易,完全不同的 universe + asset class。属于"研究人员收藏的有趣方法",对 v1 没有立足点。
- **批判性意见**:① 论文目标受众是个人投资者 + 系统交易者,**不是学术 finance 论文**——无理论框架贡献,只是策略报告;② Sharpe 1.0 在 17 年回测 + 含 2008 / 2020 两个剧烈波动率事件,**已经是 VIX ETN 类策略的天花板,实战恐怕更低**(VIX ETN 本身有 termination risk,XIV/SVXY 2018-02-05 Volmageddon 直接清算);③ "5% haircut on VIX"调整是 industry-known,但论文没充分讨论 ETN 创设/赎回 + 流动性突变下的真实可用容量;④ 没有 capacity-constrained 评估,与 [ssrn-5797502](#ssrn-5797502.pdf) 的精神不符;⑤ 包含"broker API 自动化"细节,**像是付费课程 + 论文混合体**,学术严谨性中等偏下;⑥ 这种"如何用现成 ETN 收割 VRP"的内容,2010 年代已被多个 hedge fund / advisor 公开化,创新性不强;⑦ 收入"量化论文"清单显得 off-topic,**用户可能只是顺便保存**,不是认真研究输入。
- **关键页码/引用**:§4(四种策略构建);§5(回测结果 + 与 SPY 组合后 Sharpe 提升);Figure 1(VIX vs realized vol,显示 80% 时期 VIX > realized);Appendix(参考文献清单)。

### ssrn-5797502.pdf
- **标题 / 作者 / 年份 / 来源**:Bottom-Up Capacity Constraints and the Limits of Anomaly Profitability / Álvaro Cartea, Mihai Cucuringu, Qi Jin, Jiexiu (Victoria) Zhu(Oxford-Man,与 [ssrn-5156605](#ssrn-5156605.pdf) 同团队)/ 2025 / SSRN
- **一句话核心贡献**:提出**自底向上(bottom-up)的 capacity-constrained 评估框架**,用每只股票的 ADV 作为可交易容量上限、用美元成交金额而非 $1 标准化收益评估 anomaly 实际可执行价值;在 126 个 anomaly 上证明 **capacity 约束(而非 transaction cost)是 anomaly 收益的主要限制**——OOS 仅 24/126 在 0 cost + capacity 约束下显著,加 cost 后只剩 9-18 个。
- **方法与主要发现**:① 数据 1930-2023,US CRSP 普通股(剔除 microcap <$5);126 anomaly(Chen-Zimmermann 2024 "best quality" + 21 个 daily 衍生);② 在线 Bayesian regression 估计 CAPM beta,得到 market-excess return 作为 label;③ 关键设计:**每只股票每天的 tradable dollar volume 不超过其 ADV 一定比例**(模拟现实约束),trade size 是 predicted return × ADV 的函数,而非 $1 cross-section normalization;④ **核心 finding 1**:60%+ 单 anomaly 策略的 OOS daily dollar return < $1000,完全没有 institutional scale;⑤ **finding 2**:幸存的"capacity-friendly"anomaly 主要是 profitability + external financing 等大盘相关因子;short-term reversal 等 predictability 很强但全部 in small-cap 的因子失败;⑥ **finding 3**:加 cost 后,作者用"only execute when expected gross > cost"的 selective execution 替代"subtract aggregate cost"——50% 以上原本 anomaly 信号根本不该执行;⑦ **finding 4**:Small-cap 强 predictability + Large-cap 弱 predictability,但 dollar return 主要来自 large-cap;⑧ **finding 5**:Fund-size 实验显示资金规模越大、平均 dollar return 越高但 Sharpe 越低(diseconomies of scale)。
- **数据与市场背景**:**美股**,1930-2023(主要 OOS 是 2000+),需要 CRSP + Chen-Zimmermann anomaly dataset + ADV。**A 股 ADV / dollar volume / tradable scale 的数据组件 v1 都有**(直接来自 PIT bin `$money` 即 dollar volume)。
- **与当前系统的关系**:**fitness 设计 + Phase 6 promotion gate(直接相关)** —— 这是 27 篇里**对 v1 最重要的方法论文之一**,直接挑战 v1 当前 fitness 只看 IC/IR/turnover 的范式,提出"capacity-aware fitness"的核心思路。
- **具体 actionable 点**:① **v1 fitness 加 capacity 约束项**——目前 design doc 的 fitness 是 `α × IR - β × turnover_cost - γ × correlation_niche`,**应该加一个 capacity penalty**:`δ × (predicted_dollar_volume / ADV_threshold)`,即如果 mined factor 的 long-short 实际需要的成交量超过 universe 的 ADV 比例上限,扣分;② **PIT bin 字段 `$money` 是 dollar volume 的直接 PIT-correct 来源**——v1 grammar 已包含 `$money`,只需在 fitness 评估时聚合"top-N stocks 的 ADV 加权"作为 capacity proxy;③ **csi300 vs all universe 选择有了决策依据**——本文说 capacity 主要在 large-cap,csi300 是大盘,**csi300 universe 的 capacity 上限远高于"全市场"包含小盘——但 csi300 的 predictability 也会更弱**,所以 v1 的 universe 决策需要在 capacity 和 predictability 间权衡,**论文支持优先 csi300/csi500 而非 all**;④ **Phase 6 promotion gate 加一条"capacity sanity check"**:晋级前评估 mined factor 在目标 fund AUM 下的实际 dollar return,而非纯 % return;⑤ **selective execution(only trade when gross > cost)**直接对应 v1 design doc 的成本处理——目前 v1 是把 cost 作为 fitness penalty,但本文的 selective execution 范式是"扔掉不该交易的 signal"——这两种处理在 GP fitness 阶段差异巨大,**值得在 Phase 2/3 设计前明确选择**。
- **采用的前提条件**:① ADV 计算用 `$money` 滚动均值,**v1 现在就能算**;② "capacity penalty in fitness" 是 fitness 函数的简单扩展,工程量小;③ "selective execution" 改变 GP fitness 的本质,需要 design doc 决策。
- **你的初步归类建议**:【**现在(fitness capacity 项 + universe 决策依据)+ Phase 6(capacity sanity)**】—— **与 [ssrn-4702406](#ssrn-4702406.pdf) 并列为 27 篇里对 v1 fitness 最直接有用的文献**。
- **批判性意见**:① 论文核心 finding "capacity > cost"具体数字来自美股 1930-2023,A 股 universe 大小(csi300=300,csi500=500,all~5000)和 turnover 结构与美股不同,具体的"24/126"幸存率不能直接外推;② ADV 作为 capacity proxy 简化了真实 market impact——实战中即使 ADV 内的交易也有 √V 的非线性 impact,作者用线性 capacity 上限是 simplification;③ "selective execution"会引入额外的 path dependency 和 selection bias,论文未充分讨论;④ "幸存因子是 profitability + external financing"恰好是 v1 量价空间无法表达的——所以这条 finding 对 v1 mining 而言是 negative news:**v1 量价因子大概率全部在 capacity 之外**。需要做心理准备;⑤ 论文不区分 PIT vs 非 PIT 数据,与 v1 项目语境不完全契合;⑥ 与作者另一篇 [ssrn-5156605](#ssrn-5156605.pdf)(Volume Shock)结合看,作者团队对"volume / liquidity 与 alpha 的关系"特别敏感,该团队的论文应优先关注,但也注意论文间相互引用的内部 echo chamber 风险。
- **关键页码/引用**:§1(核心 finding 摘要,**24/126 显著 OOS 这条数字关键**);§2(framework 数学定义);§3(零 cost 下的 capacity-only 结果);§4(加 cost 后 selective execution);§5(fund-size case study,显示 diseconomies of scale);Appendix D(126 anomaly 列表)。

### 1571_AlphaQCM_Alpha_Discovery_.pdf
- **标题 / 作者 / 年份 / 来源**:AlphaQCM: Alpha Discovery with Distributional Reinforcement Learning / 匿名作者(ICLR 2025 投稿,双盲)/ 2024 / OpenReview
- **一句话核心贡献**:在 AlphaGen([自动挖因子1.pdf](#自动挖因子1.pdf)) 基础上把搜索算法从 **PPO** 改成 **distributional RL(IQN + DQN)**,并引入 **QCM (Quantiled Conditional Moment)** 估计 Z 分布的方差,用方差作为探索 bonus 解决"reward 稀疏 + 非稳态 MDP"两个 AlphaGen 没解决的问题——在 CSI300/500/Market 上 IC 8.49% / 9.55% / 9.16%,显著优于 AlphaGen / GP / XGBoost / MLP 等基线。
- **方法与主要发现**:① 把 alpha 挖掘 MDP 显式建模为 **non-stationary + reward-sparse**:non-stationary 来自 alpha pool 更新后旧 state-action 的真实 reward 变化;reward-sparse 来自大多数随机生成的公式 IC 接近 0;② IQN(随机分位数)学习 Z 的分位数函数,DQN 学习 Z 的均值;③ QCM 通过 Z=Z β+ε 的回归从分位数估计无偏的 σ²(即使分位数有偏);④ exploration bonus = σ̂(x,a) 加在 ε-greedy 策略上;⑤ reward 算法和 AlphaGen 完全一致——新因子加入后用线性回归在 alpha pool 上拟合 mega-alpha,IC 增量 = reward;⑥ 实验:CSI300/500/Market(A 股),pool size = 10/20/50/100,250-400k steps;⑦ Domain knowledge experiment:用 Kakushadze 2016 Alpha101 的 token 序列初始化 replay memory,**早期训练 w/ DK 占优,后期 w/o DK 占优**(说明 DK 容易陷入 local optima);⑧ 增大 AlphaGen 到同样 parameter count 仍输给 AlphaQCM,**说明优势来自算法不是 model size**。
- **数据与市场背景**:**A 股**(CSI300 / CSI500 / Market),6 字段(open/close/high/low/volume/vwap,**与 v1 完全一致**),baostock 数据。和 AlphaGen 一脉相承。
- **与当前系统的关系**:**搜索算法层(GP 的 RL 替代)+ Phase 3 GP 设计** —— 这篇直接对 v1 的"GP vs RL"决策给出更精确的现代答案:不是 GP vs PPO,而是 GP vs Distributional RL。**对 v1 Phase 3 GP 实现的最大警示:reward-sparsity 是真实问题,需要在 GP fitness 中显式处理**(例如 niching 不够多样、相同因子被反复尝试都是 reward-sparsity 表现的形式)。
- **具体 actionable 点**:① **GP fitness 加 variance-based exploration**——v1 Phase 3 GP 设计中,"correlation niche penalty"和"hash dedup"是当前的多样性机制,但 AlphaQCM 的"variance bonus"提供了另一种 framing:**fitness 中如果加入一项与历史已评估因子的差异度奖励**(类似 quality diversity / novelty search 的精神),可能比单纯 correlation penalty 更稳健。本文实证支持这条;② **"non-stationary reward"概念明确写入 v1 design doc**——目前 design doc §4.4 的 "Diversity = hash dedup + correlation niche penalty"是 stationary fitness 隐含假设;但实际 GP 演化中,**后期 generation 的 fitness 函数会因为 pool 状态变化而改变**——这是 AlphaQCM 强调的 non-stationarity。 v1 GP 实现时应**明确意识到这一点**,例如周期性 reset elite cache、定期重算 niche correlation 等;③ **algorithm benchmark** 对比 reference——v1 Phase 3 GP 完成后,可以用 AlphaQCM 论文的实验设定(CSI300/500/Market + 同样数据周期)做对照,**预期 v1 GP IC < AlphaQCM 8.5%,但 v1 的可解释性 + PIT-correctness 是 trade-off**;④ **Token / operator set 完全可复用**——文中 token 设计与 AlphaGen 一致(已在 [自动挖因子1.pdf](#自动挖因子1.pdf) 笔记中详述)。
- **采用的前提条件**:① "exploration bonus"和"non-stationary fitness"概念可以**现在(Phase 3 GP 设计前)就纳入 design doc**;② Distributional RL 实现远超 v1 范围,留作 v3 替代方案;③ AlphaQCM 论文 IC 数据是 baostock 非 PIT,v1 实际 IC 一定更低但更可信。
- **你的初步归类建议**:【**现在(Phase 3 GP 设计的 non-stationarity + variance-based exploration 思想)+ v3(完整 distributional RL 替代实现)**】—— 与 [AlphaGen / 自动挖因子1](#自动挖因子1.pdf) 并列为 27 篇里**对 v1 搜索算法层最相关的论文**。
- **批判性意见**:① **数据是 baostock 前复权**,与 [自动挖因子1](#自动挖因子1.pdf) 同问题——non-PIT,IC 含 survivorship bias,A 股 PIT 化后 IC 一定降;② IQN + QCM 实现复杂度远高于 PPO,**对小团队几乎不可执行**——4 个网络(online Q + online quantile + 2 targets),572k 参数,training 25-40 万 step——v1 RTX 4060 Ti 完全可以做但工程量不容小觑;③ **同样数据集下 GP w/o filter / GP w/ filter 都跑得很差(IC ~3-5%)** —— 这是论文对 GP 的标准 baseline 设法,但**用的是开源 gplearn 或类似实现,不一定是 SOTA GP**。真实 A 股 GP 系统(华泰金工/中金等)实际表现是否真的这么差,论文未给出可信比较;④ "domain knowledge w/ 初期占优、w/o 后期占优"实验有点反直觉,作者解释 local optima 是合理的但**不是唯一解释**——可能 w/ DK 引入的 prior 不适合当前 reward landscape;⑤ 投稿匿名,作者身份不明,**可能与 [自动挖因子1](#自动挖因子1.pdf) Yu 等中科院团队是同一/相关团队**(reward 函数 + 数据集 + token 集完全一致),需要 author identification 才能完整评价。
- **关键页码/引用**:Algorithm C.1(reward calculation,与 AlphaGen 完全一致);§3(QCM 方法,Z=Zβ+ε 回归);Table 1 主结果(IC 9.55% on CSI500);Table H.5(domain knowledge ablation);Table I.6(parameter size ablation);Appendix F(hyperparameter 完整列表)。

### 2024.acl-long.402.pdf
- **标题 / 作者 / 年份 / 来源**:EFSA: Towards Event-Level Financial Sentiment Analysis / Chen, Zhang, Yu, Zhang, Zeng, He, Ao(中科院 CAS + 郑州大学 + 深交所)/ 2024 / ACL 2024 Long Paper
- **一句话核心贡献**:把传统的"实体级"金融情感分析(FSA)升级到**"事件级"**——新任务 EFSA 输出 5-tuple `(company, industry, coarse_event, fine_event, sentiment)`,发布 12,160 篇中文财经新闻数据集,提出 4-hop Chain-of-Thought LLM 方法,达到 SOTA。
- **方法与主要发现**:① 观察:同一实体(如 Nvidia)在一条新闻中可能因为不同事件(profit forecast vs stock price movement)展现相反情感,所以"实体级"FSA 不足;② 把"事件抽取"难题(事件长且不连续)重新建模为**分类任务**——预定义 hierarchical 分类(coarse event 8 类 × fine event ~50 类);③ 工业分类用 knowledge-based rules;④ 4-hop CoT LLM:逐步抽取 company → industry → events → sentiment;⑤ 数据集 12,160 articles / 13,725 quintuples,中文财经新闻。
- **数据与市场背景**:**中文金融新闻文本**,不是股票市场数据。任务是 NLP 抽取,不是因子生成 / 回测。
- **与当前系统的关系**:**完全不相关** —— v1 是基于股价/成交量的 GP 因子挖掘,与 NLP 文本处理是两条平行 pipeline。即使是 v2 / v3 想引入 sentiment-based factor,也需要先有完整的"新闻流 → 时序情感 score → 个股级时序特征"pipeline,这篇论文只覆盖了第一步的一半。
- **具体 actionable 点**:① **无直接 actionable for v1**;② **v3+ 远期可能性**:如果项目扩展到"alternative data factor"(新闻、舆情、研报),这篇是中文场景的数据集 + baseline reference,但需要先做 ~6 个月的 NLP / event extraction pipeline 投资;③ 论文标题 "event-level" 提醒一个**经济原则**:同一公司在同一周期可能因不同 catalyst 有相反 sentiment——这对 mined factor 的"sentiment 类信号"是 reminder(纯量价 v1 不直接相关)。
- **采用的前提条件**:① 需要 NLP 全栈(corpus / event ontology / LLM API / labeling);② 需要把抽取出的 (company, event, sentiment) 5-tuple 时间序列化成日级股票特征(论文未涉及这一步);③ 远超 v1/v2 范围。
- **你的初步归类建议**:【**不适用**】—— off-topic for factor mining,文件被收录可能是因为研究者关注 NLP + 金融领域,不是 v1 输入。
- **批判性意见**:① **明确不属于"量化论文"范畴**——这是 NLP / sentiment analysis 论文,放在量化论文清单是收录者 mis-classification;② 数据集 12k 文章质量未在论文中充分讨论(数据来源、人工标注协议、annotator agreement 等);③ 8×50 = 400 个细分事件类别在 12k articles 上每类平均只有 ~30 个样本——**对 fine-grained 分类是严重少样本**;④ 4-hop CoT LLM 对 GPT-3.5/4 API 调用密集,成本和延迟实战中难以负担;⑤ **shenzhen 交易所是合著者之一**(Li Zeng 在 IT 部门),所以这个数据集和方法可能与交易所内部用例有关——研究目的与公开 academic finance 略错位;⑥ 没有任何"factor performance"实证——是否真能用于交易完全没回答。
- **关键页码/引用**:§3 数据集构建;§4 4-hop CoT LLM 方法;Figure 1(任务定义示例);Table 2 数据集统计;Table 4-6 主结果。

### 2407.06567v3.pdf
- **标题 / 作者 / 年份 / 来源**:FINCON: A Synthesized LLM Multi-Agent System with Conceptual Verbal Reinforcement for Enhanced Financial Decision Making / Yu, Yao, Li, Deng, Jiang, Cao, Chen, Suchow, Cui, Liu, Xu, Zhang, Subbalakshmi, Xiong, He, Huang, Li, Xie 等 18+ 作者(Stevens Tech + Harvard + The Fin AI)/ 2024-11(v3)/ NeurIPS 2024 / arXiv 2407.06567
- **一句话核心贡献**:多 LLM agent 框架,**模拟人类投资公司的 manager-analyst 层级**,做 single-stock trading 和 portfolio management;创新点是**双层风险控制**(每日 CVaR + 跨 episode verbal reinforcement)和**conceptual belief 反向传播**(从 manager 到 analyst);把交易决策建模为 POMDP,用 textual gradient descent 优化 prompt-based policies。
- **方法与主要发现**:① 架构:Manager 1 个 + Analyst 多个(News / Filing / Audio / Image 等模态各 1 个)+ Risk-Control 1 个;② Within-episode 风险:每日用 CVaR 监控;③ Cross-episode 风险:基于 PnL 趋势 + reasoning trajectory 把"投资 belief"提炼成 conceptual perspectives,选择性反向传播到相关 analyst;④ Portfolio task 用经典 mean-variance optimization,buy/sell/hold 决定 weights 边界;⑤ 实证在 7 只 US 个股 + 1 个 portfolio 上击败 FINGPT / FINMEM / FINAGENT / DRL baseline。
- **数据与市场背景**:**美股**(7 只代表性个股 + 1 个 portfolio),多模态输入(news / 10-K / earnings call audio / chart images)。**与 A 股股票因子无关**。
- **与当前系统的关系**:**完全不相关 layer** —— v1 是因子挖掘(信号生成),FINCON 是 trading decision agent(信号 → 决策)。两者甚至不在同一栈层。
- **具体 actionable 点**:① **无直接 actionable for v1**;② 概念性提醒:**CVaR 作为 within-episode 风险监控**对应到 v1 Phase 6 promotion 的"max drawdown"作为 mined factor 的辅助 gate——但这是常识,不需要这篇论文;③ **verbal reinforcement(prompt 优化)** 概念在 v1 不可用(我们没有 LLM agent);④ Manager-Analyst 角色分离对应到 v1 没有的"组合层"——v1 mined 因子作为 raw signal,后续训练 pipeline 才做 decision,这层不混在一起。
- **采用的前提条件**:① 完整 LLM agent stack(GPT-4 / Claude API,多模态 input pipeline,memory store,verbal reinforcement loop);② 多模态金融数据(audio call transcripts / chart images / news);③ 远超 v1/v2/v3 范围。
- **你的初步归类建议**:【**不适用**】—— 不同层(trading decision vs factor mining),不同 paradigm(LLM agent vs symbolic GP),不同 universe(美股 + 多模态 vs A 股 + OHLCV)。
- **批判性意见**:① 论文实验只在 7 只 US stocks + 1 portfolio,样本极小;② LLM agent + multi-modal 的 API 成本日积月累不可忽视,**真实账户 PnL 应该扣除 token cost 后看**——论文未明示;③ "verbal reinforcement"的 textual gradient descent 数学上不严格,本质是 prompt engineering with feedback loop——是否真的"learn"还是 emergent 行为,缺乏严格 ablation;④ "Manager-Analyst hierarchy"是 prompt 模板设计,可能换种描述方式效果就完全不同——对 prompt 敏感度的 robustness 测试不足;⑤ CVaR 作为 risk monitor 是经典做法,创新边际有限;⑥ 18 作者 + NeurIPS,论文工程量大但 marginal scientific contribution 中等。
- **关键页码/引用**:Figure 1-2 架构图;§2.1-2.2 POMDP 数学建模;§3.1 manager-analyst structure;Eq. (1) mean-variance optimization;§4 实证 setup;Table 2/3 主结果。

### 2502.15458v2.pdf
- **标题 / 作者 / 年份 / 来源**:Clustered Network Connectedness: A New Measurement Framework with Application to Global Equity Markets / Bastien Buchwalter, Francis X. Diebold, Kamil Yilmaz(SKEMA + UPenn + Koç U)/ 2025-12(v2)/ arXiv 2502.15458
- **一句话核心贡献**:把 Diebold-Yilmaz 经典的 VAR-based 网络连通性(network connectedness)框架扩展为支持"clustered identification"——允许 cluster 内 shock 相关、cluster 间 shock 正交,统一了 Cholesky(C=N)和 generalized(C=1)两种特例,既保留因果排序的灵活性又控制 ordering combinatorics。
- **方法与主要发现**:① 在 N 维 VAR 上,用 cluster 结构(C 个 cluster,每个含若干节点)定义新的 Q⁻¹ 变换矩阵,使 Q⁻¹ut 的协方差矩阵在 cluster 间块对角、cluster 内非零;② 由此得到 cluster-orthogonalized impulse responses 和 variance decompositions;③ 应用:16 国权益市场,3 个地理 cluster(美洲 / 欧洲 / 亚太),比较 generalized vs clustered VD;④ 显示 clustered 视角能识别 generalized 视角抹平的跨 cluster 因果,同时不需要 N! 种 ordering check。
- **数据与市场背景**:**全球股票指数**(16 国权益市场),不涉及个股层。计量经济学(econometrics)方法论文,不是因子方法论文。
- **与当前系统的关系**:**不相关或概念性** —— v1 是 A 股个股因子挖掘,这篇是跨市场指数 spillover 测量,两件不同的事。**可能间接相关**:如果 v2/v3 做"全球 risk regime"信号,clustered connectedness 可以作为 systemic shock 的度量,但 v1 完全用不到。
- **具体 actionable 点**:**无直接 actionable for v1**。概念性启示:① VAR-based variance decomposition 是衡量"哪些节点 drive 哪些"的标准工具,如果未来 v2/v3 需要研究"v1 mined factor 之间的 spillover / lead-lag",这套框架是合理选择,但 v1 阶段先做出可用的因子才是重点;② "cluster-orthogonalized identification"的精神(行业内允许相关,行业间正交)与 [decisions.md](docs/factor_mining/decisions.md) D2 的"v2 才做行业中性化"思想有概念呼应——v2 加入行业 grouping 时如果要做 cross-factor 分析,可以借鉴。
- **采用的前提条件**:整体需要"已有多个因子"+"想研究因子间动态结构"的前提,v1 单因子挖掘阶段完全用不到。
- **你的初步归类建议**:【**不适用**】—— 跨市场 spillover 度量,与个股因子挖掘几乎无 overlap。属于"研究人员收藏的方法论文",对 v1 / v2 没有立足点。
- **批判性意见**:① 论文是**纯方法论文**,empirical application 只是 illustration,没有声称"用 cluster connectedness 直接赚钱";② Diebold-Yilmaz 框架在 quant 业界已经被 30 多年的研究覆盖,这篇属于"refinement"而非"breakthrough";③ VAR + impulse response 的 lookahead bias 在实战中怎么处理论文未细谈,academic exercises 通常用全样本估计——v1 项目 PIT-correctness 要求与论文方法不直接兼容;④ 16 个国家指数样本在金融大数据时代显得小;⑤ 适合作为 risk management / macro research 的工具,不适合作为 factor mining input。
- **关键页码/引用**:§2.2 cluster-orthogonalized IRF 数学定义;Eq. (5)-(8) cluster Q⁻¹ 推导;§3 16 国权益市场实证;Figure 3-5 connectedness visualizations。

### 2508.13174v1.pdf
- **标题 / 作者 / 年份 / 来源**:AlphaEval: A Comprehensive and Efficient Evaluation Framework for Formula Alpha Mining / Ding, Chen, Huang, Guo, Mao, Shao, Zou, Liu, Zhang(北大 PKU + Baruch + Zhengren Quant)/ 2025-08 / arXiv 2508.13174(AAAI 2026 投稿)/ **代码开源** [github.com/BerkinChen/AlphaEval](https://github.com/BerkinChen/AlphaEval)
- **一句话核心贡献**:针对当前 alpha mining 评估"只看 IC / RankIC / 回测"的窄视野缺陷,提出 **AlphaEval 5 维度评估框架**——predictive power / temporal stability / robustness(对市场扰动)/ financial logic(可解释性)/ diversity,**全部不需要回测**,可并行、可复现,跨 8 个主流 mining 模型(GP / AutoAlpha / AlphaGen / AlphaForge / AlphaQCM / FAMA / AlphaAgent / Random)做了横向对比。
- **方法与主要发现**:① 5 个 metric:**Predictive Power Score (PPS)** = IC + β × ICIR 的复合(β = 0.5/0.8 实证最优);**Stability** = 不同时间段 IC 的方差稳定度;**Robustness** = market 数据加扰动后 alpha 值的 Pearson correlation(PFS, Perturbation Fidelity Score);**Diversity** = alpha 之间 cross-correlation 的 1 - mean 上三角;**Logic** = LLM 评分(0-100,根据 alpha 公式的经济合理性);② 5 个 metric 都是**单 alpha 或 alpha set 级别可算**,无需 portfolio backtest;③ 实证证明 5 metric 综合得分与 backtest portfolio Sharpe 高度一致;④ **PFS ≥ 0.9 的因子 MaxDD 显著低于 < 0.9 的(t=4.12, p=0.0001)**——稳健性 metric 真的能 predict 实战回撤;⑤ **RRE(predictive 维度子项)与年化 turnover 显著负相关 R²=0.815**——可解释性维度真的对应低换手;⑥ Random model 在 5 维度的得分给出 baseline 参照(0.006/0.962/0.899/0.976/63.0)。
- **数据与市场背景**:作者 setup 主要在 A 股(中科院系列论文用同样的 CSI300/500 + 6 字段),论文也展示了 S&P 500 复测(Table 7)。**数据集与 v1 完全兼容**(open/high/low/close/volume/vwap)。
- **与当前系统的关系**:**evaluator + Phase 6 promotion gate(直接相关)** —— 27 篇里**对 v1 evaluator / promotion 设计最具体的方法论参考**。design doc D4 + §6 当前只列了"OOS IR > 0.3 / RankIC mean > 0.02 / max corr < 0.6 / 6m IR > 0.2 in ≥70% of windows"——AlphaEval 给出了一个更完整且可计算的 5 维度分数,正好弥补 v1 promotion gate 的具体度量空白。**特别是 Robustness / Logic 两维 v1 还没有任何度量**。
- **具体 actionable 点**:① **v1 evaluator 直接借用 AlphaEval 的 5 维度做 Phase 6 promotion 分数**——具体:**PPS (IC + β·ICIR), Stability (rolling IC std), PFS (perturbation), Diversity (cross-corr), Logic (LLM scoring) 五项加权 + 阈值**。`promote.py` (Phase 6) 的实现思路直接 fork 论文的 [开源仓库](https://github.com/BerkinChen/AlphaEval),按需简化(尤其 Logic 一维需要 LLM API,可选);② **PFS(Perturbation Fidelity Score)直接采用**——做法是对 OHLCV panel 加小幅 noise 后重算 alpha,与原 alpha 值算 Pearson corr,PFS ≥ 0.9 的 alpha 实战 MaxDD 显著更低。**这是 v1 现在就能实现的 governance test**,只需要 evaluator 支持 perturbation pipeline;③ **Diversity score** 用 1 - mean(cross-corr) 表达,正好对应 v1 design doc §4.4 的"correlation niche penalty",可以直接作为 Phase 6 检验最终入池组合的 alpha 集合多样性;④ **Logic score (LLM-based 经济合理性评分)**对 v1 是新增维度——可以用 GPT/Claude API 把 mined factor 表达式喂给 LLM,要求其评估"是否符合经济直觉",作为 promotion 时的 sanity score。**但应明确:Logic score 永远是辅助,不能 promote 决策代理**;⑤ **AlphaEval 论文 Table 1 总结了 11 个主流 alpha mining 模型的 evaluation 现状**——v1 在 design doc 引用时,可以把这表作为"现有方法评估片面性"的 reference,加强 v1 的"manual promotion + 多维度 gate"决策合理性;⑥ **operator table(Table 4)**:32 个算子,涵盖 v1 design doc §5.1 的全部 + 几个 v1 没列的(Skew/Kurt/Med/Mad/Slope/Rsquare/Resi/Power/IdxMax/IdxMin/WMA),**对 v1 算子库扩展是有用 reference**,建议至少把 Skew/Kurt/Slope/IdxMax/IdxMin 加进 v1。
- **采用的前提条件**:① 5 维度评估在 v1 Phase 2-3 evaluator + Phase 6 validator 中**现在就可以分批落地**:PPS/Stability/Diversity 不依赖外部 → Phase 2 就实现;PFS perturbation → Phase 3 加入;Logic LLM → Phase 6 可选;② 论文代码开源,工程 cost 是 fork 而非重写;③ Logic 维度需要 LLM API 预算,可作为 Phase 6 选项。
- **你的初步归类建议**:【**现在(全面落地 5 维度)+ Phase 6 重点参考**】—— **27 篇里对 v1 evaluator + promotion gate 最直接 actionable 的论文**,优先级与 [ssrn-5797502](#ssrn-5797502.pdf)、[自动挖因子1](#自动挖因子1.pdf) 并列第一梯队。
- **批判性意见**:① **Logic 维度用 LLM 评分本身就值得怀疑**——GPT 对 alpha 公式的"经济合理性"评分有 bias(可能偏好它训练过的因子表达,而排斥新颖的)。论文用 LLM 评分给出 71.5(AlphaAgent)最高,71 是不是真的"最合理"取决于评分 LLM 的偏见;② **PFS perturbation** 用什么 noise distribution、强度多大,论文里有 default 但 cross-market / cross-asset 的合适设置可能不同——A 股个股波动率不同于 SP500,perturbation magnitude 需要重新校准;③ **Predictive Power Score 用 IC + β × ICIR**,β = 0.5-0.8 是论文实证最优,但**这个 β 本身就需要回测调,与论文宣称的"backtest-free"略有矛盾**;④ 论文实证 ablation 集中在 8 个开源模型,**真实 hedge fund 内部的因子可能完全不同**,对 selection criteria 适用性未必跨场景;⑤ **基准 baseline "Random model"** 的存在很好,但 random 在 stability/robustness/diversity 得分都 > 0.9,这暴露了这三个 metric 对"无 alpha 但稳定的随机噪声"无区分力——若要做 promotion 必须先过 PPS gate,这点论文没充分强调;⑥ 作者团队 Zhengren Quant 是中国量化公司,论文写作工程化 + benchmark 导向,与学术理论贡献相比偏 application,但**对 v1 这种以落地为目标的项目反而是好事**。
- **关键页码/引用**:§3(5 维度定义,**核心**);Table 1(现有 mining 模型 evaluation 现状清单);Table 7(S&P 500 上 8 模型对比);Table 4(32 算子定义,**可作 v1 算子库扩展参考**);Table 5(RRE-turnover 实证关系);Figure 4/5(PPS β 敏感性 + PFS 阈值);**[github 仓库](https://github.com/BerkinChen/AlphaEval) 是直接落地起点**。

### Machine-learning-in-the-Chinese-stock-mark_2022_Journal-of-Financial-Economi.pdf
- **标题 / 作者 / 年份 / 来源**:Machine Learning in the Chinese Stock Market / Markus Leippold, Qian Wang, Wenyu Zhou(Zurich + Zhejiang U)/ 2022 / Journal of Financial Economics 145, 64-82 / **顶刊 JFE 论文,A 股 ML 因子的 reference paper**
- **一句话核心贡献**:第一篇在 A 股全面应用 Gu-Kelly-Xiu (2020) US ML asset pricing 框架的论文——1,160 个信号(90 stock-level + 11 macro + industry dummies)× 多种 ML 方法(NN / GBRT / RF / ENET 等);**关键 findings 与美股截然不同**:**liquidity 是 A 股最重要的预测因子**(而非美股的 profitability / size / value);predictability 集中在 small + non-SOE 股票(retail traders 主导);大盘 + SOE 在长 horizon 也有 predictability;OOS 收益扣交易成本后仍显著。
- **方法与主要发现**:① 数据:A 股 main board,2000-2020,1,160 信号,90 stock characteristics 包括 v1 量价 + 财务 + microstructure(turnover、abnormal turnover 等);② 方法:OLS + L1/L2 + RF + GBRT + 浅 / 深神经网络;③ **NN 在 OOS R² 上稳定击败其他**,小盘 / 非国企子样本 R² 尤其大;④ A 股 OOS R² 显著高于美股(retail-dominated → 信号 less efficiently arbitraged);⑤ **Variable importance:liquidity-related signals 占 top **(abnormal turnover, illiquidity, share turnover 等),与美股 GKX (2020) profitability/momentum 主导的结论完全不同;⑥ Long-short Sharpe ~3-4(GBRT/NN top decile),long-only Sharpe ~1-1.5(更符合 A 股 short-sale 受限实操);⑦ Transaction cost 2%(双边,A 股 setup),扣除后年化 alpha 仍 12-15%;⑧ Pan et al. (2015) 的 atr(abnormal turnover ratio)被作为 A 股特有 variable 加入,有显著贡献。
- **数据与市场背景**:**A 股 main board**(上海 + 深圳 A 股),2000-2020。**数据极其丰富**:90 stock-level characteristics 包括 OHLCV、turnover、liquidity 各种衍生(Amihud illiq、share turnover、bid-ask、abnormal turnover)、基本面(BM、EP、SP、profitability)、波动率、动量、特异波动率等。**11 macro variables 部分 v1 用不上**(政策利率、CPI、PMI 等)。
- **与当前系统的关系**:**经济先验 + universe + fitness 设计的 anchoring 文献** —— 这是 A 股因子的"权威性最高的 ML reference"。**它告诉我们 v1 量价空间在 A 股里能赚什么 alpha**:**liquidity / turnover 类信号是关键**,而 v1 当前的 PIT bin 6 字段里有 `$volume` 和 `$money`(dollar volume),**正好可以表达 turnover/illiquidity 类信号**。
- **具体 actionable 点**:① **v1 design doc 加一段"A 股 anchoring"** —— 引用本文 finding"liquidity is the top predictor in China",指导 GP 的算子组合方向:**优先组合 `$volume / $money / ts_std($volume) / Amihud-like 表达`,而非偏向 cs_rank($close) / ts_corr($close) 之类的纯价格因子**——可以作为 Phase 3 GP 的"prior distribution"提示;② **abnormal turnover ratio (atr) 直接进算子库** —— Pan et al. (2015) 的 atr 定义类似 `$volume / ts_mean($volume, n)`,**v1 现有算子可直接表达**——design doc 应明确"v1 Phase 3 GP 应能演化出 atr-like 表达式"作为 sanity smoke test;③ **csi300 vs all universe 的 trade-off 有了实证依据**:本文说 A 股 predictability 集中在 small + non-SOE,**这意味着 csi300(大盘)上的 mined factor 期望 R² 会偏低**(但 stability + capacity 优,见 [ssrn-5797502](#ssrn-5797502.pdf));若 v1 想最大化 R²,应该把 universe 扩展到 csi500 + 创业板甚至 csi all,**但要同时引入小盘的"shell value / suspension / restructuring"风险**;④ **Long-only Sharpe 比 long-short 低很多** 这条对 v1 fitness 计算的 IC 评估有 implication:A 股 v1 的 mined factor 实战上是 long-only(因为短不了空),长期 IC 数字会比论文里的 long-short IC 报告低;⑤ **Variable importance 的 ranking 表(Figure 5 等)**值得手抄出来作为 v1 design doc 附录——A 股 top-15 predictors 中 turnover/liquidity 占主导,profitability 排次,**与美股 GKX 排名完全不同**。
- **采用的前提条件**:① "A 股 anchoring 段"**现在就能写**;② atr-like 表达作为 GP smoke test 需要 Phase 3 GP 实现后加;③ 完整复现 GKX-style ML pipeline 远超 v1 范围(v1 是 GP 公式因子,不是 ML 回归)。
- **你的初步归类建议**:【**现在(经济先验 anchoring + universe 决策依据)+ v2(若想跨进 ML factor model)**】—— **27 篇里对 v1 A 股市场适用性 anchoring 最权威的一篇**,价值在于它把美股 vs A 股的差异定量化了,而不是因子方法本身。
- **批判性意见**:① 数据 2000-2020,**没用 PIT 数据**——A 股 ticker 在这 20 年有大量并购重组 + 借壳 + ST 退市,non-PIT 的"重新 lookup adj_factor / 重新连续化"会引入 survivorship bias。本文 OOS R² 一定**高估真实可信值**;② **liquidity 类 signal 与 capacity 直接冲突** —— 本文说 liquidity / turnover signal 最 predictive,但 [ssrn-5797502](#ssrn-5797502.pdf) 指出 capacity 是真正 bottleneck,**A 股最 predictive 的恰好是 capacity 最受限的小盘 + 非 SOE**——v1 设计时要意识到 A 股的"高 R² 因子 ≠ 高 dollar return 因子";③ **作者团队 in Zurich + Zhejiang**,样本截止 2020 ——A 股 2021-2025 的"白马股退潮 + 量化暴跌 + 注册制全面铺开"的 regime shift 不在论文里;④ 1,160 信号中 **基本面 + macro + industry dummies 几乎全部 v1 不可表达**,真正属于 v1 量价空间的可能只有 30-40 个 turnover/liquidity 类——所以 v1 用本文 anchoring 时不能照搬整张 variable importance 表;⑤ NN/GBRT 是 black-box,与 v1 公式因子(symbolic GP)是不同范式,论文方法不能直接搬到 v1 实现;⑥ **Long-only Sharpe ~1.5 是 best case** ——v1 实战预期更低,要做好心理准备。
- **关键页码/引用**:Abstract(三个 A 股 specificity);§2.2 数据描述(1160 signals 列表);§3 方法(各 ML);Table 5(各方法 OOS R²);**Figure 5 + Table 7(variable importance,liquidity 主导,关键)**;§4.2(SOE vs non-SOE 子样本);§5(transaction cost 后表现)。

---

## 汇总表

| 文件 | 一句话贡献 | 初步归类 | 关键前提 |
|------|------------|----------|----------|
| 市场态势选因子.pdf | 用 SPF IPG 预期修正预测美股 anomaly 时变 | 不适用 | 美股基本面 + 多因子轮动,v1 范围外 |
| 态势选因子.pdf (RSAP-DFM) | A 股端到端深度学习"双 regime shifting"因子模型 | v3 或不适用 | 不同范式(latent factor vs symbolic) |
| 态势选因子3.pdf (HireVAE) | 在线自适应 regime-switching VAE 因子模型 | v3 或不适用 | 不同范式;regime 部件可单独借鉴 |
| 强化学习50因子.pdf (AlphaPortfolio) | RL+Transformer 直接出组合权重(美股) | v3 或不适用 | 612 维基本面特征 + 月频组合层 |
| 数据补足收益暴增.pdf (Missing Financial Data) | B-XS 因子模型 + 时序补缺,论证 median fill 偏差 | 现在(防御性 NaN)+ v2(B-XS) | v1 阶段防御性处理 + v2 接基本面后落地算法 |
| 自动挖因子1.pdf (AlphaGen) | RL+PPO 挖协同因子,组合增量 IC 作 reward(A 股) | **现在(算子集)+ v2 + v3** | A 股 + 6 字段与 v1 极接近,**最直接对照** |
| 自动挖因子2.pdf (AlphaForge) | Generative-predictive 因子搜索 + 动态时变组合 | **现在(label/walk-forward)+ v2/v3** | label=vwap,与 v1 forward_return 设计相关 |
| 自动挖因子3.mht (alpha-gfn README) | GFlowNet 按 reward 概率采样多样化因子(US demo) | v3 或不适用 | demo 级别,看 AlphaSAGE 2025 更靠谱 |
| ssrn-2528780.pdf (Lucky Factors) | Bootstrap 强制 null 的多重检验 panel 因子选择 | **现在 + Phase 6** | v1 evaluator 加 bootstrap gate 思路可直接借鉴 |
| ssrn-3604626.pdf (Open Source Asset Pricing) | 319 美股 anomaly 的开源复现,reproducibility 论证 | **现在(量价 seed)+ v2(完整库)** | 量价子集可作 GP 种群 seed |
| ssrn-4106794.pdf (Missing Financial Data I) | 与 数据补足收益暴增 同篇,SSRN 版 | 同 数据补足收益暴增 | **重复文件,人类决策去重** |
| ssrn-4339591.pdf (Reversals/Liquidity) | 短反转的清洁分解 + vol/turnover 调制(美股) | **现在(vol-conditional 算子)+ v2** | 严格剥离 PEAD/IMOM 需 announcement+industry |
| ssrn-4385668.pdf (Value premium in China) | A 股大市值 BM/EP 更高(shell value);低情绪 value 更好 | 现在(经济先验)+ v2(PLS combo) | A 股 anchoring;v1 无法直接表达 value |
| ssrn-4560216.pdf (GPT's Idea of Stock Factors) | GPT-4 生成 35 因子(美股 15 月);Sharpe 4.49 严重存疑 | 现在(GP seed)+ Phase 6 工具 | **方法可借鉴,实证结论严重存疑** |
| ssrn-4702406.pdf (Expected Returns on ML) | ML 因子三摩擦后 Sharpe 衰减 57%,LSTM 抗摩擦最强 | **现在 + Phase 6** | shrinkage / holding-period sensitivity 直接落 v1 |
| ssrn-5080998.pdf (Pairs Trading Clustering) | 偏相关距离聚类做 pairs trading | 现在(novelty 用偏相关)+ 其余不适用 | 仅借鉴 partial correlation 在 novelty test |
| ssrn-5156605.pdf (Volume Shock & Overnight) | Volume Shock 与 overnight return 正相关(美股) | **现在(overnight/intraday primitive + EMA)** | v1 算子库扩展候选 |
| ssrn-5178543.pdf (Profitability Retrospective) | Profitability subsumes quality / defensive / alt-value | v2(主体)+ 现在(low-vol caveat) | v1 量价无法表达 profitability |
| ssrn-5316487.pdf (VIX ETN Trading) | 用 VIX ETN 收割 VRP(美股衍生品) | 不适用 | 不同 asset class |
| ssrn-5797502.pdf (Bottom-Up Capacity) | Capacity 而非 cost 是 anomaly 主要限制 | **现在(fitness capacity 项)+ Phase 6** | 与 ssrn-4702406 并列对 v1 fitness 最有用 |
| 1571_AlphaQCM (AlphaQCM) | 分布式 RL + QCM 方差 bonus 解决 reward-sparse(A 股) | **现在(non-stationary 思想)+ v3 完整实现** | 与 AlphaGen 同源,A 股 + 6 字段 |
| 2024.acl-long.402.pdf (EFSA) | 事件级中文金融情感分析(NLP) | 不适用 | NLP 数据集,与因子无关 |
| 2407.06567v3.pdf (FINCON) | LLM 多 agent 交易决策(POMDP + verbal RL) | 不适用 | 不同 layer(decision vs factor) |
| 2502.15458v2.pdf (Clustered Connectedness) | VAR-based 跨市场 spillover 测量框架 | 不适用 | 跨市场指数 spillover,非个股因子 |
| 2508.13174v1.pdf (**AlphaEval**) | **5 维度无回测评估框架**(预测/稳定/鲁棒/可解释/多样性),开源 | **现在(全面落地 5 维度)+ Phase 6 重点** | **27 篇里对 v1 evaluator 最直接 actionable** |
| Machine-learning-in-the-Chinese-stock-mark... (JFE 2022) | A 股 ML 因子,**liquidity 是 top predictor**(与美股不同) | **现在(A 股 anchoring + universe 决策)** | 经济先验 anchoring,v1 量价空间适用性最强 |

---

## 优先级总结(我的研究助理判断,最终由人类 + reviewer 决策)

### Tier 1:对 v1 直接 actionable,建议优先落地
1. **[ssrn-5797502.pdf](#ssrn-5797502.pdf) (Capacity Constraints)** + **[ssrn-4702406.pdf](#ssrn-4702406.pdf) (ML Expected Returns)** — fitness 设计的两个核心增强:capacity penalty + shrinkage / holding-period sensitivity。
2. **[2508.13174v1.pdf](#2508.13174v1.pdf) (AlphaEval)** — 5 维度评估框架,可作为 Phase 6 promotion gate 的 backbone,**有开源代码**。
3. **[自动挖因子1.pdf](#自动挖因子1.pdf) (AlphaGen)** + **[自动挖因子2.pdf](#自动挖因子2.pdf) (AlphaForge)** + **[1571_AlphaQCM.pdf](#1571_AlphaQCM_Alpha_Discovery_.pdf) (AlphaQCM)** — A 股同字段对照系列,operator set / label / walk-forward / non-stationarity 思想直接复用。
4. **[Machine-learning-in-Chinese-stock-market (JFE 2022)](#Machine-learning-in-the-Chinese-stock-mark_2022_Journal-of-Financial-Economi.pdf)** — A 股市场 anchoring,**liquidity 类信号是 top predictor**,直接指导 v1 GP 的 operator 组合方向。
5. **[ssrn-5156605.pdf](#ssrn-5156605.pdf) (Volume Shock & Overnight)** — v1 算子库扩展候选:overnight/intraday return primitive + EMA + Volume Shock 表达。

### Tier 2:对 v1 evaluator / promotion / 经济先验有借鉴
6. **[ssrn-2528780.pdf](#ssrn-2528780.pdf) (Lucky Factors)** — bootstrap 多重检验 gate。
7. **[ssrn-3604626.pdf](#ssrn-3604626.pdf) (Open Source Asset Pricing)** — 量价子集作 GP 种群 seed + monotonicity 辅助指标。
8. **[ssrn-4339591.pdf](#ssrn-4339591.pdf) (Reversals & Liquidity)** — vol/turnover-conditional reversal 表达 + 经济先验。
9. **[数据补足收益暴增.pdf](#数据补足收益暴增.pdf) / [ssrn-4106794.pdf](#ssrn-4106794.pdf) (Missing Financial Data,重复)** — evaluator NaN 处理的"防御性铁律"。
10. **[ssrn-4385668.pdf](#ssrn-4385668.pdf) (Value premium in China)** — A 股 size 与 value 反向(shell value)经济先验。
11. **[ssrn-4560216.pdf](#ssrn-4560216.pdf) (GPT's Idea of Factors)** — LLM 辅助 seed / 经济解释(注意:实证结论存疑)。

### Tier 3:v2 / v3 远期相关
12. **[ssrn-5178543.pdf](#ssrn-5178543.pdf) (Profitability Retrospective)** — v2 接基本面后的优先因子。
13. **[态势选因子.pdf](#态势选因子.pdf) (RSAP-DFM)** + **[态势选因子3.pdf](#态势选因子3.pdf) (HireVAE)** + **[强化学习50因子.pdf](#强化学习50因子.pdf) (AlphaPortfolio)** — v3 替代范式,概念性参考。
14. **[自动挖因子3.mht](#自动挖因子3.mht已转-txt实际是-github-readme-而非论文) (alpha-gfn)** — v3 GFlowNet 候选(注意找 AlphaSAGE 2025 正式版)。
15. **[ssrn-5080998.pdf](#ssrn-5080998.pdf) (Pairs Trading Clustering)** — partial correlation 用于 novelty test。

### Tier 4:不适用 / off-topic
16. **[市场态势选因子.pdf](#市场态势选因子.pdf) (Macro Perceptions & Anomalies)** — 美股基本面 anomaly 轮动。
17. **[ssrn-5316487.pdf](#ssrn-5316487.pdf) (VIX ETN Trading)** — 美股衍生品交易。
18. **[2024.acl-long.402.pdf](#2024.acl-long.402.pdf) (EFSA)** — 中文金融 NLP。
19. **[2407.06567v3.pdf](#2407.06567v3.pdf) (FINCON)** — LLM 多 agent 交易决策。
20. **[2502.15458v2.pdf](#2502.15458v2.pdf) (Clustered Connectedness)** — 跨市场 spillover 计量经济学。

---

## 跨论文 themes & 综合观察

1. **A 股 vs 美股的"liquidity vs profitability"分野**:[JFE 2022](#Machine-learning-in-the-Chinese-stock-mark_2022_Journal-of-Financial-Economi.pdf) 显示 A 股 liquidity 类信号是 top predictor,与美股 [ssrn-5178543](#ssrn-5178543.pdf) 主张的"profitability subsumes all"形成鲜明对比。v1 纯量价 + A 股的设定**反而是与 A 股 alpha 来源最对齐的子空间**——这是一个 design choice 的隐性优势。

2. **Capacity 是真正的 bottleneck,不是 cost**:[ssrn-5797502](#ssrn-5797502.pdf) 实证 + [ssrn-4702406](#ssrn-4702406.pdf) 量化都指向同一结论。v1 当前 fitness 重 cost 轻 capacity,**这是设计的盲点**,应该在 Phase 2-3 evaluator 设计前修正。

3. **PIT 数据是 v1 最大的 differentiator**:27 篇里**没有一篇是基于 PIT-correct 数据的**——所有 A 股因子文献(AlphaGen / AlphaForge / AlphaQCM / JFE 2022)都用 baostock 或 CSMAR 的 non-PIT 数据。v1 的 IC 一定低于这些 paper 的 number,但这是**真实可信的 IC**,而它们是**带 survivorship 的高估 IC**。这条要写入 v1 用户文档作为 baseline expectation 校准。

4. **多重检验问题在 GP mining 是真实威胁**:[ssrn-2528780](#ssrn-2528780.pdf) 给出 panel + bootstrap 的方法论;[ssrn-4560216](#ssrn-4560216.pdf) (GPT factors with Sharpe 4.49) 是反面教材;[2508.13174](#2508.13174v1.pdf) (AlphaEval) 的 5 维度评估是工程化解决方案。三篇结合可作为 v1 Phase 6 promotion gate 设计的核心 reference。

5. **mutual IC 过滤可能过度严格**:[自动挖因子1 (AlphaGen)](#自动挖因子1.pdf) 实证证明 0.9746 mutual IC 的因子组合后仍贡献 IC 0.0458 ——这对 v1 design doc §4.4 的"correlation niche penalty"是 reminder,**不要把 niche penalty 调太严**,会误杀有用的"近似但互补"因子。

6. **AlphaEval([2508.13174](#2508.13174v1.pdf))的 PFS perturbation 是非常实际的 governance test**:对 OHLCV 加 noise 后看 alpha 一致性,**v1 现在就该把这个加入 Phase 1-2 governance test 套件**——可以提前发现 mined factor 是否对小数据扰动过度敏感(overfitted)。

7. **operator set 跨论文高度一致**:[自动挖因子1 (AlphaGen)](#自动挖因子1.pdf) / [AlphaQCM](#1571_AlphaQCM_Alpha_Discovery_.pdf) / [AlphaEval](#2508.13174v1.pdf) 三篇都用了几乎一样的 22-32 个 operator(加减乘除、ts_* 系列、cs_* 系列、Greater/Less、IdxMax/IdxMin、WMA/EMA、Mad、Slope/Rsquare/Resi)——**v1 design doc §5.1 的算子设计可以直接对齐,补缺几个 v1 当前没列的(Skew, Kurt, Slope, IdxMax, IdxMin, EMA, Mad)**。

8. **NLP / LLM / 多 agent 这一支(2024.acl-long.402, 2407.06567v3)与 v1 完全平行**:这是个独立技术方向(alternative data factor),v1 不应分心。如果远期 v3 想引入新闻/舆情因子,这两篇是参考但不优先。



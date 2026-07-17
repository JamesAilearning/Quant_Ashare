# CSI800 扩池 · 虚高防护工单(跑任何回测前必须落地)

> **Status:** 架构侧 review + 工单。CSI800 扩池(lever **b** = Alpha158 breadth,非质量因子重测)现在 Step 2/3(preset + per-universe benchmark + sleeve loader prep),**尚未跑任何回测**——正是建防护的窗口。
> **红线:** 本工单三件在落地前,**不读、不信任何 CSI800 业绩数字**。和你 Gate-2/3"先建地基再跑"一个道理。

## 为什么(虚高机制)

CSI800 = CSI300(大盘)+ CSI500(中盘)。中盘流动性差:spread 宽、盘口薄、冲击成本高。**若用 CSI300 的成本假设跑 CSI800,任何 CSI500 sleeve 的"alpha"都可能是虚高**——低估的中盘 illiquidity 冒充 edge。而 CSI800 之所以"可能比 CSI300 好看",恰恰是中盘的低效;不把成本做实,你根本分不清真 edge 和被低估的 illiquidity。**一个虚高的 CSI800 win = 假阳性,会误导晋升,比没结果更糟。**

## 现状(survey origin/main @ e65ae6c)

- `config/presets/csi800.yaml` 用 **flat `slippage_bps` 5.0**(继承 CSI300),**无 CSI500 保守档**。
- 金融排除口径:**未找到**(见 §末"要澄清的")。
- CSI300/CSI500 sleeve 分开报:**在建**(`src/core/attribution_sleeve_loader.py`,Step-3 prep,尚未接 pipeline)。
- 范围隔离干净(只改 universe + benchmark,topk/n_drop/model/cadence 全 held)。

## 必须在跑回测前落地的三件

### 1. ★ CSI500 保守滑点(最关键)—— 扩现有差异化滑点框架

- 现有框架已按段调滑点(688/300 ±20%、BJ ±30%、ST ±5%)。**加一档 CSI500(中盘)保守**:中盘 spread/冲击显著高于大盘,保守幅度应**明显高于大盘 base**(具体幅度 CC 按 A 股中盘微观结构 + 现有框架量级定,**跑前写死、别试后回调**)。
- **成本敏感性带**:CSI800 回测**同时出 base 与 conservative 两档净收益**,**主判以 conservative 为准**(base 仅参考)。
- 理由:cost realism 是区分"真 edge"与"低估中盘 illiquidity"的唯一手段。

### 2. sleeve 分开报接线(诊断,已在建 → 跑前接通)

- CSI300 sleeve vs CSI500 sleeve 各自的净收益 / 换手 / 暴露分开报。
- **红旗判据(跑前写死):** 若 CSI800 的"增量 alpha"几乎全来自 CSI500 sleeve,且在 conservative 滑点下塌掉 → 判定为虚高、非 breadth 收益,不得据此晋升。

### 3. 健康验证 veto 预钉(跑前,不是跑后)

- 复用 Gate-④ 晋升健康验证(退化/合理/行为/**虚高**)。即便按既定决策**不做 CSI300-vs-CSI800 正式对照**,**健康验证的否决线也必须跑前写死**:conservative 成本后净超额、换手、单票权重、中盘集中度、CSI500-sleeve 依赖度。
- 防的是"扩完看着好就上"—— CSI800 **没有预注册**,这道 pinned veto + conservative 成本就是它唯一的护栏(替代预注册的作用)。

## 一个要澄清的(别盲目加)—— 金融排除

现役 CSI300 Alpha158 模型 **包含金融股**(银行是 CSI300 大权重、有正常价量)。交接里的"金融排除口径"很可能是**质量因子上下文**的遗留(金融股算不出盈利因子 —— Gate-2 那条),而这条 CSI800 是 **Alpha158 breadth**,金融股对价量模型完全能用。所以:

- **先确认"CSI800 到底排不排金融":**
  - (a) 若**排** → 这是**相对现役的偏离**,须给经济理由 + 口径与 Gate-2 一致地写死,并在健康验证里显式标注偏离;
  - (b) 若**不排**(与现役一致 —— 更可能对) → **别加**。
- **这不是主虚高杆,别当必做项机械塞进来。** 机械照搬质量因子的金融排除会平白砍掉一大块宇宙、且偏离现役。

## OpenSpec / 归属 / 时机

- 折进现有 CSI800 workstream(`2026-07-16-per-universe-canonical-benchmark` 或其后续 step change),作为 **"跑回测前的 guard step"**。
- 层归属都在既有边界内:滑点扩展碰 cost 层、sleeve 碰 attribution 层、健康验证碰 promotion 层。
- push 前本地 review loop(`docs/codex/local-review-loop.md`)。
- **时机:1+2+3 落地 → 才跑第一个 CSI800 回测。** §末金融问题先澄清、不阻塞 1+2+3。

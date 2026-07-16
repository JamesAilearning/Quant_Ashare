# Proposal: CSI800 战役虚高防护三件套（跑任何战役回测前的 guard step）

## Why

CSI800 = CSI300（大盘）+ CSI500（中盘）。中盘 spread 宽、盘口薄、冲击
成本高——若沿用大盘成本假设跑 CSI800，CSI500 sleeve 的"alpha"可能是
**被低估的 illiquidity 冒充 edge**。CSI800 相对 CSI300 的吸引力恰恰来自
中盘低效，不把成本做实就分不清真 edge 与虚高；一个虚高的 CSI800 win
是假阳性，会误导晋升，比没结果更糟。

(b) 地基（#365-#367）已收官；probe 底数（docs/research/
csi800_probe_brief.md，只读非决策）显示模型天然把 61.8% 权重压进中盘
腿、成本拖累系"平铺 5bps 滑点"假设下的乐观值——虚高机制不是假想，
是已观测到的倾向。本 change 在跑任何 (a) 战役回测**之前**钉死三道
护栏。**红线：三件落地前，不跑、不读、不信任何 CSI800 业绩数字**
（probe 维持其"底数非决策"地位，不作业绩证据复读）。

## 操作人决策账（2026-07-16 签，全按推荐）

- **DP-1 滑点机制 = (A) 钉死双标量敏感带**。canonical 合约的滑点是单一
  对称标量（`slippage_bps ∈ [0, 200]`），**不存在**分段滑点框架（工单
  survey 所引"688/300 ±20%、BJ ±30%、ST ±5%"是涨跌停 `limit_threshold`
  口径，且 per-instrument 阈值本身即 audit A4 backlog）——故不"扩框架"
  而是双 run 敏感带，零 runtime 改动。**(B) per-instrument 分段滑点成本
  模型记 backlog**（audit A4 同族，不挡战役）。
- **DP-2 conservative 幅度 = 20 bps 全账本平铺，跑前写死、绝不试后回调**。
  依据：A 股中盘单边冲击+价差对 topk=50 量级约 15-25bps；probe 实证
  61.8% 权重在中盘腿，全书 20bps ≈ 中盘腿按 25bps+ 计而大盘腿被过罚
  ——过罚方向即保守方向。round-trip 成本由 ~25bps 升至 ~55bps（×2.2），
  显著下压净超额正是测试目的。
- **DP-3 金融 = 不排除，与现役一致**。`ex_financials` 是质量因子战役
  宇宙口径（金融股算不出盈利因子）；Alpha158 价量模型金融完全可用，
  现役含金融、probe 双边含金融。排除反而制造与现役的无理由偏离。与
  质量战役口径的差异是**有意的**，就此记录。
- **DP-4 veto 数字表 = 见 spec delta**（判据先于数据冻结——本质是轻量
  预注册；单杠杆单对照的多重性面远小于九 trial 战役，故不动用全套
  FWER 机器，但"跑前写死、跑后不改"的纪律等同）。

## What changes

- **NEW capability `v2-csi800-expansion-guards`**：CSI800 战役回测的
  前置 guard 契约——
  1. **成本敏感带**：每个战役决策 run 必须成对（base 5bps +
     conservative 20bps），**主判 conservative**，base 仅参考；
  2. **sleeve 分解接线**：CSI300/CSI500 sleeve 的净贡献/权重/换手分开
     报（消费 #366 的 `attribution_sleeve_loader`，经显式 config 键接入
     attribution 层）；
  3. **虚高 veto 表**：五条判据跑前钉死（数字在 spec delta），任一触发
     即该 run 不得作为晋升依据。
- 金融不排除的决定 + (B) backlog 注记随契约入档。
- NO canonical runtime 语义变更：敏感带是 config 层双 run；sleeve 接线
  只触 attribution 诊断层（不改官方超额数字）；veto 是研究纪律层。

## Impact

- 实现分两个后续 PR（本提案先行签署）：
  1. guard-1+3：conservative 战役 preset（`csi800_conservative.yaml`，
     slippage_bps=20，过治理配对测试）+ veto 表治理测试（存在性+数字
     pin）；
  2. guard-2：attribution 层 sleeve 接线（pipeline + walk-forward 的
     显式 config 键，与 industry 分组源互斥，覆盖界守卫 fail-loud 透传）。
- 三件全绿后才允许点第一个 CSI800 战役回测（walk-forward 全窗）。

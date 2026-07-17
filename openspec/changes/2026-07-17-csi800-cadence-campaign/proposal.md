# Proposal: CSI800 降换手战役（节奏 × breadth）+ 晋升 attestation 前置

## Why

CSI800 首发战役（#373，veto① 触发不予晋升）给出了干净的机制诊断：
**breadth 毛 alpha 是真的**（毛超额 +12.58% vs csi300 参照 +8.83%，
+3.75pp 优势；非换手买来——veto③ 比率 0.99×；非中盘单边注——csi500
时均 48.2%），但 **24.5× 年化单边换手 × 中盘成本**把它全部吃掉：
20 bps 保守档净超额 −1.02%，盈亏平衡滑点 ≈17-18 bps 落在中盘实际
成本区间内部。瓶颈不是信号是执行节奏。

阶段 7b（csi300）已证明降频机制**有效但原料不够**：周频（N=5）毛差
−3.88pp、净差 +1.33pp（方向对，+2.73% 毛基数太薄跨不过噪声地板），
预注册裁决 DEAD-END、"禁在（csi300 的标签×节奏）二维加格"。csi800
正是当时指向的"原料升级"：毛基数 12.58%（4.6 倍于 csi300 的 2.73%），
若周频省下 ~4/5 换手成本（20bps 档 ~5.9pp）而毛塌缩幅度与 7b 同量级
（个位 pp），净超额有真实机会转正。本战役用**已有机器**（7a cadence
三字段 + #368-#373 guard 全家）在新原料上补上这一维，不新建 runtime
机制。

另有一个**硬前置**：#373 codex r10 终态下，attach 工装在现生产者
形态结构性永不发 `promotion_eligible=true`（参照 positions 无不可变
内容锚）。若本战役赢了而 attestation 未落地，只能拿到"diagnostic
WIN"且**须重跑**才能晋升（摘要必须在 run 时盖章，事后补盖不构成
认证）。故生产者 attestation 作为本 change 的第一阶段，先落地再点火。

## 操作人决策账（DP 表——签字后冻结，跑前写死）

- **DP-1 臂设计 = 单维加节奏，三发新 run**：N=5 双档配对
  （base 5 bps + conservative 20 bps）+ csi300 N=5 参照（veto③ 基准，
  同配置含 cadence）。N=1 三发不重跑——#373 已证工件（pair v2 +
  attach 五项勾验）就是 N=1 对照臂。不加标签维（7b "禁加格"纪律在
  节奏×标签平面继续有效；本战役是节奏 × **宇宙**，新原料单维）。
- **DP-2 节奏参数 = N=5，phase=0，anchor=fold_phase**（与 7b primary
  同构；iso_week 仅作胜者复核切片，沿 7b 承诺）。**禁试后调 N**——
  N∈{1,5}，无网格；改 N 须新 OpenSpec 变更并作废既有结果。
- **DP-3 主判据（预注册双条件，AND）**：
  1. conservative（20 bps）N5 全窗净超额年化 **> 0**（绝对判据）；
  2. **毛塌缩否决**：N5 毛超额年化 < N1 毛超额年化 × **50%** →
     判"省成本靠杀 alpha"，即便净转正也不算 WIN（假阳性防线；
     7b 实测毛塌缩幅度 −3.88pp/2.73% 供量级参照，csi800 若同量级
     应远在 50% 线上）。
  两条同过 = WIN；任一不过 = 如实入档不晋升。数字跑后不得修改。
- **DP-4 veto 表沿用 + veto③ 参照口径修订**：五条数字原样（20bps/
  80%/1.5×/campaign_v1/75%+10%）；veto③ 的参照 = **csi300 N5 参照**
  （"同配置"必须含 cadence 三字段，否则周频 csi800 对日频 csi300 的
  比率无意义地趋零、veto③ 失去牙齿）。attach 工装的参照绑定 diff
  白名单不变（cadence 三字段在三发内一致，不入 diff）。
- **DP-5 晋升 attestation 前置（本 change 唯一 runtime 触点）**：
  1. fold report 写盘时对已持久化的 positions 字节盖 `positions_sha256`
     （manifest 同步），fold report schema 版本升级；
  2. pair 工件升 **v3**：参照 run 作为第三方入证（run_id/config_sha256/
     report_sha256/fold_report_sha256 同款四件）；
  3. attach 验全链摘要（pair→fold report 哈希→positions_sha256→
     盘面字节），达标即 `reference_content_binding =
     "producer_digest_certified"`（r10 预留档位），晋升门可开。
  三件全绿并入 main 之后才允许点火（沿 #368 "护栏先行"纪律）。
- **DP-6 敏感带与约束不动**：20 bps 预注册值、敏感带成对构造、
  `campaign_risk_constraints_v1`、金融不排除——全部沿 #368/#372 原样。

## What changes

- **MODIFIED capability `v2-csi800-expansion-guards`**：
  1. 生产者 positions attestation（DP-5-1，runtime）；
  2. 配对工件 v3 三方认证 + attach 摘要链验证（DP-5-2/3，工具层）；
  3. N5 降换手战役预注册（DP-1/2/3/4，研究纪律层）：三 preset、
     主判据双条件、veto③ 参照口径、治理 pin（N5 三发内部配对 diff
     恰 {slippage_bps, output_dir} 等沿用；N5 vs N1 同名 preset 恰差
     {rebalance_cadence_days, output_dir}——phase/anchor 显式写死同值）。
- NO 官方超额语义变更：attestation 是工件字段追加；cadence 用 7a
  已有机器；判据是研究纪律层。

## Impact

- 分阶段 PR（本提案先行签署）：
  1. PR-A：生产者 attestation（fold report/manifest `positions_sha256`
     + schema bump + 测试）；
  2. PR-B：pair v3 三方认证 + attach 全链摘要验证 +
     `producer_digest_certified` 可达 + 测试；
  3. PR-C：三个 N5 战役 preset + 治理 pin 扩展；
  4. 点火（单独授权）：三发串行 → pair v3 + attach → 简报 + 数字
     STOP 签字。
- 顺带收口：`2026-07-16-csi800-antiinflation-guards` 与
  `2026-07-16-per-universe-canonical-benchmark` 两个已 ship 的 change
  随 PR-A 归档（openspec/changes/archive/）。
- 若 WIN：晋升流程首次具备完整机器可验证据链；若 LOSE：csi800
  breadth 在"降频后仍不过保守成本关"下收束，方向 A 全链闭环。

# 阶段8 Gate-4A 战役档案 + 复盘（quality_profitability_v1）

> 本仓第一个完整 confirmatory 预注册战役的收束档案（方向 A，操作人签，
> 2026-07-16）。结论、流程账、防线形态、复盘教训四部分；数字全部原样，
> 可复算证据见 [gate4a_fwer_evidence.json](gate4a_fwer_evidence.json)。

## 1. 结论（有效排除性结果）

**盈利质量"原料"方向（C1_GPA / C2_PROF / C3_cash_based_OP 及全部注册
变体）在 dev 窗（2020Q2→2024Q4，csi300_pit_ex_financials，十分位内
排名，季度视野）无可辨别正向选股信息。** 全批 FWER = CLEAN_NEGATIVE：

| trial | n | rank_ic_mean | t |
|---|---|---|---|
| C1_GPA | 19 | −0.0392 | −0.82 |
| C2_PROF | 19 | −0.0414 | −1.23 |
| C3_cash_based_OP | 19 | −0.0252 | −0.62 |
| C1_from_2018 | 23 | −0.0081 | −0.19（max） |
| exclude_fold_0（派生） | 18 | — | −1.50 |
| holding_semiannual | 9 | −0.0662 | −1.08 |
| holding_annual | 4 | −0.0657 | −0.60 |
| st_off | 19 | −0.0392 | −0.82 |
| size_decile_variants（五分位） | 19 | −0.0416 | −0.87 |

九 trial 观测 t 全负；max t = −0.19，距 2.85 硬地板与 bootstrap q95
bar 双门槛皆远。**2025 holdout 保持未揭盲**（无候选可送终裁；
`holdout_unblinded=false` 为签字冻结的一次性状态，不可反悔）。
按冻结三态规则落 `reject_iff 干净阴性`——与降频/阶段6标签/阶段7b
同类的有效排除：这不是失败，是花一次严格实验的成本永久关掉一扇门。

关键诊断：`exclude_fold_0` 反而恶化到 −1.50——C1 的"接近零"表面均值
由 2020Q2 COVID 反弹单折拉升，删除后全族更负。honesty_envelope 的
预期（"大概率经历多次阴性"）如实兑现。

## 2. 流程账（全链可审计）

- **PR 链**：#340-#346（Gate-2 契约+桥+修正）→ #347（Step-A 全量
  ingest+覆盖报告）→ #351（消歧）→ #352（Step-B 冻结包，18 轮 codex）
  → #353（归档）→ #354/#355（评估器+DP3）→ #356（spec）→ #357（C1
  结果）→ #358/#359（C2/C3）→ #360（六切片）→ #361（FWER 收束）。
- **Ledger E001-E028** 全闭环：注册（E001-E006）→ 冻结自证（E010）→
  每个决策级 run 前登记+后登记成对（E011/E012、E013-E016）→ 切片语义
  pin（E017-E022，操作人全签）→ 切片结果+FWER 裁决（E023-E028）。
  无一 run 先斩后奏，无一结果漏报。
- **三 DP 签署**（2026-07-15）：陈旧度 20 交易日 / JSON 单写 / 跨度零
  观测硬中止。切片语义与 FWER 机制亦全部操作人签署后方点火。
- **报告**：gate4a_c{1,2,3}_ic_report.md + gate4a_slices_fwer_report.md
  + 本档案；工具 = gate4a_ic_evaluator.py / gate4a_fwer_adjudication.py。

## 3. 防线最终形态（下次战役的起点模板）

- **gate3_prereg_gate v16 十四层**：plan/ledger 冻结+时序自证、清树、
  manifest 全量 re-hash、候选 config 链绑定、canonical PIT battery
  只增不换、窗口从冻结 config 链派生、终裁窗精确+一次性揭盲全局终态、
  链上文件全须冻结件、值层零 env 占位符、设计章戳（cadence 三元组+
  宇宙）、揭盲横幅只在全检查过后打印。rehearsal 24 场景全 PASS。
- **评估器防线**（codex 五 PR 累计 19 轮对抗沉淀）：canonical stamp
  镜像（fillable 规则）、四层计数宇宙过滤（成员−金融−ST−微结构）、
  PITDataProvider 路由（退市后掩码）、跨端点报告期对齐、相邻期 Δ 服务
  （期洞不跨差）、总市值跨度覆盖硬断言、切片工件语义如实回显、
  注册 ID 单一权威名集。
- **FWER 裁决防线**：工件身份+冻结几何逐位校验、非有限值拒、重复映射
  拒、确定性 seed、可复算证据入 git。

## 4. 复盘：下次预注册要避的坑

1. **切片操作语义要在冻结时 pin 死**。本役六切片冻结时只有名字，点火
   前补签了一整轮语义（E017-E022）——流程守住了，但那是额外一轮
   审签；下次注册切片时直接把 stamp 几何/参数写进冻结件。
2. **注册切片前做零成本可行性 probe**。st_off 在 csi300 ex-fin 上
   全折零命中（成分股几乎不 ST）——切片实证空转，白占一个 N。注册前
   一行 SQL/一次 mask 计数就能预判。
3. **FWER 机制 pin 应包含最小样本约束**。稀疏 trial（annual n=4）在
   块重采下抽出重复位置→null 重尾→bootstrap bar 虚高（+14.5）。本役
   靠 2.85 硬地板兜底不影响裁决；若未来家族里有边缘正信号，虚高的
   bar 会造成过度保守——机制 pin 时写明 per-trial 最小 n 或对稀疏
   trial 的处理。
4. **YTD 混截面是冻结设计的已知噪声源**。A 股利润表年初累计制下，
   同一截面混 Q1/H1/Q3/FY 申报；冻结变换白名单锁死后不可事后"修"。
   下次设计公式时在冻结前就决定是否注册 TTM 变换。
5. **未跟踪文件会挡 gate clean-tree**。两次靠"临时移 scratchpad +
   sha 校验放回"（方案 b）解决；根治 = 别让工单草稿裸放 repo 树里
   （本役后已入库）。
6. **流程上有效的做法，保留**：per-候选冻结 preset + config 链绑定
   （裸 CLI 声明零信任）；run 前后 ledger 成对登记；每轮 codex
   finding 全修+场景钉守；"声称的强制必须有验证"；数字原样呈报
   （阴性照报，verdict 归操作人）。

## 5. 状态与后续

战役封存。评估器与 FWER 脚本是**协议绑定的模板**而非即插即用件：
evaluator 硬编码本协议的候选注册表（公式/字段/端点/冻结 stub 映射）与
plan 路径，adjudication 拒收 `protocol_id != quality_profitability_v1`
的工件并锁定九 trial 身份/几何表。新家族复用 = 复制三件套骨架后
**逐处替换协议常量并重走冻结+审签流程**（这些绑定正是防串协议的
防线，不是缺陷）。下一段方向由操作人另发工单（已点名 CSI800 扩池
方向），本档案不预设任何研究假设。

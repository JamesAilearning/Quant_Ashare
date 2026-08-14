# Fundamental GP panel bridge + leakage defenses (阶段8 基本面方向 · 第2步)

## Why

四条独立证据线已证明量价因子在 CSI800 上到头（阶段8 GP 穷尽搜索只收敛到换手率变体
pv001，加进完整策略后边际贡献为负，被纪律拦截）。战场转向基本面/质量信息源。

勘察（2026-08-13）确认地基已就位：财报 PIT 层（版本保留 store + PIT 契约 +
`FinancialPITDataView`）已投产，CSI800 财报已 ingest（627→2142 issuers），覆盖率
经实测不劣于 CSI300（19 项里 16 项 floor 更严或持平，仅 3 项更松且均为已知弱字段）。

**唯一缺的是把 as-of 财务值变成 GP 能吃的 date×instrument 面板的那一步 —— 它今天
不存在，也没有任何机器检查守它。**

这一步是整条链路的泄漏风险集中点。GP 会穷尽搜索并精准放大任何 PIT 泄漏：数据里若
有未来信息，它一定会挖出来，造出"OOS t=10 的神因子"，纯属泄漏。而财务数据与现有
量价终端在 PIT 性质上**根本不同**：

* 现有 `$pe`/`$turnover_rate` 等 daily_basic 终端按 `trade_date` 键入、当日可观测，
  面板化天然 PIT（`qlib_bin_builder` 直接写 bin）；
* 财务报表是**事件日期数据**，PIT 语义全在 `available_from_trade_date`（有效公告日
  之后**严格第一个**交易日）。最典型的泄漏写法就是按 `end_date`/报告期键入，或从
  公告日**当日**（而非严格次日）ffill —— 两者都会静默产生未来信息。

因此本 change 的核心主张：**面板化模块与它的泄漏防线必须同时落地，防线不可后补。**
模块尚不存在正是设计防线的最好时机 —— 一旦有了"能跑出因子"的桥而防线滞后，第一个
诱人的高 IC 结果就会成为放松防线的压力源。

## What changes

### 1. 面板化桥模块 + 它依赖的两处上游扩展

新增 `src/research/fundamental_panel.py`：把 `FinancialPITDataView.as_of` 的单日横
截面堆叠成 GP 消费的 `dict[field -> date×instrument DataFrame]`。

**gate 不需要改签**（这一点经实测成立）：

* 隔离 gate 的 reverse 向禁止的是 **`src/` 内非 `src/research/` 模块** import
  `src.research.*`（`tests/governance/test_financial_pit_view_isolation.py`）；桥模块
  本身在 `src/research/` 内，import view 完全合法；
* GP 引擎**通过参数接收** panel（`gp_engine.py:11`；`miner._build_pit_panel` 是既有的
  "外部构造 panel 再传入"范式）；
* 编排放在 `scripts/research/` 层 —— 不在 `src/` 下，不受 reverse gate 约束，
  `scripts/research/gate4a_ic_evaluator.py` 是既有先例。

数据以**参数**流入 GP 而非以 **import 依赖**流入，D5 gate 与隔离 gate 保持原样。

**但"零改动"是错的（codex #427 P1，本提案的核心更正）。** 初版由"不必改 gate"错误
推广出"不必改任何东西"，实测证否 —— 桥要能真正跑起来，还需要两处上游扩展：

* **GP 终端注册**（`src/factor_mining/` 非零改动）：`FeatureRegistry.V1` 只有 12 个
  终端（4 价 + 2 量 + 6 daily_basic），`grammar._random_leaf` 把 `allowed_terminals`
  与 `V1_*` 取交集，`Terminal` 拒绝注册表外的名字。因此裸财务字段名产生不出可用白
  名单，而改写成 `$…` 形式仍是未知终端 —— 无论面板怎么传，GP 都长不出基本面表达式。
  本 change 因此把**基本面终端注册**（新终端组 + 其类型/taint 规则 + 对应测试）纳入
  scope。这不削弱 D5：注册的是终端符号与类型，不引入任何 qlib/PIT import。
* **view 的公开 provenance 响应**（`src/research/financial_pit_view.py` 非零改动）：
  `as_of` 今天返回值列 + 可选的 `_report_period__<endpoint>` / `__prior`，**不返回
  `available_from_trade_date`**。若同时冻结 view，桥就只能读私有内部/裸 store，或从
  抽样日期与值变化去反推 —— 那正是"证据"该排除的东西。本 change 因此把**给 view 增
  加一个公开的、携带 provenance 的响应**纳入 scope，让证据由**唯一门**给出，而不是
  由桥私自推断。证据记的是"**服务了哪条披露**"，不是"值是否有效"：被服务记录的字段
  值为 NA 时**仍带其可用日**，只有尚无任何已公告记录时证据才为 NA（codex #427 r3
  P2）—— 否则"两期皆 NA"的字段在 (iii) 平移下值与证据都不变，会误拒正确实现。

桥模块的输出契约：面板 **加上每个 field 的 `available_from` 证据帧**（同形状），
**外加 view 已有的 `report_period` / `prior` provenance**（见 §1b）。证据帧不是可选
的调试品，而是防线 (i) 的机器可验对象。

### 1b. 面板必须携带跨端点与跨期的对齐 provenance

view 的契约明确：**各 endpoint 独立服务**，消费者须用 `include_report_periods` 防止
跨端点混季比率；差分类公式须用 `include_prior_period` 取相邻期。而起步三因子恰好两
者都要：GP/A 跨 income×balancesheet（比率必须同期），资产增长与 C3 应计要相邻期差分。

若面板只堆当前值（初版契约），最小链路会**静默算出混季比率**，或根本算不出差分。
因此面板契约扩展为携带这两种 provenance。

**但同期强制不能放在面板层**（codex #427 r2 P1）：桥在表达式产生之前运行，evaluator
又把每个终端各自解析为值帧，所以桥无从知道候选是否跨端点 —— 全局遮蔽会误杀合法的
同端点表达式，不遮蔽则混季比率仍可达。强制点因此放在**求值路径**（按该表达式实际
引用的终端判断），这使 `evaluator.py` 进入 scope。

**遮蔽点必须落在第一个跨端点子树**（操作数跨端点的最低节点），在任何父级滚动/横截面
算子消费它之前 —— **只遮蔽最终 cell 太晚**（codex #427 r3 P1）：
`ts_mean(div_safe($revenue, $total_assets), 5)` 在交易日 T 可能两端点同期，但滚动窗内
**更早日期**的混季比率仍会被平均进来，在 T 产出一个表达式末端检查抓不到的、被污染的
非 NA 值。须有嵌套表达式回归用例。

**且 provenance 得有路径抵达求值。** 桥把 `periods` 作为第三个返回对象，而现有 GP 调用
是 `evaluate_factor(expr, panel, ...)` 只传值面板 —— 新纳入 scope 的 evaluator 根本
**拿不到** report periods（codex #427 r3 P1）。本 change 因此须定义并接线一个带
provenance 的面板/求值参数（或明确一个在求值前把 period 帧打包进 mapping 的 adapter）；
若这条通路需要动 `gp_engine.py` 的传参，则 `gp_engine.py` 也进 scope。

**另有一处致命实现细节**（同轮 P1）：view 把 instrument 归一化为 store 原生 `ts_code`
（`600000.SH`），而 factor_mining 面板与 forward-return 用 qlib 标签（`SH600000`），
两者**零交集**（repo 已有 `test_namespace_mismatch_refuses` 记录）。面板必须冻结为与
GP 输入同一命名空间，并以**逐列精确比对**的测试守住。

### 2. 四件套泄漏防线（与桥同一个 change 落地）

* **(i) as-of 断言** — 属性层：合成小店上断言"`available_from` 前必为 NA、自该日起
  等于 disclosure-of-record 值、**公告日当天仍不可见**（严格次日）、restated 期仍服务
  original"；运行时层：桥输出证据帧后自检 `(available_from <= T).all()`，并对抽样日期
  与边界日（公告日、前一日、后首个交易日）核对 `panel[T] == view.as_of(T)`。
* **(ii) 合成金丝雀（已按 codex #427 P1 重设计）** — 初版的"未来值金丝雀"是**装饰性
  的**：一个由 forward return 派生、却带着满足 `available_from <= T` 的**看似合法证据**
  的值，可用性断言无法与真实财报值区分；让 fixture 省略证据，测到的只是"拒绝无证据
  输入"，而同样的语义泄漏只要抄一份元数据就能存活。金丝雀必须腐蚀 builder **能真正
  **计算出**的不变量，因此定为两个：
  ① **提前公告**：证据 > 交易日 —— 直接违反 `available_from <= T`，可计算；
  ② **可用日单调性**：同一 instrument 的证据序列必须随交易日**非递减**（carry-forward
     只会前进到更新的已公告期）—— 未来信息回填会打破单调性，纯结构不变量，无需
     知道值的语义即可判定。
  **显式排除**"证据-值不同源"金丝雀（codex #427 r2 P1）：初次修订曾把它列为第三条，
  但 builder 没有独立的记录身份可用来识破"P₂ 的值贴着 P₁ 的 provenance"这个谎；若
  唯一路径被完全绕过，builder 更是根本不运行。值与 provenance 的对应性是 **view 的
  不变量**，由 canonical PIT battery 守，不在面板层重述 —— 否则那条测试只是在验证
  自己的 mock。
  **任何金丝雀存活到 factor_pool 准入 = 红。**
* **(iii) 公告日平移敏感性（最锋利的一把刀；断言按 codex #427 P2 分层）** — 把店内有效
  公告日整体后移 N∈{5,10,21} 交易日重建面板：
  - **无条件断言**：**值 + 证据一起**的哈希必须改变。**不变 ⇒ 面板化根本没消费公告日
    （极可能按 `end_date` 键入）= 泄漏在别处，直接 REFUSE。** 这条不可绕过 —— 它从
    **行为**上验证公告日真被消费，而非验证代码"看起来"用了它。只哈希值会误拒合法实现
    （codex #427 r2 P2）：延迟的申报可能重复上期同值、或该字段两期皆 NA，值不变而
    provenance 已变；把证据纳入哈希既保住无条件性，又不制造假失败。
  - **条件断言**：IC 序列的变化只在**刻意构造的确定性 fixture** 上要求（该 fixture 保
    证平移会改变被评估日的取值或横截面 rank），并明确容差。理由：一个**正确的**
    公告日感知实现也可能只改字节不改 IC —— 平移后的披露仍落在采样评估日之间、rank
    未变、或候选本身为常数/全缺失。无条件要求两者都变会误拒合法实现。
* **(iv) prereg 门挂接** — 上述测试进战役门的 PIT battery（沿 `--extra-pit-case` 的
  **append-only、不可替换**语义）；新增两个 rehearsal 场景（注入探针 / 金丝雀存活均须
  致门 REFUSE，仿 R6）；桥模块与平移脚本进 `FROZEN_ARTIFACTS`；终端集经
  `grammar.allowed_terminals` 白名单冻结（pv_incremental 先例），使 GP 只能在预注册的
  基本面字段上繁殖。

### 3. PIT 行业 as-of 接口预留（本 change 只留接口，不实现）

行业中性化已定走**路径 B（真 PIT range 模式）**：静态快照的 PIT 不正确性正好会被 GP
放大（用 2026 行业标签给 2018 截面分组 = 系统性未来信息），花大力气把财报 PIT 做到
0.1-0.4% 残余，不能在行业这一步引入跨年污染。

路径 B 本身是**独立的后续 change**（publisher v2 保留 `in_date`/`out_date` 发 range
artifact + range 模式 as-of 消费者 + `within_industry_rank` + 一次性抓取签收 + 新注册）。
但本 change 必须**预留接口**以免返工：桥模块的面板构造签名接受一个可选的
"as-of 分组标签解析器"（`(trade_date, instruments) -> labels`），今天传 `None`，将来
接 PIT 行业 artifact 时无需改动桥的形状或其防线。

## Impact

* 新增：`src/research/fundamental_panel.py`（桥）、其测试、金丝雀测试、平移敏感性
  诊断脚本（`scripts/research/`）。
* **修改**（初版误称零改动，codex #427 两轮更正）：
  - `src/factor_mining/grammar.py` —— 注册基本面终端组 + 其类型/taint 规则；
  - `src/factor_mining/evaluator.py` —— 跨端点同期强制（见 §1b：必须在**知道表达式**
    的地方做，面板层做不到）；
  - `src/research/financial_pit_view.py` —— 增加公开的 provenance 响应，使
    `available_from_trade_date` 由唯一门给出而非被桥反推。
  三处均不引入任何 qlib/PIT import，D5 不受影响。
  - `src/factor_mining/gp_engine.py` —— **仅当** period provenance 的传参通路必须
    经由它时（见 §1b 末段）；若 adapter 方案足够，则不动。
* **仍然零改动**：`src/factor_mining/pit_adapter.py`、`src/data/pit/*`、canonical
  runtime。
* **零 gate 改签**：D5 gate 与财务 PIT 隔离 gate 均保持原样并继续通过。
* 新 spec capability `v2-fundamental-gp-panel`：面板化的 PIT 契约 + 防线要求。
* 不训练、不挖因子、不动生产数据：本 change 只交付桥与防线；真正的基本面 GP 战役是
  后续的预注册包（新 charter + 新未触碰窗 + 新 ledger）。

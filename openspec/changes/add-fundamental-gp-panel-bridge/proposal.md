# Fundamental GP panel bridge + leakage defenses (阶段8 基本面方向 · 第2步)

## Why

四条独立证据线已证明量价因子在 CSI800 上到头（阶段8 GP 穷尽搜索只收敛到换手率变体
pv001，加进完整策略后边际贡献为负，被纪律拦截）。战场转向基本面/质量信息源。

勘察（2026-08-13）确认地基已就位：财报 PIT 层（版本保留 store + PIT 契约 +
`FinancialPITDataView`）已投产，CSI800 财报已 ingest（627→2142 issuers），覆盖率
经实测不劣于 CSI300（19 个字段 14 个 floor 更严）。

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

### 1. 面板化桥模块（研究侧，不碰任何 gate）

新增 `src/research/fundamental_panel.py`：把 `FinancialPITDataView.as_of` 的单日横
截面堆叠成 GP 消费的 `dict[field -> date×instrument DataFrame]`。

**关键设计：零 gate 改签。** 勘察一度认为需要显式改签隔离 gate，实测后确认不必：

* 隔离 gate 的 reverse 向禁止的是 **`src/` 内非 `src/research/` 模块** import
  `src.research.*`（`tests/governance/test_financial_pit_view_isolation.py`）；桥模块
  本身在 `src/research/` 内，import view 完全合法；
* GP 引擎**通过参数接收** panel（`gp_engine.py:11` "panel + forward-return data via
  parameters"；`miner._build_pit_panel` 是既有的"外部构造 panel 再传入"范式）；
* 编排放在 `scripts/research/` 层 —— 它不在 `src/` 下，不受 reverse gate 约束，且
  `scripts/research/gate4a_ic_evaluator.py` 已是"scripts 层消费 view"的既有先例。

于是数据以**参数**流入 GP 而非以 **import 依赖**流入：`src/factor_mining/` 零改动、
零新 import，D5 gate 与隔离 gate 都不需要改签。这比改签更安全 —— 边界没有被削弱，
只是被正确地绕开了。

桥模块的输出契约：面板 **加上每个 field 的 `available_from` 证据帧**（同形状的
date×instrument 帧，记录该 cell 所服务记录的可用日）。证据帧不是可选的调试品，而是
防线 (i) 的机器可验对象 —— 没有它，"面板是 PIT 干净的"就只是一句话。

### 2. 四件套泄漏防线（与桥同一个 change 落地）

* **(i) as-of 断言** — 属性层：合成小店上断言"`available_from` 前必为 NA、自该日起
  等于 disclosure-of-record 值、**公告日当天仍不可见**（严格次日）、restated 期仍服务
  original"；运行时层：桥输出证据帧后自检 `(available_from <= T).all()`，并对抽样日期
  与边界日（公告日、前一日、后首个交易日）核对 `panel[T] == view.as_of(T)`。
* **(ii) 合成金丝雀** — ①未来值金丝雀：合成字段 T 日值 = T 日 forward_return，桥必须
  **拒绝**（拿不到 available_from 证据即拒）；②提前公告金丝雀：造 `ann_date > T` 却被
  服务于 T 的记录，面板化必须失败。**任何金丝雀存活到 factor_pool 准入 = 红。**
* **(iii) 公告日平移敏感性（最锋利的一把刀）** — 把店内有效公告日整体后移
  N∈{5,10,21} 交易日重建面板，断言①面板值哈希必须变②IC 序列差异超容差。
  **若平移后完全不变 ⇒ 面板化根本没消费公告日（极可能按 end_date 键入）= 泄漏在
  别处，直接 REFUSE。** 这一件不可被绕过：它从**行为**上验证公告日真的被消费了，而
  不是验证代码"看起来"用了它。
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
* **零改动**：`src/factor_mining/`（含 pit_adapter、grammar、gp_engine）、
  `src/research/financial_pit_view.py`、`src/data/pit/*`、canonical runtime 全部不动。
* **零 gate 改签**：D5 gate 与财务 PIT 隔离 gate 均保持原样并继续通过。
* 新 spec capability `v2-fundamental-gp-panel`：面板化的 PIT 契约 + 防线要求。
* 不训练、不挖因子、不动生产数据：本 change 只交付桥与防线；真正的基本面 GP 战役是
  后续的预注册包（新 charter + 新未触碰窗 + 新 ledger）。

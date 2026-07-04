# PIT 穿透 SignalAnalyzer + PerformanceAttribution(审计 P2 / P0-6 follow-up)

## Why

`SignalAnalyzer._fetch_returns` 与 `PerformanceAttribution._get_instrument_returns`
仍直连 `qlib.data.D.features`,无 PIT opt-in——代码里两处 `TODO(P0-6 follow-up)` +
WARN 日志是自我承认的缺口,治理白名单
(`test_pit_provider_is_sole_qlib_features_caller.py`)各挂 1 个豁免计数。后果:
**退市后被 qlib bundle 前向填充的 close 进入 IC 与归因**——退市股在退市日前最后
几天的前向收益窗口(T+1→T+1+period)跨过退市日,取到的是假填充价而非 NaN,
IC 截面被污染,归因的成分收益同理。这是量化正确性缺陷:数值"看起来平滑",
实际是幸存者伪影。

**Step 0 爆炸半径已测定(2026-07-03,只读)**:REGEN-2 回放宇宙 481 只中
**10 只在 2018–2025 窗口内退市**(SH600068 2021-09-13、SZ002411 2023-07-12、
SZ000671 2023-08-16、SZ000961 2024-07-11、SH600297 2024-08-28、SZ000413
2024-10-11、SH600837 2025-03-04、SH600705 2025-05-27、SH601989 2025-09-05、
SZ000627 2025-09-30)→ PIT 穿透**必动锚的 `ic_1d`/`ic_5d`**(散布于含退市事件
的折)→ 本 change 走**有计划的基线重签**流程。回测三指标
(annualized_return / max_drawdown / information_ratio)来自
`excess_return_with_cost`,不经 SignalAnalyzer,**不动**(backtest_runner 的
PIT 接线不在本 change 范围)。

**Step 0 的三个设计修正(相对审计计划的假设)**:

1. "canonical 路径显式传入"是**全新接线**——`WalkForwardEngine`/`Pipeline`
   今天完全没有 PIT provider(backtest_runner 的可选参数只是模式先例,
   canonical 路径无人传)。接线需要配置面:两个 config 新增
   `delisted_registry_path`(默认空 = 不构造 provider = 现状 WARN)。
2. REGEN-2 mini-bundle **没有 delisted registry**(只有
   namechanges/calendars/features/instruments)——replay 脚本要跟随 canonical
   语义,fixture 需新增 **mini registry**(生产 registry 全量仅 326 行/14KB,
   直接收录;**reference data,走"agent 拉取、操作员过目签字"流程**)。
3. **标签打架墙(实现期发现)**:`PITDataProvider._init_qlib` 硬 pin
   `post_adjusted`,而 canonical walk-forward / replay 运行时声明
   `pre_adjusted`——`data_adjust_mode` 经核实是**纯声明标签**(不改变 qlib
   数据读取;同一 PIT bundle 两个视角两个叫法),但 `init_qlib_canonical`
   的单例相等检查让两个标签在一个进程里互斥 → 在 canonical 引擎里构造
   provider 会**构造期必炸**。最小诚实修法:provider 的
   `data_adjust_mode`/`region` **参数化**(默认 None→POST / "cn",
   daily_recommend 等既有调用方逐位不变),canonical 接线传调用方运行时的
   同款标签 → provider 的 init 成为幂等 no-op。

## What changes

沿用 backtest_runner/factor_analyzer 的既有模式(**可选 `pit_provider` 参数 +
缺席时 WARN**,不发明新机制),四层:

1. **两个分析器加 opt-in**:`SignalAnalyzer.analyze(..., pit_provider=None)`、
   `PerformanceAttribution.analyze(..., pit_provider=None)`;传入时
   `_fetch_returns`/`_get_instrument_returns` 走 `pit_provider.get_features`
   (post-delist mask 生效),缺席时保持现状 + WARN(独立调用者不破坏)。
2. **canonical 接线**:`WalkForwardConfig`/`PipelineConfig` 新增
   `delisted_registry_path: str = ""`;非空时引擎在 run 起点构造一次
   `PITDataProvider`(provider_uri 对齐校验沿用 backtest_runner 先例)并传给
   两个分析器;为空时不构造(WARN 路径,现状)。canonical 生产配置
   (`config_walk.yaml`)填入生产 registry 路径。
3. **replay 同步 + 基线重签**:replay 脚本构造同款 provider(指向 fixture mini
   registry),使锚语义与生产一致;基线重生成**必须在 canonical-pinned 环境
   (CI)**完成——本机 off-pin(numpy 2.4.4),fold-0 tie-break 不可复现,
   红线禁止本机重生成。重签 PR 附旧新逐折 diff 表 + 每折变化与退市事件的
   对应解释(方向必须可归因到上述 10 只)。
4. **治理白名单**:两个文件的豁免条目改为"opt-in + WARN 兜底"注释与计数,
   逐条说明(这是该测试的设计用途,非绕过)。

## PR 拆分

- **PR-1(锚中性,先行)**:PerformanceAttribution 穿透 + 配置面 + 引擎接线
  (归因不在锚内;CI 的 REGEN-2 leg 绿 = 证明)。
- **PR-2(锚敏感,单独)**:SignalAnalyzer 穿透 + replay 接线 + mini registry
  fixture(操作员签字)+ **基线重签**(CI 重生成),PR 描述明确
  "intentional semantic change",引用本 proposal 与 Step 0 证据表。

## What does NOT change

- backtest_runner / microstructure mask 的 PIT 接线现状(各自已有 opt-in,
  canonical 是否给它们接线是**独立的未来决策**,不在本 change);
- 回测三指标的语义与数值(锚中的 return/dd/IR 列必须逐位不动——PR-2 重签
  diff 表里这三列若有任何漂移 = 停下调查);
- `pit_provider is None` 时两个分析器的行为(WARN 路径逐位不变,回归测试钉死)。

## 风险

- **最大风险:低估锚影响** → Step 0 已实测(10 只清单在上);重签 diff 表逐折
  核对方向合理性是 PR-2 的验收核心。
- qlib session 共存(canonical runtime 单例、adjust_mode 一致性)→ 沿用
  backtest_runner `_validate_pit_provider_alignment` 先例,不新造。
- fixture registry 与生产 registry 漂移 → fixture 是快照,PR 里记录来源
  (生产 registry 的 git-provenance/日期);回放语义只依赖 10 只命中股的
  delist_date,稳定。

# Tasks: PIT 穿透 SignalAnalyzer + PerformanceAttribution(审计 P2)

## OpenSpec(propose 阶段)

- [x] Step 0 前置核查(2026-07-03,全程只读):两处 TODO 仍在;白名单各 1 计数;
      **爆炸半径实测 = 回放宇宙 481 只中 10 只在窗口内退市 → 锚的 ic_1d/ic_5d 必动
      → 走重签**;engine/pipeline 无既有 PIT 接线(需新配置面);mini-bundle 无
      registry(fixture 需补,reference-data 签字流程)。
- [x] proposal.md / tasks.md / spec delta 起草
- [x] `openspec validate add-pit-analyzer-routing --strict` 绿
- [x] 操作员审提案(2026-07-03)——**定性:该动锚、重签是修正不是回归**(与
      fold-0 numpy-pin 重签同类)。四要点全认可 + 三点补充(已折入下方任务):
      (1) PR-1 接线要有"配非空 registry → provider 传到位"的激活测试,不留死代码;
      (2) 重签新锚必须在 CI/canonical(numpy<2 pinned)环境重跑绿,证明 pinned 栈
      确定性复现(与原锚同等 CI 守卫);
      (3) registry fixture 签字前出**三方核对表**(mini registry vs 生产 registry
      vs 真实退市事实,逐只对日期)——签的是"忠实子集、日期都对"。
      并行性:本 change 不抢 GPU,可与阶段6 并行;顺带提升阶段6 IC 诊断的 PIT 洁净度。

## PR-1 — 归因穿透 + 配置面 + 引擎接线(锚中性,先行)

- [ ] `PerformanceAttribution.analyze(..., pit_provider=None)`:
      `_get_instrument_returns` 走 provider;缺席 WARN 路径逐位不变(回归钉死)。
- [ ] `WalkForwardConfig`/`PipelineConfig` + `delisted_registry_path: str = ""`
      (默认空 = 恒等;非空缺失 → 构造期 fail-loud);引擎 run 起点构造一次
      provider,alignment 校验沿用 backtest_runner 先例。
- [ ] 引擎把 provider 传给 PerformanceAttribution(SignalAnalyzer 留给 PR-2)。
- [ ] mock-PITDataProvider 单测:走 provider 不走 D.features;WARN 回归;
      坏路径 fail-loud;白名单注释/计数逐条更新。
- [ ] **接线激活测试(操作员补充 1)**:配非空 registry 时,PR-1 管道把 provider
      一路传到 PerformanceAttribution 的调用点(mock 断言收到同一 provider 实例)
      ——接线在 PR-1 就被执行过,不是等 PR-2 激活的死代码。
- [ ] 验收:CI 6 legs 绿(REGEN-2 leg 不动 = 归因确不在锚内的证明)。

## PR-2 — SignalAnalyzer 穿透 + replay 接线 + 基线重签(锚敏感,单独)

- [ ] `SignalAnalyzer.analyze(..., pit_provider=None)`:`_fetch_returns` 走
      provider;WARN 回归钉死;引擎接线补上 SignalAnalyzer 一侧。
- [ ] mini delisted-registry fixture(生产 registry 快照,326 行/14KB,记录来源
      与日期)——签字前出**三方核对表(操作员补充 3)**:每只窗口内退市股的
      delist_date,mini vs 生产 registry vs 真实退市事实逐只对照;操作员签
      "忠实子集、日期都对"后入库(reference-data 红线)。
- [ ] replay 脚本构造 provider(指向 fixture registry),锚语义与生产对齐。
- [ ] **基线重签(必须 CI/canonical 环境;本机 off-pin 禁止)**:重生成 → 旧新
      逐折 diff 表 → 每折 IC 变化归因到 10 只退市股之一 → 回测三指标逐位不动
      (动 = 停下调查)→ PR 描述明确 intentional semantic change 引用本 proposal。
- [ ] **重签复现守卫(操作员补充 2)**:新锚在 CI/canonical(numpy<2 pinned)
      环境重跑绿——与原 REGEN-2 锚同等的确定性复现证明,不是"生成一次就信"。
- [ ] 验收:CI 6 legs 绿(REGEN-2 leg 对新基线绿)。

## Must-not-touch

- `pit_provider is None` 时两个分析器行为逐位不变。
- 回测三指标(return/dd/IR)在重签前后逐位不动。
- backtest_runner / microstructure mask 的 canonical PIT 接线(独立未来决策)。

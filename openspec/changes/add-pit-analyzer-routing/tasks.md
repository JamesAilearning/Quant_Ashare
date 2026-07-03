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

## PR-2 — SignalAnalyzer 穿透 + replay 接线（锚敏感通道，已建）

- [x] `SignalAnalyzer.analyze(..., pit_provider=None)`：`_fetch_returns` 走
      provider；WARN 回归钉死；两引擎接线（engine 折内 + pipeline step 4）；
      第四份 intentional duplication 的 alignment 守卫；白名单注释更新。
- [x] registry fixture：**三方核对表闭合**（mini=生产 sha256 逐字节相等；
      10/10 全部日级确证——操作员对 4 只 2025 年退市做了独立网络核验）。
      **操作员签字 ✓（2026-07-03）**，三条件全落：
      (1-1) recall cross-check：源=tushare 官方全量 list_status='D' + 构建期
      reference-cases 双向校验 + 分年计数与公开退市数逐年吻合
      (2018:5/2020:16/2024:52 精确命中，窗口内全市场 223 只，B 股 0)；
      (1-2) 物理锁死：改名 `delisted_registry_frozen_20260618.parquet`
      （frozen full snapshot 语义），回归测试 sha256 pin（ba24d66c…），
      更新只准走重签通道；
      (1-3) 过期护栏：REGISTRY_SNAPSHOT_DATE >= REPLAY_WINDOW_END 断言。
- [x] replay 脚本构造 provider（--delisted-registry-path CLI + 回归测试接
      fixture），锚语义与生产对齐；config_walk.yaml 填生产 registry
      （复用既有 QUANT_DELISTED_REGISTRY env var），env 文档更新。
- [x] **重签通道基础设施（操作员决定 2，四点收紧全落）**：
      `.github/workflows/regen-baseline.yml`（workflow_dispatch，runner 钉死
      ubuntu-22.04，canonical pin 安装，gen-env 断言在脚本内）+
      `scripts/regen/diff_baselines.py`（R1/R2/R3 验收规则**先行承诺**、
      job 内强制，5 个合成场景自测）+ evidence sidecar
      （run URL/baseline sha/registry sha/pip-freeze hash/runner 镜像；
      回归测试断言"存在时必匹配"，首次重签起 presence 强制）+
      runbook 落库（docs/baseline_regen2.md 重签通道一节；diff 表+证据
      随重签 PR 落库，merge 即签名）。
- [x] **确认项（停牌→摘牌间隙语义）**：bins 层实证抽查 3 只不同退市类型
      （601989 吸收合并、000961/000671 面值）——停牌首日起 bins 即 NaN，
      间隙由 bundle 层天然覆盖，IC 逐日 dropna 兼容 ✓。
- [ ] CI 判定锚影响（见下方判定修正）；若动 → 走重签通道（diff 表 R1-R3 +
      新锚 CI 重跑绿 = 确定性复现证明）；若不动 → 通道备而未用。

## 判定修正（bins 层实证，2026-07-03）

Step 0 判"锚必动"基于"bundle 前向填充退市股 close"的假设。实证到 bins 层
（上述 3 只抽查）发现 Phase B.2 重建的 PIT bundle 在**裸 $close 上本就
NaN-正确**——§4.3.2 文档本来就写明 mask 补的是**窗口算子**泄漏,而
`_fetch_returns` 取裸 $close 不经窗口算子 → mask 在此 bundle 上对 IC 输入
是 no-op → **REGEN-2 锚预期逐位不动,重签预期不触发**。PR-2 的价值不变:
治理缺口关闭 + 纵深防御（未来窗口算子表达式/旧 bundle 消费方兜底）。
CI 的 REGEN-2 leg 是最终裁判——绿 = 判定修正成立;红 = 走重签通道。

## Must-not-touch

- `pit_provider is None` 时两个分析器行为逐位不变。
- 回测三指标(return/dd/IR)在重签前后逐位不动。
- backtest_runner / microstructure mask 的 canonical PIT 接线(独立未来决策)。

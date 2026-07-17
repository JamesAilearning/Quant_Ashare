# Tasks: 2026-07-17-csi800-cadence-campaign

## 0. 提案签署（本 PR）
- [ ] 操作人签 DP-1..DP-6（proposal.md 决策账），签字后数字冻结

## 1. PR-A — 生产者 attestation（runtime 唯一触点）
- [ ] engine 写盘每折后对已持久化 positions 字节计算 sha256，写入
      fold report 顶层 `positions_sha256` 与 manifest
- [ ] fold report schema 版本升级（`4-positions-attestation`），
      两引擎 schema parity 测试相应更新（pipeline 侧口径按 parity
      测试现行约定处理并注明理由）
- [ ] 测试：摘要与盘面字节一致；失败折不携带；篡改 positions 后
      重验失配
- [ ] 顺带归档两个已 ship change：`2026-07-16-csi800-antiinflation-
      guards`、`2026-07-16-per-universe-canonical-benchmark` →
      `openspec/changes/archive/`
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. PR-B — pair v3 三方认证 + attach 摘要链 + N1 基线抬进认证工件
- [ ] pair 工具：`--reference-run` 三方入证（四件套 + 配置绑定钉死
      差集校验），schema 升 `csi800_pair_report_v3`；v3 侧条目新增
      逐折毛超额（`excess_return_without_cost`，取自哈希钉住的
      fold report）
- [ ] attach：改从 v3 工件读参照认证条目（pre-v3 拒绝）；全链摘要
      验证（pair→fold report 哈希→positions_sha256→盘面字节）；
      缺摘要维持 unauthenticated + block；失配拒绝
- [ ] **不可变锚 + verdict 侧车（强制两件套）**（codex #374 r3+r4）：
      attach 内嵌资格恒 false（非权威）；certify 独立步骤——验证
      pair v3 字节==HEAD 已提交、全摘要链、五 veto+主判据，全过则
      产出 verdict 侧车（被锚 pair digest+commit id+判定），certify
      不改写任何已锚工件；晋升仅以"已提交侧车+digest 与已提交 pair
      一致"形态成立；测试含 certify 不改写/侧车断链拒/工作树拒/
      顺序不可倒置
- [ ] **N1 工件 v3 重生成**（codex #374 r1）：从完好 run 目录重生成
      `docs/research/csi800_campaign_pair_report.json` 至 v3（逐折
      毛值入档），治理断言 v2 已钉哈希（双侧 run_id/config_sha256/
      report_sha256/fold_report_sha256）逐字段不变；PR diff 供人工
      复核。N1 run 目录保持完好直至战役收束（丢失=fail-closed）
- [ ] 既有防线（窗口绑定/内嵌换手交叉验证/去重/非有限值/质量闭合）
      全保留，测试断言不回退
- [ ] 测试：全链达标晋升门开；positions 换后拒；参照 fold report
      配对后改拒；pre-v3 工件拒；v3 毛值与 hash 验证后 fold report
      不符拒
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 3. PR-C — N5 战役 preset + 治理 pin
- [ ] 三 preset：`csi300_cadence5_reference.yaml`、
      `csi800_cadence5_base.yaml`、`csi800_cadence5_conservative.yaml`
      （cadence 三字段显式写死 5/0/fold_phase；余沿 campaign 三件）
- [ ] 治理 pin：N5 双档 diff 恰 {slippage_bps, output_dir}；N5 参照
      vs N5 base diff 恰 {instruments, benchmark_code,
      attribution_sleeve_grouping, output_dir}；N5 vs N1 同角色
      preset diff 恰 {rebalance_cadence_days, rebalance_phase,
      rebalance_anchor, output_dir}（N1 preset 不动，diff 由显式
      写死值产生）；resolved 级 cadence pin（三发同值 5/0/fold_phase）
- [ ] 主判据双条件数字 pin（>0 与 50%）入治理测试
- [ ] 主判据比较工装：仅消费已提交 v3 工件（N1 与 N5 双侧），毛
      塌缩比较 + 净判据 + 覆盖全折校验，缺失/失配拒绝
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 4. 点火（单独授权，PR-A/B/C 全并后）
- [ ] 三发串行（参照 → base → conservative），日志入 scratchpad
- [ ] pair v3 生成 + attach 全链勾验（veto 五项 + 主判据双条件）
- [ ] 战役简报 + 证据工件入库 PR → codex/CI → 数字 STOP 签字
- [ ] 判定入档：WIN → 晋升流程（仍须五 veto + attestation 门）；
      LOSE → 方向 A 收束闭环

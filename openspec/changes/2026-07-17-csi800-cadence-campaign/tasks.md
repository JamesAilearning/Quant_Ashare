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

## 2. PR-B — pair v3 三方认证 + attach 摘要链
- [ ] pair 工具：`--reference-run` 三方入证（四件套 + 配置绑定钉死
      差集校验），schema 升 `csi800_pair_report_v3`
- [ ] attach：改从 v3 工件读参照认证条目（pre-v3 拒绝）；全链摘要
      验证（pair→fold report 哈希→positions_sha256→盘面字节）；
      达标置 `producer_digest_certified`，晋升门可开；缺摘要维持
      unauthenticated + block；失配拒绝
- [ ] 既有防线（窗口绑定/内嵌换手交叉验证/去重/非有限值/质量闭合）
      全保留，测试断言不回退
- [ ] 测试：全链达标晋升门开；positions 换后拒；参照 fold report
      配对后改拒；pre-v3 工件拒
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
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 4. 点火（单独授权，PR-A/B/C 全并后）
- [ ] 三发串行（参照 → base → conservative），日志入 scratchpad
- [ ] pair v3 生成 + attach 全链勾验（veto 五项 + 主判据双条件）
- [ ] 战役简报 + 证据工件入库 PR → codex/CI → 数字 STOP 签字
- [ ] 判定入档：WIN → 晋升流程（仍须五 veto + attestation 门）；
      LOSE → 方向 A 收束闭环

# Tasks: 2026-07-17-csi800-cadence-campaign

## 0. 提案签署（本 PR）
- [ ] 操作人签 DP-1..DP-6（proposal.md 决策账），签字后数字冻结

## 1. PR-A — 生产者 attestation（runtime 唯一触点）
- [ ] **前置归档（本 change 的依赖，codex #374 r17）**：归档两个已
      ship change `2026-07-16-csi800-antiinflation-guards`、
      `2026-07-16-per-universe-canonical-benchmark` →
      `openspec/changes/archive/`，使 `v2-csi800-expansion-guards`
      物化进 `openspec/specs/`——本 change 的 MODIFIED delta 依赖该
      canonical spec 存在，SHALL 在归档之后才应用/校验
- [ ] wf engine 写盘每折后对已持久化 positions 字节计算 sha256，
      写入 fold report 顶层 `positions_sha256` 与 manifest
- [ ] **pipeline 引擎对称**（AGENTS.md 双引擎同名字段义务）：
      pipeline_report 以同名 `positions_sha256` 盖其持久化
      positions.json；两引擎 schema parity 测试断言双侧在场
- [ ] fold report schema 版本升级（`4-positions-attestation`）
- [ ] 测试：摘要与盘面字节一致（两引擎）；失败折不携带；篡改
      positions 后重验失配
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. PR-B — pair v3 三方认证 + attach 摘要链 + N1 基线抬进认证工件
- [ ] pair 工具：`--reference-run` 三方入证（四件套 + 配置绑定钉死
      差集校验），schema 升 `csi800_pair_report_v3`；v3 侧条目新增
      逐折毛超额（`excess_return_without_cost`，取自哈希钉住的
      fold report）
- [ ] attach：改从 v3 工件读参照认证条目（pre-v3 拒绝）；全链摘要
      验证（pair→fold report 哈希→positions_sha256→盘面字节）；
      缺摘要维持 unauthenticated + block；失配拒绝
- [ ] **不可变锚 + verdict 侧车（强制两件套）**（codex #374
      r3+r4+r10+r12）：attach 内嵌资格恒 false（非权威）；certify
      独立步骤——验证 pair v3 字节==主线锚（origin/main 可达
      commit）+ **经证据锚（origin/main 可达 commit）读取全部 N5
      源证据字节**（`git show <evidence-anchor>:<path>` 口径；证据
      未主线锚定即拒，绝不消费本地/untracked 工件）+ 全摘要链 +
      五 veto/主判据（N1 侧经 N1 主线锚读取,codex r14），全过则产出 verdict 侧车（pair 锚 + 证据锚 + N1 锚
      commit id + 判定），certify 不改写任何已锚工件；晋升仅以
      "已提交侧车 + digest 与已提交 pair 一致"形态成立；测试含
      certify 不改写/侧车断链拒/工作树拒/**证据未锚定拒**/顺序不可
      倒置
- [ ] **N1 源 fold reports 本体入库**（codex #374 r1+r7+r8）：双侧
      46 个 fold report（~1.1 MB）提交至钉死证据目录，目录
      `.gitattributes -text` 保证字节保真；治理测试逐折断言已提交
      源文件 sha256 == 已提交 v2 工件所钉 fold_report_sha256（CI
      端到端可验，毛值直接读自哈希验证过的源）；证据工件/v3 重生成
      两方案均废止（前者值不可复验来源、后者依赖单机目录）
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
- [ ] 主判据双条件数字 pin（>0 与 50%）+ 比较臂 pin
      （conservative-to-conservative + 双臂毛发散 ≤5% fail-closed）
      入治理测试
- [ ] 主判据比较工装：N1 侧仅从**已提交的钉死 N1 源 fold report
      目录**读毛值（读取前逐折哈希验证 == v2 已钉值，codex r9），
      N5 侧消费 pair v3；毛塌缩比较（钉臂）+ 净判据 + 覆盖全折校验
      + 双臂发散校验，缺失/断链/失配拒绝
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 4. 点火（单独授权，PR-A/B/C 全并后）
- [ ] 三发串行（参照 → base → conservative），日志入 scratchpad
- [ ] pair v3 生成 + attach 全链勾验（veto 五项 + 主判据双条件；
      attach 内嵌资格恒 false，非权威）
- [ ] 战役简报 + 证据工件（pair v3）入库 PR → codex/CI → 数字 STOP
      签字 → **用户 merge（= pair v3 提交评审，锚成立）**
- [ ] 判定 LOSE（任一主判据不过或 veto 触发）→ 如实入档，方向 A
      收束闭环，certify 不执行
- [ ] 判定 WIN → **N5 三 run 源证据入库并先并主线**（codex r9+r10）：
      三份聚合 walk_forward_report.json + 全部 fold reports +
      positions 本体（~10 MB，字节保真 `-text`，codex r13：聚合是
      report_sha256/内嵌 config/fold 声明/veto① 净值的复验源）
      提交钉死证据目录 → PR → **用户 merge（证据锚成立）**；可与
      pair v3 同 PR
- [ ] 判定 WIN → **certify 步骤**（证据锚成立后）：验证 pair v3
      字节==主线锚 + 经证据锚（origin/main 可达 commit，
      `git show <evidence-anchor>:<path>` 口径）读取全部证据字节
      端到端重算全链 + N1 侧经 N1 主线锚读取 + 五 veto/主判据 →
      产出 verdict 侧车（**三锚 commit id：pair + 证据 + N1** +
      判定）→ 侧车入库 PR → codex/CI → STOP → **用户 merge（侧车
      提交评审）** → 晋升成立；证据或 N1 未主线锚定时 certify 拒绝
- [ ] 顺序不可倒置：run → attach → pair 提交 → 源证据提交 →
      certify → 侧车提交 → 晋升；跳过任一环 = 晋升无效；下游按侧车
      记录的三锚(pair/证据/N1)重算复验

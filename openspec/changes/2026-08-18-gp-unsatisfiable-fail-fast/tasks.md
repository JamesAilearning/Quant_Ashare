# Tasks: 2026-08-18-gp-unsatisfiable-fail-fast

## 实现

- [x] `grammar.py` 加静态可满足性预检,`random_expression` 入口调用
- [x] 预检**不碰 rng**;可满足时一步都不改原路径
- [x] 无解时 `GrammarError`,消息说清白名单容不下什么

## 验证(每条都要实测数字,不接受"应该没问题")

- [x] **安全性**:穷举白名单子集,逐个比对「预检判定」与「实际能否生成」——
      不得存在「预检拒绝但实际可生成」的情形(假阴性是治理级错误)
- [x] **确定性**:同种子下生成的表达式与改前**逐字节相同**
- [x] **提速**:那条 38.8 分钟的用例改后耗时
- [x] `tests/logic/factor_mining/` 全量 + governance + mypy/ruff
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 实测数字（原样）

```
那条用例          2327.77s (38.8 分)  →  0.54s
factor_mining 全量  41:41            →  2:39
logic+governance   25:28            →  6:50   (4379 passed / 29 skipped)
```

安全性穷举：361 个白名单子集，**假拒绝 0**（预检拒绝但实际可生成 = 0），
保守漏判 0。
确定性：None 白名单 40 种子 + 七字段白名单 40 种子，改前改后**逐字节相同**。

## codex #452 r1（一条 P2，但按我自己的标准是红线）

- [x] **假拒绝坐实**:`max_depth=0` 时无叶类型(CSF)仍走 `_random_operator`,
  子节点拿 `max_depth-1` 照样取叶子——`cs_winsorize($circ_mv)` 是 depth 0 下
  **真实生成得出来**的,而按 `max_depth` 封顶的可达性把它判成无解。我上一版
  361 个子集全用 `max_depth=6`,**整条深度维度是盲区**
- [x] 修:不动点**跑到收敛**,不按 `max_depth` 封顶。只在「任何深度都无解」时
  才拒,严格更保守——只会少拒不会多拒
- [x] `max_depth` 参数一并从判据里删掉:留着会让读者以为深度参与判断
- [x] 穷举补上深度维度:**424 个(白名单 × 深度)组合,假拒绝 0**,两侧都有样本
- [x] 确定性含深度复验:2 白名单 × 3 深度 × 20 种子 = 120 样本,改前改后逐字节相同
- [x] 变异验证:把 `max_depth` 封顶加回去 → 三条用例红(含新加的 depth-0 那条)
- [x] 全量:logic + governance `4380 passed / 29 skipped`(5:20)

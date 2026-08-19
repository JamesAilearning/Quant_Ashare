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

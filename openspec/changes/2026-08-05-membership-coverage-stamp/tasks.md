# Tasks: 2026-08-05-membership-coverage-stamp

## 1. 实现（单 PR）
- [x] resolver：`resolve()` 原子写/合并 `membership_coverage.json`
      （子集合并、腐坏 WARN+重建）
- [x] loader：stamp 消费（per-sleeve stamp 优先/legacy 回退/矛盾拒/
      畸形拒/min 语义不变）+ 拒绝消息更新
- [x] 测试：resolver 三态 + loader 五态
- [ ] openspec validate --strict + ruff/mypy/目标测试 + 全量快套
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. 并后（不在本 change 内执行，录以为序）
- [ ] 重跑 03（仅加 stamp）→ 重点火 m3' → 门阶段 → 数字 STOP

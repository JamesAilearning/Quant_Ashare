# Tasks: 2026-08-04-csi800-n5-bootstrap-reanchor

## 0. 提案签署
- [x] 操作人裁决"另行提案"路线（A 选项）并签 RA-DP-1..4
      （会话内计划签署；本 PR merge = 冻结）

## 1. 重注册（单 PR，merge = 新窗冻结生效）
- [x] 三 preset 窗口键按 RA-DP-1 更新（头注释同步）
- [x] `BOOTSTRAP_DRYRUN_WINDOW` → RA-DP-2
- [x] 治理/logic/executor 钉守随动（窗口 pin 表、lib 夹具、
      executor `_WINDOWS`）
- [x] runbook 操作卡窗口表 + 干跑窗引用更新
- [x] RA-DP-3 证据入档：三门工件 JSON + v1 拒绝简报
- [x] spec delta：自举中止后重注册路径 ADDED requirement
- [ ] openspec validate --strict + ruff/mypy/目标测试 + 全量快套
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. 并后（不在本 change 内执行，录以为序）
- [ ] 三发 GPU 串行点火（m1'/m2'/m3'）
- [ ] 成员门×3 → 候选 manifest → ensemble 门 → 数字 STOP
- [ ] 门全过 → 切换执行器 --dry-run 先行 → 实跑 → 证据入库

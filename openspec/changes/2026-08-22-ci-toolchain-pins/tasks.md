# Tasks: 2026-08-22-ci-toolchain-pins

## 实现

- [x] `dev` 四条加小版本上界，下界抬到 CI 实际使用的小版本
- [x] `ui` 三条加大版本上界
- [x] 守卫：CI 装的每条依赖都有上界（覆盖面从 workflow 安装行推导）
- [x] 守卫：判代码的工具必须是小版本粒度
- [x] 守卫：workflow 里内联的 numpy / scipy 窗口与 pyproject 逐字一致

## 验证（每条要实测数字）

- [x] `tests/governance/` 全绿
- [ ] 变异全部被咬
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 留档：本次的下界会把本机 venv 排除在外

本机共享 venv（`D:\爱马仕\hermes-agent\venv`）是 pytest 9.0.3，而本次把下界抬到
9.1。这不是副作用，是**把已经存在的偏差变得可见**——本机与 CI 装的本来就不是
同一个版本，只是此前没有任何东西说出来。本机验证仍属参考，裁判是 CI。

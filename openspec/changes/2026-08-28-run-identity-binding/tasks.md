# Tasks

## 0. 勘察（已完成，本提案的依据）

- [x] 0.1 多智能体跨面勘察（四个面并行枚举 + 逐条对抗式反驳，20 agent）:
      **3 条确认 / 13 条被驳回**。驳回率 81% —— 被驳回的多数是「以为不存在、
      其实已经做对了」，例如 pipeline 的 `_make_run_dir`、`_resume.py` 那个
      刻意排除 `output_dir` 的指纹
- [x] 0.2 三条确认项在**真实数据**上复核，数字逐个对上:
      - `walk_forward_report.json` 顶层键无 `run_id` / `config_fingerprint` /
        `git_commit`（实测打印）
      - catalog **105 行 / 33 目录 / 72 行被覆盖**
      - `output/walk_forward` 单目录 **59 行、3 个不同配置指纹**
- [x] 0.3 关键约束确认:`engine.py:186` 的 `FoldManifest.discover(output_dir)`
      让「同目录」成为 resume 的**前提** —— 所以「每次运行一个新目录」这条
      看似显然的修法**行不通**，必须把身份绑到目录的**内容**上

## 1. 产物带身份

- [ ] 1.1 `run_id = uuid4().hex` 在非 dry-run 起跑时铸一次，贯穿本次运行
- [ ] 1.2 `walk_forward_report.json` 顶层写 `run_id` / `config_fingerprint` /
      `git_commit`
- [ ] 1.3 指纹用**既有**的 `compute_config_fingerprint`（`_resume.py:116`），
      不新造哈希。它已经排除 `output_dir`——改目录名不该换指纹
- [ ] 1.4 目录里落 `run_owner.json`（`run_id` / `config_fingerprint` /
      `started_at`）:目录自己要回答得了「这堆字节属于哪一次运行」

## 2. 指纹冲突拒绝

- [ ] 2.1 起跑时读 owner 标记;指纹不同 ⇒ **拒绝**并同时点名两个指纹与目录
- [ ] 2.2 `--allow-overwrite` 作为显式逃生门;用了就在报告里记下来（否则
      「这次是被允许覆盖的」这件事同样查不到）
- [ ] 2.3 指纹**相同**时行为不变——resume 照常。这条要有用例正面钉住，
      否则很容易在实现里把 resume 一起拦掉
- [ ] 2.4 owner 标记读不出来/损坏 ⇒ 当作**没有 owner**（legacy 目录），
      不拒绝。老目录必须还能跑

## 3. 目录级互斥

- [ ] 3.1 运行期间持有以 `output_dir` 为名的锁，复用
      `single_flight.lock_path_for` 的机制（不新造第二套锁语义）
- [ ] 3.2 第二个进程**拒绝启动**并说清是谁持有;不许静默排队、更不许并行写
- [ ] 3.3 崩溃残留的锁要能被识别/接管——否则一次 kill 之后目录永久锁死。
      按 `single_flight` 既有的处置来，不自造

## 4. 向后兼容

- [ ] 4.1 老工件缺三个字段 ⇒ 读侧当 **legacy**，不是损坏。本 change **不改**
      任何读侧行为
- [ ] 4.2 **不回填**历史工件:那 72 行指向的产物已经没了，凭空补一个 id 等于
      替一次无从复原的运行编造证据
- [ ] 4.3 pipeline 那一侧一行不动（它已有 `_make_run_dir`）

## 5. 验证

- [ ] 5.1 用例:同指纹复用 → resume 正常;异指纹 → 拒绝且消息里有两个指纹;
      带 `--allow-overwrite` → 放行且记录;无 owner 的老目录 → 放行
- [ ] 5.2 并发:第二个进程拒绝启动（真起两个进程，不是 mock 锁）
- [ ] 5.3 产物字段:跑一次真的滚动验证（**小规模**、`RUN_E2E=1` 门内），
      核对报告顶层三个字段与 owner 标记一致
- [ ] 5.4 变异复验:每一道新闸逐条熄火（钉**条件整行**）
- [ ] 5.5 `ruff` / `mypy --strict` / `openspec validate --strict`

## 6. Runbook

- [ ] 6.1 `docs/csi800-n5-production-runbook.md` 那句「严格串行，绝不并发」
      改成指向机器守卫，并写明逃生门与拒绝时该怎么办

## 7. 划界（本 change 不做）

- 不做 UI 的四值证据状态（A2 —— 它消费本 change 落下的字段）
- 不给每次运行铸独立目录（resume 依赖同目录）
- 不做产物完整性清单（每个文件的大小/摘要）
- 不改任何官方指标、不改目录布局、不动 pipeline

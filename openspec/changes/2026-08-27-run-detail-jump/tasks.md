# Tasks: 2026-08-27-run-detail-jump

## 实现

- [x] `web/operator_ui/jobs_jump.py`：判定 + 链接形状的纯函数（零 streamlit
      导入）。运行中→给，终态/未起/拿不到 id→不给；每个参数在出发侧真跑
      `_param_guard.sanitize`
- [x] 「运行中」判据逐字照搬结果页既有那句（`str(status).lower() == "running"`，
      不 strip、不扩同义词）——两处口径不同，同一个运行会一处给入口一处不给
- [x] 链接带一次性 handoff 令牌；令牌形状过不了作业页校验时**抛**，不静默少带
      （空串等于 default，所以判据里 `not handoff_token` 那一半不能省）。校验排在
      判定**之前**：否则铸坏令牌的调用方在终态运行上一路安静，直到「恰好有个
      运行在跑」那天才炸——而那正是本入口唯一有用的时刻
- [x] 结果页接线：`results.py` 在选中运行的状态判定处渲染，自动刷新开关原样不动
- [~] ~~滚动验证页接线：`walk_forward.py` 建 `_ui_run_by_dir`~~ —— **第二轮撤回**，
      见下文 codex #473 第二轮 P1：那一页在结构上看不到运行中的作业，入口
      永远不触发。该文件最终**未改动**
- [x] 接线的那一页（结果页）不自己拼 query params、不自己判「运行中」

## 验证（每条要实测数字）

- [x] 新守卫 `tests/logic/test_run_detail_jobs_jump.py`：**24 passed / 21
      subtests**（第二、三轮增删后的实测值）
- [x] 同一性钉：三个参数**真跑** `sanitize`；status 值域从 `jobs.py` 源码 AST
      解析（读不出来就抛，不静默返回空集）；`status != _DEFAULTS["status"]`；
      作业页 `handoff_keys` 的耦合也钉住（第二轮后是 `{"status", "search"}`）
- [x] 落地侧真跑：`job_io._apply_filters` 用链接给的两个值筛三行夹具，只剩那一行
- [x] 结果页一条接线钉，钉的是**条件整行**（`    if _jobs_jump is not None:\n`）。
      滚动验证页那一条随入口撤回一并删除，换成钉住「那一页看不到运行中作业」
      的三条前提（见第二轮）
- [x] 变异复验 **15 条全咬、零逃逸**（第三轮的实测值；首版 8 条里滚动验证页
      的 M7/M8 已随入口撤回删除，第二、三轮新增交接语义相关的若干条）
- [x] `ruff check src/ tests/ scripts/ web/` clean
- [x] `mypy --strict` 改动的三个源文件（`jobs_jump.py` / `pages/jobs.py` /
      `pages/results.py`）0 error（本机 mypy 1.20.2；CI 钉 >=2.3,<2.4）
- [x] `pytest tests/logic tests/governance`：**5069 passed / 34 skipped /
      1810 subtests**（244.34s，第三轮后复跑）
- [x] `openspec validate 2026-08-27-run-detail-jump --strict` valid

## 已知残留（本 change 有意不做）

- ~~作业页的一次性交接令牌只覆盖 `handoff_keys={"status"}`…需单独裁决。~~
  **第二轮已取消这条划界**：评审把这个窄口升到 P1，是对的——它会让「针对选中行
  的动作（含停止）指向错的运行」。`handoff_keys` 已扩到 `{"status", "search"}`。
  当时担心的「队列链接反过来清掉手打搜索」在第三轮做了三变体 × 四场景的实测，
  结论是**清掉才是对的**（队列链接说的是「给我看这个状态的作业」）。详见第三轮。
- 本段留着划掉的原文而不是删掉：这份 change 归档后会进 OpenSpec baseline，
  一条被推翻的划界读起来必须能看出它**被推翻了**，而不是像从没写过。

## codex #473 第二轮

- [x] **P1 滚动验证页的入口在结构上永远不触发，已移除。**
      `JobManager.start()` 把 `run_dir` 初始化为 `None`；`job_runner.main()`
      **只在子进程成功之后**才写它；而该页的 `wf_jobs` 过滤掉没有 `run_dir`
      的记录。三条合起来：一个正在跑的作业不在那一页的任何一张表里。首版
      对着它写的测试只能捏造 `status="running"` + 有 `run_dir` 的组合——那个
      组合在生产里不存在。
- [x] 换成一条钉住**那三条前提**的测试：任何一条变了都该重新评估这个划界。
      入口只留在结果页（`viewable_jobs` 不过滤 `run_dir`，运行中的
      walk_forward 作业在那里可见）。
- [x] 评估过 codex 建议的「不依赖完成后字段推导活体身份」：走不通。UI 每次
      强制唯一 `output_dir`，运行中的作业还没有产物目录，所以它与本页任何一个
      可选目录都不对应——不是取值方式的问题，是那一页按设计只展示已完成的运行。
- [x] **P1 交接令牌只覆盖 `status`，`search` 有窄口。** 操作人跟着链接来过
      一次、改过搜索框、再跟同一个运行的链接来 ⇒ URL 的 search 没变 ⇒ 走普通
      路径保留他手打的词 ⇒ 链接显示不出那一行、或显示另一个运行的行，而针对
      「选中行」的动作（含停止）指向了错的运行。
- [x] 修法：`handoff_keys` 扩到 `{"status", "search"}`。**不**加「该键在 URL
      里才覆盖」的前提——见下一条的实测。
      （首版报告把这条列为「已知残留、建议单独裁决」，评审把它升到 P1，是对的。）

## codex #473 第三轮

- [x] **P1 proposal / tasks 与实现不一致。** 首版 proposal 承诺「两个详情页都给
      入口」并把「改 `handoff_keys`」明列为 non-goal，而第二轮的实现恰恰撤回了
      前者、做了后者；旧的 `[x]` 任务项与「已知残留」段也留着自相矛盾的陈述。
      本仓以 OpenSpec baseline 为准，这份 change 归档后会留下一份描述「实现故意
      拒绝的行为」的契约。proposal 已重写，过时的任务项已就地更正而非追加。
- [x] **P2 那条交接用例喂的是非典型残值态，掩盖了真实行为。** 评审说得对：真实
      的 settled 态是 `jobs_search` 与 `jobs_last_url_search` 都等于操作人输入的
      词（页面每帧把 session 回镜进 URL）。
- [x] **但评审建议的修法实测更差。** 用页面自己的 AST 跑三个变体 × 四个真实场景：

      | 场景 | 去掉 URL 在场前提 | 首版（带前提） | 评审建议（跳过重播种） |
      | --- | --- | --- | --- |
      | 详情页链接（status+search），此前改过搜索且已回镜 | ✅ 落到那一行 | ✅ | ✅ |
      | 队列链接（只带 status），settled 态 | ✅ 重置搜索 | ✅ | ❌ 保留 `manual` |
      | 队列链接（只带 status），非典型残值态 | ✅ 重置搜索 | ❌ 保留 `manual` | ❌ 保留 `manual` |
      | 重复跟随同一运行（URL 值没变，令牌是新的） | ✅ 一次性压过 | ✅ | ✅ |
      | **通过** | **4/4** | 3/4 | 2/4 |

      队列链接（`_today_decision_queue_helpers.queue_page_link` 实测只给
      `{"status": ...}`，且 `today_workbench.py:244` 会为它铸令牌）说的是「给我看
      这个状态的作业」；保留一个无关的搜索词会让操作人看到空列表、以为那条队列项
      消失了。所以采用 4/4 的那个：**去掉**前提，`handoff_keys` 保持扩大。
- [x] 用页面自己的 AST **真跑**播种，五条用例覆盖：详情页精确落行 / 重复跟随一次性
      压过 / 队列链接重置搜索 / **同一条链接不因内部残值而行为不同** / 覆盖只生效
      一次。
- [x] 变异复验 **15 条全咬住**（新增：重新加回「URL 在场」前提 / 交接不标记已消费）。

## codex #473 第五轮：一条 P2——交接只覆盖两个键，其余陈旧筛选照样吞掉那一行

- [x] 普通分支的条件是「URL 值与**上次消费的** URL 值不同」。操作人离开前把
      `jobs_type` 改成 `provider`、而 `jobs_last_url_type` 仍是默认 `all`
      ——跟着新链接回来时 URL 里没有 `type`，`_qp_read` 给 `all`，条件为假,
      于是**保留 provider**，被请求的运行当场被筛掉。`page` 同理（停在第 3
      页，单行结果在第 1 页）
- [x] 这与本 change 已经写进 spec 的那句话是同一件事——**到达的 URL 就是这次
      导航的完整筛选状态**。只覆盖其中两个键，等于把那句话只兑现一部分
- [x] `_HANDOFF_KEYS = frozenset(_DEFAULTS) - _HANDOFF_EXEMPT`：**减出来**而
      不是手写清单（#471 学到的同一课）。将来新增的筛选键自动进入交接集合；
      手写清单会让每一个新键成为本缺陷的下一个实例
- [x] 豁免的只有**呈现偏好**（`sort_by` / `sort_dir` / `autorefresh`）:它们
      不改成员、也不改那一行在哪。一起重置等于替操作人做一个链接没请求过的
      决定
- [x] 旧守卫钉的是**字面拼写** `handoff_keys=frozenset({"status", "search"})`
      ——集合改成推导之后当场失配，而它从来没钉住「哪些键真的会被覆盖」。换成
      从 `jobs.py` 源码里**求值**那三个集合（`_DEFAULTS` 是 `AnnAssign` 不是
      `Assign`，只收 `Assign` 会一个也找不到——找不到就响亮地红，不静默跳过）
- [x] 行为级用例**真跑**播种:陈旧 `type=provider` + 陈旧 `page=3` 必须被重置,
      而 `sort_by` / `autorefresh` 必须留着
- [x] 变异复验 5 条全咬（推导窄回两键 / 豁免集合塞进 `type` / 豁免清空 /
      调用点传手写字面量 / 行为级两条各一）
- [x] `tests/logic` + `tests/governance` **5073 passed**

## codex #473 第六轮：一条 P2——「豁免」没有真的保住偏好

- [x] **实测复现**:settled 态（操作人选定 `sort_by=duration`、页面回镜进 URL
      所以 `jobs_last_url_sort_by` 也是 `duration`）下，一条不带 `sort_by` 的
      链接让 `_qp_read` 给出默认 `created_at`，**普通分支**看到「URL 值变了」
      照样重置。实测 `sort_by=created_at` / `autorefresh=0`——我写进 spec 的
      那条「偏好在交接里存活」是假的
- [x] **我的用例把它掩盖了**:喂的是 `jobs_last_url_sort_by="created_at"` 的
      **残值态**——那一格下普通分支看到「URL 值没变」就不动，于是偏好「看
      起来」保住了。这是本 PR 第三次栽在「用例喂了一个非典型状态」上（前两
      次是 search 交接、以及 walk_forward 的 `status=running`+`run_dir`）
- [x] 修法:豁免键在**交接的那一帧整个跳过**（`continue`，连行尾那句「记下
      这次消费的 URL 值」都不执行），并且**只在那一帧**——条件里少了「这次
      交接还没消费过」，偏好就被永久豁免，操作人再也改不动排序
- [x] 顺手删掉一行恒等操作:原本想「把上次消费值对齐成当前值」，但页面每帧
      会把 session 回镜进 URL，下一帧要么看到相等、要么写回同一个值。变异
      实测去掉它行为不变 → 删（留着只会让人以为多了一层保障）
- [x] 用例改成**两帧驱动**:第一帧交接保住偏好，第二帧同一令牌（已由**被测
      代码自己**标记消费，不是用例预塞——预塞会让「标记已消费」那一行的变异
      逃逸）下必须重新跟着 URL 走
- [x] 变异复验 **4 条全咬**（不传 `handoff_preserve` / 跳过条件去掉
      `fresh_handoff` / 不标记已消费 / 豁免集合清空）。首轮 3 条全逃，逐条
      补钉之后才咬住
- [x] `tests/logic` + `tests/governance` **5074 passed**

## codex #473 第七轮：一条 P2——遮挡筛选状态的控件键没被重置

- [x] 大多数筛选控件直接用 `key="jobs_<k>"`，控件与筛选状态是**同一个键**，
      交接写一次就够。但 `st.date_input` 用的是**另一个** key
      （`jobs_date_from_widget`），而紧随其后那行又把控件的值写回
      `jobs_date_from`——只重置筛选状态毫无用处:控件在同一帧把陈旧日期原样
      写回去，说好的「精确落到那一行」照样落到一个空列表上
- [x] 与 #471 那条是**同一类**:带 key 的控件遮住了喂给它的那个值。两个 PR
      在同一天各撞一次
- [x] `_HANDOFF_WIDGET_MIRRORS` 映射「筛选键 → 遮挡它的控件键」，交接时按
      **控件自己的类型**写（`date_input` 的 session 值是 `date | None`，
      不是 ISO 串）
- [x] **构造性守卫**（不是手写清单）:枚举 `_DEFAULTS` 的每个键，只要页面里
      存在 `key="jobs_<k>_widget"`，它就必须**要么进镜像表、要么在豁免集合
      里**；镜像表也不许指向不存在的控件键（抄错名字 = 那一行永远不生效）。
      顺带查出第三个遮挡对 `jobs_autorefresh_widget`（它在豁免集合里，
      合法）——codex 只点了两个
- [x] 两个测试 harness 补上被测函数的模块级依赖（漏掉会 `NameError`，是**响亮**
      的失败，不会静默少测一段）
- [x] 变异复验 3/3（不同步镜像 / 镜像表清空 / 镜像表抄错名字）
- [x] `tests/logic` + `tests/governance` **5076 passed**

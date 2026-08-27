# Tasks

## 1. 纯函数（`_daily_decision_helpers.py`）

- [x] 1.1 `find_nominal_baseline(artifacts, *, read_payload, as_of, limit)`：
      从 `as_of` 起向后找第一份可信的再平衡日工件
- [x] 1.2 六个分因常量（HOLD / 无节奏字段 / 节奏字段形状违约 / schema 不受支持 /
      建仓时点不合法 / 读不出来），逐条记账不静默跳过
- [x] 1.3 **不推断缺失**：没有 `rebalance_day` 键的老工件不当作再平衡日
      （`hold_state` 对它返回 `is_hold=False`，只看 `is_hold` 会误判）
- [x] 1.4 扫描上界 + `limit_reached` / `exhausted` 两态分开（「翻完了都没有」
      与「翻到上限就停了」对操作人的下一步不同）
- [x] 1.5 `baseline_roster`：只给代码集合。工件里没有权重/股数/金额，等权假设
      写进来就是凭空造仓位
- [x] 1.6 读盘由调用方**注入**：保持本模块零 I/O，读边界仍由页面那侧执法

## 2. 页面渲染

- [x] 2.1 位置在候选表**之前**——它框定下面所有内容的语境
- [x] 2.2 找到 → 日期 / 只数 / topk / universe / 代码表
- [x] 2.3 没找到 → 说清已回溯几份 + 明说「不等于没有持仓」
- [x] 2.4 跳过清单折叠展开，逐条给日期与原因
- [x] 2.5 撞上界时单独说明
- [x] 2.6 读盘走本页既有的 `read_json_artifact`（因此仍受 `guard_output_path`
      约束），不另开读路径

## 3. 红线守卫（机器执法）

- [x] 3.1 affordance 禁令：`st.text_area` / `st.number_input` /
      `st.data_editor` / `st.file_uploader` / `st.download_button` /
      `execCommand` / `navigator.clipboard`
- [x] 3.2 执行口径词只在**渲染字符串**里查——用 AST 取真正传给 `st.*` 的字面量
      （含 f-string 的字面段）。**首版写成全文串禁，当场被自己的免责声明judged
      红**（这一页本来就大量否定「买入 / 卖出 / 下单」），说明全文禁词是错的
      工具：它会逼出更弱的免责声明
- [x] 3.3 词表刻意**窄**：只收「除非真把功能做出来、否则没理由出现」的词
      （差分单 / 缓冲带 / 目标仓位 / 股数 / 手数）
- [x] 3.4 接线钉钉**条件整行**（`if _baseline.found:` 等）

## 4. 测试

- [x] 4.1 纯函数 15 条：六种分因各一 + 上界 + 翻完 + 从选中日起算 + 选中日自己
      可以是基准 + 名单只有代码 + 损坏 payload **抛**而不是给空名单
- [x] 4.2 拿**本机真实工件**跑一遍（worktree 无 `output/` 时 skip，不伪造通过）
- [x] 4.3 实测结论：本机 `2026-07-31` 是 HOLD、`2026-06-16` schema 不受支持 ⇒
      **当前没有可信基准**。这正是这一页今天回答不了、而操作人需要知道的事
- [x] 4.4 变异复验 **17 条全咬住**（含「老工件被当成再平衡日」「HOLD 日也当基准」
      「扫描无上界」「撞上界谎报成翻完了」「忽略 as_of」「缺代码的候选被静默丢弃」
      「给名单加导出按钮」「渲染出执行口径词」）
- [x] 4.5 `tests/logic` + `tests/governance` **5064 passed**；ruff /
      mypy --strict / openspec validate --strict 全绿

## 5. 划界（本 change 不做）

- 不做基准名单 × 当日 top-K 的成员对照（下一个 PR，依赖本 PR 的基准定位）。
- 不做跨日决策履历浏览（再下一个；它要能在工件缺失时照常工作，会打破「页面由
  工件驱动」这个现有前提）。
- 不碰决策日志的 schema 或写入路径。
- 不引入 `src.inference.rebalance_schedule`：节奏只读产出器写下的字段。

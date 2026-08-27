# Tasks

## 1. 纯函数（`web/operator_ui/compare_basket.py`）

- [x] 1.1 `admit_to_basket(run_id, *, selectable_ids, run_id_alias, all_rows)`
      → 六态判定，每种不可加入都给**具体**原因
- [x] 1.2 `add_to_basket` / `remove_from_basket`：篮子存**解析后**的 id
      （入口计数与对比页选中数不许对不上）
- [x] 1.3 `basket_query_value` / `basket_readiness`
- [x] 1.4 上界 `MAX_BASKET_SIZE=5` 与 `MIN_COMPARE_SIZE=2`：注释写明它们是
      `_param_guard._run_ids` 与对比页 `max_selections` 的副本

## 2. 共享渲染（`compare_basket_widget.py`）

- [x] 2.1 准入是函数做的**第一件事**，按钮据此禁用——等按下再报错等于把人
      送进一次注定失败的交互
- [x] 2.2 每种拒绝都当场说清是哪一种；别名在加入**前**披露
- [x] 2.3 篮子为空时什么都不画（常驻空面板白占注意力预算）
- [x] 2.4 不足 2 个不给链接，改说还差几个

## 3. 三个来源页接线

- [x] 3.1 `jobs.py`：动作栏 2/3 列 → 3/4 列
- [x] 3.2 `_results_render.py`：动作栏 4 列 → 5 列
- [x] 3.3 `walk_forward.py`：本页此前**没有任何 run-level 动作**，在 KPI 前
      加一行；当前运行 id 取自 `run_options[目录键]`
- [x] 3.4 三页都传**全量**目录行：只传 `catalog.rows`（当前所有者）的话，
      「被接管」会退化成「目录里根本没有」，分因就没了

## 4. 测试

- [x] 4.1 六种准入各一条 + 篮子操作（存解析后 id / 重复报告不去重 / 上界 /
      不可加入不入篮 / 移除保序）
- [x] 4.2 **同一性**钉：满篮子真跑 `sanitize("run_ids", ...)` 通过、超一个
      被拒；`max_selections` 与 `2 <= len <= 5` 对齐；`COMPARABLE_TYPES` 与
      `selectable_catalog` 的 `allowed_types` 正则取出比对；page_link 目标
      文件存在
- [x] 4.3 AST 钉：准入是第一件事；`disabled=` 条件表达式整行
- [x] 4.4 三页接线钉 + 全量目录行传参
- [x] 4.5 `tests/logic` + `tests/governance` 全量 / ruff / mypy --strict

## 5. 划界（本 change 不做）

- 不判可比性：那是对比页 `assess_comparability` 的事，入口重推必然漂移。
- 篮子不跨会话持久化：它是「我现在想比这几个」的临时意图，不是需要留档的
  研究决定。
- 不做批量加入（「把这一页筛选结果全加进来」）：上界是 5，批量只会把篮子
  一次填满并逼出「先移除一个」。

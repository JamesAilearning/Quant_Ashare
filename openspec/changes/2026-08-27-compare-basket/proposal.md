# 对比篮子：把「我想比这几次运行」从来源页带到对比页

## Why

研究运行对比页已经能通过 `?run_ids=a,b` 预填选择，但**没有任何页面产生
那个 URL**。操作人在作业页/结果页/滚动验证页看到一次想比的运行，只能记下
运行 ID，再手工去对比页的下拉框里翻——下拉框的标签是
`{run_id} · {type} · {status} · {timestamp}`，几十条里靠肉眼找两个 ID。

滚动验证页更糟：它此前**没有任何 run-level 动作**，看完一次运行想跟另一
次比，连个入口都没有。

## What Changes

会话级的**对比篮子**：三个来源页各加一个「＋ 加入对比」，攒够 2 个后给出
带 `?run_ids=...` 的对比页链接。

**准入在按下之前判好**。对比页的可选目录来自 `selectable_catalog`：每个
`(类型, 产物目录)` 只留一个当前所有者，其余的要么经 `run_id_alias` 折进
所有者，要么不可寻址。一个未知 id 会让对比页 `st.error` + `st.stop()`——
整页停在拒绝信息上。所以按钮在不可加入时是**禁用**的，且旁边写明是哪一种：

| 判定 | 含义 |
| --- | --- |
| `ok` | 直接可选 |
| `aliased` | 当前工件由另一个 id 持有，**说出**会以哪个 id 加入 |
| `superseded` | 产物目录已被同目录的更新运行接管 |
| `wrong_type` | 对比页只收 pipeline / walk_forward |
| `no_artifacts` | 没有记录产物目录 |
| `unknown` | 不在统一作业目录中 |

一句「不可用」不算如实——这几类对操作人的下一步完全不同。

## Impact

- Affected specs: `v2-operator-ui-console`
- Affected code：新增 `web/operator_ui/compare_basket.py`（纯函数）与
  `compare_basket_widget.py`（共享渲染）；三个来源页各加接线
- **只读**：不启动作业、不改任何工件、不写 URL 以外的跨页状态
- **不判可比性**：实验合同是否一致、指标是否完整，是对比页
  `assess_comparability` 的事。在入口重推一遍就是第二份会漂移的推导。

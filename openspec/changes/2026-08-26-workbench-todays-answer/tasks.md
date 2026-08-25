# Tasks: 2026-08-26-workbench-todays-answer

## 实现

- [x] `todays_buy_answer`：三态 + 如实边缘的纯合成层，零自造判定
      （陈旧/完整性=出单侧裁决；节奏=已核验信号分类；说给今天=
      `entry_date == cn_today()`）
- [x] 优先级：出单侧判据先行——会拒时即使工件看似今天也拒答
- [x] 流程态与异常态分开：`no_instruction`（点名最新指令面向哪天）≠
      `unanswerable`（带原因）
- [x] 每态带「不是订单」免责声明
- [x] 页面顶部插槽回填（容器占位，不打乱既有计算顺序），四态显式配色

## 验证（每条要实测数字）

- [x] 新守卫 13 条：四态各自的文案与数字如实（含 15/14 落后天数）、优先级
      钉、完整性 None 不当放行、裁决不可达带原因、entry 早/晚各点名日期、
      无节奏标记拒合成、四态免责声明齐、接线源码钉 + 配色四态齐
- [x] 变异实测：短路 `refuses_today` 分支 → 优先级钉 1 failed（咬住）
- [x] 定向 30 passed / 17 subtests；logic 全量见 PR 实测数字
- [x] openspec validate --strict valid

## 流程说明

实现与规格同 PR，遵循 #467 的先例（codex 判 UI 新契约必须有配套 change）。

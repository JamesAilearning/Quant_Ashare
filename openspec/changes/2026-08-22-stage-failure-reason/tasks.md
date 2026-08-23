# Tasks: 2026-08-22-stage-failure-reason

## 实现

- [x] `_capture_stage_errors()`：作用域内收集阶段自己的 ERROR 行
- [x] `_stage_detail()`：折成单行、有上限、截断必须声明、空捕获不编造
- [x] 四个阶段调用点全部套上（fetch / snapshot / 五个 rebuild / validate）
- [x] 工作台失败卡片与待办队列共用 `failed_update_summary`
- [x] 退出码 11 的文案（UI 常量 + 运维手册 + 编排器 docstring）

## 验证（每条要实测数字）

- [x] 生产者守卫 30 条 + 8 subtests
- [x] 消费端与退出码守卫 15 条 + 4 subtests
- [x] 变异 28 条全部被咬（含「捕获点挂错 logger」这条静默空转变异）

## codex 第一轮：三条 P2，全部属实

| | 问题 | 根子 |
|---|---|---|
| P2 | 截断从头填到超限就停，把 01 hole report 最后那句 "Re-run with the same --output-dir" 切掉 | **正是我反对 200 字符上限时用的那个论据**，我自己在另一处又犯了 |
| P2 | `PITValidator._log_summary` 全部用 INFO，包括失败检查的 error 文本 → validate 阶段一条都收不到 | 只验了假 runner（它自己发 ERROR），没验真实校验路径 |
| P2 | 阶段一条 ERROR 都没记时，`detail` 是裸摘要，读侧渲染成「原因：fetch failed hard (exit 1)」 | 把「只有退出码」伪装成一条解释——比不说更糟 |

修法：

- 截断改为**保头尾**：第一条通常是为什么，最后一条通常是怎么办；丢中间并报数
- 校验器把**失败的**检查按 ERROR 记（警告仍 INFO——警告-only 在本项目里是 PASS）
- 兜底串附一个标记，读侧据此把「没有原因」与「有原因」分开；标记串两侧各声明
  一次（两模块刻意不互相 import），配一条一致性守卫，与 `STATUS_SCHEMA_VERSION`
  同样的处理

codex 点名要「用真实校验日志行为、而不是假 runner」写回归——照做了：直接调真的
`_log_summary`，并端到端验它那句话走进 `detail`。同样，「用一个静默失败的 runner
走完整链路」而不是伪造一个空 `detail`，也照做了。
- [x] 分目录实测：data_pipeline 447 / governance 446 / pit 34 / logic 4194
- [x] ruff clean；mypy --strict 231 文件 0 error
- [x] `openspec validate --strict` valid
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 自审第三轮：携带原因不能反过来把整份记录弄丢

`_record_status` 以 `ensure_ascii=False` 序列化，一个不成对代理会让写盘抛
UnicodeEncodeError，而它按「可观测性失败不改变退出码」的契约把异常吞掉——代价是
**整份状态记录写不出来**，UI 继续显示上一次的记录，操作人以为什么都没跑。

本改动之前 `detail` 只承载编排器自己写的常量串与异常消息，这条路几乎走不到；
现在它承载阶段记进日志的**任意文本**，而 `surrogateescape`（Python 解码文件系统
路径的方式）恰恰产出代理。三个前提都实测证过：代理确实让工件写不出来、
surrogateescape 确实产出代理、`_stage_detail` 确实原样带过去。

修在 `_stage_detail`（引入任意文本的是本改动），不动 `_record_status` 那份刻意
且被 codex 加固过的「吞一切」契约。用 `backslashreplace` 而非 `replace`：后者只留
一个问号，把「这里原本有个诡异字节」这条线索也抹掉。

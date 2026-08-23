# Tasks: 2026-08-22-stage-failure-reason

## 实现

- [x] `_capture_stage_errors()`：作用域内收集阶段自己的 ERROR 行
- [x] `_stage_detail()`：折成单行、有上限、截断必须声明、空捕获不编造
- [x] 四个阶段调用点全部套上（fetch / snapshot / 五个 rebuild / validate）
- [x] 工作台失败卡片与待办队列共用 `failed_update_summary`
- [x] 退出码 11 的文案（UI 常量 + 运维手册 + 编排器 docstring）

## 验证（每条要实测数字）

- [x] 生产者守卫 28 条 + 7 subtests
- [x] 消费端与退出码守卫 9 条 + 3 subtests
- [x] 变异 20 条全部被咬（含「捕获点挂错 logger」这条静默空转变异）
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

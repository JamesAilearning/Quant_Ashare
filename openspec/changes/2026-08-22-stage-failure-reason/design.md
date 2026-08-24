# Design: 阶段失败原因的传递

## 那道缝在哪

```
01_fetch_tushare.py  ──logger.error("...Re-run the full range...")──▶ 日志文件
        │
        └── return 1 ──▶ Runner = Callable[[list[str]], int] ──▶ 编排器
                                                                   │
                                          "fetch failed hard (exit 1)" ──▶ 工件 ──▶ UI
```

int 是唯一穿得过去的东西。原因和修法都留在了左上角。

## 为什么是「编排器侧的作用域捕获」，不是别的

| 方案 | 否决理由 |
|---|---|
| 改 `Runner` 让它返回富结构 | 要动七个脚本的 `main(argv) -> int` 签名与 CLI 约定 |
| 只给 fetch 补一个专用通道 | 下一个阶段栽了会原样重演；这是逐个补，不是判据 |
| 消费端读日志尾巴 | 归属不可靠。`update_progress.py` 模块文档已判过一次：跨运行追加的日志要精确归属，得先在写入侧落带日期的运行边界 |
| 让 06 用它已有的 `--report-json` | 只解决一个阶段，且 `build_plan` 从不传这个参数 |

作用域捕获对七个阶段一视同仁、零脚本改动，并且**顺带**把
`_verify_snapshot_refreshed` 这个非 `Runner` 的阶段也接上了——它的原因同样只记
在日志里。

## 两个决定成败的细节

**捕获点必须是 `logging.getLogger("src")`。** `src/core/logger.py:66` 在那里设了
`propagate = False`（为免重复输出）。挂在真 root 上的 handler 一条记录都收不到，
整套机制会**静默空转**、测试还全绿。有一条守卫直接钉这条布线。

**分辨「阶段自己报的」与「编排器对它的转述」靠作用域，不靠 logger 名。** 编排器
每一句 `_logger.error("Fetch FAILED ...")` 都在阶段调用**返回之后**才发出，落在
窗口外；而 `_verify_snapshot_refreshed` 把原因记在自己函数体内，落在窗口内。按
logger 名过滤反而会做错——阶段栽在它调用的 helper 模块里时，那条 ERROR 同样是
这个阶段的失败原因。

## 上限为什么不是 200

`job_io._extract_failure_detail` 用 200，那是给 Jobs 页表格单元格的。本改动要救
的那句真实消息约 350 字符，而**后**半句才是修法。按 200 截会精准地留下抱怨、切
掉办法。取 1200，且截断必须声明、丢弃条数必须报出来。

## 不变式

可观测性绝不允许改变运行结果。`logging.Handler.handle` 并不包住 `emit`，所以
handler 里抛出的异常会从阶段自己那句 `logger.error(...)` 冒出去，把一次**可诊断
的失败**变成一次崩溃——正是本改动要消除的那种事。`emit` 因此吞掉一切：少一行详
情可以接受，改变退出码不可以。摘除写在 `finally` 而非 `except`。

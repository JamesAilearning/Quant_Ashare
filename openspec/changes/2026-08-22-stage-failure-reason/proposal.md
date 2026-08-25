# 阶段失败必须带着它自己报出的原因

## Why

2026-08-17 / 08-20 / 08-21，夜间更新连续三晚失败，退出码全是 11。状态工件里
三晚写的是同一句话：

```
fetch failed hard (exit 1)
```

而 01 在那三晚里每一次都已经把话说全了，包括**怎么修**：

```
refusing narrower-scope merge for endpoint 'daily': this run covered
[20180101, 20260821] but the manifest already covers [20151001, 20260821] with
unresolved holes. A narrower range does not re-attempt every prior hole, so
self-healing would silently drop out-of-range holes. Re-run the full range to
extend it, or pass --reset-manifest
```

这句话停在日志里，因为 `Runner = Callable[[list[str]], int]` 只让一个 int 穿过
编排器与阶段脚本之间那道缝。工件、UI、待办队列拿到的全部信息就是「11」。

雪上加霜的是，那时三份退出码表都把 11 写作「查 token / 网络」。而这三晚的
token 与网络自始至终正常——排障因此被文案直接引向了错的方向。

## What Changes

- **生产者**：编排器给每一次阶段调用套一个作用域内的日志捕获，把该阶段自己
  报出的 ERROR 行折进状态工件的 `detail`。七个脚本一行都不改，七个阶段一视
  同仁（只补 fetch 会在下一个阶段上原样重演）。
- **消费端**：今日工作台的失败卡片与待办队列渲染这个原因，两处走同一个函数。
- **退出码表**：11 不再冒充一个具体原因；三份表加一致性守卫。

## Non-goals

- 不改任何阶段脚本的退出码约定或 CLI。
- 不改 `startup_repair` / `swap`：它们的原因来自被捕获的异常对象本身，已经在
  `detail` 里，再套一层只会把同一句话写两遍。
- 不做日志尾巴抓取。`update_progress.py` 的模块文档已经写明：跨运行追加的日志
  要精确归属，得先让写入侧落一个带日期的运行边界——那是另一个改动。

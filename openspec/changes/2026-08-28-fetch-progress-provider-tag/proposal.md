# fetch 进度行带上 provider 标记：把「不知道是谁的」变成确定归属

## Why

运行中心的「走到哪了」在生产上几乎总说「无法归属」。

判据（#465 建立）是**窗口完整 + 边界独占**：读到的那段日志里必须一条边界都
不是别人的，才敢说这条进度属于本次运行。两个前提在生产上都站不住：

1. 真实日志按**尾部**读（`log_tail` 只取尾部几千字符），窗口天然截断——而
   「我看不到别人的边界」证明不了别人不存在；
2. 兄弟 bundle **共用同一份** `daily_update.log`（日志路径由 provider 的
   **父**目录推导），而单飞锁是 **per-provider** 的——两个 provider 可以同时
   在跑，行会交错。

于是操作人看到的是「最后一条进度是 daily 2026 年 2400/5883，但**这可能不是
本次运行的**」。信息在，可信度不在。

出路早就写在代码里。`update_progress._current_segment` 的 docstring：

> 要把这条判据放松回「最后一条是我们的」，得先让写入侧给进度行本身打上
> provider 标记，或让每个 provider 写自己的日志。

本 change 做前者（backlog 已裁决走这条：改动最小，「按 provider 分日志」被否）。

## What Changes

**写侧盖章**：`TushareFetcherConfig` 新增 `provider_tag`，进度行末尾追加
` provider=<规范化目录>`。编排器用**与运行边界行同一个** `_norm(provider_dir)`
传进去（`--provider-tag`）——两处身份差一个字节，读侧的完整回环校验就会把它
判成别人的，而症状只是「归属还是报不出来」，不像个 bug。

无标记时**什么也不加**，不是加一个 `provider=` 空值：读侧必须能分辨「这次没报
身份」与「报了一个空身份」。含换行的标记一律当作没有标记——带换行的行会被行式
日志切成两半，前半截的身份被截断成一个**不同**却完全合法的身份串。

**读侧据此放松判据**：窗口里有**我们自己**盖过标记的进度行时，归属只需
「我们最后一条边界 + 按标记过滤」——不要求窗口完整，也不怕别人的边界穿插。
同一个 provider 不会与自己并发（单飞锁），所以自己的边界之后、下一条自己的
边界之前，带自己标记的行只能是这一次的。

**老日志照常**：`provider` 捕获组可选，标记落地之前写的行仍能解析。不带标记的
行**不属于任何人**——归属退回边界独占判据，也就是标记落地之前的行为，没有退步。

## Impact

- Affected specs: `v2-daily-data-update`（写侧）、`v2-run-center-page`（读侧）
- Affected code:
  - `src/data/tushare/fetcher.py`（`provider_tag` 字段 + 进度行后缀）
  - `scripts/data_pipeline/01_fetch_tushare.py`（`--provider-tag`）
  - `src/data_pipeline/daily_update.py`（fetch argv 带上身份）
  - `web/operator_ui/update_progress.py`（可选捕获组 + 标记路径 + 边界校验
    抽成共用函数）
- **日志格式变了**（追加后缀），但只在行尾追加：既有读侧正则以 `(skipped=…)`
  收尾，老解析器与新日志、新解析器与老日志都照常。
- 不改产物、不改退出码、不改任何数据路径。

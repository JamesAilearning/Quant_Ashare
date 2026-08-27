# Tasks

## 1. 写侧盖章

- [x] 1.1 `TushareFetcherConfig.provider_tag` + `_progress_provider_suffix()`
- [x] 1.2 进度行末尾追加 ` provider=<tag>`（**行尾**，既有读侧正则以
      `(skipped=…)` 收尾，老解析器照常匹配）
- [x] 1.3 无标记时**什么也不加**（不是 `provider=` 空值）——读侧要能分辨
      「没报身份」与「报了空身份」
- [x] 1.4 含换行的标记一律当作没有标记：行式日志会把它切成两半，前半截的身份
      被截断成一个**不同**却完全合法的身份串
- [x] 1.5 `01_fetch_tushare.py` 加 `--provider-tag`
- [x] 1.6 编排器用**与 `run_boundary_line` 同一个** `_norm(provider_dir)`

## 2. 读侧据此放松判据

- [x] 2.1 `_PROGRESS_RE` 的 provider 组**可选**且锚行尾（不锚的话一个恰好含
      `provider=` 的名字会被读走）
- [x] 2.2 `FetchProgress.provider` 字段；空串 = 这条行没报身份
- [x] 2.3 `last_fetch_progress(..., provider_key=)` 按标记过滤；不带标记的行
      在这条路径上**一律跳过**
- [x] 2.4 `_own_boundary`：标记路径的定位器，判据只有「最后一条**我们自己的**
      边界」，**不要求** `window_complete`
- [x] 2.5 `_provider_matches`：与边界身份同一条**完整回环**规矩
- [x] 2.6 `_boundary_defect`：两条路径共用一份边界校验（两份会漂，而漂的那份
      会把损坏边界当成合法起点）
- [x] 2.7 失败原因取两条路径中**更贴切**的那个（损坏边界不该报成窗口截断）

## 3. 测试

- [x] 3.1 写侧：配置了标记 / 没配 / 标记含换行
- [x] 3.2 **同一性**：编排器传给 fetch 的标记 == 它写进边界行的身份；且标记
      本身是自己的 `normcase(resolve())` 完整回环
- [x] 3.3 向后兼容：老行照常解析、带标记的行带出 provider、恰好含
      `provider=` 的 endpoint 名不被读成标记
- [x] 3.4 标记路径：截断窗口仍能归属 / 别人的边界穿插不再挡路 / 只取我们自己
      最后一条边界之后 / 无标记时退回边界独占 / 只有别人的标记不归属 / 没有
      我们的边界不归属 / 损坏边界报 corrupt 而非 truncated / 非规范形态的标记
      被拒
- [x] 3.5 变异复验 **21 条全咬住**。首轮 7 条真逃逸，全部修在源头：
      - 「进度行不盖章」「CLI 身份没接进 config」→ 补接线钉（盖章函数本身对，
        不代表进度行/CLI **用了**它）
      - 「身份比较被宽容化」→ 补 strip / 大小写 / basename 三种拼写的用例
      - 「别人的边界也当成我们的起点」→ 补一条「别人的边界在最后、我们的进度
        在它之前」的用例
      - 「独占判据熄火」→ 补 foreign_boundary 用例（#465 遗留的覆盖缺口）
      - 「规范形态校验熄火」→ **删掉那段代码**：`stamped == provider_key` 之后
        再算 `normcase(resolve(stamped))` 不可达（provider_key 本身就是规范
        形态），留着只会让人以为多了一层防御
- [x] 3.6 `tests/logic` + `tests/governance` + `tests/data_pipeline`
      **5552 passed**；ruff / mypy --strict / openspec validate --strict 全绿

## 4. 划界（本 change 不做）

- 不给**别的**阶段的日志行加标记：本 change 只解决「走到哪了」这一条读侧
  诉求，fetch 进度行是它唯一的数据源。
- 不改日志路径（「每个 provider 写自己的日志」是被否掉的选项 (c)）。
- 不用标记去判断「哪一次运行」：标记只解决「哪个 provider」。历次运行的行都
  躺在同一份日志里，所以仍要从**我们自己最后一条边界**之后取。

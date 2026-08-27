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


## 第二轮（codex P2）：provider 标记在生产上不生效 → 加运行身份

- [x] **先证伪首版。** 一条用例把前提钉死：一个 endpoint-年的进度行
      （30 行 × 约 150 字符）就撑破 4000 字符的读取窗口，边界被挤出去，
      标记路径退化成 `window_truncated`。没有这一条，后面每一条都只在证明
      「新路径能用」，证明不了「旧路径不够用」
- [x] 写侧：编排器铸 `run_id = uuid4().hex`，同写状态工件与 fetch argv
      （`--run-id`）；fetcher 盖在每条进度行上（`run=` 排在 `provider=`
      **之前**——provider 是路径、可含空格，必须占到行尾）
- [x] 读侧：`run_id` 非空时按逐字节相等过滤进度行，**不经过任何边界**；
      边界法保留为老日志/手工 fetch 的退路
- [x] `AttributedProgress.attribution` 记录归属**是怎么来的**；页面按它分派，
      不再拿边界戳比工件起跑时刻（run 戳路径没有边界戳，比就会说成对不上）
- [x] 新分因 `no_own_run_stamp`：窗口里的进度行带的是别的运行的身份。与
      「窗口截断」分开说——后者会让操作人去调大窗口，而问题在别处
- [x] 畸形的 `run=` 只是**不匹配**，不是「日志损坏」：一条脏行不该毁掉整页
      归属。工件里的 `run_id` 相反——畸形当损坏（同 `pid` / `launch_nonce`），
      因为读成「没有身份」会让归属悄悄退回一条答不出来的路径
- [x] **两端同一身份**由一条**整跑**用例证明：跑一次编排，取工件里的
      `run_id` 与 fetch argv 里的 `--run-id` 对。分别测「工件写了」「argv
      传了」证明不了它们是同一个
- [x] 两次运行拿到两个不同 id（上一次留在窗口里的行靠这个不同才不被误收）
- [x] 首版那条「进度行经由后缀助手」的守卫钉的是**排版**（含缩进换行的源码
      串），加第二个后缀时因换行改写而失配 → 换成 AST：找到进度那条
      `_logger.info`，看它尾部实参里出现了哪些助手调用
- [x] 变异复验 **21 条全咬住**（首轮 17/21，逃逸四条各自补钉：工件不写
      run_id / 没转发进 build_plan / 页面不按来源分派 / 读取器丢掉参数）
- [x] `pytest tests/`（除 e2e）**5614 passed / 38 skipped / 1892 subtests**。
      `tests/regression` 里 REGEN-2 replay 的 2 个 error 是**本机依赖栈**不在
      canonical pin 上（numpy 2.4.4 / scipy 1.17.1，测试自己打印了这条警告），
      与本改动无关；CI 跑的是钉住的栈
- [x] ruff / mypy --strict（四个改动源文件）全绿

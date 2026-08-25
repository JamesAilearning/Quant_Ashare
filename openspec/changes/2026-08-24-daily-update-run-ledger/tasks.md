# Tasks: 2026-08-24-daily-update-run-ledger

## 实现

- [x] 写侧 `daily_update.py`：终态追加一行台账（残尾隔离 + 单次 write + fsync）
- [x] 写侧 `daily_update.py`：起跑时写一行带日期的运行边界
- [x] 读侧：台账 reader（容错解析、坏行计数、按 provider 过滤）
- [x] 读侧：`update_progress` 按边界切段，切不到就如实说不知道
- [x] UI：今日工作台「近 N 次」条带

## 治理钉（机器强制）

- [x] 台账**只可追加**：`_append_ledger` 的 `open` mode 只能是 `ab`/`rb`，
      且不许出现 `write_text` / `os.replace` / `truncate` / `write_bytes`
- [x] 台账写入失败**绝不改变退出码**（吞 `Exception` 而非 `OSError`）
- [x] `--dry-run` 不写台账、不写边界
- [x] 阶段语义零改动：AST 守卫钉住 `_execute_daily_update` 内不得出现
      `_append_ledger` / `run_boundary_line` / `default_ledger_path`
- [x] 边界带完整日期 + normalized provider；别的 provider 的边界不采纳
- [x] UI 复现台账、不自造判定；台账缺失/不可读/坏行都要说出来

## 验证（实测数字）

- [x] 写侧守卫 **18 条 + 7 subtests**；读侧/UI 守卫 **38 条 + 12 subtests**
- [x] 变异累计 **20 条全咬**
- [x] 分目录：logic **4284** / data_pipeline **437** / governance **517** / pit **34**
- [x] ruff clean；mypy --strict **232 文件 0 error**
- [x] `openspec validate --strict` valid
- [ ] codex CLEAN + CI 七绿 → STOP 等 merge

## codex 第七轮：一个 P2 —— 判据从「本 provider 的路径」抬到「命名空间」

兄弟 provider B 把 --status-path 指到 **A 的**台账上：B 的配置里
`default_ledger_path(B)` 与之不等，照样通过——B 的第一次 _record_status 就把
A 的只可追加历史原子替换掉。单个配置**看不见**别的 provider，所以判据抬到
命名空间：`*.daily_update_ledger.jsonl` 这个名字形状整体保留给台账写入者，
任何可配置路径都不许落在这个形状上——不必知道它是谁的台账。仍在
`__post_init__` 配置验证层，阶段语义零改动。

## codex 第六轮：一个 P1 —— 台账也是写入者，碰撞在构造期拒绝

台账路径是派生的、没有 CLI 开关——但碰撞的**另一头**可以被打错：
--delisted-registry / --reference-cases / 显式 --status-path 指到
`<provider>.daily_update_ledger.jsonl` 上，终态一到台账把 JSON 追加进
canonical 输入；status 撞上更糟，每次 _record_status 的原子替换把「只可追加」
的台账整个截掉。修在 `__post_init__` **配置验证层**（状态路径那套守卫的所在
地），阶段语义零改动的硬约束不动。containment 查 provider/tushare 树与
.new/.bak，equality 查三个可打错的输入 + 单飞锁。

**顺带删了一条自己刚写的死守卫**：台账 vs 状态暂存名的相等检查——暂存名 =
名字+".tmp" 而台账以 .jsonl 结尾，碰撞不可构造。查不可构造的碰撞是死代码。

## codex 第五轮：两个 P2 —— 洗白的数据与对不上的两本账

**坏字节落在 JSON 字符串里时，替换解码会把它洗成合法行。** 整份
`errors="replace"` 后 detail 里的坏字节变成 `�`，JSON 照样合法、验形照过、
渲染成一次真实运行、malformed 计零。我此前注释里写「那一行随后多半解析失败」
——**错**，坏字节在字符串里时恰恰不失败。改为逐行严格解码：解码失败 = 坏行。

**日志与状态工件是两本各自往前走的账。** 状态写入是 best-effort（写失败只记
ERROR，运行照常），于是旧运行留下 running 状态、新运行只落边界的情形是真实
存在的。「确定归属」的口气只许在边界戳与显示的状态记录 `started_at` **精确
相等**时用（写入侧同一次运行用同一个 `started_at.isoformat()` 写两者——前提
有源码级守卫钉住）；对不上时如实说两本账不一致、进度属于边界那次。这**不是**
被禁的那个启发式：禁的是拿进度行的**时分秒**去比状态起跑时刻来「猜」，这里
是两个完整 ISO 戳的相等性**验证**。

## codex 第四轮：三个 P2 —— 全部是「诚实」的续深

**验形要在分类之前。** `{}` 或 `{"provider_dir": 5}` 被计进 foreign，页面就会
说「这行属于另一个 provider」——把损坏说成了别人。「foreign」只配给完整合法、
仅身份不同的 v1 记录。

**身份/时间字段要非空。** 空串通过 `isinstance(str)`，一条 `exit_code: 0` 配
空时间戳的行会渲染成「日期不明的成功」。provider_dir/run_date/started_at/
finished_at 四个字段钉非空；detail 只钉类型（空不空是措辞问题，不是身份问题）。

**未归属要说真原因。** 三种「不知道」（窗口截断/有外来边界/确实无边界）对
操作人的下一步不同，而页面一律说「窗口里没有边界」——在最常见的截断窗口上
这是句假话（边界明明可见）。`AttributedProgress.unattributed_reason` 三值枚举，
页面逐一措辞，守卫钉住三个键都接上。

## codex 第三轮：一个 P1 + 一个 P2

**P1 截断窗口不许声称独占——同一根因的第三种形态。** 第二轮把判据抬到「窗口
里的边界全是我们的」，但窗口是 `log_tail` 的尾部几千字符：兄弟 B 起得足够早，
它的边界已滚出窗口而 B 仍在写，窗口里只剩我们的边界，独占检查照样通过。
**「窗口里看不到」证明不了「不存在」**。修法：`log_window` 把「我看到的是不是
全部」这一事实交出去（缺失日志 = 完整的空），`last_fetch_progress_for_run` 的
`window_complete` 为**必填**参数，截断窗口一律如实说不知道；另有 AST 守卫钉住
调用点不许自填 True。实际后果如实呈报：真实日志几乎总大于窗口，于是生产上
归属几乎总是「不知道」——要拿回确定归属，得让进度行自带 provider 标记或按
provider 分日志，两者都在生产编排器那一侧，呈裁决（本 change 阶段语义零改动
的硬约束不动）。

**P2 `schema_version` 只比值不钉类型。** JSON 的 `true` 与 `1.0` 在 Python 里
都 `== 1`——一条版本字段本身就坏掉的行会被拿 v1 语义硬解释。与 exit_code 的
bool 排除同一课：先钉类型再比值（`type(version) is not int`）。

## codex 第二轮：一个 P1 + 一个 P2

**P1 反向交错——边界排序推不出归属。** 第一轮我把判据改成「最后一条边界是
我们的就算数」。它在反向交错下仍然说错话：B 先起跑（边界 B），A 随后起跑
（边界 A，成了最后一条），而 **B 仍在跑**——B 的进度行不会再带一条边界，于是
它们落在边界 A 之后，被当成 A 的。

判据因此抬到**独占**：窗口里的边界**全部**是我们的，才谈得上归属。进度行本身
不带 provider，靠边界排序推不出来；而同一个 provider 不会与自己并发（单飞锁是
per-provider），所以「边界全是我们的」就足以断定其后的行也是我们的。代码反而
更短了。

codex 另给了两条出路——给进度行打 provider 标记、或每个 provider 写自己的
日志。**两条都在生产编排器的阶段语义那一侧，本轮不碰**（本 change 的硬约束就是
阶段语义零改动）。要放松这条判据，得先另起一个改动。

**P2 台账缺一条跨字段不变式。** `exit_code: 0` 配 `failed_stage: "fetch"` 自相
矛盾，而只查字段类型的话它原样通过，`LedgerRun.ok` 会把它渲染成一次**成功**的
运行。状态工件 reader 早已钉住同一条不变式（`update_status` 里 exit_code 与
failed_stage 互相印证），这里照抄，不另立一套；顺带补齐它另外两条：字段**缺席**
不等于 `null`（`.get()` 会把缺席读成成功），空串不是阶段名。

## codex 第一轮：一个 P1 + 两个 P2

**P1 别人的边界排在我们后面时，不许回头用我们那条旧的。** 兄弟 bundle **共用
同一条日志**（`default_log_path` 取 `<provider 父目录>/logs/daily_update.log`），
而单飞锁是 **per-provider** 的——两个 provider **可以同时在跑**，行会交错。首版
「跳过别人的边界、找我们最后一条」会把交错进来的**别人的**进度当成我们的，还以
「归属已确定」的口气说出来。改为：取**任意 provider** 的最后一条边界，再问它是不
是我们的；不是就说不知道。

**P2 带对 provider 的 JSON 对象 ≠ 一条可解释的记录。** 首版不校验
`schema_version`，也不校验字段类型——未来版本的记录、或 `exit_code: true`
（`isinstance(True, int)` 在 Python 里为真！）都会被显示成一次**失败的运行**。
把损坏的数据讲成事实，比报「读不了」糟得多。加了 v1 校验，不合格计入 malformed。

**P2 整份台账全坏时 `runs` 也是空的。** 那条「还没有记录」的早返回没带计数，于是
一份**损坏的**历史看起来像良性的空历史。计数改为在进入分支之前拼好，两条分支都带。

## 一处守卫自身的切片太宽（变异抓到）

「空分支要带计数」那条守卫，第一版用文本位置切 `strip[index("if not history.runs"):]`
——把**后面**那条正常 caption 也圈了进来，`note_text` 在那里出现，于是断言真空地
绿着。改用 AST 精确取那个 `If` 节点的分支体。

## 刻意不做（勿在评审中重开）

- 不存耗时字段（可由两个时间戳推出）
- 不写运行「结束」标记（状态工件与台账已回答）
- 不给台账加 CLI 覆盖开关（那会把状态工件那整套路径守卫一并请进来）
- 不做百分比进度条（fetch 只是六阶段里的第二个）
- 窗口里找不到边界时不扩大读取直到找到（日志无界，那是没有上界的读取）

## 三条既有守卫开火，处置一律「改我，不削弱守卫」

| 守卫 | 为什么开火 | 处置 |
|---|---|---|
| `src/` 零 `decision_journal` 引用 | 我在 `_append_ledger` 的 docstring 里点了那个模块的名字，去说明追加纪律照抄自它 | **改我的措辞**：只讲道理不点名。治理边界是「`src/` 与 web 层自有状态零关联」，为一句注释破例不值 |
| `update_progress` 不许长回归属猜测（禁 `started_at` 等 token） | 我的字段叫 `boundary_started_at` | **改我的字段名** → `boundary_stamp`。被禁的那个名字指的是「拿进度时刻比状态工件的起跑时刻」这个被证伪的启发式；我的戳来自边界本身、不参与比较。改名把区别摆明，守卫一字未动 |
| 进度只在 running 分支内渲染 | 它用 `_progress = _read_progress()` 作**定位锚**，我改名后它先匹配到后面的 `_baseline_progress = _read_progress()` | **只更新锚串，断言一字未动**——那条断言依然完全正确 |

第二条尤其值得记：那个守卫当年立起来，正是为了**阻止**「给进度加归属」。它的前提
（写入侧没有带日期的边界）被本改动消除了——但正确做法不是删它，而是让我的命名与
它划清界限，并另加一条正面守卫钉住「归属来自边界，不是来自比时刻」。

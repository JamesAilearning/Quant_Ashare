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

## codex 第十九轮：两个 P2 —— 探针也要非阻塞、边界戳要验形

**残尾探针先于追加阻塞。** 上一轮把追加改成了非阻塞，而它**前面**的残尾
探针还是 `is_file()/stat()` 后的阻塞 `open("rb")`——FIFO 挂死只是提前了
一行。探针改同一套纪律：os.open(O_RDONLY|O_NONBLOCK|O_NOFOLLOW) + 描述符
S_ISREG 验证。守卫钉「O_NONBLOCK/S_ISREG 各出现 ≥2 次」——只钉一处，另一处
丢了照样挂死。

**边界戳 `\S+` 太宽。** 坏字节/遗留编码洗出的乱码被当「起跑时刻」，
run_center 的不一致分支以确定口气宣布归属。戳验形（fromisoformat + 带时区）
验不过 = corrupt_boundary 第四态，与台账坏行同一处置；页面第四句措辞 + 键
守卫扩四。

## codex 第十八轮：两个 P2 —— FIFO 会挂死、fromisoformat 太宽容

**FIFO 上的 O_WRONLY open 会阻塞等读者**——一次已完成的更新挂死在可观测性
写入上、握着单飞锁不放，反向耦合契约被一个 open 反转。O_NONBLOCK（无读者的
FIFO 直接 ENXIO 进吞噬通道）+ 已开描述符上 S_ISREG 验证（有读者时 open 会
成功，靠它拒掉）。常规文件不受 O_NONBLOCK 影响。

**`date.fromisoformat` 接受 `20260825`、`2026-W35-2`** 这类写入侧永不产的
形态——工作台按 YYYY-MM-DD 切 `[5:]` 会渲染出 `825` 这种鬼标签。要求与
`date.isoformat()` 产出**精确回环**。

## codex 第十七轮：一个 P2 —— 硬链接不经过 O_NOFOLLOW

派生位被硬链到 canonical 输入或别的 provider 的台账时，两个名字指**同一个
inode**——路径比较看不见、O_NOFOLLOW 不管（那只管符号链接）。对**已打开的
描述符** fstat：查的就是拿到的 inode，没有 check-then-use 窗口；st_nlink > 1
拒写 + ERROR，退出码照旧。NTFS 建硬链接无需特权，行为测试本机真跑（与
symlink 测试不同，不 skip）。

## codex 第十六轮：一个 P2 —— 先查再开是竞态，打开动作自己要拒随

上一轮的 `is_symlink()` 前置检查与 `open("ab")` 是两次文件系统操作——中间
路径可以被换成链接（check-then-use）。改为 os.open 带 `O_NOFOLLOW`（POSIX
上遇链接**原子地** ELOOP 失败）+ fdopen("ab")；Windows 没有 O_NOFOLLOW
（getattr 得 0），保留前置检查为主防线——那里创建符号链接本就需要特权，
残余竞态窗口如实记录。append-only 守卫随新结构重钉：fdopen mode 只许 "ab"、
Path.open 只许 "rb"、三个承重 flag（O_APPEND/O_NOFOLLOW/O_CREAT）逐一钉住。

## codex 第十五轮：两个 P2 —— 派生位上的符号链接、我自己造的规格矛盾

**symlink 拒随。** B 名下的台账名被链到 A 的台账时，`open("ab")` 跟随链接、
B 的记录直接写进 A 的历史——构造期检查解析的是各自配置的路径，看不见事后落
在派生位上的链接。`_append_ledger`（可观测性层）开头查 `is_symlink()`，拒绝
并记 ERROR，退出码照旧；阶段语义零改动。

**两份 delta 打架，是我上一轮写出来的。** run-center delta 说「状态戳不等→
unknown」，主 delta 与实现说「属于边界那次运行、只是不属于展示的记录」。
取实现语义（归属仍确定，身份披露不同）对齐 run-center delta——一个语义，
只说一遍。

## codex 第十四轮：一个 P2 —— 非归一化身份是坏行，不是别人的

写入侧 `_norm` 只产归一化绝对路径；`provider_dir: "../bundle"` 过了非空检查
后被 `_describes` 判成「别人的」——掩盖台账损坏。验它是 normcase∘normpath
的不动点且为绝对路径，不是就计 malformed（照抄「foreign 只配给完整合法记录」
那条既立纪律，把身份形态也纳入「合法」的定义）。

## codex 第十三轮：一个 P2 —— design.md 里同根陈述的第三处

上上轮改了 proposal.md、上轮改了 v2-run-center-page delta，而 design.md
「台账为什么不给 CLI 覆盖」一节还写着「那一整类风险在这里不存在」——第三处
同根陈述。这次不再凭记忆找文件：**对整个 change 目录做了穷尽 grep**
（攻击面/改道/无从/不存在/找得到边界），确认除历史记录外这是最后一处；按
终态重写（派生只封正向；反向碰撞面由命名空间保留守住，守卫不可当多余删）。

## codex 第十二轮：一个 P2 + 一个 P2 —— 时间戳要解析得动、第二份 delta 也要对齐

**非空还不够，得解析得动。** `run_date: "foobar"` 配胡话时间戳通过非空检查，
被渲染成一次「真实」运行。按写入侧的产出验：ISO 日期、带时区的 ISO 时间戳、
结束不早于开始；验不过就是坏行。

**v2-run-center-page 那份 delta 漏对齐了。** 上一轮改了 design.md 与
v2-daily-data-update，而同一 change 里的第二份 delta 还写着「找到本 provider
边界即确定」。按终态重写（三前提 + 真原因 + 外来边界在场即败）。教训并入
[规格自相矛盾防线]：**改完要 grep 的是整个 change 目录，不是记忆里想到的
那几个文件**。

## codex 第十一轮：两个 P2 —— 文档与终态实现的矛盾（防「按旧文档回退」）

design.md 还写着「窗口里找得到边界→归属确定」——那是被评审连打三层之前的
初版判据；proposal.md 还声称「台账无 CLI 覆盖 ⇒ 改道攻击面根本不存在」——
而终态在 __post_init__ 里为**另一头**的改道加了整套命名空间保留。两份文档
按终态重写：归属=完整窗口+独占+状态戳相等三前提；攻击面=「不提供覆盖只封住
一个方向」。这正是[规格自相矛盾防线]要防的：留着旧论述，未来的改动会拿它
当依据把守卫当多余删掉。

## codex 第十轮：两个 P2 —— 连败数长出诚实边界

**坏行是屏障。** 它可能是一次成功——丢掉再数会把断开的两段焊成一段，
「连续 2 次失败」可能根本不连续。**截断是下界。** 8 连败与 7 连败在截到
7 条的视图里长得一样，报「正好 7」低估了这份台账要暴露的模式。
`consecutive_failures` 改返回 `FailureStreak(count, exact, blocked)`：撞到
成功或数完整份台账 = 精确；数到坏行或截断 = **至少** count；最新一行就读不
了 = 整体不可断。页面三态分别措辞。

**守卫自身又栽一次「断言被别处满足」**（本会话同类第 4 次，变异 BC 抓到）：
「页面有『至少』措辞」按词断言被 count==1 分支满足、真空绿。重锚到承载机制
的精确表达式（qualifier 三元式）。规则升级：源码级守卫先问「把机制删掉，
断言还能被别处满足吗」。

## codex 第九轮：一个 P2 —— 保留检查按路径组件做，不只 basename

`--status-path <台账名>/status.json` 的叶子是无辜的 status.json——basename
检查放行；而写状态要先 mkdir 出台账那个名字的**目录**，随后 _append_ledger
撞 IsADirectoryError 被（按反向耦合契约）吞掉，运行永远进不了历史。保留
检查改为查**每一段**路径组件；变异（退回 basename）咬住。

## codex 第八轮：一个 P2 —— 可变根路径也入保留名单

命名空间保留上一轮漏了两个头：--provider-dir 与 --tushare-dir。B 把
provider 根指到 A 的台账上，B 自己的派生台账只是后缀出现两次、相等检查看不
见——而一次成功重建后 swap() 会把 A 的台账 rename 成 .bak 再整个换掉。
tushare 根同理（fetch 直写其下）。两行入名单，守卫 subTest 从三字段扩到五。

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

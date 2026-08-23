# Delta for v2-run-center-page

## MODIFIED Requirements

### Requirement: 数据更新手动启动为 detached 子进程

页面 SHALL 经独立模块 `web/operator_ui/update_runner.py` 启动
`scripts/daily_update.py` 子进程；页面自身 MUST NOT import `subprocess`。
操作人**未指定抓取范围**时，argv SHALL 与调度器（`run_daily_update.bat`）
**逐字相同**：`--tushare-dir <provider 父目录/tushare_raw> --provider-dir
<provider> --delisted-registry <registry> --reference-cases
<仓库>/tests/pit/reference_cases.yaml --start-date 20180101`，首两元素为
`<python 解释器>` 与仓库布局推导的脚本绝对路径。

页面 SHALL 允许操作人显式指定开始与结束日期；给出时 argv SHALL 只在这两处
不同，SHALL NOT 一并带入任何其他参数。页面 SHALL 把即将执行的 argv **本身**
呈现给操作人，而不是另抄一份措辞，使偏离永远可见。

范围此前是写死的。2026-08-17 / 08-20 / 08-21 连续三晚失败，原因是一次收盘前的
更宽范围抓取把 fetch manifest 撑到 20151001 并记下未解决的洞，此后每次按缺省
下限跑都被 manifest 的范围守卫拒绝——它拒绝缩窄合并，因为更窄的范围不会重试
范围外的洞。01 给出的修法是「按完整范围重跑」，而这个页面做不到。

「镜像调度器」这条不变式没有被推翻，而是被写准了：缺省仍逐字相同，偏离只能
来自操作人的显式输入，且显示的就是要跑的那一组。

子进程 SHALL detached 于 UI 会话（Windows：`CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW`；POSIX：`start_new_session=True`），`stdin=DEVNULL`，
stdout/stderr 追加写入调度器同一条日志流（`<provider 父目录>/logs/
daily_update.log`），追加前 SHALL 写入一行携带完整日期时间的
「launch attempt」标记（既有日志行仅有时分秒；措辞 MUST NOT 宣称进程
已存在——标记先于 `Popen`），`Popen` 失败路径 SHALL 追加失败标记，
避免调度器后续输出被误归因到未发生的 UI 运行；子进程 env SHALL 经
`utf8_child_env()` 钉 UTF-8。

启动前 SHALL 预检并 fail-loud 拒绝（不启动进程）：(a) argv 三路径
（provider/tushare/registry）任一不是绝对路径——空串会被解析成当前
工作目录，异约定拼写会把数据落到别处；(b) 子进程将继承的 env 中
`TUSHARE_TOKEN` 缺失或为空白；(c) 状态工件属于该 provider 且
state=running 且按 reader 语义分类为新鲜；(d) 抓取范围任一端不是 8 位
ASCII 数字的 YYYYMMDD、不是真实日期，或两端的**生效值**颠倒——留空的开始
取缺省下限、留空的结束取运行日，只比字面输入会放过一个颠倒的区间。
编排器与 01 对日期格式零校验，畸形值一路流到 tushare 那头才炸，而那已经是
一次约两小时运行的中途。预检 (c) 是 advisory——并发的
权威仲裁是 `daily_update` 自身的单飞锁（撞锁 exit 17 落日志）；runner
MUST NOT 触碰锁文件。

`launched` 结果仅表示进程已创建（携 pid 与日志路径），MUST NOT 被呈现为
「更新成功」——成败由状态工件与日志承载。

#### Scenario: 非绝对路径拒绝启动

- **GIVEN** 三路径中任一为空串（解析为 `.`）或相对/异约定拼写
- **WHEN** 操作人点击启动
- **THEN** 返回 `unusable_path` 并指名是哪个旗标，进程未被创建

#### Scenario: token 缺失拒绝启动

- **GIVEN** 子进程将继承的 env 无 `TUSHARE_TOKEN`（或仅空白）
- **WHEN** 操作人点击启动
- **THEN** 返回 `no_token`，进程未被创建，页面展示修复指引

#### Scenario: 正在运行时拒绝重复启动

- **GIVEN** 状态工件属于该 provider、state=running 且新鲜
- **WHEN** 操作人点击启动
- **THEN** 返回 `already_running`，进程未被创建
- **AND** 陈旧 running 记录或外来 provider 的记录不触发此拒绝
  （交由单飞锁权威仲裁）

#### Scenario: 成功启动只报告事实

- **WHEN** 预检通过且 `Popen` 成功
- **THEN** 返回 `launched`，携 pid 与日志路径
- **AND** 页面文案不将其呈现为更新成功

#### Scenario: 启动失败响亮

- **WHEN** 日志目录不可创建或解释器无法启动（OSError）
- **THEN** 返回 `launch_failed` 并携原因，绝不静默

#### Scenario: 不指定范围时 argv 与调度器逐字相同
- **WHEN** 操作人不改动范围就启动
- **THEN** argv 与本改动之前的完全一致，既不含 `--end-date`，开始日期也仍是
  调度器的那个下限

#### Scenario: 显式范围只改范围
- **WHEN** 操作人给出开始与结束日期
- **THEN** argv 只在这两处不同，没有任何其他参数被一并带入

#### Scenario: 畸形日期在按钮之前被拒
- **WHEN** 某一端不是 8 位 ASCII 数字的 YYYYMMDD，或是 8 位数字但并非真实日期
- **THEN** 页面拒绝启动，启动器在被直接调用时同样拒绝且不创建任何进程

全角数字是这条的具体来源：`\d` 与 `int()` 都收 Unicode 数字，而随后的顺序比较
按字典序比原串，全角码位远在 ASCII 之上——一个数值上更早的结束日期会被判成更
晚，颠倒的区间就这样交给了子进程。

#### Scenario: 留空的一端按生效值参与顺序判定
- **WHEN** 某一端留空，而它的生效值（缺省下限或运行日）使区间颠倒
- **THEN** 范围仍被拒绝，并指出那一端生效的是哪个日期

#### Scenario: 呈现的参数就是将要执行的参数
- **WHEN** 页面展示启动参数
- **THEN** 展示的内容由构建 argv 的同一次调用派生，且预览与启动读取同一组输入

## ADDED Requirements

### Requirement: 交易日历闸会让本次运行空转时 SHALL 在启动前预警

页面 SHALL 在交易日历闸会使本次运行 no-op 时于启动前给出预警，SHALL 复现该闸
的完整条件而不是其中一项，并 SHALL 只预警不拦截。判定 SHALL 与编排器逐点一致，
由测试穷尽比对；UI SHALL NOT 判得比该闸更宽。

日历闸 no-op 时以 **exit 0** 结束，状态工件记成一次成功，而实际不抓取、不重建、
不切换。一次周末补跑因此看起来成功、实则什么都没做。

no-op 是三个条件的合取：非交易日、未指定结束日期、且存在可用的 live bundle。
只复现头一项，会在「没有 live bundle、闸放行去 bootstrap」时预警一件不会发生
的事。

第三项看的是**修复之后**的状态：编排器在日历闸之前先跑 `check_and_repair`，
live 目录不在而 `.bak` 还在时它会恢复（`.bak` 与 `.new` 都在时完成那次中断的
切换），修完照样 no-op。只看修复前的状态，恰恰会在这个恢复序列上漏报。

该闸刻意只把周末当非交易日；工作日节假日走正常流程，由 fetch 的新鲜度闸优雅
no-op。UI 若「顺手」加上节假日，就与闸不一致，同样是在预警不会发生的事。

`update_runner` 与编排器的唯一耦合是 CLI 进程边界，不得 import 编排器，所以这
两个判据只能在 UI 侧重述；重述必然有漂移风险，因此以穷尽等价的测试钉住。

#### Scenario: 周末且未指定结束日期时给出预警
- **WHEN** 运行日是周六或周日、结束日期为空、且 provider 下存在可用 bundle
- **THEN** 页面预警本次会 no-op 并 exit 0、工件会记成成功，并指出填写结束日期
  即可绕过

#### Scenario: 指定了结束日期就不预警
- **WHEN** 操作人填了结束日期
- **THEN** 不预警，因为该闸此时不生效

#### Scenario: 没有可用 bundle 时不预警
- **WHEN** provider 下的 bundle 骨架不完整，且没有能被修复恢复成可用 bundle 的
  兄弟目录
- **THEN** 不预警，因为该闸此时放行去 bootstrap

#### Scenario: 能被修复恢复的状态仍要预警
- **WHEN** live 目录不存在，但 `.bak`（或 `.bak` 与 `.new`）能让启动修复得到一个
  可用 bundle
- **THEN** 仍然预警，因为修复发生在日历闸之前，闸随后照样 no-op

#### Scenario: 预警不夺走操作人的决定
- **WHEN** 预警出现
- **THEN** 启动按钮仍可点击，因为 no-op 无害而操作人可能正是要它

#### Scenario: 重述与编排器逐点一致
- **WHEN** 任一侧的判据被改动
- **THEN** 穷尽比对的测试失败

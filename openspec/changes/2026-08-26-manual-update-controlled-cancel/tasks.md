# Tasks: 2026-08-26-manual-update-controlled-cancel

## 实现

- [x] `UpdateLaunch.process`：launched 分支携带活 `Popen` 句柄（取消的
      唯一凭据；frozen dataclass 仅作传递）
- [x] `cancel_update(process, log_path, grace_seconds)`：三态结局
      （already_finished no-op 不落标记 / cancelled 带 graceful 与
      returncode / cancel_failed 带原因）；POSIX killpg SIGINT 先礼后兵，
      Windows 如实硬杀（实测依据入注释与提案）
- [x] 取消动作落 `[run_center]` 带日期标记（请求+结局），复用 launch
      归属惯例
- [x] run_center：会话句柄区（poll 退役）+ 两步确认（武装→确认/保留）+
      三态结局如实措辞（含「状态工件仍标 running」的硬杀标注）

## 验证（每条要实测数字）

- [x] 平台送达性实证（写码前）：NEW_GROUP|NO_WINDOW 下 CTRL_BREAK 发送
      成功但子进程纹丝不动；仅 NEW_GROUP 时送达但 rc=0xC000013A、无
      KeyboardInterrupt——Windows 优雅路径为假，设计按实测落
- [x] 守卫 8 条（no-op 不落标记/活进程取消+双标记/Windows graceful 必
      False/POSIX SIGINT 真送达且判 graceful（CI Linux 腿直跑，本机
      skip）/签名钉 Popen/禁 pid 杀入口/launch 带句柄/页面两步+会话域+
      三态措辞）
- [x] 变异两发（去 already_finished 短路 1f/去取消标记 1f）全咬
- [x] 定向 7 passed / 1 skipped（POSIX 腿）；logic 全量见 PR 实测数字
- [x] openspec validate --strict valid

## codex 第一轮（2×P1 + 1×P2）

- [x] P1 切换窗：取消可落在 swap 两段 rename 之间（canonical 暂缺,下次
      启动修复复原——swap 基线本就只承诺 crash-atomicity + 事后修复）。
      cancel_update 退出后做 canonical 存在性检查（纯文件系统,不 import
      管线层）,命中即 swap_interrupted=True + SWAP WINDOW 标记 + 页面响
      亮指引立即重跑;确认措辞与规格改口,不再说「在线数据不受影响」的
      绝对句。变异去检测 1f 咬住
- [x] P1 证据跨 rerun：硬杀留下的 running 若只在首个渲染更正,一次 rerun
      就退回「正在更新」并锁启动闸到六小时线。持久证据键按状态戳**精确
      相等**绑定被取消那一次（cancelled_run_matches 纯 helper,真值表
      钉）,running 分支专属措辞+启动闸解锁,状态被接替即退役。变异松相
      等 3f 咬住
- [x] P2 失败保句柄：cancel_failed 时进程可能还活着——句柄是唯一合法取
      消凭据,只有确认终局（cancelled/already_finished）才交出

## codex 第二轮（2×P1 + 1×P2）

- [x] P1 假解锁：页面解锁按钮而 launch 内部状态闸仍按 fresh running 拒绝
      ——切换窗后被指引的「立即重跑」一直 already_running 到六小时线。
      闸新增 cancelled_started_at 旁路,只放行**戳完全相等**那一条（单飞
      锁仍是真仲裁）;页面把证据递进闸。变异去放行 1f 咬住
- [x] P1 证据旧戳：子进程可能在页首读取之后才写 running 记录,页首快照
      存证据=旧戳,下一轮精确匹配落空、证据被退役、孤儿照样锁页。改为
      终止后**重读**状态工件、验 running+属主才落证据;页面钉四处
- [x] P2 bootstrap 误诊：canonical 裸缺位不是切换窗签名——首次
      bootstrap 本来就没有 live bundle。签名收紧为「canonical 缺 且
      .bak 在」（镜像 bundle_swap 命名,web/ 不 import 管线层）;
      bootstrap 缺位用例专防误诊。变异去签名 2f 咬住

## codex 第三轮（2×P2）

- [x] P2 二次启动顶句柄：子进程写 running 前的窗口里按钮仍可点——第二次
      launch 会顶掉第一个句柄（第二子进程通常 exit 17 即退、句柄退役），
      原运行失去唯一取消凭据。双闸：按钮 disabled 纳入会话在飞布尔 +
      点击时兜底拒绝
- [x] P2 graceful 越权声明：POSIX 礼貌信号同样可能落在切换窗内——
      graceful 成功文案的「在线数据未受影响」以 swap_interrupted 为条件，
      与硬杀分支同款
- [x] 既有「launch 闸」守卫锚串随表达式增强更新（断言意图一字未动，
      freshness 闸仍在耗）

## codex 第四轮（2×P2）

- [x] P2 标记失败透出：日志启动后变不可写时,静默吞掉=操作不可审计。
      _append_cancel_marker 回报落盘成败,UpdateCancel.markers_written 聚
      合,页面响亮警告;终止本身照常（主结局优先）。变异吞失败 1f 咬住
- [x] P2 硬杀消息按重读条件化：杀在「launch 到状态落盘」窗口里=无孤儿可
      更正——「将持续标注/已解锁」只在证据真落盘时声称;无记录情形如实
      分支（needle 跨源码换行的断言坑顺带修正）

## codex 第五轮（P1 + 2×P2）

- [x] P1 收养错运行：锁随进程消亡即释放,调度器可在「杀后、重读前」起跑
      ——按 provider+kind 收养会把活运行标成已取消并解锁双闸。证据改为
      **时间绑定**（launch ≤ started_at ≤ killed,纯 helper 真值表钉:
      接替者/旧记录/解析不动/缺时区全拒）;变异松恒真 2f 咬住
- [x] P2 失败结局审计：两个 cancel_failed 返回落在标记聚合之外——失败的
      取消静默无审计。聚合补齐,页面警告覆盖 cancelled+cancel_failed;
      kill 即抛存根用例
- [x] P2 unknown 第三态：检查自身抛 OSError 不许当健康——swap_state_
      unknown 透出,graceful 文案在未核实时不声称数据无恙,页面「无法
      核实请人工确认」警告;变异当健康 1f 咬住

## codex 第六轮（2×P1：时间界收紧到贴边）

- [x] 下界在**派生调用之前**采样——子进程可在派生返回后、页面记时前写下
      running,晚采下界把真被杀的运行判成旧记录拒绑（源码序钉：前采行
      先于 launch 调用）
- [x] 上界由取消边界在**确认死亡当刻**返回（UpdateCancel.exited_at）——
      死亡确认后还要写标记/查文件系统,调度器可在那段接锁写接替记录,
      页面晚采会框进窗。区间断言 + 变异去采样 1f 咬住
- [x] 零 spawn 守卫再次咬中注释字面,改述（本页零派生）

## codex 第七轮（2×P2）

- [x] P2 探测吞错：Path.exists() 把权限/IO 类 OSError 吞成 False——两次
      失败探测拼成健康非切换态,graceful 又声称数据无恙。改严格 stat：
      FileNotFoundError=确证不在,其它 OSError 上抛入 unknown 分支;变异
      退回吞错 1f 咬住
- [x] P2 审计警告困在 cancelled 分支：cancel_failed 的标记缺失永远渲染
      不到。三个警告外提到共同作用域（swap 两条只在 cancelled 为真,共同
      层无害）;缩进级 needle 钉共同作用域

## codex 第八轮（P2：迟到死亡补结算）

- [x] cancel_failed 返回后进程才真死——退役块当自然完成丢句柄,孤儿
      running 无证据锁页。失败时留未决上下文（上界=请求时刻:被杀那次的
      started_at 必早于请求,接替者必晚于真实死亡>请求,不误收养）;退役
      块凭它补结算证据后再退役

## codex 第九轮（3×P2：收养上身份、补结算全款收尾、watcher 盯句柄）

- [x] P2c 收养证据加**进程身份**硬条件——调度器可在 launch 之后、UI 子
      进程拿锁之前起跑并夺锁,其 started_at 恰落窗内,被杀的 UI 子进程只
      是 exit-17 输家,纯时间窗会把**活着的**调度器运行标成已取消。修在
      不变式层杀整族：产出器把 os.getpid() 落进每条状态记录（launch 直
      接 spawn 编排器无 shell 壳,Popen.pid 即写者）;读侧解析 pid（缺=
      None 不算截断,在场必须正 int 否则 corrupt——true 会 True==1 误绑,
      字符串会静默永不绑）;绑定 helper 要求 record_pid == killed_pid
      （排 bool）,时间窗降为防 pid 复用的副防线;两处收养（confirm+补结
      算）都递 pid。真值表 + 产出器 mid-run 断言 + 变异去 pid 检查/去产
      出器落 pid 各自咬住
- [x] P2b 迟到补结算欠**全款收尾**——此前只重读状态、存证据,不做切换窗
      检查:迟到死亡同样可能落在两段 rename 之间,不查就把「canonical 缺
      位需立即修复」静默标成干净取消。收尾抽成共享实现
      `_confirmed_death_outcome`（结局标记+严格 swap 检查+unknown 三态）,
      当场路径与新入口 `settle_late_cancel`（活进程 fail-loud 拒绝）共
      用;补结算结局进 _LAST_CANCEL_KEY 走同一套渲染（三警告消费处）,
      并当场 rerun 不带旧横幅渲染到底。行为用例（.bak 在→swap 命中+
      "exited late" 标记）+ 变异断开 provider_dir 咬住
- [x] P2a watcher 盯**未决取消句柄**——硬杀后状态签名与日志进度双冻结,
      只比那两样的片段永不开火,补结算要等手动交互,死进程的取消控件与
      孤儿 running 一直挂着。watching 纳入未决句柄;片段内句柄一死即整
      页 rerun 让补结算当场跑。页面钉 + fragment 体内 needle
- [x] 锚串两处随第九轮更新（_watching 表达式、补结算切片锚避开 confirm
      分支的「迟到死亡补结算的时间上界」字样）,断言意图一字未动

## codex 第十轮（3×P2：kill 已发才可补结算、审计缺口不许洗白、graceful 声称要核实）

- [x] P2 kill 未发的失败不许迟到补结算——kill() 抛了=进程没被碰过,之后
      自然跑完就是自然完成,补结算成「已强制取消」+落迟到标记+做 swap 诊
      断全是撒谎。UpdateCancel 增 `kill_issued`（OSError 路径 False,超时
      路径 True;POSIX 前置 killpg 走 suppress 送达证不出来,fail-closed
      记 False——宁可孤儿等陈旧线不把自然完成标成被杀）;页面未决上下文
      只在 kill_issued 时留。真值双存根 + 变异翻 False→True 咬住
- [x] P2 迟到补结算不得洗白原失败的审计缺口——请求/失败标记当时没落盘,
      日志恢复可写后迟到结局标记写成,审计链仍缺头两条。未决上下文携带
      `cancel_pending_markers_written`,settle_late_cancel 收种子聚合而非
      重置（缺键 fail-closed=False:证不出完整就不声称）。行为用例（种子
      False+迟到标记写成→仍 False）+ 变异硬编码 True 咬住
- [x] P2 graceful 的「编排器自己写下了终态记录」要**核实**不要从及时退
      出推断——SIGINT 可落在 import/解析配置/拿锁阶段,终录路径尚未就位,
      进程照样宽限窗内退出而工件缺失/陈旧/仍 running。新 helper
      `terminal_record_confirms_the_run`（finished 且写者 pid == 被杀句
      柄 pid,复用第九轮身份判据;None/无 pid/running/损坏全 False）;
      UpdateCancel 增 `terminal_recorded`（硬杀恒 False）;页面核实版才
      说终态已写（文案注明已核实）,未核实的 graceful 与硬杀同走孤儿收养
      （收养条件从 `not graceful` 改为 `not terminal_recorded`——SIGINT
      落在 running 写后/终录前同样留孤儿）,措辞如实区分。真值表 + POSIX
      腿负断言 + 变异去 pid 合取咬住
- [x] 锚串一处随第十轮更新（graceful 文案切片锚避开 _mode 字符串的裸词
      首现）,断言意图一字未动

## codex 第十一轮（2×P2：两个绑定谓词补成身份+时间对称完备）

- [x] P2 补结算上界**改判**——第八轮用请求时刻当上界的前提「被杀那次的
      started_at 必早于请求」在「spawn 之后、写 running 之前确认取消」
      的窗口里不成立:子进程可在请求之后才写出自己的记录,请求时刻上界
      把这条**真孤儿**拒之窗外、孤儿被当活运行锁页六小时。上界改为补结
      算入口采样的死亡观测时刻（_late_outcome.exited_at,采样先于状态重
      读——与当场路径同款「先定界再读」）;pid 身份是主判据（第九轮）,
      窗口只剩防 pid 复用一职,上界只须 ≥ 真实死亡。cancel_pending_at
      降为触发标记+审计戳。回归用例=请求后写出的记录在观测上界下能绑、
      在请求上界下被拒（第八轮错法的实证对照）;r8 的上界断言随之刻意
      改判（非锚串腾挪）
- [x] P2 终态核实补时间窗——纯 pid 会把复用同 pid 的**陈年** finished
      工件核实成本次终态（SIGINT 把新子进程杀在写任何状态之前）。
      terminal_record_confirms_the_run 增 launched_at/exited_at 窗:
      launch ≤ started ≤ finished ≤ exit,任一戳缺失/解析不动/无时区
      fail-closed;launched_at 从页面会话透传进 cancel_update 与
      settle_late_cancel（缺=核实不成立）。真值表补陈年工件/窗界缺失/
      finished 越界/无时区四类;变异去窗（恒 True）咬住

## 既有守卫开火一处（处置=改我不削弱守卫）

- 页面源码禁现 spawn 字样的守卫咬了我的注释字面——注释改述（「活进程
  句柄」+指明零 spawn），守卫一字未动

## 刻意不做（勿在评审中重开）

- 不取消调度器自动运行（无句柄、越权）
- 不加协作式取消哨兵（触 `_execute_daily_update` 阶段语义红线）
- 不按 pid 杀、不跨 UI 重启恢复取消能力（句柄只活在会话内存，如实降级）
- 不由 UI 伪造状态工件终态（写者纪律：只有编排器写）

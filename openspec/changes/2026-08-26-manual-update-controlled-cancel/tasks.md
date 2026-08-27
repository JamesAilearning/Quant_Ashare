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

## codex 第十二轮（P2：迟到收养身份改为生存期内观察的精确戳候选）

- [x] P2 死亡观测上界仍留 pid 回收窗——watcher 观测可晚于真实死亡最长
      一个轮询周期,OS 可在空窗内把 pid 回收给接替的调度器运行:戳与
      pid 双双落窗,活运行被收养成已取消。三代取法的谱系:第八轮请求时
      刻上界**拒真孤儿**（子进程可在请求后才写记录）→第十一轮观测时刻
      上界**收接替者**（本轮）→终态=生存期内观察:新 helper
      `observe_own_running_record`（活→读→活 三步,pid 在两次 poll 之
      间被子进程持续持有不可能回收,读到的 pid==句柄 记录必然是它自己
      写的）;confirm 当刻 + watcher 每拍两处刷新候选入未决上下文;补结
      算只收养「戳与候选**精确相等** + 直接 pid 相等」,无候选
      fail-closed 不收养（孤儿等陈旧线,不把回收 pid 的接替者标成已取
      消）。evidence_binds_to_killed_run 保留给当场路径（exited_at 在
      死亡确认当刻采样,无观测空窗）。真值四支（活+是它的→取到;死后/
      pid 不同/别的 provider→None）+ 两处刷新计数钉 + 变异去 pid 合取
      咬住;第八/十一轮的上界断言随之刻意改判并留谱系注释

## codex 第十三轮（P2：补结算移到 watcher 片段注册之前）

- [x] P2 死句柄 rerun 循环——fragment 在每次**整页执行**时也内联运行,
      死句柄支路的 st.rerun 在走到它之后任何代码之前中止本轮;补结算块
      原在片段之后,下一轮又先撞片段:无限 rerun、补结算永不执行、句柄
      永不退役（await 支路不循环正因其解除条件在片段之前重算）。修=退
      役/补结算块整体上移到 `_session_live` 读取之后、片段注册之前;原
      取消控件区只留「本轮途中才死→收起控件,下一轮顶部块接手」的轻量
      守卫,结算义务单一归属。守卫按 codex 要求钉**执行顺序**而非仅钉
      轮询源存在:锚唯一性 + 补结算块 < 片段注册 < 控件区 + 补结算入口
      恰好一处

## codex 第十四轮（P2：already_finished 撞未决 kill 改走完整补结算）

- [x] P2 重试竞态——先前 cancel_failed 已发 kill,操作人重试,进程恰在
      「顶部结算检查之后、cancel_update 初检之前」死掉:already_finished
      的 no-op 语义把句柄和未决上下文一起丢弃,迟到收尾（结局标记/swap
      诊断/孤儿证据）整个跳过——孤儿锁页六小时、切换窗命中无人报。
      修=补结算抽成页面共享函数 `_settle_late_pending`（单一实现,末尾
      整页重绘不返回）,顶部退役块与 confirm 分支的 already_finished×
      未决拦截共用;no-op 语义收窄为「无未决取消的自然结束」（规格同步
      carve-out,防场景 vs 正文矛盾）。守卫:调用点恰好两处 + 拦截切片
      三钉（结局落盘之前/验未决/走共享函数）

## codex 第十五轮（P2：审计缺失跨重试聚合）

- [x] P2 重试洗白审计缺口——首次 kill 超时且标记写失败（未决上下文存
      False）,日志恢复可写、重试成功终止:只报本次 True 会把先前那次活
      取消的审计缺口静默洗掉;再次超时也会用本次 True 覆盖存量 False。
      修在取消边界单点:cancel_update 收 `prior_markers_written` 种子,
      请求标记聚合起步（`and (prior is not False)`）,所有返回路径
      （cancelled/两个 cancel_failed）自动携带;页面 confirm 分支从未决
      上下文透传。警告消费的是**完整审计链**状态。行为三支（prior=False
      重试成功→False 且本次标记确实写成;None/True 不误伤;再超时不洗
      白）+ 页面接线钉 + 变异去聚合咬住

## codex 第十六轮（P2：kill 未发的重试也要把审计缺失写回未决）

- [x] P2 未决在场（prior=True）+ 重试的 kill() 自身抛且标记写失败——
      kill_issued 守卫拦住未决更新,存量 True 不动;进程随后死于**先前**
      的 kill,迟到结算从陈旧 True 起步、报完整审计链,本次缺失的请求/
      失败标记被抹掉。修=confirm 分支新增非 kill_issued 的 cancel_failed
      支路:未决在场时把聚合值（cancel_update 已按 prior 种子聚合,False
      不会被洗回）写回 `cancel_pending_markers_written`;不动
      `cancel_pending_at`——本次没发 kill,未决身份仍属先前那次。真值
      （prior True + 本次标记失败 → 聚合 False）+ 页面切片三钉（验未决
      在场/写回聚合值/不新立未决身份）

## codex 第十七轮（2×P2：组信号成功=已发、候选取消前先观察）

- [x] P2 POSIX killpg 成功返回是「已发出」的证明（内核受理投递）——此
      前 suppress 后一律当证不出,后备 process.kill() 恰与已发 SIGKILL
      生效的退出竞态抛 OSError 时硬编码 kill_issued=False:首次取消不留
      未决,先前 SIGKILL 导致的迟到死亡被当自然完成,整套迟到收尾跳过。
      修=suppress 改 try/else 记 signal_issued（SIGINT/SIGKILL 任一成功
      即 True）,OSError 返回路径带 signal_issued;只有**所有**信号调用
      都抛才算没发。POSIX-only 用例（mock killpg 成功+kill 抛→True;
      全抛→False）——本机 win32 skip,裁判是 CI ubuntu 腿
- [x] P2 生存期候选在**取消调用之前**先观察一次——取消调用可耗满宽限
      窗,kill 恰在返回后生效,确认后的唯一观察撞死进程返回 None:已写
      出 running 的真孤儿无候选被拒收养、锁页六小时。修=confirm 分支
      在请求时刻之前 `_own_before` 预观察（此刻进程没被碰,必然在生存
      期内;记录戳写出后不变,前后观察若都成功必然同值）,失败分支
      `_own_now or _own_before` 兜底。候选观察点 2→3 处（取消前/失败
      后/watcher）,计数钉+源码序钉+兜底钉

## codex 第十八轮（P2：无信号送达的死亡不得报成已取消）

- [x] P2 POSIX「初检后自然完成 + SIGINT 抛错」竞态——异常路径照进宽限
      窗,下一次 poll 把自然死亡判成 graceful,套上取消专属的 swap/审计
      收尾与 graceful 文案。修=统一**分类闸**:kill() 成功返回也计入
      signal_issued;确认死亡前 `not signal_issued` 一律按自然完成
      （already_finished + 结局标记收口已落的请求标记,kill_issued=
      False）——两平台同一竞态窗（Windows=初检到 kill() 的毫秒窗）一
      起封;graceful 从此蕴含 SIGINT 已发。页面标记缺失警告扩到
      already_finished（自然竞态收场也写标记）。双平台确定性用例
      （poll 序列存根 + POSIX mock killpg 抛）+ 变异去闸咬住;r7 警告
      锚串随扩展更新,断言意图不变

## codex 第十九轮（P2：kill 撞尸体要复检再分类）

- [x] P2 poll 到 kill 之间的窄竞态——进程恰在预检后退出,kill() 对已终
      结句柄抛 OSError:立即返回 cancel_failed 会留死句柄、把自然完成报
      成取消失败,还绕过第十八轮的分类闸。修=except 分支**复检** poll:
      仍活=真失败照旧（FAILED 标记+cancel_failed）;已死=落到分类闸
      （无信号→already_finished,有信号→确认死亡收尾）,等死挪进 kill
      成功的 else（抛错+已死无可等）。双平台确定性用例
      （_DiesUnderKill:kill 置旗再抛,poll 依旗翻转;POSIX 两变体=killpg
      全抛→自然完成、killpg 成功→cancelled）+ 断言不落 FAILED 标记 +
      变异去复检咬住

## codex 第二十轮（P2：kill 静默返回不是送达证据——观察空间四分收口）

- [x] P2 事实性纠错——CPython Popen.kill()→send_signal() 内部先 poll,
      进程已终结时**什么都不发、静默正常返回**（Windows terminate 同款
      吞 PermissionError）;第十九轮的 _DiesUnderKill 存根假设抛错,与真
      实 Popen 不符。修=返回后复检:仍活=真送达（内部 poll 见活进程,
      os.kill 已执行）;已死+无信号在先=歧义微秒窗,用终态 oracle
      （terminal_record_confirms_the_run,复用第九/十一轮 pid+窗判据）
      裁决——终录在=自然完成,不在=按已发信号的确认死亡走（无终录自然
      猝死的孤儿收养收尾对它同样正确）。kill_issued 字段证据分级更新。
      相对时刻构造工件戳（吸取 launch 闸炸弹教训）;变异「返回即当送
      达」咬住
- [x] **死亡分类族划线**（r10→r17→r18→r19→r20 五轮）:本轮后 kill 调用
      观察空间 {抛错,返回}×{仍活,已死} 四分完备,每格显式归类、无一格
      靠调用语义假设;歧义格由终态 oracle 裁决而非新增探测器。该族若再
      出意见,按 #463 式终轮划线呈用户裁决,不再逐洞精修

## codex 第二十一轮（P2：显式 "pid": null 是损坏不是 legacy 缺省）

- [x] P2 读侧「在场」要按**键**判不按值——`.get("pid")` 把显式
      `"pid": null` 与旧记录缺键混同,畸形记录被放行成合法 legacy:硬取
      消后证据绑不上它（pid=None fail-closed）,页面误称无匹配 running、
      锁启动到六小时陈旧线。修=`"pid" in payload` 分支内验型,显式 null
      落进 corrupt(错误文案注明「显式 null 不等于缺省」);缺键照旧
      None=合法 legacy。回归=malformed 集合加 None(json 写出即
      "pid": null);变异退回 .get 咬住(SUBFAILED pid=None)

## codex 第二十二轮（P2：解锁声称以本帧证据覆盖为条件）

- [x] P2 证据落盘后、宣告渲染前被调度器接替——顶部逻辑已退役证据、恢复
      _running_fresh,而 _LAST_CANCEL_KEY 里历史的 evidence_stored=True
      仍让文案念「将持续标注/已解锁」,与同一帧上方的「正在运行」+禁用
      按钮自相矛盾。修=成功文案条件收窄为 `evidence_stored and
      _cancelled_this_run`（本帧判定）;退役情形新增如实改口分支（证据
      曾落盘、已按纪律退役、以当前状态为准）。守卫:条件版分支存在 +
      改口分支存在 + elif 链先窄后宽源码序钉

## codex 第二十三轮（2×P2：读失败不退证据、边界内补候选观察）

- [x] P2 corrupt/missing 是读取失败不是接替证明——瞬时卷/权限失效让
      _cancelled_this_run 为假,顶部逻辑借它把证据永久清掉;访问恢复后
      同一条孤儿 running 复现,被当活运行锁页六小时。修=退役判定抽纯
      helper `evidence_retires`:只认确凿接替（戳不同的 running / finished
      终态）,missing/corrupt 保留证据（它只在匹配 running 出现时生效,
      留着无害）。真值五支 + 变异（missing/corrupt 也退役）咬住
- [x] P2 「记录写在取消前观察之后、进程死在边界返回与事后观察之间」的
      双落空——两端候选观察都取不到,真孤儿被拒收养。修=cancel_failed
      的两条活进程路径（TimeoutExpired 证明活着/kill 抛后复检仍活）在
      **边界内**补一次 observe_own_running_record,随 UpdateCancel 新字段
      own_running_stamp 带回;页面候选链改三级兜底（事后观察→边界捕获→
      取消前观察,多次成功必同戳）。行为用例（Survivor+在场记录→捕获;
      无 provider→None）+ 页面链钉 + 变异（去边界观察）咬住

## codex 第二十四轮（P2：launch nonce——收养身份的构造性收口）

- [x] P2 codex 自己点破要害:边界观察「只是挪动竞态,不是封闭」——任何
      有限次生存期观察都留下「最后观察→死亡」尾窗,记录写在尾窗里时三
      级候选（取消前/边界内/事后）全空,真孤儿被拒。构造性终态=身份随
      记录本体落盘:launcher spawn 前生成 uuid4().hex 一次性 nonce、经
      LAUNCH_NONCE_ENV 注入子进程环境;编排器验形（32 位小写 hex,环境
      垃圾不落）后写进每条状态记录;读侧解析（在场必须合形否则
      corrupt,缺=None legacy）;两处收养（confirm+补结算）主判据=
      record_bears_launch_nonce（覆盖生存期任意时刻的记录、构造性免疫
      pid 复用;别人的 nonce 直接拒）,无 nonce 的 legacy 记录保留
      pid+时间窗/精确候选回退链。合成回归=「观察→写记录→死亡→返回」
      三级候选全空时 nonce 仍认领 + 产出器双记录落 nonce/垃圾不落 +
      读者真值 + spawn 前注入源码序钉 + 镜像钉;变异（nonce 判定恒假）
      咬住
- [x] **收养身份族硬线**（r8/9/11/12/17/23/24 七轮谱系收口）:nonce 是
      构造性封闭——身份由子进程写进它产出的每条记录,不存在观察窗,
      观察式候选降为 legacy 回退。该族再出意见按 #463 式终轮划线呈用户
      裁决,不再逐洞精修

## codex 第二十五轮（P2：nonce 证据不依赖死后读取,不确凿读取先落证据）

- [x] P2 r23 的姊妹缺陷——补结算/confirm 的死后读取撞 corrupt/missing
      时,未决上下文（含 nonce）被退役而证据还没落:孤儿恢复可读后被当
      活运行锁页六小时。nonce 使修法变优雅:被杀运行的身份**先验已知**
      （本会话自己的 launch nonce）,证据落盘不依赖那次读取——两处在不
      确凿读取时落 nonce-only 证据（戳空）;覆盖判定（页首）与启动闸
      （_blocking_run_status/launch_daily_update 新 kwarg
      cancelled_launch_nonce）同步收 nonce 放行,防 nonce-only 证据重演
      「按钮解锁、闸仍拒」假解锁（第二轮同款）;evidence_retires 升为双
      身份（nonce 覆盖优先,带本次 nonce 的孤儿绝不因戳空被误判接替者
      退役）。确凿读取的措辞条件不动（evidence_stored 只对确凿说话）。
      真值扩展 + 闸 nonce 放行三支 + 页面钉;变异首跑逃逸（elif False
      仍含子串）→ 锚收紧带 elif 前缀后咬住

## codex 第二十六轮（P2：身份在场一票裁决,or 语义收口）

- [x] P2 「nonce匹配 or 戳匹配」的 or 语义漏洞——粗粒度/冻结系统时钟可
      让接替记录与被取消记录**同戳**,异 nonce/无 nonce（调度器）的接替
      仍被判覆盖:活运行被标已取消、双闸解锁。修=抽单一谓词
      `evidence_covers_record`（任一侧带 nonce → 必须双方同 nonce;戳
      只留给双方都无 nonce 的 legacy 对）,页首覆盖判定/证据退役/启动闸
      三处共用不许分抄;收养处 legacy 回退同步收紧（`not _kill_nonce`/
      `not _kn`:本会话有 kill nonce 时子进程必然写 nonce,无 nonce 记录
      不可能是它）。r23/25 真值按新语义刻意改判（nonce 证据 vs 无 nonce
      同戳记录:覆盖→确凿接替）;真值全表 + 闸同戳异 nonce 不放行 +
      页面双钉;变异（退回 or 语义）双测咬住

## codex 第二十七轮（P2：graceful 终态核实同上 nonce 一票裁决）

- [x] P2 terminal_record_confirms_the_run 还停在 pid+时间窗——pid 复用
      + 冻结/粗粒度时钟可让陈年 finished 工件同时过 pid 与窗,
      terminal_recorded=True 使页面跳过孤儿证据链、谎称编排器写了核实
      终录。修=核实器同上第二十六轮语义:任一侧带 nonce → 必须双方同
      nonce（窗不再是判据——nonce 唯一,陈年/接替工件拿不到;冻结钟下
      本次运行的合法终录也不再被窗误拒）;legacy 双无 nonce 保留原
      pid+窗合取。launch_nonce 经 cancel_update→_confirmed_death_outcome
      穿线,页面 confirm 调用递会话 nonce（settle 传 None:graceful=False
      短路）。真值六支（同 nonce 窗内/窗外都认;异/无 nonce 同窗拒;
      反向 legacy 会话拒 nonce 记录）+ 页面接线钉;变异（去 nonce 分支
      退回 pid+窗）咬住

## codex 第二十八轮（P2：no-op kill oracle 转发 nonce——r27 集成回归）

- [x] P2 我第二十七轮的集成回归——r20「kill 静默返回」分支的终态
      oracle 调用没转发 launch_nonce:新语义下 UI 子进程带 nonce 的合法
      终录被「nonce vs None」拒掉,自然完成落进 else 被记 signal_issued
      =True、报成强制取消。修=调用点转发 cancel_update 的 launch_nonce;
      _DiesQuietly 回归补变体A'（会话带 nonce + 终录带同 nonce →
      already_finished）;变异（去转发）咬住

## codex 第二十九轮（2×P2：签名入身份字段、硬杀后终录如实点名）

- [x] P2 watcher 签名缺身份字段——同戳接替（粗粒度/冻结时钟可造）恰恰
      **只有身份在变**,kind/戳/新鲜度全不动:片段永不重绘,页面停在
      「已取消」、按钮虚开（launch 边界会拒,但操作人看到矛盾帧）直到
      手动交互。修=_status_signature 元组加 launch_nonce+pid 两字段;
      守卫锚随扩展更新+两字段钉
- [x] P2 Windows「终态已写、台账未完」窗内硬杀——重读见 finished 且经
      nonce/pid 核实是本次运行自己的终录,却落进「写记录前被终止」兜
      底,与上方 finished 横幅自相矛盾,且掩盖「可能仅台账追加被打断」
      这一真相。修=confirm 分支复用既有终态 oracle 单独检测
      （terminal_after_kill 标记）+ 专属如实措辞分支（运行本体已完成落
      账,上方状态即它）;源码序钉（专属分支先于兜底 else）

## codex 第三十轮（P2：迟到补结算同款终录检出）

- [x] P2 r29 的检出只装在 confirm 当场分支——kill 成功但宽限窗超时的
      迟到死亡走 _settle_late_pending,Windows 子进程同样可在真正终止前
      写完终态:补结算结局无 terminal_after_kill,帧照样落「写记录前被
      终止」兜底、与 finished 横幅矛盾。修=补结算复用同一终态 oracle
      （上界=补结算观测时刻:nonce 身份在场窗不参与,legacy 对观测时刻
      ≥真实死亡亦成立）,结局带 terminal_after_kill 键走同一渲染。守
      卫=oracle 调用恰好两处 + 补结算切片带赋值前缀锚（裸函数名锚被
      `False and` 熄火变异逃逸过一次,实测收紧后咬住）。**过程自纠**:
      变异还原误用 git checkout 洗掉了未提交改动,已重做并改用 cp 备份
      还原（本教训并入变异纪律）

## codex 第三十一轮（P2：终录检出改快照版,消二次读取竞态）

- [x] P2 r30 的 oracle 内部**重读**与已捕获快照之间,调度器接替可改写
      共享工件——第一读见本次终录、第二读见接替,terminal_after_kill 翻
      假,帧对着 finished 横幅说「写记录前被终止」。修=拆
      `terminal_status_confirms_the_run` 快照版（判据与读盘版逐字一致,
      读盘版=读一次+委托快照版,保留给 cancel_update 内部无快照的两处
      调用）;页面 confirm/补结算改用各自**本帧已读**的
      _fresh_status/_late_status。快照回归（终录快照判定与盘面无关,接
      替改写不影响）+ 页面钉（快照版恰两处/读盘版零残留/两处都带快照
      实参锚）

## codex 第三十二轮（P2：「上方状态即它」在渲染帧复验——r22 同类）

- [x] P2 terminal_after_kill 在快照帧被接受,消息却在 st.rerun() 后的新
      帧渲染——其间接替可改写工件,新帧横幅已是接替记录,「上方状态即
      它」变谎话。修=结局携带被认定终录的身份（terminal_identity:
      戳+nonce,两处写点同带）;渲染分支对**本帧** _status 复验（kind
      finished + 戳相等 + nonce 相等）,不符走如实改口文案（终录取消当
      时经核实,此刻已被接替,以台账为准）。页面钉六条（两写点/复验三
      比对/改口文案）

## codex 第三十三轮（P2：改口文案不许指向台账）

- [x] P2 r32 改口文案「以台账为准」在本窗恰不成立——terminal_after_kill
      代表的正是「终态已写、台账追加被打断」:接替抹掉快照后,台账里可
      能根本没有这条,指向台账=指向不存在的记录。修=终录身份随行取消当
      时核实到的**事实**（exit_code/run_date,两写点同带）,改口文案呈现
      随行事实并明说「台账追加可能被打断,不保证已入台账」。守卫=
      assertNotIn「终态以台账为准」+ 不保证声明钉 + 两写点事实随行钉。
      过程瑕疵:文案断行拆断了既有 needle（跨源码换行老坑）,调整断行
      位置复绿

## codex 第三十四轮（2×P2：台账承诺全撤、缺记录≠证实写前被杀）

- [x] P2 graceful 成功文案「状态与台账如实可查」——_append_ledger 是刻
      意 best-effort（写失败吞掉照常退出,daily_update.py 契约）,
      terminal_recorded 只验过状态工件。修=全页台账措辞审计:graceful
      改「状态工件如实可查（台账为尽力追加,不在本页核实范围）」;
      no-op 的「以状态与台账为准」同类软化;匹配分支「已完成落账」改述
      避免入账误读。守卫=两个 assertNotIn（台账如实/台账为准）+ 不保证
      声明钉
- [x] P2 兜底把「写前被杀」当已证事实——接替可在死亡与重读之间改写工
      件,唯一已证事实只是「此刻无匹配记录」。修=兜底如实列举三种可能
      （写前被杀/被接替改写/暂不可读）+ nonce 复现承诺（不确凿路径已
      留证据,孤儿复现会被盖住）。r29 序钉锚随改述更新（断言意图不动）

## codex 第三十五轮（2×P2：杀前终态快照判别、graceful 渲染帧复验）

- [x] P2 终录先在时 kill 静默返回≠no-op——「终态已写、正在追加台账」窗
      内进程活着,TerminateProcess 对活进程即执行:r20/28 的 oracle 分支
      把它报成 already_finished（取消未执行）,绕过页面
      terminal_after_kill 链。修=杀前先做一次终态快照
      （pre_kill_terminal）:终录**早已在**+杀后死亡 → 按已发信号的确认
      死亡归类（页面如实呈报「终录已写、台账可能被打断」）;终录**仅在
      杀后**才出现 → 才是真自然完成。quiet-kill 测试 A/A' 变体按新语义
      改判 cancelled,新增变体N（stub 在 kill() 内写录=真 no-op →
      already_finished）;变异（快照恒假）咬住
- [x] P2 graceful 的 terminal_recorded 只带布尔——r32 同款渲染帧复验补
      到 graceful:身份随行（graceful_identity:pid/nonce/launched_at/
      exited_at）,渲染帧用同一快照 oracle 对本帧 _status 复验,接替情形
      如实改口（终态以取消当时核实为准）。快照 oracle 页面调用 2→3 处
      计数钉更新;跨行 needle 教训再犯一次（改短锚）

## codex 第三十六轮（2×P2：不可判定格显式化、复验三态化）

- [x] P2 r35 判别的本质局限被点破——杀前快照证明**记录时序**不证明**送
      达**:「终录先在+kill 静默返回+死亡」格里,TerminateProcess 真杀
      与快照后自然退出（CPython 吞 access-denied 正常返回）经 Popen API
      不可区分。r20 判「取消未执行」在前者撒谎,r35 判「强制终止已执
      行」在后者撒谎——终态=不在两个都可能撒谎的标签里二选一,新增
      UpdateCancel.terminal_race:already_finished+race 标记+专属措辞
      （终录先于终止指令;送达无法确定;二者数据等价,台账不保证）。
      quiet-kill A/A' 第三次改判并留三轮谱系注;变异（去 race 标记）
      咬住
- [x] P2 graceful 渲染帧复验的 missing/corrupt 被当接替（r23/25 同款第
      三次出现）——两处复验（graceful/terminal_after_kill）都补三态:
      匹配=声称成立;missing/corrupt=「暂不可读,以取消当时核实为准」;
      合法不匹配才说「已被接替」。计数钉（三态分支恰两处）

## codex 第三十七轮（P2：terminal_race 分支补渲染帧复验）

- [x] P2 r36 新增的 race 分支只带布尔——「成败以上方状态为准」在接替
      改写后对着接替横幅说（r32/35 同类第三次出现）。修=race 结局同用
      graceful_identity 身份随行（条件放宽为 terminal_recorded or
      terminal_race;runner race 返回补 exited_at 观测界）,渲染帧同款
      oracle 复验+三态改口（匹配/暂不可读/已被接替）。快照 oracle 页
      面调用 3→4、missing/corrupt 三态 2→3 计数钉同步

## codex 第三十八轮（P2：tak 复验统一到共享 oracle,补 pid 身份）

- [x] P2 r32 的 _tid_current 手写三元比对（kind/戳/nonce）漏 pid——
      legacy 无 nonce 会话下,同戳异 pid 的 finished 接替（粗粒度时钟可
      造同戳）被认成本次终录。修=terminal_identity 补随行 pid/
      launched_at/exited_at 四件套（confirm/补结算两写点）,复验改调共
      享 terminal_status_confirms_the_run（nonce 一票裁决/legacy
      pid+全窗,与取消当时核实判据同构）。快照 oracle 页面调用 4→5 计
      数钉;三条声称链复验至此全部同一 oracle,无手写比对残留

## 既有守卫开火一处（处置=改我不削弱守卫）

- 页面源码禁现 spawn 字样的守卫咬了我的注释字面——注释改述（「活进程
  句柄」+指明零 spawn），守卫一字未动

## 刻意不做（勿在评审中重开）

- 不取消调度器自动运行（无句柄、越权）
- 不加协作式取消哨兵（触 `_execute_daily_update` 阶段语义红线）
- 不按 pid 杀、不跨 UI 重启恢复取消能力（句柄只活在会话内存，如实降级）
- 不由 UI 伪造状态工件终态（写者纪律：只有编排器写）

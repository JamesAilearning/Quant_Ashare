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

## 既有守卫开火一处（处置=改我不削弱守卫）

- 页面源码禁现 spawn 字样的守卫咬了我的注释字面——注释改述（「活进程
  句柄」+指明零 spawn），守卫一字未动

## 刻意不做（勿在评审中重开）

- 不取消调度器自动运行（无句柄、越权）
- 不加协作式取消哨兵（触 `_execute_daily_update` 阶段语义红线）
- 不按 pid 杀、不跨 UI 重启恢复取消能力（句柄只活在会话内存，如实降级）
- 不由 UI 伪造状态工件终态（写者纪律：只有编排器写）

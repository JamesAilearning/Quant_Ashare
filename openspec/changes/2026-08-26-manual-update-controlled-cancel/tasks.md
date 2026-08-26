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

## 既有守卫开火一处（处置=改我不削弱守卫）

- 页面源码禁现 spawn 字样的守卫咬了我的注释字面——注释改述（「活进程
  句柄」+指明零 spawn），守卫一字未动

## 刻意不做（勿在评审中重开）

- 不取消调度器自动运行（无句柄、越权）
- 不加协作式取消哨兵（触 `_execute_daily_update` 阶段语义红线）
- 不按 pid 杀、不跨 UI 重启恢复取消能力（句柄只活在会话内存，如实降级）
- 不由 UI 伪造状态工件终态（写者纪律：只有编排器写）

# Tasks: 2026-08-16-ui-run-center

## W1 update_runner（detached 启动器）

- [ ] `web/operator_ui/update_runner.py`：argv 镜像调度器形状（六参数 +
  `--start-date 20180101`），首两元素 = 解释器 + 仓库推导脚本路径
- [ ] detach：win32 `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`，POSIX
  `start_new_session=True`；`stdin=DEVNULL`；stdout/stderr 追加
  `<provider 父目录>/logs/daily_update.log`；launch 标记行带完整日期
- [ ] env 经 `utf8_child_env()`；token 预检读同一 env 映射
- [ ] 预检：unusable_path（三路径必须绝对，空串=CWD 陷阱）/ no_token /
  already_running（仅本 provider + running + 新鲜；advisory，锁是权威）/
  script_missing / launch_failed 各态
- [ ] `log_tail()` 只读工具

## W2 recommend_runner（同步 runner）

- [ ] `web/operator_ui/recommend_runner.py`：五旗标 argv，同步
  `subprocess.run` + UTF-8 text + timeout 900s + `cwd=repo 根` +
  `utf8_child_env()`
- [ ] 结果五态：ok / failed / timeout / launch_failed / run_failed
  （脚本缺失），stdout/stderr 尾部随载

## W3 页面 + 注册

- [ ] `web/operator_ui/pages/run_center.py`：状态三态展示（reader 语义
  复用）+ 刷新 + 启动按钮（running 新鲜禁用）+ 日志尾部 expander；
  出单区 `st.code` 权威命令 + ensemble-only 按钮 + 结果 fail-loud
- [ ] `app.py`：`_navigation`「运行」组 + `_ICON_MAP` 各加一条

## W4 测试

- [ ] `tests/logic/test_update_runner.py`：argv **全列表相等**钉（走私
  旗标即红）、detach/env/日志钉、五拒绝分支、漂移守卫（脚本与
  reference_cases 存在）、源码目标钉
- [ ] `tests/logic/test_recommend_runner.py`：argv **全列表相等**钉 +
  六禁标文档性断言、UTF-8 kwargs 钉、四结果分支、源码目标钉
- [ ] `tests/logic/test_run_center_page_source.py`：页面禁 spawn/写 API/
  编排器 import；须引用两 runner；app.py 注册行 + 图标

## W5 runbook

- [ ] `docs/run-center-runbook.md`：职责、并发权威说明、日志落点、UI 启动
  `.bat` 模板（TUSHARE_TOKEN 注册表回读）

## codex #440 r1（三条全实修）

- [ ] P1 出单×更新并发：`_runnable` 增加 `not _running_fresh`
  （bundle_swap 两段 rename 非读者并发），拒绝态给专属说明；页面源码
  测试钉住闸门表达式
- [ ] P1 超时撕裂工件：产物改写每次一新的暂存目录，exit 0 后逐文件
  同卷 `os.replace` 原子发布；超时/失败只清暂存不碰已发布；发布中断
  保留暂存并指名；六个 staging/publish 生命周期测试
- [ ] P2 marker 缓冲：`log_fh.flush()` 先于 `Popen`；子进程抢写次序
  回归测试（无 flush 则红，反向验证过）

## codex #440 r2（1 条 P1 实修）

- [ ] 状态闸门非权威（写失败/陈旧即失真）→ 新增 `provider_lock.py`
  （单飞锁 web 侧镜像，不 import src），出单执行持锁进行;锁忙/锁
  文件不可用 → `blocked_by_update` fail-closed
- [ ] 双向互斥行为测试:真 `single_flight` 持锁 → runner 拒绝(端到端);
  stub 锁忙 → 不派生;通用测试全部改打锁桩(避免碰真锁文件/CI 无 D 盘)
- [ ] 页面渲染 `blocked_by_update` 专属分支 + 源码钉;spec 增
  「状态失真时锁仍拦得住」场景

## codex #440 r3（1 条 P1 实修）

- [ ] 顺序发布第 2/3 个 replace 失败会留混批工件集且暂存不再完整 →
  发布改带回滚账本:旧版本先入暂存 `.prior`,失败则新文件退回暂存+
  旧版本复位(整体回滚);un-publish 失败的名字不复位旧版(避免砸掉
  新文件唯一副本),逐名指名滞留
- [ ] 两个回滚测试:中途失败 → 发布目录逐字节复原+暂存完整;回滚
  不完整 → 残留逐名+证据目录保留

## 验证

- [ ] `pytest tests/logic/test_update_runner.py tests/logic/test_recommend_runner.py tests/logic/test_run_center_page_source.py tests/logic/test_operator_ui_page_header.py tests/logic/test_operator_ui_theme.py tests/governance/ -x`
- [ ] mypy 用 CI 精确命令全量（ubuntu-3.11 leg 同款）
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

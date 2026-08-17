# Tasks: 2026-08-16-ui-run-center

## W1 update_runner（detached 启动器）

- [x] `web/operator_ui/update_runner.py`：argv 镜像调度器形状（六参数 +
  `--start-date 20180101`），首两元素 = 解释器 + 仓库推导脚本路径
- [x] detach：win32 `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`，POSIX
  `start_new_session=True`；`stdin=DEVNULL`；stdout/stderr 追加
  `<provider 父目录>/logs/daily_update.log`；launch 标记行带完整日期
- [x] env 经 `utf8_child_env()`；token 预检读同一 env 映射
- [x] 预检：unusable_path（三路径必须绝对，空串=CWD 陷阱）/ no_token /
  already_running（仅本 provider + running + 新鲜；advisory，锁是权威）/
  script_missing / launch_failed 各态
- [x] `log_tail()` 只读工具

## W2 recommend_runner（同步 runner）

- [x] `web/operator_ui/recommend_runner.py`：五旗标+暂存 `--out-dir`
  argv，同步执行 + UTF-8 text + timeout 900s + `cwd=repo 根` +
  `utf8_child_env()`
- [x] 结果六态：ok / blocked_by_update / failed / timeout /
  launch_failed / run_failed，stdout/stderr 尾部随载

## W2b provider_lock（权威串行化，codex r2）

- [x] `web/operator_ui/provider_lock.py`：更新器 provider 单飞锁的
  web 侧镜像（不 import 管线层），出单执行持锁覆盖子进程读窗口

## W3 页面 + 注册

- [x] `web/operator_ui/pages/run_center.py`：状态三态展示（reader 语义
  复用）+ 刷新 + 启动按钮（running 新鲜禁用）+ 日志尾部 expander；
  出单区 `st.code` 权威命令 + ensemble-only 按钮 + 结果 fail-loud
- [x] `app.py`：`_navigation`「运行」组 + `_ICON_MAP` 各加一条

## W4 测试

- [x] `tests/logic/test_update_runner.py`：argv **全列表相等**钉（走私
  旗标即红）、detach/env/日志钉、五拒绝分支、漂移守卫（脚本与
  reference_cases 存在）、源码目标钉
- [x] `tests/logic/test_recommend_runner.py`：argv **全列表相等**钉 +
  六禁标文档性断言、UTF-8/管道/进程组 kwargs 钉、全部结果分支、
  staging/publish/回滚生命周期、锁互斥双向实证、源码目标钉
- [x] `tests/logic/test_run_center_page_source.py`：页面禁 spawn/写 API/
  编排器 import；须引用两 runner；app.py 注册行 + 图标

## W5 runbook

- [x] `docs/run-center-runbook.md`：职责、并发权威说明、日志落点、UI 启动
  `.bat` 模板（TUSHARE_TOKEN 注册表回读）

## codex #440 r1（三条全实修）

- [x] P1 出单×更新并发：`_runnable` 增加 `not _running_fresh`
  （换库两段 rename 非读者并发），拒绝态给专属说明；页面源码
  测试钉住闸门表达式
- [x] P1 超时撕裂工件：产物改写每次一新的暂存目录，exit 0 后逐文件
  同卷 `os.replace` 原子发布；超时/失败只清暂存不碰已发布
- [x] P2 marker 缓冲：`log_fh.flush()` 先于 `Popen`；子进程抢写次序
  回归测试（无 flush 则红，反向验证过）

## codex #440 r2（1 条 P1 实修）

- [x] 状态闸门非权威（写失败/陈旧即失真）→ 新增 `provider_lock.py`
  （单飞锁 web 侧镜像，不 import src），出单执行持锁进行;锁忙/锁
  文件不可用 → `blocked_by_update` fail-closed
- [x] 真 `single_flight` 持锁 → runner 端到端拒绝;通用测试打锁桩
  (避免碰真锁文件/CI 无 D 盘)
- [x] 页面渲染 `blocked_by_update` 专属分支 + 源码钉;spec 增
  「状态失真时锁仍拦得住」场景

## codex #440 r3（1 条 P1 实修）

- [x] 顺序发布第 2/3 个 replace 失败会留混批工件集且暂存不再完整 →
  发布改带回滚账本:旧版本先入暂存 `.prior`,失败则新文件退回暂存+
  旧版本复位(整体回滚);un-publish 失败的名字不复位旧版(避免砸掉
  新文件唯一副本),逐名指名滞留
- [x] 两个回滚测试:中途失败 → 发布目录逐字节复原+暂存完整;回滚
  不完整 → 残留逐名+证据目录保留

## codex #440 r4（1 条 P1 实修）

- [x] `exists()` 预检吞瞬态 stat 错误 → 旧版未入账即被覆盖,事后
  「完整回滚」实为丢件 → 账本移动直接尝试,仅 FileNotFoundError 视为
  无旧版,其余 OSError 走整体回滚(该文件对未动)
- [x] 回归测试:账本移动遇 PermissionError → 整体回滚,发布目录逐字节
  复原(含未被静默覆盖的旧版),暂存完整

## codex #440 r5（1×P1 + 2×P2 全实修）

- [x] P1 超时只杀顶层进程:joblib 孙进程握着捕获管道,drain 永久挂起且
  锁不释放 → 子进程以自有进程组/会话启动(win 组旗标;posix new
  session),超时先 `_kill_tree`(win `taskkill /F /T`;posix `killpg`)
  再宽限 drain;终止不完整时保留暂存、指名顶层 pid、如实声明锁释放
- [x] P2 反向锁互斥测试:web 镜像持锁 → 真 `single_flight` 抛
  `AlreadyRunningError`,释放后可正常获取(双向实证闭环)
- [x] P2 本文件勾选如实化(实现项 [x],未做项保留 [ ])

## codex #440 r6（1×P1 + 1×P2 全实修）

- [x] P1 taskkill 非零 + 顶层已退出被推断成「树已死」→ 非零一律视为
  终止不完整(死顶层 pid 让 /T 无从走树,孤儿工作进程可能仍持有管道与
  bundle);安全方向=假「不完整」可接受,假「已死」不可接受;单测钉
  rc128+顶层退出 → note 非空
- [x] P2 预写 marker 在 Popen 失败后误归因调度器输出 → 措辞改
  「launch attempt」+ 失败路径追加「launch FAILED」标记;两处日志
  状态回归测试

## 后续修复（#440 并后操作人实测反馈，2026-08-17）

- [x] 刷新按钮"像坏的":它本就可用(无 disabled,点击即重跑重读),但状态
  未变时整页重绘与未点击不可区分 → 加「上次读取 HH:MM:SS」+ 点击 toast
- [x] 更新进行中自动轮询(`st.fragment(run_every=30)`),状态跃迁时
  `st.rerun(scope="app")` 整页重绘——只刷片段会让出单闸门(依赖主脚本
  作用域)与状态展示自相矛盾
- [x] 日志尾部乱码:共享日志双写入者(本页钉 UTF-8 / 计划任务未钉,中文落
  cp936) → 逐行解码 UTF-8→GBK→replace;真日志实测 `INFO ��` → `INFO —`;
  两个新测试(混编码各自还原 / 不可解码字节不抛异常)
- [ ] 源头修:调度器 `run_daily_update.bat` 补 `PYTHONIOENCODING=utf-8`
  ——**今晚运行中不可动**(cmd 边执行边读取批处理文件,改动会破坏执行),
  且与 tracked 模板对齐任务有交集,待运行结束后另行处置

## 验证

- [x] `pytest tests/logic/test_update_runner.py tests/logic/test_recommend_runner.py tests/logic/test_run_center_page_source.py tests/logic/test_operator_ui_page_header.py tests/logic/test_operator_ui_theme.py tests/governance/ -x`
- [x] mypy 用 CI 精确命令全量（ubuntu-3.11 leg 同款）
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

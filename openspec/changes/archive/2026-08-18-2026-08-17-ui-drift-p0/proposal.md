# Proposal: 修 UI 与生产现状的 P0 漂移（csi800 守卫缺失 / 成本口径过期 / entry 红线缺席）

## Why

`#440` 并入后对全部 operator UI 页面做了一轮与生产现状（csi800 / N5
ensemble / 周频 iso_week / 20 bps 单边）的比对审计，查出三条会**直接
伤到操作人**的漂移。三条都经端到端复现，不是纸面推断。

### D1 配置运行页开箱即坏（最严重）

页面加载时无条件套用 Default 预设，而 `config/presets/default.yaml` 是
`instruments: csi800` + 三条扩池守卫。但页面组装 `config_dict` 时只带走
`instruments`，三条守卫既无控件也不进配置。实测：

```
PipelineConfig(instruments='csi800', provider_uri=...)
→ PipelineError: instruments='csi800' requires attribution_sleeve_grouping=True,
  risk_constraints_enabled=True AND risk_constraints_calibration='campaign_v1'
```

页面却显示「✓ 配置有效」→ 点运行 → 「作业已启动」→ 作业在配置构造阶段
即死。**第一次打开该页什么都不改就是这个状态**；「重跑历史 csi800 作业」
同样静默丢守卫。

### D2 今日推荐的成本参照过期近一半

`ROUND_TRIP_COST = 0.0030`（30 bps 往返 = 15 bps 单边）是 csi800 N5 认证
之前的数。认证生产口径是 **20 bps 单边**，一次完整往返 = 佣金×2 + 印花税
+ 滑点×2 = **55 bps**。候选表每一行的成本锚都乐观了近一倍。

### D3 今日推荐没有 entry_date 红线说明

`entry` 是**已收盘**会话（可交易性筛选需要该日真实 bar，工具永远出不了
面向未来会话的单）。runbook 把「每次必读 entry_date」列为红线，运行中心
页也写了，唯独操作人每天真正读清单的这一页没有——`grep 已收盘` 零命中。

## What changes

### W1 config_run：守卫三件套进配置、进控件、进守卫

- `_GUARD_FIELD_DEFAULTS`（值 = dataclass 默认 False/False/`default`，
  **不是** csi800 契约值——契约由守卫响亮索取，默认不该把 campaign 语义
  静默盖到 csi300 运行上）；与 `_COST_FIELD_DEFAULTS` 合成
  `_RESET_FIELD_DEFAULTS`，`_apply_preset` 与 `_detect_preset` 共用同一张
  表（两条重置路径分家正是它们漂移的方式）。
- 新增「🛡️ csi800 扩池守卫」区块三个控件；三键进**共享** `config_dict`
  （两侧 schema 都收），因此 pipeline 与 walk_forward 同时被覆盖。
- `training_guards.validate_csi800_guard_triple` **委托 canonical 校验器**
  裁决（UI 复述规则正是本 bug 的成因），在渲染守卫段与提交复检段各调一次
  （stale-frame 防御，比照既有 `non_production_bundle_error` 处理）。
- `slippage_bps` 默认**不动**：治理钉把 5.0 定义为 base 敏感带的 in-code
  默认，改成 20 会把每个未声明该键的预设静默拖进 conservative 带。改为
  help 文案讲清两个带各自是什么、认证复现要显式填 20。

### W2 今日推荐：成本口径派生化 + 列名同源

- `ROUND_TRIP_COST` 由三项**组装**而非复述：滑点 live 读认证 profile
  （`scripts/eval_profiles.py`，刻意 qlib-free）；佣金与印花税按仓库既有
  「跨层刻意复制 + 测试钉一致性」模式（同 `update_status.py` 对写侧常量的
  处理）本地定义，由 CI 钉住不漂。组装式抄 `backtest_runner` 的
  exchange kwargs（open=佣金+滑点；close=佣金+印花税+滑点）。
- 列名 `COST_REFERENCE_COLUMN` 由常量 f-string 派生——旧代码把 30 写死在
  表头，口径一改就「列名说 30、算的是 55」。
- 页面加两条 caption：**entry 是已收盘会话**（红线）、成本列是**保守
  下界**而非逐日门槛（评分是 1 日预测收益，周频持有约 5 日才付一次往返）。

### W3 测试

- 行为测试 `tests/logic/test_csi800_guard_triple_ui.py`：先复现开箱即坏，
  再证明守卫在同一条件下拒绝、补齐后可构造；全部真子集分别拒绝；
  非 csi800 宇宙不受影响；padded `" csi800 "` 仍被守住。
- 源码钉重锚：重置表必须是两族并集且 apply/detect 共用（并断言两族各自
  的私有重置路径**不再存在**）。
- 成本三钉：值 = 55 bps；move-the-source 联动（挪动 profile 滑点 → UI 锚
  跟随）；复制常量与 canonical 源逐一相等。

## 边界（本 change 不做）

- 不改后端校验器、不改 `config/presets/*.yaml` 内容、不动 serving config。
- 不改 `slippage_bps` / `ensemble_window` 的默认值（前者是治理钉死的 base
  带；后者是 dataclass 默认，改动会与 canonical 分叉）——只加说明。
- P1/P2 档漂移（作业↔滚动验证跳转、状态词汇、metric_status 与运行身份、
  预设分组、结果页毛/净口径标注）另 PR，本 change 不含。

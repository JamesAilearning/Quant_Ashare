# Tasks

## 桥模块（研究侧）

- [ ] `src/research/fundamental_panel.py`：`build_fundamental_panel(view, fields,
      trade_dates, instruments, *, group_resolver=None)` → `(panels, evidence)`，
      两者同形状 `dict[field -> date×instrument DataFrame]`；逐交易日 `view.as_of`，
      实例内缓存已由 view 提供（只付过滤成本）。
- [ ] 返回前自检：每个非 NA cell 的 `available_from <= trade_date`，违反即 raise；
      无法建立证据的字段 → 拒绝返回面板（不返回"无证据面板"）。
- [ ] `group_resolver` 参数今天恒传 `None`（PIT 行业 artifact 属后续 change）；
      签名与文档写明"绝不以当前快照兜底"。
- [ ] 性能：先测全历史 × CSI800 的墙钟时间；若逐日 Python 循环过慢，改为按
      `available_from_trade_date` 的 `merge_asof` 向量化 —— **但等价性须由 (i) 的
      抽样一致性断言守住**（两种实现必须给出同一面板）。

## 四件套防线（与桥同批落地，不可后补）

- [ ] (i) `tests/logic/research/test_fundamental_panel_pit.py`：合成小店属性测试 —
      公告前 NA / 严格次日起可见 / 公告日当天仍 NA / restated 期服务 original /
      missing-stays-missing；抽样 + 边界日（公告日、前一日、后首个交易日）核对
      `panel[T] == view.as_of(T)`。
- [ ] (ii) `tests/logic/research/test_fundamental_panel_canaries.py`：未来值金丝雀
      （T 日值 = T 日 forward_return → 必须被证据断言拒绝）；提前公告金丝雀
      （`ann_date > T` 却被服务 → 面板化必须 raise）。两者均断言"拒绝发生在进入
      因子评估之前"。
- [ ] (iii) `scripts/research/fundamental_ann_shift_sensitivity.py`：公告日整体后移
      N∈{5,10,21} 交易日重建面板；断言面板内容哈希必变 + IC 序列差异超容差；
      **不变即 REFUSE**（报告"公告日未被消费"）。配套合成店单测（方向可机器判定）。
- [ ] (iv) prereg 挂接（随基本面战役的预注册包）：(i)(ii) 进该战役门的 PIT battery
      （append-only，canonical battery 不可替换）；两个 rehearsal 场景（注入探针 /
      金丝雀存活 → 门 REFUSE，仿 R6）；桥模块 + (iii) 脚本进 `FROZEN_ARTIFACTS`；
      终端集经 `grammar.allowed_terminals` 白名单冻结。

## 验证与边界

- [ ] 确认 `src/factor_mining/` 零改动、零新 import；D5 gate 与
      `test_financial_pit_view_isolation.py` 均照原样通过（**不改签**）。
- [ ] 确认 canonical runtime / `src/data/pit/*` / `financial_pit_view.py` 未被触碰。
- [ ] ruff + mypy --strict + 治理全套 + 新增测试全绿。
- [ ] 用起步三因子（① GP/A、③ 资产增长、② C3 纯 BS 应计 —— CSI800 覆盖率已实测全绿）
      跑通"面板化 → GP → 边际贡献"最小链路，**只为验证链路与防线，不做因子裁决**
      （裁决属后续预注册战役）。
- [ ] Spec delta 归档时并入 `openspec/specs/v2-fundamental-gp-panel`。

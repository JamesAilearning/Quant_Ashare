# Tasks: 2026-08-17-ui-drift-p2

## W1 预设分类

- [x] `classify_preset_names`（判据 = 顶层 `mode` 键；自维护，无需人工
  登记）；实测 6 可跑 / 30 冻结
- [x] 下拉框只收可跑预设；冻结件只读列出 + 三条真实原因 + 命令行指引
- [x] `_detect_preset` 只在可跑预设中匹配（否则会命名一个下拉框已不提供
  的选项，选择框静默回落而状态说的是另一个）

## W2 诚实化文案

- [x] 「Production = 全量生产」→「全市场基线」，并指明生产权威在
  `config/serving/csi800_n5_production.yaml`
- [x] 页面声明「本页产出的是日频研究配置」
- [x] `ensemble_window` 保持默认 1，加 help 说明正典用 3 及扫描证据

## W3 结果页口径标注（按独立复核实证，非审计表述）

- [x] 主指标 →「绝对**毛**：未减基准、未扣成本」
- [x] IR →「扣费后超额」（与正上方主指标口径相反，必须各标各的）
- [x] 净值曲线 / 月度收益各自声明绝对毛，并指出与 IR 不可直接相减
- [x] 回撤图 ↔ 风险卡互相声明「不是同一个数」，**三处差异逐一写出**
  （基准 / 成本 / 算术 vs 几何累计）；明说「本就不相等」
- [x] 收益卡 help 补毛净可**反号**的教训（csi800 毛 +3.68% / 净均负）

## W4 测试

- [x] `test_preset_classification.py`（11 用例）：分区性 / 标记语义 /
  已知战役件必为冻结 / 内置必可跑 / 页面接线四钉
- [x] `test_results_cost_convention_labels.py`（7 用例）：各口径标注到位 /
  三处差异必须**成组**出现在同一段说明里 / 明说不相等 / 反号教训 /
  既有正确范本不得被碰掉

## 复核记录（重要）

独立复核**推翻了审计对回撤差异的成因归因**：审计称差在「扣费后超额 vs
绝对毛」，实测差异有三个维度，且**成本是最小的一项**（该 run 上成本只
解释 21bp，两数差 395bp）。按审计表述写标注会让操作人更糊涂。审计的
行号也有一处错（月度收益在 838-915，不在 759）。

## codex #445 r1（四条 P2 全实修）

- [x] 选择器**标签本身**仍写着 Production——帮助气泡说清了但操作人多半只看
  选项。加 `format_func` + `_PRESET_DISPLAY_NAMES`,显示名改「全市场基线
  (instruments=all,日频;**非**生产服务配置)」;选项**值**保持内置名
  (`load_preset` 按它解析文件名)
- [x] 复跑指引一句话统一成 run_walk_forward 对 pipeline 形状的冻结件是错的
  (bootstrap 三成员 / candidate extends config.yaml,walk-forward 加载器会
  拒绝),gate3 那批根本不可跑。新增 `frozen_preset_runner`(按**内容**判:
  gate3 前缀优先 → walk-forward 窗口键/extends → pipeline 窗口键),冻结件
  按实际 runner 分组给命令。实测 16 wf / 4 pipeline / 8 不可跑 / 2 未定
- [x] 收益卡 help 无条件说「主指标是绝对毛」,而老工件会兜底成扣费后超额
  → 同卡自相矛盾。help 改为**随分支**,兜底分支同时说明总收益/净值/月度
  仍是绝对毛
- [x] 夏普与 IR 同源(扣费后超额)却只写「夏普比率」→ 会被当成绝对毛主指标
  的配套。标注补齐并列入 help 的净超额清单

## 验证

- [x] 定向 72 passed；mypy CI 精确命令 220 文件 Success；ruff 全过
- [x] logic + governance 全层
- [x] `openspec validate --strict`
- [x] codex CLEAN + CI 绿 → STOP 等 merge

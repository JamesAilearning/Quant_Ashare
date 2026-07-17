# CSI800 战役首发简报 — 三 run 串行 + veto 五项勾验（2026-07-17）

**判定：veto① 触发（conservative 净超额 ≤ 0）→ 本对 run 不得作为晋升
依据（`promotion_eligible=false`），如实入档。** 其余四条 veto 全部
未触发；勾验状态 COMPLETE。本简报是研究纪律层结论记录，非晋升申请。

## 1. Run 事实记录

三发严格串行，全部跑在 main `5e26e14`（= #372 veto④ 校准修订合并
commit；工作树 dirty 标志仅系一个未跟踪的个人 probe preset
`my_probe_csi800.yaml`，已跟踪文件零改动）。配置为 #371 的三个
tracked preset（2018-2025 全窗、24/3/3 步 3 → 23 折、ensemble 3、
`campaign_v1` 约束校准、sleeve 分组按 pin 生效）。

| run | preset | 折 | 结果 |
|---|---|---|---|
| csi300 参照 | `csi300_campaign_reference.yaml`（5 bps, SH000300TR） | **22/23** | fold 8 被 `max_per_name` RAISE 如约中止（见 §2） |
| csi800 base | `csi800_campaign_base.yaml`（5 bps, SH000906TR） | 23/23 | 全折 official |
| csi800 conservative | `csi800_campaign_conservative.yaml`（20 bps, SH000906TR） | 23/23 | 全折 official |

**首发点火事故（已档）**：校准修订前的首发 csi300 参照被 P0-1 默认
约束击落 23/23 折（`max_per_board` 沪主板 53-60% vs 0.40、
`cash_buffer_min` 0.55-0.9% vs 0.01，结构性失配）。操作人签选项 A →
#372 `campaign_risk_constraints_v1` 于零战役结果窗口内修订；失败工件
留证于 `output/walk_forward/csi300_campaign_reference_failed_calib_20260717/`。

## 2. 参照 run fold 8 单折中止（反集中约束真触发）

修订后的校准保留 `max_per_name=0.05` 严格 RAISE。参照 run fold 8
（valid 2022-04~2022-06）中 SZ002241 持仓权重漂移至 6.84-7.04%
（> 5% 上限，2022-05-10 起连续多日），RAISE 如约中止该折、聚合以
NaN 占位并如实降 `valid_folds=22`。这与首发事故性质不同：**这是
约束对真实单票集中暴露在正常工作**，不是校准失配。csi800 双档
46 折零触发。含义：参照侧统计以 22 折计入，veto③ 换手基准照常可用
（`ref_valid_folds` 已入证据工件披露）。

## 3. 官方数字（逐折年化超额均值，净=with_cost）

| run | 毛超额 | 净超额 | 净 95% CI | mean IR | 净为正折数 |
|---|---|---|---|---|---|
| csi300 参照（5bps） | +8.83% | **+2.24%** | [−5.29%, +9.08%] | 0.21 | 13/22 |
| csi800 base（5bps） | +12.58% | **+6.08%** | [−5.57%, +17.43%] | 0.41 | 13/23 |
| csi800 conservative（20bps） | +12.59% | **−1.02%** | [−12.70%, +10.34%] | −0.31 | 11/23 |

- 双档毛超额一致（12.58 vs 12.59）+ IC 完全相同（1d 0.0114 / 5d
  0.0171）——同种子同预测，两档只差执行成本，敏感带构造成立。
- **成本算术一致性**：年化单边换手 ≈ 24.5×，增量滑点 15 bps 双边
  预期拖累 ≈ 2 × 24.5 × 0.15% ≈ 7.35pp；实测净落差 6.08 − (−1.02)
  = 7.10pp。两数吻合，敏感带结果内部自洽。
- 净口径盈亏平衡滑点 ≈ 17-18 bps（线性内插），恰落在 DP-2 依据的
  中盘实际成本区间（15-25 bps）**内部偏下**——这正是 veto① 要抓的
  情形：净 edge 对成本假设不鲁棒。

## 4. veto 五项勾验（证据工件 `csi800_campaign_pair_report.json`）

配对自证先行通过：投影 diff 恰 `{slippage_bps: 5.0 → 20.0}` 一处，
run id + config/report sha256 双侧入档。

| # | 判据（跑前钉死） | 实测 | 触发? |
|---|---|---|---|
| ① | conservative 净超额 ≤ 0 | **−1.02%** | **是** |
| ② | csi500 贡献 ≥ 80% 毛超额 且 ①成立 | 份额 71.1%（44.6/62.7pp，23 折） | 否 |
| ③ | 换手 > 1.5× csi300 参照 | 比率 **0.99**（日均 0.1030 vs 0.1040） | 否 |
| ④ | campaign_v1 约束未接线/未记录/被改 | 46 折 provenance 全一致，零问题 | 否 |
| ⑤ | csi500 时均权重 > 75% 或 unknown > 10% | 48.2% / 0.0% | 否 |

勾验工装：`scripts/research/csi800_campaign_attach_vetoes.py`（消费
guard-1 配对工件 + guard-2 sleeve/positions/provenance 工件，②③⑤
的算子实现与注记见脚本 docstring；③ 双侧同一纯函数
`sleeve_turnover` 重算，年化常数在比率中消去）。证据绑定与
fail-closed（codex #373 r1）：base/conservative 证据目录以 guard-1
同款 loader 重载并强制 `run_id/config_sha256/report_sha256` 与配对
工件逐字段一致；参照以 #371 钉死差集
（恰 `{instruments, benchmark_code, attribution_sleeve_grouping}`）
结构绑定；②⑤ 要求全折归因覆盖、③ 要求 csi800 双档全折 positions
完整（缺口即判触发），参照缺折仅当聚合已档失败
（`report_path` null，即 fold 8）方可豁免并入 `ref_failed_folds`
披露。

## 5. 诊断读数（非晋升论据）

- **breadth 毛 alpha 是真的**：csi800 毛超额比 csi300 参照高
  +3.75pp（12.58 vs 8.83），且不是靠换手买来的（③ 比率 0.99，
  几乎逐字相同），也不是中盘单边注（csi500 时均 48.2%，probe 时
  的 61.8% 在 campaign 配置下自然回落；unknown 桶 0%）。
- **但净口径不过保守成本关**：20 bps 全账本下净超额转负。机制清晰
  ——年化 24.5× 单边换手把 15 bps 增量滑点放大成 ~7pp 拖累，吃光
  毛 alpha 优势还倒贴。
- 与 probe 底数方向一致（probe: 毛 csi800 > csi300、净均负），
  现在有 guard 级配对证据 + veto 纪律背书。
- **天花板指向**：alpha 排序信号在 csi800 上有效（毛口径、IC 为
  正），瓶颈是换手强度 × 中盘成本。若要续做 csi800，方向是降换手
  （节奏/持有期族，阶段 7 已有 2×2 承诺框架）而非调成本假设——
  20 bps 是预注册值，试后回调被 spec 明文禁止。

## 6. 状态与后续（待操作人裁决）

- 本对 run 判定：**不予晋升**，作为诊断证据入档（本简报 + 配对
  工件）。
- holdout 未揭盲，不可反悔条款不受影响（本战役未触 holdout）。
- 可选后续（均需单独授权）：(i) 就此收束 csi800 战役，结论入
  OpenSpec 归档；(ii) 以"降换手 × csi800"为题开新 OpenSpec 变更
  （复用阶段 7 节奏框架 + 本 guard 体系，20 bps 主判口径不变）；
  (iii) 仅归档两个已合并 OpenSpec change，战役封存。

# v2-daily-stock-recommendation — delta for 2026-08-05-ensemble-serving-bound-params

## ADDED Requirements

### Requirement: ensemble 晨跑参数 SHALL 从钉死 serving config 绑定且显式不等即拒

`--ensemble-manifest` 模式下，宇宙/再平衡节奏/topk 三参数 SHALL 按下列语义解析：未显式给出时 SHALL 取 `config/serving/csi800_n5_production.yaml`（两级绑定链锚定件）的绑定值；显式给出且与绑定值不等 SHALL 拒绝出单（fail-loud，不静默以任一方覆盖另一方）；绑定源缺失、不可解析或缺任一绑定键 SHALL 拒绝（ensemble 模式必须有绑定源）。legacy 单模型模式 SHALL 保持原缺省与行为逐字不变——缺省语义的翻转会让 csi300 时代模型踩进 csi800 打分禁配。

#### Scenario: ensemble 模式一行命令绑定齐全

- **WHEN** 仅以 `--ensemble-manifest <路径>` 调用晨跑
- **THEN** instruments/rebalance_cadence_days/topk 取绑定值
  （csi800/5/50），产物携 iso-week 再平衡字段

#### Scenario: 显式参数与绑定值不等时拒绝

- **WHEN** ensemble 模式下显式给出与绑定值不等的
  `--instruments`/`--rebalance-cadence-days`/`--topk`
- **THEN** 拒绝出单并指出不等的参数与两侧值——显式参数不是绕过
  绑定链的通道

#### Scenario: 绑定源缺失时拒绝

- **WHEN** ensemble 模式下 serving config 缺失/不可解析/缺绑定键
- **THEN** 拒绝出单（绝不回退到 CLI 缺省——那正是漏参陷阱本身）

#### Scenario: legacy 单模型路径行为不变

- **WHEN** 以 `--model`（或缺省 canonical 路径）调用且未显式给出
  三参数
- **THEN** 沿用原缺省（csi300/1/50），不读 serving config

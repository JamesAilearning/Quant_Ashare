# Proposal: ensemble 晨跑参数从钉死 serving config 绑定

## Why

切换后的晨跑命令要求操作人显式携带
`--instruments csi800 --rebalance-cadence-days 5 --topk 50`——CLI
缺省仍是 csi300/日频（#392 codex r1 发现的漏参陷阱只靠 runbook 文字
防守）。漏一个参数 = 产出 csi300 日频工件、无 iso-week 字段，与
认证协议相悖；这是每天都要过一遍的人为失误面。

**裸改 CLI 缺省不可行**：legacy `--model` 单模型路径（csi300 时代
模型）若继承 csi800 缺省，会踩进 R1 明令禁止的"csi300 时代模型给
csi800 打分"禁配。正确形态 = **manifest 驱动绑定**：这些值早已被
两级绑定链钉死在 `config/serving/csi800_n5_production.yaml`（治理
测试锚定认证胜者），ensemble 模式就该从那里读。

## What Changes

1. `scripts/daily_recommend.py`：`--instruments`/
   `--rebalance-cadence-days`/`--topk` 缺省改为 None（哨兵），跑前
   解析：
   - **ensemble 模式**（`--ensemble-manifest` 给出）：None → 取
     `config/serving/csi800_n5_production.yaml` 绑定值；显式给出且
     与绑定值不等 → **拒绝**（fail-loud，不静默覆盖）；serving
     config 缺失/畸形/缺键 → 拒绝（ensemble 模式必须有绑定源）。
   - **legacy 单模型模式**：None → 原缺省（csi300 / 1 / 50），
     行为逐字不变。
2. 晨跑命令缩为
   `python scripts/daily_recommend.py --ensemble-manifest <路径>`；
   runbook 周节奏卡/首次上线卡第 6 步同步改写（显式传参仍允许但
   必须等值）。
3. 测试：ensemble 模式绑定三值/显式等值放行/显式不等拒/serving
   config 缺失拒；legacy 缺省逐字不变钉守。

## Non-goals

- 不改 serving config 内容与两级绑定链治理；
- 不改 RecommendationConfig/推荐管线本体；
- 不动 legacy 单模型路径任何行为。

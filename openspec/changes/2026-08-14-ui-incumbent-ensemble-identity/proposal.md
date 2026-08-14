# Proposal: 今日推荐页认得现任 ensemble —— 停止显示已退役的单模型

## Why

生产于 **2026-08-05** 切换为 csi800 N5 三成员 ensemble（manifest
`csi800_n5_ensemble_manifest.json`，四道门 PASS，`csi800_recert_status.json`
= WIN）。运维 UI 没有跟上这次切换，今天有**两处在对操作人说假话**：

**① 页顶「现任生产模型」横幅描述的是已退役的单模型。**
`resolve_model_path()` 的实现是「`QUANT_MODEL_PATH` > 写死默认值」，默认值
是切换前的 incumbent `alpha158_lgb_pit.pkl`。它**没有 manifest 的概念**，
于是横幅照旧渲染那个单模型的训练窗 / 晋升时间 / 模型名——而生产早已不用它。

**② ensemble 分支印着一句已到期的承诺。**
`daily_decision.py` 在识别出 ensemble 工件后写：

> 「当前生产为单模型形态,单模型 sidecar 交叉核对不适用;ensemble 形态的
> 现任 manifest 核对随生产切换(PR-C')落地。」

PR-C' 已于 2026-08-05 落地。这句话现在**既是假陈述**（当前生产不是单模型
形态），**又把一个已到期的 TODO 说成未来时**。

**③ 缺前提：没有「现任 manifest 在哪」的事实源。**
`docs/operations-env-vars.md` 只有 `QUANT_MODEL_PATH`；runbook 的晨跑命令
写的是占位符 `--ensemble-manifest <生产 manifest>`。所以即使想做 ensemble
的现任交叉核对，UI 也**无从知道现任 manifest 是哪一份**——这是 ② 之所以
一直没兑现的真实原因，必须一并补上。

这三条合起来的后果：**这一页的全部价值是让操作人在读清单前先确认「这是谁
给出的建议」，而它现在给的答案是错的。**

## What changes

### W1 — 现任身份的事实源（补 ③）

新增文档化环境变量 `QUANT_ENSEMBLE_MANIFEST`（`docs/operations-env-vars.md`
登记），**默认值 = 2026-08-05 切换写下的生产 manifest 路径**，遵循该文件
既有约定「每个 `QUANT_*` 的默认值等于历史硬编码路径」。并把 runbook 里的
`<生产 manifest>` 占位符换成该变量。

**未设 ≠ 单模型**（codex #430 r1）：把「变量没配」读成「生产改回单模型」是
凭空造事实。真那样做的话，任何升级了 UI 却没加新变量的部署，不但继续显示
退役模型，**还会对正确的 ensemble 清单警告「请勿据此下单」**——比不修更坏。
确实只服务单模型的部署，用显式 `none` 声明（opt-out 是有人做的陈述，不是
代码从缺省里推断出来的）。

**刻意的不对称**：CLI `scripts/daily_recommend.py` 的 `--ensemble-manifest`
**保持显式必传，不吃这个默认值**。跑的那一端不该有隐式默认（选错模型就是
错单）；读的那一端（UI 只做核对、不出单）才用得起默认值。这一点写进变量
文档，免得后来者「顺手」给 CLI 也加上默认。

### W2 — 横幅认 ensemble（修 ①）

`resolve_incumbent()` 取代 `resolve_model_path()` 的独角戏，返回现任形态：

* ensemble（`QUANT_ENSEMBLE_MANIFEST` 可解析）→ 横幅显示 manifest 文件名、
  sha256 前缀、成员数、各成员 fit 窗；
* 单模型（**显式** `QUANT_ENSEMBLE_MANIFEST=none` 的 opt-out；未设走
  文档化默认 manifest，不是这一态）→ 维持原有晋升 meta 横幅，一字不改；
* 变量已设但文件缺失/不可解析 → **醒目 WARN**，不回退到单模型形态、
  不显示占位值。

「缺字段只进 WARN、绝不填默认值」的既有纪律原样适用于新增字段。

### W3 — 现任 manifest 交叉核对（兑现 ②）

ensemble 工件的 `meta.ensemble.manifest_sha256` 与现任 manifest 的实际
sha256 比对：

* 相同 → 「出自现任 manifest」；
* 不同 → **醒目 WARN**：此清单出自另一份 manifest（与单模型侧「其他模型」
  同一告警类别）；
* 现任无法解析 → WARN 说明无法核对，**不静默跳过**（静默跳过正是本页存在
  的意义的反面）。

同时删除那句「当前生产为单模型形态…随 PR-C' 落地」。

## 边界（本 change 不做）

* **不改 CLI 行为**：`daily_recommend.py` 只读不动。
* **不让 UI 代跑任何东西**：本页「只渲染落盘工件、不重跑推理、不触发训练/
  GPU/job」的硬契约不变（治理测试守）。
* **不碰数据更新与季度轮换的 UI 化**——那是下一个 change（操作人已排在
  本项之后）。

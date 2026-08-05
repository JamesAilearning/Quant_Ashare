# Serving 模型↔宇宙一致性守卫 — 工单(交 CC 起小 OpenSpec)

> **Status:** 待 CC 拾取。**小 PR,建议赶在 CSI800 cutover 之前或并行落地**(不碰 cutover 本身,可与自举点火并行)。
> **性质:** 本文件 = 工单/设计输入。行为变更 + 动 run-meta 契约 → 走 OpenSpec(建议 `add-serving-universe-consistency-guard`,MODIFY `v2-daily-stock-recommendation`)。

## 1. 坑(架构侧 review 实证,origin/main @ 07414d2)

`daily_recommend` 的 `--instruments` 是**自由参数**(默认 csi300),**单模型路径没有任何"模型↔宇宙一致性"检查**:

- 实测:`src/inference/daily_recommend.py` 全文对 instruments 只做透传/记录(config→meta→日志),无一处与模型的训练宇宙比对;
- 也就是说,今天 `--model alpha158_lgb_pit.pkl --instruments csi800` 会**静默出清单**——一个零认证证据的组合(认证的 +6.52% 属于滚动重训 N5 ensemble;冻结模型恰恰挂过 C-4),横截面还分布外(模型在 300 只上学的排序,加 500 只中盘);
- **ensemble 路径已有守护**(`ensemble_serving.py` ~L372 拒绝静默换宇宙)——洞只在单模型路径;
- **cutover 之后这个坑变大**:新 runbook 明写 CLI 默认仍 csi300/daily,操作人要手动带 csi800 flags——新旧两套并存期,一个 flag 打错就是错配清单。

这正是"悬空守卫"反面教材的形状:看似有治理(ensemble 侧),实际最常用的单模型路径裸奔。fail-loud 优于 silent-wrong。

## 2. 修法(设计)

### 2.1 元数据侧(来源真相)
- **训练宇宙写进 trainer sidecar**(与 fit 窗并列;sidecar 机制已在,`_daily_decision_helpers.load_trainer_sidecar_sha` 在读)。今后每个模型产出时自记 `universe`。
- **现役模型回填**:`alpha158_lgb_pit.pkl` 的 sidecar/晋升 meta **显式回填 `universe: csi300`**(Gate-④ 晋升记录在案的事实,操作人签)——**不许**代码里静默默认 csi300。回填后,缺字段 = 一律 refuse。

### 2.2 Serving 侧(守卫)
- `daily_recommend` 早期守卫段(与 bundle 新鲜度/ST 源同级)加检查:**requested `--instruments` ≠ 模型 meta 的 `universe` → fail-closed exit 1**,domain 错误给三样:两个值、为什么拒(无认证证据/横截面 OOD)、修法(换对 instruments 或换对模型)。
- **meta 缺 `universe` 字段 → 同样 refuse**,错误信息指向回填指引;绝不静默放行、绝不默认。
- **ensemble 路径不改**(已守护),但补一条测试**钉死**现有拒绝行为不退化。
- 研究逃生口(可选,按仓库惯例):`--allow-universe-mismatch`,默认关;启用则 **(a)** 放宽记录持久化进工件 meta(对齐 cockpit 工单审注#1 的"非默认守卫放宽必落盘"),**(b)** 工件标 research-only。若嫌复杂,首版可不做逃生口——直接硬拒,研究需求走研究脚本。
- **产物 meta 增记 `model_universe`**(现在只记 requested `instruments`)——查看器/审计能看出"哪个模型对哪个宇宙出的单"。

### 2.3 Runbook
- `docs/daily-recommend-runbook.md` 的"When it refuses"表加一行(新拒绝类型 + 修法);`csi800-n5-production-runbook.md` 并存期一节引用之。

## 3. 治理测试(BLOCKING)
1. 老模型(meta=csi300)+ `--instruments csi800` → exit 1,domain 错误含两个宇宙值;
2. 匹配组合(csi300+csi300;将来 ensemble+csi800)→ 正常通过;
3. meta 缺 `universe` → refuse + 回填指引(**非**静默默认);
4. 现役回填后的 sidecar → csi300 正常跑(不破坏明早的运行);
5. ensemble 路径:manifest 宇宙拒绝行为**钉死**(回归);
6. (若做逃生口)启用 → meta 落盘放宽记录 + 工件标记,测"重读工件仍显示降级"。

## 4. 边界
- **不碰**:cutover 脚本/自举门/认证判据;守卫语义只加不改;canonical 训练路径除 sidecar 增字段外不动。
- 单 PR 量级;push 前本地 review loop;`openspec validate --strict`。

## 5. 时机
与 m1'/m2'/m3' 自举点火**并行不冲突**;**理想在早间命令切到 csi800 之前合并**——新旧并存期正是这守卫的主场。

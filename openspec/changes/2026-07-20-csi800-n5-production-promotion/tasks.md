# Tasks: 2026-07-20-csi800-n5-production-promotion

## 0. 提案签署（本 PR）
- [ ] 操作人签 DP-1..DP-6（proposal.md 决策账），签字后冻结

## 1. PR-A — 服务节奏机制 + 两级绑定链工件（唯一 runtime 触点）
- [x] iso-week 再平衡日判定（交易日历驱动：ISO 周第一个交易日；
      跨年周/长假周/单日周确定性测试）——src/inference/rebalance_schedule.py
- [x] daily_recommend 输出工件增 `rebalance_day` +
      `next_rebalance_date` 字段 + 非再平衡日 HOLD 提示（schema
      追加字段；如与 artifact schema v2 requirement 原文冲突则该
      PR 内 MODIFIED 全文重述）
- [x] **operator UI 决策页 HOLD reader 同 PR 落地**（codex #385
      r5：读 `rebalance_day`，HOLD 工件显示 HOLD 状态 +
      next_rebalance_date + 入场表单阻断；旧工件无字段向后兼容）
      + 测试双态
- [x] **iso_week 复核 preset 落地**
      （`csi800_cadence5_conservative_isoweek.yaml`，纯配置文件，
      先于其复核 run 入库——codex #385 r2：两级链的比较对象必须
      在一级测试之前存在）+ **一级治理测试**：该 preset vs 胜者
      preset 恰差 {rebalance_anchor, output_dir} 钉死
- [x] csi800 服务参数落地（config/serving/csi800_n5_production.yaml，
      语义经两级绑定链锚定；服务侧白名单差异字段写死）
- [x] **二级治理测试**：服务参数 vs iso_week 复核 preset 恰差
      白名单 pin（tests/governance/test_csi800_n5_production_serving.py）
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 2. PR-B — iso_week 复核 run + 候选训练 + guard eval（结果盲）
- [x] iso_week 复核 run 单发串行（7b 胜者复核切片落地，消费
      PR-A 已入库的 preset）；判据跑前钉死：净超额年化 > 0
      （毛/净差如实入档为诊断披露）——codex #385 r1：锚漂移
      证据前置于生产绑定生效。gen2 干净树跑于 main 4df3109
      （首跑 dirty 树留证 *_gen1_dirtytree/，结果盲保持）
- [x] **复核证据入库**（docs/research/evidence/csi800_n5_runs/
      csi800_cadence5_conservative_isoweek/，本 PR 并主线 = 锚成立）：
      聚合 report + 逐折 reports + positions 本体（codex #385 r3：晋升门经
      origin/main 锚 git show 读取并验证 config 绑定 preset +
      净值从锚上重导，本地未锚定输出拒绝）
- [x] 训练配置定稿（csi800_n5_candidate.yaml：④ 镜像窗——guard
      洁净约束下"最新可用-embargo"的解；治理窗口 pin 入档）
- [x] **训练点火 = 用户执行（GPU，阶段6 先例，2026-07-21 授权）**；产物 =
      候选 pkl + trainer sidecar（仅训练 provenance——codex #387
      r2：fit_*_for_inference 属 inference meta，由 PR-C 晋升执行
      按 ④ 先例写入；guard eval 以显式 --fit-start/--fit-end 传
      preset 预注册窗，不依赖 inference meta）
- [x] guard 窗日期跑前钉死：2025-07-01..2026-06-12（= ④ 已提交
      comparison-origin 窗，候选未训未验于此；治理 pin）
- [x] frozen guard eval（eval_frozen_model_oos 同族口径升级
      csi800/SH000906TR/N5/20bps）跑毕——数字保持未读直至 PR-C
      数字 STOP（已呈报：gate C-4 净 −2.14% FAIL）
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

## 3. PR-C — 晋升执行（数字 STOP）【R1 废止：gate C-4 FAIL 实证
（PR #388 入档），本节由 §5 R1 分段取代；下列未勾项不再执行】
- [ ] 晋升工具执行前置校验：侧车 --verify 通过 +
      promotion_eligible: true + iso_week 复核门过线（否则拒绝；
      零写入限于 canonical 本体，失败记录照常写入——codex #385 r2）
- [ ] guard eval 硬 veto 勾验（0 degenerate/0 straddle/净>0/五 veto
      数字沿 canonical spec）
- [ ] pre-promote 备份（pkl+meta 带时间戳）+ docs/promotion/ 新
      baseline json + 现任基线保留；**写候选 inference meta**
      （<canonical>.meta.json 含 fit_*_for_inference/train_window/
      promoted_at，④ 先例——serving fail-loud 依赖它）
- [ ] canonical pkl + meta 替换；runbook 修订为周节奏操作卡
      （含观察期纪律与 73bps 盈亏平衡参考）
- [ ] **数字 STOP**：guard eval 全部数字首次呈报 → codex/CI →
      用户 merge = 晋升执行完成
- [x] 若任一门不过：如实入档不晋升，现任不动，处置另行提案
      （**已触发**：gate C-4 冻结候选 guard 窗净 −2.14%≤0；诊断=
      冻结/协议结构性错配+guard 年协议级弱年；guard eval 工件+
      简报入库，canonical 零写入；选项 1=协议对齐另行提案）

## 4. 收束
- [ ] 观察期起点记录（首季度只记录不回调）
- [ ] 战役记忆/runbook 终稿同步 → `/opsx:archive`


## 5. R1 协议对齐分段（2026-07-21 签，替代 §3 执行路径）

### PR-A' — ensemble 服务机制（runtime 触点）
- [ ] daily_recommend 多模型 ensemble 消费（serving manifest 列
      三成员 pkl+meta，apply_ensemble 同语义打分；缺员/断链
      fail-loud 拒绝出单，绝不静默降级）
- [ ] serving manifest schema + 治理绑定扩展（manifest 成员窗口
      算术 pin：24m 滚动+3m valid+错峰终点）
- [ ] 测试：三成员合成 ensemble 等价性/缺员拒绝/断链拒绝/单模型
      旧路径字节不变
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

### PR-B' — per-retrain 门工装 + 轮换执行器
- [ ] retrain gate 工具：五门(trainer 完整性/退化/约束干跑/IC 方向/
      serving veto 面②③⑤)产出机读 gate 工件；任一不过=成员不入
      ensemble+如实入档;连续两季不过=操作人决策点
- [ ] veto③ 参照:锚上 isoweek 复核换手均值(git show 读取)
- [ ] **轮换执行器**(codex #389 r2/r3/r4)：认证有效性前置校验——
      仅 git show 读单一状态工件 csi800_recert_status.json 内容
      判 verdict(LOSE 即冻结零写入+升级决策点;WIN 状态合并恢复;
      零日期/拓扑推断);有效期=状态工件路径主线 tip commit 日期
      +15 个月 + gate 工件消费 + pre-rotation manifest 备份
      (单步回退) + 轮换落 manifest；测试:过期冻结/LOSE 冻结/
      WIN 恢复/备份在场/回退单步/合法轮换全链/侧车路径非年检
      触碰不影响判定
- [ ] **年检状态工件义务**(codex #389 r3/r4)：单一状态工件
      schema(verdict/WIN 侧车内容哈希/证据锚/判定说明)+仅年检
      流程可改的治理钉守+首次自举写初始 WIN 状态+runbook 年检
      操作卡
- [ ] runbook 修订:季度重训操作卡+周节奏服务卡+观察期纪律+年度
      再认证义务
- [ ] codex review 循环 + CI 绿 → STOP 等 merge

### PR-C' — 自举点火 + 切换执行（数字 STOP）
- [ ] 三名错峰成员训练配置定稿(训窗终点 T-6m/T-3m/T,各 24m+3m,
      跑前钉死具体日期)
- [ ] **三发 GPU 点火 = 操作人执行,严格串行**
- [ ] 三成员各过 per-retrain 门(gate 工件入库)
- [ ] 切换执行:pre-promote 备份+三成员 inference meta+serving
      manifest+baseline json(④ 先例)+**初始 WIN 状态工件**
      (codex #389 r7:csi800_recert_status.json 首写——verdict WIN
      +现行 #383 verdict 侧车内容哈希引用+证据锚,与切换同 PR 入库;
      缺此项轮换执行器读无有效状态会误冻结首次季度轮换)
- [ ] **数字 STOP**:三成员门工件+valid 窗数字首次呈报 → codex/CI
      → 用户 merge = 切换完成,观察期起点记录
- [ ] 既有候选(run 20260721_195924)如实弃置(训窗与错峰算术不符)

### 收束
- [ ] 首季观察期报告义务入 runbook;年度再认证日历锚定
- [ ] 战役记忆终稿 → `/opsx:archive`

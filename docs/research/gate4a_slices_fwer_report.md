# Gate-4A 切片 + 全批 FWER 裁决报告（quality_profitability_v1）

> 决策级结果**原样记录**（ledger E017-E022 预登记 / E023-E028 结果登记）。
> 本报告 = 冻结三态 verdict 规则的**裁决输入**；操作人终裁；绝不 auto-pivot。

## 全批 FWER 裁决输入：**CLEAN_NEGATIVE（干净阴性）**

签署机制（ledger pin 2026-07-16）：N=9，trial t=折级 rank_ic 序列 t；
联合 moving-block bootstrap（共享位置重采保留族内相关），block=4，
B=10000，seed=20260716，单侧正向；通过=t > q95 bar **且** t≥2.85 硬地板。

| trial | n | rank_ic_mean | t |
|---|---|---|---|
| C1_GPA（主） | 19 | −0.0392 | −0.82 |
| C2_PROF（主） | 19 | −0.0414 | −1.23 |
| C3_cash_based_OP（主） | 19 | −0.0252 | −0.62 |
| C1_from_2018 | 23 | −0.0081 | −0.19 |
| exclude_fold_0（E018 派生） | 18 | — | −1.50 |
| holding_semiannual | 9 | −0.0662 | −1.08 |
| holding_annual | 4 | −0.0657 | −0.60 |
| st_off | 19 | −0.0392 | −0.82 |
| size_decile_variants（五分位） | 19 | −0.0416 | −0.87 |

- **max 观测 t = −0.19**（C1_from_2018）——九 trial 全负，距 2.85 硬地板
  与 bootstrap bar（+14.485）双门槛皆远。
- 工件：`output/gate4a/fwer_20260716T011428Z/`（verdict.json/md），
  五切片 run 工件见 ledger E023-E027（各自 gate ACCEPT 存档在内）。

## 如实观察

1. **没有任何口径挽救信号**：放宽会计 regime（from_2018）、剔除 COVID
   反弹折（exclude_fold_0 反而更负，−1.50——fold_0 是全族最大正折，
   删它使均值更负）、拉长持有期（半年/年）、关 ST 过滤、改分位粗细——
   九个注册视角一致为负。
2. **st_off 实证空转**：ST 掩码在 csi300_pit_ex_financials 上全折零命中
   （成分股几乎不 ST），切片与主 run 逐位一致——记录该事实，说明 st_on/
   off 在本宇宙不构成敏感性维度。
3. **bootstrap bar (+14.485) 显著偏高的机制注记**：稀疏 trial
   （holding_annual n=4）在块重采下会抽到重复位置 → 微小方差 → null
   max-t 重尾。签署的 2.85 硬地板正是有效下界；本裁决全负，不受影响。
   机制如实记录，不作事后改动（签署 pin 不回改）。
4. exclude_fold_0 佐证 C1 主 run 的表面均值被 2020Q2 反弹折拉升：
   删除后 t 从 −0.82 恶化到 −1.50。诊断性结论：C1 的"接近零"不是
   被压制的信号，而是正折集中于单一 regime 事件。

## 按冻结 verdict 规则的读数

`reject_iff: 干净阴性 — 无候选过 4A` 成立于本裁决输入：**盈利质量
"原料"方向（C1/C2/C3 及全部注册变体）在 dev 窗无可辨别正向选股信息**。
这是与降频/阶段6/阶段7b 同类的有效排除性结果。per 冻结规则:
不扩搜索、不改公式、不 auto-pivot；4B/4C 的 `when: only_if_4B_success`
链条自然不点火；**2025 holdout 保持未揭盲**（holdout_unblinded=false，
无候选可送终裁）。终裁语义与后续方向由操作人裁定。

## 溯源

五切片 run 均 gate ACCEPT@clean main（#360 后,冻结包最晚 commit
2026-07-16T08:40+08 附近,各工件 gate_accept.txt 存全文）;评估器 =
#354+#355+#358+#360;FWER 脚本 = 本 PR（签署机制单测 5 项覆盖）。

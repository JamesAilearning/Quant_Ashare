# 阶段8 Gate-3 · 冻结 preflight —— manifest / folds / holdout(待签)

> **状态:** ✅ 已签。操作人于 2026-07-13 签定候选 **(a) 2025-01-01→2025-12-31**
> (ledger E009);`<TO_FILL@freeze>` 已回填;随冻结 commit 一并入库。
> **配套:** `quality_profitability.yaml` / `_ledger.yaml` / `_rehearsal.md`。

## 1. 数据 manifest(已产出)

- 生成器: `scripts/research/gate3_store_manifest.py`(fail-loud;`--verify` 模式
  供 rehearsal R5 与每次决策级 run 前的 gate 复核)。
- 产物: `docs/prereg/quality_profitability_store_manifest.json`
  —— **1880 文件**(income 627 / balancesheet 626 / cashflow 627),
  aggregate sha256 = `4560e8536524e4a0…`(完整值见 json)。
- 语义: 冻结即锁死这份 store 字节;任何 re-ingest/篡改 → R5 拒绝。

## 2. Folds(定案 —— 镜像 canonical,两臂共享)

复用 `config_walk.yaml` 的 canonical 网格,**不另造折窗**(4B 两臂必须共享折窗,
`permitted_config_diff: [quality_factor_only]`):

- anchor `2018-01-01`;**24m train + 3m valid + 3m test,步进 3m → 23 折**;
- fold_0 test = 2020-04→06(COVID,预注册敏感性 `exclude_fold_0`);
- 末折 test = 2025-10→12;embargo/n_drop = canonical(#212 纪律)。

**质量特征窗口注**: 质量因子 as-of 序列自 2019-01-01 起(决策②,regime 一致性);
更早月份该特征列为 NA(LightGBM 原生 NA 处理,缺失策略照 yaml,绝不填)。
主判仅统计 test 窗在 dev 区间内的折(见 §3);2026H1 数据(bundle 日历至
2026-06-17)**整体不入本研究**——canonical 网格止于 2025-12,扩界即破坏两臂共享。

## 3. Holdout 候选(待操作人签 —— 三选一)

untouched_final_holdout:冻结后至最终裁决前**绝不触碰**;dev 折供 4A/4B 开发判定。
三候选均为整年/整半年边界(clean-of-results 时点划定,无缝可挑):

| 候选 | holdout 窗 | holdout 含季度再平衡次数 | dev 折(test 窗) | 权衡 |
|---|---|---|---|---|
| **(a) 推荐** | **2025-01-01 → 2025-12-31** | **4** | fold_0–18(2020Q2→2024Q4,19 个季度) | 平衡: holdout 4 次再平衡对季度主持有期是最小充分;dev 保留含 2024 年 regime 的 19 折;2025=最近完整年,裁决最贴近当下 |
| (b) | 2025-07-01 → 2025-12-31 | 2 | fold_0–20(→2025Q2,21 折) | dev 最厚,但 holdout 仅 2 次再平衡 —— 对季度 cadence 太薄,终裁 CI 宽到近乎无效 |
| (c) | 2024-07-01 → 2025-12-31 | 6 | fold_0–16(→2024Q2,17 折) | 终裁最强(6 次再平衡),代价是 dev 少 2 个季度且 2024H2-2025 regime 完全不进开发 |

**推荐 (a)**。理由: 季度主持有期下 holdout 的统计单元是"再平衡期",(b) 的 2 个
单元撑不起 paired 净超额 CI;(c) 更稳健但 dev 丢掉最近 regime,对"质量因子是否
在当下 A 股仍有效"的判定反而失真。(a) 是两者的中点,且整年边界无 game 空间。

**防 game 声明**: 本候选划定于任何因子值/回测结果产生之前(clean-of-results,
git 时间戳可证);三候选均为日历整界;签字后写死进 yaml,`prohibited_variants`
禁止事后改窗。

## 4. 签字后的收尾清单(冻结第 2-4 步)

1. rehearsal 六场景真跑 → 6/6 PASS(任何 FAIL 先修门);
2. 回填 yaml `<TO_FILL@freeze>`: manifest 指针(§1)、window.end_boundary
   (= 所签 holdout 起点前一交易日)、folds(§2 定案)、holdout(所签项);
   ledger 追加 manifest/folds/holdout/freeze 四条目;
3. 五件(yaml/ledger/rehearsal/preflight/manifest.json)+ manifest 脚本
   一并 commit 盖 git 时间戳 = 预注册生效;
4. 之后才可点火 Gate-4A。

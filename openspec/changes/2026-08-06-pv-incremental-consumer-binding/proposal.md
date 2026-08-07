# Proposal: 消费方强制注册绑定（评估器 / 裁决器）

Stacks on #402（注册器）。

## Why

#402 让注册器把冻结批次的证据都记了下来——manifest 字节摘要、GP 输入
（池/表达式/基线）摘要。但**记录不等于强制**（codex #402 r6 的两条 P1，
当时因本 PR 范围限制未修）：

1. `pv_incremental_eval.py` 接受任何 `--baseline-preds`,从不与注册记录
   的基线摘要比对——可以用基线 A 繁殖、拿基线 B 裁决增量性；同一冻结
   模型的两份 provenance 合法导出各自都成立,正因如此身份必须按**摘要**
   而非模型名核验。
2. 注册后到 OOS 评估之间若 `candidates.json` 被编辑/替换,两个消费方都
   不校验字节——独占创建只挡第二个注册器,挡不住事后修改,"冻结的 family"
   可以悄悄变。

两者都让冻结停留在名义上,而一次性 OOS 窗口的全部意义正建立在冻结之上。

## What Changes

- 新增 `scripts/research/pv_incremental_registration.py`（消费方共享）：
  `load_registration`（sidecar 必在 / 协议恰等 / 摘要合法 / **manifest
  当前字节须等于注册时摘要**）+ `assert_baseline_matches_registration`
  （所传基线摘要须等于注册记录的挖掘基线摘要）。**无逃生口**——未注册
  的清单不可评估、不可裁决。
- 评估器 `main`：读注册 → 校验 manifest 字节 → 校验基线身份，任一不符
  转 `PVEvalError` 拒。
- 裁决器 `main`：读注册 → 校验 manifest 字节；判决工件增记
  `registration_manifest_sha256`（可溯）。
- 顺带修 #402 的一个真 bug（被本 PR 新增的端到端测试当场抓到）：注册器
  对 payload 算摘要却以文本模式写盘，Windows 上 LF→CRLF 使**盘上字节与
  摘要不符**，于是任何注册在本机平台都通不过消费方校验。写入改
  `newline=""`。该 bug 在 #402 单独存在时无外部可见后果（当时没有任何
  东西校验那个摘要），本 PR 引入校验即暴露，故修在此处。

## Impact

- Affected specs: `v2-factor-mining-foundations`（ADDED 一条 Requirement）。
- Affected code: 新增共享模块 + 两个消费方各一处接线 + 注册器写入模式。
- **行为变更**：未注册（无 sidecar）的清单不再可被评估/裁决——这是本 PR
  的要义,不设开关。
- 点火不在本 PR。

# 无解配置必须立刻拒绝，而不是指数级重试

## 问题（实测，不是推断）

`grammar.py` 的 `_gen` 在**每一层深度**重试 `MAX_OP_RETRIES = 10`，而子树生成
写在 `try` **内部**：

```python
for _ in range(MAX_OP_RETRIES):
    op, input_types = rng.choice(candidates)
    try:
        children = tuple(_gen(t, max_depth - 1, ...) for t in input_types)
        return OperatorCall(op.name, children)
    except GrammarError:
        continue
```

于是当终端白名单**无解**时，一次顶层调用最坏走 `10 ** max_depth` 次 `_gen`。
默认 `max_depth=6` → **10⁶**。

本机实测（`pytest tests/logic/factor_mining/ --durations`）：

```
2327.77s  test_pv_incremental_fitness.py::TerminalWhitelistTests::
          test_unusable_whitelist_fails_loud_not_empty_pool
 107.10s  test_gp_engine.py::test_gp_converges_on_toy_ma_crossover_target
  13.12s  test_miner.py::test_run_mining_with_pool_top_k_truncates
          （其余 717 个用例全部 < 12 秒）
```

**一个用例 38.8 分钟**，占该子目录 41:41 的 93%。它只是把病理情形显式化了：
`population_size=4` → 顶层重试 `4 × 50 = 200` 次，`2327.77 / 200 ≈ 11.6 秒`
一次失败尝试，与 10⁶ 的量级吻合。

## 为什么现在才炸

`grammar.py` 里那段注释写着：

> *the rejection rate of the trivial form is < 1% in practice so the retry
> budget is never exhausted*

这个前提在 #437（`grammar.py +135` / `evaluator.py +265`，扩终端与算子）之前
成立。候选池变大、taint 约束更易走不通之后，指数就爆了 —— **没有人碰过那个
测试**，CI 的 `tests/logic/` 步骤自己从 5.1 分钟变成 ~50 分钟：

```
2026-08-15T23:22   5.1 分   112c6252  (#433)
2026-08-16T01:06  50.1 分   a852dfab  (#437)   ← 跃变点
…此后稳定 40–52 分
```

## 影响面：不只是测试

指数级重试在**生产代码**里。任何战役只要白名单/taint 组合让某个子树无解，就
付 `10 ** depth`；真实 `population_size` 默认 500，量级远大于测试里的 4。

## 方案

在 `random_expression` 入口做一次**静态可满足性预检**，无解则立刻
`GrammarError`。

**硬约束(本 change 的核心)**:

1. **只在可证明无解时拒绝**。预检是**保守**的 —— 判不准就照旧走原路径。
   拒错一个可生成的配置 = 悄悄缩小 GP 搜索空间,那是治理级错误,比慢严重。
2. **不得扰动 rng 抽取序列**。预检在任何 `rng` 调用**之前**完成,且可满足时
   一次 rng 都不动 —— 种子可复现是本仓钉死的不变量
   (`test_run_mining_same_seed_identical_pool`)。
3. 失败仍是 `GrammarError`,消息仍说清「白名单容不下什么」—— fail-loud 不变,
   只是不再花 10⁶ 步才说出口。

## 不做什么

- **不动 `MAX_OP_RETRIES = 10`**:那是给「构造器后置校验偶发拒绝」留的余量,
  与本问题正交,改它会动到可满足路径的 rng 序列。
- **不动测试的 `population_size`**:那是掩盖症状。

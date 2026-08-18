# 加固幸存者池的确定性与"无法比较"的失败方向

## Why

晋升门的**处理臂**是 `filter_correlated` 产出的幸存者池。起草基本面战役
预注册协议时要冻结"池怎么构成"，逐项对照实现才发现：**这个函数今天并不
确定，而且它的失败方向是宽松的**。三条缺陷都由 PR #446 的 codex 评审
（r9/r10/r11）实证驱动，与那份协议本身无关 —— 它们影响**每一条战役线**，
因此拆出来单独走本 change。

三条事实：

1. **tie-break 用了 Python 加盐 hash。** `filter_correlated` 的扫描顺序是
   `(-fitness, expr_hash)`，而 `expr_hash = hash(expr)` 跨解释器进程 /
   `PYTHONHASHSEED` 变化 —— `factor_pool.py` 自己就记着这句。顺序决定
   **两个同分且互相相关的幸存者谁先入池**，于是同一批工件在两次执行里
   可以留下不同的池。晋升裁决的处理臂因此不可复现。

2. **"没滤成"被当作"滤过了"。** 求值抛出非 `KeyError` 异常、或返回非
   DataFrame 时，该因子被**保留**并继续参与后续裁决 —— 相关性过滤在宽松
   方向被静默跳过。同理，`max_abs_corr` 对联合有限观测不足 `min_overlap`
   的配对**跳过并贡献 0.0**，与"真正不相关"无法区分；季频、覆盖率不齐的
   财报因子尤其容易触发。

3. **一次 resume 可以横跨两个搜索空间。** `run()` 无条件用当前面板重设
   `_allowed_terminals`，而已有种群被保留。窄面板 checkpoint 恢复后用宽
   面板 resume，后续世代能碰到早先世代根本够不着的终端；`score_expression`
   同样会从"这次调用碰巧传进来的面板"推导池，给那次 run 育不出的表达式
   出分。

## What Changes

- **`validator`**：新增 `canonical_expr_digest`（表达式规范串的 sha256）
  与 `ValidationError`；`filter_correlated` 的扫描顺序改用稳定摘要；求值
  失败、非 DataFrame 返回、以及**任何不可比配对**一律 `ValidationError`
  拒绝，不再保留因子。
- **`evaluator`**：新增 `max_abs_corr_with_skips`，同时返回不可比对数
  （重叠不足与退化相关都计入）。`max_abs_corr` 的**签名与返回类型**不变、
  内部委托，但**取值会变** —— 相关性现在先按 `np.isfinite` 过滤再判重叠
  与计算，故三处调用方（GP novelty penalty、`FactorPool.correlation_with`、
  validator 池过滤）在存在 `inf` 的场景下拿到不同的数（见下方
  「共享路径的取值变更」）。
- **`gp_engine`**：新增 `_has_run` 标记并随 checkpoint 持久化 `has_run` /
  `allowed_terminals`；`score_expression` 对注入 AST 校验终端池（与既有的
  算子池校验对称）；`run()` 拒绝会改变已建立池的 resume，以及引用池外终端
  的预填种群。

## Impact

**共享面（这是本 change 单独成立的理由）**：

| 组件 | 谁在用 | 本 change 的影响 |
|---|---|---|
| `filter_correlated` | 每条战役的晋升过滤（`promote.promote_run`） | 排序稳定化；失败由"保留"改为"拒绝" |
| `max_abs_corr` | GP novelty penalty、validator 池过滤、`FactorPool.correlation_with` | 签名/返回不变，新增可选的带跳过计数版本 |
| `GPEngine.run` / `score_expression` / checkpoint | 所有挖掘 run、resume、以及 `starter-check` 这类审计路径 | 池一旦建立即不可放宽 |

**行为变更的方向**：从"静默宽松"改为"fail-loud"。这会让**此前被静默保留
的因子**现在使 promote 直接失败 —— 那正是意图：一个无法确定性构造的池不
应该被拿去裁决。全量 4287 passed 证实没有既有调用方依赖被移除的吞咽路径
（该路径在正常运行中从未触发过，这正是"静默"的含义）。

**共享路径的取值变更（合并前须知）**：`max_abs_corr` 此前让 `dropna` 留下
±inf，整对的相关因此变 NaN 并贡献 `0.0` —— 一个 inf cell 就把一段真实的
强相关抹成"零相关"。对三个消费者这都是**错的方向**：GP novelty penalty
把冗余因子当新颖而少罚、池过滤少剔、`correlation_with` 低报。现在先按
`np.isfinite` 过滤再判重叠与相关，有限观测足量时照常计算；T2-3 的原保证
（永不返回非有限值）原样成立。含义：novelty penalty 与池过滤的数字会在
存在 inf 的场景下变化 —— 变得更准，但确实是变化。

**破坏性变更（合并前须知）**：早于本 change 的 checkpoint 不记录终端池，
一律**拒绝加载**（而非当作全新引擎 —— 那会跳过池守卫，是宽松方向）。
代价评估：pv 战役已收束、链路验证是一次性 run，无在途长跑依赖它们；
需要续跑的场景走 re-mine，与 PIT 内容绑定对无凭出处的处置一致。

**不在本 change 范围**：基本面战役的预注册协议本体（`fundamental_gp_v1`）
及其终端白名单机制 —— 那些在 PR #446，stack 在本 change 之上。

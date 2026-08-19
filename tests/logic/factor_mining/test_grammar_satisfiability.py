"""无解配置必须立刻被拒，而不是指数级重试（openspec 2026-08-18-gp-unsatisfiable-fail-fast）。

`_gen` 在每层深度重试 MAX_OP_RETRIES=10，且子树生成在 try 内部，所以白名单无解
时一次顶层调用最坏 10 ** max_depth 步（默认 6 → 10⁶）。实测一条回归用例因此跑
38.8 分钟，占 factor_mining 子目录 41:41 的 93%，CI 的 tests/logic 步骤也被它从
5.1 分钟拖到约 50 分钟。

这里钉的**不是速度**，是那次修复必须同时守住的两条红线：预检绝不能拒掉本可生成
的配置（那会悄悄缩小 GP 搜索空间，比慢严重），也绝不能扰动 rng 序列（种子可复现
是本子系统钉死的不变量）。
"""

from __future__ import annotations

import itertools
import random
import sys
import unittest
from pathlib import Path
from random import Random

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.factor_mining.grammar import (  # noqa: E402
    ExprType,
    FeatureRegistry,
    GrammarError,
    _gen,
    _provably_unsatisfiable,
    random_expression,
)

_TARGET = ExprType("CSF", "PURE")


class RefusesUnsatisfiableUpFront(unittest.TestCase):
    def test_a_whitelist_matching_nothing_is_refused(self) -> None:
        with self.assertRaises(GrammarError) as caught:
            random_expression(
                _TARGET, max_depth=6, min_depth=2, rng=Random(1),
                allowed_terminals=frozenset({"$nonexistent"}),
            )
        # 仍然 fail-loud，且说清是白名单的问题。
        self.assertIn("frozen field set", str(caught.exception))

    def test_the_refusal_is_immediate_not_after_the_retry_budget(self) -> None:
        # 不计时（CI 机器速度不可控），而是数**生成器被调用了几次**：预检生效
        # 时一次都不该走到 _gen。
        calls = 0
        real_gen = _gen

        def counting(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return real_gen(*args, **kwargs)  # type: ignore[arg-type]

        from unittest import mock

        with mock.patch("src.factor_mining.grammar._gen", counting):
            with self.assertRaises(GrammarError):
                random_expression(
                    _TARGET, max_depth=6, min_depth=2, rng=Random(1),
                    allowed_terminals=frozenset({"$nonexistent"}),
                )
        self.assertEqual(calls, 0, "无解配置不该进入采样递归")


class NeverRefusesSomethingGeneratable(unittest.TestCase):
    """假拒绝是**治理级**错误：池子会与预注册的实验不符。"""

    #: 对照用的深度。**故意取小**：建立「事实」那一侧要直接调 `_gen`，而无解
    #: 白名单在那条路径上仍是 10 ** depth —— 深度 6 会让这条守护用例自己跑
    #: 三分钟，那就成了它要防的那种测试。
    #:
    #: **必须含 0**：codex #452 抓到的假拒绝正是 `max_depth=0` —— 无叶类型
    #: (CSF) 在 depth 0 仍会走 `_random_operator`，`cs_winsorize($circ_mv)`
    #: 真的生成得出来。我上一版只测了 depth=6，整条深度维度是盲区。
    _DEPTHS = (0, 1, 2, 3)

    def test_verdict_agrees_with_reality_across_whitelist_subsets(self) -> None:
        terms = sorted(FeatureRegistry.V1)
        subsets: list[frozenset[str]] = []
        for size in range(0, 3):
            subsets += [frozenset(c) for c in itertools.combinations(terms, size)]
        rnd = random.Random(7)
        for _ in range(12):
            size = rnd.randint(3, len(terms))
            subsets.append(frozenset(rnd.sample(terms, size)))
        subsets.append(frozenset({"$nonexistent"}))

        false_refusals = []
        refused_count = generatable_count = 0
        for allowed in subsets:
            refused = _provably_unsatisfiable(_TARGET, allowed, None)
            refused_count += int(refused)
            for depth in self._DEPTHS:
                produced = False
                for seed in range(5):
                    try:
                        _gen(_TARGET, depth, min(2, depth), Random(seed),
                             allowed, None)
                        produced = True
                        break
                    except (GrammarError, ValueError):
                        continue
                generatable_count += int(produced)
                if refused and produced:
                    false_refusals.append((sorted(allowed), depth))
        self.assertEqual(
            false_refusals, [],
            f"预检拒绝了实际可生成的白名单: {false_refusals[:3]}",
        )
        # 防空转:样本里必须**两侧都有** —— 全是「可生成」的话，这条用例
        # 根本没检验拒绝路径。
        self.assertGreater(refused_count, 0, "样本里没有无解白名单")
        self.assertGreater(generatable_count, 0, "样本里没有可生成白名单")

    def test_depth_zero_leafless_target_is_not_refused(self) -> None:
        # codex #452：max_depth=0 时 CSF 无叶，`_gen` 仍走 `_random_operator`，
        # 子节点拿 max_depth-1 照样取叶子 —— cs_winsorize($circ_mv) 是 depth 0
        # 下真实生成得出来的。按 max_depth 封顶的可达性会把它误判成无解。
        self.assertFalse(_provably_unsatisfiable(_TARGET, None, None))
        produced = None
        for seed in range(8):
            try:
                produced = _gen(_TARGET, 0, 0, Random(seed), None, None)
                break
            except (GrammarError, ValueError):
                continue
        self.assertIsNotNone(produced, "depth 0 本就应当生成得出来")

    def test_a_single_taint_whitelist_still_generates(self) -> None:
        # 注释里点名的合法情形：白名单只admits一种 taint，于是部分算子候选的
        # 输入池为空 —— 靠重试换候选仍应成功，预检**不得**拦它。
        seven = frozenset(sorted(FeatureRegistry.V1)[:7])
        self.assertFalse(_provably_unsatisfiable(_TARGET, seven, None))
        self.assertIsNotNone(
            random_expression(_TARGET, 6, 2, Random(3), seven, None)
        )


class DoesNotDisturbTheRngSequence(unittest.TestCase):
    def test_the_precheck_consumes_no_randomness(self) -> None:
        # 可满足配置下，预检前后 rng 状态必须一致 —— 它一个数都不许抽。
        rng = Random(11)
        before = rng.getstate()
        _provably_unsatisfiable(_TARGET, None, None)
        self.assertEqual(before, rng.getstate())

    def test_seeded_generation_is_unchanged(self) -> None:
        # 同种子重复调用必须逐字节相同（跨实现的对比在 PR 里做过；这里钉住
        # 「同种子 → 同结果」这条不变量本身）。
        seven = frozenset(sorted(FeatureRegistry.V1)[:7])
        for allowed in (None, seven):
            with self.subTest(allowed="none" if allowed is None else "seven"):
                first = [str(random_expression(_TARGET, 6, 2, Random(s), allowed, None))
                         for s in range(10)]
                again = [str(random_expression(_TARGET, 6, 2, Random(s), allowed, None))
                         for s in range(10)]
                self.assertEqual(first, again)


if __name__ == "__main__":
    unittest.main()

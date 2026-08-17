"""结果页的口径标注（UI drift 审计 P2，经独立复核后按实证落地）。

复核实测（run 20260805_094309_265470_cdfdb033_91f434349725）：

* 主指标 / 总收益 / 净值曲线 / 月度收益都读自 nav 的 ``strategy_return``，
  它源出 qlib ``report_normal["return"]``，而 qlib 把成本**加回**了已扣成本
  的收益（``account.py:283`` 的 ``(now_earning + now_cost) / …``）——所以是
  **绝对毛**。本仓库自己要净口径时必须手工再减一次
  （``backtest_runner.py`` 的 ``- report_normal["cost"]``）。
* 风险卡的最大回撤 −22.51% 与正下方回撤图的 −26.46% **不是同一个数**，
  9 个有完整产物的 run 全部不一致。差异来自三处：减不减基准、扣不扣成本、
  算术累计（qlib ``risk_analysis`` 默认 ``mode="sum"``）还是几何累计。
  **成本是其中最小的一项**（该 run 上只解释 21bp，而两数差 395bp）——所以
  标注不能像原审计那样把差异说成「扣没扣费」，那会让操作人更糊涂。
* IR 与它上方的主指标口径相反（IR 是扣费后超额），并排摆着必须各自标注。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_RENDER = PROJECT_ROOT / "web" / "operator_ui" / "pages" / "_results_render.py"


class CostConventionLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = _RENDER.read_text(encoding="utf-8")

    def test_primary_metric_is_labelled_gross_and_absolute(self) -> None:
        # 「毛」单说不够：读者还需要知道它也没减基准。
        self.assertIn("绝对**毛**口径", self.src)
        self.assertIn("未减基准、未扣成本", self.src)

    def test_information_ratio_carries_its_own_convention(self) -> None:
        # IR 与正上方的主指标口径相反，同卡并排必须各标各的。
        self.assertIn("信息比率（IR，扣费后超额）", self.src)

    def test_nav_and_monthly_charts_declare_gross(self) -> None:
        # 净值曲线与月度收益都是绝对毛。
        self.assertGreaterEqual(self.src.count("**绝对毛**"), 3)

    def test_nav_caption_does_not_link_to_the_primary_metric(self) -> None:
        # codex #445 r2: 主指标的口径随分支而变（老工件兜底成扣费后超额），
        # 所以「与收益卡主指标同源」这句在兜底路径下会重新制造矛盾——正是
        # 条件化 help 刚消除掉的那条。图只描述自己的口径。
        self.assertNotIn("与收益卡主指标同源", self.src)
        self.assertIn("源自回测收益序列", self.src)
        self.assertIn("以其各自标签为准", self.src)

    def test_drawdown_mismatch_names_all_three_axes(self) -> None:
        # 关键：不得把差异归因成「扣没扣费」——成本只占约 5%。三处差异
        # 必须都写出来，否则操作人看到 −22.51% vs −26.46% 仍然无解。
        self.assertIn("算术累计", self.src)
        self.assertIn("几何累计", self.src)
        self.assertIn("扣费后超额", self.src)
        # 归因必须成组出现在同一段说明里，而不是散落各处。
        risk_help_at = self.src.index("风险卡片：最大回撤取自")
        risk_help = self.src[risk_help_at : risk_help_at + 600]
        for axis in ("基准", "成本", "算术"):
            with self.subTest(axis=axis):
                self.assertIn(axis, risk_help)

    def test_the_two_drawdowns_are_stated_to_differ(self) -> None:
        # 页面必须明说「本就不相等」，否则读者会以为其中一个是 bug。
        self.assertIn("不是同一个数", self.src)
        self.assertIn("本就不相等", self.src)

    def test_gross_net_sign_flip_lesson_is_stated(self) -> None:
        # 本项目最贵的教训：毛净可以反号（csi800 战役毛 +3.68% / 净均负）。
        self.assertIn("反号", self.src)

    def test_sharpe_carries_the_net_excess_label(self) -> None:
        # codex #445 r1: 夏普与 IR 同源（risk_analysis.excess_return_with_cost），
        # 同样是扣费后超额；不标注就会被当成上方绝对毛主指标的配套。
        self.assertIn("夏普比率（扣费后超额）", self.src)
        self.assertNotIn('f"夏普比率：', self.src)
        # help 里的净超额清单必须把夏普也列进去。
        self.assertIn("扣费后年化超额、信息比率、夏普比率", self.src)

    def test_help_text_follows_the_primary_metric_branch(self) -> None:
        # codex #445 r1: 老工件没有 strategy_annualized_return 时，主指标
        # 兜底成扣费后超额；此时若仍无条件说「主指标是绝对毛」，同一张卡片
        # 自相矛盾。help 必须跟着实际取到的分支走。
        self.assertIn("if strategy_annualized is not None", self.src)
        self.assertIn("旧工件兜底路径", self.src)
        # 兜底分支必须同时说明「总收益/净值/月度仍是绝对毛」——否则读者会
        # 以为整张卡都翻成了净超额。
        fallback_at = self.src.index("旧工件兜底路径")
        block = self.src[fallback_at : fallback_at + 400]
        self.assertIn("仍是**绝对毛**", block)

    def test_walk_forward_summary_convention_note_survives(self) -> None:
        # 既有的正确范本不得被本次改动碰掉。
        self.assertIn(
            "年化收益、回撤、IR 均为**扣费后超额**口径", self.src
        )


if __name__ == "__main__":
    unittest.main()

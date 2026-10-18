"""星级取值范围的契约测试（2026-10-12 线上 400）。

事故：服务端对 `star` 加了范围校验，越界返回 400
（`{"detail": "star 应在 0~5.0 之间"}`）；而 `_save_trade_signal` 里
`add()` 抛异常会让整条信号连同 MQTT 推送、企业微信/短信推送、
**订单创建**一起被跳过（同一个 try 块）。

根因：波段策略卖出侧的星级由 `-final_score / 2` 直接算出、没有夹值。
`final_score` 是「技术分 + 持仓回撤扣分(+账户回撤扣分)」的叠加，
实测最低可到 −12.25（`(枢轴 −5 + 布林 −7.5)/2 −6`），折算得 6.1 星。
同一份折算逻辑当时在四个策略里各写了一遍，漏改一处就静默丢信号。

本文件守住三件事：

1. `BaseStrategy.score_to_star` 是唯一的折算入口，输出恒在 [0, 5]；
2. API 边界 `_as_star` 再截断一次 —— 上游给脏值不该演变成「信号整条丢失」；
3. 所有策略的 star 产出点都走同一个折算函数（源码级守卫，防新增策略又漏夹）。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pandas as pd

import strategy.swing_trading_strategy as swing_module
from api.trade_signal import TradeSignalApi, _as_star
from strategy.base_strategy import BaseStrategy
from strategy.swing_trading_strategy import SwingTradingStrategy


class TestScoreToStar(unittest.TestCase):
    """`BaseStrategy.score_to_star`：10 分制打分 → 0~5 星级。"""

    def test_actual_out_of_range_score_is_clamped(self) -> None:
        """事故现场的真实分值：final_score=−12.25 曾经算出 6.1 星。"""
        self.assertEqual(BaseStrategy.score_to_star(-12.25), 5.0)

    def test_boundary_values(self) -> None:
        """边界逐个确认：−10.0 已经是上限，−9.75 仍在范围内。"""
        cases: list[tuple[float, float]] = [
            (0.0, 0.0),
            (-0.2, 0.1),
            (-9.75, 4.9),
            (-10.0, 5.0),
            (-10.1, 5.0),
            (-18.25, 5.0),
            (9.75, 4.9),
            (10.0, 5.0),
            (20.0, 5.0),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(BaseStrategy.score_to_star(score), expected)

    def test_buy_and_sell_sides_share_one_conversion(self) -> None:
        """买入正分与卖出负分只差符号：`abs(score)/2` 口径一致。"""
        self.assertEqual(
            BaseStrategy.score_to_star(7.5), BaseStrategy.score_to_star(-7.5)
        )

    def test_always_within_range(self) -> None:
        """全区间扫描：任何输入都必须落在 [0, 5]（服务端硬约束）。"""
        score: float = -100.0
        while score <= 100.0:
            value: float = BaseStrategy.score_to_star(score)
            self.assertGreaterEqual(value, 0.0, msg=f"score={score}")
            self.assertLessEqual(value, 5.0, msg=f"score={score}")
            score += 0.37

    def test_non_finite_and_empty_inputs(self) -> None:
        """NaN / None 落 0：NaN 参与比较恒为 False，夹取对它无效。"""
        self.assertEqual(BaseStrategy.score_to_star(float("nan")), 0.0)
        self.assertEqual(BaseStrategy.score_to_star(None), 0.0)  # type: ignore[arg-type]

    def test_one_decimal_place(self) -> None:
        """服务端字段口径是保留一位小数，本端先对齐。"""
        self.assertEqual(BaseStrategy.score_to_star(-9.749), 4.9)


class TestApiBoundaryStar(unittest.TestCase):
    """API 边界 `_as_star`：上游脏值不再演变成整条信号丢失。"""

    def test_clamps_upper_bound(self) -> None:
        self.assertEqual(_as_star(6.1), 5.0)
        self.assertEqual(_as_star(99), 5.0)

    def test_clamps_negative_to_zero_without_sign_flip(self) -> None:
        """只截断、不取绝对值：负数说明上游符号算错，截成 0 并告警。"""
        self.assertEqual(_as_star(-3.0), 0.0)

    def test_passthrough_and_rounding(self) -> None:
        self.assertEqual(_as_star(3.649), 3.6)
        self.assertEqual(_as_star(5.0), 5.0)
        self.assertEqual(_as_star(0), 0.0)

    def test_invalid_values_fall_back_to_zero(self) -> None:
        self.assertEqual(_as_star(None), 0.0)
        self.assertEqual(_as_star(""), 0.0)
        self.assertEqual(_as_star("abc"), 0.0)
        self.assertEqual(_as_star(float("nan")), 0.0)

    def test_add_sends_in_range_star(self) -> None:
        """端到端：越界星级在请求体里已经被截断，不会再打到服务端 400。"""
        client = MagicMock()
        client.post.return_value = {"id": "sig-1"}
        api = TradeSignalApi(client)

        api.add(
            strategy_id="s1",
            user_strategy_id="us1",
            stock_code="600183.SH",
            trade_price=125.12,
            action="卖出",
            star=6.1,
        )

        body: dict[str, object] = client.post.call_args[0][1]
        self.assertEqual(body["star"], 5.0)
        self.assertEqual(body["action"], "卖出")


class TestSwingStarOutput(unittest.TestCase):
    """波段策略的 star 产出：驱动真实卖出路径，用极端分值打到越界区。"""

    def _make_strategy(self) -> SwingTradingStrategy:
        strategy = SwingTradingStrategy()
        strategy.user_strategy_name = "测试波段策略"
        strategy.user_strategy_id = "us-test"
        # 账户回撤在用例里不参与（真实链路由 workflow 注入），避免读 cfg 造成不确定
        strategy.account_drawdown_pct = 0.0
        return strategy

    def _sell_inputs(self) -> tuple[dict[str, object], dict[str, object]]:
        """构造一条会触发卖出的输入：价格 140 → 100，技术分被打成深度负值。

        2026-10-14 起「持仓回撤」分项已由买入信号价止损取代，这里靠账户回撤
        扣分把总分推低，从而驱动同一段 star 折算代码。
        """
        stock_data: dict[str, object] = {
            "code": "600183.SH",
            "name": "测试股",
            "price": 100.0,
            "preclose": 100.0,
            "high": 100.0,
            "create_time": "2026-10-12 10:24:00",
        }
        position: dict[str, object] = {
            "cost_price": 130.53,
            "buy_time": "2026-10-09 09:35:00",
            "quantity": 100,
        }
        return position, stock_data

    def test_sell_star_stays_in_range_at_extreme_score(self) -> None:
        """技术分 −6.25 + 账户回撤扣分 −12.0 = −18.25 ⇒ 星级必须夹到 5.0，而不是 9.1。

        原用例靠「持仓回撤 −6.0」把分值打到越界区；该项已由买入信号价止损取代
        （见 `BaseStrategy.SELL_BUY_PRICE_BREAK_*`），改用更深的账户回撤扣分构造
        同样的越界场景 —— 本用例守的是 star 折算的夹取，与分值来源无关。
        """
        strategy = self._make_strategy()
        strategy.account_drawdown_pct = -15.0
        strategy.SELL_RISK_DRAWDOWN_SCORE = -12.0
        position, stock_data = self._sell_inputs()

        with (
            mock.patch.object(strategy, "_get_minute_cache", return_value=[{"close": 100.0}]),
            mock.patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
            mock.patch.object(swing_module, "InDaySRFactor") as inday_cls,
            mock.patch.object(swing_module, "BollSRFactor") as boll_cls,
        ):
            inday_cls.return_value.score_with_sr.return_value = -5.0
            boll_cls.return_value.score.return_value = -7.5

            triggered, extra = strategy._check_sell_conditions(
                code="600183.SH", position=position, stock_data=stock_data
            )

        self.assertTrue(triggered)
        self.assertAlmostEqual(float(extra["score"]), -18.25, places=2)
        self.assertEqual(extra["star"], 5.0)

    def test_sell_star_includes_account_drawdown(self) -> None:
        """再叠加账户回撤扣分（−6.0）后分数更负，星级仍必须 ≤ 5。"""
        strategy = self._make_strategy()
        strategy.account_drawdown_pct = -15.0
        position, stock_data = self._sell_inputs()

        with (
            mock.patch.object(strategy, "_get_minute_cache", return_value=[{"close": 100.0}]),
            mock.patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
            mock.patch.object(swing_module, "InDaySRFactor") as inday_cls,
            mock.patch.object(swing_module, "BollSRFactor") as boll_cls,
        ):
            inday_cls.return_value.score_with_sr.return_value = -5.0
            boll_cls.return_value.score.return_value = -7.5

            triggered, extra = strategy._check_sell_conditions(
                code="600183.SH", position=position, stock_data=stock_data
            )

        self.assertTrue(triggered)
        self.assertLessEqual(float(extra["star"]), 5.0)
        self.assertGreaterEqual(float(extra["star"]), 0.0)

    def test_buy_star_stays_in_range(self) -> None:
        """买入侧同样走统一折算：技术分打满时也不得超过 5 星。"""
        strategy = self._make_strategy()
        stock_data: dict[str, object] = {
            "code": "600183.SH",
            "name": "测试股",
            "price": 100.0,
            "preclose": 100.0,
            "high": 100.0,
            "create_time": "2026-10-12 10:24:00",
        }

        with (
            mock.patch.object(strategy, "_get_minute_cache", return_value=[{"close": 100.0}]),
            mock.patch.object(strategy, "_get_sr_points", return_value=pd.DataFrame()),
            mock.patch.object(swing_module, "InDaySRFactor") as inday_cls,
            mock.patch.object(swing_module, "BollSRFactor") as boll_cls,
        ):
            inday_cls.return_value.score_with_sr.return_value = 10.0
            boll_cls.return_value.score.return_value = 10.0

            triggered, extra = strategy._check_buy_conditions(
                code="600183.SH", stock_data=stock_data
            )

        self.assertTrue(triggered)
        self.assertEqual(extra["star"], 5.0)


class TestStarConversionNotDuplicated(unittest.TestCase):
    """源码级守卫：star 的折算只允许存在一份实现。"""

    _STRATEGY_DIR: Path = Path(__file__).resolve().parents[1] / "strategy"

    def test_every_star_output_uses_shared_conversion(self) -> None:
        """每个 `"star":` 产出点必须来自 score_to_star / _to_star 或已折算的变量。

        历史形态：四个策略各写一遍 `round(max(0.0, min(-score / 2.0, 5.0)), 1)`，
        漏改一处（波段卖出侧）就让越界星级打到服务端，整条信号丢失。
        """
        offenders: list[str] = []
        for path in sorted(self._STRATEGY_DIR.glob("*_strategy.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if '"star":' not in line:
                    continue
                if "score_to_star" in line or "_to_star" in line:
                    continue
                if line.strip().startswith('"star": star'):
                    # 允许「先折算成 star 变量、再写进字典」，变量本身由下一条规则守
                    continue
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], msg="star 产出点未走统一折算")

    def test_star_variable_is_always_converted(self) -> None:
        """`star: float = ...` 这类赋值必须调用 score_to_star。"""
        offenders: list[str] = []
        for path in sorted(self._STRATEGY_DIR.glob("*_strategy.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                stripped: str = line.strip()
                if not stripped.startswith("star: float ="):
                    continue
                if "score_to_star" in stripped:
                    continue
                offenders.append(f"{path.name}:{lineno}: {stripped}")
        self.assertEqual(offenders, [], msg="star 变量未走统一折算")


if __name__ == "__main__":
    unittest.main()

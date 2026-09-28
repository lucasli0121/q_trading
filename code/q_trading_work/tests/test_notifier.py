"""
Author: liguoqiang
Date: 2026-08-02
Description: 推送类测试 —— 企业微信/微信推送地址解析，以及交易信号推送文案
    的拼装与路由（2026-09-28 起文案改由 TradeNotifier.format_signal_message
    统一生成，含股票名称与推荐星级）。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from api.client import ApiClient
from api.order import OrderApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from trade.notifier import TradeNotifier


def _sample_reason() -> str:
    """用户样例里的触发原因（策略原样给出的因子分值明细）。"""
    return "条件满足: 因子返回的分值满足买入条件, 枢轴点分值:5.00, 布林带分值:5.00"



class TestNotifier(unittest.TestCase):
    def setUp(self) -> None:
        from utils.tools import resource_path
        _cfg_path: str = resource_path("cfg/stock.cfg")
        self._api_client = ApiClient()
        self._api_client.set_fallback_token("e6f13658afb1ddfd5acfb0cf012c2fcefa19baf6b213ac782b8e610309011e2e")
        self._order_api: OrderApi = OrderApi(self._api_client)
        self._strategy_api: StrategyApi = StrategyApi(self._api_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._api_client)
        self._notifier: TradeNotifier = TradeNotifier(
            api_client=self._api_client, config_path=_cfg_path
        )
        return super().setUp()
    def test_notify_push_url(self):
        user_strategy_id: str = "6a662d5028893aa08cf31225"
        result = self._notifier._resolve_wx_push_url(user_strategy_id)
        print(result)

class TestSignalMessageFormat(unittest.TestCase):
    """交易信号推送文案（2026-09-28 用户确认的目标格式）。

    样例：买入002050.SZ(三花智控)，买入价:35.42,买入条件：…，买入推荐: 3颗星
    """

    def setUp(self) -> None:
        from utils.tools import resource_path

        self._notifier: TradeNotifier = TradeNotifier(
            config_path=resource_path("cfg/stock.cfg")
        )

    def test_matches_user_confirmed_sample(self) -> None:
        """与用户给出的目标串逐字符一致（含分隔符位置）。"""
        message: str = TradeNotifier.format_signal_message(
            action="买入",
            code="002050.SZ",
            price=35.42,
            reason=_sample_reason(),
            star=3.0,
            stock_name="三花智控",
        )
        self.assertEqual(
            message,
            "买入002050.SZ(三花智控)，买入价:35.42,买入条件："
            "条件满足: 因子返回的分值满足买入条件, 枢轴点分值:5.00, 布林带分值:5.00"
            "，买入推荐: 3颗星",
        )

    def test_sell_uses_sell_wording_and_keeps_profit_rate(self) -> None:
        message: str = TradeNotifier.format_signal_message(
            action="卖出",
            code="002050.SZ",
            price=36.00,
            reason=_sample_reason(),
            star=4.0,
            stock_name="三花智控",
            profit_rate=1.6372,
        )
        self.assertTrue(message.startswith("卖出002050.SZ(三花智控)，卖出价:36.00"))
        self.assertIn(",卖出条件：", message)
        self.assertIn("，卖出推荐: 4颗星", message)
        self.assertTrue(message.endswith("，收益率:1.64%"))

    def test_fractional_star_keeps_one_decimal(self) -> None:
        """star 由 score/2 得到，会带小数（3.5 星）。"""
        message: str = TradeNotifier.format_signal_message(
            action="买入", code="000001.SZ", price=10.0, star=3.5
        )
        self.assertIn("，买入推荐: 3.5颗星", message)

    def test_zero_star_omitted(self) -> None:
        """拿不到星级时不显示「0颗星」（那会被误读成不推荐）。"""
        message: str = TradeNotifier.format_signal_message(
            action="买入", code="000001.SZ", price=10.0
        )
        self.assertNotIn("推荐", message)

    def test_missing_price_omitted(self) -> None:
        message: str = TradeNotifier.format_signal_message(
            action="买入", code="000001.SZ", reason="条件满足", star=2.0
        )
        self.assertNotIn("买入价", message)
        self.assertEqual(message, "买入000001.SZ,买入条件：条件满足，买入推荐: 2颗星")

    def test_missing_name_drops_parentheses(self) -> None:
        """名称查不到时退化为纯代码，不留空括号。"""
        message: str = TradeNotifier.format_signal_message(
            action="买入", code="000001.SZ", price=10.0
        )
        self.assertTrue(message.startswith("买入000001.SZ，买入价:10.00"))


class TestResolveStockName(unittest.TestCase):
    """股票名称解析：显式传入 > 缓存 > 接口查询，失败一律降级为空串。"""

    def setUp(self) -> None:
        from utils.tools import resource_path

        self._notifier: TradeNotifier = TradeNotifier(
            config_path=resource_path("cfg/stock.cfg")
        )

    def test_explicit_name_wins_without_query(self) -> None:
        with patch("app_context.AppContext") as mock_ctx:
            self.assertEqual(
                self._notifier._resolve_stock_name("002050.SZ", "三花智控"), "三花智控"
            )
            mock_ctx.return_value.stock_info_api.get_by_codes.assert_not_called()

    def test_query_then_cache(self) -> None:
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"name": "三花智控"}
            ]
            self.assertEqual(self._notifier._resolve_stock_name("002050.SZ"), "三花智控")
        # 第二次命中缓存，不再发请求（否则每条信号都要多打一次接口）
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.side_effect = (
                AssertionError("命中缓存后不应再查接口")
            )
            self.assertEqual(self._notifier._resolve_stock_name("002050.SZ"), "三花智控")

    def test_query_failure_degrades_to_empty(self) -> None:
        """未初始化 AppContext / 接口异常都不能影响推送本身。"""
        with patch("app_context.AppContext", side_effect=RuntimeError("no context")):
            self.assertEqual(self._notifier._resolve_stock_name("000001.SZ"), "")
        # 负缓存：失败过的代码不反复重试
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.side_effect = (
                AssertionError("失败结果也应缓存")
            )
            self.assertEqual(self._notifier._resolve_stock_name("000001.SZ"), "")


class TestNotifyRouting(unittest.TestCase):
    """notify 的路由：传结构化字段则重排文案，否则原样推送。"""

    def setUp(self) -> None:
        from utils.tools import resource_path

        self._notifier: TradeNotifier = TradeNotifier(
            config_path=resource_path("cfg/stock.cfg")
        )
        self._sent: list[tuple[str, str | None]] = []

        def _capture(url: str, message: str, recipient: str | None) -> bool:
            self._sent.append((message, recipient))
            return True

        self._notifier._send_http_text = _capture  # type: ignore[method-assign]
        self._notifier._resolve_wx_push_url = (  # type: ignore[method-assign]
            lambda user_strategy_id: "http://example.invalid/webhook"
        )

    def test_signal_fields_are_reformatted(self) -> None:
        with patch.object(
            self._notifier, "_resolve_stock_name", return_value="三花智控"
        ) as mock_name:
            self._notifier.notify(
                recipient="波段交易策略",
                user_strategy_id="us1",
                action="买入",
                stock_code="002050.SZ",
                price=35.42,
                reason=_sample_reason(),
                star=3.0,
            )
        mock_name.assert_called_once_with("002050.SZ", "")
        self.assertEqual(
            self._sent[0][0],
            "买入002050.SZ(三花智控)，买入价:35.42,买入条件："
            "条件满足: 因子返回的分值满足买入条件, 枢轴点分值:5.00, 布林带分值:5.00"
            "，买入推荐: 3颗星",
        )
        self.assertEqual(self._sent[0][1], "波段交易策略")

    def test_buy_signal_has_no_profit_rate_section(self) -> None:
        with patch.object(self._notifier, "_resolve_stock_name", return_value=""):
            self._notifier.notify(
                action="买入", stock_code="000001.SZ", price=10.0, star=2.0
            )
        self.assertNotIn("收益率", self._sent[0][0])

    def test_plain_message_is_preserved(self) -> None:
        """成交通知等调用方（只传 message）走旧路径，文案不被改写。"""
        self._notifier.notify("时间:2026-09-28 10:00:00 股票:000001.SZ 买入", recipient="策略A")
        self.assertEqual(
            self._sent[0][0], "时间:2026-09-28 10:00:00 股票:000001.SZ 买入"
        )

    def test_empty_message_skipped(self) -> None:
        self._notifier.notify()
        self.assertEqual(self._sent, [])


if __name__ == "__main__":
    unittest.main()

"""
Author: liguoqiang
Date: 2026-08-02
Description: 策略模板 API 测试 —— 校验回测结果按策略模板 ID 关联的新接口契约。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from api.strategy import StrategyApi


class TestStrategyApi(unittest.TestCase):
    def test_save_backtest_uses_strategy_endpoint(self) -> None:
        client = MagicMock()
        api = StrategyApi(client)

        result = api.save_backtest(
            "s-1", {"total_trades": 3, "trades": [{"code": "000001"}]}
        )

        client.post.assert_any_call(
            "/api/strategy/s-1/backtest",
            {"result_data": {"total_trades": 3, "trades": [{"code": "000001"}]}},
        )
        self.assertIsInstance(result, str)

    def test_get_backtest_uses_strategy_endpoint(self) -> None:
        client = MagicMock()
        client.get.return_value = [{"total_trades": 1}]
        api = StrategyApi(client)

        result = api.get_backtest("s-1")

        client.get.assert_any_call("/api/strategy/s-1/backtest")
        self.assertEqual(result, [{"total_trades": 1}])


if __name__ == "__main__":
    unittest.main()

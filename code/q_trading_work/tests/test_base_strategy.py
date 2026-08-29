import unittest
from typing import Any

import pandas as pd

from strategy.base_strategy import BaseStrategy


class DummyStrategy(BaseStrategy):
    strategy_name = "DummyStrategy"
    strategy_type = "选股策略"
    description = "测试策略"

    def init_factors(self) -> None:
        return None

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict]:
        if len(stock_data) < 2:
            return False, {"reason": "data too short"}
        return bool(stock_data[-1]["close"] > stock_data[-2]["close"]), {
            "last_close": float(stock_data[-1]["close"])
        }

    def before_trading(
        self, trade_date: str, stock_codes: list[str], **kwargs: Any
    ) -> None:
        pass

    def handle_minute_bar(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def handle_tick_bar(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def check_minute_buy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_minute_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def check_tick_buy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_tick_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def load_his_daily_data(self, code: str, days: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"code": code, "close": 10.0, "open": 9.5, "volume": 1000},
                {"code": code, "close": 11.0, "open": 10.5, "volume": 1000},
            ]
        )


class TestBaseStrategy(unittest.TestCase):
    def test_select_returns_matched_results(self) -> None:
        strategy = DummyStrategy()

        results = strategy.select(["000001"], days=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "000001")
        self.assertTrue(results[0]["matched"])
        self.assertEqual(results[0]["extra"]["last_close"], 11.0)


if __name__ == "__main__":
    unittest.main()

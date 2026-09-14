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
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_minute_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def check_tick_buy(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        return False, {}

    def check_tick_sell(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict]:
        return False, {}

    def load_his_daily_data(
        self, code: str, today_str: str, days: int
    ) -> pd.DataFrame:
        """与基类签名一致：select() 会以关键字传入 today_str。"""
        return pd.DataFrame(
            [
                {"code": code, "close": 10.0, "open": 9.5, "volume": 1000},
                {"code": code, "close": 11.0, "open": 10.5, "volume": 1000},
            ]
        )


class TestBaseStrategy(unittest.TestCase):
    def test_normalize_params_unwraps_desc_value(self) -> None:
        """default_params 的 {desc, value} 包装格式应被解包为纯值。"""
        wrapped = {
            "match": {
                "pe_limit": {"desc": "最大市盈率", "value": 300},
                "rebound_score": {"desc": "反弹分值", "value": 10.0},
            },
            "sell": {"max_score": {"desc": "卖出分值", "value": -8.0}},
        }
        result = BaseStrategy._normalize_params(wrapped)
        self.assertEqual(result["match"]["pe_limit"], 300)
        self.assertEqual(result["match"]["rebound_score"], 10.0)
        self.assertEqual(result["sell"]["max_score"], -8.0)

    def test_normalize_params_keeps_flat_values(self) -> None:
        """纯值格式（strategy_params/yaml）应保持不变。"""
        flat = {
            "match": {"pe_limit": 300.0, "rebound_score": 10.0},
            "sell": {"max_score": -8.0},
        }
        result = BaseStrategy._normalize_params(flat)
        self.assertEqual(result, flat)

    def test_normalize_params_handles_empty_and_none(self) -> None:
        self.assertEqual(BaseStrategy._normalize_params({}), {})
        self.assertIsNone(BaseStrategy._normalize_params(None))

    def test_select_returns_matched_results(self) -> None:
        strategy = DummyStrategy()

        # select() 现签名为 (codes, today_str, days)：today_str 用于推算日线取数区间，
        # 原用例缺少该参数（历史遗留）。
        results = strategy.select(["000001"], "2026-07-15", days=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "000001")
        self.assertTrue(results[0]["matched"])
        self.assertEqual(results[0]["extra"]["last_close"], 11.0)

    def test_select_handles_empty_data(self) -> None:
        """日线为空时应返回 matched=False 而不是抛异常。"""
        strategy = DummyStrategy()
        strategy.load_his_daily_data = lambda code, today_str, days: pd.DataFrame()

        results = strategy.select(["000001"], "2026-07-15", days=2)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["matched"])


if __name__ == "__main__":
    unittest.main()

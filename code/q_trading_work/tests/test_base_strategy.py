import unittest
from typing import Any
from unittest.mock import patch

import pandas as pd

from strategy.base_strategy import BaseStrategy, MarketDataFetchError


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
        self, code: str, today_str: str, days: int, **kwargs: Any
    ) -> pd.DataFrame:
        """与基类签名一致：select() 会以关键字传入取数重试参数。"""
        return pd.DataFrame(
            [
                {"code": code, "close": 10.0, "open": 9.5, "volume": 1000},
                {"code": code, "close": 11.0, "open": 10.5, "volume": 1000},
            ]
        )


class ProbeStrategy(BaseStrategy):
    """最小策略：**不重载** load_his_daily_data，用于验证基类取数语义（B 决策）。"""

    strategy_name = "ProbeStrategy"
    strategy_type = "选股策略"
    description = "测试策略"

    def init_factors(self) -> None:
        return None

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        if not stock_data:
            return False, {"reason": "empty"}
        return True, {"score": 8.5}


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

    def test_price_limit_pct_matrix(self) -> None:
        """R13：涨跌停幅度按板块判定（ST 5%、创业板/科创板 20%、其余 10%）。"""
        cases = (
            ("000001", "平安银行", 10.0),
            ("601398", "工商银行", 10.0),
            ("002594", "比亚迪", 10.0),
            ("300750", "宁德时代", 20.0),
            ("301029", "怡合达", 20.0),
            ("688981", "中芯国际", 20.0),
            ("002456", "*ST欧菲", 5.0),
            ("600001", "ST某某", 5.0),
            ("002456", "st小写", 5.0),
            ("002456", "", 10.0),
        )
        for code, name, expected in cases:
            with self.subTest(code=code, name=name):
                self.assertEqual(
                    BaseStrategy.price_limit_pct(code, name), expected
                )

    def test_near_price_limit_pct_margin(self) -> None:
        """接近涨跌停的余量可配置，且不会退化成非正数。"""
        strategy = DummyStrategy()
        self.assertEqual(strategy._near_price_limit_pct("000001"), 9.0)
        self.assertEqual(strategy._near_price_limit_pct("300750"), 19.0)
        self.assertEqual(strategy._near_price_limit_pct("300750", margin=2.0), 18.0)
        # 余量（30）大于幅度（10）时夹到 1.0，避免阈值变成负数把判定方向反过来
        self.assertEqual(strategy._near_price_limit_pct("000001", margin=30.0), 1.0)

    def test_change_percent_scales_ratio_field(self) -> None:
        """R16/R13：`change_percent` 是小数比率，需 ×100；缺失时用昨收自算。"""
        strategy = DummyStrategy()
        self.assertAlmostEqual(
            strategy._change_percent({"change_percent": 0.0325}, 10.325, 10.0), 3.25
        )
        # 字段缺失 / 为 0 / 非法 → 用昨收自算
        for bar in ({}, {"change_percent": None}, {"change_percent": 0.0},
                    {"change_percent": "abc"}):
            with self.subTest(bar=bar):
                self.assertAlmostEqual(
                    strategy._change_percent(bar, 9.5, 10.0), -5.0
                )

    def test_change_percent_no_division_by_zero(self) -> None:
        """R13：preclose<=0（数据不全）时不得除零。"""
        strategy = DummyStrategy()
        self.assertEqual(strategy._change_percent({}, 10.0, 0.0), 0.0)
        self.assertEqual(strategy._change_percent({"change_percent": 0.0}, 10.0, -1.0), 0.0)

    def test_select_returns_matched_results(self) -> None:
        strategy = DummyStrategy()

        # select() 现签名为 (codes, today_str, days)：today_str 用于推算日线取数区间，
        # 原用例缺少该参数（历史遗留）。
        results = strategy.select(["000001"], "2026-07-15", days=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["code"], "000001")
        self.assertTrue(results[0]["matched"])
        self.assertEqual(results[0]["extra"]["last_close"], 11.0)
        # 新增字段：score / fetch_failed / reason_code（服务端选股表需要 score）
        self.assertEqual(results[0]["score"], 0.0)
        self.assertFalse(results[0]["fetch_failed"])
        self.assertEqual(results[0]["reason_code"], BaseStrategy.REASON_MATCHED)

    def test_select_handles_empty_data(self) -> None:
        """日线为空时应返回 matched=False 而不是抛异常。"""
        strategy = DummyStrategy()
        strategy.load_his_daily_data = lambda code, today_str, days, **kwargs: pd.DataFrame()

        results = strategy.select(["000001"], "2026-07-15", days=2)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["matched"])
        # 「无数据」不是「取数失败」，两者必须区分开
        self.assertFalse(results[0]["fetch_failed"])
        self.assertEqual(results[0]["reason_code"], BaseStrategy.REASON_NO_DATA)


class TestSelectFetchSemantics(unittest.TestCase):
    """B 决策：select 必须把「取数失败」与「正常不满足条件」区分开。

    修复前的缺陷：load_his_daily_data 把行情接口异常吞进 `logger.debug` 并返回空表，
    于是服务端 `/api/market/kline/day` 间歇性 500 会表现为「所有股票都是 no data」
    → 0 候选 → 0 交易，且日志里什么都看不到。
    """

    @staticmethod
    def _patch_market_api():
        """patch 基类里取的 AppContext（行情接口由它提供）。"""
        return patch("strategy.base_strategy.AppContext")

    def test_fetch_exception_marks_fetch_failed_and_warns(self) -> None:
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.side_effect = Exception(
                "HTTP 500"
            )
            with self.assertLogs("strategy.base_strategy", level="WARNING") as logs:
                results = strategy.select(["000001"], "2026-07-15", days=2)

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertFalse(item["matched"])
        self.assertTrue(item["fetch_failed"])
        self.assertEqual(item["reason_code"], BaseStrategy.REASON_FETCH_FAILED)
        self.assertIn("HTTP 500", item["extra"]["reason"])
        # 关键：异常必须落到 warning（原来是 debug，线上等于静默）
        joined = "\n".join(logs.output)
        self.assertIn("取数失败", joined)
        self.assertIn("不能当作正常不满足条件忽略", joined)
        self.assertIn("code=000001", joined)

    def test_empty_result_is_no_data_not_fetch_failed(self) -> None:
        """接口正常返回空列表 = 无数据，不应被标记成取数失败。"""
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.return_value = []
            results = strategy.select(["000001"], "2026-07-15", days=2)

        item = results[0]
        self.assertFalse(item["fetch_failed"])
        self.assertEqual(item["reason_code"], BaseStrategy.REASON_NO_DATA)
        self.assertEqual(item["extra"]["reason"], "no data")

    def test_retry_then_success_keeps_fetch_failed_false(self) -> None:
        """C 决策的重试语义：首次失败、第二次成功，结果仍算正常命中。"""
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            api = mock_ctx.return_value.market_api
            api.get_day_kline.side_effect = [
                Exception("HTTP 500"),
                [
                    {
                        "code": "000001",
                        "close": 10.0,
                        "open": 9.0,
                        "volume": 1,
                        "create_time": "2026-07-13",
                    }
                ],
            ]
            results = strategy.select(
                ["000001"], "2026-07-15", days=2, retries=1, retry_interval=0
            )

        self.assertEqual(api.get_day_kline.call_count, 2)
        self.assertTrue(results[0]["matched"])
        self.assertFalse(results[0]["fetch_failed"])
        # score 直接从 extra 提取，供保存选股结果使用
        self.assertEqual(results[0]["score"], 8.5)

    def test_raise_on_error_propagates_market_data_fetch_error(self) -> None:
        """回测链路：raise_on_error=True 时取数失败必须上抛，不能降级为空表。"""
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            api = mock_ctx.return_value.market_api
            api.get_day_kline.side_effect = Exception("HTTP 500")
            with self.assertRaises(MarketDataFetchError) as ctx:
                strategy.select(
                    ["000001"],
                    "2026-07-15",
                    days=2,
                    retries=1,
                    retry_interval=0,
                    raise_on_error=True,
                )

        # 重试 1 次 = 共 2 次尝试，消息里带上区间与尝试次数便于定位
        self.assertEqual(api.get_day_kline.call_count, 2)
        self.assertIn("000001", str(ctx.exception))
        self.assertIn("attempts=2", str(ctx.exception))

    def test_load_marks_fetch_failed_in_attrs(self) -> None:
        """load_his_daily_data 的失败语义通过 DataFrame.attrs 透出（不改列结构）。"""
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            mock_ctx.return_value.market_api.get_day_kline.side_effect = Exception("boom")
            df = strategy.load_his_daily_data("000001", "2026-07-15", 2)

        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), ["code", "open", "close", "volume", "create_time"])
        self.assertTrue(df.attrs["fetch_failed"])
        self.assertIn("boom", df.attrs["fetch_error"])

    def test_get_day_kline_always_receives_date_range(self) -> None:
        """服务端只保留约 3 个月行情、且会回源第三方，因此必须恒定带日期查询。"""
        strategy = ProbeStrategy()
        with self._patch_market_api() as mock_ctx:
            api = mock_ctx.return_value.market_api
            api.get_day_kline.return_value = []
            strategy.load_his_daily_data("000001", "2026-07-15", 20)

        kwargs = api.get_day_kline.call_args.kwargs
        self.assertTrue(kwargs.get("start"))
        self.assertTrue(kwargs.get("end"))
        self.assertEqual(kwargs["code"], "000001")


if __name__ == "__main__":
    unittest.main()

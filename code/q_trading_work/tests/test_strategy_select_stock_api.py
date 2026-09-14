"""
Author: liguoqiang
Date: 2026-09-15
Description: 策略选股结果 API 测试 —— 校验新增 score 字段的请求契约。

服务端 `StrategySelectStockCreateRequest` 已由
    {"user_strategy_id": ..., "codes": ["000001", ...]}
改为
    {"user_strategy_id": ..., "stocks": [{"code": "000001", "score": 7.5}, ...]}
本文件锁定该契约，并验证旧写法（纯代码字符串列表）仍能被归一化。
全部使用 mock client，不连服务端。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from api.strategy_select_stock import StrategySelectStockApi


class TestStrategySelectStockAdd(unittest.TestCase):
    """POST /api/strategy_select_stocks/add 的请求体契约。"""

    def test_add_posts_stocks_with_score(self) -> None:
        client = MagicMock()
        client.post.return_value = [{"code": "000001", "score": 7.5}]
        api = StrategySelectStockApi(client)

        result = api.add("us-1", [{"code": "000001", "score": 7.5}])

        client.post.assert_called_once_with(
            "/api/strategy_select_stocks/add",
            {
                "user_strategy_id": "us-1",
                "stocks": [{"code": "000001", "score": 7.5}],
            },
        )
        self.assertEqual(result, [{"code": "000001", "score": 7.5}])

    def test_add_normalizes_legacy_code_list(self) -> None:
        """兼容旧写法：纯代码列表按 score=0.0 归一化，而不是报错。"""
        client = MagicMock()
        client.post.return_value = []
        api = StrategySelectStockApi(client)

        api.add("us-1", ["000001", "600519"])

        body = client.post.call_args.args[1]
        self.assertEqual(
            body["stocks"],
            [
                {"code": "000001", "score": 0.0},
                {"code": "600519", "score": 0.0},
            ],
        )

    def test_add_clamps_invalid_score_to_zero(self) -> None:
        """score 缺失 / None / 非数值一律归 0.0（服务端也会再兜一次）。"""
        client = MagicMock()
        client.post.return_value = []
        api = StrategySelectStockApi(client)

        api.add(
            "us-1",
            [
                {"code": "000001"},
                {"code": "600519", "score": None},
                {"code": "002415", "score": "not-a-number"},
            ],
        )

        body = client.post.call_args.args[1]
        self.assertEqual(
            body["stocks"],
            [
                {"code": "000001", "score": 0.0},
                {"code": "600519", "score": 0.0},
                {"code": "002415", "score": 0.0},
            ],
        )

    def test_add_skips_request_when_nothing_valid(self) -> None:
        """空列表 / 无法解析出 code 时不发请求（服务端对空 stocks 返回 400）。"""
        client = MagicMock()
        api = StrategySelectStockApi(client)

        self.assertEqual(api.add("us-1", []), [])
        self.assertEqual(api.add("us-1", [{"score": 1.0}, ""]), [])

        client.post.assert_not_called()

    def test_add_returns_empty_when_api_returns_nothing(self) -> None:
        client = MagicMock()
        client.post.return_value = None
        api = StrategySelectStockApi(client)

        self.assertEqual(api.add("us-1", [{"code": "000001", "score": 1.0}]), [])

    def test_add_wraps_single_dict_result(self) -> None:
        client = MagicMock()
        client.post.return_value = {"code": "000001", "score": 1.0}
        api = StrategySelectStockApi(client)

        self.assertEqual(
            api.add("us-1", [{"code": "000001", "score": 1.0}]),
            [{"code": "000001", "score": 1.0}],
        )


if __name__ == "__main__":
    unittest.main()

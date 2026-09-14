"""
Author: liguoqiang
Date: 2026-09-14
Description: 回测结果 API（BacktestApi）测试 —— 校验保存/查询接口契约：
    POST /api/strategy/{strategy_id}/backtest（body: {"result_data": {...}}）
    GET  /api/strategy/{strategy_id}/backtest
    并验证服务端记录能被解析回 BacktestDao。全部使用 mock client，不连服务端。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from api.backtest import BacktestApi
from backtest.backtest_engine import BacktestSummary, TradeResult
from dao.backtest_dao import BacktestDao


def _dao(strategy_id: str = "s-1") -> BacktestDao:
    summary = BacktestSummary(
        strategy_name="强势反弹策略",
        total_trades=1,
        total_return_pct=10.0,
        total_profit=1000.0,
        initial_capital=10000.0,
        max_drawdown_pct=5.0,
        start_date="2026-01-01",
        end_date="2026-01-31",
    )
    trades = [
        TradeResult(
            code="000001",
            strategy_name="强势反弹策略",
            buy_date="2026-01-05",
            sell_date="2026-01-12",
            buy_price=10.0,
            sell_price=11.0,
            profit_pct=10.0,
        )
    ]
    return BacktestDao.from_summary(
        strategy_id=strategy_id,
        summary=summary,
        trades=trades,
        frequency="daily",
        create_time="2026-09-14 23:00:00",
    )


class TestBacktestApiSave(unittest.TestCase):
    def test_save_posts_to_strategy_backtest_endpoint(self) -> None:
        client = MagicMock()
        client.post.return_value = "rec-1"
        api = BacktestApi(client)

        record_id = api.save(_dao("s-1"))

        self.assertEqual(record_id, "rec-1")
        client.post.assert_called_once()
        path, body = client.post.call_args.args
        self.assertEqual(path, "/api/strategy/s-1/backtest")
        # 服务端 BacktestSaveRequest 只认 result_data 字段
        self.assertEqual(list(body.keys()), ["result_data"])
        self.assertEqual(body["result_data"]["strategy_id"], "s-1")
        self.assertAlmostEqual(
            body["result_data"]["backtest_return_rate"], 0.10
        )
        self.assertEqual(body["result_data"]["trades"][0]["code"], "000001")
        self.assertEqual(
            body["result_data"]["summary"]["total_trades"], 1
        )

    def test_save_requires_strategy_id(self) -> None:
        api = BacktestApi(MagicMock())
        with self.assertRaises(ValueError):
            api.save(BacktestDao())

    def test_save_result_data_keeps_legacy_body(self) -> None:
        client = MagicMock()
        client.post.return_value = "rec-2"
        api = BacktestApi(client)

        api.save_result_data("s-2", {"total_trades": 3})

        client.post.assert_called_once_with(
            "/api/strategy/s-2/backtest", {"result_data": {"total_trades": 3}}
        )


class TestBacktestApiGet(unittest.TestCase):
    def test_get_uses_strategy_backtest_endpoint(self) -> None:
        client = MagicMock()
        client.get.return_value = [
            {
                "_id": "rec-1",
                "strategy_id": "s-1",
                "create_time": "2026-09-14 23:00:00",
                "result_data": {
                    "backtest_return_rate": 0.10,
                    "total_trades": 1,
                },
            }
        ]
        api = BacktestApi(client)

        records = api.get("s-1")

        client.get.assert_called_once_with("/api/strategy/s-1/backtest")
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0], BacktestDao)
        self.assertEqual(records[0].id, "rec-1")
        self.assertEqual(records[0].strategy_id, "s-1")
        self.assertAlmostEqual(records[0].backtest_return_rate, 0.10)

    def test_get_returns_empty_list_when_no_records(self) -> None:
        client = MagicMock()
        client.get.return_value = None
        api = BacktestApi(client)
        self.assertEqual(api.get("s-1"), [])
        self.assertEqual(api.get_raw("s-1"), [])

    def test_get_skips_non_dict_records(self) -> None:
        client = MagicMock()
        client.get.return_value = ["bad", {"_id": "ok", "result_data": {}}]
        api = BacktestApi(client)
        records = api.get("s-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "ok")


if __name__ == "__main__":
    unittest.main()

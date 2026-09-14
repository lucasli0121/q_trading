"""
Author: liguoqiang
Date: 2026-09-14
Description: 回测结果数据对象（BacktestDao）测试 ——
    校验「回测汇总 -> DAO -> 服务端请求体 -> 回测记录 -> DAO」的往返一致性，
    以及服务端嵌套/扁平两种记录形态的解析兼容性。
    全部为纯内存计算，不连服务端。
"""

from __future__ import annotations

import unittest

from backtest.backtest_engine import BacktestSummary, TradeResult
from dao.backtest_dao import BacktestDao


def _summary(**kwargs) -> BacktestSummary:
    """构造回测汇总，默认字段可被覆盖。"""
    base: dict = dict(
        strategy_name="强势反弹策略",
        total_trades=2,
        win_count=1,
        loss_count=1,
        win_rate=50.0,
        total_return_pct=12.5,
        total_profit=1250.0,
        benchmark_return_pct=3.0,
        initial_capital=10000.0,
        final_capital=11250.0,
        max_drawdown_pct=8.0,
        annualized_return_pct=30.0,
        sharpe_ratio=1.2,
        alpha=9.5,
        start_date="2026-01-01",
        end_date="2026-03-31",
        trading_days=58,
    )
    base.update(kwargs)
    return BacktestSummary(**base)


def _trades() -> list[TradeResult]:
    return [
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


class TestBacktestDaoBuild(unittest.TestCase):
    """从回测汇总构建 DAO。"""

    def test_from_summary_maps_scalar_fields(self) -> None:
        dao = BacktestDao.from_summary(
            strategy_id="s-1",
            summary=_summary(),
            trades=_trades(),
            frequency="daily",
            create_time="2026-09-14 23:00:00",
        )
        self.assertEqual(dao.strategy_id, "s-1")
        self.assertAlmostEqual(dao.backtest_return_rate, 0.125)  # 12.5% -> 0.125
        self.assertAlmostEqual(dao.backtest_profit, 1250.0)
        self.assertAlmostEqual(dao.benchmark_return_rate, 0.03)
        self.assertAlmostEqual(dao.max_drawdown, 0.08)
        self.assertAlmostEqual(dao.initial_amount, 10000.0)
        self.assertEqual(dao.start_date, "2026-01-01")
        self.assertEqual(dao.end_date, "2026-03-31")
        self.assertEqual(dao.frequency, "daily")
        self.assertEqual(dao.create_time, "2026-09-14 23:00:00")

    def test_from_summary_keeps_trades_and_summary_in_result_data(self) -> None:
        dao = BacktestDao.from_summary(
            strategy_id="s-1", summary=_summary(), trades=_trades()
        )
        self.assertEqual(dao.result_data["strategy_name"], "强势反弹策略")
        self.assertEqual(len(dao.result_data["trades"]), 1)
        self.assertEqual(dao.result_data["trades"][0]["code"], "000001")
        self.assertEqual(
            dao.result_data["summary"]["total_trades"], 2
        )
        self.assertEqual(dao.result_data["summary"]["trading_days"], 58)

    def test_from_summary_without_trades(self) -> None:
        dao = BacktestDao.from_summary(
            strategy_id="s-1", summary=_summary(), trades=None
        )
        self.assertEqual(dao.result_data["trades"], [])
        self.assertEqual(dao.result_data["summary"]["total_trades"], 2)

    def test_from_summary_strategy_name_override(self) -> None:
        dao = BacktestDao.from_summary(
            strategy_id="s-1",
            summary=_summary(strategy_name=""),
            strategy_name="覆盖名",
        )
        self.assertEqual(dao.result_data["strategy_name"], "覆盖名")

    def test_from_summary_empty_summary(self) -> None:
        dao = BacktestDao.from_summary(
            strategy_id="s-1", summary=BacktestSummary()
        )
        self.assertEqual(dao.backtest_return_rate, 0.0)
        self.assertEqual(dao.result_data["trades"], [])
        self.assertEqual(dao.result_data["summary"]["total_trades"], 0)


class TestBacktestDaoSerialize(unittest.TestCase):
    """DAO -> 服务端请求体。"""

    def _dao(self) -> BacktestDao:
        return BacktestDao.from_summary(
            strategy_id="s-1",
            summary=_summary(),
            trades=_trades(),
            frequency="minute",
            create_time="2026-09-14 23:00:00",
        )

    def test_to_result_data_is_self_describing(self) -> None:
        data = self._dao().to_result_data()
        for key in (
            "strategy_id",
            "backtest_return_rate",
            "backtest_profit",
            "benchmark_return_rate",
            "start_date",
            "end_date",
            "initial_amount",
            "max_drawdown",
            "frequency",
            "create_time",
            "trades",
            "summary",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["strategy_id"], "s-1")
        self.assertAlmostEqual(data["backtest_return_rate"], 0.125)
        self.assertEqual(data["trades"][0]["code"], "000001")

    def test_to_payload_matches_server_request_model(self) -> None:
        """服务端 BacktestSaveRequest 只接受 result_data 一个字段。"""
        payload = self._dao().to_payload()
        self.assertEqual(list(payload.keys()), ["result_data"])
        self.assertEqual(payload["result_data"]["strategy_id"], "s-1")

    def test_to_db_matches_server_table_shape(self) -> None:
        doc = self._dao().to_db()
        self.assertEqual(
            sorted(doc.keys()), ["create_time", "result_data", "strategy_id"]
        )
        self.assertEqual(doc["strategy_id"], "s-1")
        self.assertEqual(doc["create_time"], "2026-09-14 23:00:00")

    def test_result_data_is_json_serializable(self) -> None:
        import json

        json.dumps(self._dao().to_payload(), ensure_ascii=False)


class TestBacktestDaoParse(unittest.TestCase):
    """服务端记录 -> DAO。"""

    def test_from_db_nested_server_record(self) -> None:
        record = {
            "_id": "rec-1",
            "strategy_id": "s-1",
            "create_time": "2026-09-14 23:00:00",
            "result_data": {
                "strategy_name": "强势反弹策略",
                "backtest_return_rate": 0.125,
                "backtest_profit": 1250.0,
                "benchmark_return_rate": 0.03,
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
                "initial_amount": 10000.0,
                "max_drawdown": 0.08,
                "frequency": "daily",
                "trades": [{"code": "000001"}],
                "summary": {"total_trades": 2},
            },
        }
        dao = BacktestDao.from_db_record(record)
        self.assertEqual(dao.id, "rec-1")
        self.assertEqual(dao.strategy_id, "s-1")
        self.assertAlmostEqual(dao.backtest_return_rate, 0.125)
        self.assertAlmostEqual(dao.max_drawdown, 0.08)
        self.assertEqual(dao.frequency, "daily")
        self.assertEqual(dao.create_time, "2026-09-14 23:00:00")
        self.assertEqual(dao.result_data["trades"][0]["code"], "000001")
        self.assertEqual(dao.result_data["summary"]["total_trades"], 2)

    def test_from_db_flat_record(self) -> None:
        """兼容历史/扁平形态：标量字段与 result_data 同级。"""
        record = {
            "id": "rec-2",
            "strategy_id": "s-2",
            "backtest_return_rate": 0.05,
            "backtest_profit": 500.0,
            "start_date": "2026-02-01",
            "end_date": "2026-02-28",
            "initial_amount": 10000.0,
            "max_drawdown": 0.02,
            "frequency": "daily",
            "create_time": "2026-09-14 23:10:00",
            "trades": [],
        }
        dao = BacktestDao.from_db_record(record)
        self.assertEqual(dao.id, "rec-2")
        self.assertEqual(dao.strategy_id, "s-2")
        self.assertAlmostEqual(dao.backtest_return_rate, 0.05)
        self.assertEqual(dao.start_date, "2026-02-01")
        self.assertEqual(dao.frequency, "daily")

    def test_round_trip_is_lossless_for_scalars(self) -> None:
        """DAO -> 服务端记录 -> DAO，标量字段不变。"""
        src = BacktestDao.from_summary(
            strategy_id="s-1",
            summary=_summary(),
            trades=_trades(),
            frequency="daily",
            create_time="2026-09-14 23:00:00",
        )
        record = dict(src.to_db())
        record["_id"] = "rec-1"
        back = BacktestDao.from_db_record(record)
        for attr in (
            "strategy_id",
            "backtest_return_rate",
            "backtest_profit",
            "benchmark_return_rate",
            "max_drawdown",
            "initial_amount",
            "start_date",
            "end_date",
            "frequency",
            "create_time",
        ):
            self.assertEqual(
                getattr(src, attr), getattr(back, attr), f"字段不一致: {attr}"
            )
        self.assertEqual(len(back.result_data["trades"]), 1)

    def test_default_dao_is_empty(self) -> None:
        dao = BacktestDao()
        self.assertEqual(dao.id, "")
        self.assertEqual(dao.strategy_id, "")
        self.assertEqual(dao.result_data, {})
        self.assertEqual(dao.to_result_data()["trades"], [])


if __name__ == "__main__":
    unittest.main()

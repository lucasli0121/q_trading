"""回测取数的本地归档分支。

守的是 `_fetch_stock_data` 里那条新加的路径。项目里最容易出事的就是这种
「在既有链路上加一个分支」的改动：分支写反了，回测照跑、结果照出，只是数据
来源变了，没有任何报错。

关键语义（写错会静默改变回测结果）：
    None  = 本次不用本地，调用方继续走 HTTP
    []    = 本地覆盖完整，但这只股票在这段时间确实没记录（按无数据处理，**不回落**）

`talib` 是项目运行依赖但本机环境可能装不起来（需要 C 库）。回测引擎的 import
链会带出它，而本测试只关心取数分支、不碰任何因子计算，缺它时注入空壳即可：
它在模块级只被 import，属性都在函数体内访问。
"""

import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

if "talib" not in sys.modules:
    try:
        import talib  # noqa: F401
    except ImportError:
        sys.modules["talib"] = types.ModuleType("talib")

from backtest.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from dao.local_hq_dao import KIND_MINUTE, LocalHqDao, LocalHqError  # noqa: E402


def _day_row(code: str, stamp: str) -> dict:
    return {
        "code": code,
        "create_time": stamp,
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 100000,
        "amount": 1020000.0,
    }


def _write_day(root: Path, day: str, rows: list[dict]) -> None:
    year, _, _ = day.split("-")
    path = root / "day" / year / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)


def _write_minute(root: Path, day: str, rows: list[dict]) -> None:
    year, month, _ = day.split("-")
    path = root / "minute" / year / month / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)


class TryLocalKlineTest(unittest.TestCase):
    """直接构造引擎实例，跳过 __init__ 那一串 Api/TradeManager 依赖。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for day in ("2026-10-12", "2026-10-13", "2026-10-14"):
            _write_day(
                self.root,
                day,
                [_day_row("A.SZ", f"{day} 00:00:00"), _day_row("B.SZ", f"{day} 00:00:00")],
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _engine(self, mode: str, frequency: str = "daily") -> BacktestEngine:
        engine = object.__new__(BacktestEngine)
        engine.logger = logging.getLogger("test.local_hq")
        engine.config = BacktestConfig(
            local_hq_root=str(self.root),
            local_hq_mode=mode,
            frequency=frequency,
        )
        engine._local_hq = (
            LocalHqDao(self.root) if mode != "off" else None
        )
        return engine

    def test_disabled_mode_never_reads_local(self) -> None:
        engine = self._engine("off")
        self.assertIsNone(engine._try_local_kline("A.SZ", "2026-10-12", "2026-10-14"))

    def test_complete_coverage_returns_records(self) -> None:
        engine = self._engine("only")
        records = engine._try_local_kline("A.SZ", "2026-10-12", "2026-10-14")
        self.assertIsNotNone(records)
        assert records is not None
        self.assertEqual(len(records), 3)

    def test_covered_without_record_returns_empty_not_none(self) -> None:
        """本地覆盖完整但没有这只股票：按「无数据」处理，**不能回落 HTTP**。

        回落会让这一次回测里部分股票来自本地、部分来自服务端，而服务端对缺失
        区间会回源补齐，同一份数据的结果随调用时间漂移。
        """
        engine = self._engine("only")
        result = engine._try_local_kline("Z.SZ", "2026-10-12", "2026-10-14")
        self.assertIsNotNone(result)
        self.assertEqual(result, [])

    def test_incomplete_coverage_raises_in_only_mode(self) -> None:
        engine = self._engine("only")
        with self.assertRaises(LocalHqError):
            engine._try_local_kline("A.SZ", "2026-10-01", "2026-10-14")

    def test_incomplete_coverage_falls_back_in_prefer_mode(self) -> None:
        engine = self._engine("prefer")
        self.assertIsNone(engine._try_local_kline("A.SZ", "2026-10-01", "2026-10-14"))
        self.assertEqual(engine._local_hq.stats.coverage_miss, 1)

    def test_minute_frequency_reads_minute_partition(self) -> None:
        row = {
            "code": "A.SZ",
            "minute_time": "2026-10-14 09:32:00",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "price": 10.2,
            "preclose": 10.0,
            "volume": 1000,
            "amount": 10200.0,
            "change_percent": 0.02,
            "turnover": 0.001,
            "amp": 0.07,
        }
        _write_minute(self.root, "2026-10-14", [row])
        engine = self._engine("only", frequency="minute")
        records = engine._try_local_kline("A.SZ", "2026-10-14", "2026-10-14")
        self.assertIsNotNone(records)
        assert records is not None
        self.assertEqual(len(records), 1)
        self.assertIn("minute_time", records[0])


class InvalidModeTest(unittest.TestCase):
    def test_invalid_mode_is_rejected_before_any_request(self) -> None:
        """模式写错必须立刻报错，不能退化成 off。

        写错模式的意图一定是用本地数据，静默退回 HTTP 会得到「看起来正常但
        来源不对」的回测结果。
        """
        with self.assertRaises(ValueError):
            BacktestEngine(BacktestConfig(local_hq_root="/tmp/x", local_hq_mode="preferred"))


if __name__ == "__main__":
    unittest.main()

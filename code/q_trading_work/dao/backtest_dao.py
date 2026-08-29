"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-26 13:30:00
Description: 策略回测结果数据对象
"""

from dataclasses import dataclass
import logging
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)


@dataclass
class BacktestDao:
    """策略回测结果数据对象，通过 strategy_id 关联 StrategyDao"""
    id: str
    strategy_id: str
    backtest_return_rate: float  # 回测收益率（如 0.15 表示 15%）
    backtest_profit: float  # 回测收益金额
    benchmark_return_rate: float  # 基准收益率（如沪深300同期收益）
    start_date: str  # 回测开始日期 YYYY-MM-DD
    end_date: str  # 回测结束日期 YYYY-MM-DD
    initial_amount: float  # 本金
    max_drawdown: float  # 最大回撤（如 0.20 表示 20%）
    frequency: str  # 频率（如 daily / weekly / monthly）
    result_data: dict[str, Any]  # 回测结果 JSON（完整数据）
    create_time: str

    def __init__(self, id: str = "", strategy_id: str = "") -> None:
        self.id = id
        self.strategy_id = strategy_id
        self.backtest_return_rate = 0.0
        self.backtest_profit = 0.0
        self.benchmark_return_rate = 0.0
        self.start_date = ""
        self.end_date = ""
        self.initial_amount = 0.0
        self.max_drawdown = 0.0
        self.frequency = ""
        self.result_data = {}
        self.create_time = ""

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.strategy_id = data.get("strategy_id", "")
        self.backtest_return_rate = to_float(data.get("backtest_return_rate", 0.0))
        self.backtest_profit = to_float(data.get("backtest_profit", 0.0))
        self.benchmark_return_rate = to_float(data.get("benchmark_return_rate", 0.0))
        self.start_date = data.get("start_date", "")
        self.end_date = data.get("end_date", "")
        self.initial_amount = to_float(data.get("initial_amount", 0.0))
        self.max_drawdown = to_float(data.get("max_drawdown", 0.0))
        self.frequency = data.get("frequency", "")
        self.result_data = data.get("result_data", {})
        self.create_time = data.get("create_time", "")

    def to_db(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "backtest_return_rate": self.backtest_return_rate,
            "backtest_profit": self.backtest_profit,
            "benchmark_return_rate": self.benchmark_return_rate,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_amount": self.initial_amount,
            "max_drawdown": self.max_drawdown,
            "frequency": self.frequency,
            "result_data": self.result_data,
            "create_time": self.create_time,
        }

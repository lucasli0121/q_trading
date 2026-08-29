"""
Author: liguoqiang
Date: 2026-07-08
Description: 回测模块 — 提供回测引擎、配置与结果数据类。
"""

from backtest.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestSummary,
    TradeCostConfig,
    TradeResult,
)

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestSummary",
    "TradeCostConfig",
    "TradeResult",
]

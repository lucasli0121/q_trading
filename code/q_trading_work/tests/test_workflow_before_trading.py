"""真实链路集成测试（默认跳过）。

本文件会构造完整的 `StrategyWorkflow` 并调用 `start()`：连接生产 MQTT、
从服务端拉取真实用户策略，随后 `daily_before_trading()` 会把**真实选股结果
写回服务端**。因此默认跳过，只有显式开启时才运行：

    Q_TRADING_RUN_LIVE_TESTS=1 python -m pytest tests/test_workflow_before_trading.py

日常回归请勿开启；该文件目前也没有任何断言，运行通过不代表逻辑正确。
"""

import os
import unittest

import pandas as pd

from workflow.strategy_workflow import StrategyWorkflow

LIVE_TESTS_ENV = "Q_TRADING_RUN_LIVE_TESTS"


@unittest.skipUnless(
    os.getenv(LIVE_TESTS_ENV),
    f"真实链路集成测试，需 {LIVE_TESTS_ENV}=1 才运行（会写真实选股结果）",
)
class TestBaseStrategy(unittest.TestCase):
    def test_select_returns_matched_results(self) -> None:
        work_flow = StrategyWorkflow()
        work_flow.start()
        work_flow.daily_before_trading()


if __name__ == "__main__":
    unittest.main()

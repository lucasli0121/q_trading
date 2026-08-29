import unittest
from typing import Any

import pandas as pd

from workflow.strategy_workflow import StrategyWorkflow


class TestBaseStrategy(unittest.TestCase):
    def test_select_returns_matched_results(self) -> None:
        work_flow = StrategyWorkflow()
        work_flow.start()
        work_flow.daily_before_trading()


if __name__ == "__main__":
    unittest.main()

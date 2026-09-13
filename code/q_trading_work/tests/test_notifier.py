"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测命令行入口测试 —— 参数解析、策略解析、配置构建、
    JSON 导出与主流程（引擎侧 mock，避免真实回测耗时）。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from api.client import ApiClient
from api.order import OrderApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from trade.notifier import TradeNotifier



class TestNotifier(unittest.TestCase):
    def setUp(self) -> None:
        from utils.tools import resource_path
        _cfg_path: str = resource_path("cfg/stock.cfg")
        self._api_client = ApiClient()
        self._api_client.set_fallback_token("e6f13658afb1ddfd5acfb0cf012c2fcefa19baf6b213ac782b8e610309011e2e")
        self._order_api: OrderApi = OrderApi(self._api_client)
        self._strategy_api: StrategyApi = StrategyApi(self._api_client)
        self._user_strategy_api: UserStrategyApi = UserStrategyApi(self._api_client)
        self._notifier: TradeNotifier = TradeNotifier(
            api_client=self._api_client, config_path=_cfg_path
        )
        return super().setUp()
    def test_notify_push_url(self):
        user_strategy_id: str = "6a662d5028893aa08cf31225"
        result = self._notifier._resolve_wx_push_url(user_strategy_id)
        print(result)

if __name__ == "__main__":
    unittest.main()

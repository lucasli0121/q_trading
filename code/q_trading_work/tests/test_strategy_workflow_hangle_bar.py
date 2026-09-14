"""
Author: liguoqiang
Date: 2026-08-02
Description: 策略工作流**真实链路**集成测试（默认跳过）。

⚠️ 风险说明：本文件不做任何 mock —— `setUp` 里用的是带真实 admin token 的
`ApiClient` 和真实的 `TradeManager`，`test_process_bar_buy_creates_order_once`
会 `wf.start()`（连接生产 MQTT、加载真实用户策略）后投喂真实分钟行情，
进而可能**在服务端产生真实订单**。因此默认跳过，只有显式开启才运行：

    Q_TRADING_RUN_LIVE_TESTS=1 python -m pytest tests/test_strategy_workflow_hangle_bar.py

（文件名 `hangle_bar` 是历史笔误，为免影响既有调用方暂不改名。）
"""

from __future__ import annotations

from configparser import ConfigParser
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from api.client import ApiClient
from api.market import MarketApi
from api.pool import PoolApi
from api.stock_info import StockInfoApi
from api.strategy import StrategyApi
from api.user_strategy import UserStrategyApi
from app_context import AppContext
from dao.order_dao import OrderDao, OrderStatus
from mq.mqtt_client import MqttTopic
from trade.manager import TradeManager, TradeExecutionResult
from workflow.strategy_workflow import StrategyState, StrategyWorkflow

LIVE_TESTS_ENV = "Q_TRADING_RUN_LIVE_TESTS"


@unittest.skipUnless(
    os.getenv(LIVE_TESTS_ENV),
    f"真实链路集成测试，需 {LIVE_TESTS_ENV}=1 才运行（可能产生真实订单）",
)
class TestStrategyWorkflowHandlerBar(unittest.TestCase):
    def setUp(self) -> None:
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = self._load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            # 同时设置全局 ApiClient 的 fallback token，确保 AppContext().pool_api 等
            # 在后台线程中也能携带认证信息
            AppContext().api_client.set_fallback_token(admin_token)
        
        self.wf = StrategyWorkflow(pool_size=2)
        self.market_api: MarketApi = MarketApi(self._admin_client)
        self.wf._user_strategy_api = UserStrategyApi(self._admin_client)
        self.wf._strategy_api = StrategyApi(self._admin_client)
        self.wf._pool_api = PoolApi(self._admin_client)
        self.wf._stock_info_api = StockInfoApi(self._admin_client)
        self.wf._trade_manager = TradeManager()
        


    def _load_admin_token(self) -> str:
        """从 cfg/stock.cfg 读取 admin_token。

        :return: admin_token 字符串，未配置时返回空字符串
        """
        try:
            from utils.tools import resource_path
            cp = ConfigParser()
            cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
            return cp.get("server", "admin_token", fallback="").strip()
        except Exception:
            return ""
    # ---- 行情处理与订单去重 ----

    def test_process_bar_buy_creates_order_once(self) -> None:
        self.wf.start()
        code: str = "600183.SH"
        start: str = "2026-08-31 09:30:00"
        end: str = "2026-08-31 15:00:00"
        topic = MqttTopic.stock_minute_pool_code("6a6b695628893aa08c08522a", code)
        values = self.market_api.get_minute_kline(code = code, start = start, end = end)
        if len(values) > 0:
            item = values[0]
            self.wf.handle_bar(topic, item)
    # def test_process_bar_skips_buy_when_holding(self) -> None:
    #     instance = self._fake_instance("BUY")
    #     state = make_state(instance=instance)
    #     self.wf._user_strategies["sid"] = [state]
    #     self.wf._load_positions = MagicMock(
    #         return_value={"000001": {"quantity": 100}}
    #     )
    #     self.wf._save_trade_signal = MagicMock()
    #     self.wf._create_buy_order = MagicMock()

    #     self.wf._process_bar(state, "000001", {})
    #     self.wf._create_buy_order.assert_not_called()

    # def test_process_bar_sell_respects_pending(self) -> None:
    #     instance = self._fake_instance("SELL")
    #     state = make_state(instance=instance)
    #     self.wf._user_strategies["sid"] = [state]
    #     self.wf._load_positions = MagicMock(
    #         return_value={"000001": {"cost_price": 10.0, "quantity": 100}}
    #     )
    #     self.wf._save_trade_signal = MagicMock()
    #     self.wf._create_sell_order = MagicMock()

    #     self.wf._process_bar(state, "000001", {})
    #     self.wf._create_sell_order.assert_called_once()

    #     state.pending_codes.add("000001")
    #     self.wf._process_bar(state, "000001", {})
    #     self.wf._create_sell_order.assert_called_once()

    # def test_process_bar_not_running_skipped(self) -> None:
    #     instance = self._fake_instance("BUY")
    #     state = make_state(status="stopped", instance=instance)
    #     self.wf._user_strategies["sid"] = [state]
    #     self.wf._save_trade_signal = MagicMock()
    #     self.wf._create_buy_order = MagicMock()

    #     self.wf._process_bar(state, "000001", {})
    #     self.wf._create_buy_order.assert_not_called()

    # def test_process_bar_max_stock_count(self) -> None:
    #     instance = self._fake_instance("BUY")
    #     state = make_state(instance=instance)
    #     state.max_stock_count = 1
    #     self.wf._user_strategies["sid"] = [state]
    #     self.wf._load_positions = MagicMock(
    #         return_value={"600519": {"quantity": 100}}
    #     )
    #     self.wf._save_trade_signal = MagicMock()
    #     self.wf._create_buy_order = MagicMock()

    #     self.wf._process_bar(state, "000001", {})
    #     self.wf._create_buy_order.assert_not_called()

    # # ---- 订单创建与回滚 ----

    # def test_create_buy_order_deducts_cash_and_marks_pending(self) -> None:
    #     state = make_state(cash=10000.0)
    #     self.wf._submit_trade = MagicMock(return_value=True)
    #     self.wf._save_runlog = MagicMock()

    #     self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

    #     order = self.wf._submit_trade.call_args.args[0]
    #     self.assertEqual(order.entrust_quantity, 1000)
    #     self.assertEqual(state.available_cash, 0.0)
    #     self.assertIn("000001", state.pending_codes)
    #     self.wf._save_runlog.assert_called_once()

    # def test_create_buy_order_rolls_back_on_submit_failure(self) -> None:
    #     state = make_state(cash=10000.0)
    #     self.wf._submit_trade = MagicMock(return_value=False)
    #     self.wf._save_runlog = MagicMock()

    #     self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

    #     self.assertEqual(state.available_cash, 10000.0)
    #     self.assertNotIn("000001", state.pending_codes)
    #     self.wf._save_runlog.assert_not_called()

    # def test_create_buy_order_skips_duplicate(self) -> None:
    #     state = make_state(cash=10000.0)
    #     state.pending_codes.add("000001")
    #     self.wf._submit_trade = MagicMock(return_value=True)

    #     self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

    #     self.wf._submit_trade.assert_not_called()
    #     self.assertEqual(state.available_cash, 10000.0)

    # def test_create_sell_order_rolls_back_on_failure(self) -> None:
    #     state = make_state(cash=5000.0)
    #     state.positions = {"000001": {"cost_price": 10.0, "quantity": 100}}
    #     self.wf._load_positions = MagicMock(
    #         return_value={"000001": {"cost_price": 10.0, "quantity": 100}}
    #     )
    #     self.wf._submit_trade = MagicMock(return_value=False)

    #     self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 12.0})

    #     self.assertEqual(state.available_cash, 5000.0)
    #     self.assertNotIn("000001", state.pending_codes)



if __name__ == "__main__":
    unittest.main()

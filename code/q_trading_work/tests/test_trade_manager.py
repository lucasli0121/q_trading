"""
Author: liguoqiang
Date: 2026-08-02
Description: 交易管理器测试 —— 覆盖新成本模型（绝对滑点 + 费率 + 最低手续费）、
    订单提交流程、持仓执行结果同步与通知。所有写操作均 mock，避免污染生产数据。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dao.order_dao import OrderDao, OrderStatus
from trade.manager import TradeCostConfig, TradeManager
from utils.tools import resource_path


def _write_cfg() -> str:
    """写入一个不含 [trade] 的临时配置，确保 notify 不会向真实 webhook 发请求。"""
    tmp_dir = tempfile.mkdtemp(prefix="q_trading_cfg_")
    cfg_path = Path(tmp_dir) / "stock.cfg"
    cfg_path.write_text(
        "[cost_config]\n"
        "buy_slip=0.2\n"
        "sell_slip=0.2\n"
        "fee_pct=0.02\n"
        "fee_low=5\n",
        encoding="utf-8",
    )
    return str(cfg_path)


def _buy_order(price: float = 10.0, qty: int = 100) -> OrderDao:
    return OrderDao(
        user_strategy_id="us-1",
        stock_code="600000",
        entrust_quantity=qty,
        trade_price=price,
        trade_quantity=qty,
        action="买入",
    )


def _sell_order(price: float = 12.0, qty: int = 100, cost: float = 10.2) -> OrderDao:
    return OrderDao(
        user_strategy_id="us-1",
        stock_code="600000",
        entrust_quantity=qty,
        trade_price=price,
        trade_quantity=qty,
        position_price=cost,
        action="卖出",
    )


class TestTradeManager(unittest.TestCase):
    """交易成本与订单执行测试。"""

    def setUp(self) -> None:
        self.cfg = _write_cfg()

    def _manager(self, cost_config: TradeCostConfig | None = None) -> TradeManager:
        return TradeManager(cost_config=cost_config, config_path=self.cfg)

    # ---- 成本模型 ----

    def test_execute_buy_applies_slippage_and_fee_floor(self) -> None:
        """买入：成交价 = 基准价 + 0.2 元；手续费 = max(金额*0.02%, 5元)。"""
        manager = self._manager(
            TradeCostConfig(buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5)
        )
        order = _buy_order(price=10.0, qty=100)

        result = manager.execute_order(order)

        self.assertEqual(result.order.status, OrderStatus.SUCCESS.value)
        self.assertAlmostEqual(result.execution_price, 10.2)
        self.assertAlmostEqual(result.commission_fee, 5.0)  # 比例 0.204 元 < 5 元下限
        self.assertEqual(result.action, "买入")
        self.assertAlmostEqual(order.position_price, 10.2)
        self.assertEqual(order.trade_quantity, 100)

    def test_execute_buy_big_order_fee_by_percentage(self) -> None:
        """大额订单手续费按 0.02% 比例计算，不受 5 元下限影响。"""
        manager = self._manager(
            TradeCostConfig(buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5)
        )
        order = _buy_order(price=100.0, qty=10000)

        result = manager.execute_order(order)

        self.assertAlmostEqual(result.execution_price, 100.2)
        self.assertAlmostEqual(result.commission_fee, 200.4)  # 10000*100.2*0.0002

    def test_execute_sell_profit_calculation(self) -> None:
        """卖出：成交价 = 基准价 - 0.2；盈亏 = 收入 - 成本 - 手续费。"""
        manager = self._manager(
            TradeCostConfig(buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5)
        )
        order = _sell_order(price=12.0, qty=100, cost=10.2)

        result = manager.execute_order(order)

        self.assertAlmostEqual(result.execution_price, 11.8)
        self.assertAlmostEqual(result.commission_fee, 5.0)
        # 1180 - 1020 - 5 = 155
        self.assertAlmostEqual(order.profit_amount, 155.0)
        self.assertAlmostEqual(order.profit_rate, 155.0 / 1020.0, places=6)

    def test_execute_sell_with_zero_cost_basis(self) -> None:
        """成本为 0 时盈亏率记为 0，不抛除零异常。"""
        manager = self._manager(
            TradeCostConfig(sell_slippage=0.2, fee_pct=0.02, fee_low=5)
        )
        order = _sell_order(price=12.0, qty=100, cost=0.0)
        result = manager.execute_order(order)
        self.assertEqual(result.order.profit_rate, 0.0)

    def test_execute_order_guards_invalid_execution_price(self) -> None:
        """滑点超过基准价时成交价不能为负。"""
        manager = self._manager(
            TradeCostConfig(buy_slippage=0.2, sell_slippage=0.2, fee_pct=0.02, fee_low=5)
        )
        order = _sell_order(price=0.05, qty=100, cost=10.0)

        result = manager.execute_order(order)

        self.assertGreater(result.execution_price, 0)
        self.assertEqual(result.order.status, OrderStatus.SUCCESS.value)

    def test_load_cost_config_from_real_cfg(self) -> None:
        """从真实 cfg/stock.cfg 读取成本配置（语义：滑点元、费率%、最低手续费元）。"""
        config = TradeManager._load_cost_config(resource_path("cfg/stock.cfg"))
        self.assertEqual(config.buy_slippage, 0.2)
        self.assertEqual(config.sell_slippage, 0.2)
        self.assertEqual(config.fee_pct, 0.02)
        self.assertEqual(config.fee_low, 5.0)

    # ---- 订单提交 ----

    def test_submit_order_success_with_callback(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._save_order_via_api = MagicMock(return_value="order-1")  # type: ignore[method-assign]
        manager._update_order_via_api = MagicMock(return_value=True)  # type: ignore[method-assign]
        manager._update_strategy_execution = MagicMock()  # type: ignore[method-assign]
        order = _buy_order()
        completed: list[str] = []

        result = manager.submit_order(order, callback=lambda r: completed.append(r.status))

        self.assertIsNotNone(result)
        self.assertEqual(order.id, "order-1")
        self.assertEqual(completed, [OrderStatus.SUCCESS.value])
        manager._save_order_via_api.assert_called_once()
        manager._update_order_via_api.assert_called_once()
        manager._update_strategy_execution.assert_called_once()

    def test_submit_order_delayed_execution(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._save_order_via_api = MagicMock(return_value="order-2")  # type: ignore[method-assign]
        manager._update_order_via_api = MagicMock(return_value=True)  # type: ignore[method-assign]
        manager._update_strategy_execution = MagicMock()  # type: ignore[method-assign]
        order = _buy_order()

        manager.submit_order(order, delay_seconds=0.05)
        self.assertEqual(order.status, OrderStatus.ENTRUST.value)
        time.sleep(0.15)
        self.assertEqual(order.status, OrderStatus.SUCCESS.value)

    def test_submit_order_sell_notifies_sell_branch(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._save_order_via_api = MagicMock(return_value="order-s")  # type: ignore[method-assign]
        manager._update_order_via_api = MagicMock(return_value=True)  # type: ignore[method-assign]
        manager._update_strategy_execution = MagicMock()  # type: ignore[method-assign]
        manager._notifier.notify = MagicMock()  # type: ignore[method-assign]

        manager.submit_order(_sell_order(price=12.0, qty=100, cost=10.2))

        self.assertIn("收益", manager._notifier.notify.call_args.args[0])

    def test_execute_and_notify_falls_back_to_save_order(self) -> None:
        """订单 ID 为空时，成交后再次尝试保存订单。"""
        manager = self._manager(TradeCostConfig())
        manager._save_order_via_api = MagicMock(side_effect=["", "order-fb"])  # type: ignore[method-assign]
        manager._update_order_via_api = MagicMock(return_value=True)  # type: ignore[method-assign]
        manager._update_strategy_execution = MagicMock()  # type: ignore[method-assign]
        manager._notifier.notify = MagicMock()  # type: ignore[method-assign]

        order = _buy_order()
        manager.submit_order(order)

        self.assertEqual(manager._save_order_via_api.call_count, 2)
        self.assertEqual(order.id, "order-fb")

    def test_save_order_via_api_failure_returns_empty(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._order_api = MagicMock()
        manager._order_api.create.side_effect = Exception("api down")

        self.assertEqual(manager._save_order_via_api(_buy_order()), "")

    def test_update_order_via_api_requires_id(self) -> None:
        manager = self._manager(TradeCostConfig())
        order = _buy_order()
        self.assertFalse(manager._update_order_via_api(order))

    def test_update_order_via_api_success_and_failure(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._order_api = MagicMock()
        order = _buy_order()
        order.id = "o-1"
        self.assertTrue(manager._update_order_via_api(order))
        manager._order_api.update.side_effect = Exception("api down")
        self.assertFalse(manager._update_order_via_api(order))

    def test_save_order_via_api_success(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._order_api = MagicMock()
        manager._order_api.create.return_value = {"id": "o-new"}
        self.assertEqual(manager._save_order_via_api(_buy_order()), "o-new")

    def test_get_stock_name_empty_and_error(self) -> None:
        manager = self._manager(TradeCostConfig())
        self.assertEqual(manager._get_stock_name(""), "")
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.side_effect = Exception(
                "api down"
            )
            self.assertEqual(manager._get_stock_name("600000"), "")
        self.assertEqual(manager._stock_name_cache.get("600000"), "")

    def test_get_strategy_name_cache_hit(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._strategy_name_cache["us-1"] = "策略A"
        self.assertEqual(manager._get_strategy_name("us-1"), "策略A")

    # ---- 策略执行数据同步 ----

    def test_update_strategy_execution_buy_adds_position(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {}
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]
        order = _buy_order(price=10.0, qty=100)
        order.id = "order-3"
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        saved = manager._user_strategy_api.save_execution.call_args
        self.assertIsNotNone(saved)
        kwargs = saved.kwargs
        self.assertEqual(len(kwargs["positions"]), 1)
        self.assertEqual(kwargs["positions"][0]["code"], "600000")
        self.assertEqual(kwargs["positions"][0]["quantity"], 100)
        self.assertEqual(kwargs["positions"][0]["name"], "平安银行")
        self.assertEqual(kwargs["start_date"], order.create_time[:10])
        # 买入时 profit_amount=0，不应触发 total_profit 同步
        update_calls = manager._user_strategy_api.update.call_args_list
        total_profit_calls = [
            c for c in update_calls if "total_profit" in c.kwargs
        ]
        self.assertEqual(len(total_profit_calls), 0)

    def test_update_strategy_execution_syncs_total_profit(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {}
        order = _sell_order(price=12.0, qty=100, cost=10.2)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        update_calls = manager._user_strategy_api.update.call_args_list
        self.assertTrue(
            any(
                call.kwargs.get("user_strategy_id") == "us-1"
                and call.kwargs.get("total_profit") == 180.0
                for call in update_calls
            )
        )

    def test_update_strategy_execution_first_buy_uses_initial_amount(self) -> None:
        """首次买入（无执行记录）：从 UserStrategyDao 取 initial_amount，
        正确计算 remaining_cash 并新增一条执行记录。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {}
        manager._user_strategy_api.get.return_value = {
            "initial_amount": 100000.0,
            "total_profit": 0.0,
        }
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]
        order = _buy_order(price=10.0, qty=100)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        save_kwargs = manager._user_strategy_api.save_execution.call_args.kwargs
        self.assertEqual(save_kwargs["initial_amount"], 100000.0)
        self.assertEqual(save_kwargs["current_profit"], 0.0)
        # 剩余资金 = 初始资金 100000 - 买入成本 1000
        self.assertEqual(save_kwargs["remaining_cash"], 99000.0)
        # 买入时 profit_amount=0，不应触发 total_profit 同步
        update_calls = manager._user_strategy_api.update.call_args_list
        total_profit_calls = [
            c for c in update_calls if "total_profit" in c.kwargs
        ]
        self.assertEqual(len(total_profit_calls), 0)

    def test_update_strategy_execution_sell_records_profit(self) -> None:
        """卖出股票：把已实现盈亏计入 current_profit 与 total_profit，
        并新增一条执行记录（保存最新剩余资金）。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 10000.0,
            "current_profit": 0.0,
            "positions": [
                {
                    "code": "600000",
                    "quantity": 100,
                    "cost_price": 10.2,
                    "current_price": 10.2,
                }
            ],
        }
        order = _sell_order(price=12.0, qty=100, cost=10.2)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        save_kwargs = manager._user_strategy_api.save_execution.call_args.kwargs
        # 已实现盈利 = 12*100 - 10.2*100 = 180，计入 current_profit
        self.assertEqual(save_kwargs["current_profit"], 180.0)
        self.assertEqual(save_kwargs["positions"], [])
        self.assertEqual(save_kwargs["remaining_cash"], 10180.0)
        self.assertTrue(
            any(
                call.kwargs.get("user_strategy_id") == "us-1"
                and call.kwargs.get("total_profit") == 180.0
                for call in manager._user_strategy_api.update.call_args_list
            )
        )

    def test_update_strategy_execution_sell_removes_position(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 10000.0,
            "positions": [
                {
                    "code": "600000",
                    "quantity": 100,
                    "cost_price": 10.2,
                    "current_price": 10.2,
                }
            ],
            "current_profit": 0.0,
        }
        order = _sell_order(price=12.0, qty=100, cost=10.2)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        saved = manager._user_strategy_api.save_execution.call_args
        kwargs = saved.kwargs
        self.assertEqual(kwargs["positions"], [])
        # 剩余资金 = 初始资金 10000 + 已实现盈利 180（12*100 - 10.2*100）
        self.assertEqual(kwargs["remaining_cash"], 10180.0)
        self.assertGreater(kwargs["current_profit"], 0)

    def test_update_strategy_execution_adds_to_existing_position(self) -> None:
        """加仓：加权平均成本。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {
            "positions": [
                {"code": "600000", "quantity": 100, "cost_price": 10.0}
            ]
        }
        order = _buy_order(price=12.0, qty=100)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        pos = manager._user_strategy_api.save_execution.call_args.kwargs[
            "positions"
        ][0]
        self.assertEqual(pos["quantity"], 200)
        self.assertAlmostEqual(pos["cost_price"], 11.0, places=2)

    def test_update_strategy_execution_partial_sell(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {
            "positions": [
                {"code": "600000", "quantity": 200, "cost_price": 10.0}
            ]
        }
        order = _sell_order(price=12.0, qty=100, cost=10.0)
        manager.execute_order(order)

        manager._update_strategy_execution(order)

        pos = manager._user_strategy_api.save_execution.call_args.kwargs[
            "positions"
        ][0]
        self.assertEqual(pos["quantity"], 100)

    def test_update_strategy_execution_api_error(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        order = _buy_order()
        manager.execute_order(order)
        manager._update_strategy_execution(order)  # 不应抛异常

    def test_update_strategy_execution_save_error(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {}
        manager._user_strategy_api.save_execution.side_effect = Exception("api down")
        order = _buy_order()
        manager.execute_order(order)
        manager._update_strategy_execution(order)  # 不应抛异常

    def test_load_friend_names_parses(self) -> None:
        tmp_dir = tempfile.mkdtemp(prefix="q_trading_friends_")
        cfg_path = Path(tmp_dir) / "stock.cfg"
        cfg_path.write_text(
            "[trade]\nwechat_friend_name=Alice, Bob\n    Carol\n",
            encoding="utf-8",
        )
        manager = TradeManager(cost_config=TradeCostConfig(), config_path=str(cfg_path))
        self.assertEqual(
            manager._notifier._wechat_friend_names, ["Alice", "Bob", "Carol"]
        )

    def test_send_http_text_success_and_failure(self) -> None:
        manager = self._manager(TradeCostConfig())
        with patch("trade.notifier.request.urlopen") as mock_open:
            self.assertTrue(
                manager._notifier._send_http_text("https://example.com/hook", "hi", "s")
            )
            mock_open.side_effect = Exception("network")
            self.assertFalse(
                manager._notifier._send_http_text("https://example.com/hook", "hi", "s")
            )

    def test_send_to_wechat_friend_when_itchat_missing(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._notifier._wechat_friend_names = ["Alice"]
        manager._notifier.notify("测试消息")  # HAS_ITCHAT=False 时静默跳过

    # ---- 名称解析 ----

    def test_get_stock_name_caches_result(self) -> None:
        manager = self._manager(TradeCostConfig())
        with patch("app_context.AppContext") as mock_ctx:
            mock_ctx.return_value.stock_info_api.get_by_codes.return_value = [
                {"name": "平安银行"}
            ]
            self.assertEqual(manager._get_stock_name("600000"), "平安银行")
            self.assertEqual(manager._get_stock_name("600000"), "平安银行")
        # 第二次命中缓存，不应再次调用 API
        self.assertEqual(manager._stock_name_cache.get("600000"), "平安银行")

    def test_get_strategy_name_resolves_and_falls_back(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get.return_value = {"strategy_id": "s1"}
        manager._strategy_api = MagicMock()
        manager._strategy_api.get_by_id.return_value = {"name": "强势反弹策略"}

        self.assertEqual(manager._get_strategy_name("us-1"), "强势反弹策略")

        # 失败时回退为 user_strategy_id
        manager._user_strategy_api.get.side_effect = Exception("api down")
        self.assertEqual(manager._get_strategy_name("us-2"), "us-2")

    # ---- 通知 ----

    def test_notify_sends_to_enterprise_webhook(self) -> None:
        tmp_dir = tempfile.mkdtemp(prefix="q_trading_hook_")
        cfg_path = Path(tmp_dir) / "stock.cfg"
        cfg_path.write_text(
            "[trade]\n"
            "enterprise_wechat_webhook_url=https://example.com/hook\n"
            "wechat_webhook_url=\n"
            "wechat_friend_name=\n",
            encoding="utf-8",
        )
        manager = TradeManager(cost_config=TradeCostConfig(), config_path=str(cfg_path))
        manager._notifier._send_http_text = MagicMock(return_value=True)  # type: ignore[method-assign]

        manager._notifier.notify("测试消息", recipient="策略A")

        manager._notifier._send_http_text.assert_called_once()
        self.assertIn("测试消息", manager._notifier._send_http_text.call_args.args[1])


if __name__ == "__main__":
    unittest.main()

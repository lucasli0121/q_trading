"""
Author: liguoqiang
Date: 2026-08-02
Description: 交易管理器测试 —— 覆盖新成本模型（绝对滑点 + 费率 + 最低手续费）、
    订单提交流程、持仓执行结果同步与通知。所有写操作均 mock，避免污染生产数据。
"""

from __future__ import annotations

import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dao.order_dao import OrderDao, OrderStatus
from dao.strategy_execution_dao import StrategyExecutionDao
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


def _flow(
    order_id: str,
    action: str,
    code: str,
    qty: int,
    price: float,
    fee: float = 0.0,
    profit_amount: float = 0.0,
    when: str = "",
    status: str = OrderStatus.SUCCESS.value,
) -> dict:
    """构造一条订单流水记录，字段与 `OrderApi.list` 的返回保持一致。"""
    return {
        "id": order_id,
        "user_strategy_id": "us-1",
        "stock_code": code,
        "entrust_quantity": qty,
        "trade_price": price,
        "trade_quantity": qty,
        "position_price": price,
        "profit_rate": 0.0,
        "profit_amount": profit_amount,
        "commission_fee": fee,
        "status": status,
        "create_time": when,
        "action": action,
        "client_order_id": "",
    }


def _isolate_manager_files(test_case: unittest.TestCase) -> None:
    """把 `TradeManager` 落盘到 `log/` 下的文件重定向到临时目录。

    该类自身会写两类文件：订单幂等键（`SUBMITTED_ORDER_KEYS_FILE`）与
    执行记录补偿队列（`PENDING_EXECUTION_FILE`）。用例若走真实路径会污染
    仓库 `log/`；补偿队列尤其危险——进程下次启动 `flush_pending_executions()`
    会把测试数据的执行记录**补投到真实服务端**。其余路径（如 `cfg/stock.cfg`）
    保持原样，避免影响断言真实配置的用例。
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="q_trading_log_"))
    real_resource_path = resource_path
    redirected: dict[str, str] = {
        TradeManager.SUBMITTED_ORDER_KEYS_FILE: str(
            tmp_dir / "submitted_order_keys.jsonl"
        ),
        TradeManager.PENDING_EXECUTION_FILE: str(tmp_dir / "pending_execution.jsonl"),
    }

    def _fake_resource_path(relative_path: str) -> str:
        return redirected.get(relative_path) or real_resource_path(relative_path)

    patcher = patch("utils.tools.resource_path", side_effect=_fake_resource_path)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    # 便于用例直接预置/断言这两个文件的内容
    test_case._log_tmp_dir = tmp_dir  # type: ignore[attr-defined]
    test_case._order_key_file = redirected[  # type: ignore[attr-defined]
        TradeManager.SUBMITTED_ORDER_KEYS_FILE
    ]
    test_case._pending_file = redirected[  # type: ignore[attr-defined]
        TradeManager.PENDING_EXECUTION_FILE
    ]


class TestTradeManager(unittest.TestCase):
    """交易成本与订单执行测试。"""

    def setUp(self) -> None:
        self.cfg = _write_cfg()
        _isolate_manager_files(self)

    def _manager(self, cost_config: TradeCostConfig | None = None) -> TradeManager:
        manager = TradeManager(cost_config=cost_config, config_path=self.cfg)
        # 订单流水接口默认置为「无记录」：`_update_strategy_execution` 在「无执行记录」
        # 或「执行记录取数失败」时会调用它重建持仓，测试里不能真的发 HTTP。
        # 需要断言订单保存/更新行为的用例再自行覆盖 `_order_api`。
        manager._order_api = MagicMock()
        manager._order_api.list.return_value = []
        return manager

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
        # 2026-10-14：滑点 0.2 → 0.02 元，与回测 CLI 默认值保持一致
        self.assertEqual(config.buy_slippage, 0.02)
        self.assertEqual(config.sell_slippage, 0.02)
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
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get.return_value = {"name": "爸爸的波段交易"}
        manager._notifier.notify_trade_result = MagicMock()  # type: ignore[method-assign]

        manager.submit_order(_sell_order(price=12.0, qty=100, cost=10.2))

        # 2026-09-28：成交回执改为传结构化字段，卖出带持仓价与收益
        kwargs = manager._notifier.notify_trade_result.call_args.kwargs
        self.assertEqual(kwargs["action"], "卖出")
        self.assertIsNotNone(kwargs["position_price"])
        self.assertIsNotNone(kwargs["profit_amount"])
        # recipient 用**用户策略名**，不用策略模板名
        self.assertEqual(kwargs["recipient"], "爸爸的波段交易")

    def test_execute_and_notify_falls_back_to_save_order(self) -> None:
        """订单 ID 为空时，成交后再次尝试保存订单。"""
        manager = self._manager(TradeCostConfig())
        manager._save_order_via_api = MagicMock(side_effect=["", "order-fb"])  # type: ignore[method-assign]
        manager._update_order_via_api = MagicMock(return_value=True)  # type: ignore[method-assign]
        manager._update_strategy_execution = MagicMock()  # type: ignore[method-assign]
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get.return_value = {"name": "测试策略"}
        manager._notifier.notify_trade_result = MagicMock()  # type: ignore[method-assign]

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

    def test_get_user_strategy_name_cache_hit(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_name_cache["us-1"] = "策略A"
        self.assertEqual(manager._get_user_strategy_name("us-1"), "策略A")

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
        """取数故障不抛异常，且必须留 WARNING。

        新口径：先尝试从订单流水重建（本例流水为空 → 相当于零持仓），
        订单流水也取不到时才放弃本次记录。
        """
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        order = _buy_order()
        manager.execute_order(order)
        with self.assertLogs("trade.manager", level="WARNING") as cm:
            manager._update_strategy_execution(order)  # 不应抛异常
        self.assertTrue(
            any("订单流水重建" in r.getMessage() for r in cm.records),
            "取数故障必须能从日志看到已改走订单流水重建",
        )

    def test_rebuild_positions_from_orders_on_load_failure(self) -> None:
        """执行记录取数失败时从订单流水重放持仓，不再以零持仓重建。

        本次成交订单已落库（先存订单再更新执行记录），重放时必须排除它，
        否则本次持仓会被重复计入。非成交（撤单）记录不参与重放。
        """
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        manager._user_strategy_api.get.return_value = {"initial_amount": 100000.0}
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]

        order = _buy_order(price=11.0, qty=100)
        order.id = "order-now"
        manager.execute_order(order)

        manager._order_api.list.return_value = [
            _flow("o-1", "买入", "600000", 200, 10.0, fee=5.0, when="2026-09-20 10:00:00"),
            _flow("o-2", "买入", "600519", 100, 20.0, when="2026-09-21 10:00:00"),
            _flow("order-now", "买入", "600000", 100, 11.0, when="2026-09-24 10:00:00"),
            _flow(
                "o-x", "买入", "000001", 100, 5.0,
                when="2026-09-22 10:00:00", status=OrderStatus.CANCELLED.value,
            ),
        ]

        manager._update_strategy_execution(order)

        kwargs = manager._user_strategy_api.save_execution.call_args.kwargs
        positions = {p["code"]: p for p in kwargs["positions"]}
        self.assertEqual(set(positions), {"600000", "600519"})
        # 600000 = 历史 200 股（成本 (200*10+5)/200 = 10.025）+ 本次 100 股 @11
        self.assertEqual(positions["600000"]["quantity"], 300)
        self.assertAlmostEqual(positions["600000"]["cost_price"], 10.35, places=4)
        self.assertEqual(positions["600519"]["quantity"], 100)
        self.assertEqual(kwargs["start_date"], "2026-09-20")

    def test_rebuild_accumulates_realized_profit_from_orders(self) -> None:
        """重建时按卖出订单累计已实现盈亏（与 metrics 口径一致）。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]

        order = _buy_order(price=20.0, qty=100)
        order.stock_code = "600519"
        manager.execute_order(order)

        manager._order_api.list.return_value = [
            _flow("o-1", "买入", "600000", 100, 10.0, fee=5.0, when="2026-09-20 10:00:00"),
            _flow(
                "o-2", "卖出", "600000", 100, 12.0, fee=5.0,
                profit_amount=200.0, when="2026-09-21 10:00:00",
            ),
        ]

        manager._update_strategy_execution(order)

        kwargs = manager._user_strategy_api.save_execution.call_args.kwargs
        # 历史卖出已实现 200；本次为买入（profit_amount=0），不再增加
        self.assertEqual(kwargs["current_profit"], 200.0)
        self.assertEqual([p["code"] for p in kwargs["positions"]], ["600519"])

    def test_give_up_execution_record_when_both_sources_fail(self) -> None:
        """持仓与订单流水都取不到：放弃本次新记录，不写一条残缺记录。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        manager._order_api.list.side_effect = Exception("api down")
        order = _buy_order()
        manager.execute_order(order)

        with self.assertLogs("trade.manager", level="ERROR") as cm:
            manager._update_strategy_execution(order)  # 不应抛异常

        manager._user_strategy_api.save_execution.assert_not_called()
        self.assertTrue(
            any("放弃本次执行记录" in r.getMessage() for r in cm.records),
            "放弃记录必须留 ERROR，便于运维发现记录滞后",
        )

    def test_no_execution_record_and_order_flow_failure_keeps_writing(self) -> None:
        """「尚无执行记录」是合法初始状态：订单流水取不到时仍按零持仓写入。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get_latest_execution.return_value = {}
        manager._order_api.list.side_effect = Exception("api down")
        order = _buy_order()
        manager.execute_order(order)

        with self.assertLogs("trade.manager", level="WARNING") as cm:
            manager._update_strategy_execution(order)

        manager._user_strategy_api.save_execution.assert_called_once()
        self.assertTrue(
            any("按零持仓处理" in r.getMessage() for r in cm.records),
        )

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

    def test_get_user_strategy_name_resolves_and_falls_back(self) -> None:
        """2026-09-28：取用户策略名（user_strategy.name），不再回查策略模板名。

        原实现「user_strategy → strategy_id → 策略模板 name」拿到的是模板名，
        同一模板下多条用户策略区分不开，通知的 recipient 对不上号。
        """
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.get.return_value = {
            "strategy_id": "s1",
            "name": "爸爸的波段交易",
        }
        manager._strategy_api = MagicMock()
        manager._strategy_api.get_by_id.return_value = {"name": "波段交易策略"}

        self.assertEqual(manager._get_user_strategy_name("us-1"), "爸爸的波段交易")
        manager._strategy_api.get_by_id.assert_not_called()

        # 失败时回退为 user_strategy_id
        manager._user_strategy_api.get.side_effect = Exception("api down")
        self.assertEqual(manager._get_user_strategy_name("us-2"), "us-2")

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


class TestTradeManagerAccountingAndMetrics(unittest.TestCase):
    """P0-26 / P1-16 / P1-17 / P1-27 新增逻辑的定点测试。

    这些用例直接驱动内部方法（`_apply_order_to_positions` /
    `_refresh_execution_metrics` / `_annualize_return_rate` / `_mark_order_key_submitted`
    / `flush_pending_executions`），不经过真实 API，避免污染生产数据。
    """

    def setUp(self) -> None:
        self.cfg = _write_cfg()
        # P1-17 / P1-16：幂等键与补偿队列都会落盘，用例必须隔离到临时目录，
        # 否则跨用例/跨次运行的残留键会让「首次提交」变成「重复提交」，
        # 补偿队列残留还会在下次启动时把测试数据补投到真实服务端。
        _isolate_manager_files(self)

    def _manager(self, cost_config: TradeCostConfig | None = None) -> TradeManager:
        manager = TradeManager(cost_config=cost_config, config_path=self.cfg)
        # 订单流水接口默认置为「无记录」：`_update_strategy_execution` 在「无执行记录」
        # 或「执行记录取数失败」时会调用它重建持仓，测试里不能真的发 HTTP。
        # 需要断言订单保存/更新行为的用例再自行覆盖 `_order_api`。
        manager._order_api = MagicMock()
        manager._order_api.list.return_value = []
        return manager

    def _execution(self, **overrides: object) -> StrategyExecutionDao:
        """构造 StrategyExecutionDao。

        注意：该类自定义了 `__init__(id, user_strategy_id)`，
        其余字段是**普通实例属性**（`@dataclass` 生成的构造器被覆盖），
        因此这里先构造再逐个赋值。
        """
        execution = StrategyExecutionDao(id="e1", user_strategy_id="us-1")
        execution.initial_amount = 10000.0
        execution.start_date = "2026-01-05"
        for name, value in overrides.items():
            setattr(execution, name, value)
        return execution

    # ---- 资金口径：成本价含买入手续费（与历史数据/服务端一致） ----

    def test_buy_fee_included_in_cost_price(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]
        order = _buy_order(price=10.0, qty=1000)
        order.commission_fee = 5.0
        order.create_time = "2026-07-15 10:00:00"
        execution = self._execution()

        manager._apply_order_to_positions(execution, order)

        pos = execution.positions[0]
        # 成本价 = (成交金额 + 手续费) / 数量 = (1000*10 + 5) / 1000
        self.assertAlmostEqual(pos["cost_price"], 10.005, places=4)
        # 资金占用 = quantity * cost_price，不需要再单独加手续费
        self.assertAlmostEqual(
            pos["quantity"] * pos["cost_price"], 10005.0, places=4
        )
        # fee 仅作审计留存，不参与任何资金/盈亏计算
        self.assertEqual(pos["fee"], 5.0)
        # X3：当日买入全部计入不可卖
        self.assertEqual(pos["t1_qty"], 1000)
        self.assertEqual(pos["t1_day"], "2026-07-15")

    def test_add_position_weighted_cost_includes_fee(self) -> None:
        """加仓：新旧两笔的买入手续费都体现在加权成本价里。"""
        manager = self._manager(TradeCostConfig())
        manager._get_stock_name = MagicMock(return_value="平安银行")  # type: ignore[method-assign]
        order = _buy_order(price=12.0, qty=1000)
        order.commission_fee = 5.0
        order.create_time = "2026-07-16 10:00:00"
        execution = self._execution(
            positions=[
                {
                    "code": "600000", "quantity": 1000, "cost_price": 10.005,
                    "current_price": 10.0, "fee": 5.0,
                    "t1_qty": 0, "t1_day": "2026-07-15",
                }
            ]
        )

        manager._apply_order_to_positions(execution, order)

        pos = execution.positions[0]
        self.assertEqual(pos["quantity"], 2000)
        # (1000*10.005 + 1000*12 + 5) / 2000 = 11.005
        self.assertAlmostEqual(pos["cost_price"], 11.005, places=4)
        self.assertAlmostEqual(pos["fee"], 10.0, places=4)
        # 隔日加仓：t1 重新起算为本次买入量
        self.assertEqual(pos["t1_qty"], 1000)
        self.assertEqual(pos["t1_day"], "2026-07-16")

    def test_partial_sell_keeps_cost_price_and_prorates_audit_fee(self) -> None:
        manager = self._manager(TradeCostConfig())
        order = _sell_order(price=12.0, qty=400, cost=10.0)
        order.commission_fee = 3.0
        order.create_time = "2026-07-16 10:00:00"
        execution = self._execution(
            positions=[
                {
                    "code": "600000", "quantity": 1000, "cost_price": 10.005,
                    "current_price": 12.0, "fee": 5.0,
                    "t1_qty": 1000, "t1_day": "2026-07-15",
                }
            ]
        )

        manager._apply_order_to_positions(execution, order)

        pos = execution.positions[0]
        self.assertEqual(pos["quantity"], 600)
        # 卖出不改变剩余持仓成本价（成本价已含买入费）
        self.assertAlmostEqual(pos["cost_price"], 10.005, places=4)
        # 审计用手续费按剩余比例结转：5 * 600/1000 = 3.0
        self.assertAlmostEqual(pos["fee"], 3.0, places=4)
        # 隔日卖出：上一交易日买入的部分已解禁
        self.assertEqual(pos["t1_qty"], 0)

    def test_full_sell_clears_position(self) -> None:
        manager = self._manager(TradeCostConfig())
        order = _sell_order(price=12.0, qty=1000, cost=10.005)
        order.create_time = "2026-07-16 10:00:00"
        execution = self._execution(
            positions=[
                {
                    "code": "600000", "quantity": 1000, "cost_price": 10.005,
                    "current_price": 12.0, "fee": 5.0,
                    "t1_qty": 1000, "t1_day": "2026-07-15",
                }
            ]
        )

        manager._apply_order_to_positions(execution, order)

        self.assertEqual(execution.positions, [])

    # ---- 剩余资金口径统一 ----

    def test_refresh_execution_metrics_counts_fee_once_via_cost_price(self) -> None:
        """手续费只通过成本价体现一次（重复扣减会算成 8990）。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._resolve_execution_days = MagicMock(return_value=30)  # type: ignore[method-assign]
        manager._resolve_benchmark_return_rate = MagicMock(return_value=None)  # type: ignore[method-assign]
        execution = self._execution(
            initial_amount=10000.0,
            current_profit=0.0,
            positions=[
                {
                    "code": "600000", "quantity": 100, "cost_price": 10.05,
                    "current_price": 10.05, "fee": 5.0,
                }
            ],
        )

        manager._refresh_execution_metrics(execution, _buy_order(), "us-1")

        # 10000 + 0 - (100 * 10.05) = 8995
        self.assertAlmostEqual(execution.remaining_cash, 8995.0, places=6)
        self.assertEqual(execution.execution_days, 30)
        # 持仓盈亏 = (现价 - 成本价) * 数量 = 0（成本价里已含买入费）
        pos = execution.positions[0]
        self.assertEqual(pos["profit_amount"], 0.0)
        self.assertEqual(pos["profit_rate"], 0.0)
        # 落库时带上 P1-27 的新字段
        saved = manager._user_strategy_api.save_execution.call_args.kwargs
        self.assertIn("execution_days", saved)
        self.assertIn("annualized_return_rate", saved)

    def test_refresh_execution_metrics_clamps_negative_cash(self) -> None:
        """持仓成本超过初始资金时，剩余资金夹取为 0，不出现负数。"""
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._resolve_execution_days = MagicMock(return_value=0)  # type: ignore[method-assign]
        manager._resolve_benchmark_return_rate = MagicMock(return_value=None)  # type: ignore[method-assign]
        execution = self._execution(
            initial_amount=10000.0,
            positions=[
                {
                    "code": "600000", "quantity": 2000, "cost_price": 10.0,
                    "current_price": 10.0, "fee": 5.0,
                }
            ],
        )

        manager._refresh_execution_metrics(execution, _buy_order(), "us-1")

        self.assertEqual(execution.remaining_cash, 0.0)

    # ---- P1-27：执行天数与年化收益率 ----

    def test_annualize_return_rate_guards(self) -> None:
        # 样本期不足 20 个交易日 → 不计算，避免短期噪声被指数放大
        self.assertEqual(TradeManager._annualize_return_rate(0.10, 5), 0.0)
        self.assertEqual(TradeManager._annualize_return_rate(0.10, 19), 0.0)
        self.assertEqual(TradeManager._annualize_return_rate(0.10, 0), 0.0)
        # 恰好一年（250 个交易日）→ 年化等于区间收益
        self.assertAlmostEqual(
            TradeManager._annualize_return_rate(0.10, 250), 0.10, places=6
        )
        # 20 个交易日赚 10% → 年化被显著放大
        self.assertGreater(TradeManager._annualize_return_rate(0.10, 20), 1.0)
        # 本金亏光 → 年化 -100%
        self.assertEqual(TradeManager._annualize_return_rate(-1.5, 250), -1.0)
        # 零收益 → 0
        self.assertEqual(TradeManager._annualize_return_rate(0.0, 250), 0.0)
        # 极端值被夹取，避免污染前端展示
        self.assertLessEqual(TradeManager._annualize_return_rate(1000.0, 20), 100.0)

    def test_resolve_execution_days_guards(self) -> None:
        manager = self._manager(TradeCostConfig())
        self.assertEqual(manager._resolve_execution_days(""), 0)
        self.assertEqual(manager._resolve_execution_days("bad"), 0)
        # 未来日期 → 0
        future = (
            datetime.date.today() + datetime.timedelta(days=10)
        ).strftime("%Y-%m-%d")
        self.assertEqual(manager._resolve_execution_days(future), 0)
        # 过去日期 → 至少 1 个交易日（不依赖具体运行日期）
        past = (
            datetime.date.today() - datetime.timedelta(days=10)
        ).strftime("%Y-%m-%d")
        days = manager._resolve_execution_days(past)
        self.assertGreaterEqual(days, 1)

    def test_resolve_benchmark_return_rate_from_kline(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._benchmark_code = "510300"  # 默认关闭，用例显式开启
        rows = [{"close": 100.0}, {"close": 105.0}, {"close": 110.0}]
        with patch("api.market.MarketApi.get_day_kline", return_value=rows):
            self.assertAlmostEqual(
                manager._resolve_benchmark_return_rate("2026-01-05"), 0.1, places=6
            )
        # 数据不足 2 条 → None（调用方保持原值，不写坏数据）
        with patch(
            "api.market.MarketApi.get_day_kline", return_value=[{"close": 100.0}]
        ):
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
        # 接口异常 → None
        with patch(
            "api.market.MarketApi.get_day_kline", side_effect=Exception("api down")
        ):
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
        # 空起始日期 → None
        self.assertIsNone(manager._resolve_benchmark_return_rate(""))

    def test_load_benchmark_code_from_cfg_and_default(self) -> None:
        # 未配置 [benchmark] → 默认关闭（后台暂无指数行情，不打无效请求）
        self.assertEqual(TradeManager._load_benchmark_code(self.cfg), "")
        # 配置了则生效
        tmp_dir = tempfile.mkdtemp(prefix="q_trading_bm_")
        cfg_path = Path(tmp_dir) / "stock.cfg"
        cfg_path.write_text("[benchmark]\ncode=000905\n", encoding="utf-8")
        self.assertEqual(TradeManager._load_benchmark_code(str(cfg_path)), "000905")
        # 显式置空 → 关闭基准计算
        cfg_empty = Path(tmp_dir) / "empty.cfg"
        cfg_empty.write_text("[benchmark]\ncode=\n", encoding="utf-8")
        self.assertEqual(TradeManager._load_benchmark_code(str(cfg_empty)), "")
        # 仓库内真实配置当前为「关闭」状态
        self.assertEqual(
            TradeManager._load_benchmark_code(resource_path("cfg/stock.cfg")),
            "",
        )

    def test_benchmark_disabled_by_default_makes_no_request(self) -> None:
        """未配置基准代码时，连取数请求都不应发出。"""
        manager = self._manager(TradeCostConfig())
        self.assertEqual(manager._benchmark_code, "")
        with patch("api.market.MarketApi.get_day_kline") as mock_kline:
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
        mock_kline.assert_not_called()

    def test_resolve_benchmark_return_rate_disabled_when_code_empty(self) -> None:
        """`code=` 置空 = 主动关闭基准：不发起任何请求，直接返回 None。"""
        manager = self._manager(TradeCostConfig())
        manager._benchmark_code = ""
        with patch("api.market.MarketApi.get_day_kline") as mock_kline:
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
        mock_kline.assert_not_called()

    def test_resolve_benchmark_return_rate_backoff_on_failure(self) -> None:
        """后台暂不支持指数行情：失败后一段时间内不再重复打无效请求。"""
        manager = self._manager(TradeCostConfig())
        manager._benchmark_code = "510300"
        with patch(
            "api.market.MarketApi.get_day_kline", return_value=[{"close": 100.0}]
        ) as mock_kline:
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
            # 数据不足 → 进入静默期
            self.assertGreater(manager._benchmark_unavailable_until, 0.0)
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
        self.assertEqual(mock_kline.call_count, 1)

    def test_resolve_benchmark_return_rate_recovers_after_backoff(self) -> None:
        """静默期到期后重试，成功后清除标记（后台补齐行情即可自动恢复）。"""
        manager = self._manager(TradeCostConfig())
        manager._benchmark_code = "510300"
        manager._benchmark_unavailable_until = time.time() + 3600
        rows = [{"close": 100.0}, {"close": 110.0}]
        with patch("api.market.MarketApi.get_day_kline", return_value=rows):
            # 静默期内不请求
            self.assertIsNone(manager._resolve_benchmark_return_rate("2026-01-05"))
            # 模拟到期
            manager._benchmark_unavailable_until = 0.0
            self.assertAlmostEqual(
                manager._resolve_benchmark_return_rate("2026-01-05"), 0.1, places=6
            )
        self.assertEqual(manager._benchmark_unavailable_until, 0.0)

    # ---- P1-17：订单本地幂等 ----

    def test_mark_order_key_submitted_at_most_once(self) -> None:
        manager = self._manager(TradeCostConfig())
        key = "us-1:600000:买入:20260715100000:abcd1234"
        self.assertTrue(manager._mark_order_key_submitted(key))
        # 同一幂等键再次登记 → False，表示应跳过重复 POST
        self.assertFalse(manager._mark_order_key_submitted(key))
        # 空键不做本地去重（兼容未生成幂等键的旧调用点）
        self.assertTrue(manager._mark_order_key_submitted(""))

    def test_save_order_via_api_skips_duplicate_client_order_id(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._order_api = MagicMock()
        manager._order_api.create.return_value = {"id": "o1"}
        order = _buy_order()
        order.client_order_id = "dup-key"

        self.assertEqual(manager._save_order_via_api(order), "o1")
        # 第二次调用（模拟 POST 超时后的重试）必须被本地幂等表拦下
        self.assertEqual(manager._save_order_via_api(order), "")
        self.assertEqual(manager._order_api.create.call_count, 1)

    def test_prune_submitted_order_keys_drops_expired(self) -> None:
        manager = self._manager(TradeCostConfig())
        with manager._submitted_keys_lock:
            manager._submitted_order_keys["expired"] = (
                time.time() - manager.ORDER_KEY_TTL_SECONDS - 1
            )
            manager._submitted_order_keys["fresh"] = time.time()
            manager._prune_submitted_order_keys()
        self.assertNotIn("expired", manager._submitted_order_keys)
        self.assertIn("fresh", manager._submitted_order_keys)

    def test_order_key_persisted_and_reloaded_across_restart(self) -> None:
        """P1-17：服务端不接收 client_order_id，幂等必须靠自己落盘跨重启生效。"""
        first = self._manager(TradeCostConfig())
        key = "us-1:600000:买入:20260715100000:abcd1234"
        self.assertTrue(first._mark_order_key_submitted(key))
        self.assertTrue(Path(self._order_key_file).exists())

        # 新进程/重启：重新构造 manager，应从文件恢复幂等键
        second = self._manager(TradeCostConfig())
        self.assertIn(key, second._submitted_order_keys)
        self.assertFalse(second._mark_order_key_submitted(key))

    def test_order_key_reload_skips_expired_entries(self) -> None:
        path = Path(self._order_key_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"k": "old-key", "t": time.time() - 2 * 24 * 3600})
            + "\n"
            + json.dumps({"k": "new-key", "t": time.time()})
            + "\n"
            + "not-json\n",
            encoding="utf-8",
        )

        manager = self._manager(TradeCostConfig())

        self.assertNotIn("old-key", manager._submitted_order_keys)
        self.assertIn("new-key", manager._submitted_order_keys)

    def test_order_key_file_missing_is_tolerated(self) -> None:
        """幂等键文件不存在时启动不得报错。"""
        Path(self._order_key_file).unlink(missing_ok=True)
        manager = self._manager(TradeCostConfig())
        self.assertEqual(manager._submitted_order_keys, {})

    def test_compact_order_keys_file_rewrites_snapshot(self) -> None:
        """只追加会让文件无限增长，超过阈值时按当前有效键重写。"""
        manager = self._manager(TradeCostConfig())
        manager.MAX_SUBMITTED_ORDER_KEYS = 3
        for i in range(6):
            manager._mark_order_key_submitted(f"key-{i}")

        content = Path(self._order_key_file).read_text(encoding="utf-8").strip().splitlines()
        # 重写后仅保留当前有效键（去掉了历史追加行）
        self.assertEqual(len(content), len(manager._submitted_order_keys))
        for line in content:
            self.assertIn(json.loads(line)["k"], manager._submitted_order_keys)

    # ---- P1-16：执行记录重试与补偿队列 ----

    def test_save_execution_with_retry_enqueues_after_all_attempts(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        manager._user_strategy_api.save_execution.side_effect = Exception("api down")
        manager.SAVE_EXECUTION_RETRY_INTERVAL = 0.0  # 去掉退避等待，加快用例
        manager._enqueue_pending_execution = MagicMock()  # type: ignore[method-assign]

        self.assertFalse(
            manager._save_execution_with_retry(self._execution(), "us-1")
        )

        self.assertEqual(
            manager._user_strategy_api.save_execution.call_count,
            TradeManager.SAVE_EXECUTION_RETRIES,
        )
        manager._enqueue_pending_execution.assert_called_once()

    def test_flush_pending_executions_replays_latest_per_user(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        tmp_dir = tempfile.mkdtemp(prefix="q_trading_pending_")
        path = Path(tmp_dir) / "pending_execution.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"enqueue_time": "t1",
                         "payload": {"user_strategy_id": "us-1", "remaining_cash": 1.0}}
                    ),
                    json.dumps(
                        {"enqueue_time": "t2",
                         "payload": {"user_strategy_id": "us-1", "remaining_cash": 2.0}}
                    ),
                    json.dumps(
                        {"enqueue_time": "t3",
                         "payload": {"user_strategy_id": "us-2", "remaining_cash": 3.0}}
                    ),
                    "not-json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("utils.tools.resource_path", return_value=str(path)):
            flushed = manager.flush_pending_executions()

        # us-1 只补投最后一条，us-2 一条 → 共 2 条
        self.assertEqual(flushed, 2)
        payloads = [
            call.kwargs
            for call in manager._user_strategy_api.save_execution.call_args_list
        ]
        self.assertEqual(
            sorted(p["user_strategy_id"] for p in payloads), ["us-1", "us-2"]
        )
        us1 = [p for p in payloads if p["user_strategy_id"] == "us-1"][0]
        self.assertEqual(us1["remaining_cash"], 2.0)
        # 全部补投成功 → 队列文件被清空
        self.assertFalse(path.exists())

    def test_flush_pending_executions_no_file(self) -> None:
        manager = self._manager(TradeCostConfig())
        manager._user_strategy_api = MagicMock()
        tmp_dir = tempfile.mkdtemp(prefix="q_trading_pending_none_")
        missing = str(Path(tmp_dir) / "nope.jsonl")
        with patch("utils.tools.resource_path", return_value=missing):
            self.assertEqual(manager.flush_pending_executions(), 0)
        manager._user_strategy_api.save_execution.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""
Author: liguoqiang
Date: 2026-08-02
Description: 策略工作流测试 —— 覆盖策略加载、状态机、分钟行情处理、
    订单去重/资金回滚、信号记录、盘前选股与定时任务。
    除真实策略类加载外，所有 API 副作用均 mock，避免污染生产数据。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from dao.order_dao import OrderDao, OrderStatus
from mq.mqtt_client import MqttTopic
from trade.manager import TradeExecutionResult
from workflow.strategy_workflow import StrategyState, StrategyWorkflow


class FakeScheduler:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = False) -> None:
        self.shutdown_called = True


def make_state(
    status: str = "running",
    cash: float = 100000.0,
    pool: list[str] | None = None,
) -> StrategyState:
    return StrategyState(
        strategy_id="sid",
        user_strategy_id="us1",
        strategy_name="强势反弹策略",
        class_path="strategy.strong_rebound_strategy",
        class_name="StrongReboundStrategy",
        status=status,
        stock_pool=pool or [],
        initial_amount=cash,
        available_cash=cash,
        max_stock_count=4,
        instance=MagicMock(),
    )


class TestStrategyWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.wf = StrategyWorkflow(pool_size=2)
        self.wf._admin_client = MagicMock()
        self.wf._user_strategy_api = MagicMock()
        self.wf._strategy_api = MagicMock()
        self.wf._pool_api = MagicMock()
        self.wf._stock_info_api = MagicMock()
        self.wf._trade_manager = MagicMock()

    def _fake_instance(self, signal: str = "BUY") -> MagicMock:
        instance = MagicMock()
        instance.strategy_name = "强势反弹策略"
        instance.handle_minute_bar.return_value = (
            True,
            {"signal": signal, "current_price": 10.0},
        )
        instance.handle_tick_bar.return_value = (
            True,
            {"signal": signal, "current_price": 10.0},
        )
        return instance

    # ---- 资金与数量 ----

    def test_calc_buy_quantity_invalid_price(self) -> None:
        state = make_state(cash=100000.0)
        self.assertEqual(self.wf._calc_buy_quantity(state, 0.0), 0)
        self.assertEqual(self.wf._calc_buy_quantity(state, -1.0), 0)

    def test_calc_buy_quantity_no_cash(self) -> None:
        state = make_state(cash=0.0)
        self.assertEqual(self.wf._calc_buy_quantity(state, 10.0), 0)

    def test_calc_buy_quantity_insufficient_for_one_lot(self) -> None:
        """可用资金不足一手（100股）时返回 0，禁止超支下单。"""
        state = make_state(cash=500.0)
        self.assertEqual(self.wf._calc_buy_quantity(state, 20.0), 0)
        state.available_cash = 1999.0
        self.assertEqual(self.wf._calc_buy_quantity(state, 20.0), 0)

    def test_calc_buy_quantity_rounds_to_lots(self) -> None:
        state = make_state(cash=100000.0)
        self.assertEqual(self.wf._calc_buy_quantity(state, 20.0), 5000)
        # 单笔上限 10 万
        state.available_cash = 500000.0
        self.assertEqual(self.wf._calc_buy_quantity(state, 20.0), 5000)

    def test_calc_available_cash(self) -> None:
        state = make_state(cash=10000.0)
        state.positions = {
            "600000": {"cost_price": 10.0, "quantity": 300},
            "600519": {"cost_price": 100.0, "quantity": 20},
        }
        self.assertEqual(self.wf._calc_available_cash(state), 5000.0)

    def test_calc_available_cash_includes_realized_profit(self) -> None:
        """剩余可用资金 = 初始资金 + 已实现盈利 - 持仓成本。"""
        state = make_state(cash=10000.0)
        state.current_profit = 2000.0
        state.positions = {"600000": {"cost_price": 10.0, "quantity": 300}}
        # 10000 + 2000 - 3000 = 9000
        self.assertEqual(self.wf._calc_available_cash(state), 9000.0)

    # ---- 策略加载 ----

    def test_load_strategy_class_valid_and_invalid(self) -> None:
        from strategy.strong_rebound_strategy import StrongReboundStrategy

        cls = self.wf._load_strategy_class(
            "strategy.strong_rebound_strategy", "StrongReboundStrategy"
        )
        self.assertIs(cls, StrongReboundStrategy)
        self.assertIsNone(
            self.wf._load_strategy_class("strategy.base_strategy", "NotFound")
        )
        self.assertIsNone(
            self.wf._load_strategy_class(
                "strategy.strong_rebound_strategy", "StrongReboundStrategyX"
            )
        )

    def test_instantiate_strategy(self) -> None:
        from strategy.strong_rebound_strategy import StrongReboundStrategy

        instance = self.wf._instantiate_strategy(StrongReboundStrategy)
        self.assertIsNotNone(instance)
        self.assertEqual(instance.strategy_name, "强势反弹策略")

    def test_build_strategy_instance(self) -> None:
        template = {
            "id": "s1",
            "name": "强势反弹策略",
            "class_path": "strategy.strong_rebound_strategy",
            "class_name": "StrongReboundStrategy",
        }
        instance = self.wf._build_strategy_instance(template)
        self.assertIsNotNone(instance)
        self.assertEqual(instance.strategy_id, "s1")
        # 缺字段返回 None
        self.assertIsNone(self.wf._build_strategy_instance({"name": "x"}))

    def test_load_strategies_from_db(self) -> None:
        self.wf._strategy_api.list.return_value = [
            {
                "id": "s1",
                "name": "强势反弹策略",
                "class_path": "strategy.strong_rebound_strategy",
                "class_name": "StrongReboundStrategy",
            }
        ]
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "status": "running",
                "pool_id": "",
                "initial_amount": 100000.0,
                "max_stock_count": 4,
            }
        ]
        self.wf._user_strategy_api.get_latest_execution.return_value = {}

        loaded = self.wf.load_strategies_from_db()

        self.assertEqual(len(loaded), 1)
        self.assertIn("s1", self.wf._strategy_instances)
        self.assertEqual(loaded[0].status, "running")
        self.assertEqual(loaded[0].available_cash, 100000.0)
        self.assertEqual(self.wf.get_all_strategies()["us1"].user_strategy_id, "us1")

    def test_load_execution_data_populates_positions(self) -> None:
        state = make_state(cash=0.0, status="stopped")
        self.wf._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 20000.0,
            "positions": [
                {"code": "600000", "cost_price": 10.0, "quantity": 200}
            ],
        }
        self.wf._load_execution_data(state)
        # UserStrategyDao 未设置 initial_amount 时，取执行数据中的值
        self.assertEqual(state.initial_amount, 20000.0)
        self.assertEqual(state.positions["600000"]["quantity"], 200)
        self.assertEqual(state.available_cash, 20000.0 - 2000.0)

    def test_load_execution_data_api_error_keeps_cash_init(self) -> None:
        state = make_state(cash=0.0, status="stopped")
        state.positions = {"600000": {"cost_price": 10.0, "quantity": 100}}
        self.wf._user_strategy_api.get_latest_execution.side_effect = Exception("api down")
        self.wf._load_execution_data(state)
        self.assertEqual(state.available_cash, 0.0)

    def test_load_execution_data_reads_current_profit(self) -> None:
        state = make_state(cash=0.0, status="stopped")
        self.wf._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 10000.0,
            "current_profit": 500.0,
            "positions": [
                {"code": "600000", "cost_price": 10.0, "quantity": 100}
            ],
        }
        self.wf._load_execution_data(state)
        self.assertEqual(state.current_profit, 500.0)
        self.assertEqual(state.available_cash, 9500.0)

    def test_load_execution_data_prefers_remaining_cash_from_record(self) -> None:
        """最新剩余资金优先取最近一条执行记录的 remaining_cash。"""
        state = make_state(cash=0.0, status="stopped")
        self.wf._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 10000.0,
            "current_profit": 500.0,
            "remaining_cash": 8800.0,
            "positions": [],
        }
        self.wf._load_execution_data(state)
        self.assertEqual(state.available_cash, 8800.0)

    def test_load_positions_prefers_remaining_cash_from_record(self) -> None:
        state = make_state(cash=0.0, status="stopped")
        self.wf._user_strategy_api.get_latest_execution.return_value = {
            "initial_amount": 10000.0,
            "current_profit": 100.0,
            "remaining_cash": 7000.0,
            "positions": [{"code": "600000", "cost_price": 10.0, "quantity": 100}],
        }
        positions = self.wf._load_positions(state, force_refresh=True)
        self.assertEqual(positions["600000"]["quantity"], 100)
        self.assertEqual(state.available_cash, 7000.0)

    def test_calc_buy_quantity_uses_realized_profit(self) -> None:
        """可用资金含已实现盈利时，可买入金额随之增加。"""
        state = make_state(cash=15000.0)
        # 可用 15000，10元股价 → 单笔上限 100000 内全用：1500 股
        self.assertEqual(self.wf._calc_buy_quantity(state, 10.0), 1500)

    # ---- 状态管理 ----

    def test_find_state_and_getters(self) -> None:
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.assertIs(self.wf._find_state("us1"), state)
        self.assertIs(self.wf.get_strategy_state("us1"), state)
        self.assertEqual(self.wf.get_all_strategies()["us1"], state)
        self.assertTrue(self.wf._is_strategy_running("sid"))
        self.assertFalse(self.wf._is_strategy_running("missing"))

    def test_set_stock_pool(self) -> None:
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.assertTrue(self.wf.set_stock_pool("us1", ["600000", "600519"]))
        self.assertEqual(state.stock_pool, ["600000", "600519"])
        self.assertFalse(self.wf.set_stock_pool("nope", []))

    def test_run_stop_pause_strategy(self) -> None:
        state = make_state(status="stopped")
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_runlog = MagicMock()

        self.assertTrue(self.wf.run_strategy("us1"))
        self.assertEqual(state.status, "running")
        self.wf._user_strategy_api.update.assert_called_with("us1", status="running")
        # 重复启动返回 False
        self.assertFalse(self.wf.run_strategy("us1"))

        self.assertTrue(self.wf.pause_strategy("us1"))
        self.assertEqual(state.status, "paused")
        self.assertTrue(self.wf.stop_strategy("us1") is False)  # paused 不能直接 stop

        self.assertTrue(self.wf.run_strategy("us1"))
        self.assertTrue(self.wf.stop_strategy("us1"))
        self.assertEqual(state.status, "stopped")

        self.assertFalse(self.wf.run_strategy("missing"))

    def test_update_strategy_status_mapping(self) -> None:
        state = make_state(status="stopped")
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_runlog = MagicMock()
        self.assertTrue(
            self.wf.update_strategy_status("us1", "running")
        )
        self.assertFalse(self.wf.update_strategy_status("us1", "unknown"))

    def test_add_user_strategy(self) -> None:
        from dao.user_strategy_dao import UserStrategyDao

        self.wf._strategy_api.get_by_id.return_value = {
            "id": "s1",
            "name": "强势反弹策略",
            "class_path": "strategy.strong_rebound_strategy",
            "class_name": "StrongReboundStrategy",
        }
        us = UserStrategyDao(
            id="us-new",
            strategy_id="s1",
            user_id="u1",
            status="stopped",
        )
        self.wf._save_runlog = MagicMock()
        self.assertTrue(self.wf.add_user_strategy(us))
        self.assertIsNotNone(self.wf._find_state("us-new"))
        # 重复添加返回 True
        self.assertTrue(self.wf.add_user_strategy(us))

    def test_delete_strategy(self) -> None:
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_runlog = MagicMock()
        self.assertTrue(self.wf.delete_strategy("us1"))
        self.assertIsNone(self.wf._find_state("us1"))
        self.assertNotIn("sid", self.wf._user_strategies)
        self.assertFalse(self.wf.delete_strategy("us1"))

    def test_update_strategy_stock_pool(self) -> None:
        state = make_state()
        self.wf._pool_api.get_by_id.return_value = {"name": "科技股池"}
        self.wf._pool_api.get_stocks.return_value = [
            {"code": "000001"},
            {"code": "600519"},
        ]
        self.wf.update_strategy_stock_pool(state, "pool-1")
        self.assertEqual(state.stock_pool, ["000001", "600519"])

    def test_reload_stock_pool(self) -> None:
        state = make_state()
        state.pool_id = "pool-1"
        self.wf._pool_api.get_by_id.return_value = {"name": "科技股池"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "000001"}]
        self.wf._reload_stock_pool(state)
        self.assertEqual(state.stock_pool, ["000001"])

    # ---- 行情处理与订单去重 ----

    def test_process_bar_buy_creates_order_once(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_buy_order = MagicMock()

        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_called_once()

        # 已有在途订单时不再下单
        state.pending_codes.add("000001")
        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_called_once()

    def test_process_bar_skips_buy_when_holding(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._load_positions = MagicMock(
            return_value={"000001": {"quantity": 100}}
        )
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_buy_order = MagicMock()

        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_not_called()

    def test_process_bar_sell_respects_pending(self) -> None:
        instance = self._fake_instance("SELL")
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._load_positions = MagicMock(
            return_value={"000001": {"cost_price": 10.0, "quantity": 100}}
        )
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_sell_order = MagicMock()

        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_sell_order.assert_called_once()

        state.pending_codes.add("000001")
        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_sell_order.assert_called_once()

    def test_process_bar_not_running_skipped(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state(status="stopped")
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_buy_order = MagicMock()

        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_not_called()

    def test_process_tick_buy(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_buy_order = MagicMock()

        self.wf._process_tick("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_called_once()

        state.pending_codes.add("000001")
        self.wf._process_tick("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_called_once()

    def test_process_bar_max_stock_count(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state()
        state.max_stock_count = 1
        self.wf._user_strategies["sid"] = [state]
        self.wf._load_positions = MagicMock(
            return_value={"600519": {"quantity": 100}}
        )
        self.wf._save_trade_signal = MagicMock()
        self.wf._create_buy_order = MagicMock()

        self.wf._process_bar("sid", instance, "000001", {})
        self.wf._create_buy_order.assert_not_called()

    # ---- 订单创建与回滚 ----

    def test_create_buy_order_deducts_cash_and_marks_pending(self) -> None:
        state = make_state(cash=10000.0)
        self.wf._submit_trade = MagicMock(return_value=True)
        self.wf._save_runlog = MagicMock()

        self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

        order = self.wf._submit_trade.call_args.args[0]
        self.assertEqual(order.entrust_quantity, 1000)
        self.assertEqual(state.available_cash, 0.0)
        self.assertIn("000001", state.pending_codes)
        self.wf._save_runlog.assert_called_once()

    def test_create_buy_order_rolls_back_on_submit_failure(self) -> None:
        state = make_state(cash=10000.0)
        self.wf._submit_trade = MagicMock(return_value=False)
        self.wf._save_runlog = MagicMock()

        self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

        self.assertEqual(state.available_cash, 10000.0)
        self.assertNotIn("000001", state.pending_codes)
        self.wf._save_runlog.assert_not_called()

    def test_create_buy_order_skips_duplicate(self) -> None:
        state = make_state(cash=10000.0)
        state.pending_codes.add("000001")
        self.wf._submit_trade = MagicMock(return_value=True)

        self.wf._create_buy_order(state, "000001", {"current_price": 10.0})

        self.wf._submit_trade.assert_not_called()
        self.assertEqual(state.available_cash, 10000.0)

    def test_create_sell_order_rolls_back_on_failure(self) -> None:
        state = make_state(cash=5000.0)
        state.positions = {"000001": {"cost_price": 10.0, "quantity": 100}}
        self.wf._load_positions = MagicMock(
            return_value={"000001": {"cost_price": 10.0, "quantity": 100}}
        )
        self.wf._submit_trade = MagicMock(return_value=False)

        self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 12.0})

        self.assertEqual(state.available_cash, 5000.0)
        self.assertNotIn("000001", state.pending_codes)

    def test_submit_trade_callback_discards_pending(self) -> None:
        state = make_state(cash=10000.0)
        state.pending_codes.add("000001")
        order = OrderDao(
            user_strategy_id="us1",
            stock_code="000001",
            entrust_quantity=100,
            trade_price=10.0,
            action="买入",
        )

        def fake_submit(order, callback, delay_seconds):
            result = TradeExecutionResult(
                order=order,
                execution_price=10.2,
                commission_fee=5.0,
                status=OrderStatus.SUCCESS.value,
                action="买入",
            )
            callback(result)
            return result

        self.wf._trade_manager.submit_order.side_effect = fake_submit
        self.wf._load_positions = MagicMock()

        ok = self.wf._submit_trade(order, state, rollback_amount=1020.0)

        self.assertTrue(ok)
        self.assertNotIn("000001", state.pending_codes)
        self.wf._load_positions.assert_called_with(state, force_refresh=True)

    def test_submit_trade_rolls_back_on_failed_status(self) -> None:
        state = make_state(cash=5000.0)
        state.pending_codes.add("000001")
        order = OrderDao(
            user_strategy_id="us1",
            stock_code="000001",
            entrust_quantity=100,
            trade_price=10.0,
            action="买入",
        )

        def fake_submit(order, callback, delay_seconds):
            result = TradeExecutionResult(
                order=order,
                execution_price=10.2,
                commission_fee=5.0,
                status=OrderStatus.FAILED.value,
                action="买入",
            )
            callback(result)
            return result

        self.wf._trade_manager.submit_order.side_effect = fake_submit
        self.wf._load_positions = MagicMock()

        self.wf._submit_trade(order, state, rollback_amount=1020.0)

        # 失败回滚：5000 + 1020
        self.assertEqual(state.available_cash, 6020.0)
        self.assertNotIn("000001", state.pending_codes)

    def test_submit_trade_returns_false_on_exception(self) -> None:
        state = make_state()
        order = OrderDao(
            user_strategy_id="us1",
            stock_code="000001",
            entrust_quantity=100,
            trade_price=10.0,
            action="买入",
        )
        self.wf._trade_manager.submit_order.side_effect = Exception("submit failed")

        self.assertFalse(self.wf._submit_trade(order, state))

    # ---- 信号记录 ----

    def test_save_trade_signal_buy(self) -> None:
        self.wf._trade_signal_api = MagicMock()
        self.wf._trade_signal_api.latest.return_value = None  # 无历史信号，不过滤
        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            mock_mqtt = MagicMock()
            mock_mqtt.publish.return_value = True
            mock_ctx.return_value.mqtt_client = mock_mqtt
            result = self.wf._save_trade_signal(
                "sid", "000001", {"signal": "BUY", "current_price": 10.5}
            )
            self.assertTrue(result)
            self.assertTrue(mock_mqtt.publish.called)
            call_args = mock_mqtt.publish.call_args
            self.assertEqual(call_args.args[0], MqttTopic.STOCK_TRADING_SIGNAL)
            payload = json.loads(call_args.args[1])
            self.assertEqual(payload["strategy_id"], "sid")
            self.assertEqual(payload["stock_code"], "000001")
            self.assertEqual(payload["action"], "买入")

    def test_save_trade_signal_buy_suppressed_by_filter(self) -> None:
        """连续相同买入方向且时间间隔 < 10分钟 → 信号被抑制。"""
        from datetime import datetime, timedelta
        recent_time: str = (datetime.now() - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        self.wf._trade_signal_api = MagicMock()
        self.wf._trade_signal_api.latest.return_value = {
            "action": "买入", "trade_price": 10.4, "create_time": recent_time,
        }
        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            mock_mqtt = MagicMock()
            mock_mqtt.publish.return_value = True
            mock_ctx.return_value.mqtt_client = mock_mqtt
            result = self.wf._save_trade_signal(
                "sid", "000001", {"signal": "BUY", "current_price": 10.5}
            )
            # 间隔 3 分钟 < 10 分钟 → 抑制
            self.assertFalse(result)
            mock_mqtt.publish.assert_not_called()

    def test_save_trade_signal_sell_computes_profit(self) -> None:
        # 第一次 latest（信号过滤）：action 为空不会匹配 → 不过滤
        # 第二次 latest（盈亏计算）：返回买入价 10.0
        self.wf._trade_signal_api = MagicMock()
        self.wf._trade_signal_api.latest.side_effect = [
            {},  # 信号过滤：无 action → 不匹配
            {"trade_price": 10.0},  # 盈亏计算
        ]
        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            mock_mqtt = MagicMock()
            mock_mqtt.publish.return_value = True
            mock_ctx.return_value.mqtt_client = mock_mqtt
            result = self.wf._save_trade_signal(
                "sid", "000001", {"signal": "SELL", "current_price": 12.0}
            )
            self.assertTrue(result)
            self.assertTrue(mock_mqtt.publish.called)
            call_args = mock_mqtt.publish.call_args
            self.assertEqual(call_args.args[0], MqttTopic.STOCK_TRADING_SIGNAL)
            payload = json.loads(call_args.args[1])
            self.assertEqual(payload["action"], "卖出")
            self.assertAlmostEqual(payload["profit_rate"], 20.0)
            self.assertAlmostEqual(payload["profit_amount"], 2.0)

    # ---- 盘前任务与生命周期 ----

    def test_daily_before_trading(self) -> None:
        instance = MagicMock()
        instance.strategy_name = "强势反弹策略"
        instance.before_trading.return_value = None
        instance.select.return_value = [{"code": "000001", "matched": True}]
        state = make_state()
        self.wf._strategy_instances = {"sid": instance}
        self.wf._user_strategies = {"sid": [state]}
        self.wf._stock_info_api.get_hot_industries.return_value = [{"name": "科技"}]
        self.wf._stock_info_api.get_by_industry.return_value = [{"code": "600519"}]
        self.wf._strategy_select_stock_api = MagicMock()
        self.wf._save_runlog = MagicMock()

        self.wf.daily_before_trading()

        instance.select.assert_called_once()
        self.wf._strategy_select_stock_api.add.assert_called_once_with(
            "sid", ["000001"]
        )
        self.wf._save_runlog.assert_called()

    def test_daily_before_trading_skips_stopped_strategy(self) -> None:
        instance = MagicMock()
        state = make_state(status="stopped")
        self.wf._strategy_instances = {"sid": instance}
        self.wf._user_strategies = {"sid": [state]}
        self.wf._stock_info_api.get_hot_industries.return_value = []

        self.wf.daily_before_trading()

        instance.select.assert_not_called()

    def test_on_start_and_on_stop(self) -> None:
        self.wf.load_strategies_from_db = MagicMock()
        fake_scheduler = FakeScheduler()
        self.wf._setup_scheduler = MagicMock(
            side_effect=lambda: setattr(self.wf, "_scheduler", fake_scheduler)
        )
        state = make_state(status="running")
        self.wf._user_strategies = {"sid": [state]}
        self.wf._save_runlog = MagicMock()

        self.wf.on_start()
        self.assertTrue(fake_scheduler.started)

        self.wf.on_stop()
        self.assertTrue(fake_scheduler.shutdown_called)
        self.assertEqual(state.status, "paused")
        self.assertEqual(self.wf._strategy_instances, {})
        self.assertEqual(self.wf._user_strategies, {})

    def test_setup_scheduler_reads_config(self) -> None:
        self.wf._setup_scheduler()
        self.assertIsNotNone(self.wf._scheduler)
        job = self.wf._scheduler.get_job("daily_before_trading")
        self.assertIsNotNone(job)
        self.wf._scheduler.start()
        self.wf._scheduler.shutdown(wait=False)

    def test_handle_bar_rejects_non_list_payload(self) -> None:
        self.wf._running = True
        self.wf.handle_bar("topic", {"code": "000001"})  # 不应抛异常
        self.wf.handle_tick("topic", "not-a-list")

    def test_handle_bar_dispatches_tasks(self) -> None:
        self.wf._running = True
        instance = self._fake_instance("BUY")
        self.wf._strategy_instances = {"sid": instance}
        state = make_state()
        self.wf._user_strategies = {"sid": [state]}
        self.wf.submit = MagicMock()

        self.wf.handle_bar("topic", [{"code": "000001"}])
        self.wf.submit.assert_called()

    # ---- 更多分支覆盖 ----

    def test_init_without_admin_token(self) -> None:
        with patch(
            "workflow.strategy_workflow.tools.load_admin_token", return_value=""
        ):
            wf = StrategyWorkflow(pool_size=1)
        self.assertIsNotNone(wf._trade_manager)

    def test_load_strategy_class_not_subclass(self) -> None:
        self.assertIsNone(
            self.wf._load_strategy_class("strategy.base_strategy", "ABC")
        )

    def test_instantiate_abstract_strategy_fails(self) -> None:
        from strategy.base_strategy import BaseStrategy

        self.assertIsNone(self.wf._instantiate_strategy(BaseStrategy))

    def test_update_strategy_stock_pool_edge_cases(self) -> None:
        state = make_state()
        self.wf._pool_api.get_by_id.return_value = None
        self.wf.update_strategy_stock_pool(state, "pool-x")
        self.wf._pool_api.get_by_id.return_value = {"name": ""}
        self.wf.update_strategy_stock_pool(state, "pool-x")
        self.wf._pool_api.get_by_id.side_effect = Exception("api down")
        self.wf.update_strategy_stock_pool(state, "pool-x")
        # 空 pool_id 直接返回
        self.wf.update_strategy_stock_pool(state, "")

    def test_reload_stock_pool_edge_cases(self) -> None:
        state = make_state()
        state.pool_id = "pool-1"
        self.wf._pool_api.get_by_id.return_value = None
        self.wf._reload_stock_pool(state)
        self.wf._pool_api.get_by_id.return_value = {"name": ""}
        self.wf._reload_stock_pool(state)
        self.wf._pool_api.get_by_id.side_effect = Exception("api down")
        self.wf._reload_stock_pool(state)
        state.pool_id = ""
        self.wf._reload_stock_pool(state)

    def test_load_strategies_from_db_error_paths(self) -> None:
        self.wf._strategy_api.list.side_effect = Exception("api down")
        self.assertEqual(self.wf.load_strategies_from_db(), [])
        self.wf._strategy_api.list.side_effect = None
        self.wf._strategy_api.list.return_value = []
        self.assertEqual(self.wf.load_strategies_from_db(), [])

        self.wf._strategy_api.list.return_value = [
            {
                "id": "s1",
                "name": "强势反弹策略",
                "class_path": "strategy.strong_rebound_strategy",
                "class_name": "StrongReboundStrategy",
            }
        ]
        self.wf._user_strategy_api.list_all.side_effect = Exception("api down")
        self.assertEqual(self.wf.load_strategies_from_db(), [])

        # 用户策略缺少 strategy_id 或关联实例缺失
        self.wf._user_strategy_api.list_all.side_effect = None
        self.wf._user_strategy_api.list_all.return_value = [
            {"id": "us-bad", "strategy_id": ""},
            {"id": "us-miss", "strategy_id": "s-unknown", "status": "stopped"},
        ]
        self.wf._user_strategy_api.get_latest_execution.return_value = {}
        loaded = self.wf.load_strategies_from_db()
        self.assertEqual(len(loaded), 0)

    def test_run_stop_pause_edge_branches(self) -> None:
        state = make_state(status="running")
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_runlog = MagicMock()
        # 已在运行 → 重复启动失败
        self.assertFalse(self.wf.run_strategy("us1"))
        # 实例为空 → 启动失败
        state.instance = None
        state.status = "stopped"
        self.assertFalse(self.wf.run_strategy("us1"))
        # 未在运行 → stop/pause 失败
        state.instance = MagicMock()
        self.assertFalse(self.wf.stop_strategy("us1"))
        self.assertFalse(self.wf.pause_strategy("us1"))
        # 未找到 → 失败
        self.assertFalse(self.wf.stop_strategy("nope"))
        self.assertFalse(self.wf.pause_strategy("nope"))
        # 状态同步失败不影响本地状态切换
        self.wf._user_strategy_api.update.side_effect = Exception("api down")
        state.status = "running"
        self.assertTrue(self.wf.stop_strategy("us1"))
        state.status = "running"
        self.assertTrue(self.wf.pause_strategy("us1"))
        state.status = "stopped"
        self.assertTrue(self.wf.run_strategy("us1"))

    def test_add_user_strategy_error_branches(self) -> None:
        from dao.user_strategy_dao import UserStrategyDao

        us = UserStrategyDao(id="us1", strategy_id="", user_id="")
        self.assertFalse(self.wf.add_user_strategy(us))
        us = UserStrategyDao(id="", strategy_id="s1", user_id="u1")
        self.assertFalse(self.wf.add_user_strategy(us))

        us = UserStrategyDao(id="us-x", strategy_id="s-x", user_id="u1")
        self.wf._strategy_api.get_by_id.side_effect = Exception("api down")
        self.assertFalse(self.wf.add_user_strategy(us))
        self.wf._strategy_api.get_by_id.side_effect = None
        self.wf._strategy_api.get_by_id.return_value = {}
        self.assertFalse(self.wf.add_user_strategy(us))

    def test_delete_strategy_not_found_and_api_error(self) -> None:
        self.assertFalse(self.wf.delete_strategy("nope"))
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_runlog = MagicMock()
        self.wf._user_strategy_api.delete.side_effect = Exception("api down")
        self.assertTrue(self.wf.delete_strategy("us1"))

    def test_handle_bar_tick_skip_invalid_items(self) -> None:
        self.wf._running = True
        instance = self._fake_instance("BUY")
        self.wf._strategy_instances = {"sid": instance}
        state = make_state()
        self.wf._user_strategies = {"sid": [state]}
        self.wf.submit = MagicMock()

        self.wf.handle_bar("topic", [{"code": ""}, "not-dict"])
        self.wf.handle_tick("topic", [{"code": ""}, "not-dict"])
        # 策略未运行 → 不派发
        state.status = "stopped"
        self.wf.handle_bar("topic", [{"code": "000001"}])
        self.wf.submit.assert_not_called()
        self.wf.handle_tick("topic", [{"code": "000001"}])

    def test_process_bar_not_matched_and_unknown_signal(self) -> None:
        instance = MagicMock()
        instance.strategy_name = "强势反弹策略"
        instance.handle_minute_bar.return_value = (False, {"reason": "no signal"})
        state = make_state()
        self.wf._user_strategies["sid"] = [state]
        self.wf._process_bar("sid", instance, "000001", {})  # 未匹配

        instance.handle_minute_bar.return_value = (True, {"signal": "UNKNOWN"})
        self.wf._save_trade_signal = MagicMock()
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._process_bar("sid", instance, "000001", {})  # 未知信号

    def test_process_bar_stock_pool_filter_and_sell_no_position(self) -> None:
        instance = self._fake_instance("SELL")
        state = make_state(pool=["600519"])
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_trade_signal = MagicMock()
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._create_sell_order = MagicMock()
        self.wf._process_bar("sid", instance, "000001", {})  # 不在股票池
        self.wf._create_sell_order.assert_not_called()

        state.stock_pool = []
        self.wf._process_bar("sid", instance, "000001", {})  # 无持仓跳过卖出
        self.wf._create_sell_order.assert_not_called()

    def test_process_tick_not_matched_and_pool_filter(self) -> None:
        instance = self._fake_instance("BUY")
        state = make_state(pool=["600519"])
        self.wf._user_strategies["sid"] = [state]
        self.wf._save_trade_signal = MagicMock()
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._create_buy_order = MagicMock()
        self.wf._process_tick("sid", instance, "000001", {})  # 不在股票池
        self.wf._create_buy_order.assert_not_called()

        instance.handle_tick_bar.return_value = (False, {"reason": "no"})
        self.wf._process_tick("sid", instance, "000001", {})  # 未匹配

    def test_save_trade_signal_latest_missing_and_errors(self) -> None:
        self.wf._trade_signal_api = MagicMock()
        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            mock_mqtt = MagicMock()
            mock_mqtt.publish.return_value = True
            mock_ctx.return_value.mqtt_client = mock_mqtt

            # 卖出但查不到最近买入 → 盈亏为 0
            self.wf._trade_signal_api.latest.return_value = None
            self.wf._save_trade_signal(
                "sid", "000001", {"signal": "SELL", "current_price": 12.0}
            )
            payload = json.loads(mock_mqtt.publish.call_args.args[1])
            self.assertEqual(payload["profit_rate"], 0.0)
            # API add 应该被调用
            self.wf._trade_signal_api.add.assert_called()

            # 查询买入记录异常 → 不影响信号保存，API + MQTT 仍然执行
            self.wf._trade_signal_api.latest.side_effect = Exception("api down")
            self.wf._trade_signal_api.add.reset_mock()
            self.wf._save_trade_signal(
                "sid", "000001", {"signal": "SELL", "current_price": 12.0}
            )
            self.wf._trade_signal_api.add.assert_called()

            # API 保存异常 → 返回 False
            self.wf._trade_signal_api.latest.side_effect = None
            self.wf._trade_signal_api.latest.return_value = None
            self.wf._trade_signal_api.add.side_effect = Exception("api down")
            result = self.wf._save_trade_signal(
                "sid", "000001", {"signal": "BUY", "current_price": 12.0}
            )
            self.assertFalse(result)

    def test_create_buy_order_invalid_price_and_no_cash(self) -> None:
        state = make_state(cash=0.0)
        self.wf._submit_trade = MagicMock(return_value=True)
        self.wf._save_runlog = MagicMock()
        self.wf._create_buy_order(state, "000001", {"current_price": 0})
        self.wf._submit_trade.assert_not_called()
        self.wf._create_buy_order(state, "000001", {"current_price": 10.0})
        self.wf._submit_trade.assert_not_called()

    def test_create_sell_order_branches(self) -> None:
        state = make_state(cash=5000.0)
        self.wf._load_positions = MagicMock(return_value={})
        self.wf._submit_trade = MagicMock(return_value=True)
        self.wf._save_runlog = MagicMock()
        # 无持仓
        self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 12.0})
        self.wf._submit_trade.assert_not_called()
        # 有持仓但价格无效
        self.wf._load_positions = MagicMock(
            return_value={"000001": {"cost_price": 10.0, "quantity": 100}}
        )
        self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 0})
        self.wf._submit_trade.assert_not_called()
        # 在途去重
        state.pending_codes.add("000001")
        self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 12.0})
        self.wf._submit_trade.assert_not_called()
        # 成功路径
        state.pending_codes.clear()
        self.wf._create_sell_order(state, "000001", 10.0, {"current_price": 12.0})
        self.wf._submit_trade.assert_called_once()
        self.wf._save_runlog.assert_called_once()

    def test_submit_trade_with_on_complete_callback(self) -> None:
        state = make_state()
        order = OrderDao(
            user_strategy_id="us1",
            stock_code="000001",
            entrust_quantity=100,
            trade_price=10.0,
            action="买入",
        )

        def fake_submit(order, callback, delay_seconds):
            result = TradeExecutionResult(
                order=order,
                execution_price=10.2,
                commission_fee=5.0,
                status=OrderStatus.SUCCESS.value,
                action="买入",
            )
            callback(result)
            return result

        self.wf._trade_manager.submit_order.side_effect = fake_submit
        self.wf._load_positions = MagicMock()
        extra = MagicMock()

        ok = self.wf._submit_trade(order, state, on_complete=extra)

        self.assertTrue(ok)
        extra.assert_called_once()

    def test_load_positions_cache_and_error(self) -> None:
        state = make_state()
        state.positions = {"000001": {"quantity": 100}}
        self.assertEqual(self.wf._load_positions(state), {"000001": {"quantity": 100}})
        # 无 user_strategy_id
        empty = StrategyState(
            strategy_id="s",
            user_strategy_id="",
            strategy_name="t",
            class_path="x",
            class_name="y",
        )
        self.assertEqual(self.wf._load_positions(empty, force_refresh=True), {})
        # API 异常
        state.positions = {}
        self.wf._user_strategy_api.get_latest_execution.side_effect = Exception(
            "api down"
        )
        self.assertEqual(self.wf._load_positions(state, force_refresh=True), {})

    def test_save_runlog_no_id_and_error(self) -> None:
        empty = StrategyState(
            strategy_id="s",
            user_strategy_id="",
            strategy_name="t",
            class_path="x",
            class_name="y",
        )
        self.wf._save_runlog(empty, "msg")
        self.wf._user_strategy_api.save_runlog.side_effect = Exception("api down")
        self.wf._save_runlog(make_state(), "msg")

    def test_daily_before_trading_error_branches(self) -> None:
        instance = MagicMock()
        instance.strategy_name = "强势反弹策略"
        instance.select.return_value = [{"code": "000001", "matched": True}]
        state = make_state()
        self.wf._strategy_instances = {"sid": instance}
        self.wf._user_strategies = {"sid": [state]}
        self.wf._admin_client.post.return_value = {"code": 0}
        self.wf._save_runlog = MagicMock()

        # 热门行业查询失败
        self.wf._stock_info_api.get_hot_industries.side_effect = Exception("api down")
        self.wf.daily_before_trading()
        self.wf._stock_info_api.get_hot_industries.side_effect = None

        # 行业股票查询失败
        self.wf._stock_info_api.get_hot_industries.return_value = [{"name": "科技"}]
        self.wf._stock_info_api.get_by_industry.side_effect = Exception("api down")
        self.wf.daily_before_trading()
        self.wf._stock_info_api.get_by_industry.side_effect = None

        # before_trading 异常
        instance.before_trading.side_effect = Exception("boom")
        self.wf.daily_before_trading()
        instance.before_trading.side_effect = None

        # select 异常
        instance.select.side_effect = Exception("boom")
        self.wf.daily_before_trading()
        instance.select.side_effect = None

    def test_daily_before_trading_empty_combined(self) -> None:
        instance = MagicMock()
        instance.strategy_name = "强势反弹策略"
        state = make_state()
        self.wf._strategy_instances = {"sid": instance}
        self.wf._user_strategies = {"sid": [state]}
        self.wf._stock_info_api.get_hot_industries.return_value = []

        self.wf.daily_before_trading()

        instance.select.assert_not_called()


if __name__ == "__main__":
    unittest.main()

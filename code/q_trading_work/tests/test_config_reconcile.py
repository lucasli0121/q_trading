"""配置热更新（对账）测试。

背景：本端原先只在进程启动时读一次用户策略配置（`_init_workflow` →
`load_strategies_from_db`），网上改了策略参数或股票池里的股票要重启进程才生效。
修复由两条通道组成：服务端写库后 publish 变更通知 + 本端定时对账兜底，
本文件覆盖本端这一侧的对账语义。

用例重点不是「有没有拉到新数据」，而是几个错了不会报错、只会静默跑偏的地方：

- 分配关系查询失败时**不能**把本地策略删掉（fail-closed）：删了等于本实例不再
  跑这些策略，而多实例下它们又未必被别的实例接住；
- 池内容/池绑定变化后要按需**补跑当日选股**：策略的买入判定读「当日选股结果」，
  池里新加的票不补选就永远进不了买入分支；
- 补跑不能清当日缓存（`reset_cache=False`）：分钟缓存与区间最高价是行情数据，
  与配置无关，清掉等于当天已跑的行情作废；
- 参数「有没有变」要按**解析后的生效参数**判定：三级回退是「择一」，
  原始字典变了生效值未必变。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from strategy.base_strategy import BaseStrategy
from workflow.strategy_workflow import StrategyState, StrategyWorkflow

STRONG_TEMPLATE: dict[str, object] = {
    "id": "s1",
    "name": "强趋势策略",
    "class_path": "strategy.strong_rebound_strategy",
    "class_name": "StrongReboundStrategy",
}


class _Fake(BaseStrategy):
    """最小策略子类：只有一个可热更新的类属性，用于验证二次 apply 的基线语义。"""

    strategy_name: str = "会话测试策略"

    MIN_SCORE: float = 1.0

    def init_factors(self) -> None:
        pass

    def is_match_strategy(self, stock_data: list[dict]) -> tuple[bool, dict]:
        return False, {}

    def apply_params(self, params: dict) -> None:
        self.MIN_SCORE = params.get("min_score", self.MIN_SCORE)


def make_workflow(service_name: str = "") -> StrategyWorkflow:
    """装配一个所有外部副作用都被替身的 workflow。"""
    wf = StrategyWorkflow(pool_size=1)
    wf._admin_client = MagicMock()
    wf._admin_client.get.return_value = []
    wf._strategy_api = MagicMock()
    wf._user_strategy_api = MagicMock()
    wf._user_strategy_api.get_latest_execution.return_value = {}
    wf._user_strategy_api.get.return_value = {}
    wf._pool_api = MagicMock()
    wf._stock_info_api = MagicMock()
    wf._stock_info_api.get_hot_industries.return_value = []
    wf._strategy_select_stock_api = MagicMock()
    wf._workflow_service_api = MagicMock()
    wf._trade_manager = MagicMock()
    wf._notifier = MagicMock()
    wf._cache = MagicMock()
    wf._save_runlog = MagicMock()
    wf._service_name = service_name
    wf._running = True
    wf.xshg = MagicMock()
    wf.xshg.is_session.return_value = True
    # 交易时段与 MQTT 订阅都换成可控替身：用例不应依赖执行时刻，也不应真的连 MQTT
    wf._is_trading_hours = MagicMock(return_value=True)  # type: ignore[method-assign]
    wf.subscribe_pool = _fake_subscribe(wf)
    wf.unsubscribe_pool = _fake_unsubscribe(wf)
    return wf


def _fake_subscribe(wf: StrategyWorkflow):
    def _subscribe(pool_id: str) -> bool:
        if pool_id:
            with wf._pool_lock:
                wf._pool_ids.add(pool_id)
        return True

    return MagicMock(side_effect=_subscribe)


def _fake_unsubscribe(wf: StrategyWorkflow):
    def _unsubscribe(pool_id: str) -> None:
        with wf._pool_lock:
            wf._pool_ids.discard(pool_id)

    return MagicMock(side_effect=_unsubscribe)


def make_state(
    status: str = "running",
    pool: list[str] | None = None,
    pool_id: str = "",
    instance: object | None = None,
) -> StrategyState:
    return StrategyState(
        strategy_id="s1",
        user_strategy_id="us1",
        strategy_name="强趋势策略",
        class_path="strategy.strong_rebound_strategy",
        class_name="StrongReboundStrategy",
        status=status,
        stock_pool=list(pool or []),
        pool_id=pool_id,
        initial_amount=100000.0,
        available_cash=100000.0,
        # 与服务端 item 里的 initial_amount 保持一致：字段有值即视为「显式配置」，
        # 否则对账会把「配置标记从 False 变 True」当成一次真实变更
        initial_amount_configured=True,
        max_stock_count=-1,
        instance=instance if instance is not None else MagicMock(),
    )


class TestReconcilePoolChanges(unittest.TestCase):
    """股票池相关变更：内容刷新、绑定重订阅、补跑选股。"""

    def setUp(self) -> None:
        self.wf = make_workflow()
        self.state = make_state(pool=["000001"], pool_id="p1")
        self.state.user_strategy_name = "爸爸的强趋势"
        self.wf._user_strategies = {"s1": [self.state]}
        # 模拟启动期已订阅该池
        self.wf._pool_ids = {"p1"}
        self.wf._strategy_templates = {"s1": dict(STRONG_TEMPLATE)}
        self.wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "name": "爸爸的强趋势",
                "status": "running",
                "pool_id": "p1",
                "max_stock_count": -1,
                "initial_amount": 100000.0,
                "strategy_params": {"min_score": 1.0},
            }
        ]
        # 参数未变化的基线：实例上的生效参数与传入的一致
        self.state.instance.strategy_params = {"min_score": 1.0}
        self.state.instance.user_strategy_name = "强趋势策略"
        self.state.instance.requires_daily_selection = True
        self.wf._may_refresh = MagicMock(return_value=True)
        self.wf._maybe_refresh_selection = self.wf._may_refresh  # type: ignore[method-assign]

    def test_pool_content_change_refreshes_and_reruns_selection(self) -> None:
        """同一个池里加了股票：刷新 stock_pool 并触发补跑选股。"""
        self.wf._pool_api.get_by_id.return_value = {"name": "科技池"}
        self.wf._pool_api.get_stocks.return_value = [
            {"code": "000001"},
            {"code": "600519"},
        ]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], ["us1"])
        self.assertEqual(self.state.stock_pool, ["000001", "600519"])
        self.wf._may_refresh.assert_called_once()
        # 池绑定没变，不该重新订阅
        self.wf.subscribe_pool.assert_not_called()

    def test_pool_content_unchanged_is_not_reported(self) -> None:
        """池内容没变时不算变更，也不补跑选股（避免每分钟白跑一次全池选股）。"""
        self.wf._pool_api.get_by_id.return_value = {"name": "科技池"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "000001"}]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], [])
        self.wf._may_refresh.assert_not_called()

    def test_pool_rebinding_subscribes_new_pool(self) -> None:
        """换池：必须订阅新池的行情 topic，否则新池的行情永远收不到。"""
        self.wf._user_strategy_api.list_all.return_value[0]["pool_id"] = "p2"
        self.wf._pool_api.get_by_id.return_value = {"name": "新池"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "600519"}]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], ["us1"])
        self.assertEqual(self.state.pool_id, "p2")
        self.wf.subscribe_pool.assert_called_once_with("p2")
        self.assertEqual(self.state.stock_pool, ["600519"])
        # 旧池没人引用了 → 退订
        self.wf.unsubscribe_pool.assert_called_once_with("p1")
        self.wf._may_refresh.assert_called_once()

    def test_unbinding_pool_clears_stock_list(self) -> None:
        """解除池绑定：股票列表清空，且不残留订阅。"""
        self.wf._user_strategy_api.list_all.return_value[0]["pool_id"] = ""

        self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(self.state.pool_id, "")
        self.assertEqual(self.state.stock_pool, [])
        self.wf.unsubscribe_pool.assert_called_once_with("p1")

    def test_pool_kept_when_another_strategy_still_uses_it(self) -> None:
        """仍有策略引用该池时不得退订。"""
        other = make_state(pool=["600519"], pool_id="p1")
        other.user_strategy_id = "us2"
        self.wf._user_strategies["s1"].append(other)
        self.wf._user_strategy_api.list_all.return_value.append(
            {
                "id": "us2",
                "strategy_id": "s1",
                "status": "running",
                "pool_id": "p1",
                "strategy_params": {"min_score": 1.0},
            }
        )
        other.instance.strategy_params = {"min_score": 1.0}
        other.instance.requires_daily_selection = True
        self.wf._pool_api.get_by_id.return_value = {"name": "科技池"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "000001"}]
        self.wf._pool_ids = {"p1"}

        self.wf.reconcile_user_strategies(reason="test")

        self.wf.unsubscribe_pool.assert_not_called()


class TestReconcileParams(unittest.TestCase):
    """策略参数热更新。"""

    def setUp(self) -> None:
        self.wf = make_workflow()
        self.instance = MagicMock()
        self.instance.strategy_params = {"min_score": 1.0}
        self.instance.requires_daily_selection = True
        self.state = make_state(instance=self.instance)
        self.wf._user_strategies = {"s1": [self.state]}
        self.wf._strategy_templates = {"s1": dict(STRONG_TEMPLATE)}
        self.wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        self.wf._run_daily_selection_for_state = MagicMock()

    def _item(self, params: dict | None) -> dict:
        return {
            "id": "us1",
            "strategy_id": "s1",
            "status": "running",
            "pool_id": "",
            "max_stock_count": -1,
            "initial_amount": 100000.0,
            "strategy_params": params,
        }

    def test_unchanged_params_do_not_reload(self) -> None:
        self.wf._user_strategy_api.list_all.return_value = [self._item({"min_score": 1.0})]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], [])
        self.instance.reload_params.assert_not_called()

    def test_changed_params_reload_instance(self) -> None:
        """生效参数变了 → 调 reload_params，且传的是服务端最新的两级参数。"""
        self.wf._user_strategy_api.list_all.return_value = [self._item({"min_score": 6.0})]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], ["us1"])
        self.instance.reload_params.assert_called_once_with(
            {"min_score": 6.0}, None
        )
        # 参数变化会影响选股结果 → 需要补跑
        self.assertTrue(summary["selection_refreshed"])

    def test_clearing_user_params_falls_back_to_template(self) -> None:
        """用户参数被清空 → 生效值落到模板默认参数（三级回退是「择一」）。"""
        template = dict(STRONG_TEMPLATE)
        template["default_params"] = {"min_score": {"desc": "买入门槛", "value": 3.0}}
        self.wf._strategy_api.list.return_value = [template]
        self.wf._strategy_templates = {"s1": template}
        self.wf._user_strategy_api.list_all.return_value = [self._item({})]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], ["us1"])
        # default_params 的 {desc, value} 包装必须在比较前解包，否则与生效值永远不等
        self.instance.reload_params.assert_called_once_with(
            {}, template["default_params"]
        )

    def test_same_effective_value_but_different_raw_is_not_reloaded(self) -> None:
        """原始参数变了但生效值没变（如包装格式差异）→ 不重载。"""
        template = dict(STRONG_TEMPLATE)
        template["default_params"] = {"min_score": {"value": 1.0}}
        self.wf._strategy_api.list.return_value = [template]
        self.wf._strategy_templates = {"s1": template}
        self.wf._user_strategy_api.list_all.return_value = [self._item({})]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["updated"], [])
        self.instance.reload_params.assert_not_called()


class TestReconcileAddRemove(unittest.TestCase):
    """用户策略的增删同步。"""

    def setUp(self) -> None:
        self.wf = make_workflow()
        self.wf._strategy_templates = {"s1": dict(STRONG_TEMPLATE)}
        self.wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        self.wf._user_strategy_api.get_latest_execution.return_value = {}
        self.wf._user_strategy_api.get.return_value = {}
        self.wf._save_runlog = MagicMock()
        self.wf.subscribe_pool = _fake_subscribe(self.wf)

    def test_new_user_strategy_is_added(self) -> None:
        """界面上新建的用户策略会被自动加载（不必重启进程）。"""
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us-new",
                "strategy_id": "s1",
                "name": "新策略",
                "status": "running",
                "pool_id": "p9",
                "max_stock_count": -1,
                "initial_amount": 100000.0,
            }
        ]
        self.wf._pool_api.get_by_id.return_value = {"name": "池9"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "600519"}]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["added"], ["us-new"])
        state = self.wf.get_all_strategies()["us-new"]
        self.assertEqual(state.user_strategy_name, "新策略")
        self.assertEqual(state.stock_pool, ["600519"])
        self.wf.subscribe_pool.assert_called_once_with("p9")

    def test_deleted_user_strategy_is_removed(self) -> None:
        """服务端已删除的策略要从运行表移除并清掉缓存。"""
        state = make_state()
        self.wf._user_strategies = {"s1": [state]}
        self.wf._user_strategy_api.list_all.return_value = []

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary["removed"], ["us1"])
        self.assertEqual(self.wf.get_all_strategies(), {})
        self.wf._cache.clear_user.assert_called_once_with("us1")

    def test_assignment_lookup_failure_is_fail_closed(self) -> None:
        """分配关系查不到：不新增、不删除，只做已有策略的字段热更新。

        这条最危险：查询失败时若把「没查到分配」当成「没分配给我」，本地策略会被
        全部移除，而多实例下它们又未必被别的实例接住 —— 表现是「策略静默停止，
        但界面上还是运行中」。
        """
        self.wf._service_name = "workflow_service_1"
        self.wf._workflow_service_api.list_by_service.side_effect = Exception("api down")
        self.wf._workflow_service_api.list.side_effect = Exception("api down")
        state = make_state()
        self.wf._user_strategies = {"s1": [state]}
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "name": "改名了",
                "status": "running",
                "strategy_params": {"min_score": 1.0},
            }
        ]
        state.instance.strategy_params = {"min_score": 1.0}

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertFalse(summary["assign_ok"])
        self.assertEqual(summary["removed"], [])
        self.assertEqual(summary["added"], [])
        # 仍然热更新了字段
        self.assertEqual(summary["updated"], ["us1"])
        self.assertEqual(state.user_strategy_name, "改名了")

    def test_assignment_empty_is_treated_as_no_assignment(self) -> None:
        """接口正常返回空列表 ≠ 查询失败：这种情况应当移除本地策略。"""
        self.wf._service_name = "workflow_service_1"
        self.wf._workflow_service_api.list_by_service.return_value = []
        self.wf._workflow_service_api.list.return_value = []
        self.wf._user_strategies = {"s1": [make_state()]}
        self.wf._user_strategy_api.list_all.return_value = [
            {"id": "us1", "strategy_id": "s1", "status": "running"}
        ]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertTrue(summary["assign_ok"])
        self.assertEqual(summary["removed"], ["us1"])

    def test_status_change_from_web_is_applied(self) -> None:
        """界面上启停策略要跟随（由停到跑还要补跑当日选股）。"""
        state = make_state(status="stopped")
        state.instance.strategy_params = {"min_score": 1.0}
        self.wf._user_strategies = {"s1": [state]}
        self.wf._maybe_refresh_selection = MagicMock(return_value=True)
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "status": "running",
                "strategy_params": {"min_score": 1.0},
            }
        ]

        summary = self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(state.status, "running")
        self.assertEqual(summary["updated"], ["us1"])
        self.wf._maybe_refresh_selection.assert_called_once()

    def test_status_not_taken_over_when_workflow_stopped(self) -> None:
        """工作流未运行时不用服务端状态改写本地状态。"""
        self.wf._running = False
        state = make_state(status="paused")
        state.instance.strategy_params = {"min_score": 1.0}
        self.wf._user_strategies = {"s1": [state]}
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "status": "running",
                "strategy_params": {"min_score": 1.0},
            }
        ]

        self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(state.status, "paused")

    def test_max_stock_count_change_is_applied(self) -> None:
        """持仓上限改动立即生效（0 = 限制买入，不能被执行记录洗掉）。"""
        state = make_state()
        state.instance.strategy_params = {"min_score": 1.0}
        state.max_stock_count = -1
        self.wf._user_strategies = {"s1": [state]}
        self.wf._user_strategy_api.list_all.return_value = [
            {
                "id": "us1",
                "strategy_id": "s1",
                "status": "running",
                "max_stock_count": 0,
                "strategy_params": {"min_score": 1.0},
            }
        ]

        self.wf.reconcile_user_strategies(reason="test")

        self.assertEqual(state.max_stock_count, 0)
        self.assertIn("max_stock_count=0", self.wf._buy_block_reason(state, {}))


class TestReconcileSelectionRefresh(unittest.TestCase):
    """补跑当日选股的三个闸门。"""

    def setUp(self) -> None:
        self.wf = make_workflow()
        self.template = MagicMock()
        self.template.requires_daily_selection = True
        self.state = make_state(instance=self.template)
        self.wf._run_daily_selection_for_state = MagicMock()
        self.wf._collect_hot_industry_codes = MagicMock(return_value=["600519"])
        self.wf._hot_industry_cache = (0.0, [])

    def test_refresh_runs_for_selection_dependent_strategy(self) -> None:
        self.assertTrue(self.wf._maybe_refresh_selection(self.state, "test"))
        args, kwargs = self.wf._run_daily_selection_for_state.call_args
        self.assertFalse(kwargs["reset_cache"])
        self.assertEqual(args[0], self.state)

    def test_refresh_skipped_for_strategy_without_selection_dependency(self) -> None:
        self.template.requires_daily_selection = False
        self.assertFalse(self.wf._maybe_refresh_selection(self.state, "test"))
        self.wf._run_daily_selection_for_state.assert_not_called()

    def test_refresh_skipped_outside_trading_hours(self) -> None:
        self.wf._is_trading_hours = MagicMock(return_value=False)  # type: ignore[method-assign]
        self.assertFalse(self.wf._maybe_refresh_selection(self.state, "test"))
        self.wf._run_daily_selection_for_state.assert_not_called()

    def test_refresh_skipped_when_workflow_not_running(self) -> None:
        """工作流未运行时（正在停止）不补跑，避免在关闭过程中发起全池取数。"""
        self.wf._running = False
        self.state.status = "stopped"
        self.assertFalse(self.wf._maybe_refresh_selection(self.state, "test"))
        self.wf._run_daily_selection_for_state.assert_not_called()

    def test_refresh_throttled_within_interval(self) -> None:
        """同一策略短时间内只补跑一次：用户连点保存不应反复跑全池选股。"""
        self.assertTrue(self.wf._maybe_refresh_selection(self.state, "first"))
        self.assertFalse(self.wf._maybe_refresh_selection(self.state, "second"))
        self.assertEqual(self.wf._run_daily_selection_for_state.call_count, 1)

    def test_refresh_skipped_for_non_running_strategy(self) -> None:
        self.state.status = "stopped"
        self.assertFalse(self.wf._maybe_refresh_selection(self.state, "test"))
        self.wf._run_daily_selection_for_state.assert_not_called()

    def test_trading_hours_detection(self) -> None:
        from datetime import datetime

        # 本用例测的正是该方法自身，先还原真实实现（setUp 里被换成了恒 True）
        self.wf._is_trading_hours = StrategyWorkflow._is_trading_hours.__get__(self.wf)  # type: ignore[method-assign]
        self.wf.xshg.is_session.return_value = True
        self.assertTrue(self.wf._is_trading_hours(datetime(2026, 9, 30, 10, 0)))
        self.assertTrue(self.wf._is_trading_hours(datetime(2026, 9, 30, 12, 0)))
        self.assertTrue(self.wf._is_trading_hours(datetime(2026, 9, 30, 14, 59)))
        self.assertFalse(self.wf._is_trading_hours(datetime(2026, 9, 30, 9, 0)))
        self.assertFalse(self.wf._is_trading_hours(datetime(2026, 9, 30, 15, 30)))
        self.wf.xshg.is_session.return_value = False
        self.assertFalse(self.wf._is_trading_hours(datetime(2026, 9, 30, 10, 0)))

    def test_selection_refresh_does_not_reset_cache(self) -> None:
        """补跑路径必须保留当日缓存（不调 before_trading）。"""
        # setUp 里为了隔离把该方法换成了替身，本用例测的是它自身
        self.wf._run_daily_selection_for_state = (  # type: ignore[method-assign]
            StrategyWorkflow._run_daily_selection_for_state.__get__(self.wf)
        )
        instance = MagicMock()
        instance.strategy_name = "强趋势策略"
        instance.select.return_value = []
        self.wf._strategy_templates = {}
        state = make_state(pool=["000001"], instance=instance)
        self.wf._user_strategies = {"s1": [state]}
        self.wf._pool_api.get_by_id.return_value = {"name": "池子"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "000001"}]

        self.wf._run_daily_selection_for_state(state, [], "2026-09-30", reset_cache=False)

        instance.before_trading.assert_not_called()

        # 盘前链路（默认 reset_cache=True）照旧清理缓存
        self.wf._run_daily_selection_for_state(state, [], "2026-09-30")
        instance.before_trading.assert_called_once()


class TestReconcileTriggers(unittest.TestCase):
    """触发通道：MQTT 通知与定时轮询。"""

    def test_config_change_message_triggers_reconcile(self) -> None:
        wf = make_workflow()
        wf.reconcile_user_strategies = MagicMock(return_value={})  # type: ignore[method-assign]

        wf.on_config_change({"scope": "user_strategy", "action": "update"})

        wf.reconcile_user_strategies.assert_called_once()

    def test_concurrent_reconcile_is_skipped(self) -> None:
        """同时只允许一轮对账：并发会对同一个池重复订阅 MQTT。"""
        wf = make_workflow()
        nested: list[dict] = []

        def _list() -> list:
            # 在拉模板的过程中再触发一次对账（模拟 MQTT 通知与轮询撞在一起）
            nested.append(wf.reconcile_user_strategies(reason="inner"))
            return []

        wf._strategy_api.list.side_effect = _list
        wf._user_strategy_api.list_all.return_value = []

        wf.reconcile_user_strategies(reason="outer")

        self.assertEqual(nested, [{"skipped": "busy"}])

    def test_scheduler_throttles_outside_trading_hours(self) -> None:
        """非交易时段按更长的间隔对账。"""
        import time

        wf = make_workflow()
        wf.reconcile_user_strategies = MagicMock(return_value={})  # type: ignore[method-assign]
        wf._is_trading_hours = MagicMock(return_value=False)  # type: ignore[method-assign]
        wf._last_reconcile_ts = 0.0

        wf._reconcile_job()
        wf.reconcile_user_strategies.assert_called_once_with(reason="scheduler")

        # 刚对账完 → 非交易时段内不再重复
        wf._last_reconcile_ts = time.time()
        wf.reconcile_user_strategies.reset_mock()
        wf._reconcile_job()
        wf.reconcile_user_strategies.assert_not_called()

        # 超过非交易时段间隔 → 恢复对账
        wf._last_reconcile_ts = time.time() - wf.RECONCILE_INTERVAL_OFFHOURS_SECONDS - 1
        wf._reconcile_job()
        wf.reconcile_user_strategies.assert_called_once_with(reason="scheduler")

    def test_scheduler_uses_short_interval_during_trading_hours(self) -> None:
        """交易时段用短间隔：刚对账完过了 60 秒就要再对一次。"""
        import time

        wf = make_workflow()
        wf.reconcile_user_strategies = MagicMock(return_value={})  # type: ignore[method-assign]
        wf._is_trading_hours = MagicMock(return_value=True)  # type: ignore[method-assign]
        wf._last_reconcile_ts = time.time() - wf.RECONCILE_INTERVAL_SECONDS - 1

        wf._reconcile_job()

        wf.reconcile_user_strategies.assert_called_once_with(reason="scheduler")

    def test_reconcile_api_failure_keeps_local_state(self) -> None:
        """拉用户策略失败时不动本地运行表。"""
        wf = make_workflow()
        wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        wf._user_strategy_api.list_all.side_effect = Exception("api down")
        wf._user_strategies = {"s1": [make_state()]}

        summary = wf.reconcile_user_strategies(reason="test")

        self.assertEqual(summary.get("error"), "list_all_failed")
        self.assertEqual(len(wf.get_all_strategies()), 1)

    def test_reconcile_invalidates_star_threshold_cache(self) -> None:
        """推送/下单星级门槛在用户偏好里，不在对账对象里，但要对同一个心跳生效。

        通知器自己带 300 秒 TTL，不额外清理的话界面上改完门槛最多要等 5 分钟；
        对账每 60 秒跑一次，顺手清缓存把它压到 1 分钟。
        """
        wf = make_workflow()
        wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        wf._user_strategy_api.list_all.return_value = []

        wf.reconcile_user_strategies(reason="test")

        wf._notifier.invalidate_preferences_cache.assert_called_once()


class TestBaseStrategyParamReload(unittest.TestCase):
    """参数热更新的基线语义：二次 apply 不能回退成上一次的值。"""

    def test_second_apply_restores_class_default(self) -> None:
        instance = _Fake()

        instance.reload_params({"min_score": 5.0})
        self.assertEqual(instance.MIN_SCORE, 5.0)

        # 用户在界面上把这个参数删了：期望回到类默认值 1.0，
        # 而不是保留上一轮的 5.0（参数只增不减正是这个陷阱造成的）
        instance.reload_params({})
        self.assertEqual(instance.MIN_SCORE, 1.0)

    def test_previous_override_is_not_leaked_across_reloads(self) -> None:
        instance = _Fake()
        instance.reload_params({"min_score": 2.0})
        instance.reload_params({"min_score": 3.0})
        self.assertEqual(instance.MIN_SCORE, 3.0)
        instance.reload_params({})
        self.assertEqual(instance.MIN_SCORE, 1.0)


if __name__ == "__main__":
    unittest.main()

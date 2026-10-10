"""启动期池级 MQTT 订阅测试。

事故背景（2026-10-10 实测）：本地实例「一直收不到订阅消息」。推送端把
`q_share/stock/minute/{pool_id}/{code}` 推得一切正常，本端日志里却只有
`MQTT 订阅 topic 成功: q_share/config_change (QoS: 1, mid=1)` —— **池级 topic 一次
都没订阅**，且没有任何错误日志、`_subscribe_mqtt()` 返回 True。

根因是两个语义共用一个集合：`_init_workflow` 把「打算订阅的池」预填进「已订阅登记表」
（当年就叫 `_pool_ids`，现名 `_subscribed_pool_ids`），
而 `subscribe_pool` 把「在登记表里」当作「已订阅」并直接短路返回，
于是启动时的订阅循环整个空转：登记表里有 4 个池，broker 侧 0 个订阅。

本文件守住三件事：

1. 启动时每个待订阅池都必须真的 subscribe 到 real_time 与 minute 两个 topic；
2. `_subscribed_pool_ids`（已订阅登记表）不得被启动流程预填 —— 成员必须都有对应订阅；
3. `subscribe_pool` 的幂等只针对「真的订阅过」的池，而不是「登记过」的池。
"""

from __future__ import annotations

import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

import workflow.base_workflow as base_workflow
import workflow.strategy_workflow as strategy_workflow
from tests.test_config_reconcile import STRONG_TEMPLATE, make_workflow
from workflow.base_workflow import BaseWorkflow


class _FakeMqttClient:
    """只记录调用、不连 broker 的替身。

    逐次记录 subscribe 调用（不去重），这样「重复订阅」在断言里看得见。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.unsubscribed: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    def connect(self) -> bool:
        return True

    def subscribe(self, topic: str, callback: object, qos: int = 1) -> bool:
        self.calls.append(topic)
        return True

    def unsubscribe(self, topic: str) -> bool:
        self.unsubscribed.append(topic)
        return True


def _minute_topic(pool_id: str) -> str:
    return f"q_share/stock/minute/{pool_id}/#"


def _real_time_topic(pool_id: str) -> str:
    return f"q_share/stock/real_time/{pool_id}/#"


class _PoolWorkflow(BaseWorkflow):
    """最小子类：启动待订阅池由构造参数给定。"""

    def __init__(self, startup_pools: list[str]) -> None:
        super().__init__(pool_size=1)
        self._startup_pools: list[str] = list(startup_pools)

    def _collect_startup_pool_ids(self) -> list[str]:
        return list(self._startup_pools)


class TestStartupPoolSubscription(unittest.TestCase):
    """基类 `_subscribe_mqtt` 的启动订阅行为。"""

    def setUp(self) -> None:
        self.client = _FakeMqttClient()
        self._ctx_patcher = mock.patch.object(
            base_workflow,
            "AppContext",
            lambda: SimpleNamespace(mqtt_client=self.client),
        )
        self._ctx_patcher.start()
        self.addCleanup(self._ctx_patcher.stop)

    def test_startup_subscribes_both_topics_for_every_pool(self) -> None:
        """启动时每个池都要订阅 minute 与 real_time，不能只订阅 config_change。"""
        wf = _PoolWorkflow(["p1", "p2"])

        self.assertTrue(wf._subscribe_mqtt())

        self.assertEqual(
            set(self.client.calls),
            {
                _real_time_topic("p1"),
                _minute_topic("p1"),
                _real_time_topic("p2"),
                _minute_topic("p2"),
                "q_share/config_change",
            },
        )

    def test_registry_only_contains_actually_subscribed_pools(self) -> None:
        """不变量：`_subscribed_pool_ids` 里的每个池都必须有真实的订阅记录。

        这是本次事故的直接守卫 —— 预填登记表会让这条断言失败。
        """
        wf = _PoolWorkflow(["p1", "p2"])
        wf._subscribe_mqtt()

        self.assertEqual(set(wf._subscribed_pool_ids), {"p1", "p2"})
        for pool_id in wf._subscribed_pool_ids:
            self.assertIn(_minute_topic(pool_id), self.client.calls)
            self.assertIn(_real_time_topic(pool_id), self.client.calls)

    def test_config_change_still_subscribed_with_pools(self) -> None:
        """池级订阅不得挤掉配置变更通知（否则界面改配置不再热生效）。"""
        wf = _PoolWorkflow(["p1"])
        wf._subscribe_mqtt()

        self.assertIn("q_share/config_change", self.client.calls)

    def test_no_startup_pool_falls_back_to_global_wildcard(self) -> None:
        """没有池绑定时才走全局通配的兼容模式。"""
        wf = _PoolWorkflow([])

        self.assertTrue(wf._subscribe_mqtt())

        self.assertEqual(
            set(self.client.calls),
            {
                "q_share/stock/minute/#",
                "q_share/stock/real_time/#",
                "q_share/config_change",
            },
        )
        self.assertEqual(wf._subscribed_pool_ids, set())


class TestSubscribePoolIdempotency(unittest.TestCase):
    """`subscribe_pool` / `unsubscribe_pool` 的幂等语义。"""

    def setUp(self) -> None:
        self.client = _FakeMqttClient()
        self._ctx_patcher = mock.patch.object(
            base_workflow,
            "AppContext",
            lambda: SimpleNamespace(mqtt_client=self.client),
        )
        self._ctx_patcher.start()
        self.addCleanup(self._ctx_patcher.stop)

    def test_repeated_subscribe_only_hits_broker_once(self) -> None:
        """重复订阅会让同一根行情收到两次回调，必须挡住。"""
        wf = _PoolWorkflow([])

        self.assertTrue(wf.subscribe_pool("p1"))
        self.assertTrue(wf.subscribe_pool("p1"))

        self.assertEqual(self.client.calls.count(_minute_topic("p1")), 1)
        self.assertEqual(self.client.calls.count(_real_time_topic("p1")), 1)

    def test_unsubscribe_drops_registry_and_both_topics(self) -> None:
        wf = _PoolWorkflow([])
        wf.subscribe_pool("p1")

        wf.unsubscribe_pool("p1")

        self.assertEqual(wf._subscribed_pool_ids, set())
        self.assertEqual(
            set(self.client.unsubscribed),
            {_minute_topic("p1"), _real_time_topic("p1")},
        )


def _user_strategy_item(pool_id: str) -> dict[str, object]:
    return {
        "id": f"us-{pool_id}",
        "strategy_id": "s1",
        "name": "爸爸的强趋势",
        "status": "running",
        "pool_id": pool_id,
        "max_stock_count": -1,
        "initial_amount": 100000.0,
        "strategy_params": {"min_score": 1.0},
    }


class TestStrategyWorkflowStartupPools(unittest.TestCase):
    """StrategyWorkflow 侧：不在初始化里预填登记表，且能给出启动待订阅池。"""

    def setUp(self) -> None:
        self.wf = make_workflow()
        self.wf._strategy_templates = {"s1": dict(STRONG_TEMPLATE)}
        self.wf._strategy_api.list.return_value = [dict(STRONG_TEMPLATE)]
        self.wf._user_strategy_api.list_all.return_value = [
            _user_strategy_item("p1"),
            _user_strategy_item("p2"),
        ]
        self.wf._pool_api.get_by_id.return_value = {"name": "测试池"}
        self.wf._pool_api.get_stocks.return_value = [{"code": "000001"}]

    def _init(self) -> None:
        # service_name 置空 => 兼容模式加载全部用户策略，避免依赖分配表桩
        with mock.patch.object(
            strategy_workflow.tools, "load_service_name", return_value=""
        ):
            self.wf._init_workflow()

    def test_init_workflow_does_not_prefill_subscribed_registry(self) -> None:
        """`_init_workflow` 只能「收集」，不能写 `_subscribed_pool_ids`。"""
        self._init()

        self.assertEqual(self.wf._subscribed_pool_ids, set())
        self.assertEqual(set(self.wf._collect_startup_pool_ids()), {"p1", "p2"})

    def test_startup_subscription_covers_pools_from_user_strategies(self) -> None:
        """真实链路：加载用户策略 → 订阅其绑定的每个池。"""
        self._init()

        # 换掉测试替身，走真实的 subscribe_pool
        self.wf.subscribe_pool = MethodType(BaseWorkflow.subscribe_pool, self.wf)
        client = _FakeMqttClient()
        with mock.patch.object(
            base_workflow,
            "AppContext",
            lambda: SimpleNamespace(mqtt_client=client),
        ):
            self.assertTrue(self.wf._subscribe_mqtt())

        self.assertEqual(set(self.wf._subscribed_pool_ids), {"p1", "p2"})
        for pool_id in ("p1", "p2"):
            self.assertIn(_minute_topic(pool_id), client.calls)
            self.assertIn(_real_time_topic(pool_id), client.calls)


if __name__ == "__main__":
    unittest.main()

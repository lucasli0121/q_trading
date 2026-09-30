"""推送/下单星级门槛测试（2026-09-30）。

背景：服务端在用户偏好里加了 `push_buy_min_star` / `push_sell_min_star`，
本端要按它过滤「星不够」的信号：信号照旧写库，但不发 MQTT 实时流、
不推送企业微信/微信、也不创建订单。

口径（用户确认）：
- 阈值 <= 0 表示不限；否则按 `star >= 阈值`（含边界，与服务端信号列表的
  `min_star` 用的是 `$gte` 一致）；
- 拦截范围是三个出口共用同一个判定：MQTT、文本推送、订单创建。

本文件的重点是几个「错了不会报错、只会静默跑偏」的地方：

- 门槛取不到时按**不限**兜底 —— 门槛同时决定下单，按「拦全部」兜底等于
  一次接口异常就让整个实例停止下单；
- 门槛必须缓存 —— 判定发生在每条信号上（分钟级、逐股），而解析门槛要走
  user_strategy → user_id → preference 两次 HTTP；
- 解析不到 user_id 时**不能**缓存空结果，否则一次网络抖动会让门槛失效一整个 TTL；
- 未达门槛的信号**仍要写库** —— 界面按星级筛历史信号靠的是这份记录。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mq.mqtt_client import MqttTopic
from trade.notifier import TradeNotifier
from workflow.strategy_workflow import StrategyState, StrategyWorkflow


class _FakeUserStrategyApi:
    """只提供 get()：门槛解析链路要拿 user_strategy.user_id。"""

    def __init__(self, user_id: str = "u1") -> None:
        self.user_id: str = user_id
        self.calls: int = 0

    def get(self, user_strategy_id: str) -> dict[str, str]:
        self.calls += 1
        if not self.user_id:
            return {}
        return {"user_id": self.user_id}


def make_notifier(
    buy_min: float = 0.0,
    sell_min: float = 0.0,
    user_id: str = "u1",
) -> TradeNotifier:
    """通知器：用户策略与偏好都打桩，发送渠道替换成可断言的替身。"""
    from utils.tools import resource_path

    notifier: TradeNotifier = TradeNotifier(config_path=resource_path("cfg/stock.cfg"))
    notifier._user_strategy_api = _FakeUserStrategyApi(user_id)  # type: ignore[assignment]
    # _load_user_preferences 是「user_id → 偏好字典」这一步，打桩后不联网；
    # 注意不能在实例上缓存同一份 dict，否则调用方改它会被后续用例看见
    prefs: dict[str, float] = {
        TradeNotifier.PREF_KEY_BUY_MIN_STAR: buy_min,
        TradeNotifier.PREF_KEY_SELL_MIN_STAR: sell_min,
    }
    notifier._load_user_preferences = (  # type: ignore[method-assign]
        lambda user_id: dict(prefs)
    )
    notifier._resolve_stock_name = lambda code, name="": "测试股票"  # type: ignore[method-assign]
    notifier._resolve_wx_push_url = (  # type: ignore[method-assign]
        lambda user_strategy_id: "http://example.invalid/webhook"
    )
    notifier._send_http_text = MagicMock(return_value=True)  # type: ignore[method-assign]
    return notifier


def make_workflow() -> StrategyWorkflow:
    wf: StrategyWorkflow = StrategyWorkflow(pool_size=1)
    wf._trade_signal_api = MagicMock()
    # 空历史 → 不触发信号风暴过滤，用例只测星级这一条
    wf._trade_signal_api.latest.return_value = {}
    wf._strategy_api = MagicMock()
    wf._user_strategy_api = MagicMock()
    wf._save_runlog = MagicMock()
    return wf


def make_state(star: float = 2.0, signal: str = "BUY") -> StrategyState:
    instance: MagicMock = MagicMock()
    instance.strategy_name = "强趋势策略"
    instance.handle_minute_bar.return_value = (
        True,
        {
            "signal": signal,
            "current_price": 10.0,
            "score": star * 2.0,
            "star": star,
            "reason": "条件满足",
        },
    )
    return StrategyState(
        strategy_id="s1",
        user_strategy_id="us1",
        strategy_name="强趋势策略",
        class_path="strategy.strong_rebound_strategy",
        class_name="StrongReboundStrategy",
        user_strategy_name="爸爸的强趋势",
        status="running",
        stock_pool=["000001"],
        initial_amount=100000.0,
        initial_amount_configured=True,
        available_cash=100000.0,
        max_stock_count=-1,
        instance=instance,
    )


class TestMinStarParsing(unittest.TestCase):
    """偏好字段的解析：非法值一律归到 0（不限），不能抛错也不能变成负门槛。"""

    def test_illegal_values_become_unlimited(self) -> None:
        for raw in (None, "", "abc", -1, -0.5, float("nan")):
            with self.subTest(raw=raw):
                self.assertEqual(TradeNotifier.parse_min_star(raw), 0.0)

    def test_numeric_values_are_kept(self) -> None:
        self.assertEqual(TradeNotifier.parse_min_star(0), 0.0)
        self.assertEqual(TradeNotifier.parse_min_star(3), 3.0)
        self.assertEqual(TradeNotifier.parse_min_star("3.5"), 3.5)
        self.assertEqual(TradeNotifier.parse_min_star(5.0), 5.0)


class TestStarPassed(unittest.TestCase):
    """门槛比较：阈值 <= 0 不限，否则 star >= 阈值（含边界）。"""

    def test_zero_threshold_passes_everything(self) -> None:
        for star in (0.0, 0.5, 3.0, 5.0):
            with self.subTest(star=star):
                self.assertTrue(TradeNotifier.star_passed(star, 0.0))

    def test_threshold_is_inclusive(self) -> None:
        """门槛 3 时 3 星要放行（与服务端信号列表 min_star 的口径一致）。"""
        self.assertTrue(TradeNotifier.star_passed(3.0, 3.0))
        self.assertTrue(TradeNotifier.star_passed(3.5, 3.0))
        self.assertFalse(TradeNotifier.star_passed(2.9, 3.0))
        self.assertFalse(TradeNotifier.star_passed(0.0, 3.0))

    def test_illegal_threshold_is_treated_as_unlimited(self) -> None:
        self.assertTrue(TradeNotifier.star_passed(0.0, -1.0))
        self.assertTrue(TradeNotifier.star_passed(0.0, "abc"))
        self.assertTrue(TradeNotifier.star_passed(0.0, None))

    def test_signal_without_star_is_not_passed_when_threshold_set(self) -> None:
        """阈值 > 0 而信号没有星级（上游没打星）时不能当成高星放过去。"""
        for raw in (None, "", "abc", float("nan")):
            with self.subTest(raw=raw):
                self.assertFalse(TradeNotifier.star_passed(raw, 3.0))


class TestResolveMinStars(unittest.TestCase):
    """门槛解析与缓存。"""

    def test_buy_and_sell_use_different_fields(self) -> None:
        notifier: TradeNotifier = make_notifier(buy_min=4.0, sell_min=3.0)

        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 4.0)
        self.assertEqual(notifier.get_push_min_star("us1", "卖出"), 3.0)

    def test_empty_user_strategy_id_is_unlimited(self) -> None:
        notifier: TradeNotifier = make_notifier(buy_min=4.0)
        self.assertEqual(notifier.get_push_min_star("", "买入"), 0.0)

    def test_unknown_action_is_unlimited(self) -> None:
        """方向未知说明上游数据异常，此时拦下单的代价远大于放过。"""
        notifier: TradeNotifier = make_notifier(buy_min=4.0)
        self.assertEqual(notifier.get_push_min_star("us1", "BUY"), 0.0)

    def test_missing_preference_means_unlimited(self) -> None:
        notifier: TradeNotifier = make_notifier()
        notifier._load_user_preferences = lambda user_id: {}  # type: ignore[method-assign]
        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 0.0)

    def test_threshold_is_cached_across_signals(self) -> None:
        """门槛判定在每条信号上执行，不能每条信号都去查两次接口。"""
        notifier: TradeNotifier = make_notifier(buy_min=4.0)
        fake_api = notifier._user_strategy_api

        for _ in range(5):
            self.assertEqual(notifier.get_push_min_star("us1", "买入"), 4.0)

        self.assertEqual(fake_api.calls, 1)

    def test_user_id_lookup_failure_is_not_cached(self) -> None:
        """查不到 user_id（接口异常）时不写缓存，下一次信号要重试。

        缓存空结果等于一次网络抖动就让门槛失效一整个 TTL，而门槛同时决定下单。
        """
        notifier: TradeNotifier = make_notifier(buy_min=4.0, user_id="")
        fake_api = notifier._user_strategy_api

        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 0.0)
        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 0.0)
        self.assertEqual(fake_api.calls, 2)

        # 接口恢复后立刻能拿到门槛（说明没有留下「0 = 不限」的缓存）
        fake_api.user_id = "u1"
        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 4.0)

    def test_invalidate_forces_refetch(self) -> None:
        """对账（60 秒一轮）清缓存后，界面上刚改的门槛要重新拉一次。"""
        notifier: TradeNotifier = make_notifier(buy_min=4.0)
        fake_api = notifier._user_strategy_api
        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 4.0)

        notifier.invalidate_preferences_cache()
        notifier._load_user_preferences = (  # type: ignore[method-assign]
            lambda user_id: {TradeNotifier.PREF_KEY_BUY_MIN_STAR: 2.0}
        )

        self.assertEqual(notifier.get_push_min_star("us1", "买入"), 2.0)
        self.assertEqual(fake_api.calls, 2)


class TestNotifySignalThreshold(unittest.TestCase):
    """notify_signal 自己也要拦：直接调它的入口不能绕过门槛。"""

    def test_below_threshold_is_not_sent(self) -> None:
        notifier: TradeNotifier = make_notifier(buy_min=4.0)

        sent: bool = notifier.notify_signal(
            action="买入", code="000001.SZ", price=10.0, star=2.0,
            user_strategy_id="us1", recipient="爸爸的强趋势",
        )

        self.assertFalse(sent)
        notifier._send_http_text.assert_not_called()

    def test_at_threshold_is_sent(self) -> None:
        notifier: TradeNotifier = make_notifier(buy_min=4.0)

        sent: bool = notifier.notify_signal(
            action="买入", code="000001.SZ", price=10.0, star=4.0,
            user_strategy_id="us1", recipient="爸爸的强趋势",
        )

        self.assertTrue(sent)
        self.assertEqual(notifier._send_http_text.call_count, 1)

    def test_zero_threshold_keeps_old_behaviour(self) -> None:
        """未配置门槛时与改动前一致：0 星信号也照推（只是不显示星级段）。"""
        notifier: TradeNotifier = make_notifier(buy_min=0.0)

        sent: bool = notifier.notify_signal(
            action="买入", code="000001.SZ", price=10.0, star=0.0,
            user_strategy_id="us1",
        )

        self.assertTrue(sent)

    def test_passed_in_threshold_skips_resolution(self) -> None:
        """调用方传了已解析的门槛时，不再重复解析（省两次 HTTP）。"""
        notifier: TradeNotifier = make_notifier(buy_min=4.0)
        notifier.get_push_min_star = MagicMock(return_value=4.0)  # type: ignore[method-assign]

        notifier.notify_signal(
            action="买入", code="000001.SZ", price=10.0, star=4.0,
            user_strategy_id="us1", min_star=4.0,
        )

        notifier.get_push_min_star.assert_not_called()


class TestSaveTradeSignalThreshold(unittest.TestCase):
    """信号链路的三个出口：未达门槛时只写库。"""

    def _save(self, wf: StrategyWorkflow, star: float) -> bool:
        return wf._save_trade_signal(
            "s1", "us1", "000001.SZ",
            {
                "signal": "BUY",
                "current_price": 10.0,
                "score": star * 2.0,
                "star": star,
                "reason": "条件满足",
            },
            strategy_name="强趋势策略",
            user_strategy_name="爸爸的强趋势",
        )

    def test_low_star_signal_is_saved_but_not_pushed(self) -> None:
        wf: StrategyWorkflow = make_workflow()
        wf._notifier = make_notifier(buy_min=4.0)

        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            ok: bool = self._save(wf, star=2.0)
            mqtt_publish = mock_ctx.return_value.mqtt_client.publish

        self.assertFalse(ok)
        # 写库照旧：界面按星级筛历史信号依赖它
        wf._trade_signal_api.add.assert_called_once()
        self.assertEqual(wf._trade_signal_api.add.call_args.kwargs["star"], 2.0)
        mqtt_publish.assert_not_called()
        wf._notifier._send_http_text.assert_not_called()

    def test_passed_star_signal_is_pushed_and_allows_order(self) -> None:
        wf: StrategyWorkflow = make_workflow()
        wf._notifier = make_notifier(buy_min=4.0)

        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            ok: bool = self._save(wf, star=4.0)
            mqtt_publish = mock_ctx.return_value.mqtt_client.publish

        self.assertTrue(ok)
        self.assertEqual(mqtt_publish.call_count, 1)
        topic, payload = mqtt_publish.call_args[0]
        self.assertEqual(topic, MqttTopic.STOCK_TRADING_SIGNAL)
        self.assertIn('"star": 4.0', payload)
        self.assertEqual(wf._notifier._send_http_text.call_count, 1)

    def test_unconfigured_threshold_keeps_old_behaviour(self) -> None:
        """门槛为 0（未配置）时不影响原有链路。"""
        wf: StrategyWorkflow = make_workflow()
        wf._notifier = make_notifier(buy_min=0.0)

        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            ok: bool = self._save(wf, star=0.0)
            mqtt_publish = mock_ctx.return_value.mqtt_client.publish

        self.assertTrue(ok)
        self.assertEqual(mqtt_publish.call_count, 1)

    def test_sell_uses_sell_threshold(self) -> None:
        wf: StrategyWorkflow = make_workflow()
        wf._notifier = make_notifier(buy_min=0.0, sell_min=4.0)

        with patch("workflow.strategy_workflow.AppContext") as mock_ctx:
            ok: bool = self._save(wf, star=3.0)
            mqtt_publish = mock_ctx.return_value.mqtt_client.publish

        # 买入门槛是 0（不限），所以同一个 3 星买入信号照常放行
        self.assertTrue(ok)
        self.assertEqual(mqtt_publish.call_count, 1)


class TestProcessBarOrderGate(unittest.TestCase):
    """端到端：未达门槛的信号不能产生订单。"""

    def setUp(self) -> None:
        self.wf: StrategyWorkflow = make_workflow()
        self.wf._notifier = make_notifier(buy_min=4.0)
        self.wf._refresh_account_drawdown = MagicMock()  # type: ignore[method-assign]
        self.wf._is_signal_in_user_scope = MagicMock(return_value=True)  # type: ignore[method-assign]
        self.wf._load_positions = MagicMock(return_value={})  # type: ignore[method-assign]
        self.wf._reserve_buy_order = MagicMock(return_value=None)  # type: ignore[method-assign]

    def test_low_star_signal_creates_no_order(self) -> None:
        state: StrategyState = make_state(star=2.0)

        with patch("workflow.strategy_workflow.AppContext"):
            self.wf._process_bar(state, "000001", {"create_time": "2026-09-30 10:00:00"})

        self.wf._reserve_buy_order.assert_not_called()
        # 连持仓都不该去拉：门槛在拉持仓之前就返回了
        self.wf._load_positions.assert_not_called()

    def test_high_star_signal_reaches_order_path(self) -> None:
        state: StrategyState = make_state(star=4.5)

        with patch("workflow.strategy_workflow.AppContext"):
            self.wf._process_bar(state, "000001", {"create_time": "2026-09-30 10:00:00"})

        self.wf._load_positions.assert_called_once()
        self.wf._reserve_buy_order.assert_called_once()


if __name__ == "__main__":
    unittest.main()

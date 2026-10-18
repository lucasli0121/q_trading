"""
Author: liguoqiang
Date: 2026-08-02
Description: 低频波段策略测试 —— 使用真实 API 行情数据（日K / 分钟字段映射）。
    服务端分钟接口当前返回空，故分钟相关用例使用真实日K数据映射为分钟字段。
"""

from __future__ import annotations

from configparser import ConfigParser
from datetime import datetime, timedelta
import unittest
from typing import Any
from unittest.mock import patch

import pandas as pd

from api.client import ApiClient
from api.market import MarketApi
from app_context import AppContext
from factor.boll_support_resistance import BollSRFactor
from factor.in_day_support_resistance import InDaySRFactor
from strategy.swing_trading_strategy import SwingTradingStrategy
from utils.tools import load_admin_token


def setUpModule() -> None:
    AppContext().api_client.set_fallback_token(load_admin_token())


def fetch_daily(code: str = "000001") -> list[dict[str, Any]]:
    return AppContext().market_api.get_day_kline(
        code=code, start="2026-03-01", end="2026-07-31"
    )


class TestSwingTradingStrategy(unittest.TestCase):

    def setUp(self) -> None:
        self._admin_client: ApiClient = ApiClient()
        admin_token: str = self._load_admin_token()
        if admin_token:
            self._admin_client.set_token(admin_token)
            # 同时设置全局 ApiClient 的 fallback token，确保 AppContext().pool_api 等
            # 在后台线程中也能携带认证信息
            AppContext().api_client.set_fallback_token(admin_token)
        self.market_api: MarketApi = MarketApi(self._admin_client)

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
            
    def test_buy_condition(self) -> None:
        code = "603993"
        start: str = "2026-07-28 09:30:00"
        end: str = "2026-07-28 15:00:00"
        strategy = SwingTradingStrategy()
        
        for i in range(0, 50):
            strategy.before_trading(start, [code])
            minute_hq_list = self.market_api.get_minute_kline(code=code, start=start, end=end)
            if len(minute_hq_list) > 0:
                for stock_item in minute_hq_list:
                    strategy._save_minute_cache(code=code, stock_data=stock_item)
                    # result, reason = strategy._check_buy_conditions(
                    #     code=code, stock_data = stock_item
                    # )
                    # if result:
                    #     print(f"Buy condition check result: {result}, info: {reason}")
                    result, reason = strategy._check_sell_conditions(
                        code=code, position = {}, stock_data = stock_item
                    )
                    if result:
                        print(f"Sell condition check result: {result}, info: {reason}")
            next_day = (datetime.strptime(start, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)).strftime("%Y-%m-%d")
            start = f"{next_day} 09:30:00"
            end = f"{next_day} 15:00:00"

class TestSwingLimitAware(unittest.TestCase):
    """R13：波段策略的涨跌停阈值按板块判定（两个方向都挡），且 preclose=0 不再除零。

    这些用例只走「时间/价格/涨跌停」的前置分支，并把 `_get_sr_points` 打桩成空表，
    因此不触网；空表本身同时验证 R3 的空表保护（不再抛 IndexError）。
    """

    USID: str = "test-swing-limit-aware"

    def _strategy(self) -> SwingTradingStrategy:
        return SwingTradingStrategy(
            user_strategy_id=self.USID,
            user_strategy_params={"match": {}, "buy": {}, "sell": {}},
        )

    @staticmethod
    def _bar(**overrides: Any) -> dict[str, Any]:
        bar: dict[str, Any] = {
            "code": "000001",
            "name": "",
            "price": 10.0,
            "open": 10.0,
            "preclose": 10.0,
            "high": 10.0,
            "low": 10.0,
            "create_time": "2026-07-15 10:30:00",
        }
        bar.update(overrides)
        return bar

    def test_buy_blocked_near_limit_main_board(self) -> None:
        """主板 −9.1%（10% 板留 1 个百分点余量）→ 接近跌停，不买入。"""
        strategy = self._strategy()
        matched, extra = strategy._check_buy_conditions("000001", self._bar(price=9.09))
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_buy_blocked_near_limit_up_main_board(self) -> None:
        """R13：涨停方向同样拦（类文档「涨停不买入」）→ 主板 +9.1% 不买入。"""
        strategy = self._strategy()
        matched, extra = strategy._check_buy_conditions("000001", self._bar(price=10.91))
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_buy_not_blocked_on_chinext_9pct_drop(self) -> None:
        """创业板 −9%（20% 板）远未接近跌停，不应被涨跌停分支挡掉。"""
        strategy = self._strategy()
        with patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy._check_buy_conditions(
                "300750", self._bar(code="300750", name="宁德时代", price=9.1)
            )
        self.assertFalse(matched)
        self.assertNotIn("接近涨停或跌停", extra.get("reason", ""))
        # 空表保护生效：分值为 0 而不是抛 IndexError
        self.assertIn("未满足", extra["reason"])

    def test_sell_blocked_near_limit_st(self) -> None:
        """ST（5% 板）+4.1% 已接近涨停，不挂卖单。"""
        strategy = self._strategy()
        matched, extra = strategy._check_sell_conditions(
            "002456", {}, self._bar(code="002456", name="*ST欧菲", price=10.41)
        )
        self.assertFalse(matched)
        self.assertIn("接近涨停或跌停", extra["reason"])

    def test_preclose_zero_does_not_raise(self) -> None:
        """preclose=0 时不得除零（原实现会抛 ZeroDivisionError）。"""
        strategy = self._strategy()
        with patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy._check_buy_conditions(
                "000001", self._bar(preclose=0)
            )
        self.assertFalse(matched)
        self.assertNotIn("检查异常", extra.get("reason", ""))


def _bar(**overrides: Any) -> dict[str, Any]:
    """构造一根分钟行情：默认涨跌幅 0、时间在 9:40 之后，不触发前置闸门。"""
    bar: dict[str, Any] = {
        "code": "000001",
        "name": "",
        "price": 10.0,
        "open": 10.0,
        "preclose": 10.0,
        "high": 10.0,
        "low": 10.0,
        "create_time": "2026-07-15 10:30:00",
    }
    bar.update(overrides)
    return bar


def _swing_strategy(tag: str) -> SwingTradingStrategy:
    """构造只走本地逻辑的波段策略实例（参数全空 ⇒ 取类属性默认值）。"""
    return SwingTradingStrategy(
        user_strategy_id=tag,
        user_strategy_params={"match": {}, "buy": {}, "sell": {}, "risk": {}},
    )


class TestSwingTrendFilter(unittest.TestCase):
    """日线趋势**门槛**：向下抬买入门槛、向上抬卖出门槛（2026-10-13 由硬闸门改成）。

    2026-09-29 原实现是硬闸门（趋势向下 ⇒ 当天直接跳过买入判定）；
    2026-10-13 按用户要求改成门槛 —— 只收紧、不封死。
    本组测试守住三件事：

    1. 趋势向下**只抬门槛**（弱信号被挡、极强信号仍可成交），而不是整条买入链路被关掉；
    2. 趋势向上抬的是**卖出**门槛，买入不受影响；
    3. 卖出与风控判定不会被趋势门槛顺手关掉 —— 下跌趋势中的持仓最需要离场。
    """

    USID: str = "test-swing-trend-filter"

    @staticmethod
    def _scores(value: float):
        """把两个位置因子打桩成固定分：位置分 = (inday + boll) / 2。"""
        return (
            patch.object(InDaySRFactor, "score_with_sr", return_value=value),
            patch.object(BollSRFactor, "score", return_value=value),
        )

    def _run(
        self, strategy: SwingTradingStrategy, price: float = 10.0, score: float = 10.0
    ):
        p1, p2 = self._scores(score)
        with p1, p2, patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            return strategy.handle_minute_bar("000001", {}, _bar(price=price))

    def test_downtrend_raises_buy_bar_instead_of_blocking(self) -> None:
        """趋势向下 ⇒ 买入门槛 +1.0：卡在基准门槛上方的弱信号被挡下。

        位置分 0.5（avg_score = 0.5）在无趋势时 `0.5 > 0` 成立 ⇒ 买入；
        趋势向下时 `0.5 > 1.0` 不成立 ⇒ 不买。这就是「门槛」与「硬闸门」的区别。
        """
        strategy = _swing_strategy(self.USID)
        matched, extra = self._run(strategy, score=0.5)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")

        strategy.set_daily_trend(down_codes=["000001"], up_codes=[], trade_date="2026-10-13")
        matched, extra = self._run(strategy, score=0.5)
        self.assertFalse(matched)
        # 买入不成立时 handle_minute_bar 会继续走卖出判定，返回的不是买入分支的明细，
        # 所以这里只能断言「没有产生 BUY」。
        self.assertNotEqual(extra.get("signal"), "BUY")

    def test_strong_signal_still_buys_when_downtrend(self) -> None:
        """极强信号（位置分 +10）在趋势向下时**仍可买入** —— 门槛不是封死。

        原硬闸门实现下这条必为 False；若改完仍是 False，说明门槛接错了位置
        （例如把 `downtrend` 短路又留了一处），等于没改成门槛。
        """
        strategy = _swing_strategy(self.USID)
        strategy.set_daily_trend(down_codes=["000001"], up_codes=[], trade_date="2026-10-13")
        matched, extra = self._run(strategy)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")
        # 成功分支会带上偏移量，便于从信号明细反查门槛被抬了多少
        self.assertIn("买入门槛偏移", str(extra.get("reason", "")))

    def test_uptrend_still_buys(self) -> None:
        """对照组：同一份行情、同一套打分，无趋势标记时照常买入。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._run(strategy)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "BUY")

    def test_uptrend_raises_sell_bar(self) -> None:
        """趋势向上 ⇒ 卖出门槛 +1.0：卡在门槛上的弱卖出信号被挡下。

        位置分 −1.0（final_score = −1.0）无趋势时 `-1.0 < 0` 成立 ⇒ SELL；
        趋势向上时 `-1.0 < -1.0` 不成立 ⇒ 不卖（避免趋势里卖飞）。
        """
        strategy = _swing_strategy(self.USID)
        matched, extra = self._run(strategy, score=-1.0)
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")

        strategy.set_daily_trend(down_codes=[], up_codes=["000001"], trade_date="2026-10-13")
        self.assertGreater(strategy.sell_threshold_offset("000001"), 0.0)
        matched, _extra = self._run(strategy, score=-1.0)
        self.assertFalse(matched)

    def test_sell_still_runs_when_downtrend(self) -> None:
        """趋势向下时卖出判定仍在执行：位置分 −10 应照常产出 SELL。"""
        strategy = _swing_strategy(self.USID)
        strategy.set_daily_trend(down_codes=["000001"], up_codes=[], trade_date="2026-10-13")
        p1, p2 = self._scores(-10.0)
        with p1, p2, patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(strategy, "_get_minute_cache", return_value=[]):
            matched, extra = strategy.handle_minute_bar("000001", {}, _bar())
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")

    def test_is_downtrend_decision_table(self) -> None:
        """判定必须同时要求 ADX 达标与 −DI > +DI；数据缺失时 fail-open。"""
        strategy = _swing_strategy(self.USID)
        nan: float = float("nan")
        cases: list[tuple[dict[str, Any], bool, str]] = [
            ({"adx": 30.0, "+DI": 20.0, "-DI": 25.0}, True, "ADX 达标且 −DI > +DI"),
            ({"adx": 24.9, "+DI": 20.0, "-DI": 25.0}, False, "ADX 未达门槛 ⇒ 视为震荡"),
            ({"adx": 30.0, "+DI": 25.0, "-DI": 20.0}, False, "方向向上"),
            ({"adx": 30.0, "+DI": 25.0, "-DI": 25.0}, False, "DI 相等 ⇒ 方向不明"),
            ({}, False, "无数据 ⇒ 不过滤（fail-open）"),
            ({"adx": nan, "+DI": 20.0, "-DI": 25.0}, False, "ADX 为 NaN ⇒ 判不出趋势"),
            ({"adx": 30.0, "+DI": nan, "-DI": nan}, False, "DI 为 NaN ⇒ 判不出趋势"),
        ]
        for detail, expected, msg in cases:
            with self.subTest(detail=detail):
                self.assertEqual(strategy._is_downtrend(detail), expected, msg)

    def test_trend_kline_days_covers_adx_lookback(self) -> None:
        """取数根数必须覆盖 ADX 的 2×period lookback。

        少一根时 talib 的末值是 NaN，`NaN > 阈值` 恒为 False ⇒ 判不出下跌趋势
        ⇒ 趋势门槛静默失效（不报错、不告警，只是永远不偏移）。
        """
        strategy = _swing_strategy(self.USID)
        self.assertGreaterEqual(
            strategy._trend_kline_days(), 2 * strategy.TREND_DAYS_LIMIT + 1
        )

    def test_match_strategy_no_longer_drops_downtrend(self) -> None:
        """选股条件 0 改成「只记录、不剔除」（2026-10-13）。

        原实现把趋势向下的股票在选股阶段就剔掉，但门槛要在**买入判定**里才生效 ——
        选股剔掉了，门槛就永远拿不到这些股票，等于把「改成门槛」退化成原来的硬闸门。
        现在流程必须继续走到市盈率检查，并把方向记进集合。
        """
        strategy = _swing_strategy(self.USID)
        stock_data: list[dict[str, Any]] = [
            {"code": "000001", "high": 10.0, "low": 9.0, "close": 9.5, "open": 9.6}
            for _ in range(40)
        ]
        blocked_reason: dict[str, Any] = {"reason": "条件1不满足: 市盈率过高"}
        with patch.object(
            strategy,
            "_adx_detail",
            return_value={"adx": 31.0, "+DI": 18.0, "-DI": 27.0},
        ), patch.object(
            strategy, "_check_industry_pe", return_value=(False, blocked_reason)
        ) as pe:
            matched, extra = strategy.is_match_strategy(stock_data)
        # 没有因为趋势向下提前返回 ⇒ 说明走到市盈率检查了
        pe.assert_called_once()
        self.assertFalse(matched)
        self.assertEqual(strategy.daily_trend_direction("000001"), -1)
        self.assertEqual(strategy.daily_trend_direction("000001"), -1)

    def test_match_strategy_records_uptrend(self) -> None:
        """选股链路与盘中链路共用同一份方向集合，回测与实盘才不会分叉。"""
        strategy = _swing_strategy(self.USID)
        stock_data: list[dict[str, Any]] = [
            {"code": "000002", "high": 10.0, "low": 9.0, "close": 9.5, "open": 9.6}
            for _ in range(40)
        ]
        with patch.object(
            strategy,
            "_adx_detail",
            return_value={"adx": 31.0, "+DI": 27.0, "-DI": 18.0},
        ), patch.object(
            strategy, "_check_industry_pe", return_value=(False, {"reason": "PE 不过"})
        ):
            strategy.is_match_strategy(stock_data)
        self.assertEqual(strategy.daily_trend_direction("000002"), 1)
        self.assertGreater(strategy.sell_threshold_offset("000002"), 0.0)


    def test_build_sr_points_returns_empty_on_short_data(self) -> None:
        """日线为空时返回空表而不是抛异常（空表会中断整个股票池的盘前准备）。"""
        strategy = _swing_strategy(self.USID)
        self.assertTrue(strategy._build_sr_points("000001", "2026-07-15", pd.DataFrame()).empty)

    def test_build_sr_points_uses_last_row_as_baseline(self) -> None:
        """枢轴点必须取「最后一根（= 前一交易日）」而不是整段的首行。"""
        strategy = _swing_strategy(self.USID)
        klines = pd.DataFrame(
            [
                {"high": 20.0, "low": 10.0, "close": 15.0},  # 更早的一根
                {"high": 11.0, "low": 8.0, "close": 11.0},  # 最后一根 = 基准日
            ]
        )
        sr_df = strategy._build_sr_points("000001", "2026-07-15", klines)
        self.assertEqual(len(sr_df), 1)
        self.assertAlmostEqual(float(sr_df.iloc[0]["pivot"]), 10.0, places=6)


class TestSwingSellStop(unittest.TestCase):
    """卖出侧：账户回撤分项 + 买入信号价硬止损。

    个股「自持仓最高点回撤」自 2026-10-14 起被移除 —— 它在恒传空持仓的实时链路
    （设计约定 D-1）里会把基准退化成「最近 2 个交易日最高价」，买缩量回调股时
    变成买入即接近卖出条件；改由 `BaseStrategy.SELL_BUY_PRICE_BREAK_*`
    （连续 3 个分钟 bar 低于买入信号价）承担。本组守住三件事：
    ① 位置分 0 且无止损时不卖；② 账户回撤分项行为不变；③ 止损触发时走硬卖出路径。
    """

    USID: str = "test-swing-sell-stop"

    def _sell(
        self,
        strategy: SwingTradingStrategy,
        account_dd: float = 0.0,
        price: float = 10.0,
    ):
        """跑一次卖出判定：两个位置因子打桩成 0，价格与账户回撤可控。"""
        if account_dd:
            strategy.set_account_drawdown(account_dd)
        with patch.object(InDaySRFactor, "score_with_sr", return_value=0.0), patch.object(
            BollSRFactor, "score", return_value=0.0
        ), patch.object(
            strategy, "_get_sr_points", return_value=pd.DataFrame()
        ), patch.object(
            strategy, "_get_minute_cache", return_value=[]
        ):
            return strategy._check_sell_conditions("000001", {}, _bar(price=price))

    # ---- 账户回撤分项（行为与改动前一致）----

    def test_account_drawdown_triggers_sell(self) -> None:
        """账户回撤 -12.5% 达默认阈值 -10% ⇒ 单独一条即可触发卖出。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy, account_dd=-12.5)
        self.assertTrue(matched)
        self.assertAlmostEqual(extra["score"], -6.0, places=2)
        self.assertIn("账户回撤", extra["reason"])

    def test_account_drawdown_below_threshold_does_not_trigger(self) -> None:
        strategy = _swing_strategy(self.USID)
        matched, _extra = self._sell(strategy, account_dd=-9.9)
        self.assertFalse(matched)

    def test_position_score_zero_does_not_sell(self) -> None:
        """位置分 0 且无账户回撤 ⇒ 不卖（去掉个股回撤后卖出边界不变）。"""
        strategy = _swing_strategy(self.USID)
        matched, extra = self._sell(strategy)
        self.assertFalse(matched)
        self.assertNotIn("买入信号价止损", extra.get("reason", ""))

    # ---- 买入信号价硬止损 ----

    def test_buy_signal_break_triggers_sell(self) -> None:
        """连续 3 个 bar 低于买入信号价 ⇒ 直接卖出，分值/星级固定。"""
        strategy = _swing_strategy(self.USID)
        strategy.set_buy_signal_price("000001", 10.5)
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 第 1 次
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 第 2 次
        matched, extra = self._sell(strategy, price=10.4)      # 第 3 次
        self.assertTrue(matched)
        self.assertEqual(extra["signal"], "SELL")
        self.assertAlmostEqual(extra["score"], -6.0, places=2)
        self.assertAlmostEqual(extra["star"], 3.0, places=1)
        self.assertIn("买入信号价止损", extra["reason"])
        self.assertIn("连续3次", extra["reason"])
        # 硬止损是独立路径，位置分文案仍原样保留
        self.assertIn("枢轴点分值:0.00", extra["reason"])

    def test_recover_to_buy_signal_resets_count(self) -> None:
        """中途回到买入信号价（含相等）⇒ 计数清零，之前累计的次数作废。"""
        strategy = _swing_strategy(self.USID)
        strategy.set_buy_signal_price("000001", 10.5)
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 1
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 2
        self.assertFalse(self._sell(strategy, price=10.5)[0])  # 回到基准 ⇒ 清零
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 重新从 1 开始
        self.assertFalse(self._sell(strategy, price=10.4)[0])  # 2
        self.assertTrue(self._sell(strategy, price=10.4)[0])   # 3 ⇒ 触发

    def test_no_baseline_does_not_stop(self) -> None:
        """没有买入信号价基准（未建仓）⇒ 不判定，不误卖。

        价格取 −5%（未触及涨跌停闸门），确保走到止损判定本身而不是被前置闸门拦掉。
        """
        strategy = _swing_strategy(self.USID)
        for _ in range(5):
            self.assertFalse(self._sell(strategy, price=9.5)[0])


if __name__ == "__main__":
    unittest.main()

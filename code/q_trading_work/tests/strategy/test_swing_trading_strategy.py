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


if __name__ == "__main__":
    unittest.main()

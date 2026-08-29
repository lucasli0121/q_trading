"""强势反弹策略。

买入条件（根据下面条件打分优先判断）：
    1. 最近5天趋势向上
    2. 最近5日涨幅 > 8%
    3. 最近3日最大跌幅不会跌穿第二支撑位
卖出条件（根据打分判断）：
    1. 最近两日跌幅超过6%
    2. 最近两日最低价和最高价都低于前一天
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import numpy as np
import pandas as pd
import yaml

from app_context import AppContext
from factor.low_rebound_factor import LowReboundFactor
from factor.high_decline_factor import HighDeclineFactor
from factor.kline_support_resistance import KlineSRFactor
from factor.multi_trend_factor import MultiTrendFactor
from factor.volume_expansion_factor import VolumeExpansionFactor
from factor.vwap_factor import VwapFactor
from strategy.base_strategy import BaseStrategy


class StrongReboundStrategy(BaseStrategy):
    """强势反弹策略。

    买入条件：
        历史行情条件（is_match_strategy 中判断）
            1. 最近5天趋势向上
            2. 最近3日收盘价都高于10日最低收盘，且反弹幅度超过 8%。
            3. 最近5日最大跌幅不会跌穿第二支撑位
        当天条件（_check_buy_conditions 中判断，根据打分）
            加分项: 1)当天开盘价 >= 昨日收盘价; 2)交易量连续上升; 3)最大跌幅不会跌穿当天第二支撑位
            减分项: 1)当天开盘价 < 昨日收盘价; 2)最大跌幅跌穿当天第二支撑位; 

    卖出条件：
        1. 最近两日跌幅（从最高点计算) 超过8%
        2. 最近两日最低价和最高价都低于前一天
        3. 积分项：1）当天股价超出阻力位；2）当天股价低开；3）股价低于平均价次数百分率
    """

    strategy_name: str = "强势反弹策略"
    description: str = (
        "强势反弹策略：股价最近连续反弹，最近涨幅增大而跌幅有限"
    )

    #适配
    PE_LIMIT: int = 300
    PROFIT_LIMIT: float = 10.0
    TREND_DAYS_LIMIT: int = 7
    CONSECUTIVE_DAYS: int = 5  # 最近连续交易日数（买入条件1）
    UP_TREND_SCORE: float = 6.0 # 向上趋势分数
    REBOUND_DAY_SCORE: float = 10.0  # 最近反弹阈值（%）

    # 买入参数
    # 连续交易日
    BUY_CONSECUTIVE_MINUTES: int = 5  # 连续分钟数（买入点1）
    BUY_TREND_MINUTES_LIMIT: int = 7 # 分钟交易趋势限制
    BUY_MIN_SCORE: float = 20.0 # 买入的最小分数

    # 卖出参数
    SELL_TREND_MINUTES_LIMIT: int = 7 # 卖出交易分钟趋势限制
    SELL_DRAWDOWN_PCT: float = -8.0  # 回撤阈值（%）
    SELL_VWAP_LIMIT: int = 7 # vwap 均价的分钟数
    SELL_DAILY_DROP_PCT: float = -5.0  # 单日跌幅阈值（%）
    SELL_MAX_SCORE: float = -15.0 # 卖出的最大分数

    def __init__(self) -> None:
        """初始化强势反弹策略。"""
        # 必须调用基类初始化：设置 logger/factor_manager，并创建选股列表缓存字段
        super().__init__()
        self.logger = logging.getLogger(__name__)
        # 从配置文件加载参数
        self._load_config()

    def _load_config(self) -> None:
        """从配置文件加载策略参数。"""
        import os
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "cfg", "strategy.yaml"
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            strategy_config = config.get("strong_rebound_strategy", {})
            match_config = strategy_config.get("match", {})
            buy_config = strategy_config.get("buy", {})
            sell_config = strategy_config.get("sell", {})

            # 适配参数
            self.PE_LIMIT = int(match_config.get("pe_limit", self.PE_LIMIT))
            self.PROFIT_LIMIT = int(match_config.get("profit_limit", self.PROFIT_LIMIT))
            self.TREND_DAYS_LIMIT = int(match_config.get("trend_days_limit", self.TREND_DAYS_LIMIT))
            self.CONSECUTIVE_DAYS = int(match_config.get("consecutive_days", self.CONSECUTIVE_DAYS))
            self.REBOUND_DAY_SCORE = float(match_config.get("rebound_score", self.REBOUND_DAY_SCORE))
            self.UP_TREND_SCORE = float(match_config.get("up_trend_score", self.UP_TREND_SCORE))
            
            # 买入参数
            self.BUY_CONSECUTIVE_MINUTES = int(buy_config.get("consecutive_minutes", self.BUY_CONSECUTIVE_MINUTES))
            self.BUY_MIN_SCORE = float(buy_config.get("min_score", self.BUY_MIN_SCORE))
            self.BUY_TREND_MINUTES_LIMIT = int(buy_config.get("trend_minutes_limit", self.BUY_TREND_MINUTES_LIMIT))
            
            # 卖出参数
            self.SELL_DRAWDOWN_PCT = float(sell_config.get("drawdown_pct", self.SELL_DRAWDOWN_PCT))
            self.SELL_DAILY_DROP_PCT = float(sell_config.get("daily_drop_pct", self.SELL_DAILY_DROP_PCT))
            self.SELL_VWAP_LIMIT = int(sell_config.get("vwap_minutes_limit", self.SELL_VWAP_LIMIT))
            self.SELL_MAX_SCORE = float(sell_config.get("max_score", self.SELL_MAX_SCORE))
            self.SELL_TREND_MINUTES_LIMIT = int(sell_config.get("trend_minutes_limit", self.SELL_TREND_MINUTES_LIMIT))
            
            self.logger.info("强势反弹策略配置加载成功: %s", strategy_config)
        except FileNotFoundError:
            self.logger.warning("配置文件 %s 未找到，使用默认参数", config_path)
        except Exception as e:
            self.logger.error("加载配置文件失败: %s", e)

    # ---- 基类抽象方法实现 ----

    def init_factors(self) -> None:
        """注册策略所需因子。

        本策略通过 API 获取行情数据，因子用于 is_match_strategy 的条件计算。
        """
        # 反弹因子
        self.factor_manager.register(LowReboundFactor.factor_name, LowReboundFactor(days=self.CONSECUTIVE_DAYS))
        # 回撤因子
        self.factor_manager.register(HighDeclineFactor.factor_name, HighDeclineFactor(days=self.CONSECUTIVE_DAYS))
        self.factor_manager.register(
            VolumeExpansionFactor.factor_name, VolumeExpansionFactor(days=self.BUY_CONSECUTIVE_MINUTES, min_price_pct=1.0)  # 连续5分钟成交量放大因子
        )
        # 平均价
        self.factor_manager.register(VwapFactor.factor_name, VwapFactor())
        # 波段支撑阻力位因子
        self.factor_manager.register(KlineSRFactor.factor_name, KlineSRFactor(days=self.CONSECUTIVE_DAYS))

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        """判断历史日线数据是否满足策略买入信号（用于回测/选股）。
        stock_data 是历史行情，不含当天行情
        本函数用于选股

        条件：
        1. 市盈率和利润增长率不能太低(市盈率<300, 利润增长率>10%)
        2. 最近趋势向上
        3. 最近反弹幅度超过 8%。

        :param stock_data: 股票日线行情数据列表，每项含 close/open/code 等字段
        :return: (是否匹配, 详细信息字典)
        """
        if not stock_data or len(stock_data) < self.CONSECUTIVE_DAYS:
            return False, {
                "reason":
                    f"{self.strategy_name}, 数据不足，需至少10个交易日"
                }

        score: float = 0.0
        stock_df: pd.DataFrame = pd.DataFrame(stock_data)
        
        # ---- 条件1：市盈率<300 且 利润增长率>10% ----
        code: str = str(stock_data[0].get("code", "")) if stock_data else ""
        if not code:
            return False, {
                "reason": (
                    f"{self.strategy_name}, "
                    "条件1不满足: 无法获取股票代码"
                ),
            }
        extra: dict[str, Any] = {
            "code": code,
            "signal": "BUY"
        }
        result, match_reason = self._match_pe_profit(code, self.PE_LIMIT, self.PROFIT_LIMIT)
        if not result:
            return result, match_reason
        if "score" in match_reason:
            score += float(match_reason.get("score", 0.0))

        # ---- 最近趋势向上 采用复合趋势判断----
        multi_trend = MultiTrendFactor(
            adx_period = self.TREND_DAYS_LIMIT,
            vwap_period = self.TREND_DAYS_LIMIT,
            high_low_period = self.TREND_DAYS_LIMIT
        )
        trend_score = multi_trend.score(stock_df)
        score += trend_score
        if trend_score >= self.UP_TREND_SCORE:
            # 全部条件满足
            extra["score"] = score
            extra["reason"] = f"达到趋势标准，趋势优先,分数{score}，选中"
            return True, extra
        
        # ---- 最近收盘价相比最低点 > 8% ----
        rebound_factor = LowReboundFactor(self.CONSECUTIVE_DAYS)
        rebound_score = rebound_factor.score(stock_df)
        score += rebound_score
        if score < self.REBOUND_DAY_SCORE:
            return False, {
                "reason": (
                    f"{self.strategy_name}, "
                    "趋势不成立时，不满足总的反弹分值，未选中"
                ),
            }
        extra["score"] = score
        extra["reason"] = f"趋势未成立时，达到反弹总分值,分数{score}，选中"
        # 全部条件满足
        return True, extra

    def _before_minute_route(self, code: str, stock_data: dict[str, Any]) -> None:
        """分钟路由前保存分钟行情缓存。"""
        self._save_minute_cache(code, stock_data)

    # ---- 内部方法 ----

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """检查当天买入条件。

        条件：
            当天条件（_check_buy_conditions 中判断，根据打分）
            评分项: 1)当天开盘价 >= 昨日收盘价; 2)交易量连续上升; 3)短线趋势向上; 4)最大跌幅不会跌穿当天第二支撑位

        :param code: 股票代码
        :param stock_data: 实时/分钟行情数据
        :return: (是否满足, 详细信息)
        """
        try:
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            create_time: str = str(stock_data.get("create_time", ""))
            score: float = 0.0
            
            if current_price <= 0:
                return False, {
                    "reason":(
                        f"{self.strategy_name}, func:check_buy_conditions,"
                        "当前价格异常"
                    ),
                    "code": code
                    }

            if open_price <= 0 or preclose <= 0:
                return False, {
                    "reason":(
                        f"{self.strategy_name}, func:check_buy_conditions,"
                        "开盘价或昨收价数据异常"
                    ),
                    "code": code
                    }
            cache_result = self._get_score_cache(code, "BUY", create_time)
            score = cache_result.get("score", 0.0)
            check_num = cache_result.get("check_number", 0)
            # 如果涨停或跌停则不买入（距离涨停价 0.5% 以内视为涨停）
            if preclose > 0 and abs((current_price - preclose) / preclose * 100) > 9.5:
                return False, {
                    "reason": (
                        f"{self.strategy_name},"
                        f"买入条件不满足: 当前价{current_price:.2f}"
                        f" 接近涨停或跌停s（昨收{preclose:.2f}），没法操作"
                    ),
                    "code": code,
                    "current_price": current_price,
                }
            # 解析当前分钟时间
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:check_buy_conditions,"
                        f"无法解析分钟时间: {create_time}"
                    ),
                    "code": code,
                }
            current_hour: int = minute_dt.hour
            current_minute: int = minute_dt.minute

            if minute_dt.hour == 9 and minute_dt.minute < 35:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, 9:35之前不操作，过滤开盘行情抖动"
                    ),
                    "code": code
                }
            # ---- 当天开盘价 >= 昨日收盘价 ----
            if preclose > 0 and score == 0.0 and current_hour == 9 and current_minute < 40:
                low_open_score = (open_price - preclose) / preclose * 100
                # print(f"code={code}, time:{create_time}, low_open_score:{low_open_score}")
                score += low_open_score
            
                        
            # ---- 交易量连续上升 ----
            ok, result = self._check_volume_price_condition(
                code, current_price, open_price, preclose, create_time
            )
            if ok and "score" in result:
                volume_score = result.get("score", 0.0)
                # print(f"code={code}, time:{create_time}, volume_score:{volume_score}")
                score += volume_score

            minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
            stock_df = pd.DataFrame(minute_cache)
            # --- 检查多个趋势组合因子---
            mt_factor = MultiTrendFactor(self.BUY_TREND_MINUTES_LIMIT)
            result = mt_factor.score(stock_df)
            # print(f"code={code}, time:{create_time}, mt_factor:{result}")
            score += float(result)

            # ---- 股价和当天均价的距离 ----
            ok, reason = self._check_above_avg_price(code)
            if ok and "score" in reason:
                above_price_score=reason.get("score", 0.0)
                # print(f"code={code}, time:{create_time}, above_price_score:{above_price_score}")
                score += above_price_score
            # 求平均值
            check_num = check_num + 1
            avg_score: float = round(float(score / check_num), 2)
            res: bool = avg_score >= self.BUY_MIN_SCORE
            extra = {
                "code": code,
                "score": avg_score,
                "signal": "BUY",
                "price": current_price
            }
            # print(f"code={code}, time:{create_time}, avg_score:{avg_score}")
            if not res:
                extra["reason"] = f"{self.strategy_name}, 最终计算的买入分值不足最小值,最小值:{self.BUY_MIN_SCORE}, 当前分值:{avg_score}"
            else:
                extra["reason"] = f"买入条件合格，分值超过最小值,当前分值:{avg_score},最小分值:{self.BUY_MIN_SCORE}"
                
            # 记录分数
            self._save_score_cache(code, "BUY", score, check_num, create_time, res)
            return res, extra
        except Exception as exc:
            self.logger.warning(
                "[%s] _check_buy_conditions(%s) error: %s",
                self.strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}


    def _check_above_avg_price(
            self, code: str,
        ) -> tuple[bool, dict[str, Any]]:
            """评分：股价高于当天均价（VWAP）。

            使用分钟缓存中的 price 和 volume 通过 VwapFactor 计算当天成交量加权均价，
            要求当前价 > 当天 VWAP。

            :param code: 股票代码
            :return: (是否满足, 详细信息)
            """
            minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
            if not minute_cache:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:_check_buy_above_avg_price,"
                        "分钟缓存为空，无法计算当天均价"
                    ),
                    "code": code,
                }

            vwap_factor = self.factor_manager.get(VwapFactor.factor_name)
            if vwap_factor is None:
                return False, {
                    "reason":
                        (
                            f"{self.strategy_name}, func:_check_buy_above_avg_price,"
                            "VwapFactor 未注册"
                        ),
                    "code": code,
                }
            score = vwap_factor.score(pd.DataFrame(minute_cache))
            # 全部条件满足
            return True, {
                "code": code,
                "score": score,
                "signal": "BUY",
            }

    def _check_volume_price_condition(
        self,
        code: str,
        current_price: float,
        open_price: float,
        preclose: float,
        create_time: str,
    ) -> tuple[bool, dict[str, Any]]:
        """N 分钟成交量放大且股价上升。"""
        volume_factor = self.factor_manager.get(VolumeExpansionFactor.factor_name)
        if volume_factor is None:
            return False, {
                "reason": (
                    f"{self.strategy_name}, func:check_buy_conditions,"
                    "跌幅因子未注册"
                ),
            }
        minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
        if not minute_cache:
            return False, {
                "reason": (
                    f"{self.strategy_name}, func:check_buy_conditions,"
                    "分钟缓存为空，无法判断量价"
                ),
                "code": code,
            }
        score = volume_factor.score(pd.DataFrame(minute_cache))
        # 全部条件满足
        return True, {
            "code": code,
            "current_price": current_price,
            "open": open_price,
            "preclose": preclose,
            "time": create_time,
            "score": score,
            "signal": "BUY",
        }

    

    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """
        条件：
        1. 卖点1: 相比持仓到目前最高点回撤超过8%（相对持仓最高点），如果没有持仓就判断最近3天
        2. 卖点2: 跳空低开
        3 卖点3： 股价低于平均价
        4 卖点4: 当天股价超出当天最大阻力位
        5. 卖点5: 14:40之后最低价低于昨日最低价，最高价低于昨日最高价

        :param code: 股票代码
        :param position: 持仓信息，含 cost_price / buy_time / quantity 等字段
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
        buy_time: str = str(position.get("buy_time", ""))
        create_time: str = str(stock_data.get("create_time", ""))
        if buy_time == "":
            today_str: str = self.get_recent_trading_day()
            if create_time and len(create_time) > 10:
                today_str = create_time
            buy_time = (datetime.datetime.strptime(today_str, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        extra: dict[str, Any] = {"code": code, "cost_price": cost_price}
        score: float = 0.0
        cache_score = self._get_score_cache(code, "SELL", create_time)
        score = cache_score.get("score", 0.0)
        check_num = cache_score.get("check_number", 0)
        try:
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            day_high: float = float(stock_data.get("high", 0))
            day_low: float = float(stock_data.get("low", 0))
            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:check_sell_conditions,"
                        f"当前价格{current_price}异常"
                        ),
                        "code": code
                    }
            extra["current_price"] = current_price
            # 如果涨停或跌停则不操作
            if preclose > 0 and abs((current_price - preclose) / preclose * 100) >= 9.5:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:check_sell_conditions,"
                        f"当前价格{current_price}接近涨停或跌停，无法操作"
                    ),
                    "code": code,
                }
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, func:check_sell_conditions,"
                        f"解析时间异常，无法操作"
                    ),
                    "code": code,
                }
            if minute_dt.hour == 9 and minute_dt.minute < 35:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, 9:35之前不操作，过滤开盘行情抖动"
                    ),
                    "code": code
                }
            # 获取当天的历史行情
            minute_cache = self._get_minute_cache(code)
            
            # 跳空低开 计分
            if minute_dt.hour == 9 and score == 0.0 and minute_dt.minute < 40:
                open_gap = self._check_sell_gap_open(open_price, preclose, extra)
                # print(f"开盘低开得分,action=SELL, code:{code}, time:{create_time}, open_gap:{open_gap}")
                score += open_gap
            # 相比持仓到目前最高点回撤超过8%（相对持仓最高点），如果没有持仓就判断最近3天
            # 计算截至目前的当天历史最高价位,
            # 
            highest_price: float = self._resolve_highest_price(
                code, buy_time, create_time, day_high, cost_price
            )
            extra["highest_price"] = round(highest_price, 2)
            sell_drawdown = self._check_sell_drawdown(current_price, highest_price, extra)
            # print(f"最高点到目前价位得分, action=SELL, code:{code}, time:{create_time}, sell_drawdown:{sell_drawdown}")
            score += sell_drawdown

            df = pd.DataFrame(minute_cache)
            # 股价低于平均价
            # ---- 股价和当天均价的距离 ----
            vwap_factor = VwapFactor(self.SELL_VWAP_LIMIT)
            result = vwap_factor.score(df)
            # print(f"股价低于均价得分, action=SELL, code:{code}, time:{create_time}, result:{result}")
            score += float(result)

            # --- 检查多个趋势组合因子---
            mt_factor = MultiTrendFactor(self.SELL_TREND_MINUTES_LIMIT)
            result = mt_factor.score(df)
            # print(f"斜率趋势 action=SELL, code={code}, time:{create_time}, mt_factor:{result}")
            score += float(result)

            check_num = check_num + 1
            avg_score: float = round(float(score / check_num), 2)
            # 平均值
            # print(f"每次平均值得分, action=SELL, code:{code}, time:{create_time}, avg_score:{avg_score}")
            res: bool = avg_score < self.SELL_MAX_SCORE
            self._save_score_cache(code, "SELL", score, check_num, create_time, res)
            if res:
                extra["signal"] = "SELL"
                extra["score"] = avg_score
                extra["reasons"] = f"卖出分数{avg_score} 小于 设定值{self.SELL_MAX_SCORE}"
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

    def _get_highest_since_buy(
            self, code: str, buy_time: str, today_str: str, today_high: float = 0,
        ) -> float:
        """查询买入至今的日K线最高价。

        通过 MarketApi 拉取 buy_time 至今的日K线数据，计算区间最高价。
        若当日现价已高于历史日K线最高价，以当日现价为准。

        :param code: 股票代码
        :param buy_time: 买入时间字符串（YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）
        :param today_high: 当日最高价
        :return: 持仓期间最高价，查询失败返回 0
        """
        if not buy_time:
            return 0.0

        try:
            end_day: str = (datetime.datetime.strptime(today_str, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            buy_date: str = buy_time[:10]  # 截取日期部分
            klines: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                code=code, start=buy_date, end=end_day,
            )
            if not klines:
                self.logger.warning("未查到 %s 的日K线数据 (%s ~ %s)", code, buy_date, end_day)
                return 0.0

            db_highest: float = max(float(k.get("high", 0)) for k in klines)
            highest: float = max(db_highest, today_high)
            return highest

        except Exception as exc:
            self.logger.warning("[%s] 查询持仓最高价失败(%s): %s", self.strategy_name, code, exc)
            return 0.0
            
    def _resolve_highest_price(
        self, code: str, buy_time: str, today_str, day_high: float, cost_price: float
    ) -> float:
        """持仓期间最高价：优先从日K线查询，失败回退内存追踪。"""
        db_highest: float = self._get_highest_since_buy(code, buy_time, today_str, day_high)
        if db_highest > 0:
            return db_highest
        if code not in self._highest_prices:
            self._highest_prices[code] = max(cost_price, day_high)
        elif day_high > self._highest_prices[code]:
            self._highest_prices[code] = day_high
        return self._highest_prices.get(code, day_high)

    def _check_sell_drawdown(
        self,
        current_price: float,
        highest_price: float,
        extra: dict[str, Any],
    ) -> float:
        """相比持仓最高点回撤超过阈值。"""
        if highest_price <= 0:
            return 0.0
        drawdown: float = (current_price - highest_price) / highest_price * 100
        extra["drawdown"] = round(drawdown, 2)
        if drawdown < self.SELL_DRAWDOWN_PCT:
            extra["reason"] = (
                f"{self.strategy_name},"
                f"持仓最高点回撤{drawdown:.2f}% < {self.SELL_DRAWDOWN_PCT}%"
                f" (最高{highest_price:.2f}, 当前{current_price:.2f})"
            )
        return drawdown

    def _check_sell_gap_open(
        self,
        open_price: float,
        preclose: float,
        extra: dict[str, Any],
    ) -> float:
        """判断跳空低开。"""
        if open_price <= 0 or preclose <= 0:
            return 0.0
        pct_chg: float = (open_price - preclose) / preclose * 100
        extra["reason"] = (
            f"{self.strategy_name}, "
            f"开盘价{open_price:.2f} 相比 昨收价{preclose:.2f} 跌幅{pct_chg}"
        )
        return pct_chg

    def _check_hl_price(
        self,
        code: str,
        day_high: float,
        day_low: float,
        create_time: str,
        extra: dict[str, Any],
    ) -> float:
        """最低价低于昨日最低价，最高价低于昨日最高价。检测3个时间点 10:00, 11:20, 14:00"""
        minute_dt: datetime.datetime | None = self._parse_time(create_time)
        score: float = 0.0
        # 最低价低于昨日最低价，最高价低于昨日最高价
        if minute_dt is None:
            return 0.0
        if (minute_dt.hour == 10 and minute_dt.minute == 0) \
            or (minute_dt.hour == 11 and minute_dt.minute >= 0 and minute_dt.minute <= 30) \
            or (minute_dt.hour == 14 and minute_dt.minute >= 20 and minute_dt.minute <= 40):
            df = self.load_his_daily_data(code, create_time, 1)
            if df.empty:
                extra["reason"] = (
                    f"{self.strategy_name}, func:check_sell_conditions,"
                    "无法获取昨日日线数据，不操作"
                )
                return 0.0
            yesterday_low: float = float(df.iloc[-1]["low"])
            yesterday_high: float = float(df.iloc[-1]["high"])
            if yesterday_low <= 0 or yesterday_high <= 0:
                extra["reason"] = (
                    f"{self.strategy_name}, "
                    "昨日日线数据异常，跳出不操作"
                )
                return 0.0
            score += (day_high - yesterday_high) / yesterday_high * 100
            score += (day_low - yesterday_low) / yesterday_low * 100
            if day_high < yesterday_high and day_low < yesterday_low:
                extra["reason"] = (
                    f"{self.strategy_name},"
                    f"最低价{day_low:.2f} < 昨日最低价{yesterday_low:.2f}"
                    f" 且 最高价{day_high:.2f} < 昨日最高价{yesterday_high:.2f}"
                )
        return score

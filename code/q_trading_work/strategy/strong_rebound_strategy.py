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

from app_context import AppContext
from core.user_strategy_cache import UserStrategyCache
from factor.low_rebound_factor import LowReboundFactor
from factor.multi_trend_factor import MultiTrendFactor
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
            加分项: 1)交易量连续上升; 2)趋势向上

    卖出条件：
        1. 最近两日跌幅（从最高点计算) 超过8%
        2. 最近两日最低价和最高价都低于前一天
        3. 积分项：1）当天股价超出阻力位；2）当天股价低开；3）股价低于平均价次数百分率
    """

    strategy_name: str = "强趋势策略"
    description: str = (
        "强趋势策略：股价最近连续反弹，最近涨幅增大而跌幅有限"
    )

    #适配
    PE_LIMIT: int = 300
    PROFIT_LIMIT: float = 10.0
    TREND_DAYS_LIMIT: int = 7
    CONSECUTIVE_DAYS: int = 5  # 最近连续交易日数（买入条件1）
    UP_TREND_SCORE: float = 6.0 # 向上趋势分数
    REBOUND_DAY_SCORE: float = 10.0  # 最近反弹阈值（%）

    # 买入参数
    # 以下默认值与 cfg/strategy.yaml[strong_rebound_strategy] 保持一致。
    # 无参数实例化（回测 CLI）走的是 yaml，两边不一致时「按源码默认值推断实际
    # 生效参数」会得出错误结论。
    # 连续交易日
    BUY_CONSECUTIVE_MINUTES: int = 10  # 连续分钟数（买入点1）
    BUY_TREND_MINUTES_LIMIT: int = 10  # 分钟交易趋势限制
    BUY_MIN_SCORE: float = 6.0  # 买入的最小分数（10 分制）

    # 当天买入综合分的权重：**成交量为主、趋势为辅**，两者均为 0~1 归一化后加权，
    # 再乘 10 得到与「10 分制」一致的刻度（上限 = (0.7 + 0.3) × 10 = 10）。
    # 原实现把两个分量直接相加再硬除以 2：成交量条件不成立时只有趋势分进分子
    # 却仍被除以 2，等于把趋势门槛抬高一倍；按当时配置，成交量侧上限 5.5、
    # 趋势侧上限 7.0，avg 上限仅 6.25，与 min_score=6.0 只差 0.25。
    BUY_VOLUME_WEIGHT: float = 0.7
    BUY_TREND_WEIGHT: float = 0.3
    # 成交量侧的可达上限（_check_volume_price_condition 的最大正向分：
    # 连续放量 2.0 + 斜率 1.5 + 价涨量增 2.0 = 5.5）
    BUY_VOLUME_MAX_SCORE: float = 5.5

    # 卖出参数（默认值同样与 cfg/strategy.yaml 对齐）
    SELL_TREND_MINUTES_LIMIT: int = 10  # 卖出交易分钟趋势限制
    SELL_DRAWDOWN_PCT: float = -6.0  # 回撤阈值（%）
    SELL_VWAP_LIMIT: int = 10  # vwap 均价的分钟数
    SELL_MAX_SCORE: float = -8.0  # 卖出的最大分数

    def __init__(
        self,
        user_strategy_id: str = "",
        user_strategy_name: str = "",
        strategy_id: str = "",
        strategy_type: str = "",
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        """初始化强势反弹策略。

        `user_strategy_name` 必须与基类签名一致：workflow 通过
        `_instantiate_strategy` 用关键字传参，子类 `__init__` 少了这个形参会抛
        `TypeError: got an unexpected keyword argument 'user_strategy_name'`，
        被 `_instantiate_strategy` 的 `except Exception` 兜住后**整个用户策略被跳过**
        （不建仓、不发信号、不卖），日志里只有一条 ERROR。
        同时必须继续往 `super().__init__()` 传，否则 `self.user_strategy_name`
        仍是空串，策略内所有以它为前缀的日志/原因文本会变成以逗号开头的空名字。
        """
        # 必须调用基类初始化：设置 logger/factor_manager、加载策略参数并创建选股列表缓存字段
        super().__init__(
            user_strategy_id=user_strategy_id,
            user_strategy_name=user_strategy_name,
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            user_strategy_params=user_strategy_params,
            default_params=default_params,
        )
        self.logger = logging.getLogger(__name__)

    def apply_params(self, params: dict[str, Any]) -> None:
        """将参数字典映射到策略属性。

        参数字典结构与 cfg/strategy.yaml 的 strong_rebound_strategy 节一致：
            {match: {...}, buy: {...}, sell: {...}}
        缺失的字段保留类属性默认值。

        :param params: 策略参数字典
        """
        if not isinstance(params, dict) or not params:
            return
        match_config: dict[str, Any] = params.get("match", {}) or {}
        buy_config: dict[str, Any] = params.get("buy", {}) or {}
        sell_config: dict[str, Any] = params.get("sell", {}) or {}

        # 适配参数（match）
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
        self.SELL_VWAP_LIMIT = int(sell_config.get("vwap_minutes_limit", self.SELL_VWAP_LIMIT))
        self.SELL_MAX_SCORE = float(sell_config.get("max_score", self.SELL_MAX_SCORE))
        self.SELL_TREND_MINUTES_LIMIT = int(sell_config.get("trend_minutes_limit", self.SELL_TREND_MINUTES_LIMIT))

    # ---- 基类抽象方法实现 ----

    def init_factors(self) -> None:
        """注册策略所需因子。

        本策略通过 API 获取行情数据，因子用于 is_match_strategy 的条件计算。
        """
        pass

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
                    f"{self.user_strategy_name}, 数据不足，需至少10个交易日"
                }

        score: float = 0.0
        stock_df: pd.DataFrame = pd.DataFrame(stock_data)
        
        # ---- 条件1：市盈率<300 且 利润增长率>10% ----
        code: str = str(stock_data[0].get("code", "")) if stock_data else ""
        if not code:
            return False, {
                "reason": (
                    f"{self.user_strategy_name}, 条件1不满足: 无法获取股票代码"
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

        # ---- 最近收盘价相比最低点 > 8% ----
        rebound_factor = LowReboundFactor(self.CONSECUTIVE_DAYS)
        rebound_score = rebound_factor.score(stock_df)
        score += rebound_score
        if trend_score >= self.UP_TREND_SCORE and rebound_score >= self.REBOUND_DAY_SCORE:
            # 全部条件满足
            extra["score"] = round(score, 2)
            extra["reason"] = f"{self.user_strategy_name},趋势成立并且最近反弹力度达到设置，趋势分{trend_score:.2f},反弹分值{rebound_score:.2f},总分数{score:.2f}，选中"
            return True, extra
        extra["score"] = round(score, 2)
        extra["reason"] = f"{self.user_strategy_name},趋势未成立时或者反弹力度不达标，趋势分{trend_score:.2f},反弹分值{rebound_score:.2f},总分数{score:.2f}，未选中"
        # 全部条件满足
        return False, extra

    def _before_minute_route(self, code: str, stock_data: dict[str, Any]) -> None:
        """分钟路由前钩子。

        注意：分钟行情缓存已改为由 `StrategyWorkflow` 单点写入 UserStrategyCache
        （每个 code 只写一次），策略侧只读，因此这里不再写入。
        """
        return

    # ---- 内部方法 ----

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """检查当天买入条件。

        条件（成交量为主、趋势为辅，两部分各归一化后加权，10 分制）：
            1) 交易量连续放大 + 量价配合（权重 0.7）
            2) 短线趋势向上（权重 0.3）

        综合分 >= `BUY_MIN_SCORE` 时触发买入。因成交量侧单独打满也只有 0.7，
        而阈值 6.0/10 = 0.6，所以只要成交量条件达标即可买入，趋势只用于加厚分数；
        反过来，趋势满分成全也补不上成交量不足（0.3 < 0.6）。

        :param code: 股票代码
        :param stock_data: 实时/分钟行情数据
        :return: (是否满足, 详细信息)
        """
        try:
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            create_time: str = str(stock_data.get("create_time", ""))

            if current_price <= 0:
                return False, {
                    "reason":(
                        f"{self.user_strategy_name}, 当前价格异常"
                    ),
                    "code": code
                }

            if open_price <= 0 or preclose <= 0:
                return False, {
                    "reason":(
                        f"{self.user_strategy_name}, 开盘价或昨收价数据异常"
                    ),
                    "code": code
                }
            # 解析当前分钟时间
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_buy_conditions,无法解析分钟时间: {create_time}"
                    ),
                    "code": code,
                }
            current_hour: int = minute_dt.hour
            current_minute: int = minute_dt.minute

            # 涨停或跌停附近不买入。阈值按该股票的涨跌停幅度推算（ST 5%、创业板/科创板
            # 20%、其余 10%），留 2 个百分点余量。原实现硬编码 9.5%，对 20% 板的股票
            # 在 ±9.5% 就被挡掉（离涨跌停还很远），ST 封在 ±5% 反而放行。
            chg_percent: float = self._change_percent(stock_data, current_price, preclose)
            near_limit: float = self._near_price_limit_pct(
                code, str(stock_data.get("name", "") or ""), margin=2.0
            )
            if preclose > 0 and abs(chg_percent) >= near_limit:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}接近涨停或跌停,涨跌幅{chg_percent:.2f}%（阈值±{near_limit:.1f}%），不再判断"
                    ),
                    "code": code,
                    "current_price": round(current_price, 2)
                }
            # 避开 9:40以前和 13--13:10时间，避免突然开盘信息干扰
            if (current_hour == 9 and current_minute <= 40) or (current_hour == 13 and current_minute <= 10):
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 9:35之前不操作，过滤开盘行情抖动"
                    ),
                    "code": code
                }
                        
            # ---- 交易量连续上升 ----
            ok, result = self._check_volume_price_condition(
                code, current_price, open_price, preclose, create_time
            )
            volume_score: float = 0.0
            if ok:
                # 成交量侧分数**可正可负**：为负说明是「价跌量增」等不利形态，
                # 应当拉低综合分而不是被当成中性（原实现只在 signal=="BUY" 时取值，
                # 卖出形态的分直接被丢掉）。
                volume_score = float(result.get("score", 0.0) or 0.0)

            minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
            stock_df = pd.DataFrame(minute_cache)
            # --- 检查多个趋势组合因子（10 分制，可达上限见 MultiTrendFactor）---
            mt_factor = MultiTrendFactor(self.BUY_TREND_MINUTES_LIMIT)
            trend_score: float = mt_factor.score(stock_df)
            # 成交量为主、趋势为辅：两侧各归一化到 0~1 后加权，再乘 10 回到 10 分制。
            # 这样 BUY_MIN_SCORE 的 6.0 仍然是「满分 10 的 60%」，
            # 且成交量侧单独打满（0.7）不足 0.6×10 时趋势侧无法独自把分数拉过阈值
            # —— 与「成交量为主」的设计一致。
            volume_component: float = volume_score / self.BUY_VOLUME_MAX_SCORE
            trend_component: float = trend_score / MultiTrendFactor.MAX_SCORE
            avg_score: float = (
                volume_component * self.BUY_VOLUME_WEIGHT
                + trend_component * self.BUY_TREND_WEIGHT
            ) * 10.0
            # 星级：10 分制折算 5 星（保留一位小数），服务端字段约束为 0~5
            star: float = max(0.0, min(avg_score / 2.0, 5.0))
            res: bool = avg_score >= self.BUY_MIN_SCORE
            extra = {
                "code": code,
                "score": round(avg_score, 2),
                "star": round(star, 1),
                "signal": "BUY",
                "price": current_price
            }
            if not res:
                extra["reason"] = (
                    f"{self.user_strategy_name}, 最终计算的买入分值不足最小值,"
                    f"最小值:{self.BUY_MIN_SCORE}, 当前分值:{avg_score:.2f}"
                    f"（成交量分:{volume_score:.2f}/{self.BUY_VOLUME_MAX_SCORE}, "
                    f"趋势分:{trend_score:.2f}/{MultiTrendFactor.MAX_SCORE}）"
                )
            else:
                extra["reason"] = (
                    f"买入条件合格，分值超过最小值,当前分值:{avg_score:.2f},"
                    f"最小分值:{self.BUY_MIN_SCORE}"
                    f"（成交量分:{volume_score:.2f}, 趋势分:{trend_score:.2f}）"
                )

            # 记录分数
            return res, extra
        except Exception as exc:
            self.logger.warning(
                "[%s] _check_buy_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}


    def _check_volume_price_condition(
        self,
        code: str,
        current_price: float,
        open_price: float,
        preclose: float,
        create_time: str,
    ) -> tuple[bool, dict[str, Any]]:
        """N 分钟成交量放大且股价上升（成交量侧打分，正向上限 5.5）。

        三个分项：连续放量（±2.0）、成交量斜率（±1.5）、量价配合（±2.0）。

        `vol_price_align == -1`（价跌量增，恐慌出逃）必须**减分**：原实现写的是
        `score += -2.0` 之外的 `score -= -2.0`（等价于 +2.0），把「越跌越放量」
        判成利多。同一份「连续下跌 + 连续放量」的分钟数据下，
        `VolumeExpansionFactor.score` 给 −3.5（signal=None），本函数却给 +1.0（BUY）。

        :param code: 股票代码
        :param current_price: 当前价
        :param open_price: 今开价
        :param preclose: 昨收价
        :param create_time: 行情时间
        :return: (是否得到有效成交量分, 明细；score 可正可负)
        """
        minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
        if not minute_cache:
            return False, {
                "reason": (
                    f"{self.user_strategy_name}, func:check_buy_conditions,"
                    "分钟缓存为空，无法判断量价"
                ),
                "code": code,
            }
        df = pd.DataFrame()
        stocks_df = pd.DataFrame(minute_cache)
        df['vol_ma_short'] = stocks_df['volume'].rolling(self.BUY_CONSECUTIVE_MINUTES).mean()
        # 成交量连续放大且价格上涨
        df['vol_up'] = (
            (stocks_df['volume'] > stocks_df['volume'].shift(1))
        )
        df['consecutive_vol_up'] = df['vol_up'].rolling(self.BUY_CONSECUTIVE_MINUTES).sum()
        # 趋势斜率
        def rolling_slope(series, window):
            """通用快速线性回归斜率"""
            x = np.arange(window)
            w = x - x.mean()
            denom = (w ** 2).sum()
            
            return series.rolling(window).apply(
                lambda y: np.dot(y, w) / denom, raw=True
            )
        df['vol_slope'] = rolling_slope(stocks_df['volume'], self.BUY_CONSECUTIVE_MINUTES)
        # 量价配合
        df['price_change'] = stocks_df['close'].pct_change()
        df['vol_price_align'] = np.where(
            (df['price_change'] > 0) & (stocks_df['volume'] > df['vol_ma_short']),
            1,   # 价涨量增 ✅
            np.where(
                (df['price_change'] < 0) & (stocks_df['volume'] > df['vol_ma_short']),
                -1,  # 价跌量增 ❌
                0    # 其他
            )
        )
        score: float = 0.0
        row = df.iloc[-1]
        price_up = row['price_change'] > 0
        consecutive = row['consecutive_vol_up']
        consecutive_score: float = 0.0
        if consecutive >= 5:
            consecutive_score += 2.0
        elif consecutive >= 4:
            consecutive_score += 1.6
        elif consecutive >= 3:
            consecutive_score += 1.2
        elif consecutive >= 2:
            consecutive_score += 0.8
        else:
            consecutive_score = 0.0
        if not price_up:
            consecutive_score *= -1
        score += consecutive_score

        slope = float(row['vol_slope'])
        vol_ma = df['vol_ma_short']
        slope_score: float = 0.0
        if slope > 0.0 and float(vol_ma.iloc[-1]) > float(vol_ma.shift(5).iloc[-1]):
            slope_score += 1.5
        elif slope > 0.0:
            slope_score += 1.0
        elif slope < 0.0:
            slope_score -= 1.0

        score += slope_score

        if row['vol_price_align'] == 1:
            score += 2.0
        elif row['vol_price_align'] == -1:
            # 价跌量增 = 恐慌出逃，减分（原实现误写为 `score -= -2.0`，等于加分）
            score -= 2.0

        if score == 0.0:
            return False, {
                "reason": (
                    f"{self.user_strategy_name}, 买入条件，成交量条件不满足，分值:{score:.2f}"
                ),
                "code": code,
            }
        # 全部条件满足
        signal = "BUY" if score > 0.0 else "SELL"
        return True, {
            "code": code,
            "current_price": current_price,
            "open": open_price,
            "preclose": preclose,
            "time": create_time,
            "score": score,
            "signal": signal
        }

    

    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """
        条件：
        1. 卖点1: 相比持仓到目前最高点回撤超过8%（相对持仓最高点），如果没有持仓就判断最近2天
        2. 卖点2: 成交量放大且向下
        3 卖点3： 股价低于平均价
        4. 卖点4: 14:40之后最低价低于昨日最低价，最高价低于昨日最高价

        :param code: 股票代码
        :param position: 持仓信息，含 cost_price / buy_time / quantity 等字段
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
        buy_time: str = str(position.get("buy_time", ""))
        create_time: str = str(stock_data.get("create_time", ""))
        if buy_time == "":
            # 设计约定：实时链路以「尽可能产出买卖信号」为目标，不依赖持仓信息，
            # 因此 position 常为空字典，此时 buy_time 不会有值。
            # 兜底规则：没有持仓信息时，以「最近 2 个交易日」作为买入基准来计算最高价。
            today_str: str = self.get_recent_trading_day()
            if create_time and len(create_time) > 10:
                today_str = create_time
            buy_time = self.get_previous_trading_day(today_str=today_str, days=2)
            self.logger.debug(
                "[%s] code=%s 无持仓信息，按最近2个交易日回溯买入基准: %s",
                self.user_strategy_name, code, buy_time,
            )
        extra: dict[str, Any] = {"code": code, "cost_price": cost_price}
        score: float = 0.0
        try:
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            day_high: float = float(stock_data.get("high", 0))
            day_low: float = float(stock_data.get("low", 0))
            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_sell_conditions,"
                        f"当前价格{current_price}异常"
                        ),
                        "code": code
                    }
            extra["current_price"] = current_price
            # 涨停或跌停附近不操作。阈值按该股票的涨跌停幅度推算（ST 5%、创业板/科创板
            # 20%、其余 10%，留 1 个百分点余量）：原实现硬编码 9.5%，ST 封在 ±5% 时
            # 仍会去挂卖单，20% 板的股票在 ±9.5% 却又不卖。
            chg_percent: float = self._change_percent(stock_data, current_price, preclose)
            near_limit: float = self._near_price_limit_pct(
                code, str(stock_data.get("name", "") or "")
            )
            if preclose > 0 and abs(chg_percent) >= near_limit:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 当前价格{current_price}接近涨停或跌停"
                        f"（涨跌幅{chg_percent:.2f}%，阈值±{near_limit:.1f}%），不作卖出判断"
                    ),
                    "code": code,
                }
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 解析时间异常，无法操作"
                    ),
                    "code": code,
                }
            # 卖出操作只忽略9:35之前
            if minute_dt.hour == 9 and minute_dt.minute < 35:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 9:35之前不操作，过滤开盘行情抖动"
                    ),
                    "code": code
                }
            # 获取当天的历史行情
            minute_cache = self._get_minute_cache(code)
            
            # 跳空低开 计分
            open_gap = self._check_sell_gap_open(open_price, preclose, extra)
            if open_gap >= 0:
                score = 0.0
            elif open_gap >= -1.0:
                score = -1.0
            elif open_gap >= -2.0:
                score = -2.0
            else:
                score = -3.0
            # 相比持仓最高点回撤超过 8%（SELL_DRAWDOWN_PCT）。
            # 最高价的口径：buy_time 有值（有持仓信息）→ 取买入至今的日K最高价；
            #                buy_time 无值（实时链路不传持仓）→ 取最近 2 个交易日的最高价，
            #                并由 _resolve_highest_price 叠加当日最高价兜底。
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

            res: bool = score < self.SELL_MAX_SCORE
            if res:
                extra["signal"] = "SELL"
                extra["score"] = round(score, 2)
                # 星级：卖出侧分数为负，按 10 分制折算 5 星并夹到 0~5
                extra["star"] = round(max(0.0, min(-score / 2.0, 5.0)), 1)
                # 字段名必须是 reason：`_save_trade_signal` 读的是
                # `extra.get("reason", "")`，原实现写成 "reasons" 会静默丢失卖出原因
                # （库里的 reason 与 MQTT 载荷都为空）。
                extra["reason"] = f"卖出分数{score:.2f} 小于 设定值{self.SELL_MAX_SCORE}"
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

    def _get_highest_since_buy(
            self, code: str, buy_time: str, today_str: str, today_high: float = 0,
        ) -> float:
        """查询 buy_time 至当前行情时间的日K线最高价。

        通过 MarketApi 拉取 [buy_time[:10], today_str] 区间的日K线，计算区间最高价；
        再与当日最高价 today_high 取较大值，即「区间最高价 ∪ 当日最高价」。

        **量纲说明（口径与文档已对齐）**：`end` 直接取当前行情时间 `today_str`，
        故意**不做「上一交易日」换算** —— 本函数的用途只是拿「最近一段区间的最高点」，
        落在今天还是昨天不影响结果，区间多包含当日未完成的K线也没有副作用
        （反而能让回撤基准更贴近真实高点）。服务端按字符串比较 start/end，
        带时间戳的 end 同样能匹配当日。

        **缓存**：日K结果在日内几乎不变，而实时链路下未持仓股票每分钟都会走一遍
        卖出判定（池内 300 只 = 每分钟 300 次请求），因此在
        `UserStrategyCache` 做二级缓存（key = code + buy_date + 当日），
        同日同区间的查询只发一次请求；内存快照优先，Redis 不可用时自动降级。

        buy_time 的取值约定（见 _check_sell_conditions）：
        - 有持仓信息时为其真实 buy_time，得到「建仓以来」最高价；
        - 无持仓信息（实时链路设计上不传持仓）时为「最近 2 个交易日」，
          得到「最近 2 个交易日 + 当日」最高价。

        :param code: 股票代码
        :param buy_time: 买入时间字符串（YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）；为空直接返回 today_high
        :param today_str: 当前行情时间（YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS），作为区间结束日
        :param today_high: 当日最高价，与区间最高价取较大值
        :return: 持仓期间最高价，查询失败返回 today_high
        """
        if not buy_time:
            return today_high

        buy_date: str = buy_time[:10]  # 截取日期部分
        cache_day: str = UserStrategyCache.day_of(str(today_str))
        cached: float = self._cache.get_highest_price(code, buy_date, cache_day)
        if cached > 0:
            return max(cached, today_high)

        try:
            end_day: str = today_str
            klines: list[dict[str, Any]] = AppContext().market_api.get_day_kline(
                code=code, start=buy_date, end=end_day,
            )
            if not klines:
                self.logger.warning("未查到 %s 的日K线数据 (%s ~ %s)", code, buy_date, end_day)
                return today_high

            db_highest: float = max(float(k.get("high", 0)) for k in klines)
            # 只缓存日K口径的区间最高价（不含 today_high，那是调用方每次传入的），
            # 读取时再与今天的最高价取大，避免不同调用方互相抬高基准。
            self._cache.save_highest_price(code, buy_date, cache_day, db_highest)
            return max(db_highest, today_high)

        except Exception as exc:
            self.logger.warning("[%s] 查询持仓最高价失败(%s): %s", self.user_strategy_name, code, exc)
            return today_high
            
    def _resolve_highest_price(
        self, code: str, buy_time: str, today_str, day_high: float, cost_price: float
    ) -> float:
        """持仓期间最高价：以日K线查询结果为准，查询失败时回退到内存追踪。

        降级链：
        1. `_get_highest_since_buy` 返回 > 0 → 直接采用（买入至今 / 最近 2 个交易日）；
        2. 查询失败或区间无数据（返回 0）→ 用 `_highest_prices` 记录当日起的盘中最高价，
           初值 max(cost_price, day_high)；实时链路 cost_price 为 0，等价于「当日最高价」，
           回撤口径退化为日内回撤（仅在日K接口异常时发生）。
        """
        db_highest: float = self._get_highest_since_buy(code, buy_time, today_str, day_high)
        if db_highest > day_high:
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
                f"{self.user_strategy_name},"
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
            f"{self.user_strategy_name}, "
            f"开盘价{open_price:.2f} 相比 昨收价{preclose:.2f} 跌幅{pct_chg}"
        )
        return pct_chg
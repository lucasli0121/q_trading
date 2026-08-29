"""
Author: liguoqiang
Date: 2026-06-29
LastEditors: liguoqiang
LastEditTime: 2026-07-26
Description: 波段交易策略
    低频依照支撑位和阻力位提供交易策略
    买入条件（全部满足）：
        历史行情条件（is_match_strategy 中判断）
            1. 昨日股价>支撑位并接近支撑位(3%的误差)
            2. 昨日股价<任何一个阻力位
            3. 如果有多个支撑价位，靠近任何一个即可

        当天条件（_check_buy_conditions 中判断）
            1. 今日股价接近支撑位上下(0.05%的误差)

    卖出条件（满足其一）：
        1. 今日股价接近阻力位(1%的误差)或者大于阻力位
        2. 今日14:40以后股价<支撑位并且相比支撑位跌幅>3%
"""

from __future__ import annotations

import datetime
from typing import Any

import pandas as pd

from app_context import AppContext
from factor.kline_support_resistance import KlineSRFactor
from strategy.base_strategy import BaseStrategy


class SwingTradingStrategy(BaseStrategy):
    """低频波段交易策略。

    买入条件（全部满足）：
        历史行情条件（is_match_strategy 中判断）
            1. 市盈率和利润增长率不能太低(市盈率<300, 利润增长率>10%，所属行业金融业，非银金融除外)
            2. 10日内最低点到最高点反弹超过8%,10内最高点到昨日收跌，但是昨日最低价和支撑位上下(3%的误差)
            3. 昨日收盘价>支撑位

        当天条件（_check_buy_conditions 中判断）
            1. 买入1：开盘价相比昨日收盘价不能过低，当天开盘价 - 昨日收盘价 > -3%
            2. 买入2：分时段检查，股价未跌穿支撑位(跌幅>-2%)且从最低点反弹：
               - 11:00-11:30: 股价 > 最低价
               - 13:30-14:00: 股价相比最低价涨幅 > 1%
               - 14:30-14:50: 股价相比最低价涨幅 > 2%
            3. 涨停不买入

    卖出条件（满足其一）：
        1. 今日股价上涨在阻力位上下附近(1%的误差)
        2. 今日14:40以后股价<支撑位并且相比支撑位跌幅>3%，如果股价涨停则不卖出
    """

    strategy_name: str = "低频波段交易策略"
    description: str = (
        "是用于网格交易模式，通过支撑价位和阻力价位来回交易，产生收益"
    )

    # ---- 买入参数 ----
    SUPPORT_NEAR_PCT: float = 3.0  # 历史行情中接近支撑位的误差（%）
    BUY_SUPPORT_TOLERANCE: float = 0.5  # 当天买入接近支撑位的误差（%）

    # ---- 卖出参数 ----
    SELL_RESISTANCE_TOLERANCE: float = 1.0  # 接近阻力位的误差（%）
    SELL_SUPPORT_DROP_PCT: float = 3.0  # 跌破支撑位幅度（%）

    # ---- 支撑/阻力位计算参数 ----
    SR_WINDOW: int = 5  # 寻找局部极值点的窗口大小
    SR_TOLERANCE: float = 0.02  # 相近价位聚类合并的容差

    def __init__(self) -> None:
        """初始化波段交易策略。"""
        # 必须调用基类初始化：设置 logger/factor_manager，并创建选股列表缓存字段
        super().__init__()
        # 缓存每只股票匹配到的支撑位和阻力位列表（is_match_strategy 填充）
        self._matched_support: dict[str, float] = {}
        self._matched_resistances: dict[str, list[float]] = {}

    # ---- 基类抽象方法实现 ----

    def init_factors(self) -> None:
        """注册策略所需因子。

        本策略使用 KlineSRFactor 基于历史K线数据计算支撑位和阻力位。
        """
        self.factor_manager.register(
            KlineSRFactor.factor_name,
            KlineSRFactor(days=15),
        )

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        """判断历史日线数据是否满足策略买入信号（用于回测/选股）。

        stock_data 是历史行情，不含当天行情。

        条件：
        1. 市盈率和利润增长率不能太低(市盈率<300, 利润增长率>10%，所属行业金融业，非银金融除外)
        2. 昨日最低价 > 其中一个支撑位并接近支撑位(3%的误差) 并且下影线比较长

        :param stock_data: 股票日线行情数据列表，每项含 close/open/high/low 等字段
        :return: (是否匹配, 详细信息字典)
        """
        if not stock_data or len(stock_data) < self.SR_WINDOW:
            return False, {"reason": f"{self.strategy_name},func:is_match_strategy,数据不足，需至少{self.SR_WINDOW}个交易日"}

        stock_df: pd.DataFrame = pd.DataFrame(stock_data)
        code: str = str(stock_df.iloc[0].get("code", ""))

        # ---- 条件1：市盈率<300, 利润增长率>10%, 此项检查不包含：金融业和非银金融行业 ----
        if not code:
            return False, {"reason": "条件1不满足: 无法获取股票代码"}
        passed, reason = self._check_industry_pe(code)
        if not passed:
            return False, reason

        return self._check_support_resistance(stock_df, code)

    def _check_industry_pe(self, code: str) -> tuple[bool, dict[str, Any]]:
        """条件1：非金融行业需要满足市盈率与利润增长率要求。"""
        try:
            stock_info_list: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(codes=code)
            industry: str = ""
            if stock_info_list:
                info: dict[str, Any] = stock_info_list[0]
                industry = str(info.get("industry", info.get("industry_name", "")))
            # 不属于金融或者非银金融时才检查市盈率和利润率
            if "金融" not in industry and "非银金融" not in industry:
                passed, reason = self._match_pe_profit(code, 300, 10)
                if not passed:
                    return False, reason
        except Exception as exc:
            self.logger.warning("查询行业信息失败 code=%s: %s", code, exc, exc_info=True)
        return True, {}

    def _check_support_resistance(
        self, stock_df: pd.DataFrame, code: str
    ) -> tuple[bool, dict[str, Any]]:
        """条件2: 10日内最低点到最高点反弹超过8%,10日内最高点到昨日收跌，昨日最低价在支撑位上下3%误差内
        条件3: 昨日收盘价>支撑位

        :param stock_df: 历史日线DataFrame，含 high/low/close 字段，按时间升序
        :param code: 股票代码
        :return: (是否匹配, 详细信息)
        """
        # ---- 计算支撑位和阻力位 ----
        sr_factor = self.factor_manager.get(KlineSRFactor.factor_name)
        if sr_factor is None:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    "支撑阻力因子未注册"
                )
            }

        sr_result: dict[str, list[float]] = sr_factor.calculate(stock_df)
        support_levels: list[float] = sr_result.get("support", [])
        resistance_levels: list[float] = sr_result.get("resistance", [])

        if not support_levels:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    "未找到有效支撑位"
                )
            }

        # ---- 昨日数据（DataFrame 最后一行） ----
        yesterday = stock_df.iloc[-1]
        yesterday_low: float = float(yesterday["low"])
        yesterday_close: float = float(yesterday["close"])
        yesterday_idx = stock_df.index[-1]

        # ---- 最近10个交易日 ----
        recent_10: pd.DataFrame = stock_df.iloc[-10:]
        low_10: float = float(recent_10["low"].min())
        high_10: float = float(recent_10["high"].max())

        if low_10 <= 0:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    "10日内最低价异常"
                )
            }

        # ---- 条件2a: 10日内最低点到最高点反弹超过8% ----
        rebound_pct: float = (high_10 - low_10) / low_10 * 100
        if rebound_pct <= 8.0:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    f"条件2a不满足: 10日内反弹{rebound_pct:.2f}% <= 8%"
                ),
                "low_10": low_10,
                "high_10": high_10,
                "rebound_pct": round(rebound_pct, 2),
            }

        # 最低点必须出现在最高点之前（先见底、后反弹）
        low_10_idx = int(recent_10["low"].idxmin())
        high_10_idx = int(recent_10["high"].idxmax())
        if low_10_idx >= high_10_idx:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    "条件2a不满足: 10日内最低点未出现在最高点之前"
                ),
            }

        # ---- 条件2b: 10日内最高点到昨日收跌 ----
        # 最高点必须出现在昨日之前，且昨日收盘价 < 最高价
        if high_10_idx >= yesterday_idx:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    "条件2b不满足: 10日内最高点即昨日，未发生回调"
                ),
            }
        if yesterday_close >= high_10:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    f"条件2b不满足: 昨日收盘{yesterday_close:.2f} >= 10日最高{high_10:.2f}，未收跌"
                ),
                "yesterday_close": yesterday_close,
                "high_10": high_10,
            }

        # ---- 条件2c: 昨日最低价在支撑位上下3%误差内 ----
        supports_near_low: list[float] = [
            s
            for s in support_levels
            if abs((yesterday_low - s) / s * 100) <= self.SUPPORT_NEAR_PCT
        ]
        if not supports_near_low:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    f"条件2c不满足: 昨日最低价{yesterday_low:.2f}"
                    f" 不在任一支撑位 {support_levels} 的{self.SUPPORT_NEAR_PCT}%范围内"
                ),
                "yesterday_low": yesterday_low,
                "support_levels": support_levels,
            }

        # 取最接近昨日最低价的支撑位（最高的那个）
        nearest_support: float = max(supports_near_low)

        # ---- 条件3: 昨日收盘价 > 支撑位 ----
        if yesterday_close <= nearest_support:
            return False, {
                "reason": (
                    f"{self.strategy_name},func:is_match_strategy,"
                    f"条件3不满足: 昨日收盘价{yesterday_close:.2f} <= 支撑位{nearest_support:.2f}"
                ),
                "yesterday_close": yesterday_close,
                "nearest_support": nearest_support,
            }

        # ---- 全部条件满足：缓存匹配结果 ----
        if code:
            self._matched_support[code] = nearest_support
            self._matched_resistances[code] = resistance_levels

        return True, {
            "code": code,
            "close": yesterday_close,
            "matched_support": nearest_support,
            "resistance_levels": resistance_levels,
            "support_levels": support_levels,
            "signal": "BUY",
        }

    def before_trading(
        self, trade_date: str, stock_codes: list[str], **kwargs: Any
    ) -> None:
        """每天开盘前准备工作。

        清理缓存，确保新一天以干净状态开始。

        :param trade_date: 交易日日期字符串 YYYY-MM-DD
        :param stock_codes: 当日关注的股票代码列表
        :param kwargs: 扩展参数
        """
        super().before_trading(trade_date, stock_codes, **kwargs)
        self._matched_support.clear()
        self._matched_resistances.clear()
        self.logger.info(
            "[%s] before_trading(trade_date=%s, codes=%d)",
            self.strategy_name, trade_date, len(stock_codes),
        )

    # ---- 内部方法 ----

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """检查当天买入条件。

        条件：
            1. 买入1：开盘价相比昨日收盘价不能过低，当天开盘价 - 昨日收盘价 > -3%
            2. 买入2：分时段检查，股价未跌穿支撑位(跌幅>-2%)且从最低点反弹：
               - 11:00-11:30: 股价 > 最低价
               - 13:30-14:00: 股价相比最低价涨幅 > 1%
               - 14:30-14:50: 股价相比最低价涨幅 > 2%
            3. 涨停不买入

        :param code: 股票代码
        :param stock_data: 实时/分钟行情数据
        :return: (是否满足, 详细信息)
        """
        try:
            preclose: float = float(stock_data.get("preclose", 0))
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            low_price: float = float(stock_data.get("low", 0))
            create_time: str = str(stock_data.get("create_time", ""))

            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:_check_buy_conditions,"
                        "当前价格异常"
                    ),
                    "code": code
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
                
            # ---- 条件1：当天开盘价 - 昨日收盘价 > -3% ----
            # 只在开盘时检查，避免开盘后股价波动导致误判
            if current_hour == 9 and current_minute < 50:
                if preclose > 0:
                    chg = (open_price - preclose) / preclose * 100
                    if chg < -3.0:
                        return False, {
                            "reason": (
                                f"{self.strategy_name}, func:check_buy_conditions,"
                                f"买点1不符合: 低开过大,开盘价{open_price}相比昨收价{preclose}涨幅{chg} < -3%: "
                            ),
                            "code": code,
                            "open": open_price,
                            "preclose": preclose,
                        }

            # 如果涨停或跌停则不买入（距离涨停价 0.5% 以内视为涨停）
            if preclose > 0 and abs((current_price - preclose) / preclose * 100) >= 9.5:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:_check_buy_conditions,"
                        f"买入条件不满足: 当前价{current_price:.2f}"
                        f" 接近涨停或跌停s（昨收{preclose:.2f}），不买入"
                    ),
                    "code": code,
                    "current_price": current_price,
                }
            
            # 当前时间小于9:50不检查
            if current_hour == 9 and current_minute < 50:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:is_match_strategy,"
                        f"当前时间{create_time}小于9:50，不检查买入条件2"
                    ),
                    "code": code,
                }
            # ---- 获取该股票的匹配支撑位 ----
            matched_support: float | None = self._matched_support.get(code)
            if matched_support is None:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:is_match_strategy,"
                        f"未找到股票 {code} 的匹配支撑位，请先执行盘前筛选"
                    ),
                    "code": code,
                }
            
            # --- 条件2：分时段检查（跌穿支撑位 + 从最低点反弹） ---
            result = self._check_buy_time_windows(
                code, current_price, low_price, matched_support, current_hour, current_minute
            )
            if result is not None:
                return result

            # 不在任何买入窗口内或条件未满足，继续等待
            return False, {
                "reason": (
                    f"{self.strategy_name},func:_check_buy_conditions,"
                    f"当前时间{create_time}不在买入窗口或条件未满足"
                ),
                "code": code,
                "current_price": current_price,
                "time": create_time,
            }

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_buy_conditions(%s) error: %s",
                self.strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

    def _check_buy_time_windows(
        self,
        code: str,
        current_price: float,
        low_price: float,
        matched_support: float,
        current_hour: int,
        current_minute: int,
    ) -> tuple[bool, dict[str, Any]] | None:
        """分时段检查买入条件：股价未跌穿支撑位且从最低点反弹。

        三个检查窗口：
        - 11:00-11:30: 跌幅 < -2% 且 股价 > 最低价
        - 13:30-14:00: 跌幅 < -2% 且 股价相比最低价涨幅 > 1%
        - 14:30-14:50: 跌幅 < -2% 且 股价相比最低价涨幅 > 2%

        :param code: 股票代码
        :param current_price: 当前价格
        :param low_price: 全天最低价
        :param matched_support: 匹配的支撑位
        :param current_hour: 当前小时
        :param current_minute: 当前分钟
        :return: (True, 详情) 触发买入; None 不在窗口或条件未满足
        """
        # 判断当前所属窗口及对应的反弹阈值
        in_window: bool = False
        rebound_threshold: float = 0.0
        window_label: str = ""

        if current_hour == 11 and 0 <= current_minute <= 30:
            in_window = True
            rebound_threshold = 0.0
            window_label = "11:00-11:30"
        elif current_hour == 13 and current_minute >= 30:
            in_window = True
            rebound_threshold = 1.0
            window_label = "13:30-14:00"
        elif current_hour == 14 and 30 <= current_minute <= 50:
            in_window = True
            rebound_threshold = 2.0
            window_label = "14:30-14:50"

        if not in_window:
            return None

        # 条件A: 股价未跌穿支撑位（相比支撑位跌幅 > -2%）
        gap_support_pct: float = (current_price - matched_support) / matched_support * 100
        if gap_support_pct <= -2.0:
            return None  # 已跌穿，不买入

        # 条件B: 股价相比最低价反弹超过阈值
        gap_low_pct: float = (current_price - low_price) / low_price * 100 if low_price > 0 else 0.0
        if gap_low_pct <= rebound_threshold:
            return None  # 反弹幅度不够，继续等待

        # 两条件同时满足 → 触发买入
        buy_reason: str = (
            f"买入: 窗口{window_label} 当前价{current_price:.2f}"
            f" 未跌穿支撑位{matched_support:.2f}(距支撑位{gap_support_pct:.2f}%)"
            f" 相比最低价{low_price:.2f}反弹{gap_low_pct:.2f}%"
        )
        return True, {
            "code": code,
            "current_price": current_price,
            "matched_support": matched_support,
            "low_price": low_price,
            "gap_support_pct": round(gap_support_pct, 2),
            "gap_low_pct": round(gap_low_pct, 2),
            "window": window_label,
            "reason": buy_reason,
            "signal": "BUY",
        }

    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """检查卖出条件是否满足（满足其一即触发）。

        条件：
        1. 今日股价突破阻力位：在阻力位附近，上下(1%的误差)
        2. 股价下跌跌穿支撑位: 股价 < 支撑位,并且相比支撑位跌幅 > 2%

        :param code: 股票代码
        :param position: 持仓信息，含 cost_price / buy_time / quantity 等字段
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
        triggered: list[str] = []
        extra: dict[str, Any] = {"code": code, "cost_price": cost_price}

        try:
            current_price: float = float(stock_data.get("price", 0))
            create_time: str = str(stock_data.get("create_time", ""))
            preclose: float = float(stock_data.get("preclose", 0))

            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:is_match_strategy,"
                        "当前价格异常"
                    ),
                    "code": code
                    }

            extra["current_price"] = current_price

            # 如果涨停或跌停则不卖出（距离涨停价 0.5% 以内视为涨停）
            if preclose > 0 and abs((current_price - preclose) / preclose * 100) >= 9.5:
                return False, {
                    "reason": (
                        f"{self.strategy_name},func:is_match_strategy,"
                        f"卖出条件不满足: 当前价{current_price:.2f}"
                        f" 接近涨停或跌停（昨收{preclose:.2f}），不操作"
                    ),
                    "code": code,
                    "current_price": current_price,
                }
            
            # ---- 获取该股票的支撑位和阻力位 ----
            matched_support: float | None = self._matched_support.get(code)
            resistances: list[float] = self._matched_resistances.get(code, [])

            # ---- 条件1：今日股价突破阻力位：在阻力位附近，上下(1%的误差) ----
            if resistances:
                for res in resistances:
                    # 大于阻力位 或 接近阻力位(1%误差内)
                    gap_from_res = abs(current_price - res) / res * 100
                    if gap_from_res <= self.SELL_RESISTANCE_TOLERANCE:
                        triggered.append(
                            f"条件1: 当前价{current_price:.2f}"
                            f" 接近阻力位{res:.2f}"
                            f" (差距{gap_from_res:.2f}% <= {self.SELL_RESISTANCE_TOLERANCE}%)"
                        )
                        break
            else:
                self.logger.debug(
                    "[%s] 股票 %s 无阻力位数据，跳过条件1",
                    self.strategy_name, code,
                )

            # ---- 条件2：股价下跌，跌穿支撑位: 股价 < 支撑位,并且相比支撑位跌幅 > 3%----
            if matched_support is not None and matched_support > 0:
                drop_pct: float = (
                    (matched_support - current_price) / matched_support * 100
                )
                if drop_pct >= self.SELL_SUPPORT_DROP_PCT:
                    triggered.append(
                        f"条件2: 当前价{current_price:.2f}"
                        f" < 支撑位{matched_support:.2f}"
                        f" 且跌幅{drop_pct:.2f}% 过大"
                    )

            if triggered:
                extra["signal"] = "SELL"
                extra["reason"] = f"卖出: {'; '.join(triggered)}"
                extra["reasons"] = triggered
                extra["matched_support"] = matched_support
                extra["resistances"] = resistances
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

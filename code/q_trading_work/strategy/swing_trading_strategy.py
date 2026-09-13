"""
Author: liguoqiang
Date: 2026-06-29
LastEditors: liguoqiang
LastEditTime: 2026-07-26
Description: 波段交易策略，适合震荡行情
    低频依照支撑位和阻力位提供交易策略，日K线数据用于计算支撑位和阻力位，分钟线数据用于判断当天买入卖出条件。
    选股条件：（is_match_strategy 适配）
        1. 昨日股价>支撑位并在支撑位(3%的误差)
        2. 昨日股价<任何一个阻力位
        3. 如果有多个支撑价位，靠近任何一个即可

    当天买入条件（全部满足）：
        1. 今日股价接近当天任何一个支撑位上下(0.05%的误差)
        2. 根据KlineSRFactor因子返回的分值判断

    卖出条件（满足其一）：
        1. 今日股价接近任何一个阻力位(0.05%的误差)
        2. 跌停不卖出
        3. 今日股价跌穿日行情的支撑位并且相比支撑位跌幅 > 2%
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pandas as pd

from app_context import AppContext
from factor.kline_support_resistance import KlineSRFactor
from factor.in_day_support_resistance import InDaySRFactor
from factor.boll_support_resistance import BollSRFactor
from strategy.base_strategy import BaseStrategy
from redis_db.redis_exec import RedisExec



class SwingTradingStrategy(BaseStrategy):
    """低频波段交易策略。

    买入条件（全部满足）：
        历史行情条件（is_match_strategy 中判断）
            1. KlineSRFactor 因子返回的分值 > 0.0，买入

        当天条件（_check_buy_conditions 中判断）
            2. 涨停不买入
            3. InDaySRFactor 因子返回的分值 > 0.0，买入

    卖出条件（满足其一）：
        1. InDaySRFactor 因子返回的分值 < 0.0，卖出
        2. 跌停不卖出
        3. KlineSRFactor 因子返回的分值 < 0.0，卖出
    """

    strategy_name: str = "低频波段交易策略"
    description: str = (
        "是用于网格交易模式，通过支撑价位和阻力价位来回交易，适合震荡行情"
    )

    # ---- 买入参数 ----
    BUY_SR_WINDOW: int = 5  # 寻找局部极值点的窗口大小

    # ---- 卖出参数 ----
    SELL_SR_WINDOW: int = 5  # 寻找局部极值点的窗口大小

    # ---- 支撑/阻力位计算参数 ----
    SR_WINDOW: int = 5  # 寻找局部极值点的窗口大小
    SR_TOLERANCE: float = 0.02  # 相近价位聚类合并的容差

    def __init__(
        self,
        user_strategy_id: str = "",
        strategy_id: str = "",
        strategy_type: str = "",
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        """初始化波段交易策略。"""
        # 必须调用基类初始化：设置 logger/factor_manager、加载策略参数并创建选股列表缓存字段
        super().__init__(
            user_strategy_id=user_strategy_id,
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            user_strategy_params=user_strategy_params,
            default_params=default_params,
        )
        self._break_max_r: dict[str, bool] = {} # 是否突破最大阻力位,对应每个股票代码
        self._inday_sr_points: dict[str, pd.DataFrame] = {}  # 当天支撑位和阻力位缓存，key=code, value={"s1":xx,"s2":xx,"r1":xx,"r2":xx}

    def apply_params(self, params: dict[str, Any]) -> None:
        """将参数字典映射到策略属性（扁平结构，key 与类属性名对应）。

        缺失的字段保留类属性默认值。

        :param params: 策略参数字典
        """
        if not isinstance(params, dict) or not params:
            return
        match_config: dict[str, Any] = params.get("match", {}) or {}
        buy_config: dict[str, Any] = params.get("buy", {}) or {}
        sell_config: dict[str, Any] = params.get("sell", {}) or {}
        self.BUY_SR_WINDOW = int(buy_config.get("buy_sr_window", self.BUY_SR_WINDOW))
        self.SELL_SR_WINDOW = int(sell_config.get("sell_sr_window", self.SELL_SR_WINDOW))
        self.SR_WINDOW = int(match_config.get("sr_window", self.SR_WINDOW))
        self.SR_TOLERANCE = float(match_config.get("sr_tolerance", self.SR_TOLERANCE))

    # ---- 基类抽象方法实现 ----

    def init_factors(self) -> None:
        """注册策略所需因子。

        本策略使用 KlineSRFactor 基于历史K线数据计算支撑位和阻力位。
        """
        pass

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        """判断历史日线数据是否满足策略买入信号（用于回测/选股）。

        stock_data 是历史行情，不含当天行情。
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
        match, reason = self._check_industry_pe(code)
        if not match:
            return False, reason
        ksr_factor = KlineSRFactor(self.SR_WINDOW, self.SR_TOLERANCE)  # 确保因子已注册
        score: float = ksr_factor.score(stock_df)
        if score > 0.0:
            return True, {"reason": "条件满足: KlineSRFactor 因子返回的分值满足买入条件", "score": score}
        if score == -10.0:
            self._break_max_r[code] = True
        return False, {"reason": "条件不满足: KlineSRFactor 因子返回的分值不满足买入条件"}

    def _check_industry_pe(self, code: str) -> tuple[bool, dict[str, Any]]:
        """只满足市盈率不要超过200, 不要亏损就行"""
        try:
            stock_info_list: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(codes=code)
            industry: str = ""
            if stock_info_list:
                info: dict[str, Any] = stock_info_list[0]
                industry = str(info.get("industry", info.get("industry_name", "")))
            # 不属于金融或者非银金融时才检查市盈率和利润率
            if "金融" not in industry and "非银金融" not in industry:
                passed, reason = self._match_pe_profit(code, 200, 0)
                if not passed:
                    return False, reason
        except Exception as exc:
            self.logger.warning("查询行业信息失败 code=%s: %s", code, exc, exc_info=True)
        return True, {}

    def handle_minute_bar(self, code: str, position: dict[str, Any], stock_data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """
        重载父类的此函数
        先执行买入判断，再执行卖出判断，目的是为了做T
        """
        self._before_minute_route(code, stock_data)
        # 如果昨日股价突破最大阻力位，今天则不进行买入，只判断卖出
        break_max = self._break_max_r.get(code, False)
        if not break_max:
            # 如果买入条件成立就直接返回
            match, extra = self._check_buy_conditions(code, stock_data)
            if match:
                return match, extra
        # 如果买入条件不成立，就判断卖出条件
        return self._check_sell_conditions(code, position, stock_data)

    def _before_minute_route(self, code: str, stock_data: dict[str, Any]) -> None:
        """分钟路由前保存分钟行情缓存。"""
        self._save_minute_cache(code, stock_data)

    def before_trading(self, trade_date: str, stock_codes: list[str], **kwargs: Any) -> None:
        """
        每天交易前的准备工作，清理分钟行情缓存。
        此处做前一天的经典枢轴点计算（采用IndaySRFactor)，供当天交易使用。
        """
        #先调用父类进行每天的清理工作
        super().before_trading(trade_date, stock_codes, **kwargs)
        self._break_max_r.clear()
        self._inday_sr_points.clear()
        #计算买入点的支撑位和阻力位，供当天交易使用
        for code in stock_codes:
            sr_df: pd.DataFrame = self._get_code_prev_sr_point(code, trade_date)
            self._save_sr_points(code, sr_df)

    def _get_code_prev_sr_point(self, code: str, trade_date: str) -> pd.DataFrame:
        inday_sr_factor = InDaySRFactor()  # 确保因子已注册
        stock_df = self.load_his_daily_data(code, trade_date, inday_sr_factor.days)  # 计算当天支撑位和阻力位，确保因子已注册
        return inday_sr_factor.calculate(stock_df)
    
    def _save_sr_points(self, code: str, sr_df: pd.DataFrame) -> None:
        if sr_df.empty:
            return
        if self._redis_enabled:
            redis_exec: RedisExec = AppContext().redis_exec
            key: str = f"{self.strategy_name}:sr_points:{code}"
            value: str = sr_df.to_json(orient="records", date_format="iso")
            if redis_exec.set_key_string(key, value, ex=self.MINUTE_CACHE_TTL):
                return
        self._inday_sr_points[code] = sr_df

    def _get_sr_points(self, code: str) -> pd.DataFrame:
        df: pd.DataFrame = pd.DataFrame()
        if self._redis_enabled:
            redis_exec: RedisExec = AppContext().redis_exec
            key: str = f"{self.strategy_name}:sr_points:{code}"
            value: str|None = redis_exec.get_key_string(key)
            if value:
                df = pd.DataFrame.from_records(json.loads(value))
                if not df.empty:
                    return df
        df = self._inday_sr_points.get(code, pd.DataFrame())
        if df.empty:
            trade_day = self.get_recent_trading_day()
            df = self._get_code_prev_sr_point(code, trade_day)
            self._save_sr_points(code, df)
        return df
    # ---- 内部方法 ----

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """检查当天买入条件。

        条件：
            根据InDaySRFactor因子返回的分值判断，分值>0.0买入
            同时增加KlineSRFactor因子返回的分值判断，分值>0.0买入

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
                
            # ---- 条件1：9:40 前不判断，避免干扰 ----
            if current_hour == 9 and current_minute < 40:
                return False, {
                    "reason": (
                        f"{self.strategy_name}, 9:40前不判断买入条件，当前时间{create_time}"
                    ),
                    "code": code,
                    "open": open_price,
                    "preclose": preclose,
                }

            # 如果涨停或跌停则不买入（距离涨停价 0.5% 以内视为涨停）
            if preclose > 0 and abs((current_price - preclose) / preclose * 100) >= 9.5:
                return False, {
                    "reason": (
                        f"{self.strategy_name}"
                        f"买入条件不满足: 当前价{current_price:.2f}"
                        f" 接近涨停或跌停s（昨收{preclose:.2f}），不买入"
                    ),
                    "code": code,
                    "current_price": current_price,
                }

            
            minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
            stock_df = pd.DataFrame(minute_cache)
            inday_sr_factor = InDaySRFactor()  # 确保因子已注册
            sr_df: pd.DataFrame = self._get_sr_points(code)
            inday_score: float = inday_sr_factor.score_with_sr(sr_df, stock_df)  # 计算当天支撑位和阻力位
            boll_sr_factor = BollSRFactor(timeperiod=self.BUY_SR_WINDOW)  # 确保因子已注册
            boll_score: float = boll_sr_factor.score(stock_df)  # 计算布林带支撑位和阻力位
            raise_score: int = 0
            if inday_score > 0.0:
                raise_score += 1
            if boll_score > 0.0:
                raise_score += 1
            if raise_score >= 2:
                return True, {
                    "reason": (
                        f"条件满足: 因子返回的分值满足买入条件, 枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f}"
                    ),
                    "signal": "BUY",
                    "code": code,
                    "score": inday_score + boll_score,
                    "current_price": current_price,
                    "create_time": create_time,
                }
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


    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """检查卖出条件是否满足（满足其一即触发）。

        卖出条件：
        1. InDaySRFactor 因子返回的分值 < 0.0，卖出
        2. 跌停不卖出
        3. 布林带辅助

        :param code: 股票代码
        :param position: 持仓信息，含 cost_price / buy_time / quantity 等字段
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
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
            # 
            minute_cache: list[dict[str, Any]] = self._get_minute_cache(code)
            stock_df = pd.DataFrame(minute_cache)
            inday_sr_factor = InDaySRFactor()  # 确保因子已注册
            sr_df: pd.DataFrame = self._get_sr_points(code)
            inday_score: float = inday_sr_factor.score_with_sr(sr_df, stock_df)  # 计算当天支撑位和阻力位
            boll_sr_factor = BollSRFactor(timeperiod=self.SELL_SR_WINDOW)  # 确保因子已注册
            boll_score: float = boll_sr_factor.score(stock_df)  # 计算布林带支撑位和阻力位
            raise_score: int = 0
            if inday_score < 0.0:
                raise_score += 1
            if boll_score < 0.0:
                raise_score += 1
            if raise_score >= 2.0:
                extra["signal"] = "SELL"
                extra["reason"] = (
                    f"条件满足: 因子返回的分值满足卖出条件, 枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f}"
                ),
                extra["current_price"] = current_price
                extra["score"] = inday_score + boll_score
                extra["create_time"] = create_time
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}


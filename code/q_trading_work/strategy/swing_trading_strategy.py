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

    strategy_name: str = "波段交易策略"
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
        user_strategy_name: str = "",
        strategy_id: str = "",
        strategy_type: str = "",
        user_strategy_params: dict[str, Any] | None = None,
        default_params: dict[str, Any] | None = None,
    ) -> None:
        """初始化波段交易策略。

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

        **与父类的差异（有意为之，2026-09-28 确认）**：父类 `handle_minute_bar`
        的买入闸门是「在当日选股列表中 且 无持仓」，这里改为「昨日未突破最大阻力位」
        （`KlineSRFactor.score == -10.0` 时置位 `_break_max_r`），即**不依赖当日选股
        结果**。本策略的定位是「在股票池上检测波段做 T 的机会」，把整个池当交易域，
        用盘中的支撑/阻力位触发，以扩大波段信号的覆盖面。因此：

        - 实盘（workflow）对**整个股票池**派发行情，池内每只股票每分钟都会跑一次
          买入判定；
        - 回测只有 `codes_today = matched_candidates + held` 会跑到这里。

        两条链路的信号集合因此**不可直接比较**，回测结果只代表「当日候选股 + 持仓」
        这一子集。卖出侧同理：实时链路恒传空持仓，未持仓股票也会走一遍卖出判定。
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
        """分钟路由前钩子。

        注意：分钟行情缓存已改为由 `StrategyWorkflow` 单点写入 UserStrategyCache
        （每个 code 只写一次），策略侧只读，因此这里不再写入。
        """
        return

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
        """计算当天（基于前一日日线）的经典枢轴支撑/阻力位。

        **返回值统一为 DataFrame**：`InDaySRFactor.calculate` 在日线数据不足
        （新股、停牌复牌、数据缺口）时返回的是浮点 `0.0` 而不是 DataFrame，
        若原样返回，调用方 `_save_sr_points` 会在 `sr_df.empty` 处抛
        `AttributeError: 'float' object has no attribute 'empty'`；
        而 `_save_sr_points` 又是在 `before_trading` 的股票池循环里被调用，
        单只股票数据不足就会中断整个池子的盘前准备。
        因此这里收敛为「拿不到点位就返回空表」，由下游按「无点位」处理。

        :param code: 股票代码
        :param trade_date: 交易日 YYYY-MM-DD
        :return: 枢轴点位表；数据不足时返回空 DataFrame
        """
        inday_sr_factor = InDaySRFactor()  # 确保因子已注册
        # 计算当天支撑位和阻力位，确保因子已注册
        stock_df: pd.DataFrame = self.load_his_daily_data(
            code, trade_date, inday_sr_factor.days
        )
        sr_df: Any = inday_sr_factor.calculate(stock_df)
        if not isinstance(sr_df, pd.DataFrame):
            self.logger.warning(
                "[%s] code=%s 日线数据不足（%s），无法计算当天支撑/阻力位，跳过",
                self.strategy_name, code, trade_date,
            )
            return pd.DataFrame()
        return sr_df
    
    def _sr_points_key(self, code: str) -> str:
        """当天支撑/阻力位缓存 key。

        key 必须包含 user_strategy_id：per-user 策略实例各写一份，若只用
        `策略名:代码` 做 key，多用户会并发互相覆盖（原 P1-9 遗留问题）。
        用户策略 ID 为空（如回测）时退化为 `default`，保持单实例场景可用。

        :param code: 股票代码
        :return: Redis key
        """
        owner: str = self.user_strategy_id or "default"
        return f"{self.strategy_name}:sr_points:{owner}:{code}"

    def _save_sr_points(self, code: str, sr_df: pd.DataFrame) -> None:
        if sr_df.empty:
            return
        if self._cache.redis_enabled:
            redis_exec: RedisExec = AppContext().redis_exec
            value: str = sr_df.to_json(orient="records", date_format="iso")
            if redis_exec.set_key_string(self._sr_points_key(code), value, ex=self._cache.MINUTE_CACHE_TTL):
                return
        self._inday_sr_points[code] = sr_df

    def _get_sr_points(self, code: str) -> pd.DataFrame:
        if self._cache.redis_enabled:
            redis_exec: RedisExec = AppContext().redis_exec
            value: str|None = redis_exec.get_key_string(self._sr_points_key(code))
            if value:
                df: pd.DataFrame = pd.DataFrame.from_records(json.loads(value))
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
            stock_name: str = str(stock_data.get("name", "") or "")
            create_time: str = str(stock_data.get("create_time", ""))

            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name},func:_check_buy_conditions,"
                        "当前价格异常"
                    ),
                    "code": code
                }
            # 解析当前分钟时间
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_buy_conditions,"
                        f"无法解析分钟时间: {create_time}"
                    ),
                    "code": code,
                }
            current_hour: int = minute_dt.hour
            current_minute: int = minute_dt.minute
                
            # ---- 避开 9:40以前和 13--13:10时间，避免突然开盘信息干扰 ----
            if (current_hour == 9 and current_minute <= 40) or (current_hour == 13 and current_minute <= 10):
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 9:40或者13:10前不判断买入条件，过滤干扰"
                    ),
                    "code": code,
                    "open": open_price,
                    "preclose": preclose,
                }

            chg_percent = self._change_percent(stock_data, current_price, preclose)
            # 涨停或跌停附近不买入（类文档「涨停不买入」，同时保留原先对跌停的拦截：
            # 原实现用 abs(...) >= 9.5 两个方向都挡，这里只把阈值改成按板块推算）。
            # ST 5%、创业板/科创板 20%、其余 10%，统一留 1 个百分点余量；原实现硬编码
            # 9.5% 会把创业板 −9.5%（远未跌停）一并挡掉，而 ST 封板时又拦不住。
            near_limit: float = self._near_price_limit_pct(code, stock_name)
            if preclose > 0 and abs(chg_percent) >= near_limit:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}"
                        f" 接近涨停或跌停,涨跌幅{chg_percent:.2f}%（阈值±{near_limit:.1f}%），不买入"
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
            avg_score: float = (inday_score + boll_score) / 2
            star: float = avg_score / 2
            if avg_score > 0:
                return True, {
                    "reason": (
                        f"条件满足: 枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f}, 平均分值:{avg_score:.2f}"
                    ),
                    "signal": "BUY",
                    "code": code,
                    "score": round(avg_score, 2),
                    "star": round(star, 1),
                    "current_price": round(current_price, 2),
                    "create_time": create_time,
                }
            return False, {
                "reason": (
                    f"当前时间{create_time}条件未满足,枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f}, 平均分值:{avg_score:.2f}"
                ),
                "code": code,
                "current_price": round(current_price, 2),
                "time": create_time,
            }

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_buy_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
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
                        f"{self.user_strategy_name},_check_sell_conditions, 当前价格异常"
                    ),
                    "code": code
                    }
            # 解析当前分钟时间
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_buy_conditions,"
                        f"无法解析分钟时间: {create_time}"
                    ),
                    "code": code,
                }
            current_hour: int = minute_dt.hour
            current_minute: int = minute_dt.minute
                
            # ---- 卖出只避开 9:35之前，避免突然开盘信息干扰 ----
            if current_hour == 9 and current_minute <= 35:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 9:35前不判断买入条件，过滤干扰"
                    ),
                    "code": code,
                }
                
            extra["current_price"] = current_price
            stock_name: str = str(stock_data.get("name", "") or "")
            chg_percent = self._change_percent(stock_data, current_price, preclose)
            # 涨停或跌停附近不卖出（类文档「跌停不卖出」，同时保留原先对涨停的拦截）。
            # 阈值同样按该股票的涨跌停幅度推算（留 1 个百分点余量）：原实现硬编码 9.5%，
            # ST 封在 ±5% 时仍会去挂卖单，创业板 ±9.5% 却又不卖。
            near_limit: float = self._near_price_limit_pct(code, stock_name)
            if preclose > 0 and abs(chg_percent) >= near_limit:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name},"
                        f"卖出条件不满足: 接近涨停或跌停,涨跌幅{chg_percent:.2f}%（阈值±{near_limit:.1f}%），不操作"
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
            avg_score: float = (inday_score + boll_score) / 2
            star: float = -1.0 * avg_score / 2
            if avg_score < 0:
                extra["signal"] = "SELL"
                extra["reason"] = (
                    f"条件满足: 枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f},平均分值:{avg_score:.2f}"
                )
                extra["current_price"] = round(current_price, 2)
                extra["score"] = round(avg_score, 2)
                extra["star"] = round(star, 1)
                extra["create_time"] = create_time
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}


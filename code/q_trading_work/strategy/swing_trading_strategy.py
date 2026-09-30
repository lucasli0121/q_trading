"""
Author: liguoqiang
Date: 2026-06-29
LastEditors: liguoqiang
LastEditTime: 2026-07-26
Description: 波段交易策略，适合震荡行情
    低频依照支撑位和阻力位提供交易策略，日K线数据用于计算支撑位和阻力位，分钟线数据用于判断当天买入卖出条件。

    选股条件（is_match_strategy，回测链路走这里）：
        0. 日线不构成下跌趋势（ADX 达门槛且 −DI > +DI 时不选）
        1. 市盈率 / 利润增长率通过（金融业与非银金融豁免）
        2. KlineSRFactor 打分 > 0

    当天买入条件（_check_buy_conditions）：
        1. 不在 9:40 前 / 13:10 前（过滤开盘干扰），不在涨跌停附近
        2. (InDaySRFactor 枢轴点分 + BollSRFactor 布林带分) / 2 > 0
        前置闸门：日线趋势向下的股票当天不做买入判定（2026-09-29 新增）

    当天卖出条件（_check_sell_conditions）：
        1. 位置分为负（同上两个因子等权平均），且不在涨跌停附近
        2. 持仓回撤达 sell.drawdown_pct（默认 -6.0%）—— 计入卖出分值
        3. 账户回撤达 risk.max_drawdown_pct（默认 -10.0%）—— 计入卖出分值

    卖出判定**不受**趋势闸门影响：下跌趋势中的持仓恰恰要靠支撑位跌破与回撤
    止损离场，把卖出一并关掉等于让持仓失去保护。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pandas as pd

from app_context import AppContext
from factor.adx_trend_factor import AdxTrendFactor
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

    # 本策略重写了 `handle_minute_bar`，买入按全池行情自行判定（做 T 的信号覆盖率
    # 是有意如此），不读当日选股结果 ⇒ 盘中改股票池后不需要补跑选股。
    requires_daily_selection: bool = False

    # ---- 买入参数 ----
    # 以下默认值与 cfg/strategy.yaml[swing_trading_strategy] 保持一致：
    # 无参数实例化走 yaml，两边分叉时「按源码默认值推断实际生效参数」会出错。
    BUY_SR_WINDOW: int = 10  # 寻找局部极值点的窗口大小

    # ---- 卖出参数 ----
    SELL_SR_WINDOW: int = 10  # 寻找局部极值点的窗口大小

    # ---- 支撑/阻力位计算参数 ----
    SR_WINDOW: int = 10  # 寻找局部极值点的窗口大小
    SR_TOLERANCE: float = 0.02  # 相近价位聚类合并的容差

    # ---- 趋势过滤参数（match）----
    # 本策略的定位是震荡行情：下跌趋势里阻力与支撑会被逐个跌穿，「贴近支撑位」
    # 不再等于「可买」（2026-09-29 用户要求）。因此在买入侧加一道日线趋势闸门：
    # ADX 达强度门槛 且 −DI > +DI ⇒ 判为下跌趋势，当天该股只做卖出不做买入。
    #
    # 必须同时要求 ADX 达门槛：「+DI > −DI」在震荡市里频繁交叉，只看方向会把
    # 一半的股票随机剔掉，反而违背「震荡市才用本策略」的定位。ADX 数据不足
    # （次新股、停牌复牌）时其值为 NaN，比较恒为 False ⇒ 判不出下跌趋势 ⇒
    # 不做过滤，这是有意的 fail-open（宁可多产信号，也不因数据缺口误禁用）。
    TREND_DAYS_LIMIT: int = 14  # ADX 周期（趋势判定窗口）
    TREND_ADX_THRESHOLD: float = 25.0  # ADX 强度门槛：达到才算「趋势确立」
    TREND_KLINE_DAYS: int = 40  # 趋势判定所需日K根数（ADX lookback = 2×周期）

    # ---- 卖出回撤参数（sell）----
    # 自持仓最高点回撤达阈值时，把 `SELL_DRAWDOWN_SCORE` 直接叠加到卖出分上
    # （不是与两个位置因子做平均）：止损要的是「果断退出」，一条回撤理由足够
    # 单独触发卖出，不该被「枢轴点=0、布林带=0」的中间态稀释掉。
    SELL_DRAWDOWN_PCT: float = -6.0  # 自持仓最高点回撤阈值（%）
    SELL_DRAWDOWN_SCORE: float = -6.0  # 触发回撤阈值时计入的卖出分值（10 分制）

    # ---- 风控参数（risk）----
    # 账户级回撤阈值（%）：账户已实现权益自峰值回撤达此值时，在卖出判定中计入
    # `drawdown_score` 分值（**不冻结开新仓**）；0 或正数表示关闭。
    # 口径与成交量策略、`BaseStrategy.RISK_MAX_DRAWDOWN_PCT` 一致：策略自己拿不到
    # 账户权益，回撤由 workflow / 回测在调用策略前注入（`set_account_drawdown`）。
    RISK_MAX_DRAWDOWN_PCT: float = -10.0
    SELL_RISK_DRAWDOWN_SCORE: float = -6.0

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
        # 必须在 `super().__init__()` **之前**建好：基类构造会回调子类的
        # `apply_params()`，子类若在其中读到尚未赋值的实例属性就抛 AttributeError，
        # 被 workflow 的 `except Exception` 兜住后表现为「整个用户策略静默不加载」
        # （不建仓、不发信号、不卖，日志里只有一条 ERROR）。
        self._downtrend_codes: set[str] = set()  # 日线趋势向下、当天跳过买入判定的股票
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
        """将参数字典映射到策略属性（结构：`{match: {...}, buy: {...}, sell: {...}, risk: {...}}`）。

        缺失的字段保留类属性默认值。

        :param params: 策略参数字典
        """
        if not isinstance(params, dict) or not params:
            return
        match_config: dict[str, Any] = params.get("match", {}) or {}
        buy_config: dict[str, Any] = params.get("buy", {}) or {}
        sell_config: dict[str, Any] = params.get("sell", {}) or {}
        risk_config: dict[str, Any] = params.get("risk", {}) or {}
        self.BUY_SR_WINDOW = int(buy_config.get("buy_sr_window", self.BUY_SR_WINDOW))
        self.SELL_SR_WINDOW = int(sell_config.get("sell_sr_window", self.SELL_SR_WINDOW))
        self.SR_WINDOW = int(match_config.get("sr_window", self.SR_WINDOW))
        self.SR_TOLERANCE = float(match_config.get("sr_tolerance", self.SR_TOLERANCE))
        # 趋势过滤（买入侧闸门）
        self.TREND_DAYS_LIMIT = int(
            match_config.get("trend_days_limit", self.TREND_DAYS_LIMIT)
        )
        self.TREND_ADX_THRESHOLD = float(
            match_config.get("trend_adx_threshold", self.TREND_ADX_THRESHOLD)
        )
        self.TREND_KLINE_DAYS = int(
            match_config.get("trend_kline_days", self.TREND_KLINE_DAYS)
        )
        # 卖出侧回撤
        self.SELL_DRAWDOWN_PCT = float(
            sell_config.get("drawdown_pct", self.SELL_DRAWDOWN_PCT)
        )
        self.SELL_DRAWDOWN_SCORE = float(
            sell_config.get("drawdown_score", self.SELL_DRAWDOWN_SCORE)
        )
        # 账户级回撤（risk 节）；回撤值本身由 workflow / 回测每轮注入，这里只读配置
        self.RISK_MAX_DRAWDOWN_PCT = float(
            risk_config.get("max_drawdown_pct", self.RISK_MAX_DRAWDOWN_PCT)
        )
        self.SELL_RISK_DRAWDOWN_SCORE = float(
            risk_config.get("drawdown_score", self.SELL_RISK_DRAWDOWN_SCORE)
        )

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

        条件（全部满足）：
            0. 日线不构成下跌趋势（ADX 达门槛且 −DI > +DI 时判为下跌，直接不选）
            1. 市盈率/利润增长率通过（金融业与非银金融豁免）
            2. KlineSRFactor 打分 > 0

        条件 0 与盘中买入闸门（`handle_minute_bar` 读 `_downtrend_codes`）用同一个
        `_is_downtrend`：选股链路（回测走的就是这里）与盘中链路若只在一侧过滤，
        回测选出的股票集合会与实盘分叉，而两边日志都「看起来正常」。

        :param stock_data: 股票日线行情数据列表，每项含 close/open/high/low 等字段
        :return: (是否匹配, 详细信息字典)
        """
        if not stock_data or len(stock_data) < self.SR_WINDOW:
            return False, {"reason": f"{self.strategy_name},func:is_match_strategy,数据不足，需至少{self.SR_WINDOW}个交易日"}

        stock_df: pd.DataFrame = pd.DataFrame(stock_data)
        code: str = str(stock_df.iloc[0].get("code", ""))

        if not code:
            return False, {"reason": "条件1不满足: 无法获取股票代码"}

        # ---- 条件0：日线趋势向下不选（与盘中买入判定同一口径）----
        # 放在市盈率查询之前：趋势是本地计算，PE 要打一次 HTTP，先过滤掉下跌趋势的
        # 股票能省下这部分请求。
        detail: dict[str, Any] = self._adx_detail(stock_df)
        if self._is_downtrend(detail):
            return False, {
                "reason": (
                    f"{self.strategy_name}, 日线趋势向下（ADX {float(detail.get('adx') or 0.0):.2f}"
                    f" >= {self.TREND_ADX_THRESHOLD:.2f}"
                    f"，-DI {float(detail.get('-DI') or 0.0):.2f}"
                    f" > +DI {float(detail.get('+DI') or 0.0):.2f}），波段策略不参与"
                )
            }

        # ---- 条件1：市盈率<300, 利润增长率>10%, 此项检查不包含：金融业和非银金融行业 ----
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

        **趋势闸门**（2026-09-29 新增）：日线趋势向下的股票当天不产生**买入**信号
        （`before_trading` 判定的 `_downtrend_codes`）；**卖出与风控判定照常执行**。
        下跌趋势里支撑位会被逐个跌穿，贴着支撑位买入等于逆势接刀，而持仓此时
        恰恰最需要靠支撑位跌破与回撤止损离场。
        """
        self._before_minute_route(code, stock_data)
        # 两种情况下不做买入判定、只判断卖出：
        # ① 日线趋势向下（见类属性的趋势过滤说明）；
        # ② 昨日股价突破最大阻力位（原逻辑）。
        downtrend: bool = code in self._downtrend_codes
        break_max = self._break_max_r.get(code, False)
        if not downtrend and not break_max:
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
        """每天交易前的准备工作：清理缓存、算当天枢轴点位、判定日线趋势方向。

        日K只取一次（`_trend_kline_days()` 根），同时喂给枢轴点计算与趋势判定：
        两者需要的根数不同（枢轴点只要前一交易日那 1 根，趋势判定要 2×ADX 周期），
        分两次取数会让盘前多打一轮 HTTP，且同一只股票在两条链路里可能拿到不同快照。

        趋势向下的股票记进 `_downtrend_codes`，当天买入判定直接跳过（见
        `handle_minute_bar`）；卖出与风控判定照常 —— 否则下跌趋势中的持仓会同时
        失去支撑位卖出与回撤止损，而那正是最需要离场的时候。
        """
        #先调用父类进行每天的清理工作
        super().before_trading(trade_date, stock_codes, **kwargs)
        self._break_max_r.clear()
        self._inday_sr_points.clear()
        self._downtrend_codes.clear()
        #计算买入点的支撑位和阻力位，供当天交易使用
        for code in stock_codes:
            klines: pd.DataFrame = self.load_his_daily_data(
                code, trade_date, self._trend_kline_days()
            )
            self._save_sr_points(code, self._build_sr_points(code, trade_date, klines))
            detail: dict[str, Any] = self._adx_detail(klines)
            if self._is_downtrend(detail):
                self._downtrend_codes.add(code)
                # 必须记 INFO：`cfg/log.yaml` 的 info.log 是 INFO 级，这条是
                # 「为什么这只股票今天没买入信号」的唯一解释，记 DEBUG 等于运维看不见
                self.logger.info(
                    "[%s] code=%s 日线趋势向下（ADX %.2f >= %.2f，-DI %.2f > +DI %.2f），"
                    "当天跳过买入判定，卖出与风控照常",
                    self.strategy_name, code,
                    float(detail.get("adx") or 0.0), self.TREND_ADX_THRESHOLD,
                    float(detail.get("-DI") or 0.0), float(detail.get("+DI") or 0.0),
                )

    def _trend_kline_days(self) -> int:
        """趋势判定与枢轴点计算共用的日K取数根数。

        ADX 的 lookback 是 `2×period`（talib 口径），少一根末值就是 NaN，
        而 `NaN > 阈值` 恒为 False ⇒ 判不出下跌趋势 ⇒ 趋势闸门静默失效，
        因此这里显式取够并留 1 根余量。
        """
        return max(
            int(self.TREND_KLINE_DAYS),
            2 * int(self.TREND_DAYS_LIMIT) + 1,
            InDaySRFactor().days,
        )

    def _adx_detail(self, klines: pd.DataFrame) -> dict[str, Any]:
        """日线 ADX 明细（方向 + 强度）；数据不足或计算异常时返回空字典。

        返回空字典表示「判不出趋势」，调用方按「不构成下跌趋势」处理 ——
        数据缺口不该被当成下跌信号把股票整个禁掉。

        :param klines: 日线行情（含 high/low/close，按日期升序）
        :return: 含 trend / level / adx / +DI / -DI 的字典；算不出来时为空字典
        """
        if klines is None or len(klines) < 2 * int(self.TREND_DAYS_LIMIT):
            return {}
        try:
            return AdxTrendFactor(days=self.TREND_DAYS_LIMIT).adx_trend(
                klines,
                period=int(self.TREND_DAYS_LIMIT),
                threshold=float(self.TREND_ADX_THRESHOLD),
            )
        except Exception as exc:
            self.logger.warning("[%s] ADX 趋势计算失败: %s", self.strategy_name, exc)
            return {}

    def _is_downtrend(self, detail: dict[str, Any]) -> bool:
        """是否构成「下跌趋势」：ADX 达强度门槛 且 −DI > +DI。

        两个条件缺一不可：
        - 只看 `−DI > +DI`：震荡市里 DI 频繁交叉，会随机剔掉一半股票，
          与本策略「只在震荡市用」的定位相悖；
        - 只看 `ADX > 门槛`：只说明「有趋势」（上涨趋势正是本策略想参与的突破行情）。

        ADX / DI 为 NaN（数据不足）时比较恒为 False，判不出下跌趋势 ⇒ 不过滤。

        :param detail: `_adx_detail` 的返回值（可为空字典）
        :return: True 表示日线下跌趋势成立，当天跳过买入判定
        """
        adx: float = float(detail.get("adx") or 0.0)
        if pd.isna(adx) or adx < float(self.TREND_ADX_THRESHOLD):
            return False
        plus_di: float = float(detail.get("+DI") or 0.0)
        minus_di: float = float(detail.get("-DI") or 0.0)
        if pd.isna(plus_di) or pd.isna(minus_di):
            return False
        return minus_di > plus_di

    def _build_sr_points(
        self, code: str, trade_date: str, klines: pd.DataFrame
    ) -> pd.DataFrame:
        """从日K取数结果构造「前一交易日」的枢轴点位表；拿不到点位返回空表。

        **返回值统一为 DataFrame**：`InDaySRFactor.calculate` 在日线数据不足
        （新股、停牌复牌、数据缺口）时返回的是浮点 `0.0` 而不是 DataFrame，
        若原样返回，调用方 `_save_sr_points` 会在 `sr_df.empty` 处抛
        `AttributeError: 'float' object has no attribute 'empty'`；
        而 `_save_sr_points` 又是在 `before_trading` 的股票池循环里被调用，
        单只股票数据不足就会中断整个池子的盘前准备。
        因此这里收敛为「拿不到点位就返回空表」，由下游按「无点位」处理。

        :param code: 股票代码
        :param trade_date: 交易日 YYYY-MM-DD
        :param klines: `load_his_daily_data` 的取数结果（按日期升序）
        :return: 枢轴点位表；数据不足时返回空 DataFrame
        """
        inday_sr_factor = InDaySRFactor()  # 确保因子已注册
        stock_df: pd.DataFrame = klines if klines is not None else pd.DataFrame()
        sr_df: Any = (
            inday_sr_factor.calculate(stock_df.tail(1)) if not stock_df.empty else 0.0
        )
        if not isinstance(sr_df, pd.DataFrame):
            self.logger.warning(
                "[%s] code=%s 日线数据不足（%s），无法计算当天支撑/阻力位，跳过",
                self.strategy_name, code, trade_date,
            )
            return pd.DataFrame()
        return sr_df

    def _get_code_prev_sr_point(self, code: str, trade_date: str) -> pd.DataFrame:
        """计算当天（基于前一日日线）的经典枢轴支撑/阻力位。

        `_get_sr_points` 缓存未命中时的兜底路径。取数根数用 `_trend_kline_days()`
        与 `before_trading` 对齐：两者取到的「最后一根」必须是同一个交易日
        （`load_his_daily_data` 的 end 恒为前一交易日），否则盘前算的枢轴点与
        盘中兜底算的会来自不同快照，点位随调用路径漂移。

        :param code: 股票代码
        :param trade_date: 交易日 YYYY-MM-DD
        :return: 枢轴点位表；数据不足时返回空 DataFrame
        """
        klines: pd.DataFrame = self.load_his_daily_data(
            code, trade_date, self._trend_kline_days()
        )
        return self._build_sr_points(code, trade_date, klines)
    
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
        """检查卖出条件是否满足。

        卖出分 = 位置分 + 两条回撤分项（回撤分项只在触发时叠加）：
            1. 位置分：(枢轴点分 + 布林带分) / 2，为负（< 0.0）时触发
            2. 持仓回撤：自持仓最高点回撤超过 `SELL_DRAWDOWN_PCT` 时计入
               `SELL_DRAWDOWN_SCORE`（10 分制负值，默认 -6.0，单独一条即可触发）
            3. 账户回撤：账户权益自峰值回撤达 `RISK_MAX_DRAWDOWN_PCT` 时计入
               `SELL_RISK_DRAWDOWN_SCORE`
        前置闸门：涨跌停附近不卖出（封板时挂单没有意义）。

        :param code: 股票代码
        :param position: 持仓信息，含 cost_price / buy_time / quantity 等字段
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
        extra: dict[str, Any] = {"code": code, "cost_price": cost_price}
        # 有持仓取真实买入日；实时链路恒传空持仓（设计约定 D-1），退化为
        # 「最近 2 个交易日」—— 与成交量策略同口径（见基类 `_sell_baseline_buy_time`）
        buy_time: str = self._sell_baseline_buy_time(
            position, str(stock_data.get("create_time", ""))
        )

        try:
            current_price: float = float(stock_data.get("price", 0))
            create_time: str = str(stock_data.get("create_time", ""))
            preclose: float = float(stock_data.get("preclose", 0))
            day_high: float = float(stock_data.get("high", 0))

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
            # 位置分：枢轴点与布林带等权平均。文案里的「平均分值」就是它，保持原样。
            avg_score: float = (inday_score + boll_score) / 2

            # ---- 回撤止损：独立负分项，直接叠加在位置分上（不做平均）----
            # 止损要的是「果断退出」：一条回撤理由（-6.0）就应足够单独触发卖出，
            # 不该被「枢轴点=0、布林带=0」的中间态稀释掉。未触发回撤时
            # final_score 恒等于 avg_score，卖出行为与加回撤前完全一致。
            final_score: float = avg_score
            dd_parts: list[str] = []
            highest_price: float = self._resolve_highest_price(
                code, buy_time, create_time, day_high, cost_price
            )
            if highest_price > 0:
                drawdown: float = (current_price - highest_price) / highest_price * 100
                extra["highest_price"] = round(highest_price, 2)
                extra["drawdown"] = round(drawdown, 2)
                if drawdown < self.SELL_DRAWDOWN_PCT:
                    final_score += self.SELL_DRAWDOWN_SCORE
                    dd_parts.append(
                        f"持仓回撤{drawdown:.2f}%(最高{highest_price:.2f}→"
                        f"{current_price:.2f}，阈值{self.SELL_DRAWDOWN_PCT:.2f}%，"
                        f"扣{self.SELL_DRAWDOWN_SCORE:.2f}分)"
                    )
            # 账户级回撤：账户权益自峰值回撤达阈值时分值入账（阈值 0/正数表示关闭）。
            # 这是账户级条件，对被检查的每只股票都成立（回撤超限时整体降仓）；
            # 回撤值由 workflow / 回测在调用策略前注入（见基类 `set_account_drawdown`）。
            account_dd: float = float(self.account_drawdown_pct or 0.0)
            extra["account_drawdown"] = round(account_dd, 2)
            risk_score: float = self._risk_drawdown_score()
            if risk_score < 0:
                final_score += risk_score
                dd_parts.append(
                    f"账户回撤{account_dd:.2f}%(阈值{self.RISK_MAX_DRAWDOWN_PCT:.2f}%，"
                    f"扣{risk_score:.2f}分)"
                )

            if final_score < 0:
                extra["signal"] = "SELL"
                reason: str = (
                    f"条件满足: 枢轴点分值:{inday_score:.2f}, 布林带分值:{boll_score:.2f},"
                    f"平均分值:{avg_score:.2f}"
                )
                # 回撤段只在触发时追加：未触发时逐字保持原有文案，避免下游按固定
                # 格式对账时被多出来的字段打乱（分隔符照用户样例，不统一改写）。
                if dd_parts:
                    reason += "，" + "；".join(dd_parts)
                extra["reason"] = reason
                extra["current_price"] = round(current_price, 2)
                extra["score"] = round(final_score, 2)
                extra["star"] = round(-1.0 * final_score / 2, 1)
                extra["create_time"] = create_time
                return True, extra

            return False, extra

        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}


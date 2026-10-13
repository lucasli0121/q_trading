"""成交量放大策略。

以 `VolumeExpansionFactor`（成交量放大因子，10 分制）为主因子给出买卖信号，
并用一个**搭配因子**做确认。

## 一、信号构成

历史日线条件（`is_match_strategy`，用于盘前选股）
    1. 成交量放大因子打分达标（`match.volume_score`，默认 4.0 = 因子的 BUY 阈值）
    2. 搭配因子给出确认（`match.partner_min_score`，默认 0.0，判定为 >=）
    3. 综合分不低于 `match.min_score`（默认 5.0 = 两道门槛都通过的底分）

当天分钟条件（`_check_buy_conditions`，用于盘中触发买入）
    1. 分钟级成交量放大因子打分达标（`buy.volume_score`）
    2. 分钟级搭配因子给出确认（`buy.partner_min_score`）
    3. 分钟数据条数不少于 max(`buy.min_minutes`, 成交量因子窗口, 搭配因子所需根数)
       —— 成交量因子窗口是 20 根、不足时**直接返回 0 分**，不显式拦掉这段
       会表现为「早盘前 20 分钟永不买入」；ADX 类搭配因子还需要 2×period 根
       （talib 的 ADX lookback = 2×period），不足时 talib 返回 NaN，
       `NaN > NaN` 为 False 会把方向判成 down、恒定给 −2.5 分，因此必须拦在算之前
    4. 涨停/跌停附近不买；9:40 前与 13:00~13:10 不判断（与其它策略一致）

卖出条件（`_check_sell_conditions`，累计负分达到阈值 `sell.max_score` 即卖出）
    1. 放量下跌：成交量因子打分转负（价跌量增 = 恐慌出逃）
    2. 量价背离：缩量上涨（量比 < 1 且当根收红），上涨乏力
    3. 跌破当日均价（VWAP）
    4. 跌破支撑位（搭配因子转负）
    5. 自持仓最高点回撤超过 `sell.drawdown_pct`
    6. 账户权益自峰值回撤达 `risk.max_drawdown_pct`（账户级条件，见下）

第 6 项是账户级风控，不是个股条件：回撤达阈值时**每一只被检查的股票都计入
`risk.drawdown_score` 分值**，效果是整体降仓，而不是冻结账户。回撤由 workflow /
回测在调用策略前注入（`BaseStrategy.set_account_drawdown`），策略自己拿不到账户权益。
默认 `risk.max_drawdown_pct=-10.0` + `risk.drawdown_score=-6.0`，与 `sell.max_score`
等值 —— 即「账户回撤超阈值」单独一条就足以触发卖出信号。

## 二、搭配因子的选择依据（实测）

在服务端日K样本（43 只股票 / 56 个交易日 / 924 个「窗口 40 根」样本，
2026-07-01~2026-09-16）上做了三件事：（a）各因子按日截面秩相关（IC）；
（b）与成交量因子的相关性（信息冗余度）；（c）成交量 × 搭配因子的分档矩阵。
实测明细见 `CODE_检查报告_2026-09-28_成交量策略与搭配因子实测.md`。

- **唯一与成交量互补的是「支撑/阻力位」这一族因子**：
  `BollSRFactor` 截面 IC **+0.166（t=+3.43）**、`KlineSRFactor` **+0.124（t=+2.74）**，
  两者与成交量打分**负相关**（−0.494 / −0.240），是独立的信息源。
  单因子看（全样本 924）：`boll > 0`（价格触及下方支撑轨）5 日超额 **+0.97%**（n=224）、
  `boll >= 0`（含「中轨下方未触轨」的 0 档）**+0.34%**（n=678）、`ksr > 0` **+0.88%**（n=183）；
  反向的 `boll <= -5` **−0.93%**（n=246）、`ksr <= -5` **−0.63%**（n=188）。
  这组数据是把 `partner_min_score` 定在 **0.0** 的依据：0 档与正档都不算差，
  负档（跌破轨道）才明显变差。
- 趋势/动量类因子全部为负 IC（`MaFactor` −0.208、`RegressionTrendFactor` −0.180、
  `MultiTrendFactor` −0.168、`AdxTrendFactor` −0.118、`PriceTrendFactor` −0.141、
  `ROCFactor` −0.101），且与成交量正相关 0.17~0.42（信息重复），叠加只会放大同向风险。
- 因此搭配因子默认 `boll_sr`（布林带支撑/阻力位），可用 `match.partner_factor`
  切换为 `kline_sr` / `multi_trend` / `ma` / `vwap` / `adx`。

## 三、成交量方向（`volume_mode`，默认 reversion = 实测方向）

同一份实测里，成交量因子**自身的 BUY 阈值**在该样本内是负超额：
`score >= 4` 的 5 日超额 **−1.03%**（超额胜率 38%，n=271），`score >= 6` 为 −0.60%
（胜率 33%），而 `score <= 0` 为 **+0.46%**；按日截面 IC **−0.156（t=−3.19）**，
前半段/后半段分段同号（−1.36% / −0.90%）。分档矩阵还显示：在**每一个** boll 档位内，
成交量分越高、后续超额越差（boll>=0 档：+1.24% / +1.01% / −1.21% 对应 V<=0 / 0<V<4 / V>=4）。

也就是说，在该样本中「放量」更接近情绪高点与派发，而「缩量 + 价格贴着支撑」才是
正超额（`V<4 且 boll>=0` → **+1.06%**，n=215）。

**当前默认为 `reversion`（缩量看多）**。依据是离线统计与正式回测方向一致：正式回测
（2026-06-01~09-30，我的科技股票池）`momentum` 12 笔 / 胜率 8.33% / **−9.14%**，
`reversion` 265 笔 / 胜率 54.34% / **+8.69%**，见
`CODE_检查报告_2026-09-29_成交量策略回测结果.md`。改回 `momentum` 即恢复因子原始
语义（放量看多）；`reversion` 把放量当否决条件，只有成交量打分 <= `quiet_volume_score`
时才允许买入。两种模式共用同一套打分、门槛与卖出逻辑，切换只改变成交量一侧的判定方向。

**样本局限**：43 只股票（服务端只有这些标的的日K）、56 个交易日、单一市场阶段，
且 5 日持有窗口相互重叠，统计量被高估。上述方向结论是「提示」而不是「定论」。
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable

import numpy as np
import pandas as pd

from factor.adx_trend_factor import AdxTrendFactor
from factor.base_factor import BaseFactor
from factor.boll_support_resistance import BollSRFactor
from factor.kline_support_resistance import KlineSRFactor
from factor.ma_factor import MaFactor
from factor.multi_trend_factor import MultiTrendFactor
from factor.volume_expansion_factor import VolumeExpansionFactor
from factor.vwap_factor import VwapFactor
from strategy.base_strategy import BaseStrategy


# 搭配因子注册表：名称 -> (中文说明, 正向满分, 最少K线数函数, 构造函数)
#
# 「正向满分」用于把不同尺度的因子归一化到 0~1。每个值必须与该因子 `score()`
# 的实际可达上限一致，否则归一化会静默失真（例如把 MaFactor 的 7.5 当成 10，
# 会让它的得分被系统性压低 25%）。
#
# 「最少K线数函数」用于在计算之前拦截数据不足的窗口：
# - BollSRFactor 需要 timeperiod 根（其轨道本身含当根价格）；
# - MaFactor.score 内置 `len < 20 → 0.0`；
# - AdxTrendFactor 是 talib.ADX，lookback = **2 × period**，不足时返回 NaN，
#   而 `NaN > NaN` 为 False 会把它判成下跌方向并恒定给出 −2.5 分（不是 0），
#   所以必须显式拦在计算之前。
PARTNER_FACTORS: dict[
    str, tuple[str, float, Callable[[int], int], Callable[[int], BaseFactor]]
] = {
    "boll_sr": (
        "布林带支撑/阻力位",
        10.0,
        lambda days: max(min(days, 20), 5),
        lambda days: BollSRFactor(timeperiod=max(min(days, 20), 5)),
    ),
    "kline_sr": (
        "K线支撑/阻力位",
        10.0,
        lambda days: max(days // 2, 3),
        lambda days: KlineSRFactor(days=max(days // 2, 3), tolerance=0.02),
    ),
    "multi_trend": (
        "复合趋势（ADX/MACD/VWAP/HH-HL）",
        MultiTrendFactor.MAX_SCORE,
        lambda days: days,
        lambda days: MultiTrendFactor(
            adx_period=days, vwap_period=days, high_low_period=days
        ),
    ),
    "ma": ("均线排列", 7.5, lambda days: 20, lambda days: MaFactor(days=5)),
    "vwap": ("均价偏离（百分点）", 3.0, lambda days: days, lambda days: VwapFactor(days=days)),
    "adx": ("ADX 趋势强度", 10.0, lambda days: 2 * days, lambda days: AdxTrendFactor(days=days)),
}

# 成交量模式
MODE_MOMENTUM: str = "momentum"  # 放量看多（因子原始语义）
MODE_REVERSION: str = "reversion"  # 缩量看多（实测方向）
# 默认方向：配置缺省与非法值都回退到它，避免「默认值」在多处各写一遍
DEFAULT_VOLUME_MODE: str = MODE_REVERSION


class VolumeExpansionStrategy(BaseStrategy):
    """成交量放大策略。

    以成交量放大因子为主因子、支撑/阻力位类因子为搭配因子，
    在日线选股的基础上由分钟行情给出买卖信号。
    """

    strategy_name: str = "成交量放大策略"
    description: str = (
        "以成交量放大因子打分给出买卖信号，并用支撑/阻力位因子做确认；"
        "放量下跌与量价背离触发卖出"
    )
    strategy_type: str = "选股策略"

    # ---- 选股参数（match）----
    VOLUME_DAYS: int = 20  # 成交量因子窗口（因子内部下限 5）
    MATCH_VOLUME_SCORE: float = 4.0  # 日线成交量打分门槛（10 分制 = 因子的 BUY 阈值）
    MATCH_PARTNER_MIN_SCORE: float = 0.0  # 搭配因子门槛（>=）
    MATCH_PARTNER_FACTOR: str = "boll_sr"  # 搭配因子名，见 PARTNER_FACTORS
    # 默认方向取实测方向 reversion（缩量看多，放量当否决条件）。
    # 依据：正式回测（2026-06-01~09-30，我的科技股票池）momentum 12 笔 / −9.14%、
    # reversion 265 笔 / +8.69%；离线统计（43 只 / 924 样本）方向一致。
    # 见 CODE_检查报告_2026-09-29_成交量策略回测结果.md
    MATCH_VOLUME_MODE: str = DEFAULT_VOLUME_MODE  # reversion（默认）/ momentum
    MATCH_QUIET_VOLUME_SCORE: float = 4.0  # reversion 模式下「缩量」上限

    # ---- 买入参数（buy）----
    BUY_MIN_MINUTES: int = 20  # 分钟数据最少条数（成交量因子需要 20 根）
    BUY_VOLUME_SCORE: float = 4.0  # 分钟级成交量打分门槛
    BUY_PARTNER_MIN_SCORE: float = 0.0  # 分钟级搭配因子门槛（>=）
    BUY_PARTNER_FACTOR: str = ""  # 空 = 跟随 match.partner_factor
    BUY_VOLUME_MODE: str = ""  # 空 = 跟随 match.volume_mode
    BUY_QUIET_VOLUME_SCORE: float = 4.0  # reversion 模式下的缩量上限
    # 成交量侧与搭配侧的权重：成交量为主（0.6）、搭配因子做确认（0.4）
    VOLUME_WEIGHT: float = 0.6
    PARTNER_WEIGHT: float = 0.4
    # 综合分 = BASE_SCORE（两道门槛都通过）+ 最多 BONUS_SCORE（量能与位置的强度）。
    # 因此默认门槛取 5.0 时等价于「只用两道门槛筛选」，门槛调高才要求更强的量能/位置。
    BASE_SCORE: float = 5.0
    BONUS_SCORE: float = 5.0
    MIN_SCORE: float = 5.0  # 综合分门槛（10 分制），见 BASE_SCORE 说明

    # ---- 卖出参数（sell）----
    SELL_MIN_MINUTES: int = 20  # 分钟数据最少条数
    SELL_MAX_SCORE: float = -6.0  # 卖出分阈值（累计负分 <= 此值触发卖出）
    SELL_DRAWDOWN_PCT: float = -6.0  # 自持仓最高点回撤阈值（%）
    SELL_VWAP_MINUTES: int = 10  # 均价（VWAP）窗口
    SELL_DIVERGENCE_SCORE: float = -2.0  # 量价背离（缩量上涨）分值
    SELL_BELOW_VWAP_SCORE: float = -3.0  # 明显跌破均价的分值
    SELL_SLIGHT_BELOW_VWAP_SCORE: float = -1.0  # 略低于均价的分值
    SELL_BREAK_SUPPORT_SCORE: float = -3.0  # 跌破支撑位的分值
    SELL_DRAWDOWN_SCORE: float = -6.0  # 触发回撤阈值时的分值

    # ---- 风控参数（risk）----
    # 账户级回撤阈值（%）：账户已实现权益自峰值回撤达此值时，在卖出判定中计入
    # `drawdown_score` 分值（**不冻结开新仓**）；0 或正数表示关闭。
    # 口径见 BaseStrategy.RISK_MAX_DRAWDOWN_PCT。
    # 依据：正式回测（2026-06-01~09-30，我的科技股票池）reversion 版最大回撤 17.01%，
    # 用户要求把回撤约束落到卖出侧。见 CODE_检查报告_2026-09-29_成交量策略回测结果.md
    RISK_MAX_DRAWDOWN_PCT: float = -10.0
    # 账户回撤达阈值时计入卖出判定的分值（10 分制，负值）。与 sell.max_score(-6.0)
    # 等值，即「账户回撤超阈值」单独一条就足以触发卖出信号。
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
        """初始化成交量放大策略。

        `user_strategy_name` 必须与基类签名一致：workflow 通过
        `_instantiate_strategy` 用关键字传参，子类 `__init__` 少了这个形参会抛
        `TypeError`，被 `_instantiate_strategy` 的 `except Exception` 兜住后
        **整个用户策略被跳过**（不建仓、不发信号、不卖），日志里只剩一条 ERROR。
        同时必须继续往 `super().__init__()` 传，否则 `self.user_strategy_name`
        是空串，策略内所有以它为前缀的日志/原因文本都会缺失策略名。
        """
        # 必须在 super().__init__() 之前建好这两个属性：基类构造过程中会回调
        # apply_params()，而 apply_params 会写 self.logger 并执行
        # self._partner_cache.clear()。放在 super() 之后赋值会抛
        # AttributeError，被 workflow 的 except 兜住后表现为「策略整个不加载」。
        self.logger = logging.getLogger(__name__)
        # 搭配因子实例按 (名称, 窗口) 缓存，避免每分钟行情都重新构造；
        # 但买入侧与日线侧可能配置不同的搭配因子，因此按名字分别缓存。
        self._partner_cache: dict[tuple[str, int], BaseFactor] = {}
        super().__init__(
            user_strategy_id=user_strategy_id,
            user_strategy_name=user_strategy_name,
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            user_strategy_params=user_strategy_params,
            default_params=default_params,
        )

    def apply_params(self, params: dict[str, Any]) -> None:
        """将参数字典映射到策略属性（结构：`{match: {...}, buy: {...}, sell: {...}}`）。

        缺失字段保留类属性默认值。`partner_factor` / `volume_mode` 写错名字时
        回退到默认值并记 warning —— 静默回退会让策略看起来在跑、实际用的是
        另一套逻辑，是最难排查的一类问题。

        :param params: 策略参数字典
        """
        if not isinstance(params, dict) or not params:
            return
        match_config: dict[str, Any] = params.get("match", {}) or {}
        buy_config: dict[str, Any] = params.get("buy", {}) or {}
        sell_config: dict[str, Any] = params.get("sell", {}) or {}
        risk_config: dict[str, Any] = params.get("risk", {}) or {}

        self.VOLUME_DAYS = int(match_config.get("volume_days", self.VOLUME_DAYS))
        self.MATCH_VOLUME_SCORE = float(
            match_config.get("volume_score", self.MATCH_VOLUME_SCORE)
        )
        self.MATCH_PARTNER_MIN_SCORE = float(
            match_config.get("partner_min_score", self.MATCH_PARTNER_MIN_SCORE)
        )
        self.MATCH_PARTNER_FACTOR = self._resolve_partner_name(
            str(match_config.get("partner_factor", self.MATCH_PARTNER_FACTOR)),
            "match.partner_factor",
        )
        self.MATCH_VOLUME_MODE = self._resolve_volume_mode(
            str(match_config.get("volume_mode", self.MATCH_VOLUME_MODE)),
            "match.volume_mode",
        )
        self.MATCH_QUIET_VOLUME_SCORE = float(
            match_config.get("quiet_volume_score", self.MATCH_QUIET_VOLUME_SCORE)
        )
        match_min_score = float(match_config.get("min_score", self.MIN_SCORE))

        self.BUY_MIN_MINUTES = int(buy_config.get("min_minutes", self.BUY_MIN_MINUTES))
        self.BUY_VOLUME_SCORE = float(buy_config.get("volume_score", self.BUY_VOLUME_SCORE))
        self.BUY_PARTNER_MIN_SCORE = float(
            buy_config.get("partner_min_score", self.BUY_PARTNER_MIN_SCORE)
        )
        self.BUY_PARTNER_FACTOR = self._resolve_partner_name(
            str(buy_config.get("partner_factor", self.BUY_PARTNER_FACTOR)),
            "buy.partner_factor",
            allow_empty=True,
        )
        self.BUY_VOLUME_MODE = self._resolve_volume_mode(
            str(buy_config.get("volume_mode", self.BUY_VOLUME_MODE)),
            "buy.volume_mode",
            allow_empty=True,
        )
        self.BUY_QUIET_VOLUME_SCORE = float(
            buy_config.get("quiet_volume_score", self.BUY_QUIET_VOLUME_SCORE)
        )
        self.VOLUME_WEIGHT = float(buy_config.get("volume_weight", self.VOLUME_WEIGHT))
        self.PARTNER_WEIGHT = float(buy_config.get("partner_weight", self.PARTNER_WEIGHT))
        buy_min_score = float(buy_config.get("min_score", self.MIN_SCORE))

        self.SELL_MIN_MINUTES = int(sell_config.get("min_minutes", self.SELL_MIN_MINUTES))
        self.SELL_MAX_SCORE = float(sell_config.get("max_score", self.SELL_MAX_SCORE))
        self.SELL_DRAWDOWN_PCT = float(
            sell_config.get("drawdown_pct", self.SELL_DRAWDOWN_PCT)
        )
        self.SELL_VWAP_MINUTES = int(
            sell_config.get("vwap_minutes", self.SELL_VWAP_MINUTES)
        )
        # 选股与买入的综合分门槛共用同一个语义（10 分制），配置里两者同名 min_score，
        # 这里统一保存为 MIN_SCORE；未配置时沿用类属性默认值。
        if "min_score" in match_config:
            self.MIN_SCORE = match_min_score
        if "min_score" in buy_config:
            self.MIN_SCORE = buy_min_score

        # 账户级回撤：值来自 risk 节，口径见 BaseStrategy.RISK_MAX_DRAWDOWN_PCT。
        # 回撤本身由 workflow / 回测每轮注入（set_account_drawdown），这里只读配置。
        self.RISK_MAX_DRAWDOWN_PCT = float(
            risk_config.get("max_drawdown_pct", self.RISK_MAX_DRAWDOWN_PCT)
        )
        self.SELL_RISK_DRAWDOWN_SCORE = float(
            risk_config.get("drawdown_score", self.SELL_RISK_DRAWDOWN_SCORE)
        )

        # 参数变化后丢弃搭配因子缓存，下一次取用时按新参数重建
        self._partner_cache.clear()

    # ---- 参数校验 ----

    @staticmethod
    def _resolve_partner_name(name: str, field: str, allow_empty: bool = False) -> str:
        """校验搭配因子名；`allow_empty` 时空值保留（表示跟随日线配置）。"""
        if not name:
            return "" if allow_empty else "boll_sr"
        if name not in PARTNER_FACTORS:
            logging.getLogger(__name__).warning(
                "%s 配置的搭配因子 %r 不存在，回退默认值；可选: %s",
                field, name, ", ".join(PARTNER_FACTORS),
            )
            return "" if allow_empty else "boll_sr"
        return name

    @staticmethod
    def _resolve_volume_mode(mode: str, field: str, allow_empty: bool = False) -> str:
        """校验成交量模式；缺省与非法值都回退为 DEFAULT_VOLUME_MODE。"""
        if not mode:
            return "" if allow_empty else DEFAULT_VOLUME_MODE
        if mode not in (MODE_MOMENTUM, MODE_REVERSION):
            logging.getLogger(__name__).warning(
                "%s 配置的成交量模式 %r 非法（应为 %s / %s），回退 %s",
                field, mode, MODE_MOMENTUM, MODE_REVERSION, DEFAULT_VOLUME_MODE,
            )
            return "" if allow_empty else DEFAULT_VOLUME_MODE
        return mode

    # ---- 因子构建 ----

    def init_factors(self) -> None:
        """注册策略所需因子。

        成交量因子与搭配因子都在本策略实例内构建并注册到实例私有的
        `factor_manager`（因子管理器已是实例私有，多用户策略互不覆盖）。
        """
        volume_factor: VolumeExpansionFactor = VolumeExpansionFactor(days=self.VOLUME_DAYS)
        self.factor_manager.register(volume_factor.factor_name, volume_factor)
        partner: BaseFactor | None = self._partner()
        if partner is not None:
            self.factor_manager.register(partner.factor_name, partner)

    def _partner_name(self, for_buy: bool = False) -> str:
        """当前生效的搭配因子名（买入侧为空时跟随日线配置）。"""
        name: str = self.BUY_PARTNER_FACTOR if for_buy else self.MATCH_PARTNER_FACTOR
        return name or self.MATCH_PARTNER_FACTOR

    def _partner(self, for_buy: bool = False) -> BaseFactor | None:
        """构建（并缓存）搭配因子实例。"""
        name: str = self._partner_name(for_buy)
        entry = PARTNER_FACTORS.get(name)
        if entry is None:
            self.logger.warning("搭配因子 %r 未注册，跳过搭配确认", name)
            return None
        key: tuple[str, int] = (name, self.VOLUME_DAYS)
        cached: BaseFactor | None = self._partner_cache.get(key)
        if cached is None:
            cached = entry[3](self.VOLUME_DAYS)
            self._partner_cache[key] = cached
        return cached

    def _partner_min_bars(self, for_buy: bool = False) -> int:
        """搭配因子在当前窗口下所需的最少 K 线数。"""
        entry = PARTNER_FACTORS.get(self._partner_name(for_buy))
        return entry[2](self.VOLUME_DAYS) if entry else 0

    @staticmethod
    def _partner_max_score(name: str) -> float:
        """搭配因子的正向满分（归一化用）。"""
        entry = PARTNER_FACTORS.get(name)
        return entry[1] if entry else 10.0

    def _volume_factor(self) -> VolumeExpansionFactor:
        """取成交量因子实例（未注册时按当前参数构建）。"""
        factor: BaseFactor | None = self.factor_manager.get(
            VolumeExpansionFactor.factor_name
        )
        if isinstance(factor, VolumeExpansionFactor):
            return factor
        factor = VolumeExpansionFactor(days=self.VOLUME_DAYS)
        self.factor_manager.register(factor.factor_name, factor)
        return factor

    # ---- 打分 ----

    def _volume_detail(self, df: pd.DataFrame) -> dict[str, Any]:
        """成交量因子打分，统一容错。

        `VolumeExpansionFactor.score` 返回 **dict**（与 `KlineSRFactor` 等返回
        float 的因子不同），数据不足时返回全 0 明细。这里统一收敛为 dict，
        避免调用方按 float 使用。

        :param df: 含 close/high/low/volume 列的行情（日线或分钟）
        :return: 因子明细字典，失败时返回零值明细
        """
        try:
            result: Any = self._volume_factor().score(df)
        except Exception as exc:
            self.logger.warning("成交量因子计算失败: %s", exc)
            return {"score": 0.0, "signal": "None", "reason": []}
        if not isinstance(result, dict):
            self.logger.warning("成交量因子返回类型异常: %r", type(result))
            return {"score": 0.0, "signal": "None", "reason": []}
        return result

    def _partner_score(self, df: pd.DataFrame, for_buy: bool = False) -> float | None:
        """搭配因子打分。

        **数据不足时返回 None**，而不是 0.0 —— 0 是一个合法的因子取值
        （如布林带「价格在中轨下方但未触及轨道」就是 0），把它与「算不出来」
        混为一谈，会让「数据不足」被当成「中性确认」而放行。

        :param df: 行情 DataFrame
        :param for_buy: True 用买入侧配置的搭配因子
        :return: 因子打分；未配置因子或数据不足时返回 None
        """
        factor: BaseFactor | None = self._partner(for_buy)
        if factor is None:
            return None
        if len(df) < self._partner_min_bars(for_buy):
            return None
        try:
            return float(factor.score(df))
        except Exception as exc:
            self.logger.warning("搭配因子 %s 计算失败: %s", factor.factor_name, exc)
            return None

    def _combine(
        self,
        volume_score: float,
        partner_score: float | None,
        partner_name: str,
        mode: str,
        volume_threshold: float,
        partner_threshold: float,
        quiet_threshold: float,
    ) -> tuple[bool, bool, float, float, float]:
        """把成交量分与搭配分合成「(成交量达标, 搭配达标, 综合分, 量能强度, 位置强度)」。

        - 成交量门槛按模式判定：`momentum` 要求 `>= volume_threshold`；
          `reversion` 要求 `<= quiet_threshold`（把放量当否决条件）。
        - 搭配门槛是 `>=`。默认 0.0：对支撑/阻力类因子表示「价格不在阻力区/
          未破支撑」，对连续型因子表示「不反对」。
        - 综合分 = `BASE_SCORE`（两道门槛都通过）+ `BONUS_SCORE` × 加权强度。
          强度用「超过门槛多少」衡量而不是用绝对分值，这样不同尺度、不同门槛
          的配置之间可比；量能侧与位置侧各占 `VOLUME_WEIGHT` / `PARTNER_WEIGHT`。

        :param volume_score: 成交量因子打分（0~10，可为负）
        :param partner_score: 搭配因子打分；None 表示无法计算
        :param partner_name: 搭配因子名（决定归一化满分）
        :param mode: 成交量模式（momentum / reversion）
        :param volume_threshold: 成交量门槛（momentum 模式）
        :param partner_threshold: 搭配因子门槛
        :param quiet_threshold: 缩量上限（reversion 模式）
        :return: (成交量是否达标, 搭配是否达标, 综合分, 量能强度, 位置强度)
        """
        if mode == MODE_REVERSION:
            volume_ok: bool = volume_score <= quiet_threshold
            span: float = max(quiet_threshold, 1e-6)
            volume_component: float = min(max(quiet_threshold - volume_score, 0.0) / span, 1.0)
        else:
            volume_ok = volume_score >= volume_threshold
            span = max(10.0 - volume_threshold, 1e-6)
            volume_component = min(max(volume_score - volume_threshold, 0.0) / span, 1.0)

        partner_ok: bool = partner_score is not None and partner_score >= partner_threshold
        partner_max: float = max(self._partner_max_score(partner_name), 1e-6)
        partner_component: float = (
            min(max(partner_score - partner_threshold, 0.0), partner_max) / partner_max
            if partner_ok and partner_score is not None
            else 0.0
        )

        total_weight: float = self.VOLUME_WEIGHT + self.PARTNER_WEIGHT
        if total_weight <= 0:
            total_weight = 1.0
        strength: float = (
            volume_component * self.VOLUME_WEIGHT + partner_component * self.PARTNER_WEIGHT
        ) / total_weight
        combined: float = (
            self.BASE_SCORE + self.BONUS_SCORE * strength if volume_ok and partner_ok else 0.0
        )
        return (
            volume_ok,
            partner_ok,
            round(combined, 2),
            round(volume_component, 4),
            round(partner_component, 4),
        )

    @staticmethod
    def _to_star(score: float) -> float:
        """10 分制折算 5 星，夹到 0~5（服务端字段约束）。

        折算逻辑已上移 `BaseStrategy.score_to_star`：原先四个策略各自实现一遍，
        漏改其中一处（波段策略卖出侧）就让超范围的星级打到服务端被 400 拒绝。
        这里保留方法名兼容既有调用点。
        """
        return BaseStrategy.score_to_star(score)

    # ---- 行情构造 ----

    @staticmethod
    def _kline_frame(stock_data: list[dict[str, Any]]) -> pd.DataFrame:
        """由行情 dict 列表构造因子所需的 DataFrame（含 price 列）。"""
        df: pd.DataFrame = pd.DataFrame(stock_data)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # VwapFactor / BollSRFactor 读的是 price 列：分钟行情里 price 与 close
        # 都有，日线行情只有 close，这里统一补一列 price，避免因子拿到空列返回 0
        if "price" not in df.columns:
            df["price"] = df["close"] if "close" in df.columns else np.nan
        return df

    # ---- 基类抽象方法实现 ----

    def is_match_strategy(
        self, stock_data: list[dict[str, Any]]
    ) -> tuple[bool, dict[str, Any]]:
        """判断历史日线数据是否满足策略买入条件（用于盘前选股/回测）。

        `stock_data` 是历史行情（不含当天），窗口长度由调用方给出
        （workflow 用 `SELECT_KLINE_DAYS = 40`，回测用 `signal_window`）。

        :param stock_data: 股票日线行情列表，每项含 code/close/high/low/volume
        :return: (是否匹配, 详细信息字典)
        """
        required: int = max(self.VOLUME_DAYS, self._partner_min_bars(False))
        if not stock_data or len(stock_data) < required:
            return False, {
                "reason": (
                    f"{self.user_strategy_name}, 数据不足，需至少 {required} 个交易日"
                    f"（成交量因子 {self.VOLUME_DAYS} 根 / 搭配因子 "
                    f"{self._partner_min_bars(False)} 根）"
                )
            }
        code: str = str(stock_data[0].get("code", ""))
        if not code:
            return False, {"reason": f"{self.user_strategy_name}, 无法获取股票代码"}

        df: pd.DataFrame = self._kline_frame(stock_data)
        volume: dict[str, Any] = self._volume_detail(df)
        volume_score: float = float(volume.get("score", 0.0) or 0.0)
        partner_name: str = self._partner_name(False)
        partner_score: float | None = self._partner_score(df, False)
        volume_ok, partner_ok, combined, vol_strength, pos_strength = self._combine(
            volume_score,
            partner_score,
            partner_name,
            self.MATCH_VOLUME_MODE,
            self.MATCH_VOLUME_SCORE,
            self.MATCH_PARTNER_MIN_SCORE,
            self.MATCH_QUIET_VOLUME_SCORE,
        )
        matched: bool = bool(volume_ok and partner_ok and combined >= self.MIN_SCORE)
        volume_threshold: float = (
            self.MATCH_QUIET_VOLUME_SCORE
            if self.MATCH_VOLUME_MODE == MODE_REVERSION
            else self.MATCH_VOLUME_SCORE
        )
        detail: dict[str, Any] = {
            "code": code,
            "score": combined,
            "star": self._to_star(combined),
            "volume_score": round(volume_score, 2),
            "partner_factor": partner_name,
            "partner_score": "" if partner_score is None else round(partner_score, 2),
            "signal": "BUY",
            "detail": list(volume.get("reason", []) or []),
        }
        judge: str = "选中" if matched else "未选中"
        partner_text: str = (
            "数据不足" if partner_score is None else f"{partner_score:.2f}"
        )
        detail["reason"] = (
            f"{self.user_strategy_name},{judge}："
            f"成交量分{volume_score:.2f}（{self.MATCH_VOLUME_MODE} 模式，门槛 {volume_threshold}），"
            f"搭配分{partner_text}（{partner_name}，门槛 {self.MATCH_PARTNER_MIN_SCORE}），"
            f"量能强度{vol_strength:.2f} 位置强度{pos_strength:.2f}，"
            f"综合分{combined:.2f}（门槛 {self.MIN_SCORE}）"
        )
        return matched, detail

    # ---- 盘前准备 ----

    def before_trading(self, trade_date: str, stock_codes: list[str], **kwargs: Any) -> None:
        """每天交易前的准备工作：清理缓存 + 判定股票池的日线趋势方向。

        趋势判定走基类 `load_daily_trend`（`AdxTrendFactor`，与波段策略同一口径），
        结果注入门槛机制：**向下 ⇒ 买入门槛 +`TREND_BUY_OFFSET`（更难买）、
        向上 ⇒ 卖出门槛 +`TREND_SELL_OFFSET`（更难卖）**。

        为什么放盘前而不是在买入判定里按需算：判定要取日K（HTTP），而
        `_check_buy_conditions` 在分钟级被高频调用，盘中打 HTTP 会拖慢甚至阻塞
        交易线程；盘前一次性取完，盘中只查集合。
        取数失败 / 数据不足时该项不产生偏移（fail-open）—— 门槛是附加收紧项，
        缺失不该改变原有行为。
        """
        super().before_trading(trade_date, stock_codes, **kwargs)
        self.load_daily_trend(stock_codes, trade_date)

    # ---- 分钟级买入 ----

    def _check_buy_conditions(
        self, code: str, stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """分钟级买入条件检查。

        成交量为主（`VOLUME_WEIGHT` = 0.6）、搭配因子确认（0.4）；成交量阈值按模式
        判定 —— `momentum` 要求 `BUY_VOLUME_SCORE` 以上，`reversion` 要求
        `BUY_QUIET_VOLUME_SCORE` 以下。

        分钟数据条数门槛取「本配置、成交量因子窗口、搭配因子所需根数」三者最大值
        （见 `PARTNER_FACTORS` 的「最少K线数」）：成交量因子不足窗口时返回 0 分，
        ADX 类搭配因子不足 2×period 时会因 NaN 恒定给 −2.5 分，两者都必须拦在计算之前。

        :param code: 股票代码
        :param stock_data: 实时/分钟行情数据
        :return: (是否满足, 详细信息)
        """
        # 极端利空禁买（2026-10-13）：市场极端 + 该股跌破 S3 ⇒ 当日只卖不买。
        # 放在最前面：硬性拦截，不受 `MIN_SCORE + buy_threshold_offset` 影响。
        if self.is_buy_blocked(code):
            return False, {
                "reason": (
                    f"{self.user_strategy_name}, 极端利空禁买"
                    "（前一交易日跌停超阈值且该股跌破S3），当日只卖不买"
                ),
                "code": code,
            }
        try:
            current_price: float = float(stock_data.get("price", 0))
            open_price: float = float(stock_data.get("open", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            create_time: str = str(stock_data.get("create_time", ""))

            if current_price <= 0:
                return False, {
                    "reason": f"{self.user_strategy_name}, 当前价格异常",
                    "code": code,
                }
            if open_price <= 0 or preclose <= 0:
                return False, {
                    "reason": f"{self.user_strategy_name}, 开盘价或昨收价数据异常",
                    "code": code,
                }
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_buy_conditions,"
                        f"无法解析分钟时间: {create_time}"
                    ),
                    "code": code,
                }
            # 避开 9:40 以前与 13:00~13:10，过滤开盘/午后开盘的瞬时抖动
            if (minute_dt.hour == 9 and minute_dt.minute <= 40) or (
                minute_dt.hour == 13 and minute_dt.minute <= 10
            ):
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 9:40 或 13:10 前不判断买入条件，过滤干扰"
                    ),
                    "code": code,
                }
            # 涨停/跌停附近不买入，阈值按板块推算（ST 5% / 创业板科创板 20% / 其余 10%）
            chg_percent: float = self._change_percent(stock_data, current_price, preclose)
            near_limit: float = self._near_price_limit_pct(
                code, str(stock_data.get("name", "") or ""), margin=2.0
            )
            if abs(chg_percent) >= near_limit:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name} 接近涨停或跌停,"
                        f"涨跌幅{chg_percent:.2f}%（阈值±{near_limit:.1f}%），不买入"
                    ),
                    "code": code,
                    "current_price": round(current_price, 2),
                }

            required: int = max(
                self.BUY_MIN_MINUTES, self.VOLUME_DAYS, self._partner_min_bars(True)
            )
            bars: list[dict[str, Any]] = self._get_minute_cache(code)
            if len(bars) < required:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, 分钟数据不足: {len(bars)}/{required}，"
                        "成交量/搭配因子窗口未满"
                    ),
                    "code": code,
                }
            df: pd.DataFrame = self._kline_frame(bars)
            volume: dict[str, Any] = self._volume_detail(df)
            volume_score: float = float(volume.get("score", 0.0) or 0.0)
            partner_name: str = self._partner_name(True)
            partner_score: float | None = self._partner_score(df, True)
            mode: str = self.BUY_VOLUME_MODE or self.MATCH_VOLUME_MODE
            volume_ok, partner_ok, combined, vol_strength, pos_strength = self._combine(
                volume_score,
                partner_score,
                partner_name,
                mode,
                self.BUY_VOLUME_SCORE,
                self.BUY_PARTNER_MIN_SCORE,
                self.BUY_QUIET_VOLUME_SCORE,
            )
            volume_threshold: float = (
                self.BUY_QUIET_VOLUME_SCORE if mode == MODE_REVERSION else self.BUY_VOLUME_SCORE
            )
            detail: dict[str, Any] = {
                "code": code,
                "signal": "BUY",
                "price": current_price,
                "current_price": current_price,
                "score": combined,
                "star": self._to_star(combined),
                "volume_score": round(volume_score, 2),
                "partner_factor": partner_name,
                "partner_score": "" if partner_score is None else round(partner_score, 2),
                "create_time": create_time,
            }
            # 买入门槛偏移：个股阀门（盘后算）+ 市场偏移，均为「>0 = 更难买」
            buy_offset: float = self.buy_threshold_offset(code)
            buy_threshold: float = self.MIN_SCORE + buy_offset
            matched: bool = bool(volume_ok and partner_ok and combined >= buy_threshold)
            partner_text: str = (
                "数据不足" if partner_score is None else f"{partner_score:.2f}"
            )
            extras: str = ""
            if volume.get("consecutive"):
                extras += f", 连续放量{int(volume.get('consecutive', 0) or 0)}分钟"
            if volume.get("vol_ratio"):
                extras += f", 量比{volume.get('vol_ratio')}"
            detail["reason"] = (
                f"{self.user_strategy_name},{'买入条件合格' if matched else '买入条件未满足'}："
                f"成交量分{volume_score:.2f}（{mode} 模式，门槛 {volume_threshold}），"
                f"搭配分{partner_text}（{partner_name}，门槛 {self.BUY_PARTNER_MIN_SCORE}），"
                f"量能强度{vol_strength:.2f} 位置强度{pos_strength:.2f}，"
                f"综合分{combined:.2f}（门槛 {buy_threshold:.2f}）{extras}"
            )
            return matched, detail
        except Exception as exc:
            self.logger.warning(
                "[%s] _check_buy_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

    # ---- 分钟级卖出 ----

    def _check_sell_conditions(
        self, code: str, position: dict[str, Any], stock_data: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """分钟级卖出条件检查（累计负分达到阈值或回撤超限即卖出）。

        分项见模块 docstring。`position` 在实时链路里恒为空字典（设计约定 D-1），
        此时买入基准回退到「最近 2 个交易日」，与其它策略的卖出基准口径一致。

        :param code: 股票代码
        :param position: 持仓信息（可能为空）
        :param stock_data: 实时/分钟行情数据
        :return: (是否触发卖出, 详细信息)
        """
        cost_price: float = float(position.get("cost_price", 0))
        create_time: str = str(stock_data.get("create_time", ""))
        # 有持仓取真实买入日；实时链路恒传空持仓（D-1），退化为「最近 2 个交易日」。
        # 回退逻辑已上移基类（`_sell_baseline_buy_time`），避免与波段策略两处口径漂移。
        buy_time: str = self._sell_baseline_buy_time(position, create_time)
        extra: dict[str, Any] = {"code": code, "cost_price": cost_price}
        try:
            current_price: float = float(stock_data.get("price", 0))
            preclose: float = float(stock_data.get("preclose", 0))
            day_high: float = float(stock_data.get("high", 0))
            if current_price <= 0:
                return False, {
                    "reason": (
                        f"{self.user_strategy_name}, func:check_sell_conditions,"
                        f"当前价格{current_price}异常"
                    ),
                    "code": code,
                }
            extra["current_price"] = current_price
            minute_dt: datetime.datetime | None = self._parse_time(create_time)
            if minute_dt is None:
                return False, {
                    "reason": f"{self.user_strategy_name}, 解析时间异常，无法操作",
                    "code": code,
                }
            if minute_dt.hour == 9 and minute_dt.minute < 35:
                return False, {
                    "reason": f"{self.user_strategy_name}, 9:35 之前不操作，过滤开盘行情抖动",
                    "code": code,
                }
            # 涨停/跌停附近不卖出（封板时挂单没有意义）
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

            score: float = 0.0
            volume_score: float = 0.0
            reasons: list[str] = []
            required: int = max(
                self.SELL_MIN_MINUTES, self.VOLUME_DAYS, self._partner_min_bars(False)
            )
            bars: list[dict[str, Any]] = self._get_minute_cache(code)
            if len(bars) >= required:
                df: pd.DataFrame = self._kline_frame(bars)
                volume: dict[str, Any] = self._volume_detail(df)
                volume_score = float(volume.get("score", 0.0) or 0.0)
                # 1) 放量下跌：因子打分本身为负（价跌量增 / 量能走弱）
                if volume_score < 0:
                    score += volume_score
                    reasons.append(f"放量下跌(成交量分{volume_score:.2f})")
                # 2) 量价背离：缩量上涨 —— 量比 < 1 且当根收红，上涨缺少承接
                ratio: float = float(volume.get("vol_ratio", 0.0) or 0.0)
                closes: pd.Series = df["close"]
                price_up: bool = (
                    len(closes) >= 2 and float(closes.iloc[-1]) > float(closes.iloc[-2])
                )
                if price_up and 0 < ratio < 1.0:
                    score += self.SELL_DIVERGENCE_SCORE
                    reasons.append(f"缩量上涨(量比{ratio:.2f})")
                # 3) 跌破当日均价
                vwap_pct: float = self._vwap_deviation(df)
                extra["vwap_pct"] = round(vwap_pct, 2)
                if vwap_pct <= -1.0:
                    score += self.SELL_BELOW_VWAP_SCORE
                    reasons.append(f"跌破均价{vwap_pct:.2f}%")
                elif vwap_pct < 0.0:
                    score += self.SELL_SLIGHT_BELOW_VWAP_SCORE
                    reasons.append(f"低于均价{vwap_pct:.2f}%")
                # 4) 跌破支撑位（搭配因子转负）
                partner_name: str = self._partner_name(False)
                partner_score: float | None = self._partner_score(df, False)
                extra["partner_score"] = (
                    "" if partner_score is None else round(partner_score, 2)
                )
                if partner_score is not None and partner_score < 0:
                    score += self.SELL_BREAK_SUPPORT_SCORE
                    reasons.append(f"破支撑({partner_name} {partner_score:.2f})")
            else:
                reasons.append(f"分钟数据不足({len(bars)}/{required})")

            # 5) 自持仓最高点回撤
            highest_price: float = self._resolve_highest_price(
                code, buy_time, create_time, day_high, cost_price
            )
            extra["highest_price"] = round(highest_price, 2)
            if highest_price > 0:
                drawdown: float = (current_price - highest_price) / highest_price * 100
                extra["drawdown"] = round(drawdown, 2)
                if drawdown < self.SELL_DRAWDOWN_PCT:
                    score += self.SELL_DRAWDOWN_SCORE
                    reasons.append(
                        f"回撤{drawdown:.2f}%(最高{highest_price:.2f}→{current_price:.2f})"
                    )

            # 6) 账户级回撤：账户权益自峰值回撤达 risk.max_drawdown_pct 时分值入账。
            #    这是**账户级**条件，对被检查的每只股票都成立（回撤超限时整体降仓），
            #    语义是「把回撤当作一条卖出理由」，不冻结开新仓。
            extra["account_drawdown"] = round(float(self.account_drawdown_pct or 0.0), 2)
            risk_score: float = self._risk_drawdown_score()
            if risk_score < 0:
                score += risk_score
                reasons.append(
                    f"账户回撤{self.account_drawdown_pct:.2f}%"
                    f"达阈值{self.RISK_MAX_DRAWDOWN_PCT:.2f}%"
                )

            extra["score"] = round(score, 2)
            extra["volume_score"] = round(volume_score, 2)
            # 卖出门槛偏移：个股阀门（卖飞后更难卖）+ 市场偏移（市场弱时更容易卖）。
            sell_offset: float = self.sell_threshold_offset(code)
            sell_threshold: float = self.SELL_MAX_SCORE - sell_offset
            if score <= sell_threshold:
                extra["signal"] = "SELL"
                # 卖出侧分数为负，统一走基类折算（夹到 0~5）
                extra["star"] = self.score_to_star(score)
                extra["reason"] = (
                    f"{self.user_strategy_name},卖出分{score:.2f} <= {sell_threshold:.2f}："
                    + ("；".join(reasons) if reasons else "无明细")
                )
                return True, extra
            extra["reason"] = (
                f"{self.user_strategy_name}, 卖出分{score:.2f} 未达阈值 {sell_threshold:.2f}"
                + (f"（{'；'.join(reasons)}）" if reasons else "")
            )
            return False, extra
        except Exception as exc:
            self.logger.warning(
                "[%s] _check_sell_conditions(%s) error: %s",
                self.user_strategy_name, code, exc,
            )
            return False, {"reason": f"检查异常: {exc}", "code": code}

    # ---- 内部辅助 ----

    def _vwap_deviation(self, df: pd.DataFrame) -> float:
        """当前价相对最近 `SELL_VWAP_MINUTES` 根均价（VWAP）的偏离（%）。

        `VwapFactor.score` 返回的就是「(现价 − VWAP) / VWAP × 100」，
        直接复用该因子，避免再写一套均价口径。

        :param df: 含 price/volume 列的分钟行情
        :return: 偏离百分数；数据异常时返回 0.0
        """
        try:
            return float(VwapFactor(days=self.SELL_VWAP_MINUTES).score(df))
        except Exception as exc:
            self.logger.warning("VWAP 因子计算失败: %s", exc)
            return 0.0

    # `_resolve_highest_price` 的实现自 2026-09-29 起在 `BaseStrategy`，本类直接继承。
    # 波段策略也要用同一口径的回撤基准，两处各写一份时查询区间与缓存 key 会漂移，
    # 同一持仓在两个策略里得到不同的回撤数字，而两边日志都「看起来正常」。

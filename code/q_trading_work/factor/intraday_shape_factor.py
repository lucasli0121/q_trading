"""日内形态因子：把当日分钟走势编码成离散形态并打分。

这个因子承担两件事，两者必须共用**同一份**编码实现：

1. 离线统计（`_shape_stats.py`）：按形态分组看次日超额收益，判断哪些形态有效
2. 盘中打分（成交量策略买入侧）：把截至当前的形态换算成买入门槛偏移

分成两份实现会让统计口径与策略口径静默漂移，表现为「统计说有效、策略上无效」，
而两边日志都正常。项目里「选股窗口三处同值」踩过同型问题，因此这里集中到一处。

## 一、编码口径

日线字段（mongo 与 HTTP 接口同名）：

    open            当日开盘价，全天所有 bar 同值
    high / low      当日累计最高/最低，随 bar 单调扩展
    close           该 bar 的收盘价，只有它逐 bar 变化
    preclose        前收盘
    change_percent  close / preclose - 1，**小数比率**

所以日内路径只能用 `close` 序列刻画，用 `open` 会得到常量。

四个量：

    gap        = 当日开盘 / 前收 - 1
    trend      = 当日收盘 / 当日开盘 - 1
    close_pos  = (收盘 - 最低) / (最高 - 最低)
    shape      = 四段符号拼接，段收益 = 段末 close / 上段末 close - 1

四段取 1 小时而非更细：符号序列取值数是 2^段数，8 段得 256 种组合，
样本摊下来每种只剩几个观测。4 段得 16 种，与可用交易日数匹配。

## 一·补：为什么还要「峰值位置」与「回落深度」

`gap` 只回答「开盘价相对昨收高不高」，它判不出「高开低走」。反例：平开或低开，
9:30–10:30 冲高形成全天最高点，之后一路震荡下行，收盘低于开盘。这种走势
`gap` 是「平开」甚至「低开」，但交易含义与高开低走一样——上方有派发。

所以再补两个量，都从 close 序列取：

    peak_seg        = argmax(close) 落在哪一段（0..3）
    peak_rise       = 当日最高 close / 当日开盘 - 1
    fall_from_peak  = 当日收盘 / 当日最高 close - 1（<= 0）

分钟表的 `high` 是当日累计值（每根都等于「截至该 bar 的最高」），它的 argmax
定位不了时刻；只有 `close` 逐 bar 变化，因此峰值一律用 close 序列的 argmax。
代价是忽略 bar 内的瞬时高点（2 分钟一根，误差有限），换来峰值时刻可比。

`dive`（冲高回落）= `peak_seg <= 1`（早盘或上午尾见高）且 `peak_rise >= 0.5%`
且 `fall_from_peak <= -2.5%`。它才是用户所说的「高开低走」，与 gap 的符号无关。

`peak_rise` 这一条不能省：只看「峰值在首段 + 回撤深」，会把「开盘即最高、
之后一路阴跌」也判成冲高回落（它的 peak_seg 同样是首段、回撤同样很深），
结果是所有单边下跌日都变成「高开低走」，「低开低走」这个标签几乎取不到。

## 一·补二：日形态标签与跨日序列

`day_type` 是**完整模式**下的一天一个标签，互斥且只在收盘后产出：
高开低走（含 dive，不看 gap）/ 低开低走 / 走弱 / 低开高走 / 高开高走 / 走强 / 震荡。

跨日序列用它：昨日及更早的形态是**隔夜已定型的信息**，盘中任何时刻都拿得到，
不存在前缀模式那种「上午还没走完」的问题。因此它比当日前缀形态更适合做规则，
样本量也不受「前 N 段是否走完」筛选影响。

`sequence_features()` 只做纯计算（标签串 → 连续弱势天数 / 衰减弱势分），
读盘与缓存由调用方负责。

## 一·补三：跨日趋势判据（均线与高点序列）

日形态标签回答「昨天是什么形状」，不回答「这只股票最近几天整体在走弱」。
用户（2026-10-19）补的两个判据都基于日线序列，与日内路径无关：

    ① 回看窗口内，收盘价低于 ma_period 日均线的次数 —— 越多越弱
    ② 回看窗口内，当日最高价低于前一日最高价的次数 —— 越多越弱

走强侧对称：高于均线的次数、高于前日高点的次数。两边相等（收在均线上、
高点打平）不计入任何一侧。

与标签序列的分工：标签是「形状」，趋势判据是「斜率」。一只股票可以连续几天
都是「震荡」标签，同时收盘价一路低于 5 日线 —— 后者才是「连续走弱」的直接度量。

`trend_features()` 只做纯计算（日线序列 → 四个计数），读盘由调用方负责。
均线按**可用K线的位置**算，不按自然日对齐：停牌日不在序列里，取该日之前
最近 ma_period 根。因此传入的序列应覆盖「回看窗口 + ma_period - 1」天，
窗口内最早那天才算得出均线。

## 二、完整模式与前缀模式

- **完整模式**（`as_of` 为空）：要求末根 >= 14:30，用于离线统计
- **前缀模式**（传 `as_of`）：只用 <= as_of 的 bar，用于盘中

前缀模式下末根所在的那一段是「进行中」，仍给真实符号但不计入 `shape`（`complete=False`）；
尚未开始或段内无数据的段用 `_` 占位，记入 `prefix_shape`。例如 11:00 时
`prefix_shape="+0__"`，表示早盘涨、上午尾走平、午后与尾盘未知。

前缀模式是实盘唯一可得的形态。盘中 11:00 时不存在「午后怎么走」这个信息，
统计若只用完整形态，得出的结论在实盘拿不到。因此统计侧也必须能按前缀口径跑。

## 三、算不出来 != 中性

覆盖度不足时 `calculate()` 返回 `data_ok=False`，而不是给中性分。
成交量因子踩过这个坑：0 分在 reversion 模式下会被读成「极缩量」并给满分买点。
形态侧同理，数据不足不能当成「形态中性所以放行」。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from factor.base_factor import BaseFactor

# 交易时段分段（HHMM），早盘 / 上午尾 / 午后 / 尾盘各一小时
SEGMENTS: tuple[tuple[int, int], ...] = (
    (930, 1030),
    (1030, 1130),
    (1300, 1400),
    (1400, 1500),
)

# 时间列候选名：归档 parquet 与 mongo 用 minute_time，实时行情用 create_time，
# 回测构造的部分 DataFrame 用 date。三者语义相同，只取第一个非空值。
TIME_FIELDS: tuple[str, ...] = ("minute_time", "create_time", "date")

# 前缀模式下「段没有数据」的占位符：段尚未开始，或段已开始但区间内没有 bar
SYMBOL_UNKNOWN = "_"

GAP_FLAT = 0.005  # 开盘缺口 ±0.5% 以内算平开
TREND_FLAT = 0.005  # 日内涨跌 ±0.5% 以内算走平
SEG_FLAT = 0.002  # 单段涨跌 ±0.2% 以内算不动
PEAK_RISE_MIN = 0.005  # 峰值至少比开盘高 0.5% 才算「冲高」（区分开盘即最高）
FALL_DEEP = 0.025  # 收盘距日内最高 close 回撤 >= 2.5% 算「深回落」
FALL_SHALLOW = 0.003  # 回撤 >= 0.3% 但不到深回落算「浅回落」
# 跨日趋势判据的均线周期：回看窗口内「收盘价低于/高于 N 日均线」计次。
# 周期只影响数据窗口长度（lookback + N - 1 天），不影响其它编码。
MA_PERIOD = 5
# 2.5% 的来历：1549 个「股票-日」样本里，从日内最高 close 回撤的 25 分位是
# −3.37%、中位数 −1.77%。取 1% 会让 54.5% 的样本都算「冲高回落」，这个标签
# 就退化成「今天不是单边上涨」，失去区分度；取 2.5% 命中 33.6%，与「上方有
# 派发」的直觉也一致。阈值是分位数口径，换股票池后应重算。

# 日形态标签。互斥优先级见 `_classify_day`。
#
# 「高开低走」不要求 gap > 0：早盘/上午见高、收盘明显低于峰值且当日下跌，
# 即使开盘价低于昨收也算。这是本次口径修正的核心，见模块 docstring 一·补。
DAY_TYPE_HIGH_LOW = "高开低走"
DAY_TYPE_LOW_LOW = "低开低走"
DAY_TYPE_WEAK = "走弱"
DAY_TYPE_LOW_HIGH = "低开高走"
DAY_TYPE_HIGH_HIGH = "高开高走"
DAY_TYPE_STRONG = "走强"
DAY_TYPE_FLAT = "震荡"

# 「弱势」标签集合：次日买入预期应下调的形态。
WEAK_DAY_TYPES: frozenset[str] = frozenset(
    {DAY_TYPE_HIGH_LOW, DAY_TYPE_LOW_LOW, DAY_TYPE_WEAK}
)
# 「强势」标签集合：只用于观测与统计对照，不参与默认打分。
STRONG_DAY_TYPES: frozenset[str] = frozenset(
    {DAY_TYPE_LOW_HIGH, DAY_TYPE_HIGH_HIGH, DAY_TYPE_STRONG}
)


def to_hhmm(stamp: Any) -> int:
    """把时间戳转成 HHMM 整数；无法解析返回 -1。

    支持 `YYYY-MM-DD HH:MM:SS`、`YYYY-MM-DD HH:MM`、`HH:MM`、`HH:MM:SS`。
    解析失败返回 -1 而不是抛异常：调用点遍布分钟缓存与归档数据，
    一条脏记录不该让整只股票当日无法编码。
    """
    text = str(stamp or "").strip()
    if not text:
        return -1
    if len(text) >= 16 and text[2] == ":":
        # HH:MM:SS 或 HH:MM
        head = text[:5]
    elif len(text) >= 16:
        head = text[11:16]
    elif len(text) >= 5 and text[2] == ":":
        head = text[:5]
    else:
        return -1
    try:
        hour, minute = head.split(":")[:2]
        hour_i = int(hour)
        minute_i = int(minute)
    except (ValueError, IndexError):
        return -1
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        return -1
    return hour_i * 100 + minute_i


class IntradayShapeFactor(BaseFactor):
    """日内形态因子。

    打分口径：中性 5.0 分起，按命中规则加减，夹到 [0, 10]。
    规则表默认为空（恒中性），由调用方按统计结论填充，见 `STRUCTURE_RULES`。

    **为什么默认中性**：41 个交易日（2026-07-17~10-13，1563 个股票-日）的统计里，
    单维形态的 |t| 全部小于 1，四段路径只有 5 个 |t| > 2 的候选且日度数仅 8~15 天，
    前后半段切分后没有一个形态符号稳定。在样本不足时给出分数，等于把噪声接进策略。
    默认规则见 `DEFAULT_STRUCTURE_RULES`，它是「宁可少加分、只轻微收紧」的版本。
    """

    factor_name: str = "IntradayShapeFactor"

    # 形态分的中枢。规则表的加减分都相对它，也是「形态中性」的取值。
    NEUTRAL_SCORE: float = 5.0

    # 结构性规则：(缺口档, 日内方向档, 收盘位置档) -> 加减分。**默认关闭**。
    #
    # 完整口径下确实有几组负超额（2026-07-17~10-13，日度数 9~16）：
    #
    #   高开|走平|收在下沿   -1.201%（t=-1.14）
    #   高开|走平|收在中部   -1.123%（t=-1.12）
    #   平开|走弱|收在中部   -0.789%（t=-2.09）
    #   低开|走平|收在下沿   -0.570%（t=-1.35）
    #
    # 但**盘中用不了**：`trend_b` 与 `cp_b` 是「截至当前」的统计量，10:30 与
    # 14:30 算出来完全不同，同一只股票会在盘中不同时刻命中不同规则，而统计
    # 只对应收盘那一刻。启用它等于用收盘结论解释盘中数据，口径不一致。
    # 真要用，应改成按前缀符号统计（见 `--prefix-upto`），不是直接填这张表。
    DEFAULT_STRUCTURE_RULES: dict[tuple[str, str, str], float] = {}

    # 路径规则：形态符号（如 "+--+"）-> 加减分。默认空。
    # 键可以是完整四段（"+--+"），也可以是前缀（"+0" 表示「早盘涨、上午尾走平」
    # 就走完了两段）。匹配时按从长到短尝试，见 `_shape_candidates`。
    #
    # 不预置任何路径规则的理由：四段路径的候选（"+-++" -1.54%、"-000" -1.32%、
    # "++++" -1.21%）日度数都只有 8~15 天，前后半段稳定性检验全部不通过。
    DEFAULT_PATH_RULES: dict[str, float] = {}

    # 冲高回落规则：回落档 -> 加减分，仅当当日 `dive=True` 时生效。默认空。
    #
    # `dive` = 早盘/上午见高 + 峰值高于开盘 ≥ `peak_rise_min` + 回撤 ≥ `fall_deep`。
    # 这是「高开低走」的正确判据所在：不看 gap，只看路径。
    # 盘中可用但**可撤销**：后续若创新高，峰值会前移、回撤会变浅，
    # 该规则随即不再命中。属单向收紧（命中后比以前更难买），可接受。
    DEFAULT_DIVE_RULES: dict[str, float] = {}

    # 跨日规则：(昨日 day_type) -> 加减分。
    DEFAULT_PREV_DAY_RULES: dict[str, float] = {}

    # 跨日规则：(连续弱势天数下限) -> 加减分。取满足条件的最大键。
    # 例：{2: -1.0, 3: -2.0} 表示连弱 2 天 −1.0、连弱 3 天及以上 −2.0。
    DEFAULT_WEAK_STREAK_RULES: dict[int, float] = {}

    # 跨日规则：标签串（昨日在前、`|` 连接，如 "低开低走|高开低走|低开高走"）
    # -> 加减分。按从长到短匹配。**不预置**：标签有 6 种，三天组合即 216 种，
    # 40 个交易日摊不开，枚举组合必然过拟合。粗粒度用连续弱势天数即可。
    DEFAULT_SEQUENCE_RULES: dict[str, float] = {}

    # 跨日趋势规则：(次数下限) -> 加减分，取满足条件的最大键，语义同
    # `weak_streak_rules`。四个计数来自 `trend_features`：
    #   below_ma     回看窗口内收盘低于 ma_period 日均线的天数（弱）
    #   above_ma     收盘高于均线的天数（强）
    #   lower_high   当日最高价低于前一日最高价的天数（弱）
    #   higher_high  当日最高价高于前一日最高价的天数（强）
    # **默认空**：判据 2026-10-19 提出，尚未做统计验证。同型的「连弱标签」
    # 统计方向恰好与先验相反（连弱后偏反弹），因此先只算不判。
    DEFAULT_BELOW_MA_RULES: dict[int, float] = {}
    DEFAULT_ABOVE_MA_RULES: dict[int, float] = {}
    DEFAULT_LOWER_HIGH_RULES: dict[int, float] = {}
    DEFAULT_HIGHER_HIGH_RULES: dict[int, float] = {}

    def __init__(
        self,
        min_bars: int = 20,
        gap_flat: float = GAP_FLAT,
        trend_flat: float = TREND_FLAT,
        seg_flat: float = SEG_FLAT,
        max_first_hhmm: int = 1000,
        min_last_hhmm: int = 1430,
        structure_rules: dict[tuple[str, str, str], float] | None = None,
        path_rules: dict[str, float] | None = None,
        rule_segments: int = 2,
        fall_deep: float = FALL_DEEP,
        fall_shallow: float = FALL_SHALLOW,
        peak_rise_min: float = PEAK_RISE_MIN,
        early_peak_seg: int = 1,
        seq_decay: float = 0.6,
        prev_day_rules: dict[str, float] | None = None,
        weak_streak_rules: dict[int, float] | None = None,
        sequence_rules: dict[str, float] | None = None,
        dive_rules: dict[str, float] | None = None,
        ma_period: int = MA_PERIOD,
        below_ma_rules: dict[int, float] | None = None,
        above_ma_rules: dict[int, float] | None = None,
        lower_high_rules: dict[int, float] | None = None,
        higher_high_rules: dict[int, float] | None = None,
    ) -> None:
        """初始化形态因子。

        :param min_bars: 最少 bar 数，低于此值直接判「算不出来」
        :param gap_flat: 开盘缺口分档阈值（小数比率）
        :param trend_flat: 日内方向分档阈值
        :param seg_flat: 单段分档阈值
        :param max_first_hhmm: 完整模式下首根不得晚于此时刻
        :param min_last_hhmm: 完整模式下末根不得早于此时刻
        :param structure_rules: 结构性规则表，None 用 `DEFAULT_STRUCTURE_RULES`
        :param path_rules: 路径规则表，None 用 `DEFAULT_PATH_RULES`
        :param rule_segments: 路径规则与结构规则使用前几段符号。默认 2，即
            「前两段走完」才判分（11:30 定型）。理由见 `_adjust`。
        :param fall_deep: 深回落阈值（从日内最高 close 回撤）
        :param fall_shallow: 浅回落阈值
        :param peak_rise_min: 峰值高于开盘多少才算「冲高」（区分开盘即最高）
        :param early_peak_seg: 峰值不晚于第几段算「早盘见高」。默认 1，即上午
            收盘前见高（覆盖 9:30–11:30）。要严格按 9:30–10:30 判，传 0。
        :param seq_decay: 跨日弱势分的日衰减系数（昨日权重 1，前一日 decay，…）
        :param prev_day_rules: 昨日形态规则表
        :param weak_streak_rules: 连续弱势天数规则表
        :param sequence_rules: 标签串规则表
        :param dive_rules: 冲高回落规则表
        :param ma_period: 跨日趋势判据的均线周期
        :param below_ma_rules: 「收盘低于均线次数」规则表（弱）
        :param above_ma_rules: 「收盘高于均线次数」规则表（强）
        :param lower_high_rules: 「最高价低于前日次数」规则表（弱）
        :param higher_high_rules: 「最高价高于前日次数」规则表（强）
        """
        self.min_bars: int = int(min_bars)
        self.gap_flat: float = float(gap_flat)
        self.trend_flat: float = float(trend_flat)
        self.seg_flat: float = float(seg_flat)
        self.max_first_hhmm: int = int(max_first_hhmm)
        self.min_last_hhmm: int = int(min_last_hhmm)
        self.rule_segments: int = max(int(rule_segments), 0)
        self.fall_deep: float = abs(float(fall_deep))
        self.fall_shallow: float = abs(float(fall_shallow))
        self.peak_rise_min: float = abs(float(peak_rise_min))
        self.early_peak_seg: int = max(int(early_peak_seg), 0)
        self.seq_decay: float = min(max(float(seq_decay), 0.0), 1.0)
        self.structure_rules: dict[tuple[str, str, str], float] = dict(
            self.DEFAULT_STRUCTURE_RULES if structure_rules is None else structure_rules
        )
        self.path_rules: dict[str, float] = dict(
            self.DEFAULT_PATH_RULES if path_rules is None else path_rules
        )
        self.prev_day_rules: dict[str, float] = dict(
            self.DEFAULT_PREV_DAY_RULES if prev_day_rules is None else prev_day_rules
        )
        self.weak_streak_rules: dict[int, float] = dict(
            self.DEFAULT_WEAK_STREAK_RULES
            if weak_streak_rules is None
            else weak_streak_rules
        )
        self.sequence_rules: dict[str, float] = dict(
            self.DEFAULT_SEQUENCE_RULES if sequence_rules is None else sequence_rules
        )
        self.dive_rules: dict[str, float] = dict(
            self.DEFAULT_DIVE_RULES if dive_rules is None else dive_rules
        )
        self.ma_period: int = max(int(ma_period), 1)
        self.below_ma_rules: dict[int, float] = dict(
            self.DEFAULT_BELOW_MA_RULES if below_ma_rules is None else below_ma_rules
        )
        self.above_ma_rules: dict[int, float] = dict(
            self.DEFAULT_ABOVE_MA_RULES if above_ma_rules is None else above_ma_rules
        )
        self.lower_high_rules: dict[int, float] = dict(
            self.DEFAULT_LOWER_HIGH_RULES if lower_high_rules is None else lower_high_rules
        )
        self.higher_high_rules: dict[int, float] = dict(
            self.DEFAULT_HIGHER_HIGH_RULES
            if higher_high_rules is None
            else higher_high_rules
        )

    # ---- 分档 ----

    def _bucket_gap(self, value: float) -> str:
        if value > self.gap_flat:
            return "高开"
        if value < -self.gap_flat:
            return "低开"
        return "平开"

    def _bucket_trend(self, value: float) -> str:
        if value > self.trend_flat:
            return "走强"
        if value < -self.trend_flat:
            return "走弱"
        return "走平"

    @staticmethod
    def _bucket_close_pos(value: float) -> str:
        if value > 0.66:
            return "收在上沿"
        if value < 0.33:
            return "收在下沿"
        return "收在中部"

    def _bucket_seg(self, value: float) -> str:
        if value > self.seg_flat:
            return "+"
        if value < -self.seg_flat:
            return "-"
        return "0"

    def _bucket_fall(self, value: float) -> str:
        """从日内最高 close 的回撤分档。非有限值返回 `_`（算不出来，不是「没回落」）。"""
        if not math.isfinite(value):
            return SYMBOL_UNKNOWN
        if value <= -self.fall_deep:
            return "深回落"
        if value <= -self.fall_shallow:
            return "浅回落"
        return "高位"

    @staticmethod
    def is_weak(day_type: str) -> bool:
        """该日形态是否属于「弱势」（次日买入预期应下调）。"""
        return day_type in WEAK_DAY_TYPES

    def _classify_day(
        self,
        gap: float,
        trend: float,
        dive: bool,
        closed: bool,
    ) -> str:
        """把一天的走势归到一个互斥标签。

        判定顺序是「先看收盘方向，再看路径与缺口」：

        1. 收盘下跌（`trend <= -trend_flat`）
           - 早盘/上午见高且从峰值回撤 >= `fall_deep` ⇒ **高开低走**
             （**不看 gap**，平开或低开后冲高再跌，同样计入）
           - 否则按缺口分：低开 ⇒ 低开低走 / 高开 ⇒ 高开低走 / 平开 ⇒ 走弱
        2. 收盘上涨 ⇒ 低开高走 / 高开高走 / 走强
        3. 其余 ⇒ 震荡

        「高开低走」排最前是刻意的：它是「上方派发」的直接证据，比单纯的
        「低开低走」更值得降预期。用户举的反例（平开、9:30–10:30 冲高、
        随后一路下跌、收盘低于开盘）正是靠这一条才能被正确归类。

        :param closed: 该日是否已收盘（末根 >= `min_last_hhmm`）。未收盘时
            峰值可能后移、回撤可能变浅，此时的标签不算「这一天的形态」。
        """
        if not closed:
            return ""
        if trend <= -self.trend_flat:
            if dive:
                return DAY_TYPE_HIGH_LOW
            if gap <= -self.gap_flat:
                return DAY_TYPE_LOW_LOW
            if gap >= self.gap_flat:
                return DAY_TYPE_HIGH_LOW
            return DAY_TYPE_WEAK
        if trend >= self.trend_flat:
            if gap <= -self.gap_flat:
                return DAY_TYPE_LOW_HIGH
            if gap >= self.gap_flat:
                return DAY_TYPE_HIGH_HIGH
            return DAY_TYPE_STRONG
        return DAY_TYPE_FLAT

    # ---- 输入规整 ----

    @staticmethod
    def _rows(bars: Any) -> list[dict[str, Any]]:
        """把 DataFrame / dict 列表统一成 dict 列表。"""
        if isinstance(bars, pd.DataFrame):
            if bars.empty:
                return []
            return bars.to_dict("records")
        if isinstance(bars, dict):
            return [bars]
        try:
            return [dict(item) for item in bars]
        except TypeError:
            return []

    @staticmethod
    def _stamp(row: dict[str, Any]) -> str:
        for field in TIME_FIELDS:
            value = row.get(field)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _number(row: dict[str, Any], field: str) -> float:
        """取数值字段，缺失或非法返回 nan（不抛异常）。"""
        value = row.get(field)
        if value is None:
            return float("nan")
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return result

    # ---- 核心编码 ----

    def encode(
        self, bars: Any, as_of: str = "", require_segments: int = 0
    ) -> dict[str, Any] | None:
        """把分钟 bar 编码成形态特征。

        :param bars: 分钟行情，DataFrame 或 dict 列表；顺序不限
        :param as_of: 截止时刻（HH:MM 或完整时间戳）。空表示用全部 bar（完整模式），
            非空表示只用 <= 该时刻的 bar（前缀模式，盘中实盘用）
        :param require_segments: 要求前 N 段已经走完，不足时返回 None。
            用于「按前 N 段符号做规则」的场景：前两段要到 11:30 才定型，
            在那之前拿它跟历史统计比是拿半截数据比完整数据。
        :return: 特征字典；数据不足或价格异常时返回 None
        """
        rows = self._rows(bars)
        if as_of:
            cutoff = to_hhmm(as_of)
            if cutoff < 0:
                return None
            rows = [row for row in rows if 0 <= to_hhmm(self._stamp(row)) <= cutoff]

        if len(rows) < self.min_bars:
            # 太少的 bar 连开盘半小时都盖不住，形状没有意义
            return None

        rows.sort(key=lambda row: self._stamp(row))

        first_hhmm = to_hhmm(self._stamp(rows[0]))
        last_hhmm = to_hhmm(self._stamp(rows[-1]))
        if first_hhmm < 0 or last_hhmm < 0:
            return None

        # 覆盖度闸门。mongo 的分钟数据按订阅累积，实测 2026-07-24 那天 488 只股票
        # 的 bar 数中位数只有 5 根。只看半天的数据谈不上「日内路径」。
        #
        # 前缀模式不检查末根时刻：盘中 11:00 时末根本来就在 11:00 附近，
        # 那是预期行为而不是数据缺陷。此时首根仍须覆盖开盘。
        if first_hhmm > self.max_first_hhmm:
            return None
        if not as_of and last_hhmm < self.min_last_hhmm:
            return None

        preclose = self._number(rows[0], "preclose")
        if not (preclose > 0):
            return None

        day_open = self._number(rows[0], "open")
        if not (day_open > 0):
            return None

        last_row = rows[-1]
        day_close = self._number(last_row, "close")
        day_high = self._number(last_row, "high")
        day_low = self._number(last_row, "low")
        if not (day_close > 0 and day_high > 0 and day_low > 0):
            return None

        gap = day_open / preclose - 1.0
        trend = day_close / day_open - 1.0
        span = day_high - day_low
        # 一字板 high == low，跨度为零。取 0.5 落「收在中部」，
        # 这是有意的：一字板的形态信息主要来自 gap，不该由 close_pos 再判一次。
        close_pos = (day_close - day_low) / span if span > 0 else 0.5

        # 日内峰值定位。分钟表的 high 是当日累计值（每根都等于「截至该 bar 的
        # 最高」），argmax 永远落在末根，定位不到时刻；只有 close 逐 bar 变化，
        # 所以峰值用 close 序列的 argmax。见模块 docstring 一·补。
        peak_hhmm: int = -1
        peak_price: float = float("nan")
        peak_seg: int = -1
        for row in rows:
            price = self._number(row, "close")
            if price > 0 and not (math.isfinite(peak_price) and price <= peak_price):
                peak_price = price
                peak_hhmm = to_hhmm(self._stamp(row))
        for index, (seg_start, seg_end) in enumerate(SEGMENTS):
            if seg_start <= peak_hhmm < seg_end:
                peak_seg = index
                break
        fall_from_peak: float = (
            day_close / peak_price - 1.0 if math.isfinite(peak_price) and peak_price > 0
            else float("nan")
        )
        # 峰值相对开盘的涨幅。用来区分「冲高后回落」与「开盘即最高、一路阴跌」：
        # 后者的 peak_seg 也是首段、fall_from_peak 同样很深，但它没有「冲高」，
        # 混为一谈会让所有单边下跌日都变成「高开低走」。
        peak_rise: float = peak_price / day_open - 1.0 if math.isfinite(peak_price) else float("nan")

        segment_returns: list[float] = []
        segment_symbols: list[str] = []
        prev_close = day_open
        # 末根所在的那一段是「进行中」：符号此刻有值，但收盘前还会变。
        # 它与「段未开始 / 段内无数据」是两回事，后者没有任何信息。
        partial_index: int = -1

        for index, (seg_start, seg_end) in enumerate(SEGMENTS):
            if last_hhmm < seg_start:
                # 该段还没开始（前缀模式下的后续时段）
                segment_returns.append(float("nan"))
                segment_symbols.append(SYMBOL_UNKNOWN)
                continue
            prices = [
                self._number(row, "close")
                for row in rows
                if seg_start <= to_hhmm(self._stamp(row)) < seg_end
            ]
            prices = [price for price in prices if price > 0]
            if not prices:
                # 段已开始但区间内没有 bar，属数据缺失，同样不给符号
                segment_returns.append(float("nan"))
                segment_symbols.append(SYMBOL_UNKNOWN)
                continue
            segment_returns.append(prices[-1] / prev_close - 1.0)
            segment_symbols.append(self._bucket_seg(segment_returns[-1]))
            prev_close = prices[-1]
            if last_hhmm < seg_end:
                partial_index = index

        features: dict[str, Any] = {
            "gap": gap,
            "trend": trend,
            "close_pos": close_pos,
            "gap_b": self._bucket_gap(gap),
            "trend_b": self._bucket_trend(trend),
            "cp_b": self._bucket_close_pos(close_pos),
            "peak_hhmm": peak_hhmm,
            "peak_seg": peak_seg,
            "peak_price": peak_price,
            "fall_from_peak": fall_from_peak,
            "fall_b": self._bucket_fall(fall_from_peak),
            "peak_rise": peak_rise,
            "early_peak": bool(0 <= peak_seg <= self.early_peak_seg),
            "dive": bool(
                0 <= peak_seg <= self.early_peak_seg
                and math.isfinite(peak_rise)
                and peak_rise >= self.peak_rise_min
                and math.isfinite(fall_from_peak)
                and fall_from_peak <= -self.fall_deep
            ),
            "bars": len(rows),
            "first_hhmm": first_hhmm,
            "last_hhmm": last_hhmm,
            "as_of": as_of,
        }
        for index, value in enumerate(segment_returns):
            features[f"seg{index}"] = value
            features[f"seg{index}_b"] = segment_symbols[index]

        # 连续走完的段数：遇到进行中的段或缺失段就停。
        # 「前 N 段符号」是实盘唯一时间稳定的形态口径：前两段在 11:30 定型后
        # 不再变化，而 trend / close_pos 这类全量统计量在盘中每个时刻都不同。
        limit = partial_index if partial_index >= 0 else len(segment_symbols)
        completed = 0
        for index in range(limit):
            if segment_symbols[index] in ("+", "-", "0"):
                completed += 1
            else:
                break

        # 完整形态只由四个 + / - / 0 组成；末段仍在进行时只是前缀，不算完整。
        # `shape` 用于统计分组，宁可留空也不能把「还没走完」当成「已经走完」。
        features["partial_index"] = partial_index
        features["completed_segments"] = completed
        features["complete"] = completed == len(SEGMENTS)
        features["shape"] = "".join(segment_symbols) if features["complete"] else ""
        features["prefix_shape"] = "".join(segment_symbols)
        # 日形态标签的判据是「这一天走完了没有」，不是「四段符号齐不齐」。
        # 用 `complete` 会让末根停在 14:30~14:59 的样本全部无标签（实测 45%），
        # 而 14:30 之后的走势基本不会改变峰值位置与回撤深度。完整模式下
        # `encode` 已强制末根 >= `min_last_hhmm`，这里只需排除前缀模式。
        features["day_type"] = self._classify_day(
            gap=gap,
            trend=trend,
            dive=bool(features["dive"]),
            closed=bool(not as_of and last_hhmm >= self.min_last_hhmm),
        )

        if completed < require_segments:
            return None
        return features

    # ---- 跨日序列 ----

    def sequence_features(self, labels: Any) -> dict[str, Any]:
        """把「最近若干天的日形态标签」压成序列特征。

        :param labels: 日形态标签序列，**昨日在前**。空串表示该日算不出来
            （缺数据），会中断连续计数而不是被跳过——不知道那天是什么形态，
            就不能假定它延续了前一天的弱势。
        :return: 序列特征字典
        """
        try:
            raw = [str(item or "") for item in labels]
        except TypeError:
            raw = []

        weak_streak = 0
        strong_streak = 0
        for label in raw:
            if not label or not self.is_weak(label):
                break
            weak_streak += 1
        for label in raw:
            if not label or label not in STRONG_DAY_TYPES:
                break
            strong_streak += 1

        # 衰减弱势分：昨日权重 1，前一日 decay，再前一日 decay²，……
        # 空串不计分（缺数据不等于不走弱），但权重位置照常按时间推进，
        # 否则「中间缺两天」会被当成「连着两天」。
        decay_weak = 0.0
        decay_strong = 0.0
        for index, label in enumerate(raw):
            if not label:
                continue
            weight = self.seq_decay**index
            if self.is_weak(label):
                decay_weak += weight
            elif label in STRONG_DAY_TYPES:
                decay_strong += weight

        return {
            "labels": raw,
            "seq": "|".join(raw),
            "days": len(raw),
            "prev_day_type": raw[0] if raw else "",
            "weak_streak": weak_streak,
            "strong_streak": strong_streak,
            "decay_weak": round(decay_weak, 3),
            "decay_strong": round(decay_strong, 3),
        }

    # ---- 跨日趋势（均线与高点序列）----

    def trend_features(self, daily: Any, window: int = 0) -> dict[str, Any]:
        """把日线序列压成「连续走弱/走强」的四个计数。

        判据（用户 2026-10-19 提出，与开盘缺口、日内路径都无关）：

        ① 收盘价低于 `ma_period` 日均线的天数 —— 弱侧证据
        ② 当日最高价低于前一日最高价的天数 —— 弱侧证据

        走强侧对称（高于均线、高于前日高点）。两边相等不计入任何一侧：
        收在均线上说明多空均衡，不是「偏强」也不是「偏弱」。

        :param daily: 日线序列，**升序**（最早在前）。每项至少含 `close` / `high`，
            缺字段、非数值或非正数的条目整体跳过。停牌日天然不在序列里。
        :param window: 只统计序列末尾最近这么多天的计数；0 或负数 = 全部。
            调用方传回看天数（lookback）。序列应比 window 长 `ma_period - 1` 天，
            窗口内最早那天才算得出均线；不够时那些天只参与「低于前日高点」
            计数，均线计数跳过（不是判成「在均线上」）。
        :return: 计数字典。窗口内没有可评估日时 `days=0`、各计数为 0，
            调用方据此走 fail-open，不当作「不弱」。
        """
        rows: list[tuple[float, float]] = []
        if isinstance(daily, (list, tuple)):
            for item in daily:
                if not isinstance(item, dict):
                    continue
                try:
                    close = float(item.get("close"))  # type: ignore[arg-type]
                    high = float(item.get("high"))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if close <= 0 or high <= 0:
                    continue
                rows.append((close, high))

        total: int = len(rows)
        if total == 0:
            return {
                "days": 0,
                "below_ma": 0,
                "above_ma": 0,
                "lower_high": 0,
                "higher_high": 0,
                "ma_period": self.ma_period,
            }

        span: int = int(window) if int(window or 0) > 0 else total
        start: int = max(total - span, 0)
        period: int = self.ma_period

        below_ma: int = 0
        above_ma: int = 0
        lower_high: int = 0
        higher_high: int = 0
        for index in range(start, total):
            close, high = rows[index]
            if index - period + 1 >= 0:
                window_rows = rows[index - period + 1 : index + 1]
                moving = sum(item[0] for item in window_rows) / len(window_rows)
                if close < moving:
                    below_ma += 1
                elif close > moving:
                    above_ma += 1
            if index > 0:
                previous_high = rows[index - 1][1]
                if high < previous_high:
                    lower_high += 1
                elif high > previous_high:
                    higher_high += 1

        return {
            "days": total - start,
            "below_ma": below_ma,
            "above_ma": above_ma,
            "lower_high": lower_high,
            "higher_high": higher_high,
            "ma_period": period,
        }

    def _adjust_trend(self, trend: dict[str, Any]) -> tuple[float, list[str]]:
        """按四张趋势规则表算加减分。

        每张表独立命中、按序累加，键的语义与 `weak_streak_rules` 一致：
        次数下限，取满足条件的最大键。例 `{2: -1.0, 4: -2.0}` 表示
        「低于均线 2~3 次 −1.0、4 次及以上 −2.0」。
        """
        adjust: float = 0.0
        reasons: list[str] = []
        tables: tuple[tuple[str, dict[int, float], str], ...] = (
            ("below_ma", self.below_ma_rules, f"收于{self.ma_period}日线下"),
            ("above_ma", self.above_ma_rules, f"收于{self.ma_period}日线上"),
            ("lower_high", self.lower_high_rules, "高点低于前日"),
            ("higher_high", self.higher_high_rules, "高点高于前日"),
        )
        for field, rules, label in tables:
            count: int = int(trend.get(field, 0) or 0)
            if count <= 0 or not rules:
                continue
            hits = [key for key in rules if key <= count]
            if not hits:
                continue
            value: float = rules[max(hits)]
            if value:
                adjust += value
                reasons.append(f"{label}{count}次{value:+.1f}")
        return adjust, reasons

    def _adjust_sequence(self, sequence: dict[str, Any]) -> tuple[float, list[str]]:
        """按跨日规则表算加减分。

        三张表各自独立命中，按序累加（都是「形态风险」的同一方向，且默认表很小）：
        ① 标签串精确匹配（从长到短）② 昨日形态 ③ 连续弱势天数（取满足的最大键）。
        """
        adjust = 0.0
        reasons: list[str] = []
        seq_text: str = str(sequence.get("seq", "") or "")
        labels: list[str] = list(sequence.get("labels", []) or [])

        if self.sequence_rules and seq_text:
            # 从长到短匹配，让更具体的规则优先，避免被短键提前截断
            for length in range(len(labels), 0, -1):
                key = "|".join(labels[:length])
                if key in self.sequence_rules:
                    value = self.sequence_rules[key]
                    if value:
                        adjust += value
                        reasons.append(f"近{length}日{key}{value:+.1f}")
                    break

        prev_type: str = str(sequence.get("prev_day_type", "") or "")
        if prev_type and self.prev_day_rules:
            value = self.prev_day_rules.get(prev_type, 0.0)
            if value:
                adjust += value
                reasons.append(f"昨日{prev_type}{value:+.1f}")

        streak = int(sequence.get("weak_streak", 0) or 0)
        if streak > 0 and self.weak_streak_rules:
            hits = [day for day in self.weak_streak_rules if day <= streak]
            if hits:
                value = self.weak_streak_rules[max(hits)]
                if value:
                    adjust += value
                    reasons.append(f"连续弱势{streak}日{value:+.1f}")

        return adjust, reasons

    # ---- 打分 ----

    def _adjust(self, features: dict[str, Any]) -> tuple[float, list[str]]:
        """按规则表算加减分，返回 (调整量, 命中理由)。

        路径规则只用**前 `rule_segments` 段**的符号做键。这个口径是刻意的：

        - `gap_b` 盘中稳定（当日开盘价与前收固定），但 `trend_b` / `close_pos`
          是「截至当前」的统计量，10:30 与 14:30 算出来完全不同
        - 用三元组（缺口×方向×位置）当规则键，同一只股票在盘中不同时刻会命中
          不同规则，而历史统计只对应收盘那一刻，规则与统计因此错配
        - 前两段符号在 11:30 定型后不再变化，规则效果与查询时刻无关

        结构规则默认关闭，原因同上：统计得到的三元组结论是收盘口径的，
        盘中用不上。需要时可由调用方显式传入，但要自己承担口径不一致的风险。
        """
        adjust = 0.0
        reasons: list[str] = []

        key = (features["gap_b"], features["trend_b"], features["cp_b"])
        structure = self.structure_rules.get(key, 0.0)
        if structure:
            adjust += structure
            reasons.append(f"{'|'.join(key)}{structure:+.1f}")

        if self.rule_segments > 0:
            head = str(features.get("prefix_shape", ""))[: self.rule_segments]
            if len(head) == self.rule_segments and SYMBOL_UNKNOWN not in head:
                path = self.path_rules.get(head, 0.0)
                if path:
                    adjust += path
                    reasons.append(f"前{self.rule_segments}段{head}{path:+.1f}")

        if self.dive_rules and features.get("dive"):
            fall_b = str(features.get("fall_b", ""))
            dive = self.dive_rules.get(fall_b, 0.0)
            if dive:
                adjust += dive
                reasons.append(f"冲高回落{fall_b}{dive:+.1f}")

        return adjust, reasons

    def calculate(
        self,
        stocks: Any,
        as_of: str = "",
        history: Any = None,
        daily: Any = None,
        trend_window: int = 0,
    ) -> Any:
        """计算当日形态特征与形态分，可选叠加跨日序列。

        当日层（`intraday_*`）与跨日层（`sequence_*`）**分开返回**。合并成一个
        分会让策略无法区分「今天冲高回落」与「昨天起就连弱」，而两者的证据强度、
        可用时点都不同：跨日层开盘即可用（昨日形态已定型），当日层要等前 N 段
        走完。策略侧按 `max(intraday_offset, sequence_offset)` 合并，见
        `VolumeExpansionStrategy._shape_offset`。

        跨日层内部还有两个子源，加减分累加后一并计入 `sequence_adjust`：

        - **标签序列**（`history`）：昨日及更早的 `day_type`，来自分钟归档
        - **趋势序列**（`daily`）：收盘 vs 均线、最高价 vs 前日，来自日K 归档

        两者数据源不同，可能只有其一可用；`trend_adjust` / `below_ma` 等字段
        单独返回，便于分开排查。

        :param stocks: 分钟行情（DataFrame 或 dict 列表）
        :param as_of: 截止时刻，空表示完整模式
        :param history: 最近若干日的 `day_type`，**昨日在前**。空/None 表示不启用
            跨日标签层。只接受已定型的历史标签，调用方不得把当日标签放进第一位。
        :param daily: 最近若干日的日线序列（`close` / `high`），**升序**，末尾是
            昨日。空/None 表示不启用跨日趋势层。序列应比 `trend_window` 长
            `ma_period - 1` 天，使窗口内最早那天也算得出均线。
        :param trend_window: 趋势计数只统计日线序列末尾最近这么多天，传回看天数；
            0 = 用整个序列（离线统计口径）
        :return: 字典，含 features / score / data_ok / reason。
            `data_ok=False` 表示**当日**形态数据不足，**不是**中性。
            `sequence_ok` 单独标记跨日层是否可用。
        """
        trend = self.trend_features(daily, window=trend_window)
        trend_ok: bool = int(trend.get("days", 0) or 0) > 0
        trend_adjust: float = 0.0
        trend_reasons: list[str] = []
        if trend_ok:
            trend_adjust, trend_reasons = self._adjust_trend(trend)

        sequence = self.sequence_features(history if history is not None else [])
        # 跨日层可用 = 有历史标签 **或** 有日线序列：两者数据源不同
        # （分钟归档 vs 日K 归档），可能只有其一可用。
        sequence_ok: bool = (
            any(bool(label) for label in sequence["labels"]) or trend_ok
        )
        sequence_adjust: float = 0.0
        sequence_reasons: list[str] = []
        if sequence_ok:
            sequence_adjust, sequence_reasons = self._adjust_sequence(sequence)
        sequence_total: float = sequence_adjust + trend_adjust
        sequence_score: float = min(
            max(self.NEUTRAL_SCORE + sequence_total, 0.0), 10.0
        )

        features = self.encode(stocks, as_of=as_of)
        # 当日层不可用时仍要返回跨日层结果：早盘前 20 分钟当日形态算不出来，
        # 但「昨天和前天的形态」已经定型，那部分信息是真实可用的。
        base: dict[str, Any] = {
            "intraday_adjust": 0.0,
            "intraday_score": self.NEUTRAL_SCORE,
            "intraday_ok": False,
            "sequence_adjust": round(sequence_total, 2) if sequence_ok else 0.0,
            "sequence_score": round(sequence_score, 2),
            "sequence_ok": sequence_ok,
            "seq": sequence["seq"] if sequence_ok else "",
            "prev_day_type": sequence["prev_day_type"] if sequence_ok else "",
            "weak_streak": sequence["weak_streak"] if sequence_ok else 0,
            "decay_weak": sequence["decay_weak"] if sequence_ok else 0.0,
            "history_days": sequence["days"] if sequence_ok else 0,
            # 跨日趋势层（均线与高点序列）。与标签层相互独立：
            # 日K 归档齐但分钟缺时 trend_ok 仍为真。
            "trend_ok": trend_ok,
            "trend_adjust": round(trend_adjust, 2) if trend_ok else 0.0,
            "below_ma": int(trend.get("below_ma", 0) or 0) if trend_ok else 0,
            "above_ma": int(trend.get("above_ma", 0) or 0) if trend_ok else 0,
            "lower_high": int(trend.get("lower_high", 0) or 0) if trend_ok else 0,
            "higher_high": int(trend.get("higher_high", 0) or 0) if trend_ok else 0,
            "ma_period": self.ma_period,
            "day_type": "",
            "early_peak": False,
            "dive": False,
            "peak_rise": float("nan"),
            "fall_b": "",
            "fall_from_peak": float("nan"),
            "peak_seg": -1,
        }

        if features is None:
            base.update(
                {
                    "score": base["sequence_score"] if sequence_ok else self.NEUTRAL_SCORE,
                    "data_ok": False,
                    "shape": "",
                    "prefix_shape": "",
                    "complete": False,
                    "completed_segments": 0,
                    "reason": ["形态数据不足"] + sequence_reasons + trend_reasons,
                }
            )
            return base

        base.update(
            {
                "day_type": features.get("day_type", ""),
                "early_peak": bool(features.get("early_peak")),
                "dive": bool(features.get("dive")),
                "peak_rise": features.get("peak_rise", float("nan")),
                "fall_b": str(features.get("fall_b", "")),
                "fall_from_peak": features.get("fall_from_peak", float("nan")),
                "peak_seg": int(features.get("peak_seg", -1)),
            }
        )

        # 前 N 段还没走完，当日规则键无从确定。返回特征供观测，但标记为不可用，
        # 让调用方走「不参与打分」而不是「给中性分放行」。
        if features["completed_segments"] < self.rule_segments:
            base.update(
                {
                    "score": base["sequence_score"] if sequence_ok else self.NEUTRAL_SCORE,
                    "data_ok": False,
                    "shape": features["shape"],
                    "prefix_shape": features["prefix_shape"],
                    "complete": features["complete"],
                    "completed_segments": features["completed_segments"],
                    "features": features,
                    "reason": [f"前{self.rule_segments}段未走完"] + sequence_reasons + trend_reasons,
                }
            )
            return base

        adjust, reasons = self._adjust(features)
        total = (
            self.NEUTRAL_SCORE + adjust + (sequence_total if sequence_ok else 0.0)
        )
        base.update(
            {
                "score": round(min(max(total, 0.0), 10.0), 2),
                "data_ok": True,
                "intraday_adjust": round(adjust, 2),
                "intraday_score": round(
                    min(max(self.NEUTRAL_SCORE + adjust, 0.0), 10.0), 2
                ),
                "intraday_ok": True,
                "shape": features["shape"],
                "prefix_shape": features["prefix_shape"],
                "complete": features["complete"],
                "completed_segments": features["completed_segments"],
                "gap_b": features["gap_b"],
                "trend_b": features["trend_b"],
                "cp_b": features["cp_b"],
                "features": features,
                "reason": reasons + sequence_reasons + trend_reasons,
            }
        )
        return base

    def score(
        self, stocks: Any, total_score: float = 10.0, as_of: str = "", history: Any = None
    ) -> float:
        """返回形态分（0~10，5 为中性）。

        :param stocks: 分钟行情
        :param total_score: 保留参数，与基类签名一致；实际满分固定 10
        :param as_of: 截止时刻，空表示完整模式
        :param history: 最近若干日的 `day_type`，昨日在前
        :return: 形态分；数据不足时返回中枢分，调用方应先用 `calculate()`
            检查 `data_ok` 再决定是否采用
        """
        return float(self.calculate(stocks, as_of=as_of, history=history)["score"])

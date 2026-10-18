"""按日形态编码与条件统计（路线 1：形状编码 + 离线条件统计）。

把每个交易日的日内分钟走势编码成离散形态，再统计该形态出现后**次日收益**的
分布。目的不是预测，而是先回答「哪些日内形态之后，次日方向有稳定偏斜」，
找到稳定偏斜之后再谈怎么接进策略。

**编码实现在 `factor/intraday_shape_factor.py`，本脚本不重复实现。**
统计口径与策略口径必须是同一份代码：分成两份会让「统计说有效、策略上无效」
这种漂移无法从日志里看出来。字段语义见该模块 docstring。

## 两种统计口径

- **完整形态**（默认）：用全天 bar 编码，与收盘后回头看一致
- **前缀形态**（`--prefix-upto HH:MM`）：只用截至该时刻的 bar 编码

前缀口径是实盘唯一可得的形态。盘中 11:00 时不存在「午后怎么走」的信息，
只用完整形态统计出的结论，在实盘拿不到。要判断某个形态能否用于盘中，
必须按 `--prefix-upto` 的口径重新统计。

## 三种被预测的收益（别混用）

| 表 | 用到的信息 | 被预测的收益 | 对应的问题 |
| --- | --- | --- | --- |
| 日形态 → 次日超额 | 当日收盘标签 | 次日 | 这种形态的股票明天怎么样 |
| 昨日形态 → 当日超额 | 前一交易日标签 | 当日 | 昨天这种形态，今天能买吗 |
| 截至昨日的连弱 → 当日超额 | 昨日及更早的标签 | 当日 | 连弱几天后，今天能买吗 |

后两张才是「盘中买入决策」的直接证据：它们只用隔夜已定型的信息。第一张含当日标签，
盘中拿不到（除非常态化使用 `--prefix-upto`）。

## 四、跨日趋势判据（2026-10-19 新增）

用户提出两个与日内路径无关的走弱判据：回看窗口内**收盘价低于 N 日均线的次数**、
**当日最高价低于前一日最高价的次数**，走强侧对称。它们由
`add_trend_columns` 从日K 归档算出（复用 `trend_features`，与策略同一份代码），
同样按「含当日 → 次日」与「截至昨日 → 当日」两种口径各出一张表。

## 样本独立性

同一天的个股高度相关（历史测算：500 只等权两两相关 0.231，≈ 4.3 个独立样本/日）。
把几百个「股票-日」样本当独立观测，显著性会被高估一个量级。因此下面的汇总表
**同时给出日度口径**：先把同一天同一形态下所有股票取均值，得到日度序列再统计，
t 值一律用日度序列算。原始样本数只作为「有多少数据」的参考。
"""

from __future__ import annotations

import argparse
import bisect
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from dao.local_hq_dao import KIND_DAY, KIND_MINUTE, LocalHqDao
from factor.intraday_shape_factor import IntradayShapeFactor


# 形态编码器。阈值与分段定义都在因子模块里，这里只引用不重写。
SHAPE_FACTOR: IntradayShapeFactor = IntradayShapeFactor()


def index_next_return(
    day_buckets: dict[str, list[dict]],
) -> dict[tuple[str, str], float]:
    """建立 (code, 日期) -> 次日收益 的索引。

    次日收益 = 下一交易日收盘 / 当日收盘 - 1。用日K 计算，不从分钟数据跨日取，
    避免把隔夜缺口和日间缺口混在一起。
    """
    result: dict[tuple[str, str], float] = {}
    for code, records in day_buckets.items():
        ordered = sorted(records, key=lambda item: str(item.get("create_time", "")))
        for current, following in zip(ordered, ordered[1:]):
            try:
                current_close = float(current.get("close") or 0.0)
                next_close = float(following.get("close") or 0.0)
            except (TypeError, ValueError):
                continue
            if current_close <= 0 or next_close <= 0:
                continue
            day = str(current.get("create_time", ""))[:10]
            result[(code, day)] = next_close / current_close - 1.0
    return result


def index_day_returns(day_buckets: dict[str, list[dict]]) -> dict[tuple[str, str], float]:
    """建立 (code, 日期) -> 当日收益 的索引。

    当日收益 = 当日收盘 / 前一交易日收盘 - 1。日K 没有 preclose 字段，
    只能由相邻两日推出。用途：检验「截至昨日的连弱形态能否预测**当日**走势」，
    这与「当日形态 → 次日收益」是两个不同的问题，用户关心的是前者
    （今天开盘后该不该买）。
    """
    result: dict[tuple[str, str], float] = {}
    for code, records in day_buckets.items():
        ordered = sorted(records, key=lambda item: str(item.get("create_time", "")))
        for previous, current in zip(ordered, ordered[1:]):
            try:
                prev_close = float(previous.get("close") or 0.0)
                close = float(current.get("close") or 0.0)
            except (TypeError, ValueError):
                continue
            if prev_close <= 0 or close <= 0:
                continue
            day = str(current.get("create_time", ""))[:10]
            result[(code, day)] = close / prev_close - 1.0
    return result


def build_panel(
    dao: LocalHqDao,
    start: str,
    end: str,
    as_of: str = "",
    require_segments: int = 0,
) -> pd.DataFrame:
    """构建「股票-日」面板：每行一天的形态特征 + 次日收益。

    :param dao: 本地归档读取层
    :param start: 开始日期
    :param end: 结束日期
    :param as_of: 截止时刻（HH:MM）。空表示用全天 bar（完整形态），
        非空表示只用该时刻前的 bar（前缀形态，与实盘盘中可得的信息一致）
    :param require_segments: 要求前 N 段已走完，否则丢弃该样本
    :return: 面板 DataFrame，含 next_ret / excess 与形态列
    """
    minute = dao.load_all(KIND_MINUTE, start, end)
    day = dao.load_all(KIND_DAY, start, end)
    next_returns = index_next_return(day)
    day_returns = index_day_returns(day)

    rows: list[dict] = []
    for code, records in minute.items():
        by_date: dict[str, list[dict]] = {}
        for record in records:
            date = str(record.get("minute_time", ""))[:10]
            by_date.setdefault(date, []).append(record)

        for date, bars in by_date.items():
            features = SHAPE_FACTOR.encode(
                bars, as_of=as_of, require_segments=require_segments
            )
            if features is None:
                continue
            forward = next_returns.get((code, date))
            if forward is None:
                # 区间最后一天没有次日收益；或该股在日K 里缺记录
                continue
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "next_ret": forward,
                    "today_ret": day_returns.get((code, date), float("nan")),
                    **features,
                }
            )

    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel

    # 横截面去均值：超额 = 个股次日收益 - 当日池内等权平均。
    # 不做这一步，样本期若是下跌市，所有形态的绝对收益都会是负的，形态之间的
    # 差异被市场 beta 吃掉，看不出任何东西。
    panel["market_ret"] = panel.groupby("date")["next_ret"].transform("mean")
    panel["excess"] = panel["next_ret"] - panel["market_ret"]
    # 当日收益同样要去横截面均值：检验「截至昨日的连弱 → 当日收益」时，
    # 若看绝对值，下跌市里所有分档都会是负的，看不出形态之间的差异。
    panel["today_excess"] = panel["today_ret"] - panel.groupby("date")[
        "today_ret"
    ].transform("mean")
    return panel


def _key_label(key: Any) -> str:
    """把 groupby 的分组键转成可读标签。

    单列分组时 pandas 给的是标量（可能是 int，比如连续弱势天数），
    多选题才是元组。int 不可迭代，直接 join 会抛 TypeError。
    """
    if isinstance(key, tuple):
        return "|".join(str(item) for item in key)
    return str(key)


def add_sequence_columns(panel: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """给面板补「跨日序列」列：连续弱势天数、昨日形态、近 N 日标签串。

    序列只由**已定型的历史日**构成，同一天的所有股票共用一套日期，因此按 code
    分组逐行推进即可。这里刻意复用 `IntradayShapeFactor.sequence_features`：
    统计口径与策略口径必须是同一份代码，否则会出现「统计说连弱 2 天有效、
    策略上不触发」这种两边日志都正常的漂移。

    注意 `weak_streak` **含当日**：面板每一行的 `next_ret` 是「当日 → 次日」，
    因此「当日收盘时已经连弱几天」正是次日买入预期的可用信息。

    :param panel: `build_panel` 的返回值
    :param lookback: 序列串回看天数
    :return: 增加 `prev_day_type` / `weak_streak` / `decay_weak` / `seq` 列的面板
    """
    if panel.empty:
        return panel

    out = panel.sort_values(["code", "date"]).copy()
    prev_types: list[str] = []
    streaks: list[int] = []
    prev_streaks: list[int] = []
    decays: list[float] = []
    seqs: list[str] = []

    for _code, sub in out.groupby("code", sort=False):
        types = [str(item or "") for item in sub["day_type"]]
        for index in range(len(types)):
            # 含当日：当日标签为空（数据不全）时序列在当日即中断，
            # 这与「不知道那天形态就不能假定延续」的口径一致。
            window = [
                types[pos]
                for pos in range(index, max(index - lookback, -1), -1)
            ]
            features = SHAPE_FACTOR.sequence_features(window)
            # 截至昨日（不含当日）：这是「今天开盘后能看到的全部信息」，
            # 用来检验「连弱 → 当日走势」，与含当日的 `weak_streak` 不同。
            prior = [
                types[pos]
                for pos in range(index - 1, max(index - 1 - lookback, -1), -1)
            ]
            prior_features = SHAPE_FACTOR.sequence_features(prior)
            prev_types.append(types[index - 1] if index > 0 else "")
            streaks.append(int(features["weak_streak"]))
            prev_streaks.append(int(prior_features["weak_streak"]))
            decays.append(float(features["decay_weak"]))
            seqs.append(str(features["seq"]))

    out["prev_day_type"] = prev_types
    out["weak_streak"] = streaks
    out["prev_weak_streak"] = prev_streaks
    out["decay_weak"] = decays
    out["seq"] = seqs
    return out


def add_trend_columns(
    panel: pd.DataFrame,
    day_buckets: dict[str, list[dict]],
    ma_period: int = 5,
    lookback: int = 5,
) -> pd.DataFrame:
    """给面板补「跨日趋势」列：低于均线次数、高点低于前日次数（含当日与截至昨日）。

    判据来自用户 2026-10-19 的提议：连续几天收盘价低于 N 日均线、连续几天
    最高价低于前日最高价，都算「连续走弱」；走强侧对称（高于均线、高于前日）。
    计算复用 `IntradayShapeFactor.trend_features`，与策略口径同一份代码。

    两套口径对应两个不同的问题，别混用：

    - **含当日**（`below_ma` / `above_ma` / `lower_high` / `higher_high`）：
      收盘时全部已知，被预测的是**次日**收益
    - **截至昨日**（`prev_*`）：今天开盘时可见的全部信息，被预测的是**当日**
      收益，对应「连弱几天后，今天还能不能买」

    均线按可用K线的位置算（停牌日不在序列里），窗口取 `lookback + ma_period - 1`
    天，使回看窗口内最早那天也算得出均线。

    :param panel: `build_panel` 的返回值（需已含 `add_sequence_columns` 的列）
    :param day_buckets: 日K 归档 `{code: [记录]}`（`dao.load_all(KIND_DAY, ...)`）
    :param ma_period: 均线周期
    :param lookback: 回看窗口（交易日数）
    :return: 增加八列计数的面板
    """
    if panel.empty:
        return panel

    out = panel.sort_values(["code", "date"]).copy()
    period = max(int(ma_period), 1)
    span = max(int(lookback), 1) + period - 1

    # 每只股票的日线序列（升序、剔非法行）。停牌日不在序列里
    series_by_code: dict[str, list[dict[str, Any]]] = {}
    for code, records in day_buckets.items():
        rows: list[dict[str, Any]] = []
        for record in sorted(records, key=lambda i: str(i.get("create_time", ""))):
            day = str(record.get("create_time", ""))[:10]
            try:
                close = float(record.get("close") or 0.0)
                high = float(record.get("high") or 0.0)
            except (TypeError, ValueError):
                continue
            if day and close > 0 and high > 0:
                rows.append({"date": day, "close": close, "high": high})
        series_by_code[code] = rows

    names: tuple[str, ...] = (
        "below_ma",
        "above_ma",
        "lower_high",
        "higher_high",
        "prev_below_ma",
        "prev_above_ma",
        "prev_lower_high",
        "prev_higher_high",
    )
    columns: dict[str, list[int]] = {name: [] for name in names}
    for code, sub in out.groupby("code", sort=False):
        rows = series_by_code.get(code, [])
        dates = [row["date"] for row in rows]
        for date in sub["date"]:
            # 含当日：窗口末尾 <= date
            upto = bisect.bisect_right(dates, date)
            tail = rows[max(0, upto - span) : upto]
            feat = SHAPE_FACTOR.trend_features(tail, window=lookback)
            # 截至昨日：窗口末尾 < date
            prior = bisect.bisect_left(dates, date)
            prior_tail = rows[max(0, prior - span) : prior]
            pfeat = SHAPE_FACTOR.trend_features(prior_tail, window=lookback)
            for name in names:
                key = name[len("prev_") :] if name.startswith("prev_") else name
                source = pfeat if name.startswith("prev_") else feat
                columns[name].append(int(source.get(key, 0) or 0))

    for name in names:
        out[name] = columns[name]
    return out


def summarize(
    panel: pd.DataFrame, keys: list[str], min_days: int = 5, col: str = "excess"
) -> pd.DataFrame:
    """按形态分组统计**超额**次日收益。

    统计的是 `excess`（个股次日收益减去当日池内等权平均），不是绝对收益。
    原因：样本期市场整体下跌时所有形态的绝对收益都是负的，形态之间的差异会被
    市场 beta 完全掩盖（2026-07-17~10-13 这段基准日度均值 -0.507%，若看绝对值
    会得出「所有形态都看跌」这种无用结论）。

    :param col: 统计哪一列收益。默认 `excess`（次日超额）；检验「昨日连弱 →
        当日走势」时传 `today_excess`。

    日度口径：先把同一天同一形态下的所有股票取均值，得到日度序列再统计。
    这是保守口径，避免把横截面上高度相关的个股当成独立样本。
    """
    rows: list[dict] = []
    if panel.empty:
        return pd.DataFrame()

    for key, sub in panel.groupby(keys, dropna=False):
        daily = sub.groupby("date")[col].mean()
        if len(daily) < min_days:
            continue
        daily_std = float(daily.std())
        t_stat = (
            float(daily.mean()) / (daily_std / len(daily) ** 0.5)
            if len(daily) > 1 and daily_std > 0
            else 0.0
        )
        label = _key_label(key)
        rows.append(
            {
                "形态": label,
                "样本数": len(sub),
                "日度数": len(daily),
                "超额均值%": round(float(sub[col].mean()) * 100, 3),
                "日度超额%": round(float(daily.mean()) * 100, 3),
                "日度std%": round(daily_std * 100, 3),
                "超额胜率%": round(float((sub[col] > 0).mean()) * 100, 1),
                "日度胜率%": round(float((daily > 0).mean()) * 100, 1),
                "t值": round(t_stat, 2),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("日度超额%")


def stability(panel: pd.DataFrame, keys: list[str], min_days: int = 4) -> pd.DataFrame:
    """把样本按时间对半切开，看形态效应在两段里是否同号。

    防「看起来显著其实是噪声」的第一道闸门：在几十种形态里挑 |t| > 2 的，光靠
    5% 显著性水平就会挑出两个假的。真正可用的效应应当在两段样本里方向一致。
    本项目此前踩过这个坑：某形态效应在前半段 +1.50%、后半段 -0.17%，符号直接反转。
    """
    if panel.empty:
        return pd.DataFrame()

    dates = sorted(panel["date"].unique())
    if len(dates) < 2 * min_days:
        return pd.DataFrame()
    mid = dates[len(dates) // 2]

    def _daily(sub: pd.DataFrame) -> pd.Series:
        return sub.groupby("date")["excess"].mean()

    rows: list[dict] = []
    for key, sub in panel.groupby(keys, dropna=False):
        first = _daily(sub[sub["date"] <= mid])
        second = _daily(sub[sub["date"] > mid])
        if len(first) < min_days or len(second) < min_days:
            continue
        first_mean = float(first.mean()) * 100
        second_mean = float(second.mean()) * 100
        label = _key_label(key)
        rows.append(
            {
                "形态": label,
                "前半段%": round(first_mean, 3),
                "后半段%": round(second_mean, 3),
                "前半段日度": len(first),
                "后半段日度": len(second),
                "同号": "是" if first_mean * second_mean > 0 else "否",
                "两段绝对差": round(abs(first_mean - second_mean), 3),
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["_stable"] = (out["同号"] == "是").astype(int)
    return out.sort_values(
        ["_stable", "两段绝对差"], ascending=[False, True]
    ).drop(columns="_stable")


def _print_table(title: str, table: pd.DataFrame, note: str = "") -> None:
    print()
    print(f"=== {title} ===")
    if note:
        print(note)
    if table.empty:
        print("（样本不足，无可用分组）")
        return
    print(table.to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日内形态编码与次日收益条件统计")
    parser.add_argument("--local-dir", required=True, help="归档 parquet 根目录")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--min-days", type=int, default=8, help="分组最少日度数")
    parser.add_argument("--csv", default="", help="把面板导出为 CSV")
    parser.add_argument(
        "--prefix-upto",
        default="",
        metavar="HH:MM",
        help=(
            "按前缀口径统计：只用当日截至该时刻的 bar 编码。盘中实盘能拿到的"
            "就是前缀形态，要判断形态能否用于盘中，必须用这个口径。"
        ),
    )
    parser.add_argument(
        "--prefix-head",
        type=int,
        default=2,
        metavar="K",
        help=(
            "前缀口径下只用前 K 段符号分组。默认 2：前两段在 11:30 定型后不再变化，"
            "这条规则在盘中任何时刻都成立。K=4 等价于完整形态。"
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.local_dir)
    if not root.is_dir():
        print(f"归档目录不存在: {root}", file=sys.stderr)
        return 1

    as_of: str = args.prefix_upto.strip()
    head_len: int = max(int(args.prefix_head), 1)
    dao = LocalHqDao(root)
    panel = build_panel(
        dao,
        args.start,
        args.end,
        as_of=as_of,
        require_segments=head_len if as_of else 0,
    )
    if panel.empty:
        print("没有可用样本：检查分钟归档的日期范围与区间是否相交", file=sys.stderr)
        return 1

    # 前缀口径的分组键取前 K 段。K 段之外的符号在查询时刻可能还没定型，
    # 拿它分组等于用「未来信息」分桶，得出来的效应盘中取不到。
    if as_of:
        panel["path_key"] = panel["prefix_shape"].astype(str).str[:head_len]

    mode_text: str = (
        f"前缀口径（截至 {as_of}，按前 {head_len} 段符号）"
        if as_of
        else "完整口径（全天 bar）"
    )
    print(f"统计口径: {mode_text}")
    print(f"面板: {len(panel)} 个「股票-日」样本 / {panel['date'].nunique()} 个交易日 "
          f"/ {panel['code'].nunique()} 只股票")
    print(f"区间: {panel['date'].min()} ~ {panel['date'].max()}")
    print(f"每只股票每天 bar 数: 中位数 {int(panel['bars'].median())}，"
          f"最少 {int(panel['bars'].min())}，最多 {int(panel['bars'].max())}")
    print("  仅统计覆盖度达标的样本（首根 ≤ 10:00；完整口径还要求末根 ≥ 14:30）；")
    print("  bar 频率在不同时期不一致（早期 1 分钟一根 240 行，近期 2 分钟一根约 116 行），")
    print("  分段收益与频率无关。")

    if args.csv:
        panel.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"面板已导出: {args.csv}")

    print()
    print("注意：同一天个股高度相关（≈4.3 独立样本/日），所以看「日度超额%」「t值」两列，")
    print("      「样本数」只说明有多少数据，不代表同等数量的独立证据。")
    print("多重比较：这里的形态有几十种，|t| > 2 在 5% 水平上本来就会挑出两个假的。")
    print("      判断某个形态是否可用，要看它是否在前后半段同号，以及样本天数是否够多。")

    _print_table(
        "开盘缺口 × 日内方向 × 收盘位置",
        summarize(panel, ["gap_b", "trend_b", "cp_b"], args.min_days),
    )
    _print_table(
        "开盘缺口（只按缺口）",
        summarize(panel, ["gap_b"], args.min_days),
    )
    _print_table(
        "日内方向（只按方向）",
        summarize(panel, ["trend_b"], args.min_days),
    )
    _print_table(
        "收盘位置（只按位置）",
        summarize(panel, ["cp_b"], args.min_days),
    )

    # 日形态与跨日序列只对完整口径有意义：前缀口径下 day_type 恒为空
    # （当日未走完，峰值与回撤还会变），拿它分组只会得到空表。
    if not as_of:
        panel = add_sequence_columns(panel, lookback=3)
        # 跨日趋势判据的计数（日K 归档；DAO 内部按区间缓存，重复加载不读盘）
        panel = add_trend_columns(
            panel,
            dao.load_all(KIND_DAY, args.start, args.end),
            ma_period=5,
            lookback=5,
        )
        counts = panel["day_type"].value_counts()
        print()
        print("日形态分布（一天一个标签，互斥优先级：冲高回落 > 缺口/方向组合）:")
        for label, count in counts.items():
            share = count / len(panel) * 100 if len(panel) else 0.0
            print(f"  {label or '（算不出来）'}: {count} 个样本（{share:.1f}%）")

        _print_table(
            "日形态（高开低走 = 当日下跌且早盘/上午见高、收盘距峰值回撤 ≥2.5%）",
            summarize(panel, ["day_type"], args.min_days),
            note=(
                "「高开低走」不看开盘缺口，这正是它与此前 gap 口径的区别："
                "平开或低开后上午冲高、随后一路下跌，同样计入。"
            ),
        )
        _print_table(
            "日形态 × 开盘缺口（同一形态在不同缺口下的差异）",
            summarize(panel, ["day_type", "gap_b"], args.min_days),
        )
        _print_table(
            "连续弱势天数（含当日；弱势 = 冲高回落 / 低开低走 / 高开低走）",
            summarize(panel, ["weak_streak"], args.min_days),
            note="这一列是「次日买入预期」的直接依据：连弱越多，次日超额应越差。",
        )
        _print_table(
            "截至昨日的连续弱势天数 → 当日超额（今天开盘时可见的信息）",
            summarize(panel, ["prev_weak_streak"], args.min_days, col="today_excess"),
            note=(
                "这一张才对应「连弱几天后，今天还能不能买」：只用昨日及更早的标签，"
                "被预测的是当日收益。上一张含当日标签，被预测的是次日收益，两者不是一回事。"
            ),
        )
        _print_table(
            "昨日形态 → 当日超额（前一交易日标签 vs 今天怎么走）",
            summarize(
                panel[panel["prev_day_type"] != ""],
                ["prev_day_type"],
                args.min_days,
                col="today_excess",
            ),
            note=(
                "这是「昨天这种形态，今天开盘后能不能买」的直接检验。"
                "与下一张的区别只在被预测的收益：这张是当日，下一张是次日。"
            ),
        )
        _print_table(
            "昨日形态（前一日标签 → 当日之后收益）",
            summarize(panel[panel["prev_day_type"] != ""], ["prev_day_type"], args.min_days),
        )
        _print_table(
            "近 3 日形态串（当日在前，`|` 分隔）",
            summarize(panel[panel["seq"] != ""], ["seq"], args.min_days),
            note=(
                "组合数 = 6^3，40 个交易日摊不开，这张表只用来看方向，"
                "不要按「|t|>2」挑规则。粗粒度请用上一张「连续弱势天数」。"
            ),
        )
        _print_table(
            "收盘低于 5 日均线次数（含当日）→ 次日超额",
            summarize(panel, ["below_ma"], args.min_days),
            note=(
                "用户 2026-10-19 提出的走弱判据一：回看 5 天内收盘低于 5 日均线的"
                "天数，先验是「越多越弱」。含当日口径，被预测的是次日收益。"
            ),
        )
        _print_table(
            "最高价低于前日次数（含当日）→ 次日超额",
            summarize(panel, ["lower_high"], args.min_days),
            note="走弱判据二：回看 5 天内当日最高价低于前一日最高价的天数。",
        )
        _print_table(
            "收盘高于 5 日均线次数（含当日）→ 次日超额",
            summarize(panel, ["above_ma"], args.min_days),
            note="走强侧对称对照（判据一的反向）。",
        )
        _print_table(
            "最高价高于前日次数（含当日）→ 次日超额",
            summarize(panel, ["higher_high"], args.min_days),
            note="走强侧对称对照（判据二的反向）。",
        )
        _print_table(
            "截至昨日收盘低于 5 日均线次数 → 当日超额（今天开盘时可见）",
            summarize(panel, ["prev_below_ma"], args.min_days, col="today_excess"),
            note=(
                "「连弱几天后今天能不能买」的直接检验：窗口截至昨日，"
                "被预测的是当日收益。"
            ),
        )
        _print_table(
            "截至昨日最高价低于前日次数 → 当日超额（今天开盘时可见）",
            summarize(panel, ["prev_lower_high"], args.min_days, col="today_excess"),
            note="判据二的「今日买入」口径。走强侧（prev_above_ma / prev_higher_high）"
            "可用 --csv 导出的面板自行分组。",
        )

    # 前缀口径必须按 path_key（前 K 段）分组：完整口径下 shape 才是四个纯符号，
    # 前缀口径下 shape 恒为空（末段还没走完），用它会得到空表。
    path_col: str = "path_key" if as_of else "shape"
    shape_panel = panel[panel[path_col].astype(str) != ""]
    path_title: str = (
        f"前 {head_len} 段符号（截至 {as_of}，实盘可得口径）"
        if as_of
        else "四段路径符号（+ 涨 / - 跌 / 0 平；早盘 / 上午尾 / 午后 / 尾盘各一小时）"
    )
    _print_table(
        path_title,
        summarize(shape_panel, [path_col], args.min_days),
        note=f"可用于形状编码的样本 {len(shape_panel)} 个",
    )
    _print_table(
        "上半段 vs 下半段（同号才值得关注）",
        stability(shape_panel, [path_col], 3),
        note=(
            "按日期对半切分。分半之后每组只剩两三天，数值本身不必当真，"
            "这里只看两段是否同号：方向来回变的效应多半是噪声而不是规律。"
        ),
    )

    print()
    print("整体次日收益（基准）:")
    daily_all = panel.groupby("date")["next_ret"].mean()
    print(f"  日度均值 {daily_all.mean() * 100:.3f}% / 日度胜率 "
          f"{(daily_all > 0).mean() * 100:.1f}% / 日度数 {len(daily_all)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

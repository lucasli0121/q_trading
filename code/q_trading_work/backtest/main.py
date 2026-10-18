"""
Author: liguoqiang
Date: 2026-08-02
Description: 回测命令行入口 —— 通过命令行对指定策略执行回测，
    支持日频/分钟频、股票池或显式代码列表、交易成本参数，
    结果可打印、导出 JSON 或保存到服务端。

用法示例：
    # 强势反弹策略，回测两只股票
    python backtest/main.py --strategy strong \
        --codes 000001,600519 --start 2026-03-01 --end 2026-07-31

    # 波段策略，使用股票池，导出 JSON
    python backtest/main.py --strategy swing \
        --pool-name 科技股票池 --start 2026-03-01 --end 2026-07-31 \
        --output backtest_result.json

    # 保存回测结果到服务端（需要 strategy_id 与 cfg 中的 admin_token）
    python backtest/main.py --strategy strong --codes 000001 \
        --start 2026-03-01 --end 2026-07-31 --save --strategy-id s-xxx

    # 分钟频回测（需要服务端提供分钟K线数据）
    python backtest/main.py --strategy strong --codes 000001 \
        --frequency minute --start "2026-07-15 09:30:00" --end "2026-07-15 15:00:00"
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

# 支持直接执行（python backtest/main.py）与模块方式（python -m backtest.main）
if __package__ in (None, ""):
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

from backtest.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestSummary,
    TradeCostConfig,
    TradeResult,
)
from dao.local_hq_dao import MODE_OFF, VALID_MODES
from strategy.base_strategy import BaseStrategy


# 常用策略快捷名 -> (模块路径, 类名)
STRATEGY_ALIASES: dict[str, tuple[str, str]] = {
    "strong": ("strategy.strong_rebound_strategy", "StrongReboundStrategy"),
    "rebound": ("strategy.strong_rebound_strategy", "StrongReboundStrategy"),
    "swing": ("strategy.swing_trading_strategy", "SwingTradingStrategy"),
    "volume": ("strategy.volume_expansion_strategy", "VolumeExpansionStrategy"),
}


def build_parser() -> argparse.ArgumentParser:
    """构建回测命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="backtest",
        description="Q-Trading 策略回测命令行工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--strategy", "-s",
        default="strong",
        help=(
            "策略名称或模块路径。快捷名: strong/rebound/swing；"
            "或使用 module:ClassName，如 strategy.strong_rebound_strategy:StrongReboundStrategy"
        ),
    )
    parser.add_argument("--codes", help="股票代码列表，逗号分隔，如 000001,600519")
    parser.add_argument("--pool-name", help="股票池名称（与 --codes 二选一）")
    parser.add_argument("--pool-id", help="股票池 ID（优先级最高）")
    parser.add_argument("--start", default="", help="回测开始日期 YYYY-MM-DD（分钟频带时间）")
    parser.add_argument("--end", default="", help="回测结束日期 YYYY-MM-DD（分钟频带时间）")
    parser.add_argument("--capital", type=float, default=100000.0, help="初始资金（元）")
    parser.add_argument("--hold-days", type=int, default=5, help="持有天数")
    # 默认值与 BacktestConfig.signal_window 同源，避免「改了一处忘了另一处」。
    # 40 根是 MultiTrendFactor 的 MACD 分项（需 34 根出值、35 根才能比较放大）
    # 能参与打分的前提。
    parser.add_argument(
        "--signal-window",
        type=int,
        default=BacktestConfig.signal_window,
        help="信号检测窗口（日线根数，默认 40；小于 35 时趋势因子的 MACD 分项失效）",
    )
    parser.add_argument(
        "--frequency", choices=("daily", "minute"), default="daily", help="K线频率"
    )
    parser.add_argument(
        "--position-size", type=float, default=1.0, help="每笔交易使用资金比例（0-1）"
    )
    parser.add_argument(
        "--split-capital-by-code",
        action="store_true",
        help=(
            "把初始资金平均分给回测涉及的每只股票，各自独立周转、互不挤占。"
            "默认关闭 = 全局单一资金池：先出现的信号占满资金、其余标的的信号被大量跳过"
        ),
    )
    parser.add_argument(
        "--benchmark",
        default="",
        help=(
            "基准代码，默认空=关闭基准对比（服务端日K暂不支持指数代码，"
            "填了会取数失败）；后台接入指数行情后再填，如 000300"
        ),
    )
    parser.add_argument(
        "--fetch-retries",
        type=int,
        default=2,
        help="行情取数失败重试次数（回测要求数据完整，重试后仍失败即中止且不写库）",
    )
    parser.add_argument(
        "--fetch-retry-interval",
        type=float,
        default=1.0,
        help="行情取数重试间隔（秒）",
    )
    parser.add_argument(
        "--max-drawdown",
        type=float,
        default=None,
        help=(
            "账户级回撤阈值（%%），负值有效，如 -10.0：账户已实现权益自峰值"
            "回撤达该值时，当日收盘卖出全部在持仓位（不冻结开新仓）。"
            "不传则取策略参数 RISK_MAX_DRAWDOWN_PCT，0 或正数表示关闭"
        ),
    )

    cost = parser.add_argument_group("交易成本")
    cost.add_argument("--buy-slip", type=float, default=0.02, help="买入滑点（元）")
    cost.add_argument("--sell-slip", type=float, default=0.02, help="卖出滑点（元）")
    cost.add_argument(
        "--fee-pct", type=float, default=0.02,
        help="手续费百分率，如 0.02 表示 0.02 个百分点",
    )
    cost.add_argument("--fee-low", type=float, default=5.0, help="最低手续费（元）")

    flt = parser.add_argument_group("信号风暴过滤")
    flt.add_argument(
        "--storm-filter",
        action="store_true",
        help=(
            "启用信号风暴过滤（与实盘 StrategyWorkflow 同语义）：同一天内、同方向的"
            "连续信号，若距上一条通过过滤的信号不足时间间隔、或价格变化不足阈值，"
            "则被抑制、不成交。默认关闭以保持历史回测可比，实盘常开。"
            "仅分钟频生效（日频同方向连续信号必然跨日，而该过滤只比同日）"
        ),
    )
    flt.add_argument(
        "--storm-time-gap-minutes",
        type=float,
        default=10.0,
        help="风暴过滤：同方向两条信号的最小时间间隔（分钟）。默认同 cfg/stock.cfg",
    )
    flt.add_argument(
        "--storm-price-change-pct",
        type=float,
        default=2.0,
        help="风暴过滤：同方向两条信号的最小价格变化（%%）。默认同 cfg/stock.cfg",
    )
    flt.add_argument(
        "--min-trade-interval-minutes",
        type=float,
        default=0.0,
        help=(
            "任意两次成交之间的最小间隔（分钟），0 = 关闭（默认）。"
            "与 --storm-filter 不是一回事：风暴过滤只对**同方向**连续信号生效，"
            "压不住「卖完又买回」；本项不分方向"
        ),
    )
    flt.add_argument(
        "--buy-dip-gate",
        action="store_true",
        help=(
            "盘中买入门槛（2026-10-14）：每只股票缓存上一次符合买入条件的价格与时刻，"
            "本次价格跌破该价格、且间隔不足 --buy-dip-gap-minutes 分钟时抑制本次买入信号。"
            "被抑制的买点**仍然更新缓存**，因此阴跌中每个买点都会把窗口清零，"
            "不会「等满 10 分钟就补一单」。只作用于买入，卖出不受影响。"
            "默认关闭以保持历史回测可比，实盘常开"
        ),
    )
    flt.add_argument(
        "--buy-dip-gap-minutes",
        type=float,
        default=10.0,
        help=(
            "盘中买入门槛的窗口（分钟），<= 0 视为关闭。"
            "默认 10，与 cfg/stock.cfg [signal_filter] time_gap_minutes 同源"
        ),
    )

    gate = parser.add_argument_group("每日门槛复算（2026-10-13）")
    gate.add_argument(
        "--enable-daily-gates",
        action="store_true",
        help=(
            "在回测中复算实盘的「盘后个股阀门 → 次日门槛」链路：每日收盘后用当日"
            "信号时间 + 分钟缓存算出阀门，次日盘前注入。默认关闭以保持历史回测"
            "数字可比。注意日线趋势门槛不受本开关控制 —— 它在策略 before_trading "
            "中自行判定，回测调 before_trading 时即已生效"
        ),
    )
    gate.add_argument(
        "--market-breadth-csv",
        default="",
        help=(
            "涨跌停家数 CSV（列 date,limit_up,limit_down；date 为 YYYYMMDD）。"
            "非空即启用市场级门槛偏移与极端利空禁买。CSV 里查不到的交易日按"
            "取数失败处理（偏移 0、不禁买），与实盘 fail-open 一致 —— "
            "注意 akshare 只覆盖最近约 30 个交易日，更早日期在 CSV 里是空值"
        ),
    )
    gate.add_argument(
        "--no-trend-gate",
        action="store_true",
        help=(
            "关闭日线趋势门槛（TREND_GATE_ENABLED=False）。用于对照：与 "
            "--enable-daily-gates 都不传时即为「门槛全关」的历史口径"
        ),
    )
    gate.add_argument(
        "--no-buy-signal-stop",
        action="store_true",
        help=(
            "关闭买入信号价止损（SELL_BUY_PRICE_BREAK_ENABLED=False）。用于对照："
            "成交量与波段策略自 2026-10-14 起用「连续 3 个分钟 bar 低于买入信号价」"
            "取代原「自持仓最高点回撤」，本开关用于拆分这一项的净效果"
        ),
    )
    gate.add_argument(
        "--no-intraday-breaker",
        action="store_true",
        help=(
            "关闭日内熔断（INTRADAY_BREAKER_ENABLED=False）。用于对照：熔断在"
            "「连续 3 次亏损卖出」后当日停该股的买入信号、在「连续 3 次卖飞」后"
            "当日停该股的卖出信号，本开关用于拆分它相对止损的净效果"
        ),
    )
    gate.add_argument(
        "--no-account-drawdown",
        action="store_true",
        help=(
            "关闭账户级回撤（RISK_MAX_DRAWDOWN_PCT=0）。用于对照：账户已实现权益"
            "自峰值回撤达阈值时，策略侧会对每只被检查的股票在卖出分上计入 "
            "risk.drawdown_score（分钟频逐 bar），引擎侧另有一条「达阈值当日收盘"
            "卖出全部在持仓位」（仅日频）。本开关把两条一起关掉"
        ),
    )
    gate.add_argument(
        "--shape-max-offset",
        type=float,
        default=None,
        metavar="F",
        help=(
            "日内形态最差时抬高买入门槛的幅度（10 分制），覆盖 buy.shape_max_offset。"
            "0 = 形态不影响门槛（仍计算并写入信号明细）。不传则取策略参数。"
            "形态分换算成门槛偏移的语义与 buy_threshold_offset 一致：正 = 更难买"
        ),
    )
    gate.add_argument(
        "--no-shape",
        action="store_true",
        help="完全关闭日内形态计算（SHAPE_ENABLED=False），用于对照形态接入选前选后的差异",
    )
    gate.add_argument(
        "--verbose",
        action="store_true",
        help="把回测日志按 INFO 打到 stdout（默认近乎静默，只有汇总）",
    )

    local = parser.add_argument_group("本地归档行情（2026-10-15）")
    local.add_argument(
        "--local-hq-dir",
        default=None,
        help=(
            "本地归档 parquet 根目录（q_trading_archive 的 data/ 目录）。"
            "服务端只保留 90 天且分钟无法回源，改读本地可解除该限制。"
            "不传时读 cfg/stock.cfg [local_hq] root"
        ),
    )
    local.add_argument(
        "--local-hq-mode",
        default=None,
        choices=list(VALID_MODES),
        help=(
            "off=全部走服务端；prefer=本地覆盖完整时用本地、否则整体回落 HTTP；"
            "only=覆盖不足直接中止回测。不传时读 cfg/stock.cfg [local_hq] mode"
        ),
    )

    out = parser.add_argument_group("结果输出")
    out.add_argument("--output", help="将回测结果导出为 JSON 文件路径")
    out.add_argument(
        "--save", action="store_true", help="将回测结果保存到服务端（需 --strategy-id）"
    )
    out.add_argument(
        "--strategy-id",
        help="保存回测结果所需的策略模板 ID（StrategyDao._id）",
    )
    out.add_argument(
        "--user-strategy-id",
        default="",
        help="用户策略关联 ID（可选，保存后额外同步策略执行指标）",
    )
    return parser


def _parse_codes(value: str | None) -> list[str] | None:
    """解析逗号分隔的股票代码列表。"""
    if not value:
        return None
    codes: list[str] = [c.strip() for c in value.split(",") if c.strip()]
    return codes or None


def resolve_strategy(spec: str) -> type[BaseStrategy]:
    """解析策略名称或 module:ClassName 为策略类。

    :raises ValueError: 无法解析或类不是 BaseStrategy 子类
    """
    if spec in STRATEGY_ALIASES:
        module_path, class_name = STRATEGY_ALIASES[spec]
    elif ":" in spec:
        module_path, class_name = spec.split(":", 1)
    elif "." in spec:
        module_path, _, class_name = spec.rpartition(".")
    else:
        raise ValueError(
            f"未知策略: {spec}，可用快捷名: {', '.join(STRATEGY_ALIASES)}，"
            "或使用 module:ClassName"
        )
    try:
        module = importlib.import_module(module_path)
        strategy_cls: type = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"加载策略失败 {module_path}:{class_name}: {exc}") from exc
    if not issubclass(strategy_cls, BaseStrategy):
        raise ValueError(f"{module_path}:{class_name} 不是 BaseStrategy 的子类")
    return strategy_cls


def _load_local_hq_defaults() -> tuple[str, str]:
    """从 cfg/stock.cfg 的 [local_hq] 节读本地归档的默认配置。

    读不到（没有该节、文件缺失、格式错误）时返回空 root + off，等价于不启用，
    与改动前的行为完全一致。
    """
    try:
        from configparser import ConfigParser

        from utils.tools import resource_path

        cp = ConfigParser()
        cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
        if not cp.has_section("local_hq"):
            return "", MODE_OFF
        root = (cp.get("local_hq", "root", fallback="") or "").strip()
        mode = (cp.get("local_hq", "mode", fallback=MODE_OFF) or "").strip()
        return root, (mode or MODE_OFF)
    except Exception:  # noqa: BLE001 - 配置读不到就按「不启用」处理
        return "", MODE_OFF


def build_backtest_config(args: argparse.Namespace) -> BacktestConfig:
    """根据命令行参数构建回测配置。"""
    if not args.codes and not args.pool_name and not args.pool_id:
        raise ValueError("请至少提供 --codes / --pool-name / --pool-id 之一")

    # CLI 显式传值优先，未传时用 cfg 的默认值
    local_root, local_mode = _load_local_hq_defaults()
    if args.local_hq_dir is not None:
        local_root = args.local_hq_dir.strip()
    if args.local_hq_mode is not None:
        local_mode = args.local_hq_mode.strip()

    return BacktestConfig(
        initial_capital=args.capital,
        benchmark_code=args.benchmark,
        pool_id=args.pool_id,
        pool_name=args.pool_name,
        stock_codes=_parse_codes(args.codes),
        start_date=args.start,
        end_date=args.end,
        hold_days=args.hold_days,
        signal_window=args.signal_window,
        frequency=args.frequency,
        position_size_pct=args.position_size,
        split_capital_by_code=args.split_capital_by_code,
        fetch_retries=args.fetch_retries,
        fetch_retry_interval=args.fetch_retry_interval,
        max_drawdown_pct=(0.0 if args.no_account_drawdown else args.max_drawdown),
        storm_filter_enabled=args.storm_filter,
        storm_time_gap_minutes=args.storm_time_gap_minutes,
        storm_price_change_pct=args.storm_price_change_pct,
        min_trade_interval_minutes=args.min_trade_interval_minutes,
        buy_dip_gate_enabled=args.buy_dip_gate,
        buy_dip_gap_minutes=args.buy_dip_gap_minutes,
        enable_daily_gates=args.enable_daily_gates,
        market_breadth_csv=args.market_breadth_csv,
        local_hq_root=local_root,
        local_hq_mode=local_mode,
        trade_config=TradeCostConfig(
            buy_slippage=args.buy_slip,
            sell_slippage=args.sell_slip,
            fee_pct=args.fee_pct,
            fee_low=args.fee_low,
        ),
    )


def export_json(
    path: str,
    config: BacktestConfig,
    trades: list[TradeResult],
    summary: BacktestSummary,
) -> None:
    """将回测配置、交易明细与汇总导出为 JSON 文件。"""
    payload: dict[str, Any] = {
        "config": asdict(config),
        "summary": asdict(summary),
        "trades": [asdict(t) for t in trades],
    }
    out_path = Path(path)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    """回测命令行入口，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose:
        # 回测独立于服务进程，不加载 cfg/log.yaml（那会写到 log 文件且带业务降噪规则）。
        # 需要观察门槛注入/选股筛选时，显式在 stdout 上打开 INFO。
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s|%(levelname)s|%(name)s|%(message)s",
            stream=sys.stdout,
        )
        # httpx 每个请求一行 INFO，长周期回测会刷出上万行、把关键日志埋掉
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        strategy_cls = resolve_strategy(args.strategy)
        config = build_backtest_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    engine = BacktestEngine(config=config)
    trades: list[TradeResult]
    summary: BacktestSummary
    try:
        # 先实例化策略，run/save_results 复用同一实例，
        # 保证 strategy_id（run 内按名称回填）与 strategy_name 在保存时可见
        strategy_instance = strategy_cls()
        if args.no_trend_gate:
            # 实例属性遮蔽类属性：判定照做（日志照记），只是不产生门槛偏移，
            # 用于对照「趋势门槛开/关」的差异。
            strategy_instance.TREND_GATE_ENABLED = False
            print("[backtest] 已关闭日线趋势门槛（TREND_GATE_ENABLED=False）", flush=True)
        if args.no_buy_signal_stop:
            # 同样是实例属性遮蔽：买入信号价照常记录，只是卖出侧不据此止损。
            strategy_instance.SELL_BUY_PRICE_BREAK_ENABLED = False
            print(
                "[backtest] 已关闭买入信号价止损（SELL_BUY_PRICE_BREAK_ENABLED=False）",
                flush=True,
            )
        if args.no_intraday_breaker:
            # 同样是实例属性遮蔽：信号照产，只是不再因「连续亏损卖出」停买、
            # 因「连续卖飞」停卖。
            strategy_instance.INTRADAY_BREAKER_ENABLED = False
            print(
                "[backtest] 已关闭日内熔断（INTRADAY_BREAKER_ENABLED=False）",
                flush=True,
            )
        if args.no_account_drawdown:
            # 同样是实例属性遮蔽。分钟频只有策略侧这条（引擎侧 `_apply_drawdown_exit`
            # 挂在日频），`build_backtest_config` 已把 `max_drawdown_pct` 归零，
            # 这里再关掉策略侧，两条路径才都关干净。
            strategy_instance.RISK_MAX_DRAWDOWN_PCT = 0.0
            print(
                "[backtest] 已关闭账户级回撤（RISK_MAX_DRAWDOWN_PCT=0）",
                flush=True,
            )
        if args.no_shape:
            # 实例属性遮蔽：形态不再计算，买入门槛退回到原口径。
            strategy_instance.SHAPE_ENABLED = False
            print("[backtest] 已关闭日内形态（SHAPE_ENABLED=False）", flush=True)
        if args.shape_max_offset is not None:
            strategy_instance.SHAPE_MAX_OFFSET = float(args.shape_max_offset)
            print(
                f"[backtest] 形态门槛幅度 SHAPE_MAX_OFFSET={args.shape_max_offset}",
                flush=True,
            )
        trades, summary = engine.run(strategy_instance)
    except Exception as exc:
        print(f"[backtest] 回测执行失败: {exc}", flush=True)
        return 1

    if args.output:
        export_json(args.output, config, trades, summary)
        print(f"[backtest] 结果已导出: {args.output}", flush=True)

    if args.save:
        if not args.strategy_id:
            print("[backtest] 保存回测结果需要 --strategy-id", flush=True)
            return 2
        ok = engine.save_results(
            strategy_instance,
            trades,
            summary,
            strategy_id=args.strategy_id,
            user_strategy_id=args.user_strategy_id or "",
        )
        print(
            f"[backtest] 回测结果保存{'成功' if ok else '失败'} "
            f"(strategy_id={args.strategy_id})",
            flush=True,
        )
        if not ok:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from strategy.base_strategy import BaseStrategy


# 常用策略快捷名 -> (模块路径, 类名)
STRATEGY_ALIASES: dict[str, tuple[str, str]] = {
    "strong": ("strategy.strong_rebound_strategy", "StrongReboundStrategy"),
    "rebound": ("strategy.strong_rebound_strategy", "StrongReboundStrategy"),
    "swing": ("strategy.swing_trading_strategy", "SwingTradingStrategy"),
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
    parser.add_argument("--signal-window", type=int, default=20, help="信号检测窗口（条）")
    parser.add_argument(
        "--frequency", choices=("daily", "minute"), default="daily", help="K线频率"
    )
    parser.add_argument(
        "--position-size", type=float, default=1.0, help="每笔交易使用资金比例（0-1）"
    )
    parser.add_argument("--benchmark", default="000300", help="基准指数代码，为空则不做基准对比")

    cost = parser.add_argument_group("交易成本")
    cost.add_argument("--buy-slip", type=float, default=0.2, help="买入滑点（元）")
    cost.add_argument("--sell-slip", type=float, default=0.2, help="卖出滑点（元）")
    cost.add_argument(
        "--fee-pct", type=float, default=0.02,
        help="手续费百分率，如 0.02 表示 0.02 个百分点",
    )
    cost.add_argument("--fee-low", type=float, default=5.0, help="最低手续费（元）")

    out = parser.add_argument_group("结果输出")
    out.add_argument("--output", help="将回测结果导出为 JSON 文件路径")
    out.add_argument(
        "--save", action="store_true", help="将回测结果保存到服务端（需 --strategy-id）"
    )
    out.add_argument(
        "--strategy-id",
        help="保存回测结果所需的策略模板 ID（StrategyDao._id）",
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


def build_backtest_config(args: argparse.Namespace) -> BacktestConfig:
    """根据命令行参数构建回测配置。"""
    if not args.codes and not args.pool_name and not args.pool_id:
        raise ValueError("请至少提供 --codes / --pool-name / --pool-id 之一")
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
    try:
        strategy_cls = resolve_strategy(args.strategy)
        config = build_backtest_config(args)
    except ValueError as exc:
        parser.error(str(exc))

    engine = BacktestEngine(config=config)
    trades: list[TradeResult]
    summary: BacktestSummary
    try:
        trades, summary = engine.run(strategy_cls)
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
            strategy_cls, trades, summary, strategy_id=args.strategy_id
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

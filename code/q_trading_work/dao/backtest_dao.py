"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-09-14 23:00:00
Description: 策略回测结果数据对象 —— 本端回测结果的**唯一数据载体**。

    BacktestEngine 跑完回测后，把汇总与交易明细装进 BacktestDao，
    再交给 BacktestApi 通过
        POST /api/strategy/{strategy_id}/backtest
    写入服务端 backtest_tbl；查询时由 BacktestApi 反序列化回 BacktestDao 列表。

    注意服务端表结构只持久化 `strategy_id` / `result_data` / `create_time` 三个字段，
    因此 DAO 的标量字段（收益率、回撤、频率…）统一打包进 `result_data`，
    由 `to_result_data()` 组装、`from_db()` 反向解析（同时兼容扁平字段形态）。
"""

from dataclasses import dataclass, field
import logging
from typing import Any

from utils.tools import to_float

logger = logging.getLogger(__name__)


@dataclass
class BacktestDao:
    """策略回测结果数据对象，通过 strategy_id 关联 StrategyDao。

    Attributes:
        id: 服务端记录 ID（`_id`，保存前为空）
        strategy_id: 策略模板 ID（StrategyDao._id）
        backtest_return_rate: 回测收益率（比例，如 0.15 表示 15%）
        backtest_profit: 回测收益金额
        benchmark_return_rate: 基准收益率（比例，如沪深300同期收益）
        start_date: 回测开始日期 YYYY-MM-DD
        end_date: 回测结束日期 YYYY-MM-DD
        initial_amount: 本金
        max_drawdown: 最大回撤（比例，如 0.20 表示 20%）
        frequency: 频率（daily / minute）
        result_data: 回测明细 JSON（交易列表 trades、汇总 summary、策略名等）
        create_time: 创建时间
    """

    id: str
    strategy_id: str
    backtest_return_rate: float  # 回测收益率（如 0.15 表示 15%）
    backtest_profit: float  # 回测收益金额
    benchmark_return_rate: float  # 基准收益率（如沪深300同期收益）
    start_date: str  # 回测开始日期 YYYY-MM-DD
    end_date: str  # 回测结束日期 YYYY-MM-DD
    initial_amount: float  # 本金
    max_drawdown: float  # 最大回撤（如 0.20 表示 20%）
    frequency: str  # 频率（如 minute / daily / weekly / monthly）
    result_data: dict[str, Any] = field(default_factory=dict)  # 回测结果 JSON（完整数据）
    create_time: str = ""

    def __init__(self, id: str = "", strategy_id: str = "") -> None:
        self.id = id
        self.strategy_id = strategy_id
        self.backtest_return_rate = 0.0
        self.backtest_profit = 0.0
        self.benchmark_return_rate = 0.0
        self.start_date = ""
        self.end_date = ""
        self.initial_amount = 0.0
        self.max_drawdown = 0.0
        self.frequency = ""
        self.result_data = {}
        self.create_time = ""

    # ---- 组装：DAO -> 服务端 ----

    def to_result_data(self) -> dict[str, Any]:
        """组装写入服务端 `result_data` 字段的完整 JSON。

        服务端只保存 `result_data` 一个 JSON 字段，因此这里把 DAO 的全部标量
        字段与明细（trades / summary）合并成一份自描述的 JSON，
        前端拿到单条记录即可还原全部回测指标。

        :return: 回测结果 JSON 字典
        """
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.result_data.get("strategy_name", ""),
            "backtest_return_rate": self.backtest_return_rate,
            "backtest_profit": self.backtest_profit,
            "benchmark_return_rate": self.benchmark_return_rate,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_amount": self.initial_amount,
            "max_drawdown": self.max_drawdown,
            "frequency": self.frequency,
            "create_time": self.create_time,
            "trades": self.result_data.get("trades", []),
            "summary": self.result_data.get("summary", {}),
        }

    def to_payload(self) -> dict[str, Any]:
        """组装 HTTP 请求体（BacktestSaveRequest：{"result_data": {...}}）。

        :return: POST /api/strategy/{strategy_id}/backtest 的请求体
        """
        return {"result_data": self.to_result_data()}

    def to_db(self) -> dict[str, Any]:
        """组装服务端 Mongo 文档形态（backtest_tbl 表结构）。

        :return: {"strategy_id": ..., "result_data": {...}, "create_time": ...}
        """
        return {
            "strategy_id": self.strategy_id,
            "result_data": self.to_result_data(),
            "create_time": self.create_time,
        }

    # ---- 解析：服务端 -> DAO ----

    def from_db(self, data: dict[str, Any]) -> None:
        """从服务端返回的记录还原 DAO 字段。

        兼容两种形态：
        1. 服务端文档 —— 标量字段在 `result_data` 内层（`{"strategy_id","result_data","create_time"}`）；
        2. 扁平记录 —— 标量字段与 `result_data` 同级（历史/本地上报形态）。

        :param data: 服务端返回的单条回测记录
        """
        payload: Any = data.get("result_data")
        if not isinstance(payload, dict):
            payload = data

        self.id = str(data.get("_id", "") or data.get("id", "") or "")
        self.strategy_id = str(
            data.get("strategy_id", "") or payload.get("strategy_id", "") or ""
        )
        self.backtest_return_rate = to_float(payload.get("backtest_return_rate", 0.0))
        self.backtest_profit = to_float(payload.get("backtest_profit", 0.0))
        self.benchmark_return_rate = to_float(
            payload.get("benchmark_return_rate", 0.0)
        )
        self.start_date = payload.get("start_date", "")
        self.end_date = payload.get("end_date", "")
        self.initial_amount = to_float(payload.get("initial_amount", 0.0))
        self.max_drawdown = to_float(payload.get("max_drawdown", 0.0))
        self.frequency = payload.get("frequency", "")
        self.create_time = str(
            data.get("create_time", "") or payload.get("create_time", "") or ""
        )
        # result_data 保留明细（trades / summary / strategy_name 等）
        self.result_data = dict(payload)

    # ---- 构建：回测结果 -> DAO ----

    @classmethod
    def from_db_record(cls, data: dict[str, Any]) -> "BacktestDao":
        """由服务端记录构建 DAO。

        :param data: 服务端返回的单条回测记录
        :return: BacktestDao 实例
        """
        dao = cls()
        dao.from_db(data)
        return dao

    @classmethod
    def from_summary(
        cls,
        strategy_id: str,
        summary: Any,
        trades: list[Any] | None = None,
        frequency: str = "",
        strategy_name: str = "",
        create_time: str = "",
    ) -> "BacktestDao":
        """由回测汇总（BacktestSummary）与交易明细构建 DAO。

        收益率 / 回撤在汇总里是百分数（如 10.0 表示 10%），
        写入 DAO 时统一换算为比例（0.10），与服务端字段语义保持一致。

        :param strategy_id: 策略模板 ID（StrategyDao._id）
        :param summary: 回测汇总对象（backtest_engine.BacktestSummary）
        :param trades: 交易明细列表（TradeResult），可为 None
        :param frequency: K线频率（daily / minute）
        :param strategy_name: 策略名称（为空时取 summary.strategy_name）
        :param create_time: 创建时间（为空由调用方补当前时间）
        :return: BacktestDao 实例
        """
        dao = cls(strategy_id=strategy_id)
        dao.backtest_return_rate = to_float(
            getattr(summary, "total_return_pct", 0.0)
        ) / 100
        dao.backtest_profit = to_float(getattr(summary, "total_profit", 0.0))
        dao.benchmark_return_rate = to_float(
            getattr(summary, "benchmark_return_pct", 0.0)
        ) / 100
        dao.start_date = getattr(summary, "start_date", "") or ""
        dao.end_date = getattr(summary, "end_date", "") or ""
        dao.initial_amount = to_float(getattr(summary, "initial_capital", 0.0))
        dao.max_drawdown = to_float(
            getattr(summary, "max_drawdown_pct", 0.0)
        ) / 100
        dao.frequency = frequency
        dao.create_time = create_time
        dao.result_data = {
            "strategy_name": strategy_name
            or getattr(summary, "strategy_name", "")
            or "",
            "trades": [_trade_to_dict(t) for t in (trades or [])],
            "summary": {
                "total_trades": getattr(summary, "total_trades", 0),
                "win_count": getattr(summary, "win_count", 0),
                "loss_count": getattr(summary, "loss_count", 0),
                "win_rate": getattr(summary, "win_rate", 0.0),
                "avg_profit": getattr(summary, "avg_profit", 0.0),
                "max_profit": getattr(summary, "max_profit", 0.0),
                "max_loss": getattr(summary, "max_loss", 0.0),
                "annualized_return_pct": getattr(
                    summary, "annualized_return_pct", 0.0
                ),
                "benchmark_annualized_pct": getattr(
                    summary, "benchmark_annualized_pct", 0.0
                ),
                "sharpe_ratio": getattr(summary, "sharpe_ratio", 0.0),
                "max_drawdown_pct": getattr(summary, "max_drawdown_pct", 0.0),
                "alpha": getattr(summary, "alpha", 0.0),
                "initial_capital": getattr(summary, "initial_capital", 0.0),
                "final_capital": getattr(summary, "final_capital", 0.0),
                "start_date": getattr(summary, "start_date", "") or "",
                "end_date": getattr(summary, "end_date", "") or "",
                "benchmark_code": getattr(summary, "benchmark_code", "") or "",
                "trading_days": getattr(summary, "trading_days", 0),
            },
        }
        return dao


def _trade_to_dict(trade: Any) -> dict[str, Any]:
    """将交易明细对象转换为可 JSON 序列化的字典。

    :param trade: TradeResult 或已是对应的字典
    :return: 交易明细字典
    """
    if isinstance(trade, dict):
        return dict(trade)
    return {
        "code": getattr(trade, "code", ""),
        "buy_date": getattr(trade, "buy_date", ""),
        "sell_date": getattr(trade, "sell_date", ""),
        "buy_price": getattr(trade, "buy_price", 0.0),
        "sell_price": getattr(trade, "sell_price", 0.0),
        "profit_pct": getattr(trade, "profit_pct", 0.0),
    }

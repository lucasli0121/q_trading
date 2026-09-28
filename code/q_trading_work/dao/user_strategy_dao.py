"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 用户策略关联数据对象 — 将全局策略与用户关联，记录用户维度的策略状态和股票池
"""

from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# 用户策略未显式配置 `initial_amount` / `max_stock_count` 时的默认值，
# 与服务端 `UserStrategyCreateRequest` / `UserStrategyItem` 的默认值保持一致：
# 不限制持仓数量（-1）+ 10 万初始资金。
#
# 注意 `0` 是**有效配置语义**（= 限制买入，不允许新建持仓或加仓，2026-09-24 确认），
# 不是默认值。因此下面两个解析函数只在「字段缺失 / 空串 / 非数字」时落回默认，
# 绝不能写成 `raw or DEFAULT` —— 那会把显式配置的 0 洗成默认值，限制直接失效，
# 而这类错误不会报错、只会让策略「突然开始下单」。
DEFAULT_INITIAL_AMOUNT: float = 100000.0
DEFAULT_MAX_STOCK_COUNT: int = -1


def parse_initial_amount(raw: Any) -> float:
    """把服务端返回的 `initial_amount` 规范化成 float。

    区分三种输入（这是本项目最容易出错的一处口径）：

    - `0` / `0.0` → 原样返回 0.0（**限制买入**，不是「未配置」）
    - 合法数字（含负数）→ 原样返回
    - `None` / `""` / 非数字 → `DEFAULT_INITIAL_AMOUNT`（10 万元）

    :param raw: 服务端/DAO 返回的原始值
    :return: 规范化后的初始资金
    """
    if raw is None or raw == "":
        return DEFAULT_INITIAL_AMOUNT
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("initial_amount 取值非法: %r，回退默认 %.2f", raw, DEFAULT_INITIAL_AMOUNT)
        return DEFAULT_INITIAL_AMOUNT


def parse_max_stock_count(raw: Any) -> int:
    """把服务端返回的 `max_stock_count` 规范化成 int。

    - `-1`（或任何负数）→ 不限制持仓数量，原样返回
    - `0` → 原样返回（**限制买入**，不是「未配置」）
    - 正数 → 持仓上限，原样返回
    - `None` / `""` / 非数字 → `DEFAULT_MAX_STOCK_COUNT`（-1，不限制）

    :param raw: 服务端/DAO 返回的原始值
    :return: 规范化后的最大持仓数量
    """
    if raw is None or raw == "":
        return DEFAULT_MAX_STOCK_COUNT
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "max_stock_count 取值非法: %r，回退默认 %d", raw, DEFAULT_MAX_STOCK_COUNT
        )
        return DEFAULT_MAX_STOCK_COUNT


class StrategyStatus(Enum):
    """策略状态枚举"""

    RUNNING = "running"  # 运行中
    STOPPED = "stopped"  # 已停止
    PAUSED = "paused"  # 已暂停
    ERROR = "error"  # 错误状态


@dataclass
class UserStrategyDao:
    """用户策略关联数据对象，通过 strategy_id 关联 StrategyDao"""

    id: str
    name: str # 自定义名称
    strategy_id: str  # 关联策略 ID（StrategyDao._id）
    status: str  # 策略状态: running / stopped / paused
    user_id: str  # 所属用户 ID（关联 UserDao.id）
    pool_id: str  # 关联股票池 ID（关联 StockPoolDao.id）
    initial_amount: float  # 初始金额
    total_profit: float  # 总收益金额
    max_stock_count: int  # 最大持仓数量
    strategy_params: dict  # 策略运行参数
    update_time: str # 更新状态的时间
    create_time: str  # 创建时间
    # 服务端记录里是否带了 `initial_amount` 字段（含值为 0）。
    # `0` 是有效语义（限制买入），必须与「字段缺失」区分：缺字段才允许用
    # 历史执行记录里的值兜底，见 `StrategyWorkflow._load_execution_data`。
    initial_amount_configured: bool = False

    def __init__(
        self,
        id: str = "",
        name: str = "",
        strategy_id: str = "",
        status: str = StrategyStatus.STOPPED.value,
        user_id: str = "",
        pool_id: str = "",
        initial_amount: float = DEFAULT_INITIAL_AMOUNT,
        total_profit: float = 0.0,
        max_stock_count: int = DEFAULT_MAX_STOCK_COUNT,
        strategy_params: dict = {},
        update_time: str = "",
        create_time: str = "",
        initial_amount_configured: bool = False,
    ) -> None:
        """初始化用户策略关联对象

        :param id: 记录 ID（MongoDB _id）
        :param name: 个人策略自定义名称
        :param strategy_id: 关联的全局策略 ID
        :param status: 策略状态
        :param user_id: 所属用户 ID
        :param pool_id: 关联股票池 ID
        :param initial_amount: 初始金额（**0 表示限制买入**；未配置时默认 10 万元）
        :param strategy_params: 策略运行参数
        :param update_time: 更新状态时间
        :param create_time: 创建时间
        :param initial_amount_configured: 服务端记录是否显式携带 initial_amount 字段
        """
        self.id = id
        self.name = name
        self.strategy_id = strategy_id
        self.status = status
        self.user_id = user_id
        self.pool_id = pool_id
        # None / "" 视为「未配置」落回默认，显式 0 原样保留（限制买入）
        self.initial_amount = parse_initial_amount(initial_amount)
        self.total_profit = total_profit
        self.max_stock_count = parse_max_stock_count(max_stock_count)
        self.strategy_params = strategy_params
        self.update_time = update_time
        self.create_time = create_time
        self.initial_amount_configured = initial_amount_configured

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库记录填充对象字段

        :param data: MongoDB 文档字典
        """
        self.id = str(data.get("_id", ""))
        self.name = data.get("name", "")
        self.strategy_id = data.get("strategy_id", "")
        self.status = data.get("status", StrategyStatus.STOPPED.value)
        self.user_id = data.get("user_id", "")
        self.pool_id = data.get("pool_id", "")
        # `0` 是有效配置（限制买入），下面的解析函数不会用 `or` 把它洗掉；
        # 只有字段缺失/空串/非法值才落回默认（10 万 / -1 不限制）。
        self.initial_amount = parse_initial_amount(data.get("initial_amount"))
        # 区分「显式配置 0」与「字段缺失」：前者是限制买入，后者才允许兜底
        # （空串等同于未配置，与服务端写入行为对齐）
        self.initial_amount_configured = data.get("initial_amount") not in (None, "")
        self.total_profit = float(data.get("total_profit", 0.0) or 0.0)
        self.max_stock_count = parse_max_stock_count(data.get("max_stock_count"))
        self.strategy_params = data.get("strategy_params", {})
        self.update_time = str(data.get("update_time", ""))
        self.create_time = str(data.get("create_time", ""))

    def to_db(self) -> dict[str, Any]:
        """将对象转换为数据库可存储的字典

        :return: 数据库文档字典
        """
        return {
            "name": self.name,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "user_id": self.user_id,
            "pool_id": self.pool_id,
            "initial_amount": self.initial_amount,
            "total_profit": self.total_profit,
            "max_stock_count": self.max_stock_count,
            "strategy_params": self.strategy_params,
            "update_time": self.update_time,
            "create_time": self.create_time,
        }

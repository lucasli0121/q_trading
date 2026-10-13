"""
Author: liguoqiang
Date: 2026-10-13 17:00:00
LastEditors: liguoqiang
LastEditTime: 2026-10-13 17:00:00
Description: 每日市场宽度（涨跌停家数）MongoDB 操作实现

以 **trade_date** 为唯一键。写入方是 q_trading_work 盘前任务，
用于记录「当天为什么对买卖门槛做了市场级偏移」，供事后归因。
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoMarketBreadthImpl:
    """每日市场宽度表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.breadth_table = self.mongo_exec.db["market_breadth_daily_tbl"]

    def upsert_breadth(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新一条市场宽度记录（唯一键：trade_date）

        :param data: 市场宽度字典
        :return: (ok, record_id_or_None)
        """
        try:
            data = data.copy()
            trade_date = data.get("trade_date", "")
            if not trade_date:
                self.logger.error("市场宽度记录缺 trade_date")
                return False, None
            if "id" in data:
                del data["id"]
            data.pop("_id", None)
            filter_dict: dict[str, Any] = {"trade_date": trade_date}
            result = self.breadth_table.update_one(filter_dict, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新市场宽度失败: {e}")
            return False, None

    def query_breadth(self, trade_date: str = "") -> tuple[bool, Any | None]:
        """查询市场宽度记录（按日期倒序）

        :param trade_date: 交易日 YYYY-MM-DD，空串表示不过滤
        :return: (ok, 记录列表)
        """
        try:
            query: dict[str, Any] = {}
            if trade_date:
                query["trade_date"] = trade_date
            return self.mongo_exec.query_by_condition(
                table=self.breadth_table,
                condition=query,
                sort={"trade_date": -1},
                skip=None,
                limit=0,
            )
        except Exception as e:
            self.logger.error(f"查询市场宽度失败: {e}")
            return False, None

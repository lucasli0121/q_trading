"""
Author: liguoqiang
Date: 2026-10-13 17:00:00
LastEditors: liguoqiang
LastEditTime: 2026-10-13 17:00:00
Description: 每日个股阀门表 MongoDB 操作实现

阀门由 q_trading_work 盘后计算（当日买入后跌破 S1 / 卖出后站上 R1 达 N 次则上调
次日同侧门槛），以 **(user_strategy_id, stock_code, trade_date)** 为唯一键落库，
盘前再由本端读取注入策略。见 q_trading_work/strategy/valve.py。
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext


class MongoStrategyDailyValveImpl:
    """每日个股阀门表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.valve_table = self.mongo_exec.db["strategy_daily_valve_tbl"]

    def upsert_valve(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新一条阀门记录（唯一键：user_strategy_id + stock_code + trade_date）

        :param data: 阀门字典
        :return: (ok, record_id_or_None)
        """
        try:
            data = data.copy()
            user_strategy_id = data.get("user_strategy_id", "")
            stock_code = data.get("stock_code", "")
            trade_date = data.get("trade_date", "")
            if not user_strategy_id or not stock_code or not trade_date:
                self.logger.error(
                    "每日阀门缺关键字段: user_strategy_id=%s stock_code=%s trade_date=%s",
                    user_strategy_id, stock_code, trade_date,
                )
                return False, None
            if "id" in data:
                del data["id"]
            data.pop("_id", None)

            filter_dict: dict[str, Any] = {
                "user_strategy_id": user_strategy_id,
                "stock_code": stock_code,
                "trade_date": trade_date,
            }
            result = self.valve_table.update_one(filter_dict, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"插入或更新每日阀门失败: {e}")
            return False, None

    def query_valves(
        self, user_strategy_id: str, trade_date: str = ""
    ) -> tuple[bool, Any | None]:
        """按用户策略查询阀门记录（可按交易日过滤）

        :param user_strategy_id: 用户策略 ID
        :param trade_date: 交易日 YYYY-MM-DD，空串表示不过滤（返回该策略全部记录，
            按交易日倒序）
        :return: (ok, 记录列表)
        """
        try:
            query: dict[str, Any] = {"user_strategy_id": user_strategy_id}
            if trade_date:
                query["trade_date"] = trade_date
            return self.mongo_exec.query_by_condition(
                table=self.valve_table,
                condition=query,
                sort={"trade_date": -1},
                skip=None,
                limit=0,
            )
        except Exception as e:
            self.logger.error(f"查询每日阀门失败: {e}")
            return False, None

    def delete_by_user_strategy(self, user_strategy_id: str) -> bool:
        """删除某用户策略的全部阀门记录

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            query: dict[str, Any] = {"user_strategy_id": user_strategy_id}
            return self.mongo_exec.delete(table=self.valve_table, condition=query)
        except Exception as e:
            self.logger.error(f"删除每日阀门失败: {e}")
            return False

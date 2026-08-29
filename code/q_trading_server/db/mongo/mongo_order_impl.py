"""
Author: liguoqiang
Date: 2026-07-02 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-07-02 00:00:00
Description: 订单表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from bson import ObjectId

from app_context import AppContext


class MongoOrderImpl:
    """订单表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.order_table = self.mongo_exec.db["order_tbl"]

    def save_order(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """保存订单记录。"""
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            result = self.order_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"保存订单失败: {e}")
            return False, None

    def update_order_status(self, order_id: str, status: str) -> bool:
        """更新订单状态。"""
        try:
            if not order_id:
                return False
            result = self.order_table.update_one(
                {"_id": ObjectId(order_id)},
                {"$set": {"status": status}},
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            self.logger.error(f"更新订单状态失败: {e}")
            return False

    def update_order(self, order_id: str, data: dict[str, Any]) -> bool:
        """更新订单字段（仅更新传入的非空字段）。

        :param order_id: 订单 ID
        :param data: 要更新的字段字典，仅更新值不为 None 的字段
        :return: 成功返回 True，否则返回 False
        """
        try:
            if not order_id:
                return False
            # 过滤掉值为 None 的字段，且不允许更新 _id 和 user_strategy_id
            set_data = {
                k: v
                for k, v in data.items()
                if v is not None and k not in ("id", "_id", "user_strategy_id")
            }
            if not set_data:
                return False
            result = self.order_table.update_one(
                {"_id": ObjectId(order_id)},
                {"$set": set_data},
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            self.logger.error(f"更新订单失败: {e}")
            return False

    def query_order_by_id(self, order_id: str) -> tuple[bool, Any | None]:
        """按订单 ID 查询订单。"""
        try:
            return self.mongo_exec.query_by_condition(
                table=self.order_table,
                condition={"_id": ObjectId(order_id)},
                sort=None,
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按 ID 查询订单失败: {e}")
            return False, None

    def query_orders_by_user_strategy(self, user_strategy_id: str) -> tuple[bool, Any | None]:
        """按用户策略 ID 查询订单列表。"""
        try:
            return self.mongo_exec.query_by_condition(
                table=self.order_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"create_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"按策略查询订单失败: {e}")
            return False, None

    def query_orders_by_user_strategy_ids(
        self,
        user_strategy_ids: list[str],
        start_time: str | None = None,
        end_time: str | None = None,
        status: str | None = None,
        action: str | None = None,
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 列表批量查询订单列表

        :param user_strategy_ids: 用户策略 ID 列表
        :param start_time: 起始时间（格式 %Y-%m-%d %H:%M:%S），为空则不限制起始时间
        :param end_time: 结束时间（格式 %Y-%m-%d %H:%M:%S），为空则不限制结束时间
        :param status: 订单状态，为空则查询所有状态
        :param action: 订单动作（买入/卖出），为空则查询所有动作
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            if not user_strategy_ids:
                return True, []
            condition: dict[str, Any] = {"user_strategy_id": {"$in": user_strategy_ids}}
            create_time_cond: dict[str, Any] = {}
            if start_time:
                create_time_cond["$gte"] = start_time
            if end_time:
                create_time_cond["$lte"] = end_time
            if create_time_cond:
                condition["create_time"] = create_time_cond
            if status:
                condition["status"] = status
            if action:
                condition["action"] = action
            return self.mongo_exec.query_by_condition(
                table=self.order_table,
                condition=condition,
                sort={"create_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"批量查询用户订单失败: {e}")
            return False, None

    def delete_order(self, order_id: str) -> bool:
        """删除订单。"""
        try:
            if not order_id:
                return False
            return self.mongo_exec.delete(
                table=self.order_table,
                condition={"_id": ObjectId(order_id)},
            )
        except Exception as e:
            self.logger.error(f"删除订单失败: {e}")
            return False

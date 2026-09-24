"""
Author: liguoqiang
Date: 2026-06-25 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-07-13 00:00:00
Description: 策略执行结果表 MongoDB 操作实现
"""

# coding="utf8"

import logging
from typing import Any

from app_context import AppContext
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from utils import tools


class MongoStrategyExecutionImpl:
    """策略执行结果表 MongoDB 操作实现"""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.execution_table = self.mongo_exec.db["strategy_execution_tbl"]

    def _user_strategy_ids_by_user(self, user_id: str) -> list[str]:
        """取指定用户下的全部用户策略 ID。

        策略执行结果表只存 user_strategy_id，没有 user_id 字段，
        所以任何「按用户」的查询都要先经用户策略表做一次 ID 映射。

        :param user_id: 用户 ID
        :return: 用户策略 ID 列表；用户无策略时返回空列表
        """
        if not user_id:
            return []
        user_strategy_impl = MongoUserStrategyImpl()
        ok, records = user_strategy_impl.query_user_strategies_by_user(user_id=user_id)
        if not ok or not records:
            return []
        return [str(r.get("_id", "")) for r in records if r.get("_id")]

    def save_execution(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """保存策略执行结果（每次插入新记录，同一 user_strategy_id 可有多条记录）

        :param data: 执行结果数据字典
        :return: 成功返回 True 和记录 ID，失败返回 False 和错误信息
        """
        try:
            data = data.copy()
            if "id" in data:
                del data["id"]
            user_strategy_id = data.get("user_strategy_id", "")
            if not user_strategy_id:
                self.logger.error("user_strategy_id is empty.")
                return False, "user_strategy_id 为空"
            result = self.execution_table.insert_one(data)
            return True, str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"保存策略执行结果失败: {e}")
            return False, None

    def upsert_execution(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        """插入或更新策略执行结果（按 user_strategy_id 唯一）

        :param data: 执行结果数据字典
        :return: 成功返回 True 和记录 ID，否则返回 False 和 None
        """
        try:
            data = data.copy()
            user_strategy_id = data.get("user_strategy_id", "")
            if not user_strategy_id:
                self.logger.error("user_strategy_id is empty.")
                return False, None
            if "id" in data:
                del data["id"]
            filter: dict[str, Any] = {"user_strategy_id": user_strategy_id}
            result = self.execution_table.update_one(filter, {"$set": data}, upsert=True)
            return True, str(result.upserted_id) if result.upserted_id else None
        except Exception as e:
            self.logger.error(f"upsert 策略执行结果失败: {e}")
            return False, None

    def query_execution_by_user_strategy(
        self, user_strategy_id: str
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 查询执行结果

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"update_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"查询策略执行结果失败: {e}")
            return False, None

    def query_execution_by_user_id(self, user_id: str) -> tuple[bool, Any|None]:
        """按用户 ID 查询该用户所有策略的执行结果

        用户没有任何用户策略时返回空列表（而不是 False），
        以免调用方把「无数据」误判成查询失败。

        :param user_id: 用户 ID
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            user_strategy_ids = self._user_strategy_ids_by_user(user_id)
            if not user_strategy_ids:
                return True, []
            return self.query_executions_by_user_strategy_ids(user_strategy_ids)
        except Exception as e:
            self.logger.error(f"按用户查询策略执行结果失败: {e}")
            return False, None

    def query_executions_by_user_and_time_range(
        self,
        user_id: str = "",
        start_time: str = "",
        end_time: str = "",
        skip: int | None = None,
        limit: int | None = None,
    ) -> tuple[bool, Any | None]:
        """按用户与 update_time 范围查询策略执行结果

        过滤条件由两部分组成，都下推到 MongoDB 执行：

        - user_strategy_id：用户策略结果表没有 user_id 字段，用 user_id 查时
          先取该用户的用户策略 ID 列表，再以 ``$in`` 匹配；user_id 为空表示
          不按用户过滤（查全部用户策略的执行结果，供管理员使用）。
        - update_time：字符串范围比较。库中值为 "%Y-%m-%d %H:%M:%S"，
          字典序与时间序一致；只传日期时按边界语义补全时刻，
          上界补 23:59:59，否则 ``$lte "2026-09-23"`` 会把当天记录全部漏掉。

        命中线上 ``{user_strategy_id: 1, update_time: -1}`` 复合索引，
        且该索引不带 collation，所以查询侧同样不能指定 collation。

        :param user_id: 用户 ID，为空表示不按用户过滤
        :param start_time: 起始时间（含），支持 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，空表示不限
        :param end_time: 结束时间（含），格式同上，空表示不限
        :param skip: 分页偏移
        :param limit: 返回条数上限，未传时按 query_by_condition 的兜底上限截断
        :return: 成功返回 True 和记录列表（无匹配时为空列表），
                 时间格式非法或查询异常返回 False 和 None
        """
        try:
            try:
                start_bound = tools.normalize_query_time(start_time, is_end=False)
                end_bound = tools.normalize_query_time(end_time, is_end=True)
            except ValueError as e:
                self.logger.error(f"时间范围参数非法: {e}")
                return False, None

            condition: dict[str, Any] = {}
            if user_id:
                user_strategy_ids = self._user_strategy_ids_by_user(user_id)
                if not user_strategy_ids:
                    # 该用户没有用户策略，结果必然为空，不必再查执行结果表
                    return True, []
                condition["user_strategy_id"] = {"$in": user_strategy_ids}

            time_condition: dict[str, str] = {}
            if start_bound:
                time_condition["$gte"] = start_bound
            if end_bound:
                time_condition["$lte"] = end_bound
            if time_condition:
                condition["update_time"] = time_condition

            ok, results = self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition=condition,
                sort={"update_time": -1},
                skip=skip,
                limit=limit,
            )
            if not ok:
                return False, None
            return True, results or []
        except Exception as e:
            self.logger.error(f"按用户与时间范围查询策略执行结果失败: {e}")
            return False, None

    def query_latest_execution_by_user_strategy(
        self, user_strategy_id: str
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 查询最近一次执行结果（按 update_time 倒序取 1 条）

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True 和记录列表（最多 1 条），否则返回 False 和 None
        """
        try:
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
                sort={"update_time": -1},
                skip=None,
                limit=1,
            )
        except Exception as e:
            self.logger.error(f"查询最近一次策略执行结果失败: {e}")
            return False, None

    def query_executions_by_user_strategy_ids(
        self, user_strategy_ids: list[str]
    ) -> tuple[bool, Any | None]:
        """按用户策略 ID 列表批量查询执行结果

        :param user_strategy_ids: 用户策略 ID 列表
        :return: 成功返回 True 和记录列表，否则返回 False 和 None
        """
        try:
            if not user_strategy_ids:
                return True, []
            return self.mongo_exec.query_by_condition(
                table=self.execution_table,
                condition={"user_strategy_id": {"$in": user_strategy_ids}},
                sort={"update_time": -1},
                skip=None,
                limit=None,
            )
        except Exception as e:
            self.logger.error(f"批量查询策略执行结果失败: {e}")
            return False, None

    def delete_execution_by_user_strategy(self, user_strategy_id: str) -> bool:
        """删除策略执行结果

        :param user_strategy_id: 用户策略 ID
        :return: 成功返回 True，否则返回 False
        """
        try:
            return self.mongo_exec.delete(
                table=self.execution_table,
                condition={"user_strategy_id": user_strategy_id},
            )
        except Exception as e:
            self.logger.error(f"删除策略执行结果失败: {e}")
            return False

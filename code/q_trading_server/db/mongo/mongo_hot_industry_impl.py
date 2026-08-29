"""
Author: liguoqiang
Date: 2026-07-15
Description: 热门行业 MongoDB 操作实现 — hot_industry_tbl 集合的增删查
"""

import logging
from typing import Any

from app_context import AppContext


class MongoHotIndustryImpl:
    """热门行业数据操作类"""

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.mongo_exec = AppContext().mongo_exec
        self.hot_industry_table = self.mongo_exec.db["hot_industry_tbl"]

    """
    添加热门行业
    :param name: 行业名称
    :return: 成功返回 True 和记录 ID，否则返回 False 和 None
    """

    def add_hot_industry(self, name: str) -> tuple[bool, str | None]:
        try:
            name = name.strip()
            if not name:
                self.logger.error("行业名称不能为空")
                return False, None
            # 检查是否已存在
            check: dict[str, Any] = {"name": name}
            res, existing = self.mongo_exec.query_by_condition(
                table=self.hot_industry_table,
                condition=check,
                sort=None,
                skip=0,
                limit=1,
            )
            if res and existing and len(existing) > 0:
                return False, f"热门行业已存在: {name}"
            result = self.mongo_exec.add(table=self.hot_industry_table, data={"name": name})
            return True, str(result) if result else None
        except Exception as e:
            self.logger.error(f"添加热门行业失败: {e}")
            return False, None

    """
    删除热门行业
    :param name: 行业名称
    :return: 成功返回 True，否则返回 False
    """

    def delete_hot_industry(self, name: str) -> bool:
        try:
            name = name.strip()
            if not name:
                self.logger.error("行业名称不能为空")
                return False
            self.mongo_exec.delete(
                table=self.hot_industry_table,
                condition={"name": name},
            )
            return True
        except Exception as e:
            self.logger.error(f"删除热门行业失败: {e}")
            return False

    """
    查询所有热门行业
    :return: 成功返回 True 和记录列表，否则返回 False 和 None
    """

    def list_hot_industries(self) -> tuple[bool, Any | None]:
        try:
            return self.mongo_exec.query_by_condition(
                table=self.hot_industry_table,
                condition={},
                sort={"name": 1},
                skip=0,
                limit=0,
            )
        except Exception as e:
            self.logger.error(f"查询热门行业列表失败: {e}")
            return False, None

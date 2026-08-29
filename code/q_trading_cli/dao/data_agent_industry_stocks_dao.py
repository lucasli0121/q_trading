#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-21 19:34:39
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:00:00
Description: 数据代理对象 
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DataAgentIndustryStocksDao:
    """代理对象分配股票列表，每个代理对象根据分配的股票代码进行数据抓取和处理"""
    id: str
    agent_name: str  # 代理名称
    # 分配的股票代码映射：键为股票代码，值为行业 id 列表
    # 一个股票代码可能属于多个行业，因此值是一个列表
    stock_codes_industry: list[dict[str, Any]]

    def __init__(
        self,
        id: str = "",
        agent_name: str = "",
        stock_codes_industry: list[dict[str, Any]] | None = None,
    ) -> None:
        """初始化数据代理对象。"""
        self.id = id
        self.agent_name = agent_name
        self.stock_codes_industry = stock_codes_industry if stock_codes_industry is not None else []

    def from_db(self, data: dict[str, Any]) -> None:
        """从数据库字典填充对象字段，兼容缺失值和空字符串。"""
        self.id = str(data.get("_id", ""))
        self.agent_name = data.get("agent_name", "")
        self.stock_codes_industry = data.get("stock_codes_industry", [])

    def to_db(self) -> dict[str, Any]:
        """转换为 MongoDB 存储字典（排除 id，由 MongoDB 自动管理 _id）。"""
        return {
            "agent_name": self.agent_name,
            "stock_codes_industry": self.stock_codes_industry,
        }

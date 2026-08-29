from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class StockInfoDao:
    id: str
    code: str
    name: str
    # 公司全称
    full_name: str
    # 所属板块
    board: str
    #行业
    industry: str
    #概念
    concept: str
    # 上市日期
    list_date: str

    def __init__(self, id: str = "", name: str = "", code: str = "") -> None:
        self.id = id
        self.name = name
        self.code = code

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.full_name = data.get("full_name", "")
        self.board = data.get("board", "")
        self.industry = data.get("industry", "")
        self.concept = data.get("concept", "")
        self.list_date = data.get("list_date", "")

    def to_db(self) -> dict[str, Any]:
        return self.__dict__
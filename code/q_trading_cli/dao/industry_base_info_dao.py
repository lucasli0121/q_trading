import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class IndustryBaseInfoDao:
    id: str
    tick_id: str
    code: str
    name: str
    

    def __init__(self, id: str = "", tick_id: str = "", name: str = "", code: str = "") -> None:
        self.id = id
        self.tick_id = tick_id
        self.name = name
        self.code = code

    def from_db(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("_id", ""))
        self.code = data.get("code", "")
        self.name = data.get("name", "")
        self.tick_id = data.get("tick_id", "")
        
    def to_db(self) -> dict[str, Any]:
        return self.__dict__
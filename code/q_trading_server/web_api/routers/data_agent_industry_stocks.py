"""
Routes for DataAgentIndustryStocks CRUD
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db.mongo.mongo_data_agent_industry_stocks_impl import MongoDataAgentIndustryStocksImpl
from web_api.auth import require_admin
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/data_agent/industry_stocks", tags=["数据代理行业分配"])


class DataAgentIndustryCreateRequest(BaseModel):
    agent_name: str = Field(...)
    stock_codes_industry: dict[str, list[str]] = Field(...)


class DataAgentIndustryItem(BaseModel):
    id: str = Field(default="")
    agent_name: str = Field(default="")
    stock_codes_industry: dict[str, list[str]] = Field(default_factory=dict)


@router.post("/create", response_model=ApiResponse[DataAgentIndustryItem])
async def create_item(req: DataAgentIndustryCreateRequest, user_id: str = Depends(require_admin)):
    impl = MongoDataAgentIndustryStocksImpl()
    ok, rid = impl.add(req.dict())
    if not ok:
        raise HTTPException(status_code=500, detail="创建失败")
    return ApiResponse(data=DataAgentIndustryItem(id=rid or "", agent_name=req.agent_name, stock_codes_industry=req.stock_codes_industry), message="创建成功")


@router.get("/agent/{agent_name}", response_model=ApiResponse[DataAgentIndustryItem])
async def get_by_agent(agent_name: str, user_id: str = Depends(require_admin)):
    impl = MongoDataAgentIndustryStocksImpl()
    res, records = impl.query_by_agent_name(agent_name)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    r = records[0]
    return ApiResponse(data=DataAgentIndustryItem(id=str(r.get("_id", "")), agent_name=r.get("agent_name", ""), stock_codes_industry=r.get("stock_codes_industry", {})))


@router.put("/{id}", response_model=ApiResponse[DataAgentIndustryItem])
async def update_item(id: str, req: DataAgentIndustryCreateRequest, user_id: str = Depends(require_admin)):
    impl = MongoDataAgentIndustryStocksImpl()
    ok = impl.update(id, req.dict())
    if not ok:
        raise HTTPException(status_code=500, detail="更新失败")
    res, records = impl.query_by_id(id)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    r = records[0]
    return ApiResponse(data=DataAgentIndustryItem(id=str(r.get("_id", "")), agent_name=r.get("agent_name", ""), stock_codes_industry=r.get("stock_codes_industry", {})), message="更新成功")


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_item(id: str, user_id: str = Depends(require_admin)):
    impl = MongoDataAgentIndustryStocksImpl()
    ok = impl.delete_by_id(id)
    if not ok:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=id, message="删除成功")

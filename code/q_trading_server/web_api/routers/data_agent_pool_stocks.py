"""
Author: liguoqiang
Date: 2026-08-06 10:30:00
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:30:00
Description: 数据代理股票分配管理路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from web_api.auth import require_admin
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/data_agent_pool/stocks", tags=["数据代理股票分配"])


class DataAgentPoolStockCreateRequest(BaseModel):
    """创建数据代理股票分配请求。

    请求体字段：
    - agent_name: 数据代理名称，必须与 DataAgent 记录一致。
    - stock_codes_pool: 分配给该代理的股票代码映射，键为代码，值为股票池 id 列表。
    """
    agent_name: str = Field(..., description="代理名称，必须与 DataAgent 记录一致")
    stock_codes_pool: dict[str, list[str]] = Field(..., description="分配股票代码映射，不能为空，键=代码，值=股票池 id 列表")


class DataAgentPoolStockUpdateRequest(BaseModel):
    """更新数据代理股票分配请求。"""
    agent_name: str | None = Field(default=None, description="新的代理名称，可选")

    stock_codes_pool: dict[str, list[str]] | None = Field(default=None, description="新的分配股票代码映射，可选")


class DataAgentPoolStockItem(BaseModel):
    """数据代理股票分配条目"""
    id: str = Field(default="", description="记录 ID")
    agent_name: str = Field(default="", description="代理名称")
    stock_codes_pool: dict[str, list[str]] = Field(default_factory=dict, description="分配股票代码映射，键=代码，值=股票池 id 列表")


@router.post("/create", response_model=ApiResponse[DataAgentPoolStockItem])
async def create_data_agent_pool_stock(
    req: DataAgentPoolStockCreateRequest,
    user_id: str = Depends(require_admin),
):
    """创建数据代理股票分配记录。

    仅管理员可调用。
    - 请求体 req.agent_name: 代理名称
    - 请求体 req.stock_codes_pool: 分配股票代码映射
    """
    if not req.stock_codes_pool:
        raise HTTPException(status_code=400, detail="stock_codes_pool 不能为空")
    impl = MongoDataAgentPoolStocksImpl()
    ok, record_id = impl.add_data_agent_pool_stock(req.dict())
    if not ok or not record_id:
        raise HTTPException(status_code=500, detail="创建数据代理股票分配记录失败")
    return ApiResponse(
        data=DataAgentPoolStockItem(
            id=record_id,
            agent_name=req.agent_name,
            stock_codes_pool=req.stock_codes_pool,
        ),
        message="创建成功",
    )


@router.get("/list", response_model=ApiResponse[list[DataAgentPoolStockItem]])
async def list_data_agent_pool_stocks(
    agent_name: str = Query(default="", description="代理名称（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_id: str = Depends(require_admin),
):
    """查询数据代理股票分配记录。

    仅管理员可调用。
    支持按 agent_name 过滤。
    skip/limit 用于分页。
    """
    impl = MongoDataAgentPoolStocksImpl()
    res, records = impl.query_data_agent_pool_stocks(
        agent_name=agent_name,
        skip=skip,
        limit=limit,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询数据代理股票分配记录失败")
    result = [
        DataAgentPoolStockItem(
            id=str(r.get("_id", "")),
            agent_name=r.get("agent_name", ""),
            stock_codes_pool=r.get("stock_codes_pool", {}),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/{id}", response_model=ApiResponse[DataAgentPoolStockItem])
async def get_data_agent_pool_stock(
    id: str,
    user_id: str = Depends(require_admin),
):
    """根据记录 ID 查询单条数据代理股票分配记录。

    仅管理员可调用。
    """
    impl = MongoDataAgentPoolStocksImpl()
    res, records = impl.query_data_agent_pool_stock_by_id(id)
    if not res or not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    return ApiResponse(
        data=DataAgentPoolStockItem(
            id=str(record.get("_id", "")),
            agent_name=record.get("agent_name", ""),
            stock_codes_pool=record.get("stock_codes_pool", {}),
        )
    )


@router.put("/{id}", response_model=ApiResponse[DataAgentPoolStockItem])
async def update_data_agent_pool_stock(
    id: str,
    req: DataAgentPoolStockUpdateRequest,
    user_id: str = Depends(require_admin),
):
    """更新数据代理股票分配记录。

    仅管理员可调用。
    可更新 agent_name、stock_codes_pool。
    """
    data: dict[str, object] = {}
    if req.agent_name is not None:
        data["agent_name"] = req.agent_name
    if req.stock_codes_pool is not None:
        data["stock_codes_pool"] = req.stock_codes_pool
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    impl = MongoDataAgentPoolStocksImpl()
    updated = impl.update_data_agent_pool_stock(id, data)
    if not updated:
        raise HTTPException(status_code=500, detail="更新数据代理股票分配记录失败")
    res, records = impl.query_data_agent_pool_stock_by_id(id)
    if not res or not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    return ApiResponse(
        data=DataAgentPoolStockItem(
            id=str(record.get("_id", "")),
            agent_name=record.get("agent_name", ""),
            stock_codes_pool=record.get("stock_codes_pool", {}),
        ),
        message="更新成功",
    )


@router.delete("/agent/{agent_name}", response_model=ApiResponse[str])
async def delete_data_agent_pool_stocks_by_agent_name(
    agent_name: str,
    user_id: str = Depends(require_admin),
):
    """根据 agent_name 删除数据代理股票分配记录。

    仅管理员可调用。
    - agent_name: 必填，数据代理名称。
    - stock_code: 可选，若指定则仅删除包含该股票代码的分配记录。
    """
    impl = MongoDataAgentPoolStocksImpl()
    deleted = impl.delete_data_agent_pool_stocks_by_agent_name(
        agent_name=agent_name
    )
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=agent_name, message="删除成功")


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_data_agent_pool_stock(
    id: str,
    user_id: str = Depends(require_admin),
):
    """删除数据代理股票分配记录"""
    impl = MongoDataAgentPoolStocksImpl()
    deleted = impl.delete_data_agent_pool_stocks(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=id, message="删除成功")

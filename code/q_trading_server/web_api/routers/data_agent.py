"""
Author: liguoqiang
Date: 2026-08-06 10:10:00
LastEditors: liguoqiang
LastEditTime: 2026-08-06 10:10:00
Description: 数据代理管理路由 - 数据代理记录的增删改查
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app_context import AppContext
from db.mongo.mongo_data_agent_impl import MongoDataAgentImpl
from web_api.auth import require_admin
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/data_agent", tags=["数据代理管理"])


class DataAgentCreateRequest(BaseModel):
    """创建数据代理请求"""
    agent_name: str = Field(..., description="代理名称")
    description: str = Field(default="", description="代理描述")
    is_online: bool = Field(default=False, description="是否在线")


class DataAgentUpdateRequest(BaseModel):
    """更新数据代理请求"""
    agent_name: str | None = Field(default=None, description="代理名称")
    description: str | None = Field(default=None, description="代理描述")
    is_online: bool | None = Field(default=None, description="是否在线")


class DataAgentItem(BaseModel):
    """数据代理条目"""
    id: str = Field(default="", description="记录 ID")
    agent_name: str = Field(default="", description="代理名称")
    description: str = Field(default="", description="代理描述")
    is_online: bool = Field(default=False, description="是否在线")
    online_time: str = Field(default="", description="上线时间")


@router.post("/create", response_model=ApiResponse[DataAgentItem])
async def create_data_agent(
    req: DataAgentCreateRequest,
    user_id: str = Depends(require_admin),
):
    """创建数据代理服务

    agent_name 需唯一，若已存在返回 409。
    """
    impl = MongoDataAgentImpl()
    # agent_name 必须唯一
    ok, existing = impl.query_data_agents(agent_name=req.agent_name)
    if ok and existing:
        raise HTTPException(status_code=409, detail="agent_name 已存在")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "agent_name": req.agent_name,
        "description": req.description,
        "is_online": req.is_online,
        "online_time": now if req.is_online else "",
    }
    ok, record_id = impl.add_data_agent(data)
    if not ok:
        raise HTTPException(status_code=500, detail="创建数据代理失败")
    await AppContext().stock_fetch.distribute_pool_stocks_to_data_agents()
    await AppContext().stock_fetch.distribute_industry_stocks_to_data_agents()
    return ApiResponse(
        data=DataAgentItem(
            id=record_id or "",
            agent_name=req.agent_name,
            description=req.description,
            is_online=req.is_online,
            online_time=data["online_time"],
        ),
        message="创建成功",
    )


@router.put("/{agent_name}", response_model=ApiResponse[DataAgentItem])
async def update_data_agent(
    agent_name: str,
    req: DataAgentUpdateRequest,
    user_id: str = Depends(require_admin),
):
    """根据 agent_name 更新数据代理信息"""
    impl = MongoDataAgentImpl()
    data: dict[str, object] = {}
    if req.agent_name is not None:
        if req.agent_name != agent_name:
            ok, existing = impl.query_data_agents(agent_name=req.agent_name)
            if ok and existing:
                raise HTTPException(status_code=409, detail="agent_name 已存在")
        data["agent_name"] = req.agent_name
    if req.description is not None:
        data["description"] = req.description
    if req.is_online is not None:
        data["is_online"] = req.is_online
        data["online_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if req.is_online else ""
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    updated = impl.update_data_agent(data, {"agent_name": agent_name})
    if not updated:
        raise HTTPException(status_code=500, detail="更新数据代理失败")
    ok, records = impl.query_data_agents(agent_name=req.agent_name if req.agent_name else agent_name)
    if not ok or not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="数据代理不存在")
    record = records[0]
    await AppContext().stock_fetch.distribute_pool_stocks_to_data_agents()
    await AppContext().stock_fetch.distribute_industry_stocks_to_data_agents()
    return ApiResponse(
        data=DataAgentItem(
            id=str(record.get("_id", "")),
            agent_name=record.get("agent_name", ""),
            description=record.get("description", ""),
            is_online=bool(record.get("is_online", False)),
            online_time=record.get("online_time", "")
        ),
        message="更新成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_data_agent(
    id: str,
    user_id: str = Depends(require_admin),
):
    """删除数据代理"""
    impl = MongoDataAgentImpl()
    deleted = impl.delete_data_agent(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除数据代理失败")
    
    await AppContext().stock_fetch.distribute_pool_stocks_to_data_agents()
    await AppContext().stock_fetch.distribute_industry_stocks_to_data_agents()
    return ApiResponse(data=id, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[DataAgentItem]])
async def list_data_agents(
    agent_name: str = Query(default="", description="代理名称（可选）"),
    is_online: int | None = Query(default=None, description="是否在线: 1=在线, 0=离线（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_id: str = Depends(require_admin),
):
    """查询数据代理列表"""
    impl = MongoDataAgentImpl()
    res, records = impl.query_data_agents(
        agent_name=agent_name,
        is_online=is_online,
        skip=skip,
        limit=limit,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询数据代理失败")
    result = [
        DataAgentItem(
            id=str(r.get("_id", "")),
            agent_name=r.get("agent_name", ""),
            description=r.get("description", ""),
            is_online=bool(r.get("is_online", False)),
            online_time=r.get("online_time", "")
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)

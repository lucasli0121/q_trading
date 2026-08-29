"""
Author: liguoqiang
Date: 2026-08-11 12:00:00
LastEditors: liguoqiang
LastEditTime: 2026-08-11 12:00:00
Description: 工作流微服务管理路由
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app_context import AppContext
from db.mongo.mongo_workflow_service_impl import MongoWorkFlowServiceImpl
from db.mongo.mongo_workflow_service_user_strategy_impl import MongoWorkFlowServiceUserStrategyImpl
from web_api.auth import require_admin
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/workflow_service", tags=["工作流服务管理"])


class WorkflowServiceCreateRequest(BaseModel):
    """创建工作流微服务请求"""

    service_name: str = Field(..., description="服务名称")
    description: str = Field(default="", description="服务描述")
    is_online: bool = Field(default=False, description="是否在线")


class WorkflowServiceUpdateRequest(BaseModel):
    """更新工作流微服务请求"""

    service_name: str | None = Field(default=None, description="服务名称")
    description: str | None = Field(default=None, description="服务描述")
    is_online: bool | None = Field(default=None, description="是否在线")


class WorkflowServiceItem(BaseModel):
    """工作流微服务响应对象"""

    id: str = Field(default="", description="记录 ID")
    service_name: str = Field(default="", description="服务名称")
    description: str = Field(default="", description="服务描述")
    is_online: bool = Field(default=False, description="是否在线")
    online_time: str = Field(default="", description="上线时间")


@router.post("/create", response_model=ApiResponse[WorkflowServiceItem])
async def create_workflow_service(
    req: WorkflowServiceCreateRequest,
    user_id: str = Depends(require_admin),
):
    """创建工作流微服务记录"""
    impl = MongoWorkFlowServiceImpl()
    ok, existing = impl.query_workflow_services(service_name=req.service_name, skip=0, limit=0)
    if ok and existing:
        raise HTTPException(status_code=409, detail="service_name 已存在")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "service_name": req.service_name,
        "description": req.description,
        "is_online": req.is_online,
        "online_time": now if req.is_online else "",
    }
    ok, record_id = impl.add_workflow_service(data)
    if not ok or not record_id:
        raise HTTPException(status_code=500, detail="创建失败")
    await AppContext().stock_fetch.distribute_user_strategy_to_workflow()
    return ApiResponse(
        data=WorkflowServiceItem(
            id=record_id,
            service_name=req.service_name,
            description=req.description,
            is_online=req.is_online,
            online_time=data["online_time"],
        ),
        message="创建成功",
    )


@router.get("/list", response_model=ApiResponse[list[WorkflowServiceItem]])
async def list_workflow_services(
    service_name: str = Query(default="", description="服务名称（可选）"),
    is_online: int | None = Query(default=None, description="是否在线: 1=在线, 0=离线（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_id: str = Depends(require_admin),
):
    """查询工作流微服务列表"""
    impl = MongoWorkFlowServiceImpl()
    res, records = impl.query_workflow_services(
        service_name=service_name,
        is_online=is_online,
        skip=skip,
        limit=limit,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        WorkflowServiceItem(
            id=str(r.get("_id", "")),
            service_name=r.get("service_name", ""),
            description=r.get("description", ""),
            is_online=bool(r.get("is_online", False)),
            online_time=r.get("online_time", ""),
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/{id}", response_model=ApiResponse[WorkflowServiceItem])
async def get_workflow_service(
    id: str,
    user_id: str = Depends(require_admin),
):
    """根据 ID 查询工作流微服务记录"""
    impl = MongoWorkFlowServiceImpl()
    res, records = impl.query_workflow_service_by_id(id)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    return ApiResponse(
        data=WorkflowServiceItem(
            id=str(record.get("_id", "")),
            service_name=record.get("service_name", ""),
            description=record.get("description", ""),
            is_online=bool(record.get("is_online", False)),
            online_time=record.get("online_time", ""),
        )
    )


@router.put("/{id}", response_model=ApiResponse[WorkflowServiceItem])
async def update_workflow_service(
    id: str,
    req: WorkflowServiceUpdateRequest,
    user_id: str = Depends(require_admin),
):
    """更新工作流微服务记录"""
    data: dict[str, object] = {}
    if req.service_name is not None:
        data["service_name"] = req.service_name
    if req.description is not None:
        data["description"] = req.description
    if req.is_online is not None:
        data["is_online"] = req.is_online
        data["online_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if req.is_online else ""
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    impl = MongoWorkFlowServiceImpl()
    if req.service_name is not None:
        ok, existing = impl.query_workflow_services(service_name=req.service_name, skip=0, limit=0)
        if ok and existing:
            for record in existing:
                if str(record.get("_id", "")) != id:
                    raise HTTPException(status_code=409, detail="service_name 已存在")
    updated = impl.update_workflow_service(data, {"_id": id})
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")
    res, records = impl.query_workflow_service_by_id(id)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    await AppContext().stock_fetch.distribute_user_strategy_to_workflow()
    return ApiResponse(
        data=WorkflowServiceItem(
            id=str(record.get("_id", "")),
            service_name=record.get("service_name", ""),
            description=record.get("description", ""),
            is_online=bool(record.get("is_online", False)),
            online_time=record.get("online_time", ""),
        ),
        message="更新成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_workflow_service(
    id: str,
    user_id: str = Depends(require_admin),
):
    """删除工作流微服务记录"""
    impl = MongoWorkFlowServiceImpl()
    service_name = ""
    res, values = impl.query_workflow_service_by_id(id)
    if res and values and len(values) > 0:
        values_list = list(values)
        service_name = values_list[0].get("service_name", "")
    deleted = impl.delete_workflow_service(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    if service_name != "":
        service_user_strategy_impl = MongoWorkFlowServiceUserStrategyImpl()
        service_user_strategy_impl.delete_by_service_name(service_name)
    return ApiResponse(data=id, message="删除成功")

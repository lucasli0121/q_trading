"""
Author: liguoqiang
Date: 2026-08-11 12:30:00
LastEditors: liguoqiang
LastEditTime: 2026-08-11 12:30:00
Description: 工作流微服务用户策略分配管理路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db.mongo.mongo_workflow_service_user_strategy_impl import MongoWorkFlowServiceUserStrategyImpl
from web_api.auth import require_admin
from web_api.models import ApiResponse

router = APIRouter(prefix="/api/workflow_service_user_strategy", tags=["工作流服务用户策略分配"])


class WorkflowServiceUserStrategyCreateRequest(BaseModel):
    """创建工作流服务用户策略分配请求"""

    service_name: str = Field(..., description="服务名称")
    user_strategy_ids: list[str] = Field(..., description="用户策略 ID 列表")


class WorkflowServiceUserStrategyUpdateRequest(BaseModel):
    """更新工作流服务用户策略分配请求"""

    service_name: str | None = Field(default=None, description="服务名称")
    user_strategy_ids: list[str] | None = Field(default=None, description="用户策略 ID 列表")


class WorkflowServiceUserStrategyItem(BaseModel):
    """工作流服务用户策略分配响应对象"""

    id: str = Field(default="", description="记录 ID")
    service_name: str = Field(default="", description="服务名称")
    user_strategy_ids: list[str] = Field(default_factory=list, description="用户策略 ID 列表")


@router.post("/create", response_model=ApiResponse[WorkflowServiceUserStrategyItem])
async def create_workflow_service_user_strategy(
    req: WorkflowServiceUserStrategyCreateRequest,
    user_id: str = Depends(require_admin),
):
    """创建工作流服务用户策略分配记录"""
    impl = MongoWorkFlowServiceUserStrategyImpl()
    ok, record_id = impl.add(req.dict())
    if not ok or not record_id:
        raise HTTPException(status_code=500, detail="创建失败")
    return ApiResponse(
        data=WorkflowServiceUserStrategyItem(
            id=record_id,
            service_name=req.service_name,
            user_strategy_ids=req.user_strategy_ids,
        ),
        message="创建成功",
    )


@router.get("/list", response_model=ApiResponse[list[WorkflowServiceUserStrategyItem]])
async def list_workflow_service_user_strategies(
    service_name: str = Query(default="", description="服务名称（可选）"),
    user_id: str = Depends(require_admin),
):
    """查询工作流服务用户策略分配记录列表"""
    impl = MongoWorkFlowServiceUserStrategyImpl()
    res, records = impl.query_by_service_name(service_name)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = [
        WorkflowServiceUserStrategyItem(
            id=str(r.get("_id", "")),
            service_name=r.get("service_name", ""),
            user_strategy_ids=r.get("user_strategy_ids", []) or [],
        )
        for r in (records or [])
    ]
    return ApiResponse(data=result)


@router.get("/service/{service_name}", response_model=ApiResponse[list[WorkflowServiceUserStrategyItem]])
async def list_workflow_service_user_strategies_by_service(
    service_name: str,
    user_id: str = Depends(require_admin),
):
    """根据 service_name 查询，并按 user_strategy_ids 数量从小到大排序"""
    impl = MongoWorkFlowServiceUserStrategyImpl()
    res, records = impl.query_by_service_name(service_name)
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    sorted_records = sorted(
        records or [],
        key=lambda item: len(item.get("user_strategy_ids", []) or []),
    )
    result = [
        WorkflowServiceUserStrategyItem(
            id=str(r.get("_id", "")),
            service_name=r.get("service_name", ""),
            user_strategy_ids=r.get("user_strategy_ids", []) or [],
        )
        for r in sorted_records
    ]
    return ApiResponse(data=result)


@router.get("/{id}", response_model=ApiResponse[WorkflowServiceUserStrategyItem])
async def get_workflow_service_user_strategy(
    id: str,
    user_id: str = Depends(require_admin),
):
    """根据 ID 查询工作流服务用户策略分配记录"""
    impl = MongoWorkFlowServiceUserStrategyImpl()
    res, records = impl.query_by_id(id)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    return ApiResponse(
        data=WorkflowServiceUserStrategyItem(
            id=str(record.get("_id", "")),
            service_name=record.get("service_name", ""),
            user_strategy_ids=record.get("user_strategy_ids", []) or [],
        )
    )


@router.put("/{id}", response_model=ApiResponse[WorkflowServiceUserStrategyItem])
async def update_workflow_service_user_strategy(
    id: str,
    req: WorkflowServiceUserStrategyUpdateRequest,
    user_id: str = Depends(require_admin),
):
    """更新工作流服务用户策略分配记录"""
    data: dict[str, object] = {}
    if req.service_name is not None:
        data["service_name"] = req.service_name
    if req.user_strategy_ids is not None:
        data["user_strategy_ids"] = req.user_strategy_ids
    if not data:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    impl = MongoWorkFlowServiceUserStrategyImpl()
    updated = impl.update(id, data)
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")
    res, records = impl.query_by_id(id)
    if not res or not records:
        raise HTTPException(status_code=404, detail="记录不存在")
    record = records[0]
    return ApiResponse(
        data=WorkflowServiceUserStrategyItem(
            id=str(record.get("_id", "")),
            service_name=record.get("service_name", ""),
            user_strategy_ids=record.get("user_strategy_ids", []) or [],
        ),
        message="更新成功",
    )


@router.delete("/{id}", response_model=ApiResponse[str])
async def delete_workflow_service_user_strategy(
    id: str,
    user_id: str = Depends(require_admin),
):
    """删除工作流服务用户策略分配记录"""
    impl = MongoWorkFlowServiceUserStrategyImpl()
    deleted = impl.delete(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=id, message="删除成功")

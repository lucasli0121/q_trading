"""
Author: liguoqiang
Date: 2026-08-13
Description: 因子管理路由 — 管理员创建/删除因子定义，所有用户可浏览
"""

from fastapi import APIRouter, Depends, HTTPException

from db.mongo.mongo_factor_impl import MongoFactorImpl
from web_api.auth import get_current_user, require_admin
from web_api.models import ApiResponse, FactorCreateRequest

router = APIRouter(prefix="/api/factor", tags=["因子管理"])


@router.post("/create", response_model=ApiResponse[dict])
async def create_factor(
    req: FactorCreateRequest, user_id: str = Depends(require_admin)
):
    """创建因子（仅管理员，同名因子返回错误）"""
    impl = MongoFactorImpl()
    ok, _, error = impl.insert_or_update_factor({
        "name": req.name,
        "description": req.description,
        "class_path": req.class_path,
        "class_name": req.class_name,
        "default_params": req.default_params,
    }, insert_only=True)
    if error == "duplicate_name":
        raise HTTPException(status_code=409, detail=f"因子名称 \"{req.name}\" 已存在")
    if not ok:
        raise HTTPException(status_code=500, detail="创建因子失败")
    return ApiResponse(data={"name": req.name}, message="创建成功")


@router.delete("/{name}", response_model=ApiResponse[str])
async def delete_factor(name: str, user_id: str = Depends(require_admin)):
    """删除因子（仅管理员）"""
    impl = MongoFactorImpl()
    # 验证因子存在
    res, factors = impl.query_factor_by_name(name)
    if not res or not factors:
        raise HTTPException(status_code=404, detail="因子不存在")
    deleted = impl.delete_factor(name)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除失败")
    return ApiResponse(data=name, message="删除成功")


@router.get("/list", response_model=ApiResponse[list])
async def list_factors():
    """查询所有因子（公开接口，无需登录）"""
    impl = MongoFactorImpl()
    res, factors = impl.query_all_factors()
    if not res:
        raise HTTPException(status_code=500, detail="查询失败")
    result = []
    for f in (factors or []):
        f["_id"] = str(f.get("_id", ""))
        result.append(f)
    return ApiResponse(data=result)


@router.get("/id/{id}", response_model=ApiResponse[dict])
async def get_factor_by_id(id: str, user_id: str = Depends(get_current_user)):
    """按记录 ID 查询单个因子详情"""
    impl = MongoFactorImpl()
    res, factors = impl.query_factor_by_id(id)
    if not res or not factors or len(factors) == 0:
        raise HTTPException(status_code=404, detail="因子不存在")
    f = factors[0]
    f["_id"] = str(f.get("_id", ""))
    return ApiResponse(data=f)


@router.get("/{name}", response_model=ApiResponse[dict])
async def get_factor(name: str, user_id: str = Depends(get_current_user)):
    """查询单个因子详情"""
    impl = MongoFactorImpl()
    res, factors = impl.query_factor_by_name(name)
    if not res or not factors or len(factors) == 0:
        raise HTTPException(status_code=404, detail="因子不存在")
    f = factors[0]
    f["_id"] = str(f.get("_id", ""))
    return ApiResponse(data=f)

"""
Author: liguoqiang
Date: 2026-08-01 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-08-01 00:00:00
Description: 交易信号管理路由 — 交易信号记录的增删查
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from db.mongo.mongo_trade_signal_impl import MongoTradeSignalImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from web_api.auth import get_current_user_with_role
from web_api.models import ApiResponse, TradeSignalCreateRequest, TradeSignalItem

router = APIRouter(prefix="/api/trade_signal", tags=["交易信号管理"])

# 星级上限，见 dao/trade_signal_dao.py 中 star 字段的口径（最大 5 星，保留一位小数）
MAX_STAR: float = 5.0


def _validate_star_value(star: float) -> float:
    """校验并归一化星级，返回保留一位小数后的值

    :raises HTTPException: 400 星级超出 0~5
    """
    if star < 0 or star > MAX_STAR:
        raise HTTPException(status_code=400, detail=f"star 应在 0~{MAX_STAR} 之间")
    return round(star, 1)


def _validate_star_range(min_star: float, max_star: float) -> None:
    """校验星级区间参数

    0 表示不限，因此只在非 0 时校验上下界与大小关系。

    :raises HTTPException: 400 参数越界或下界大于上界
    """
    for name, value in (("min_star", min_star), ("max_star", max_star)):
        if value and (value < 0 or value > MAX_STAR):
            raise HTTPException(
                status_code=400, detail=f"{name} 应在 0~{MAX_STAR} 之间，0 表示不限"
            )
    if min_star and max_star and min_star > max_star:
        raise HTTPException(status_code=400, detail="min_star 不能大于 max_star")


def _to_trade_signal_item(record: dict) -> TradeSignalItem:
    """把数据库记录转换成响应模型

    原先 add / list / latest 三处各自手写一遍字段映射，容易漏字段
    （score、star 就是这么漏掉的），统一收在这里。
    """
    return TradeSignalItem(
        id=str(record.get("_id", "")),
        strategy_id=record.get("strategy_id", ""),
        user_strategy_id=record.get("user_strategy_id", ""),
        stock_code=record.get("stock_code", ""),
        trade_price=float(record.get("trade_price", 0.0) or 0.0),
        profit_rate=float(record.get("profit_rate", 0.0) or 0.0),
        profit_amount=float(record.get("profit_amount", 0.0) or 0.0),
        action=record.get("action", ""),
        reason=record.get("reason", ""),
        score=float(record.get("score", 0.0) or 0.0),
        star=float(record.get("star", 0.0) or 0.0),
        create_time=record.get("create_time", ""),
    )


def _load_owned_strategy_ids(user_id: str, role: int) -> tuple[set[str], set[str]] | None:
    """一次性取出该用户拥有的 (用户策略 _id 集合, 全局策略 id 集合)

    管理员（role == 0）返回 None，表示不限制范围。
    只查一次库，供下面两个校验函数复用，避免每次校验都往返数据库。

    :param user_id: 用户 ID
    :param role: 用户角色，0 为管理员
    :return: (user_strategy_id 集合, strategy_id 集合)，管理员返回 None
    """
    if role == 0:
        return None
    impl = MongoUserStrategyImpl()
    res, records = impl.query_user_strategies_by_user(user_id)
    if not res or not records:
        return set(), set()
    us_ids = {str(r.get("_id", r.get("id", ""))) for r in records}
    s_ids = {r.get("strategy_id", "") for r in records}
    return us_ids, s_ids


def _resolve_user_strategy_ids_from_user(user_id: str, role: int) -> list[str] | None:
    """通过用户 ID 查询该用户关联的所有用户策略 _id

    管理员（role == 0）返回 None 表示不限制策略范围（查询所有）；
    普通用户返回该用户策略记录的 _id 列表。

    :param user_id: 用户 ID
    :param role: 用户角色，0 为管理员
    :return: user_strategy_id 列表，管理员返回 None
    """
    owned = _load_owned_strategy_ids(user_id, role)
    if owned is None:
        return None
    return sorted(owned[0])


def _assert_strategy_access(
    user_id: str, role: int, strategy_id: str = "", user_strategy_id: str = ""
) -> None:
    """校验指定策略是否属于当前用户，管理员跳过

    归属校验必须在「按 ID 直接查询」的分支里显式执行，
    否则任何登录用户只要拿到别人的 id 就能读取/删除其数据。

    :raises HTTPException: 403 不属于当前用户
    """
    owned = _load_owned_strategy_ids(user_id, role)
    if owned is None:
        return
    us_ids, s_ids = owned
    if user_strategy_id and user_strategy_id not in us_ids:
        raise HTTPException(status_code=403, detail="无权操作此用户策略")
    if strategy_id and strategy_id not in s_ids:
        raise HTTPException(status_code=403, detail="无权操作此策略")


def _query_signals_scoped(
    user_id: str,
    role: int,
    stock_codes: list,
    strategy_id: str,
    user_strategy_id: str,
    action: str,
    start_time: str,
    end_time: str,
    skip: int,
    limit: int,
    min_star: float = 0.0,
    max_star: float = 0.0,
) -> tuple[bool, Any]:
    """按当前用户的可见范围查询交易信号

    可见范围规则（/list 与 /by_star 共用）：
    - 指定了 strategy_id 或 user_strategy_id：先校验归属，再按该策略查
    - 未指定且是管理员：查全部
    - 未指定且是普通用户：查自己名下所有用户策略的信号；无策略直接返回空

    :return: (ok, records)；用户无策略时返回 (True, [])
    """
    impl = MongoTradeSignalImpl()
    if strategy_id or user_strategy_id:
        # 指定了策略 ID：先校验归属，否则可越权读取他人信号
        _assert_strategy_access(
            user_id, role, strategy_id=strategy_id, user_strategy_id=user_strategy_id
        )
        return impl.query_signals(
            stock_codes=stock_codes,
            strategy_id=strategy_id,
            user_strategy_id=user_strategy_id,
            action=action,
            start_time=start_time,
            end_time=end_time,
            min_star=min_star,
            max_star=max_star,
            skip=skip,
            limit=limit,
        )
    user_strategy_ids = _resolve_user_strategy_ids_from_user(user_id, role)
    if user_strategy_ids is None:
        # 管理员：查询所有记录
        return impl.query_signals(
            stock_codes=stock_codes,
            action=action,
            start_time=start_time,
            end_time=end_time,
            min_star=min_star,
            max_star=max_star,
            skip=skip,
            limit=limit,
        )
    if not user_strategy_ids:
        return True, []
    return impl.query_signals_by_user_strategy_ids(
        stock_codes=stock_codes,
        user_strategy_ids=user_strategy_ids,
        action=action,
        start_time=start_time,
        end_time=end_time,
        min_star=min_star,
        max_star=max_star,
        skip=skip,
        limit=limit,
    )


@router.post("/add", response_model=ApiResponse[TradeSignalItem])
def add_trade_signal(
    req: TradeSignalCreateRequest,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """添加交易信号记录"""
    user_id, role = user_info
    _assert_strategy_access(user_id, role, user_strategy_id=req.user_strategy_id)
    star = _validate_star_value(req.star)
    impl = MongoTradeSignalImpl()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "strategy_id": req.strategy_id,
        "user_strategy_id": req.user_strategy_id,
        "stock_code": req.stock_code,
        "trade_price": req.trade_price,
        "profit_rate": req.profit_rate,
        "profit_amount": req.profit_amount,
        "action": req.action,
        "reason": req.reason,
        "score": req.score,
        "star": star,
        "create_time": now,
    }
    ok, record_id = impl.add_signal(record)
    if not ok:
        raise HTTPException(status_code=500, detail="添加交易信号失败")
    record["_id"] = record_id or ""
    return ApiResponse(data=_to_trade_signal_item(record), message="添加成功")


@router.delete("/{id}", response_model=ApiResponse[str])
def delete_trade_signal(
    id: str,
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """删除交易信号记录"""
    user_id, role = user_info
    impl = MongoTradeSignalImpl()
    # 先取记录做归属校验：原实现取了 user_info 却完全没用，任意登录用户可删他人记录
    res, records = impl.query_signal_by_id(id)
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    if not records:
        raise HTTPException(status_code=404, detail="交易信号不存在")
    _assert_strategy_access(
        user_id, role, user_strategy_id=records[0].get("user_strategy_id", "")
    )
    deleted = impl.delete_signal(id)
    if not deleted:
        raise HTTPException(status_code=500, detail="删除交易信号失败")
    return ApiResponse(data=id, message="删除成功")


@router.get("/list", response_model=ApiResponse[list[TradeSignalItem]])
def list_trade_signals(
    stock_codes: list = Query(default=[], description="股票代码，默认为空，可多选"),
    strategy_id: str = Query(default="", description="策略 ID（可选）"),
    user_strategy_id: str = Query(default="", description="用户策略 ID（可选）"),
    start_time: str = Query(default="", description="创建开始时间 YYYY-MM-DD HH:MM:SS（可选）"),
    end_time: str = Query(default="", description="创建结束时间 YYYY-MM-DD HH:MM:SS（可选）"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询交易信号记录 — 管理员查所有，普通用户查自己策略的信号记录"""
    user_id, role = user_info
    res, records = _query_signals_scoped(
        user_id=user_id,
        role=role,
        stock_codes=stock_codes,
        strategy_id=strategy_id,
        user_strategy_id=user_strategy_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        skip=skip,
        limit=limit,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    return ApiResponse(data=[_to_trade_signal_item(r) for r in (records or [])])


@router.get("/by_star", response_model=ApiResponse[list[TradeSignalItem]])
def list_trade_signals_by_star(
    min_star: float = Query(
        default=0.0, description="最低星级（含），0 表示不限；查「4 星及以上」传 4"
    ),
    max_star: float = Query(
        default=0.0, description="最高星级（含），0 表示不限；精确查 4.5 星传 min_star=4.5&max_star=4.5"
    ),
    stock_codes: list = Query(default=[], description="股票代码，默认为空，可多选"),
    strategy_id: str = Query(default="", description="策略 ID（可选）"),
    user_strategy_id: str = Query(default="", description="用户策略 ID（可选）"),
    start_time: str = Query(default="", description="创建开始时间 YYYY-MM-DD HH:MM:SS（可选）"),
    end_time: str = Query(default="", description="创建结束时间 YYYY-MM-DD HH:MM:SS（可选）"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    skip: int = Query(default=0, description="分页跳过条数"),
    limit: int = Query(default=0, description="分页限制条数，0 表示不限制"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """按星级区间查询交易信号 — 管理员查所有，普通用户查自己策略的信号记录

    结果按 create_time 倒序，与其他查询接口一致。
    可见范围规则与 /list 完全相同，因此策略归属校验也一致。
    """
    user_id, role = user_info
    _validate_star_range(min_star, max_star)
    res, records = _query_signals_scoped(
        user_id=user_id,
        role=role,
        stock_codes=stock_codes,
        strategy_id=strategy_id,
        user_strategy_id=user_strategy_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        skip=skip,
        limit=limit,
        min_star=min_star,
        max_star=max_star,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    return ApiResponse(data=[_to_trade_signal_item(r) for r in (records or [])])


@router.get("/latest", response_model=ApiResponse[TradeSignalItem])
def get_latest_signal(
    strategy_id: str = Query(..., description="策略 ID"),
    user_strategy_id: str = Query(..., description="用户策略 ID"),
    stock_code: str = Query(..., description="股票代码"),
    action: str = Query(default="", description="买卖方向: 买入/卖出（可选）"),
    user_info: tuple[str, int] = Depends(get_current_user_with_role),
):
    """查询最近一次交易信号 — 按策略 ID、股票代码、买卖方向查询最新一条"""
    user_id, role = user_info
    _assert_strategy_access(user_id, role, user_strategy_id=user_strategy_id)
    impl = MongoTradeSignalImpl()
    res, records = impl.query_latest_signal(
        strategy_id=strategy_id,
        user_strategy_id=user_strategy_id,
        stock_code=stock_code,
        action=action,
    )
    if not res:
        raise HTTPException(status_code=500, detail="查询交易信号失败")
    if not records or len(records) == 0:
        raise HTTPException(status_code=404, detail="未找到交易信号")
    return ApiResponse(data=_to_trade_signal_item(records[0]))

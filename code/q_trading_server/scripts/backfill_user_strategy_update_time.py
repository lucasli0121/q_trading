"""
Author: liguoqiang
Date: 2026-09-17 00:00:00
LastEditors: liguoqiang
LastEditTime: 2026-09-17 00:00:00
Description: 一次性数据修复脚本 — 为存量 user_strategy_tbl 记录回填 update_time 字段

背景
----
UserStrategyDao 新增 update_time 字段，语义是「status 最近一次变更的时间」。
字段上线后由 MongoUserStrategyImpl 保证写入（新增时取 create_time，PATCH 改 status 时刷新为当前时间），
但字段上线之前创建的存量记录没有该字段，接口读出来是空串，前端按它排序/展示会出现空值。

回填口径
--------
记录创建时即首次写入 status，因此 create_time 是 update_time 的合理近似值：

    update_time := create_time

若个别记录连 create_time 都没有，可用 --fill-from-objectid 以 _id 内嵌时间戳
（MongoDB ObjectId 前 4 字节为插入时刻，精度到秒）作为兜底值。

使用方式（在项目根目录执行，或在任意目录执行也可——脚本会自动切到根目录）
--------------------------------------------------------------------
    # 1) 预演：只统计与预览，绝不修改数据（默认行为）
    python scripts/backfill_user_strategy_update_time.py

    # 2) 实际写入
    python scripts/backfill_user_strategy_update_time.py --apply

    # 3) create_time 也为空的记录，用 _id 时间戳兜底回填
    python scripts/backfill_user_strategy_update_time.py --apply --fill-from-objectid

    # 4) 先小批量试跑，确认结果再全量
    python scripts/backfill_user_strategy_update_time.py --apply --limit 5

幂等性
------
过滤条件只命中「update_time 缺失/为 null/为空串」的文档，重复执行不会覆盖已回填或
已被业务代码刷新的 update_time，可安全重跑。
"""

# coding="utf8"

import argparse
import logging
import os
import sys
from datetime import timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

TABLE_NAME = "user_strategy_tbl"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 记录时间统一使用东八区本地时间字符串，与项目其他 create_time/update_time 保持一致
CST = timezone(timedelta(hours=8))
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# update_time 缺失 / 为 null / 为空串
MISSING_UPDATE_TIME: dict[str, Any] = {
    "$or": [
        {"update_time": {"$exists": False}},
        {"update_time": None},
        {"update_time": ""},
    ]
}
# create_time 存在且非空（$nin 单独使用会把「字段不存在」也算作匹配，必须叠加 $exists）
HAS_CREATE_TIME: dict[str, Any] = {"create_time": {"$exists": True, "$nin": [None, ""]}}
# 可回填：缺 update_time 且有 create_time
FILLABLE: dict[str, Any] = {"$and": [MISSING_UPDATE_TIME, HAS_CREATE_TIME]}
# 无法回填：缺 update_time 且没有 create_time
BLANK_CREATE_TIME: dict[str, Any] = {
    "$and": [
        MISSING_UPDATE_TIME,
        {"$or": [{"create_time": {"$exists": False}}, {"create_time": {"$in": [None, ""]}}]},
    ]
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数

    :param argv: 参数列表，None 表示使用 sys.argv
    :return: 解析结果
    """
    parser = argparse.ArgumentParser(
        description="为存量 user_strategy_tbl 记录回填 update_time（默认仅预演，不写库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行写入；不加该参数时仅预演统计与预览",
    )
    parser.add_argument(
        "--fill-from-objectid",
        action="store_true",
        help="对 create_time 也为空的记录，用 _id 内嵌时间戳（东八区）回填 update_time",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理多少条，0 表示不限制（用于小批量试跑）",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=3,
        help="预演时预览的样例条数，默认 3",
    )
    return parser.parse_args(argv)


def _oid_time_to_str(oid: Any) -> str:
    """取 ObjectId 内嵌时间戳并转为东八区时间字符串

    :param oid: MongoDB ObjectId
    :return: 格式化时间字符串；非 ObjectId 或无法解析时返回空串
    """
    generation_time = getattr(oid, "generation_time", None)
    if generation_time is None:
        return ""
    return generation_time.astimezone(CST).strftime(TIME_FORMAT)


def _preview(table: Any, condition: dict[str, Any], sample: int) -> None:
    """打印待处理记录的样例

    :param table: MongoDB 集合
    :param condition: 查询条件
    :param sample: 预览条数
    """
    if sample <= 0:
        return
    logger.info("样例（最多 %d 条）：", sample)
    cursor = table.find(
        condition,
        {"_id": 1, "name": 1, "user_id": 1, "status": 1, "create_time": 1},
    ).limit(sample)
    for doc in cursor:
        logger.info(
            "  _id=%s name=%r status=%r user_id=%r create_time=%r",
            doc.get("_id"),
            doc.get("name", ""),
            doc.get("status", ""),
            doc.get("user_id", ""),
            doc.get("create_time", ""),
        )


def _backfill_from_create_time(table: Any, limit: int) -> int:
    """把缺失的 update_time 回填为 create_time

    优先使用聚合管道一次更新；若服务端不支持（MongoDB < 4.2）则自动回退为逐条更新。

    :param table: MongoDB 集合
    :param limit: 最多处理条数，0 表示不限制
    :return: 实际修改的文档数
    """
    condition: dict[str, Any] = dict(FILLABLE)
    if limit > 0:
        ids = [doc["_id"] for doc in table.find(FILLABLE, {"_id": 1}).limit(limit)]
        if not ids:
            return 0
        condition = {"_id": {"$in": ids}}

    try:
        result = table.update_many(condition, [{"$set": {"update_time": "$create_time"}}])
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception as e:  # 旧版服务端不支持聚合管道更新
        logger.warning("聚合管道更新失败（%s），回退为逐条更新", e)

    modified = 0
    for doc in table.find(condition, {"_id": 1, "create_time": 1}):
        ret = table.update_one(
            {"_id": doc["_id"]},
            {"$set": {"update_time": str(doc.get("create_time", ""))}},
        )
        modified += int(getattr(ret, "modified_count", 0) or 0)
    return modified


def _backfill_from_object_id(table: Any, limit: int) -> tuple[int, int]:
    """对 create_time 缺失的记录，用 _id 内嵌时间戳回填 update_time

    :param table: MongoDB 集合
    :param limit: 最多处理条数，0 表示不限制
    :return: (修改条数, 跳过条数) —— _id 非 ObjectId 时跳过
    """
    cursor = table.find(BLANK_CREATE_TIME, {"_id": 1})
    if limit > 0:
        cursor = cursor.limit(limit)

    modified = 0
    skipped = 0
    for doc in cursor:
        time_str = _oid_time_to_str(doc["_id"])
        if not time_str:
            logger.warning("_id 非 ObjectId，无法解析插入时间，跳过：_id=%s", doc["_id"])
            skipped += 1
            continue
        table.update_one({"_id": doc["_id"]}, {"$set": {"update_time": time_str}})
        modified += 1
    return modified, skipped


def main(argv: list[str] | None = None) -> int:
    """脚本入口

    :param argv: 命令行参数，None 表示使用 sys.argv
    :return: 进程退出码，0 表示成功
    """
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s-%(levelname)s-%(message)s",
    )

    # 配置与数据层均依赖工作目录下的 cfg/stock.cfg，统一切到项目根目录。
    # 注意：以「python scripts/xxx.py」方式运行时，sys.path[0] 是 scripts/ 而非项目根目录，
    # chdir 只改工作目录、不会把根目录加入模块搜索路径，因此必须显式追加，否则 import db 会失败。
    os.chdir(ROOT_DIR)
    if ROOT_DIR not in sys.path:
        sys.path.append(ROOT_DIR)

    from db.mongo.mongo_exec import MongoExec

    # 必须持有 MongoExec 实例的引用：MongoExec.__del__ 会关闭底层连接，
    # 若写成 MongoExec().db[...] 这种临时对象，实例会在该语句结束后立即被回收并关闭连接，
    # 后续操作会抛 pymongo.errors.InvalidOperation: Cannot use MongoClient after close。
    mongo_exec = MongoExec()
    table = mongo_exec.db[TABLE_NAME]

    total = table.count_documents({})
    missing = table.count_documents(MISSING_UPDATE_TIME)
    fillable = table.count_documents(FILLABLE)
    blank_create = table.count_documents(BLANK_CREATE_TIME)

    logger.info("集合 %s 共 %d 条记录", TABLE_NAME, total)
    logger.info("  update_time 已存在      : %d 条", total - missing)
    logger.info("  update_time 缺失        : %d 条", missing)
    logger.info("    ├─ 可用 create_time 回填: %d 条", fillable)
    logger.info("    └─ create_time 也为空  : %d 条", blank_create)

    if missing == 0:
        logger.info("无需处理，全部记录已有 update_time。")
        return 0

    if not args.apply:
        _preview(table, FILLABLE, args.sample)
        if blank_create > 0:
            logger.info(
                "另有 %d 条记录没有 create_time；如需回填请加 --fill-from-objectid",
                blank_create,
            )
        logger.info("当前为预演模式，未修改任何数据。加 --apply 实际执行。")
        return 0

    modified = 0
    if fillable > 0:
        modified = _backfill_from_create_time(table, args.limit)
        logger.info("已用 create_time 回填 update_time：%d 条", modified)
    else:
        logger.info("没有可用 create_time 回填的记录，跳过该步骤。")

    if args.fill_from_objectid:
        oid_modified, skipped = _backfill_from_object_id(table, args.limit)
        logger.info("已用 _id 时间戳回填 update_time：%d 条（跳过 %d 条）", oid_modified, skipped)
    elif blank_create > 0:
        logger.info(
            "仍有 %d 条记录 update_time 为空（无 create_time）；如需处理请加 --fill-from-objectid",
            blank_create,
        )

    remaining = table.count_documents(MISSING_UPDATE_TIME)
    logger.info("完成后 update_time 仍缺失：%d 条", remaining)
    if modified == 0 and fillable > 0:
        logger.warning("预期回填 %d 条但实际修改 0 条，请检查集合权限或数据状态。", fillable)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

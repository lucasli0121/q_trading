# -*- coding: utf-8 -*-
"""核查服务端各类 K 线的保留范围与可回源性（只读；接口调用可能触发服务端回源）。

回答的问题：日K / 周K / 月K 是否也需要本地留存？

跑法（容器内）：python _probe_hq_range.py

环境注意：
  - 此 mongo 实例限制了 aggregate / distinct（报 `Unrecognized pipeline stage name` 或静默返回空），
    因此统计一律用 find + count_documents，不要用聚合管道。
  - 接口回源后写不写库不确定（实测调用前后 day_hq_tbl 总数不变），所以「接口能返回」
    不等于「库里存下来了」——这正是日K不可复现的原因之一。
"""
import os
import sys
from configparser import ConfigParser
from urllib.parse import quote_plus

import requests
from pymongo import MongoClient

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_CFG = os.path.join(HERE, "..", "q_trading_server", "cfg", "stock.cfg")
LOCAL_CFG = os.path.join(HERE, "cfg", "stock.cfg")

TABLES = [
    ("day_hq_tbl", "日K"),
    ("week_hq_tbl", "周K"),
    ("month_hq_tbl", "月K"),
    ("minute_hq_tbl", "分钟"),
]


def load_mongo_uri() -> str:
    """优先读服务端配置的 [mongo] 节，读不到则回退到本地已知连接。"""
    for path in (SERVER_CFG, LOCAL_CFG):
        if not os.path.exists(path):
            continue
        cp = ConfigParser()
        cp.read(path, encoding="utf-8")
        if cp.has_section("mongo"):
            host = cp.get("mongo", "host", fallback="")
            port = int(cp.get("mongo", "port", fallback="27017"))
            user = cp.get("mongo", "username", fallback="")
            pwd = cp.get("mongo", "password", fallback="")
            db = cp.get("mongo", "database", fallback="q_share")
            if host and user:
                return "mongodb://%s:%s@%s:%d/%s" % (
                    user, quote_plus(pwd), host, port, db,
                )
    return ""


def load_server_base() -> str:
    cp = ConfigParser()
    cp.read(LOCAL_CFG, encoding="utf-8")
    if cp.has_section("server"):
        host = cp.get("server", "host", fallback="")
        port = cp.get("server", "port", fallback="8000")
        if host:
            return "http://%s:%s" % (host, port)
    return ""


def probe_tables(db) -> None:
    print("=" * 70)
    print("一、服务端库内实际保留范围")
    print("=" * 70)
    for name, label in TABLES:
        col = db[name]
        n = col.estimated_document_count()
        print("%-14s %-4s ~%d 条" % (name, label, n))
        if n == 0:
            print("%-14s      ** 空表 **" % "")
            continue
        lo = col.find_one({}, {"create_time": 1, "code": 1, "_id": 0}, sort=[("create_time", 1)])
        hi = col.find_one({}, {"create_time": 1, "code": 1, "_id": 0}, sort=[("create_time", -1)])
        print("%-14s      %s ~ %s" % ("", lo.get("create_time"), hi.get("create_time")))
        print("%-14s      code 形式样本: %s" % ("", lo.get("code")))


def probe_by_code(db, code: str) -> None:
    """同一只股票在四张表里的覆盖（只用 find，绕开聚合限制）。"""
    print()
    print("二、单只股票在各表的覆盖：%s" % code)
    for name, label in TABLES:
        col = db[name]
        lo = col.find_one({"code": code}, {"create_time": 1, "_id": 0}, sort=[("create_time", 1)])
        hi = col.find_one({"code": code}, {"create_time": 1, "_id": 0}, sort=[("create_time", -1)])
        if not lo:
            print("  %-4s 无记录" % label)
            continue
        n = col.count_documents({"code": code})
        print("  %-4s %5d 条  %s ~ %s" % (label, n, lo.get("create_time"), hi.get("create_time")))


def probe_api(base: str, code: str) -> None:
    """接口在不同区间下的返回：看「库内区间」「跨库内外」「远期历史」的差别。"""
    print()
    print("三、日K接口在不同区间下的返回（%s）" % code)
    cases = [
        ("库内最近 1 个月", "2026-09-01", "2026-10-13"),
        ("跨库内/库外（回测典型请求）", "2026-05-27", "2026-10-13"),
        ("远期历史（库内绝无）", "2025-01-01", "2025-03-31"),
    ]
    for tag, start, end in cases:
        try:
            r = requests.get(
                base + "/api/market/kline/day",
                params={"code": code, "start": start, "end": end},
                timeout=90,
            )
            rows = (r.json() or {}).get("data") or []
        except Exception as exc:
            print("  %-28s 请求异常: %s" % (tag, exc))
            continue
        if not rows:
            print("  %-28s 0 条" % tag)
            continue
        print("  %-28s %3d 条  %s ~ %s" % (
            tag, len(rows),
            rows[0].get("create_time"), rows[-1].get("create_time"),
        ))

    print()
    print("四、分钟接口（无回源路径，只查 minute_hq_tbl）")
    for tag, start, end in [("库内", "2026-09-01 09:30:00", "2026-09-01 15:00:00"),
                            ("库外（90 天前）", "2026-05-06 09:30:00", "2026-05-06 15:00:00")]:
        try:
            r = requests.get(base + "/api/market/kline/minute",
                             params={"code": code, "start": start, "end": end}, timeout=60)
            rows = (r.json() or {}).get("data") or []
            print("  %-16s %s -> %d 条" % (tag, start[:10], len(rows)))
        except Exception as exc:
            print("  %-16s 请求异常: %s" % (tag, exc))


def main() -> int:
    uri = load_mongo_uri()
    base = load_server_base()
    if not uri:
        print("未找到 mongo 连接配置（不打印凭据），跳过库内探测")
        db = None
    else:
        db = MongoClient(uri, serverSelectionTimeoutMS=10000)["q_share"]

    if db is not None:
        probe_tables(db)
        for code in ("000001", "600519"):
            probe_by_code(db, code)

    if base:
        probe_api(base, "000001")
    else:
        print("未找到 [server] 配置，跳过接口探测")

    print()
    print("=" * 70)
    print("结论速记")
    print("  · 四张表都在同一份清理任务里：>90 天删除（日/周/月/分钟一视同仁）")
    print("  · 日K可回源（实测能取到 2025 年），分钟K不可回源（90 天前永久丢失）")
    print("  · 回源触发看「区间内该 code 是否有任意记录」，非「区间是否完整」")
    print("    ⇒ 部分缺失时直接返回残缺数据，HTTP 200 且无提示")
    print("  · week_hq_tbl / month_hq_tbl 为空表，且本端无任何调用者")
    return 0


if __name__ == "__main__":
    sys.exit(main())

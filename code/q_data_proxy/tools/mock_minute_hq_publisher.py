"""模拟回放分钟行情到 MQTT。

盘中 minute_hq_sync_task 每分钟把聚合出的分钟行情推送到
`{prefix}/stock/minute/{pool_id}/{code}`。本脚本读取 minute_hq_tbl 中
「最近有数据的一天」的全部分钟行情记录，按 minute_time 分组，以 --interval 秒
为间隔逐组重放同样的 payload 与 topic，用于下游消费方联调与回放验证。

topic 由项目的 MqttTopic.stock_minute_pool_code / stock_minute_code 生成，
与生产 _publish_minute_hq 用同一套函数，避免 topic 格式在脚本里分叉；
--prefix 用于把顶层前缀 q_share 换成别的值（如测试环境）。

回放出的消息与实时消息的 topic 结构、payload 字段完全一致，下游无法仅凭
消息内容区分。开始回放前请确认下游订阅方不会把回放数据当成实时行情；
如需隔离，用 --prefix 换成测试前缀（如 q_share_test）并调整订阅方。

用法（项目根目录执行，需能连上 MongoDB 与 MQTT）：

    # 默认：回放最近有数据的一天，每分钟一组，推生产 topic
    python tools/mock_minute_hq_publisher.py

    # 指定日期与时间范围
    python tools/mock_minute_hq_publisher.py --date 2026-09-21 --start 09:30 --end 10:00

    # 快速试跑：5 秒一组，只回放 10 组，不外发
    python tools/mock_minute_hq_publisher.py --interval 5 --limit 10 --dry-run

    # 用测试前缀且不带 pool 维度
    python tools/mock_minute_hq_publisher.py --prefix q_share_test --pool-mode plain

    # 指定实例配置（多实例部署时）
    python tools/mock_minute_hq_publisher.py --cfg cfg/stock_2.cfg
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timedelta

# 允许从项目根目录（或 tools/ 目录）直接执行
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.config import ENV_STOCK_CFG, stock_cfg_path  # noqa: E402

logger = logging.getLogger("mock_minute_hq")

# 分钟行情表名（与 MongoStockMinuteHqImpl.minute_hq_table 一致）
TABLE_NAME = "minute_hq_tbl"

# payload 字段与顺序，取自 StockMinuteHqDao 与生产 _publish_minute_hq 组装出的记录，
# 保证回放消息与实时消息逐字段可比
PAYLOAD_FIELDS = (
    "code",
    "name",
    "price",
    "change_percent",
    "change_amount",
    "volume",
    "amount",
    "amp",
    "high",
    "low",
    "open",
    "close",
    "preclose",
    "qrr",
    "turnover",
    "minute_time",
    "create_time",
)

# 按「最近一天」探测时最多向前回退的自然日数（覆盖长假）
MAX_LOOKBACK_DAYS = 30


def pure_code(code: str) -> str:
    """取纯数字代码，避免 000001 / 000001.SZ / sh000001 被当成三只股票。"""
    matched = re.search(r"\d+", code or "")
    return matched.group() if matched else (code or "")


def hhmm(value: str) -> str:
    """把 HH:MM 或 HH:MM:SS 归一化成 HH:MM。"""
    return (value or "").strip()[:5]


def build_payload(record: dict) -> str:
    """按生产字段顺序序列化一条分钟行情记录。"""
    payload = {field: record.get(field) for field in PAYLOAD_FIELDS}
    return json.dumps(payload, ensure_ascii=False)


def _mqtt_topic():
    """延迟导入 MqttTopic，topic 统一由项目 MqttTopic 生成，避免格式分叉。"""
    from mq.mqtt_client import MqttTopic

    return MqttTopic


def apply_prefix(topic: str, prefix: str) -> str:
    """把 MqttTopic 生成的 topic 顶层前缀换成 --prefix 指定的值。

    MqttTopic 固定以 `q_share` 开头（见 STOCK_MINUTE_SINGLE），测试环境需要
    换个前缀时，只替换第一级，后面的层级保持不变。
    """
    default_prefix = _mqtt_topic().STOCK_MINUTE_SINGLE.split("/", 1)[0]
    if not prefix or prefix == default_prefix:
        return topic
    head, sep, tail = topic.partition("/")
    if head != default_prefix:
        return topic
    return f"{prefix}{sep}{tail}"


def build_topics(prefix: str, code: str, pool_ids, pool_mode: str) -> list[str]:
    """按 pool 模式生成 topic 列表。

    topic 一律由 MqttTopic 生成：带 pool 用 stock_minute_pool_code，
    不带 pool 用 stock_minute_code，与生产 _publish_minute_hq 同源。

    auto  : 有 pool 归属时带 pool，否则不带
    pool  : 严格复现生产（只在有 pool 归属时推送），无归属返回空列表
    plain : 一律不带 pool 维度
    """
    topic_cls = _mqtt_topic()
    ids = [pool_id for pool_id in (pool_ids or []) if pool_id]
    if pool_mode == "plain" or not ids:
        if pool_mode == "pool":
            return []
        topics = [topic_cls.stock_minute_code(code)]
    else:
        topics = [topic_cls.stock_minute_pool_code(pool_id, code) for pool_id in ids]
    return [apply_prefix(topic, prefix) for topic in topics]


def group_by_minute(records: list) -> "OrderedDict[str, list]":
    """按 minute_time 分组，组间按时间升序，组内按 code 升序。"""
    groups: "OrderedDict[str, list]" = OrderedDict()
    ordered = sorted(
        records,
        key=lambda r: (str(r.get("minute_time", "")), str(r.get("code", ""))),
    )
    for record in ordered:
        groups.setdefault(str(record.get("minute_time", "")), []).append(record)
    return groups


def in_time_range(minute_time: str, start: str, end: str) -> bool:
    """按 HH:MM 过滤，minute_time 形如 YYYY-MM-DD HH:MM:SS。"""
    clock = (minute_time or "")[11:16]
    if start and clock < start:
        return False
    if end and clock > end:
        return False
    return True


def parse_codes(codes_arg: str) -> set:
    """解析 --codes，返回纯数字代码集合。

    分隔符支持逗号、分号、空白与中文顿号/逗号；代码内部的 `.`（000001.SZ）
    不是分隔符，保持完整后由 pure_code 统一取纯数字。
    """
    return {
        pure_code(item)
        for item in re.split(r"[,;\s、，]+", codes_arg or "")
        if item.strip()
    }


def resolve_replay_date(collection, max_lookback_days: int = MAX_LOOKBACK_DAYS) -> str:
    """找出最近一个有分钟行情记录的自然日。

    逐日回退探测而不是 `sort(minute_time)` 取极值：minute_hq_tbl 的索引是
    (code, minute_time)，前缀是 code，按 minute_time 全局排序会退化成全表排序，
    数据量大时可能触碰 MongoDB 的排序内存上限。
    """
    today = datetime.now().date()
    for offset in range(max_lookback_days):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        if collection.find_one(
            {"minute_time": {"$gte": f"{day} 00:00:00", "$lte": f"{day} 23:59:59"}},
            {"_id": 1},
        ):
            return day
    return ""


def fetch_day_records(collection, date_str: str, codes: set | None = None) -> list:
    """查询某一天的全部分钟行情记录（按 minute_time 升序）。"""
    condition = {
        "minute_time": {"$gte": f"{date_str} 00:00:00", "$lte": f"{date_str} 23:59:59"}
    }
    records = list(collection.find(condition).sort("minute_time", 1))
    if codes:
        # 库内 code 可能是 000001 / 000001.SZ / sh000001，统一按纯数字匹配
        records = [r for r in records if pure_code(r.get("code", "")) in codes]
    return records


def build_pool_lookup():
    """构造 code -> pool_ids 的查询函数，口径与生产 _publish_minute_hq 一致。"""
    from stock_fetch.stock_fetch import StockFetch

    stock_fetch = StockFetch()
    sync_stocks = stock_fetch.load_agent_pool_stocks()

    def lookup(code: str):
        return stock_fetch.find_pool_id_in_sync_stocks(code, sync_stocks)

    return lookup, len(sync_stocks)


def wait_connected(mqtt_client, timeout: float = 10.0) -> bool:
    """等待 MQTT 异步连接建立。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if mqtt_client.is_connected:
            return True
        time.sleep(0.2)
    return bool(mqtt_client.is_connected)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="回放 minute_hq_tbl 中的分钟行情到 MQTT",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cfg", default="", help="配置文件路径，默认取 Q_PROXY_CFG 或 cfg/stock.cfg")
    parser.add_argument("--date", default="", help="回放日期 YYYY-MM-DD，默认最近有数据的一天")
    parser.add_argument("--start", default="", help="起始时间 HH:MM（含）")
    parser.add_argument("--end", default="", help="结束时间 HH:MM（含）")
    parser.add_argument("--codes", default="", help="只回放这些代码，逗号分隔")
    parser.add_argument("--interval", type=float, default=60.0, help="相邻分钟组之间的间隔秒数")
    parser.add_argument("--limit", type=int, default=0, help="最多回放多少组，0 表示不限")
    parser.add_argument(
        "--pool-mode",
        choices=("auto", "pool", "plain"),
        default="auto",
        help="topic 是否带 pool 维度：auto=有 pool 用 pool，否则无 pool；pool=严格复现生产；plain=不带 pool",
    )
    parser.add_argument(
        "--prefix", default="q_share", help="替换 MqttTopic 生成 topic 的顶层前缀（用于测试环境隔离）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印不外发")
    parser.add_argument("--verbose", action="store_true", help="输出 debug 日志")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.cfg:
        os.environ[ENV_STOCK_CFG] = os.path.abspath(args.cfg)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s-%(levelname)s-%(message)s",
    )

    prefix = args.prefix.rstrip("/")
    start, end = hhmm(args.start), hhmm(args.end)
    codes = parse_codes(args.codes)

    logger.info("使用配置: %s", os.path.abspath(stock_cfg_path()))

    from db.mongo.mongo_exec import MongoExec

    mongo_exec = MongoExec()
    if getattr(mongo_exec, "db", None) is None:
        logger.error("MongoDB 未初始化，请检查配置文件的 [mongo] 段")
        return 1
    collection = mongo_exec.db[TABLE_NAME]

    date_str = args.date.strip()
    if not date_str:
        date_str = resolve_replay_date(collection)
        if not date_str:
            logger.error("最近 %d 天内没有分钟行情记录，请用 --date 显式指定日期", MAX_LOOKBACK_DAYS)
            return 1
        logger.info("未指定 --date，自动选用最近有数据的一天: %s", date_str)

    records = fetch_day_records(collection, date_str, codes or None)
    if start or end:
        records = [r for r in records if in_time_range(r.get("minute_time", ""), start, end)]
    if not records:
        logger.error("日期 %s 没有匹配的分钟行情记录（start=%s end=%s codes=%s）",
                     date_str, start or "-", end or "-", args.codes or "-")
        return 1

    groups = group_by_minute(records)
    total_groups = len(groups)
    if args.limit > 0 and total_groups > args.limit:
        groups = OrderedDict(list(groups.items())[: args.limit])
        total_groups = len(groups)

    logger.info(
        "回放计划: date=%s, 分钟组=%d, 记录=%d, 间隔=%ss, prefix=%s, pool_mode=%s, dry_run=%s",
        date_str, total_groups, sum(len(items) for items in groups.values()),
        args.interval, prefix, args.pool_mode, args.dry_run,
    )

    # pool 映射：只有需要带 pool 维度时才构造（会加载交易日历，略慢）
    lookup = None
    if args.pool_mode != "plain":
        try:
            lookup, pool_stock_count = build_pool_lookup()
            logger.info("股票池映射已加载，池内股票数: %d", pool_stock_count)
        except Exception as err:
            if args.pool_mode == "pool":
                logger.error("--pool-mode pool 需要股票池映射，加载失败: %s", err)
                return 1
            logger.warning("股票池映射加载失败，回退为不带 pool 的 topic: %s", err)

    mqtt_client = None
    if not args.dry_run:
        from mq.mqtt_client import MqttClient

        mqtt_client = MqttClient()
        if not mqtt_client.connect() or not wait_connected(mqtt_client):
            logger.error("MQTT 连接失败，请检查配置文件的 [mqtt] 段与 broker 状态")
            return 1
        logger.info("MQTT 已连接，client_id: %s", mqtt_client.mqtt_client_id)

    published = 0
    skipped = 0
    try:
        for index, (minute_time, items) in enumerate(groups.items(), 1):
            for record in items:
                code = record.get("code", "")
                if not code:
                    continue
                topics = build_topics(
                    prefix, code, lookup(code) if lookup else None, args.pool_mode
                )
                if not topics:
                    skipped += 1
                    continue
                payload = build_payload(record)
                for topic in topics:
                    if args.dry_run:
                        logger.info("[dry-run] %s %s", topic, payload)
                        published += 1
                        continue
                    if mqtt_client.publish(topic, payload):
                        published += 1
                        logger.info("推送成功 topic=%s payload=%s", topic, payload)
                    else:
                        skipped += 1
                        logger.warning("推送失败 topic=%s payload=%s", topic, payload)
            logger.info("进度 %d/%d minute_time=%s 累计推送=%d", index, total_groups, minute_time, published)
            if index < total_groups and args.interval > 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("收到中断信号，提前结束回放")
    finally:
        if mqtt_client is not None:
            mqtt_client.disconnect()

    logger.info("回放结束: 推送 %d 条, 跳过 %d 条", published, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())

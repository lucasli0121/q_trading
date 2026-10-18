"""回测前清理 Redis 业务缓存（用完即删）。

背景：上一轮回测会把当日分钟行情写进 Redis（`bar:{code}:{day}` 列表，TTL 6 小时），
残留的列表是**一整天**的完整行情。下一轮即使逐分钟推送，也可能读到这份全天数据，
让「第 N 分钟」拿到第 N 分钟之后才产生的 K 线。每轮开跑前清一次，消除这个不确定性。

只删本端业务缓存，不动 Redis 里的其它键（服务端/实盘可能共用同一实例）：

  前缀        bar:     分钟行情
              score:   策略打分
              select:  选股结果
              hprice:  持仓区间最高价
  中缀        :sr_points:   波段策略的当日支撑/阻力点位
              （key 形如 `波段交易策略:sr_points:default:600150.SH`，策略名前缀不固定，
               只能用中缀匹配）

sr_points 单列出来，是因为它的读写口曾经绕过 `UserStrategyCache` 的开关
（2026-10-14 已修），回测期间会真的写进 Redis，残留后被下一轮回测读到。

用法（容器内）：python _bt_clear_redis.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/q_trading_work")

PREFIXES: tuple[str, ...] = ("bar:", "score:", "select:", "hprice:")
INFIXES: tuple[str, ...] = (":sr_points:",)


def main() -> int:
    from app_context import AppContext

    execer = AppContext().redis_exec
    if not execer.enabled:
        print("[clear-redis] Redis 未启用，跳过")
        return 0
    client = execer._client()  # noqa: SLF001 - 需要 SCAN，RedisExec 未暴露该接口
    if client is None:
        print("[clear-redis] Redis 连接不可用，跳过")
        return 0

    total: int = 0
    patterns: tuple[str, ...] = tuple(f"{p}*" for p in PREFIXES) + tuple(
        f"*{i}*" for i in INFIXES
    )
    for pattern in patterns:
        removed: int = 0
        try:
            for key in client.scan_iter(match=pattern, count=1000):
                client.delete(key)
                removed += 1
        except Exception as exc:  # noqa: BLE001 - 清理失败不应中断回测
            print(f"[clear-redis] {pattern} 清理异常: {exc}")
            continue
        if removed:
            print(f"[clear-redis] {pattern} 删除 {removed} 个")
        total += removed
    print(f"[clear-redis] 共删除 {total} 个键")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

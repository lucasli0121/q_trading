"""
Author: liguoqiang
Date: 2026-09-13
Description: UserStrategyCache 单例缓存测试。

覆盖 P0-3 / P0-4 / P2-13 / P2-25 引入的缓存语义：
    - 单例身份与 key 构造；
    - 当日分钟行情的写入 / 截断 / 条数上限 / 无效入参；
    - 策略打分按 user_strategy_id + 方向隔离、无条件覆盖；
    - 选股结果按 user_strategy_id 隔离、未命中返回 None、返回副本；
    - clear_user / clear_minute_day 的清理边界；
    - set_redis_enabled(False)（回测场景）下完全不走 Redis。

所有用例通过 `set_redis_enabled(False)` 关闭 Redis，只验证内存快照语义，
不触碰实盘 Redis（setUp/tearDown 成对开关，结束恢复默认）。
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import patch

from core.user_strategy_cache import UserStrategyCache


class TestUserStrategyCache(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = UserStrategyCache()
        # 隔离：关闭 Redis，只验证内存快照；并清空上一次用例残留
        self.cache.set_redis_enabled(False)
        self.cache._minute_snapshot.clear()
        self.cache._score_snapshot.clear()
        self.cache._select_snapshot.clear()
        self.cache._last_day = ""
        self.cache._redis_failed = False

    def tearDown(self) -> None:
        self.cache.set_redis_enabled(True)

    # ---- 单例与工具方法 ----

    def test_singleton_identity(self) -> None:
        """Singleton 元类保证全局唯一实例，且 __init__ 只执行一次。"""
        self.assertIs(UserStrategyCache(), self.cache)

    def test_day_of(self) -> None:
        self.assertEqual(
            UserStrategyCache.day_of("2026-07-15 09:30:00"), "2026-07-15"
        )
        self.assertEqual(UserStrategyCache.day_of("2026-07-15"), "2026-07-15")
        # 空/异常输入回退当天，避免缓存 key 变成空串
        today: str = datetime.datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(UserStrategyCache.day_of(""), today)
        self.assertEqual(UserStrategyCache.day_of("bad"), today)

    def test_key_builders(self) -> None:
        self.assertEqual(
            UserStrategyCache.minute_key("000001", "2026-07-15"),
            "bar:000001:2026-07-15",
        )
        self.assertEqual(
            UserStrategyCache.score_key("us1", "000001", "BUY", "2026-07-15"),
            "score:us1:000001:BUY:2026-07-15",
        )
        self.assertEqual(
            UserStrategyCache.select_key("us1", "2026-07-15"),
            "select:us1:2026-07-15",
        )

    # ---- 当日分钟行情 ----

    def test_push_and_get_minute_bars(self) -> None:
        day: str = "2026-07-15"
        for i in range(5):
            self.cache.push_minute_bar(
                "000001",
                {"create_time": f"{day} 09:3{i}:00", "price": 10.0 + i},
            )
        bars: list[dict] = self.cache.get_minute_bars("000001", day=day)
        self.assertEqual(len(bars), 5)
        self.assertEqual(bars[0]["price"], 10.0)

        # length 截断取尾部（策略要看「最近 N 根」）
        tail: list[dict] = self.cache.get_minute_bars("000001", length=2, day=day)
        self.assertEqual([b["price"] for b in tail], [13.0, 14.0])

        # 返回副本：外部修改不得污染缓存
        tail[0]["price"] = 999.0
        self.assertEqual(
            self.cache.get_minute_bars("000001", length=1, day=day)[0]["price"],
            14.0,
        )

        # 其他股票 / 其他日期互不干扰
        self.assertEqual(self.cache.get_minute_bars("600519", day=day), [])
        self.assertEqual(self.cache.get_minute_bars("000001", day="2026-07-16"), [])

    def test_minute_cache_evicts_oldest(self) -> None:
        """超过 max_cache 时丢弃最旧数据（保留最近 N 根）。"""
        day: str = "2026-07-15"
        for i in range(5):
            self.cache.push_minute_bar(
                "000001",
                {"create_time": f"{day} 09:30:00", "price": float(i)},
                max_cache=3,
            )
        bars: list[dict] = self.cache.get_minute_bars("000001", day=day)
        self.assertEqual([b["price"] for b in bars], [2.0, 3.0, 4.0])

    def test_push_minute_bar_rejects_invalid_input(self) -> None:
        day: str = "2026-07-15"
        self.cache.push_minute_bar("", {"create_time": f"{day} 09:30:00"})
        self.cache.push_minute_bar("000001", "not-a-dict")  # type: ignore[arg-type]
        self.assertEqual(self.cache.get_minute_bars("000001", day=day), [])

    # ---- 策略打分 ----

    def test_score_cache_isolated_per_user_and_action(self) -> None:
        day: str = "2026-07-15"
        self.cache.save_score(
            "us1", "000001", "BUY", {"score": 12.0, "check_number": 1}, day
        )
        self.cache.save_score(
            "us2", "000001", "BUY", {"score": 3.0, "check_number": 1}, day
        )
        self.assertEqual(self.cache.get_score("us1", "000001", "BUY", day)["score"], 12.0)
        self.assertEqual(self.cache.get_score("us2", "000001", "BUY", day)["score"], 3.0)
        # 方向隔离
        self.assertEqual(self.cache.get_score("us1", "000001", "SELL", day), {})

    def test_score_cache_overwrites_unconditionally(self) -> None:
        """P0-3：打分每次都无条件覆盖，否则 Redis 不可用时分数会冻结。"""
        day: str = "2026-07-15"
        self.cache.save_score(
            "us1", "000001", "BUY", {"score": 12.0, "check_number": 1}, day
        )
        self.cache.save_score(
            "us1", "000001", "BUY", {"score": 20.0, "check_number": 2}, day
        )
        entry: dict = self.cache.get_score("us1", "000001", "BUY", day)
        self.assertEqual(entry["score"], 20.0)
        self.assertEqual(entry["check_number"], 2)

    def test_get_score_returns_copy(self) -> None:
        day: str = "2026-07-15"
        self.cache.save_score("us1", "000001", "BUY", {"score": 1.0}, day)
        got: dict = self.cache.get_score("us1", "000001", "BUY", day)
        got["score"] = 999.0
        self.assertEqual(self.cache.get_score("us1", "000001", "BUY", day)["score"], 1.0)

    # ---- 选股结果 ----

    def test_select_stocks_roundtrip_and_missing(self) -> None:
        day: str = "2026-07-15"
        # 未命中必须返回 None（调用方据此决定是否回源服务端）
        self.assertIsNone(self.cache.get_select_stocks("us1", day))

        self.cache.save_select_stocks("us1", day, {"000001", "600519"})
        self.assertEqual(
            self.cache.get_select_stocks("us1", day), {"000001", "600519"}
        )
        # 用户隔离：P2-25 的核心诉求
        self.assertIsNone(self.cache.get_select_stocks("us2", day))

        # 返回副本
        got = self.cache.get_select_stocks("us1", day)
        assert got is not None
        got.add("999999")
        self.assertEqual(
            self.cache.get_select_stocks("us1", day), {"000001", "600519"}
        )

    # ---- 清理 ----

    def test_clear_user_and_clear_minute_day(self) -> None:
        day: str = "2026-07-15"
        self.cache.save_score("us1", "000001", "BUY", {"score": 1.0}, day)
        self.cache.save_select_stocks("us1", day, {"000001"})
        self.cache.save_score("us2", "000001", "BUY", {"score": 2.0}, day)
        self.cache.push_minute_bar("000001", {"create_time": f"{day} 09:30:00"})

        self.cache.clear_user("us1")
        self.assertEqual(self.cache.get_score("us1", "000001", "BUY", day), {})
        self.assertIsNone(self.cache.get_select_stocks("us1", day))
        # 其他用户不受影响
        self.assertEqual(self.cache.get_score("us2", "000001", "BUY", day)["score"], 2.0)
        # 行情缓存不属于某个用户，clear_user 不应清掉它
        self.assertEqual(len(self.cache.get_minute_bars("000001", day=day)), 1)

        self.cache.clear_minute_day(day)
        self.assertEqual(self.cache.get_minute_bars("000001", day=day), [])

    def test_clear_user_ignores_empty_id(self) -> None:
        """空 user_strategy_id 必须直接返回，否则会误清空全表。"""
        day: str = "2026-07-15"
        self.cache.save_score("", "000001", "BUY", {"score": 5.0}, day)
        self.cache.clear_user("")
        self.assertEqual(self.cache.get_score("", "000001", "BUY", day)["score"], 5.0)

    # ---- Redis 开关 ----

    def test_redis_disabled_skips_redis_io(self) -> None:
        """回测场景：set_redis_enabled(False) 后读写全部只走内存快照。"""
        cache = self.cache
        cache.set_redis_enabled(False)
        day: str = "2026-07-15"
        with patch.object(UserStrategyCache, "_redis") as mock_redis:
            cache.push_minute_bar("000001", {"create_time": f"{day} 09:30:00"})
            cache.get_minute_bars("000001", day=day)
            cache.save_score("us1", "000001", "BUY", {"score": 1.0}, day)
            cache.get_score("us1", "000002", "BUY", day)
            cache.save_select_stocks("us1", day, {"000001"})
            cache.get_select_stocks("us1", "2026-07-16")
            mock_redis.assert_not_called()

    def test_set_redis_enabled_toggle(self) -> None:
        cache = self.cache
        cache.set_redis_enabled(False)
        self.assertTrue(cache._redis_disabled)
        cache.set_redis_enabled(True)
        self.assertFalse(cache._redis_disabled)

    def test_reset_redis_failure(self) -> None:
        """失败标记置位后当日不再重试 Redis，跨日需由 reset 清掉。"""
        self.cache.set_redis_enabled(True)
        self.cache._redis_failed = True
        # _redis_failed 命中时直接返回 False，不会真的去访问 Redis
        self.assertFalse(self.cache._redis_ready())
        self.cache.reset_redis_failure()
        self.assertFalse(self.cache._redis_failed)


if __name__ == "__main__":
    unittest.main()

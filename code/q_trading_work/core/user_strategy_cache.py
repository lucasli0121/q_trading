"""
Description: 用户策略全局缓存（单例）。

从 BaseStrategy 中抽出的全局缓存，解决以下问题：
1. 多用户（per-user 策略实例）重复写入同一份行情缓存，导致因子看到「同一分钟 N 条」
   的污染数据（原 P0-4）；
2. 每次检查都全量读 Redis 并逐条 JSON 解析（原 P2-13）；
3. 每个用户各自请求选股结果（原 P2-25）；
4. 本地兜底缓存只在「不存在时写入」，Redis 不可用时评分冻结（原 P0-3）。

缓存内容与写入方：

    +----------------+-----------------------------------------+------------------+
    | 缓存           | key                                     | 写入方           |
    +----------------+-----------------------------------------+------------------+
    | 当日分钟行情   | bar:{code}:{day}                        | workflow（唯一） |
    | 策略打分       | score:{usid}:{code}:{action}:{day}      | 各策略实例       |
    | 选股结果       | select:{usid}:{day}                     | 各策略实例       |
    | 区间最高价     | hprice:{code}:{buy_date}:{day}          | 各策略实例       |
    +----------------+-----------------------------------------+------------------+

设计要点：
- **双重缓存**：进程内快照 + Redis。读优先内存，未命中才读 Redis 并回填快照，
  避免同一次检查内反复访问 Redis；写同时更新内存与 Redis。
- **行情由 workflow 单点写入**，策略侧只读，从根本上消除多用户重复写入。
- **写保护**：内存字典与 Redis 失败标记统一由 `threading.RLock` 保护。
- Redis 未启用或不可用时全链路安全降级到内存快照。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
from typing import Any

from core.singleton import Singleton


class UserStrategyCache(metaclass=Singleton):
    """用户策略全局缓存单例。

    使用方式::

        cache = UserStrategyCache()
        cache.push_minute_bar("000001", bar)              # workflow 写
        bars = cache.get_minute_bars("000001", length=30) # 策略读
        cache.save_score("us1", "000001", "BUY", entry, day)
    """

    # 分钟行情缓存过期时间（秒）：6 小时，覆盖整个交易日
    MINUTE_CACHE_TTL: int = 6 * 60 * 60
    # 单只股票当日分钟缓存最大条数（240 = 4 小时交易时间的分钟数）
    MAX_MINUTE_CACHE: int = 240
    # 策略打分缓存过期时间（秒）
    SCORE_CACHE_TTL: int = 6 * 60 * 60
    # 选股结果缓存过期时间（秒）：12 小时
    SELECT_CACHE_TTL: int = 12 * 60 * 60
    # 区间最高价缓存过期时间（秒）：6 小时，覆盖整个交易日
    HIGHEST_PRICE_CACHE_TTL: int = 6 * 60 * 60

    def __init__(self) -> None:
        """初始化内存快照与锁（单例，仅首次生效）。"""
        self.logger = logging.getLogger(__name__)
        self._lock = threading.RLock()
        # 内存快照：行情 (code, day) -> list[bar]
        self._minute_snapshot: dict[tuple[str, str], list[dict[str, Any]]] = {}
        # 最近一次写入的行情日期，读取未显式指定日期时用它定位 key
        self._last_day: str = ""
        # 内存快照：打分 (user_strategy_id, code, action, day) -> entry
        self._score_snapshot: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        # 内存快照：选股结果 (user_strategy_id, day) -> set[code]
        self._select_snapshot: dict[tuple[str, str], set[str]] = {}
        # 内存快照：区间最高价 (code, buy_date, day) -> price
        self._highest_price_snapshot: dict[tuple[str, str, str], float] = {}
        # Redis 不可用标记：失败一次后本次运行不再重试，避免每次调用都等超时
        self._redis_failed: bool = False
        # 显式关闭 Redis 的开关（回测等离线场景置为 True，只走内存快照，
        # 避免把历史行情写进实盘共用 Redis，也避免读到实盘的当日行情）
        self._redis_disabled: bool = False

    # ---- 通用工具 ----

    @property
    def redis_enabled(self) -> bool:
        """Redis 是否启用（来自配置，Redis 初始化失败时为 False）。"""
        try:
            return bool(self._redis().enabled)
        except Exception:  # pragma: no cover - 配置缺失等异常场景
            return False

    def _redis(self) -> Any:
        """返回全局 Redis 执行器（懒加载，避免与 AppContext 形成导入环）。"""
        from app_context import AppContext

        return AppContext().redis_exec

    def _redis_ready(self) -> bool:
        """是否可以对 Redis 发起读写（已启用、未被显式关闭、且未标记失败）。"""
        if self._redis_disabled or self._redis_failed:
            return False
        return bool(self._redis().enabled)

    def set_redis_enabled(self, enabled: bool) -> None:
        """显式开关 Redis 持久化（默认开启）。

        回测等离线场景应调用 `set_redis_enabled(False)`：
        一是避免把历史行情/打分写进与实盘共用的 Redis，
        二是避免回测读到实盘当日的行情从而污染结论。
        关闭后所有读写只走进程内快照。

        :param enabled: True 恢复使用 Redis，False 只走内存快照
        """
        with self._lock:
            self._redis_disabled = not enabled
        self.logger.info("UserStrategyCache Redis 持久化已%s", "开启" if enabled else "关闭")

    @staticmethod
    def day_of(time_str: str) -> str:
        """从时间字符串中取日期部分，为空时取当天。

        :param time_str: 行情/信号时间，如 YYYY-MM-DD HH:MM:SS
        :return: 日期字符串 YYYY-MM-DD
        """
        if time_str and len(time_str) >= 10:
            return time_str[:10]
        return datetime.datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def minute_key(code: str, day: str) -> str:
        return f"bar:{code}:{day}"

    @staticmethod
    def score_key(user_strategy_id: str, code: str, action: str, day: str) -> str:
        return f"score:{user_strategy_id}:{code}:{action}:{day}"

    @staticmethod
    def select_key(user_strategy_id: str, day: str) -> str:
        return f"select:{user_strategy_id}:{day}"

    @staticmethod
    def highest_price_key(code: str, buy_date: str, day: str) -> str:
        return f"hprice:{code}:{buy_date}:{day}"

    # ---- 当日分钟行情 ----

    def push_minute_bar(
        self,
        code: str,
        stock_data: dict[str, Any],
        max_cache: int = MAX_MINUTE_CACHE,
    ) -> None:
        """写入一根分钟行情（**应由 workflow 单点调用**，策略侧不要写）。

        同时更新内存快照与 Redis 列表；Redis 写入失败时仅保留内存快照。

        :param code: 股票代码
        :param stock_data: 分钟行情数据（price/volume/amount/high/low/create_time 等）
        :param max_cache: 单只股票最大保留条数
        """
        if not code or not isinstance(stock_data, dict):
            return
        day: str = self.day_of(str(stock_data.get("create_time", "")))
        snapshot_key: tuple[str, str] = (code, day)
        with self._lock:
            self._last_day = day
            bars: list[dict[str, Any]] = self._minute_snapshot.setdefault(snapshot_key, [])
            bars.append(dict(stock_data))
            if len(bars) > max_cache:
                del bars[: len(bars) - max_cache]

        if not self._redis_ready():
            return
        try:
            redis_exec = self._redis()
            key: str = self.minute_key(code, day)
            if redis_exec.push_value_to_rlist(
                key, json.dumps(stock_data), ex=self.MINUTE_CACHE_TTL
            ):
                redis_exec.ltrim_rlist(key, -max_cache, -1)
                return
            with self._lock:
                self._redis_failed = True
        except Exception as exc:  # pragma: no cover - 防御性
            with self._lock:
                self._redis_failed = True
            self.logger.warning("行情缓存写入 Redis 失败，回退内存快照: %s", exc)

    def get_minute_bars(
        self,
        code: str,
        length: int = 0,
        day: str = "",
    ) -> list[dict[str, Any]]:
        """读取指定股票的当日分钟行情。

        读取优先级：内存快照 → Redis（命中后回填快照）。
        同一进程内对同一 (code, day) 最多只访问一次 Redis，避免反复 IO。

        :param code: 股票代码
        :param length: 需要的条数；<=0 表示取当日全部
        :param day: 指定日期 YYYY-MM-DD；为空时取最近一次写入行情的日期
        :return: 分钟行情列表（按时间正序），无数据返回空列表
        """
        if not code:
            return []
        with self._lock:
            resolved_day: str = day or self._last_day or self.day_of("")
            snapshot_key: tuple[str, str] = (code, resolved_day)
            cached: list[dict[str, Any]] | None = self._minute_snapshot.get(snapshot_key)

        if cached is None:
            cached = self._load_minute_from_redis(code, resolved_day)
            with self._lock:
                # 其他线程可能已回填，保留已有内容（可能更长）
                existing: list[dict[str, Any]] | None = self._minute_snapshot.get(snapshot_key)
                if existing is None:
                    self._minute_snapshot[snapshot_key] = cached
                else:
                    cached = existing

        if length and length > 0:
            return [dict(item) for item in cached[-length:]]
        return [dict(item) for item in cached]

    def _load_minute_from_redis(self, code: str, day: str) -> list[dict[str, Any]]:
        """从 Redis 加载当日分钟行情并解析为 dict 列表。

        :param code: 股票代码
        :param day: 日期 YYYY-MM-DD
        :return: 解析成功的行情列表；Redis 不可用或无数据返回空列表
        """
        if not self._redis_ready():
            return []
        try:
            redis_exec = self._redis()
            raw: list[str] | None = redis_exec.get_value_from_rlist(
                self.minute_key(code, day), -1, False
            )
        except Exception as exc:  # pragma: no cover - 防御性
            self.logger.warning("行情缓存读取 Redis 失败: %s", exc)
            return []
        if raw is None:
            with self._lock:
                self._redis_failed = True
            return []
        entries: list[dict[str, Any]] = []
        for item in raw:
            try:
                entries.append(json.loads(item))
            except json.JSONDecodeError:
                self.logger.warning("行情缓存 JSON 解析失败: code=%s", code)
        return entries

    # ---- 策略打分 ----

    def save_score(
        self,
        user_strategy_id: str,
        code: str,
        action: str,
        entry: dict[str, Any],
        day: str,
    ) -> None:
        """保存某用户策略对某股票的打分（无条件覆盖，修复 P0-3）。

        key 含 user_strategy_id，避免不同用户互相覆盖。

        :param user_strategy_id: 用户策略 ID
        :param code: 股票代码
        :param action: BUY / SELL
        :param entry: 打分条目（score/avg_score/check_number/done 等）
        :param day: 日期 YYYY-MM-DD
        """
        snapshot_key: tuple[str, str, str, str] = (user_strategy_id, code, action, day)
        with self._lock:
            # 无条件覆盖：原实现在「不存在时」才写，导致 Redis 不可用时分数被冻结
            self._score_snapshot[snapshot_key] = dict(entry)

        if not self._redis_ready():
            return
        try:
            redis_exec = self._redis()
            if not redis_exec.set_key_string(
                self.score_key(user_strategy_id, code, action, day),
                json.dumps(entry),
                ex=self.SCORE_CACHE_TTL,
            ):
                with self._lock:
                    self._redis_failed = True
        except Exception as exc:  # pragma: no cover - 防御性
            with self._lock:
                self._redis_failed = True
            self.logger.warning("打分缓存写入 Redis 失败，回退内存快照: %s", exc)

    def get_score(
        self,
        user_strategy_id: str,
        code: str,
        action: str,
        day: str,
    ) -> dict[str, Any]:
        """读取某用户策略对某股票的打分。

        读取优先级：内存快照 → Redis（命中后回填快照）。

        :param user_strategy_id: 用户策略 ID
        :param code: 股票代码
        :param action: BUY / SELL
        :param day: 日期 YYYY-MM-DD
        :return: 打分条目，无记录返回空字典
        """
        snapshot_key: tuple[str, str, str, str] = (user_strategy_id, code, action, day)
        with self._lock:
            cached: dict[str, Any] | None = self._score_snapshot.get(snapshot_key)
        if cached is not None:
            return dict(cached)

        entry: dict[str, Any] = {}
        if self._redis_ready():
            try:
                raw: str | None = self._redis().get_key_string(
                    self.score_key(user_strategy_id, code, action, day)
                )
                if raw:
                    parsed: Any = json.loads(raw)
                    if isinstance(parsed, dict):
                        entry = parsed
            except json.JSONDecodeError:
                self.logger.warning("打分缓存 JSON 解析失败: code=%s", code)
            except Exception as exc:  # pragma: no cover - 防御性
                self.logger.warning("打分缓存读取 Redis 失败: %s", exc)
        if entry:
            with self._lock:
                self._score_snapshot[snapshot_key] = dict(entry)
        return dict(entry)

    # ---- 选股结果 ----

    def save_select_stocks(
        self,
        user_strategy_id: str,
        day: str,
        codes: set[str],
    ) -> None:
        """保存某用户策略当日的选股结果。

        :param user_strategy_id: 用户策略 ID
        :param day: 日期 YYYY-MM-DD
        :param codes: 选中的股票代码集合
        """
        snapshot_key: tuple[str, str] = (user_strategy_id, day)
        with self._lock:
            self._select_snapshot[snapshot_key] = set(codes)
        if not self._redis_ready():
            return
        try:
            self._redis().set_key_string(
                self.select_key(user_strategy_id, day),
                json.dumps(sorted(codes)),
                ex=self.SELECT_CACHE_TTL,
            )
        except Exception as exc:  # pragma: no cover - 防御性
            self.logger.warning("选股结果缓存写入 Redis 失败: %s", exc)

    def get_select_stocks(self, user_strategy_id: str, day: str) -> set[str] | None:
        """读取某用户策略当日的选股结果。

        :param user_strategy_id: 用户策略 ID
        :param day: 日期 YYYY-MM-DD
        :return: 选股代码集合；**未命中返回 None**（调用方据此决定是否回源）
        """
        snapshot_key: tuple[str, str] = (user_strategy_id, day)
        with self._lock:
            cached: set[str] | None = self._select_snapshot.get(snapshot_key)
        if cached is not None:
            return set(cached)

        if not self._redis_ready():
            return None
        try:
            raw: str | None = self._redis().get_key_string(
                self.select_key(user_strategy_id, day)
            )
        except Exception as exc:  # pragma: no cover - 防御性
            self.logger.warning("选股结果缓存读取 Redis 失败: %s", exc)
            return None
        if not raw:
            return None
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            self.logger.warning("选股结果缓存 JSON 解析失败: user_strategy_id=%s", user_strategy_id)
            return None
        if not isinstance(parsed, list):
            return None
        codes: set[str] = {str(item) for item in parsed if item}
        with self._lock:
            self._select_snapshot[snapshot_key] = set(codes)
        return codes

    # ---- 区间最高价（日K口径） ----

    def get_highest_price(self, code: str, buy_date: str, day: str) -> float:
        """读取「买入基准日 → 当日」的日K最高价缓存。

        读取优先级：内存快照 → Redis（命中后回填快照）。**未命中返回 0.0**
        （日K最高价恒为正，0 可安全表示「没有缓存」）。

        :param code: 股票代码
        :param buy_date: 区间起始日 YYYY-MM-DD
        :param day: 当前交易日 YYYY-MM-DD
        :return: 缓存的区间最高价；未命中返回 0.0
        """
        if not code or not buy_date or not day:
            return 0.0
        snapshot_key: tuple[str, str, str] = (code, buy_date, day)
        with self._lock:
            cached: float | None = self._highest_price_snapshot.get(snapshot_key)
        if cached is not None:
            return float(cached)

        if not self._redis_ready():
            return 0.0
        try:
            raw: str | None = self._redis().get_key_string(
                self.highest_price_key(code, buy_date, day)
            )
        except Exception as exc:  # pragma: no cover - 防御性
            self.logger.warning("区间最高价缓存读取 Redis 失败: %s", exc)
            return 0.0
        if not raw:
            return 0.0
        try:
            value: float = float(raw)
        except (TypeError, ValueError):
            self.logger.warning("区间最高价缓存内容非法: code=%s, raw=%s", code, raw)
            return 0.0
        if value > 0:
            with self._lock:
                self._highest_price_snapshot[snapshot_key] = value
        return value

    def save_highest_price(
        self, code: str, buy_date: str, day: str, price: float
    ) -> None:
        """写入「买入基准日 → 当日」的日K最高价缓存（只增不减）。

        同一交易日内区间只会向右延伸，最高价单调不减，因此取 max 后写入；
        这样即使并发写入两个不同时点的查询结果，也不会把更高的值覆盖成更低的。

        :param code: 股票代码
        :param buy_date: 区间起始日 YYYY-MM-DD
        :param day: 当前交易日 YYYY-MM-DD
        :param price: 本次查询到的区间最高价（<=0 时忽略）
        """
        if not code or not buy_date or not day or price <= 0:
            return
        snapshot_key: tuple[str, str, str] = (code, buy_date, day)
        with self._lock:
            existing: float = float(self._highest_price_snapshot.get(snapshot_key, 0.0))
            if price <= existing:
                return
            self._highest_price_snapshot[snapshot_key] = float(price)

        if not self._redis_ready():
            return
        try:
            self._redis().set_key_string(
                self.highest_price_key(code, buy_date, day),
                str(float(price)),
                ex=self.HIGHEST_PRICE_CACHE_TTL,
            )
        except Exception as exc:  # pragma: no cover - 防御性
            self.logger.warning("区间最高价缓存写入 Redis 失败: %s", exc)

    # ---- 清理 ----

    def clear_minute_day(self, day: str) -> None:
        """清理某日全部行情与区间最高价内存快照（Redis 侧靠 TTL 自然过期）。

        :param day: 日期 YYYY-MM-DD
        """
        with self._lock:
            for key in [k for k in self._minute_snapshot if k[1] == day]:
                self._minute_snapshot.pop(key, None)
            if self._last_day == day:
                self._last_day = ""
            for hkey in [k for k in self._highest_price_snapshot if k[2] == day]:
                self._highest_price_snapshot.pop(hkey, None)

    def clear_user(self, user_strategy_id: str) -> None:
        """清理某用户策略的打分与选股内存快照。

        :param user_strategy_id: 用户策略 ID
        """
        if not user_strategy_id:
            return
        with self._lock:
            for key in [k for k in self._score_snapshot if k[0] == user_strategy_id]:
                self._score_snapshot.pop(key, None)
            for key in [k for k in self._select_snapshot if k[0] == user_strategy_id]:
                self._select_snapshot.pop(key, None)

    def clear_user_scores(self, user_strategy_id: str) -> None:
        """清理某用户策略的打分缓存（内存快照 + Redis key）。

        用于**参数热更新**。打分缓存 key 是
        `score:{usid}:{code}:{action}:{day}`，**不含参数**，所以同一天内改了
        策略参数后，旧参数算出的分仍会命中缓存 —— 表现为「参数改了但判定没变」，
        且只在部分股票上出现（命中缓存的那些）。

        `clear_user` 不删 Redis key 是有道理的：它的调用场景是盘前跨天清理，
        那时的 key 属于新的一天、本来就不存在。这里必须两边一起清。

        分钟行情与区间最高价**不动**：它们是行情数据，与参数无关，清掉等于让
        当天的量能/VWAP/回撤判定全部缺数据。

        :param user_strategy_id: 用户策略 ID
        """
        if not user_strategy_id:
            return
        with self._lock:
            stale: list[tuple[str, str, str, str]] = [
                k for k in self._score_snapshot if k[0] == user_strategy_id
            ]
            for key in stale:
                self._score_snapshot.pop(key, None)
        if not stale:
            return
        # Redis 删除放在锁外：网络调用不持锁（与 save_score / get_score 同一约定）
        if not self._redis_ready():
            return
        try:
            redis_exec: Any = self._redis()
            for _, code, action, day in stale:
                redis_exec.delete_key(
                    self.score_key(user_strategy_id, code, action, day)
                )
        except Exception as exc:
            self.logger.debug("清理打分 Redis key 失败: %s", exc)

    def reset_redis_failure(self) -> None:
        """重置 Redis 失败标记（新的一天重新尝试 Redis）。"""
        with self._lock:
            self._redis_failed = False

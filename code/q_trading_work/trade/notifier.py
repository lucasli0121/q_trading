"""
Author: liguoqiang
Date: 2026-08-12
Description: 交易通知推送公共类
    从 TradeManager 中抽取推送逻辑，供 TradeManager 和 StrategyWorkflow 等模块共用。
    支持企业微信 webhook 推送和微信好友消息推送。
"""

from __future__ import annotations

import json
import logging
from configparser import ConfigParser
import threading
from typing import Any
from urllib import request

# itchat 为可选依赖，未安装时微信好友消息静默跳过
try:
    import itchat

    HAS_ITCHAT = True
except ImportError:  # pragma: no cover
    itchat = None  # type: ignore[assignment]
    HAS_ITCHAT = False


class TradeNotifier:
    """交易通知推送器。

    负责通过企业微信 webhook 和/或微信好友发送交易通知。
    推送优先级：用户偏好 wx_push_url > 配置文件 enterprise_wechat_webhook_url。
    用户偏好中 enable_wx_push 为 False 时跳过企业微信推送。

    推送/下单星级门槛（2026-09-30）：用户偏好里的 `push_buy_min_star` /
    `push_sell_min_star` 决定「几星以上才推送」。未达门槛的信号由调用方
    （`StrategyWorkflow._save_trade_signal`）只写库，不发 MQTT、不推送、不下单。

    使用方式:
        notifier = TradeNotifier(api_client=admin_client)
        notifier.notify("买入信号: code=000001", recipient="策略名", user_strategy_id="us1")
    """

    # 用户偏好缓存 TTL（秒）。超过 TTL 后重新拉取，使前端修改推送配置能生效。
    PREFERENCES_TTL_SECONDS: float = 300.0

    # 用户偏好中的推送星级门槛字段（0 = 不限）
    PREF_KEY_BUY_MIN_STAR: str = "push_buy_min_star"
    PREF_KEY_SELL_MIN_STAR: str = "push_sell_min_star"

    # 交易方向。策略与信号记录里用的是中文动作名（不是 BUY/SELL），
    # 门槛按方向取不同字段，所以这里必须用同一套字面量。
    ACTION_BUY: str = "买入"
    ACTION_SELL: str = "卖出"

    def __init__(
        self,
        api_client: Any = None,
        config_path: str = "",
    ) -> None:
        """初始化推送器。

        :param api_client: ApiClient 实例，用于查询用户偏好（user_strategy → user_id → preferences）
        :param config_path: 配置文件路径，空字符串表示使用默认路径 cfg/stock.cfg
        """
        self.logger = logging.getLogger(__name__)
        from utils.tools import resource_path

        _cfg_path: str = config_path or resource_path("cfg/stock.cfg")
        self._config_path: str = _cfg_path
        self._api_client: Any = api_client
        self._cache_lock = threading.Lock()
        # P1-29：偏好缓存改为**实例属性** + TTL。
        # 原来 `_preferences_cache` 挂在类上，所有 TradeNotifier 实例共享同一份、
        # 且一旦写入永不过期 —— 用户在前端关掉/改掉推送地址后，
        # 进程重启前都不会生效；不同实例之间还会互相污染。
        # 结构：user_id -> (写入时间 monotonic, 偏好字典)
        self._preferences_cache: dict[str, tuple[float, dict[str, Any]]] = {}

        # 星级门槛缓存：user_strategy_id -> (写入时间, (买入门槛, 卖出门槛))。
        # 门槛判定发生在**每条信号**上（分钟级、逐股），而解析它要走
        # user_strategy → user_id → preference 两次 HTTP，不缓存的话每条信号
        # 都要多打两次接口。同样必须是实例属性（见上面 P1-29 的教训）。
        self._min_star_cache: dict[str, tuple[float, tuple[float, float]]] = {}
        # user_strategy_id -> (写入时间, user_id)。只缓存解析成功的结果。
        self._user_id_cache: dict[str, tuple[float, str]] = {}

        # 从配置文件读取 webhook 地址
        self._enterprise_wechat_webhook_url: str | None = self._load_webhook_url(
            "enterprise_wechat_webhook_url"
        )
        self._wechat_webhook_url: str | None = self._load_webhook_url(
            "wechat_webhook_url"
        )
        self._wechat_friend_names: list[str] = self._load_friend_names()

        # 股票名称缓存（code -> name）。推送文案里要显示「002050.SZ(三花智控)」，
        # 名称只能查一次，否则每条信号都多打一次 /api/stock_info/code。
        # 查询失败同样写空串（负缓存），避免停牌/退市代码反复触发请求。
        self._stock_name_cache: dict[str, str] = {}

        # 延迟导入，避免循环依赖（TradeNotifier 可能被 api 层引用）
        self._user_strategy_api: Any = None

    def _get_user_strategy_api(self) -> Any:
        """获取 UserStrategyApi 实例（懒加载）。"""
        if self._user_strategy_api is None and self._api_client is not None:
            from api.user_strategy import UserStrategyApi

            self._user_strategy_api = UserStrategyApi(self._api_client)
        return self._user_strategy_api

    # ---- 配置读取 ----

    def _load_webhook_url(self, option_name: str) -> str | None:
        """从配置文件读取 webhook 地址。

        :param option_name: 配置项名称
        :return: URL 字符串，未配置时返回 None
        """
        parser = ConfigParser()
        parser.read(self._config_path, encoding="utf-8")
        if parser.has_option("trade", option_name):
            value = parser.get("trade", option_name).strip()
            return value or None
        return None

    def _load_friend_names(self) -> list[str]:
        """从配置文件读取微信好友列表，支持逗号分隔或多行配置。

        :return: 好友名称列表
        """
        parser = ConfigParser()
        parser.read(self._config_path, encoding="utf-8")
        if not parser.has_option("trade", "wechat_friend_name"):
            return []

        raw_value = parser.get("trade", "wechat_friend_name").strip()
        if not raw_value:
            return []

        names: list[str] = []
        for part in raw_value.replace("\n", ",").split(","):
            name = part.strip()
            if name:
                names.append(name)
        return names

    # ---- 文案拼装 ----

    def _resolve_stock_name(self, code: str, name: str = "") -> str:
        """解析股票名称，用于推送文案中的「代码(名称)」段。

        优先级：调用方显式传入 > 实例缓存 > `/api/stock_info/code` 查询。
        查询失败返回空串（并做负缓存），此时文案退化为不带括号的纯代码。

        :param code: 股票代码，如 002050.SZ
        :param name: 调用方已知的名称（非空时直接采用，不再查询）
        :return: 股票名称，取不到时返回空串
        """
        if name:
            return str(name)
        if not code:
            return ""
        cached: str | None = self._stock_name_cache.get(code)
        if cached is not None:
            return cached

        resolved: str = ""
        try:
            from app_context import AppContext

            info_list: list[dict[str, Any]] = AppContext().stock_info_api.get_by_codes(code)
            if info_list:
                resolved = str(info_list[0].get("name", "") or "")
        except Exception as exc:
            # 未初始化 AppContext / 接口异常都不该影响推送本身：
            # 名称只是文案的修饰，取不到就不显示括号。
            self.logger.debug("查询股票名称失败 code=%s: %s", code, exc)
        self._stock_name_cache[code] = resolved
        return resolved

    @staticmethod
    def format_signal_message(
        action: str,
        code: str,
        price: float = 0.0,
        reason: str = "",
        star: float = 0.0,
        stock_name: str = "",
        profit_rate: float | None = None,
    ) -> str:
        """拼装交易信号推送文案。

        格式（2026-09-28 按用户确认的样例固定，分隔符沿用样例本身）：
            买入002050.SZ(三花智控)，买入价:35.42,买入条件：条件满足: ...,买入推荐: 3颗星

        缺失字段整段省略，不输出 `name=` / `price=0.00` 这类占位：
        - 名称取不到 → 只显示代码；
        - `price <= 0` → 不输出价格段；
        - `star <= 0` → 不输出推荐段（拿不到星级时不显示「0颗星」）；
        - `profit_rate is None` → 不输出收益率段（买入不适用）。

        :param action: 交易方向（买入/卖出）
        :param code: 股票代码
        :param price: 触发价
        :param reason: 触发原因（策略给出的明细，含各因子分值）
        :param star: 推荐星级（0~5，可带小数）
        :param stock_name: 股票名称，为空时不显示括号
        :param profit_rate: 卖出收益率（%），None 表示不适用
        :return: 推送正文
        """
        name_part: str = f"({stock_name})" if stock_name else ""
        message: str = f"{action}{code}{name_part}"
        if price > 0:
            message += f"，{action}价:{price:.2f}"
        if reason:
            message += f",{action}条件：{reason}"
        if star > 0:
            # `:g` 让整数星级显示为「3颗星」而不是「3.0颗星」，小数保留（3.5颗星）
            message += f"，{action}推荐: {star:g}颗星"
        if profit_rate is not None:
            message += f"，收益率:{profit_rate:.2f}%"
        return message

    @staticmethod
    def format_trade_result_message(
        action: str,
        code: str,
        price: float = 0.0,
        quantity: int = 0,
        status: str = "",
        stock_name: str = "",
        position_price: float | None = None,
        profit_amount: float | None = None,
        profit_rate: float | None = None,
        create_time: str = "",
    ) -> str:
        """拼装订单成交回执文案。

        格式（2026-09-28 确认，与信号文案同构）：
            买入000001.SZ(平安银行)，成交价:10.50,数量:100,状态:成功，时间:2026-09-28 10:00:00
            卖出000001.SZ(平安银行)，成交价:12.00,数量:100,持仓价:10.00,状态:成功，收益:200.00(20.00%)，时间:…

        与原格式的差异：加上股票名称；把「时间/股票/数量/成交价/状态」的标签式写法
        改为「动作+代码(名称)，成交价:…」；收益率由 `:.4%`（20.0000%）改为
        `:.2%`（20.00%）。

        :param action: 交易方向（买入/卖出）
        :param code: 股票代码
        :param price: 成交价
        :param quantity: 成交数量（股）
        :param status: 订单状态
        :param stock_name: 股票名称，空串时不显示括号
        :param position_price: 持仓成本价，None 或 <= 0 时不显示（买入不适用）
        :param profit_amount: 收益金额，与 profit_rate 一起给才显示收益段
        :param profit_rate: 收益率（小数比率，0.2 → 20.00%）
        :param create_time: 订单创建时间，空串时不显示
        :return: 推送正文
        """
        name_part: str = f"({stock_name})" if stock_name else ""
        message: str = f"{action}{code}{name_part}"
        if price > 0:
            message += f"，成交价:{price:.2f}"
        if quantity > 0:
            message += f",数量:{quantity}"
        if position_price is not None and position_price > 0:
            message += f",持仓价:{position_price:.2f}"
        if status:
            message += f",状态:{status}"
        if profit_amount is not None and profit_rate is not None:
            message += f"，收益:{profit_amount:.2f}({profit_rate:.2%})"
        if create_time:
            message += f"，时间:{create_time}"
        return message

    # ---- 发送渠道 ----

    def _send_http_text(self, url: str, message: str, recipient: str | None) -> bool:
        """通过 HTTP webhook 发送文本通知（企业微信格式）。

        :param url: webhook URL
        :param message: 消息内容
        :param recipient: 策略名称等上下文
        :return: 发送是否成功
        """
        payload = {
            "msgtype": "text",
            "text": {
                "content": f"{message}\nrecipient={recipient or 'default'}",
            },
        }
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as _:
                return True
        except Exception as err:  # pragma: no cover - defensive path
            self.logger.exception("failed to send notification to %s: %s", url, err)
            return False

    def _send_to_wechat_friend(
        self, friend_name: str, message: str, recipient: str | None
    ) -> None:
        """向指定微信好友发送消息。

        :param friend_name: 好友备注或昵称
        :param message: 消息内容
        :param recipient: 策略名称等上下文
        """
        full_msg = f"{message}\nrecipient={recipient or 'default'}"
        if not HAS_ITCHAT:
            self.logger.info(
                "itchat not installed, skip wechat friend push to %s", friend_name
            )
            return
        try:
            if itchat is not None:
                friends = itchat.search_friends(name=friend_name)
                if not friends:
                    self.logger.warning(
                        "wechat friend not found: %s", friend_name
                    )
                    return
                friend = friends[0]
                friend.send(full_msg)
                self.logger.info("wechat message sent to %s", friend_name)
        except Exception as err:
            self.logger.exception(
                "failed to send wechat message to %s: %s", friend_name, err
            )

    # ---- 用户偏好 ----

    def _resolve_wx_push_url(self, user_strategy_id: str) -> str | None:
        """根据用户偏好解析企业微信推送 URL。

        1. 查询用户偏好中的 enable_wx_push，关闭则返回 None
        2. 开启时优先使用 wx_push_url，为空则回退到配置文件的 webhook URL

        :param user_strategy_id: 用户策略关联 ID
        :return: 推送 URL，无需推送时返回 None
        """
        if user_strategy_id:
            try:
                us_api = self._get_user_strategy_api()
                if us_api is not None:
                    us_data: dict[str, Any] = us_api.get(user_strategy_id)
                    user_id: str = us_data.get("user_id", "")
                    self.logger.debug(f"resolve_wx_push_url, user_strategy_id:{user_strategy_id}, user_id:{user_id}")
                    if user_id:
                        prefs: dict[str, Any] = self._load_user_preferences(user_id)
                        self.logger.debug(f"获取用户偏好数据:{prefs}")
                        enable_wx: bool = False
                        wx_url: str = ""
                        if prefs:
                            enable_val: Any = prefs.get("enable_wx_push", False)
                            if isinstance(enable_val, bool):
                                enable_wx = enable_val
                            elif isinstance(enable_val, str):
                                enable_wx = enable_val.lower() in ("true", "1", "yes")
                            wx_url = str(prefs.get("wx_push_url", "") or "")
                        if not enable_wx:
                            self.logger.debug("用户偏好中企业微信推送已关闭")
                            return None
                        if wx_url:
                            self.logger.debug(f"使用用户偏好 wx_push_url 推送，地址:{wx_url}")
                            return wx_url
            except Exception as e:
                self.logger.error("查询用户偏好失败，回退配置文件: %s", e)

        # 回退到配置文件中的 URL
        return self._enterprise_wechat_webhook_url

    def _load_user_preferences(self, user_id: str) -> dict[str, Any]:
        """加载指定用户的偏好设置（实例级缓存 + TTL）。

        P1-29 / P2-24：
        - 缓存条目带写入时间，超过 `PREFERENCES_TTL_SECONDS`（默认 300s）重新拉取，
          使前端修改推送配置后能自动生效，无需重启进程。
        - **HTTP 调用在锁外执行**，锁内只做缓存读取与写回；原实现把
          `PreferenceApi.get_by_user_id` 放在 `with self._cache_lock` 内，
          一个慢请求会阻塞所有用户的通知推送。
        - 空结果同样缓存（负缓存），避免偏好为空的用户每次推送都打一次接口。

        :param user_id: 用户 ID
        :return: 偏好字典（未获取到返回空字典）
        """
        import time

        now: float = time.monotonic()
        # 1) 锁内只读缓存
        with self._cache_lock:
            entry: tuple[float, dict[str, Any]] | None = self._preferences_cache.get(
                user_id
            )
        if entry is not None and (now - entry[0]) < self.PREFERENCES_TTL_SECONDS:
            return dict(entry[1])

        if self._api_client is None:
            return {}

        # 2) 锁外做网络 IO
        prefs: dict[str, Any] = {}
        try:
            from api.preference import PreferenceApi

            loaded: dict[str, Any] = PreferenceApi(
                self._api_client
            ).get_by_user_id(user_id)
            self.logger.debug("获取用户偏好结果:%s", loaded)
            if loaded:
                prefs = loaded
        except Exception as e:
            self.logger.error("获取用户微信推送配置出错:%s", e)

        # 3) 锁内写回
        with self._cache_lock:
            self._preferences_cache[user_id] = (now, prefs)
        return dict(prefs)

    # ---- 推送星级门槛 ----

    @staticmethod
    def parse_min_star(value: Any) -> float:
        """把偏好里的最小星级解析成 float，非法值一律归到 0。

        0 是「不限」的哨兵值（与服务端信号列表 `min_star` 的约定一致），
        所以取不到值、非数值、NaN、负数都返回 0.0，而不是抛错或当成负门槛。

        :param value: 偏好里的原始值（可能是 float/int/str/None）
        :return: 非负门槛值，0 表示不限
        """
        try:
            star: float = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        if star != star:  # NaN：任何比较都不成立，必须显式挡掉
            return 0.0
        return star if star > 0 else 0.0

    @staticmethod
    def star_passed(star: float, min_star: float) -> bool:
        """星级是否达到门槛（门槛 <= 0 表示不限）。

        比较口径与服务端信号列表的 `min_star` 一致——**含边界**（`$gte`）：
        门槛设 3 时，3 星算达标。注意 star 取不到值（0）时按不达标处理，
        门槛 > 0 而信号没有星级，说明上游没打星，不能当成高星放过去。

        :param star: 信号星级（0~5）
        :param min_star: 最小星级门槛（0 = 不限）
        :return: True 表示放行
        """
        limit: float = TradeNotifier.parse_min_star(min_star)
        if limit <= 0:
            return True
        try:
            current: float = float(star)
        except (TypeError, ValueError):
            return False
        if current != current:  # NaN
            return False
        return current >= limit

    def _resolve_user_id(self, user_strategy_id: str) -> str:
        """由用户策略 ID 解析所属用户 ID（实例级缓存 + TTL）。

        只缓存**解析成功**的结果：查不到时下次信号重试，否则一次网络抖动
        会让门槛（连带下单判定的那部分）静默失效一整个 TTL。

        :param user_strategy_id: 用户策略关联 ID
        :return: 用户 ID，取不到返回空串
        """
        if not user_strategy_id:
            return ""
        import time

        now: float = time.monotonic()
        with self._cache_lock:
            entry: tuple[float, str] | None = self._user_id_cache.get(user_strategy_id)
        if entry is not None and (now - entry[0]) < self.PREFERENCES_TTL_SECONDS:
            return entry[1]

        user_id: str = ""
        try:
            us_api = self._get_user_strategy_api()
            if us_api is not None:
                us_data: dict[str, Any] = us_api.get(user_strategy_id)
                if isinstance(us_data, dict):
                    user_id = str(us_data.get("user_id", "") or "")
        except Exception as exc:
            self.logger.warning(
                "查询用户策略 %s 所属用户失败，本轮按不限制门槛处理: %s",
                user_strategy_id, exc,
            )
        if user_id:
            with self._cache_lock:
                self._user_id_cache[user_strategy_id] = (now, user_id)
        return user_id

    def _resolve_min_stars(self, user_strategy_id: str) -> tuple[float, float]:
        """解析该用户策略的 (买入门槛, 卖出门槛)。

        失败兜底是 **(0.0, 0.0) = 不限**：门槛同时决定推送与下单，读不到偏好时
        按「不限」等于回到改动前的行为；若按「拦全部」兜底，一次接口异常就会
        让整个实例停止下单。

        :param user_strategy_id: 用户策略关联 ID
        :return: (push_buy_min_star, push_sell_min_star)
        """
        if not user_strategy_id:
            return 0.0, 0.0
        import time

        now: float = time.monotonic()
        with self._cache_lock:
            entry: tuple[float, tuple[float, float]] | None = self._min_star_cache.get(
                user_strategy_id
            )
        if entry is not None and (now - entry[0]) < self.PREFERENCES_TTL_SECONDS:
            return entry[1]

        user_id: str = self._resolve_user_id(user_strategy_id)
        if not user_id:
            # 解析不到所属用户 → 不写缓存，下次信号重试（见 _resolve_user_id）
            return 0.0, 0.0

        prefs: dict[str, Any] = self._load_user_preferences(user_id)
        pair: tuple[float, float] = (
            self.parse_min_star(prefs.get(self.PREF_KEY_BUY_MIN_STAR)),
            self.parse_min_star(prefs.get(self.PREF_KEY_SELL_MIN_STAR)),
        )
        with self._cache_lock:
            self._min_star_cache[user_strategy_id] = (now, pair)
        if pair != (0.0, 0.0):
            self.logger.debug(
                "用户策略 %s 推送星级门槛: 买入>=%.1f, 卖出>=%.1f",
                user_strategy_id, pair[0], pair[1],
            )
        return pair

    def get_push_min_star(self, user_strategy_id: str, action: str) -> float:
        """取该用户策略在指定方向上的最小推送星级（0 = 不限）。

        :param user_strategy_id: 用户策略关联 ID
        :param action: 交易方向（买入/卖出，与信号记录里的 action 同值）
        :return: 最小星级；未配置或取不到时返回 0.0（不限）
        """
        buy_star, sell_star = self._resolve_min_stars(user_strategy_id)
        if str(action) == self.ACTION_SELL:
            return sell_star
        if str(action) == self.ACTION_BUY:
            return buy_star
        # 方向未知：按不限处理。策略只会给出买入/卖出两种 action，
        # 落到这里说明上游数据异常 —— 此时拦下单的代价远大于放过。
        return 0.0

    def invalidate_preferences_cache(self) -> None:
        """清空用户偏好与星级门槛缓存，让界面上刚改的门槛立即重新拉取。

        偏好本身仍是 300 秒 TTL，只是把「下一次推送/下单要重新拉一次」提前。
        `StrategyWorkflow` 的配置对账（60 秒一轮）会调用它，把界面上改门槛的
        生效时间从最长 5 分钟缩到最长 1 分钟。
        """
        with self._cache_lock:
            self._preferences_cache.clear()
            self._user_id_cache.clear()
            self._min_star_cache.clear()

    # ---- 公开接口 ----

    def notify(
        self,
        message: str = "",
        recipient: str | None = None,
        user_strategy_id: str = "",
    ) -> None:
        """向接收者发送通知。只负责发送，不做文案加工。

        企业微信推送优先级：用户偏好 wx_push_url > 配置文件 enterprise_wechat_webhook_url。
        用户偏好中 enable_wx_push 为 False 时跳过企业微信推送。

        文案由调用方给出：交易信号走 `notify_signal()`、成交回执走
        `notify_trade_result()`；需要自定义文案的调用方直接传 `message`。

        :param message: 通知正文
        :param recipient: **用户策略名称**（同一模板下用户可以建多条策略，模板名
            区分不开，见 `notify_signal` 的说明）
        :param user_strategy_id: 用户策略关联 ID，用于查询用户偏好
        """
        if not message:
            self.logger.warning(
                "notify 内容为空，跳过推送 recipient=%s", recipient or "default"
            )
            return

        self.logger.info(
            "trade notify user_strategy_id: %s to %s: %s", user_strategy_id, recipient or "default", message
        )

        # 解析企业微信推送 URL：优先用户偏好，否则使用配置文件
        wx_push_url: str | None = self._resolve_wx_push_url(user_strategy_id)

        if wx_push_url:
            self._send_http_text(wx_push_url, message, recipient)

        if self._wechat_friend_names:
            for friend_name in self._wechat_friend_names:
                self._send_to_wechat_friend(friend_name, message, recipient)

    def notify_signal(
        self,
        action: str,
        code: str,
        price: float = 0.0,
        reason: str = "",
        star: float = 0.0,
        profit_rate: float | None = None,
        stock_name: str = "",
        recipient: str | None = None,
        user_strategy_id: str = "",
        min_star: float | None = None,
    ) -> bool:
        """推送交易信号（文案格式见 `format_signal_message`）。

        信号推送在分钟级链路上同股反复触发，股票名称走 `_resolve_stock_name`
        的缓存，不会每条都打接口。

        **星级门槛**：低于用户偏好门槛的信号直接跳过（不发送），返回 False。
        调用方若已在别处解析过门槛，传 `min_star` 可省掉一次解析与两次 HTTP。

        :param action: 交易方向（买入/卖出）
        :param code: 股票代码
        :param price: 触发价
        :param reason: 触发原因
        :param star: 推荐星级（0~5），0 表示无星级信息、不显示该段
        :param profit_rate: 卖出收益率（%），买入传 None 表示不适用
        :param stock_name: 股票名称，留空时按代码查询（结果缓存）
        :param recipient: 用户策略名称
        :param user_strategy_id: 用户策略关联 ID
        :param min_star: 最小星级门槛，None 表示按用户偏好解析（0 = 不限）
        :return: 是否真的发出了推送
        """
        limit: float = (
            self.get_push_min_star(user_strategy_id, action)
            if min_star is None
            else min_star
        )
        if not self.star_passed(star, limit):
            self.logger.info(
                "星级未达推送门槛，跳过信号推送: action=%s, code=%s, star=%s, "
                "min_star=%s, user_strategy_id=%s",
                action, code, star, limit, user_strategy_id,
            )
            return False

        self.notify(
            self.format_signal_message(
                action=action,
                code=code,
                price=price,
                reason=reason,
                star=star,
                stock_name=self._resolve_stock_name(code, stock_name),
                profit_rate=profit_rate,
            ),
            recipient=recipient,
            user_strategy_id=user_strategy_id,
        )
        return True

    def notify_trade_result(
        self,
        action: str,
        code: str,
        price: float = 0.0,
        quantity: int = 0,
        status: str = "",
        position_price: float | None = None,
        profit_amount: float | None = None,
        profit_rate: float | None = None,
        create_time: str = "",
        stock_name: str = "",
        recipient: str | None = None,
        user_strategy_id: str = "",
    ) -> None:
        """推送订单成交回执（文案格式见 `format_trade_result_message`）。

        买卖共用一条路径，差异只在「持仓价 / 收益」两组字段的有无：买入传 None。

        :param action: 交易方向（买入/卖出）
        :param code: 股票代码
        :param price: 成交价
        :param quantity: 成交数量（股）
        :param status: 订单状态（成功/失败等）
        :param position_price: 持仓成本价，买入传 None
        :param profit_amount: 收益金额，买入传 None
        :param profit_rate: 收益率（小数比率），买入传 None
        :param create_time: 订单创建时间
        :param stock_name: 股票名称，留空时按代码查询（结果缓存）
        :param recipient: 用户策略名称
        :param user_strategy_id: 用户策略关联 ID
        """
        self.notify(
            self.format_trade_result_message(
                action=action,
                code=code,
                price=price,
                quantity=quantity,
                status=status,
                stock_name=self._resolve_stock_name(code, stock_name),
                position_price=position_price,
                profit_amount=profit_amount,
                profit_rate=profit_rate,
                create_time=create_time,
            ),
            recipient=recipient,
            user_strategy_id=user_strategy_id,
        )

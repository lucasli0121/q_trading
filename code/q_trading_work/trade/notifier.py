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

    使用方式:
        notifier = TradeNotifier(api_client=admin_client)
        notifier.notify("买入信号: code=000001", recipient="策略名", user_strategy_id="us1")
    """

    # 用户偏好缓存 TTL（秒）。超过 TTL 后重新拉取，使前端修改推送配置能生效。
    PREFERENCES_TTL_SECONDS: float = 300.0

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

    # ---- 公开接口 ----

    def notify(
        self,
        message: str = "",
        recipient: str | None = None,
        user_strategy_id: str = "",
        *,
        action: str = "",
        stock_code: str = "",
        stock_name: str = "",
        price: float = 0.0,
        reason: str = "",
        star: float = 0.0,
        profit_rate: float | None = None,
    ) -> None:
        """向接收者发送通知。

        企业微信推送优先级：用户偏好 wx_push_url > 配置文件 enterprise_wechat_webhook_url。
        用户偏好中 enable_wx_push 为 False 时跳过企业微信推送。

        **文案（2026-09-28）**：交易信号类通知改为按结构化字段重排，`message`
        仅在未传结构化字段时原样推送（成交通知等场景仍走旧路径）。判定条件是
        `action` 与 `stock_code` 同时非空；重排格式见 `format_signal_message`。

        :param message: 通知内容（未传结构化字段时原样推送）
        :param recipient: 策略名称等上下文
        :param user_strategy_id: 用户策略关联 ID，用于查询用户偏好
        :param action: 交易方向（买入/卖出），传入即启用信号文案重排
        :param stock_code: 股票代码，传入即启用信号文案重排
        :param stock_name: 股票名称，留空时按代码查询（结果缓存）
        :param price: 触发价，仅用于信号文案
        :param reason: 触发原因，仅用于信号文案
        :param star: 推荐星级（0~5），0 表示无星级信息，不显示
        :param profit_rate: 卖出收益率（%），None 表示不适用
        """
        if action and stock_code:
            message = self.format_signal_message(
                action=action,
                code=stock_code,
                price=price,
                reason=reason,
                star=star,
                stock_name=self._resolve_stock_name(stock_code, stock_name),
                profit_rate=profit_rate,
            )

        if not message:
            # 结构化字段与 message 都没给，推一条空消息没有意义
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

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

    # 用户偏好缓存（user_id → preferences dict），避免重复 API 调用
    _preferences_cache: dict[str, dict[str, Any]] = {}

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

        # 从配置文件读取 webhook 地址
        self._enterprise_wechat_webhook_url: str | None = self._load_webhook_url(
            "enterprise_wechat_webhook_url"
        )
        self._wechat_webhook_url: str | None = self._load_webhook_url(
            "wechat_webhook_url"
        )
        self._wechat_friend_names: list[str] = self._load_friend_names()

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
                    if user_id:
                        prefs: dict[str, Any] = self._load_user_preferences(user_id)
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
                            self.logger.debug("使用用户偏好 wx_push_url 推送")
                            return wx_url
            except Exception as e:
                self.logger.debug("查询用户偏好失败，回退配置文件: %s", e)

        # 回退到配置文件中的 URL
        return self._enterprise_wechat_webhook_url

    def _load_user_preferences(self, user_id: str) -> dict[str, Any]:
        """加载用户偏好设置（带缓存）。

        :param user_id: 用户 ID
        :return: 偏好字典
        """
        if user_id in self._preferences_cache:
            return self._preferences_cache[user_id]
        if self._api_client is None:
            self._preferences_cache[user_id] = {}
            return {}
        try:
            result: Any = self._api_client.get(
                "/api/user/preference",
                params={"user_id": user_id},
            )
            if result and isinstance(result, dict):
                self._preferences_cache[user_id] = dict(result)
                return self._preferences_cache[user_id]
        except Exception:
            pass
        self._preferences_cache[user_id] = {}
        return {}

    # ---- 公开接口 ----

    def notify(
        self,
        message: str,
        recipient: str | None = None,
        user_strategy_id: str = "",
    ) -> None:
        """向接收者发送通知。

        企业微信推送优先级：用户偏好 wx_push_url > 配置文件 enterprise_wechat_webhook_url。
        用户偏好中 enable_wx_push 为 False 时跳过企业微信推送。

        :param message: 通知内容
        :param recipient: 策略名称等上下文
        :param user_strategy_id: 用户策略关联 ID，用于查询用户偏好
        """
        self.logger.info(
            "trade notify to %s: %s", recipient or "default", message
        )

        # 解析企业微信推送 URL：优先用户偏好，否则使用配置文件
        wx_push_url: str | None = self._resolve_wx_push_url(user_strategy_id)

        if wx_push_url:
            self._send_http_text(wx_push_url, message, recipient)

        if self._wechat_friend_names:
            for friend_name in self._wechat_friend_names:
                self._send_to_wechat_friend(friend_name, message, recipient)

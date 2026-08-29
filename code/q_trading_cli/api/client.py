#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-27
Description: 统一 API 客户端 — 基于 httpx 的 HTTP 客户端，
             提供 token 认证、统一响应解析和错误处理。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from api.config import ApiConfig


class ApiError(Exception):
    """API 业务错误 — 当后端返回 code != 0 时抛出。

    Attributes:
        code: 后端返回的错误码
        message: 后端返回的错误信息
    """

    def __init__(self, code: int, message: str) -> None:
        self.code: int = code
        self.message: str = message
        super().__init__(f"API Error [{code}]: {message}")


class UnauthorizedError(Exception):
    """401 未授权异常 — token 过期或未登录时抛出。

    调用方捕获此异常后应停止当前操作，登录对话框已自动弹出。
    """

    def __init__(self, message: str = "登录已过期，请重新登录") -> None:
        self.message: str = message
        super().__init__(message)


class ApiClient:
    """统一的 HTTP API 客户端。

    封装了 httpx.Client，提供：
    - 自动拼接 base_url
    - token 认证（Bearer）
    - 统一响应解析（ApiResponse 解包）
    - 请求重试和超时

    使用方式:
        client = ApiClient()
        client.set_token("xxx")           # 登录后设置 token
        data = client.get("/api/pool/list")  # 自动携带 Authorization header
        client.clear_token()              # 退出登录
    """

    # 默认超时（秒）
    DEFAULT_TIMEOUT: float = 30.0

    def __init__(self, config: ApiConfig | None = None) -> None:
        """初始化 API 客户端。

        :param config: API 配置，为 None 时自动从 cfg/stock.cfg 读取
        """
        self.logger = logging.getLogger(__name__)
        self._config: ApiConfig = config if config is not None else ApiConfig()
        self._token: str | None = None
        self._client: httpx.Client = httpx.Client(
            base_url=self._config.base_url,
            timeout=self.DEFAULT_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )

    # ---- token 管理 ----

    def set_token(self, token: str) -> None:
        """设置认证 token（非 Web 场景使用，如测试/脚本）。

        Web 场景下 token 应存储在 app.storage.user["token"] 中，
        _build_headers 会自动从会话中读取，避免多用户 token 冲突。

        :param token: JWT token 字符串
        """
        self._token = token

    def clear_token(self) -> None:
        """清除认证 token。"""
        self._token = None

    @property
    def is_authenticated(self) -> bool:
        """是否已设置 token。"""
        return self._token is not None or self._resolve_token() is not None

    @staticmethod
    def _resolve_token() -> str | None:
        """从当前用户会话中解析 token，会话不可用时返回 None。

        Web 场景下 token 按用户会话隔离存储，避免不同浏览器登录
        共用同一个 ApiClient 实例导致的 token 覆盖问题。
        """
        try:
            from nicegui import app
            token: str | None = app.storage.user.get("token", "")
            return token if token else None
        except Exception:  # noqa: BLE001
            return None

    def set_fallback_token(self, token: str) -> None:
        """设置后台线程的 fallback token。

        当 _resolve_token() 从 Web 会话中获取不到 token 时（如 MQTT 回调线程），
        使用此 token 作为后备，确保后台任务也能携带认证信息。

        :param token: JWT token 字符串
        """
        self._fallback_token: str = token

    def clear_fallback_token(self) -> None:
        """清除 fallback token。"""
        self._fallback_token = ""

    def _get_effective_token(self) -> str | None:
        """获取有效 token：优先 Web 会话，其次 fallback，最后实例 token。"""
        token: str | None = self._resolve_token()
        if token:
            return token
        token = getattr(self, "_fallback_token", "")
        if token:
            return token
        return self._token

    # ---- HTTP 方法 ----

    def _build_headers(self) -> dict[str, str]:
        """构建请求头，优先从用户会话读取 token，其次 fallback，最后实例 token。

        每次调用创建新的 headers dict，确保多用户并发请求
        各自携带正确的 Authorization header，无竞态条件。
        """
        headers: dict[str, str] = {}
        token: str | None = self._get_effective_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _parse_response(self, resp_json: dict[str, Any]) -> Any:
        """解析统一 ApiResponse 格式，返回 data 字段或抛出 ApiError。

        :param resp_json: 后端返回的 JSON 字典
        :return: data 字段的内容
        :raises ApiError: code != 0 时抛出
        """
        code: int = resp_json.get("code", -1)
        message: str = resp_json.get("message", "unknown error")
        if code != 0:
            raise ApiError(code, message)
        return resp_json.get("data")

    def _handle_response(self, resp: httpx.Response) -> Any:
        """处理 HTTP 响应，自动拦截 401 并触发登录对话框。

        先调用 raise_for_status() 检查 HTTP 状态码：
        - 401 → 清除认证状态、弹出登录对话框，然后抛出 UnauthorizedError
        - 其他 4xx/5xx → 抛出 httpx.HTTPStatusError（由调用方自行处理）

        正常响应则解析 JSON 并返回 data 字段。

        :param resp: httpx 响应对象
        :return: 解析后的 data 字段
        :raises UnauthorizedError: 401 未授权
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: 其他 HTTP 错误
        """
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # 从响应体中提取错误详情（如 "账号或密码错误"）
                detail: str = ""
                try:
                    body: dict = e.response.json()
                    detail = body.get("detail", "")
                except Exception:  # noqa: BLE001, S110
                    pass
                # 仅当请求确实携带过 token（会话已认证）时才走“会话过期”流程；
                # 登录/注册等匿名请求的 401 直接抛出，由调用方提示“账号或密码错误”。
                if self._get_effective_token() is not None:
                    self._on_401()
                raise UnauthorizedError(detail or "登录已过期，请重新登录") from e
            raise
        return self._parse_response(resp.json())

    def _on_401(self) -> None:
        """401 响应时的处理：清除认证状态，提示用户并引导至登录页。"""
        self.clear_token()
        from nicegui import app

        # 清除用户会话中的认证信息
        try:
            if app.storage.user.get("authenticated"):
                app.storage.user.update({
                    "authenticated": False,
                    "token": "",
                    "username": "",
                })
        except Exception:
            self.logger.debug("清除用户会话失败", exc_info=True)

        # 委托共享模块弹出确认框 → 跳转 /login
        from api.error_handler import show_relogin_confirm

        show_relogin_confirm()


    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """发送 GET 请求。

        :param path: API 路径，如 /api/pool/list
        :param params: 查询参数字典
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("GET %s params=%s", path, params)
        resp = self._client.get(path, params=params, headers=self._build_headers())
        return self._handle_response(resp)

    def post(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """发送 POST 请求。

        :param path: API 路径
        :param json_data: 请求体 JSON 字典
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("POST %s body=%s", path, json_data)
        resp = self._client.post(path, json=json_data, headers=self._build_headers())
        return self._handle_response(resp)

    def put(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """发送 PUT 请求。

        :param path: API 路径
        :param json_data: 请求体 JSON 字典
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("PUT %s body=%s", path, json_data)
        resp = self._client.put(path, json=json_data, headers=self._build_headers())
        return self._handle_response(resp)

    def patch(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """发送 PATCH 请求。

        :param path: API 路径
        :param json_data: 请求体 JSON 字典
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("PATCH %s body=%s", path, json_data)
        resp = self._client.patch(path, json=json_data, headers=self._build_headers())
        return self._handle_response(resp)

    def delete(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """发送 DELETE 请求。

        :param path: API 路径
        :param json_data: 可选的请求体 JSON 字典
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("DELETE %s body=%s", path, json_data)
        resp = self._client.request(
            "DELETE", path, json=json_data, headers=self._build_headers()
        )
        return self._handle_response(resp)

    def close(self) -> None:
        """关闭底层 HTTP 客户端，释放连接资源。"""
        self._client.close()

    def __enter__(self) -> ApiClient:  # noqa: PYI034
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

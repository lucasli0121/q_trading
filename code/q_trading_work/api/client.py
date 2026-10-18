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


def is_not_found(exc: BaseException) -> bool:
    """判断异常是否为 HTTP 404。

    服务端在部分接口上用 404 表达「该资源尚无数据」（例如某用户策略还没有任何
    执行记录、某股票还没有任何交易信号），这与「请求出错」是两种不同语义。
    这类接口的包装方法（`UserStrategyApi.get_latest_execution`、
    `TradeSignalApi.latest`）用本函数把 404 与真实故障区分开：
    前者收敛为空数据，后者照旧上抛交给调用方 fail-closed。

    :param exc: 待判定的异常
    :return: True 表示该异常是 HTTP 404
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return exc.response is not None and exc.response.status_code == 404


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

    def close(self) -> None:
        """关闭底层连接池。

        常驻服务一般不需要显式调用。短生命周期的脚本（对照、批处理）应当用完
        即关，否则连接会累积。

        需要留意连接复用本身：2026-10-16 实测本项目的服务端对**同一 TCP 连接
        上的第 2 个请求**稳定返回 404（`{"detail":"Not Found"}`，响应体 22 字节），
        而每次新建连接则稳定 200。凡是会遇到这种情况的场景（对照脚本一类），
        应每次新建客户端并在用完后 close。
        """
        self._client.close()

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
        """解析 token（非 Web 场景始终返回 None）。

        保留此方法以兼容现有调用链，后台服务不使用 Web 会话。
        """
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

    def _handle_response(
        self, resp: httpx.Response, *, allow_not_found: bool = False
    ) -> Any:
        """处理 HTTP 响应，自动拦截 401 并触发登录对话框。

        先调用 raise_for_status() 检查 HTTP 状态码：
        - 401 → 清除认证状态、弹出登录对话框，然后抛出 UnauthorizedError
        - 404 且 allow_not_found=True → 仅记 DEBUG 后抛出（调用方已声明「资源不存在」
          是正常业务状态，例如查询尚未产生数据的「最新执行结果」）
        - 其他 4xx/5xx → 记 ERROR（含响应体）后抛出 httpx.HTTPStatusError

        正常响应则解析 JSON 并返回 data 字段。

        :param resp: httpx 响应对象
        :param allow_not_found: 是否把 404 视为可接受的正常状态（仅影响日志级别，
            异常照旧抛出，仍由调用方决定如何兜底）
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
                except Exception:
                    pass
                # 仅当请求确实携带过 token（会话已认证）时才走“会话过期”流程；
                # 登录/注册等匿名请求的 401 直接抛出，由调用方提示“账号或密码错误”。
                if self._get_effective_token() is not None:
                    self._on_401()
                raise UnauthorizedError(detail or "登录已过期，请重新登录") from e
            # 其余 4xx/5xx：把响应体写进日志。
            # 否则上层只能看到 "422 Unprocessable Entity" 这类状态行，
            # 完全不知道是哪个字段不合格（2026-09-15 线上排查 trade_signal 422
            # 时就卡在这里 —— FastAPI 的校验详情其实就在响应体里）。
            if allow_not_found and e.response.status_code == 404:
                # 「查无此资源」在部分接口上是正常业务状态（如某用户策略尚未产生
                # 任何执行记录），调用方已显式声明可接受，因此降为 DEBUG，
                # 避免把良性 404 记进 errors.log 造成误报
                # （2026-09-15 部署设备实测：execution/latest 的 404 就是这种）。
                self.logger.debug(
                    "HTTP 404 %s（调用方声明为可接受）", self._request_url(e)
                )
            else:
                self._log_error_body(e)
            raise
        return self._parse_response(resp.json())

    @staticmethod
    def _request_url(exc: httpx.HTTPStatusError) -> str:
        """从异常中安全提取请求 URL，供日志使用。

        :param exc: httpx 抛出的状态码异常
        :return: 请求 URL，取不到时返回空字符串
        """
        try:
            if exc.request is not None:
                return str(exc.request.url)
        except Exception:  # pragma: no cover - 防御性
            pass
        return ""

    def _log_error_body(self, exc: httpx.HTTPStatusError) -> None:
        """记录 HTTP 错误响应体（截断），便于定位 4xx/5xx 的具体原因。

        :param exc: httpx 抛出的状态码异常
        """
        try:
            body_text: str = exc.response.text or ""
        except Exception:  # pragma: no cover - 防御性
            body_text = ""
        self.logger.error(
            "HTTP %s %s 响应体: %s",
            exc.response.status_code,
            self._request_url(exc),
            body_text.strip()[:500] or "<empty>",
        )

    def _on_401(self) -> None:
        """401 响应时的处理：清除认证 token 并记录日志。

        后台服务场景下无 UI 交互，仅清除 token 并记录警告日志。
        外部可通过 api.error_handler.on_401() 注册自定义回调。
        """
        self.clear_token()
        self.logger.warning("API 请求返回 401，token 已清除")

        # 触发外部注册的 401 回调
        try:
            from api.error_handler import _on_401_callbacks
            for cb in _on_401_callbacks:
                try:
                    cb()
                except Exception:
                    self.logger.debug("401 回调执行失败", exc_info=True)
        except Exception:
            pass


    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        """发送 GET 请求。

        :param path: API 路径，如 /api/pool/list
        :param params: 查询参数字典
        :param allow_not_found: 是否把 404 视为可接受的正常状态（仅降日志级别，
            异常仍会抛出）。适用于「查询单个资源可能不存在」的接口。
        :return: 解析后的 data 字段
        :raises ApiError: 业务错误
        :raises httpx.HTTPStatusError: HTTP 错误
        """
        self.logger.debug("GET %s params=%s", path, params)
        resp = self._client.get(path, params=params, headers=self._build_headers())
        return self._handle_response(resp, allow_not_found=allow_not_found)

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

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

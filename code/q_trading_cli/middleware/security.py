"""
安全中间件：拦截扫描器请求、限流、安全响应头
Author: liguoqiang
Date: 2026-07-20
"""

import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

from core.routes import ALL_ROUTES

# 已知扫描器/漏洞探针路径模式
SUSPICIOUS_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.env", re.IGNORECASE),
    re.compile(r"\.git/", re.IGNORECASE),
    re.compile(r"\.aws/", re.IGNORECASE),
    re.compile(r"\.ssh/", re.IGNORECASE),
    re.compile(r"wp-admin", re.IGNORECASE),
    re.compile(r"wp-login", re.IGNORECASE),
    re.compile(r"phpmyadmin", re.IGNORECASE),
    re.compile(r"phpunit", re.IGNORECASE),
    re.compile(r"adminer", re.IGNORECASE),
    re.compile(r"\.php$", re.IGNORECASE),
    re.compile(r"\.aspx?$", re.IGNORECASE),
    re.compile(r"\.jsp$", re.IGNORECASE),
    re.compile(r"actuator", re.IGNORECASE),
    re.compile(r"swagger", re.IGNORECASE),
    re.compile(r"api-docs", re.IGNORECASE),
    re.compile(r"dns-query", re.IGNORECASE),
    re.compile(r"geoserver", re.IGNORECASE),
    re.compile(r"config\.json", re.IGNORECASE),
    re.compile(r"appsettings", re.IGNORECASE),
    re.compile(r"web\.config$", re.IGNORECASE),
    re.compile(r"console", re.IGNORECASE),
    re.compile(r"jmx-console", re.IGNORECASE),
    re.compile(r"sitemap\.xml$", re.IGNORECASE),
    re.compile(r"robots\.txt$", re.IGNORECASE),
    re.compile(r"\.well-known/security\.txt", re.IGNORECASE),
    re.compile(r"vendor/phpunit", re.IGNORECASE),
    re.compile(r"solr/", re.IGNORECASE),
    re.compile(r"jenkins", re.IGNORECASE),
    re.compile(r"cgi-bin", re.IGNORECASE),
    re.compile(r"\.cgi$", re.IGNORECASE),
    re.compile(r"owa/", re.IGNORECASE),
    re.compile(r"ecp/", re.IGNORECASE),
    re.compile(r"remote/login", re.IGNORECASE),
    re.compile(r"telescope/", re.IGNORECASE),
    re.compile(r"_ignition/", re.IGNORECASE),
    re.compile(r"debug/default/", re.IGNORECASE),
    re.compile(r"admin/", re.IGNORECASE),
    re.compile(r"backup", re.IGNORECASE),
    re.compile(r"\.sql$", re.IGNORECASE),
    re.compile(r"\.tar\.gz$", re.IGNORECASE),
    re.compile(r"\.zip$", re.IGNORECASE),
    re.compile(r"\.bak$", re.IGNORECASE),
    re.compile(r"\.old$", re.IGNORECASE),
    re.compile(r"\.save$", re.IGNORECASE),
]

# 框架/静态资源路径（前缀匹配，含其子路径）
ALLOWED_PREFIXES: tuple[str, ...] = (
    "/_nicegui",
    "/api",
    "/static",
)

# 应用页面路由（精确匹配）
# 唯一来源：core/routes.ALL_ROUTES（由 PAGE_ROUTES 派生）。
# 这样新增页面只需在 core/routes.py 登记一次，不会再出现「路由已注册、
# 白名单漏登记」导致该路径走扫描器正则检查并被计入 IP 限流窗口
# （60 秒 30 次）而误拦正常用户。
ALLOWED_ROUTES: frozenset[str] = ALL_ROUTES


def is_allowed_path(path: str) -> bool:
    """判断路径是否为已知合法路径。

    应用路由按精确匹配，框架/静态资源按前缀匹配。

    :param path: 请求路径
    :return: True 表示合法路径，应直接放行且不计入限流
    """
    return path in ALLOWED_ROUTES or path.startswith(ALLOWED_PREFIXES)


def is_suspicious_path(path: str) -> bool:
    """检查路径是否匹配扫描器模式。"""
    for pattern in SUSPICIOUS_PATH_PATTERNS:
        if pattern.search(path):
            return True
    return False


class ScannerBlockMiddleware(BaseHTTPMiddleware):
    """拦截已知扫描器路径，返回空响应，减少日志噪音。

    对匹配已知漏洞探针模式的路径直接返回 404，
    对正常请求透传。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path: str = request.url.path

        # 根路径和已知合法路径直接放行
        if is_allowed_path(path):
            return await call_next(request)

        # 检查是否为扫描器路径
        if is_suspicious_path(path):
            return PlainTextResponse("", status_code=404)

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 IP 的简易限流中间件。

    对非合法路径的 404 请求进行限流，
    同一 IP 在窗口期内超过阈值则临时封禁。
    """

    def __init__(
        self,
        app,
        window_seconds: int = 60,
        max_requests: int = 30,
        block_seconds: int = 300,
    ):
        super().__init__(app)
        # 限流计数为进程内共享状态，用锁保护并发访问；
        # 注意：多 worker / 多进程部署时各进程独立计数，限流只能约束单进程流量。
        self._lock = threading.Lock()
        self._window_seconds: int = window_seconds
        self._max_requests: int = max_requests
        self._block_seconds: int = block_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._blocks: dict[str, float] = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip: str = request.client.host if request.client else "unknown"
        path: str = request.url.path
        now: float = time.time()

        # 检查是否在封禁列表
        with self._lock:
            if client_ip in self._blocks:
                if now - self._blocks[client_ip] < self._block_seconds:
                    return PlainTextResponse("", status_code=429)
                else:
                    del self._blocks[client_ip]

            # 仅对非合法路径计数
            if not is_allowed_path(path):
                # 清理过期记录
                self._requests[client_ip] = [
                    t for t in self._requests[client_ip]
                    if now - t < self._window_seconds
                ]
                self._requests[client_ip].append(now)

                # 超过阈值则封禁
                if len(self._requests[client_ip]) > self._max_requests:
                    self._blocks[client_ip] = now
                    return PlainTextResponse("", status_code=429)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加基础安全响应头。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response

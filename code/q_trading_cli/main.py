#!/usr/bin/env python3
'''
Author: liguoqiang
Date: 2024-08-12 09:07:02
LastEditors: liguoqiang
LastEditTime: 2024-08-24 09:53:18
Description: 项目main， 读取配置文件，启动scheduler，并启动flask
'''

# coding="utf8"

import logging
import logging.config
import os
import sys
from configparser import ConfigParser

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect

from api.client import UnauthorizedError
from app_context import AppContext
from middleware.security import (
    RateLimitMiddleware,
    ScannerBlockMiddleware,
    SecurityHeadersMiddleware,
)
from pages import login_page, main_page
from resources import strings


def initLogger(configPath):
    # 确保 log 目录存在（开发环境和 PyInstaller 环境都需要）
    os.makedirs("log", exist_ok=True)
    if os.path.exists(configPath):
        with open(configPath, "r", encoding="utf-8") as f:
            config = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s-%(name)s-%(lineno)s-%(levelname)s-%(message)s",
            filename="log/q_trading_cli.log",
            filemode="w",
        )

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not app.storage.user.get("authenticated", False):
            path: str = request.url.path
            # 记录来源路径供登录后跳转，不强制重定向，页面自行弹登录框
            if not any(path.startswith(p) for p in ("/_nicegui", "/login", "/api", "/static")):
                app.storage.user["referrer_path"] = path
        try:
            return await call_next(request)
        except ClientDisconnect:
            return Response(status_code=200)


class ClientDisconnectMiddleware(BaseHTTPMiddleware):
    """处理客户端断开连接异常，防止作为错误日志记录"""
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except ClientDisconnect:
            # 客户端主动断开连接，这通常是正常的用户行为
            return Response(status_code=200)

"""
function: init_app
description: 初始化APP
return {*}
"""
def init_app():
    # 安全中间件（最先执行）
    app.add_middleware(ScannerBlockMiddleware)      # 拦截扫描器路径
    app.add_middleware(RateLimitMiddleware)          # IP 限流
    app.add_middleware(SecurityHeadersMiddleware)    # 安全响应头
    # 业务中间件
    app.add_middleware(ClientDisconnectMiddleware)
    app.add_middleware(AuthMiddleware)

    # 全局异常处理器：兜底捕获未处理的 401 错误，防止 NiceGUI 显示错误页面
    @app.exception_handler(UnauthorizedError)
    async def handle_unauthorized_error(request: Request, _exc: Exception):
        """UnauthorizedError 已在 ApiClient._on_401() 中处理过（确认框 → /login），
        此处仅返回 200 避免 NiceGUI 显示错误页面。"""
        logger.warning(f"未捕获的 UnauthorizedError: {request.url.path}")
        return Response(status_code=200)
    # 添加以下代码以注册静态文件目录
    # 获取当前文件所在目录的路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 注册静态文件目录
    app.add_static_files('/static', os.path.join(current_dir, "static"))
    @ui.page('/login')
    def login()-> RedirectResponse | None:
        return login_page.login()

    @ui.page('/')
    def main_page_view():
        main_page.main_page()

    @ui.page('/stock-pools')
    def stock_pools_page_view():
        """股票池列表独立页面。"""
        from pages.stock_pools import show_stock_pools_page as _show
        main_page.main_page_with_content(_show, page_key="pools")

    @ui.page('/stock-quotes')
    def stock_quotes_page_view():
        """股票行情独立页面。"""
        from pages.stock_quotes import show_stock_quotes_page as _show
        main_page.main_page_with_content(_show, page_key="quotes")

    @ui.page('/settings-blacklist')
    def blacklist_page_view():
        """黑名单管理独立页面。"""
        from pages.settings_blacklist import show_blacklist_page as _show
        main_page.main_page_with_content(_show, page_key="blacklist")

    @ui.page('/settings-preferences')
    def preferences_page_view():
        """个人喜好独立页面。"""
        from pages.settings_preferences import show_preferences_page as _show
        main_page.main_page_with_content(_show, page_key="preferences")

    @ui.page('/strategy-monitor')
    def strategy_monitor_page_view():
        """策略监控独立页面。"""
        from pages.strategy_monitor import show_strategy_monitor_page as _show
        main_page.main_page_with_content(_show, page_key="monitor")

    @ui.page('/strategies')
    def strategies_page_view():
        """策略列表独立页面。"""
        from pages.strategies import show_strategies_page as _show
        main_page.main_page_with_content(_show, page_key="strategies")

'''
function app_startup
description: 应用启动时执行的函数，启动管理器
parameters: []
'''
@app.on_startup
async def app_startup():
    AppContext().mqtt_client.connect()
    AppContext().signal_manager.start()

'''
function app_shutdown
description: 应用关闭时执行的函数，清理存储
parameters: []
'''
@app.on_shutdown
async def app_shutdown():
    AppContext().signal_manager.stop()
    AppContext().mqtt_client.disconnect()
    """应用关闭时保留用户会话（不清空，避免重启后出现“已登录但无 token”的中间态）"""
    try:
        session: dict = dict(app.storage.user)
        if session:
            # 重新写回，触发 NiceGUI 将当前会话刷入持久化存储
            app.storage.user.update(session)
    except Exception:  # noqa: BLE001, S110
        pass

'''
function: 
description: 
return {*}
'''
if __name__ in {"__main__", "__mp_main__"}:
    import multiprocessing
    multiprocessing.freeze_support()

    from utils.tools import resource_path
    cp = ConfigParser()
    cp.read(resource_path("cfg/stock.cfg"), encoding="utf-8")
    cfgname = resource_path(cp.get("log", "config"))
    # 初始化日志
    initLogger(cfgname)
    logger = logging.getLogger(__name__)
    listen_host = cp.get("web", "listen_host", fallback="0.0.0.0")
    listen_port = cp.get("web", "listen_port", fallback="8085")
    init_app()
    # 添加异常处理器以静默处理 ClientDisconnect 异常
    async def client_disconnect_exception_handler(request: Request, exc: Exception):
        """处理客户端断开连接异常，不记录为错误"""
        if isinstance(exc, ClientDisconnect):
            logger.debug(f"Client disconnected: {request.url.path}")
            return Response(status_code=200)
        raise exc

    if isinstance(app, FastAPI):
        app.add_exception_handler(ClientDisconnect, client_disconnect_exception_handler)

    # PyInstaller 打包后关闭 reload，避免 fork 炸弹
    _is_frozen: bool = hasattr(sys, "_MEIPASS")
    ui.run(title=strings.get('app_name'),
        host=listen_host,
        port=int(listen_port),
        language='zh-CN',
        reconnect_timeout=120,
        storage_secret='753dcd75-e6a3-40c8-b5be-53a680472ba2',
        reload=not _is_frozen,
        uvicorn_reload_excludes=",".join((  # noqa: FLY002
            "*.py[cod]",
            "*.sw.*",
            "~*",
            "__pycache__/*",
            ".git/*",
            ".venv/*",
            "venv/*",
            ".nicegui/*",
            "log/*",
            "*.log",
            "*.tmp",
            "*.temp",
            ".pytest_cache/*",
            "*.egg-info/*",
            "dist/*",
            "build/*",
        )))


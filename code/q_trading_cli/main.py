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
import secrets
import sys
from configparser import ConfigParser
from functools import partial

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

    # 隐藏 NiceGUI 内置的「连接丢失 / 正在尝试重新连接…」浮层。
    #
    # 该浮层来自 NiceGUI 模板 templates/index.html 中固定存在的
    # <div id="popup" class="nicegui-error-popup">，前端在 socket 断开后由
    # nicegui.js 把 aria-hidden 置为 false 显示出来；nicegui.css 中
    # `#popup[aria-hidden="false"] { transition-delay: 2000ms; }`
    # 意味着只有断开持续 2 秒以上才会真正可见（短暂闪断不会弹）。
    #
    # 放在这里的理由：ui.add_css() 在页面函数之外调用时必须显式传 shared=True。
    # NiceGUI 源码 nicegui/functions/style.py::_add_javascript 中，shared=False
    # 走的是 context.client 分支（无页面上下文时会**自动创建一个客户端**），
    # 样式只会加到那个临时客户端上，页面拿不到；shared=True 才写入
    # Client.shared_head_html，对所有页面生效（该分支还刻意不自动建客户端）。
    #
    # 注意：只隐藏 #popup（断线提示），不触碰同属 nicegui-error-popup 类的
    # #too_long_message_popup（消息过大告警，属于有价值的诊断信息）。
    #
    # 影响面：隐藏的只是提示浮层，不影响重连逻辑——socket.io 照旧自动重连，
    # 服务器主动要求重连的 try_reconnect 分支照旧整页 reload。
    # 若发现频繁弹出，说明 WebSocket 真的在反复断开（反向代理空闲超时、
    # Upgrade 头未透传等），应去排查代理配置而不是依赖这个提示。
    ui.add_css("""
        #popup.nicegui-error-popup {
            display: none !important;
        }
    """, shared=True)

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

    @ui.page('/strategy-select')
    def strategy_select_page_view():
        """策略选股独立页面。"""
        from pages.strategy_select import show_strategy_select_page as _show
        main_page.main_page_with_content(_show, page_key="strategy_select")

    @ui.page('/strategy-backtest')
    def strategy_backtest_page_view(strategy_id: str = '', source: str = ''):
        """策略回测记录独立页面。

        策略模板 ID 与来源标识通过**查询参数**传入
        （``/strategy-backtest?strategy_id=xxx&source=strategies``）：
        NiceGUI 会把页面函数签名里的简单类型参数（这里是没有默认路径段的
        ``strategy_id`` / ``source``）交给 FastAPI 当成可选 query 参数注入，
        函数体拿到的就是解析后的值。不用 ``{strategy_id}`` 动态路径，是因为
        安全中间件对应用路由做精确匹配，动态路径会落进限流窗口
        （详见 core/routes.backtest_route）。

        未带参数进入（如登录回跳丢了查询串）时由页面自己渲染空态，不报错。

        ``source`` 是入口页面标识（策略监控 / 策略列表）：页面的「返回」按钮
        与侧边栏高亮都跟随它 —— 从策略列表点进来，就该高亮并返回策略列表。
        这里先用 backtest_source() 归一化（白名单校验），非法值回退策略监控。
        """
        from core.routes import backtest_source
        from pages.strategy_backtest import show_strategy_backtest_page as _show
        # 内容函数签名带参数，这里用 partial 固化；main_page_with_content 只负责
        # 布局与鉴权，仍以无参方式调用 content_fn。
        back_key: str = backtest_source(source)
        main_page.main_page_with_content(
            partial(_show, strategy_id, source=back_key), page_key=back_key
        )

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

    # storage_secret 不再硬编码在源码中。
    # 优先级：环境变量 Q_TRADING_STORAGE_SECRET > cfg/stock.cfg [web] storage_secret。
    # 两者都未配置时生成随机值（安全性最好，但重启后会话失效、用户需重新登录）。
    # 注意：更换该值会让所有已签发的会话 Cookie 失效。
    storage_secret: str = (
        os.environ.get("Q_TRADING_STORAGE_SECRET", "").strip()
        or cp.get("web", "storage_secret", fallback="").strip()
    )
    if not storage_secret:
        storage_secret = secrets.token_hex(32)
        logger.warning(
            "未配置 storage_secret（环境变量 Q_TRADING_STORAGE_SECRET 或 "
            "cfg/stock.cfg 的 [web] storage_secret），已生成随机值；"
            "进程重启后所有会话将失效，生产环境请显式配置。"
        )
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
        storage_secret=storage_secret,
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


#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2025-06-01 12:06:35
LastEditors: liguoqiang
LastEditTime: 2026-09-16
Description: 登录/注册页面。卡片沿用 doc/prototypes/login.html 原型；
页面背景为量化终端风格（蓝图网格 / 品牌辉光 / 品牌水印 / K 线粒子动画），
见 components/login_background.py，设计稿 doc/prototypes/login-bg.html。

提供两种使用方式：
  - login(): 独立登录页面路由 (/login)，带动态背景
  - render_login_dialog(): 在当前页面弹出登录对话框（**不装背景**——
    它是 fixed 浮层，会盖住已经渲染好的应用界面）

页面跟随会话主题（系统默认浅色）：背景与卡片一律走设计令牌，
深浅两套随 body 的 class 自动切换。

卡片位置：中心落在**窗口横向黄金分割点（61.8%）**，见 .login-viewport
（padding-left = calc(23.6% + 1rem)、padding-right = 1rem）。窄屏（≤767px）
回退为水平居中。
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from api.client import ApiError, UnauthorizedError
from app_context import AppContext
from colors.theme import theme_manager
from components.login_background import install_login_background
from core.routes import sanitize_referrer

# ---- 原型配色：全部取自 colors/theme.py 的令牌 ----
# 页面跟随会话主题（系统默认浅色），令牌取值随主题自动切换。
_BODY_BG: str = theme_manager.var("page")
_CARD_BG: str = theme_manager.var("surface")
# 卡片做成半透明 + 背景模糊：背后的 K 线粒子与辉光能透出来，
# 否则不透明卡片会把整块背景挡住（见 components/login_background.py）
_CARD_BG_FROSTED: str = theme_manager.var_rgba("surface", 0.82)
_CARD_BORDER: str = theme_manager.var("border")
_TAB_BG: str = theme_manager.var("surface-alt")
_TAB_ACTIVE_TEXT: str = theme_manager.var("link")
_TAB_INACTIVE_TEXT: str = theme_manager.var("muted")
_INPUT_BG: str = theme_manager.var("surface-alt")
_INPUT_BORDER: str = theme_manager.var("border")
_INPUT_FOCUS: str = theme_manager.var("primary")
_FONT: str = theme_manager.var("text")
_LABEL_COLOR: str = theme_manager.var("label")
_MUTED: str = theme_manager.var("muted")
_BRAND_BG: str = theme_manager.var("primary")
_BTN_PRIMARY: str = theme_manager.var("primary")
_BTN_HOVER: str = theme_manager.var("primary-hover")
_BRAND_TEXT: str = theme_manager.var("heading")
_ON_ACCENT: str = theme_manager.var("on-accent")
_SHADOW: str = theme_manager.var("shadow")
_SHADOW_SOFT: str = theme_manager.var_rgba("shadow", 0.2)
_FOCUS_RING: str = theme_manager.var_rgba("primary", 0.3)


# ============================================================
#  公开接口
# ============================================================


def login() -> RedirectResponse | None:
    """独立登录页面（/login 路由）。"""
    if app.storage.user.get("authenticated", False):
        return RedirectResponse("/")

    AppContext().theme_manager.apply_session_theme()

    # 量化终端风格的动态背景（网格 / 辉光 / 水印 / K 线粒子）。
    # 必须在 apply_session_theme() 之后：脚本的兜底色值按当前会话主题取。
    install_login_background()

    # 重置 NiceGUI 整个页面容器链，确保内容铺满视口。
    # 背景色只给 html/body：Quasar 的容器若带不透明底色会把 fixed 的背景层盖住。
    ui.add_css(f"""
        html, body, #nicegui, .q-layout, .q-page-container, .q-page {{
            height: 100% !important;
            min-height: 100vh !important;
            margin: 0 !important;
            padding: 0 !important;
        }}
        html, body {{
            background-color: {_BODY_BG} !important;
        }}
        #nicegui, .q-layout, .q-page-container, .q-page {{
            background-color: transparent !important;
        }}
        .nicegui-content {{
            height: 100% !important;
            min-height: 100vh !important;
            padding: 0 !important;
        }}

        /* ===== 登录视口容器：卡片中心落在窗口横向黄金分割点 =====
           容器铺满视口、水平居中卡片，因此卡片中心
           = (padding-left + 视口宽 − padding-right) / 2。
           取 padding-right = 1rem、padding-left = calc(23.6% + 1rem)
           ⇒ 卡片中心 = (23.6% + 100%) / 2 = 61.8%（黄金分割点 0.618）。
           注：这里的百分比按包含块（视口）宽度解析，与 width:100% 同源。

           box-sizing 必须显式写死：Quasar 的全局 border-box 是「继承式」的
           （通配选择器把 box-sizing 设为 inherit，只有 html/body 声明 border-box），
           祖先链上任何一层改成 content-box 都会传播下来；那样 padding 会额外撑宽
           容器，卡片右移过头并横向溢出。
           注意：本段是 f-string，注释里不要出现裸花括号，否则会被当表达式求值。 */
        .login-viewport {{
            box-sizing: border-box;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            background-color: transparent;
            padding: 1rem 1rem 1rem calc(23.6% + 1rem);
            z-index: 10;
        }}
        /* 窄屏：61.8% 会把卡片压窄到 254px（375px 视口），回退为水平居中 */
        @media (max-width: 767px) {{
            .login-viewport {{ padding: 1rem; }}
        }}
    """)

    # 全屏视口容器 — fixed 定位不依赖父级高度；背景透明以露出后面的背景层。
    # 位置（含黄金分割点偏移）见上方 .login-viewport
    with ui.element("div").classes("login-viewport"):
        _render_login_card(
            on_success=lambda: ui.navigate.to(
                # 只回跳已登记的合法路由：referrer 可能来自旧版前端写错的
                # "/monitor" 或已废弃的书签，直接跳会落到 404 页面
                sanitize_referrer(app.storage.user.get("referrer_path", "/"))
            )
        )

    return None


def render_login_dialog(on_success: Callable[[], None] | None = None) -> None:
    """在对话框中弹出登录/注册表单。

    :param on_success: 登录成功后的回调，默认跳转首页并刷新
    """
    AppContext().theme_manager.apply_session_theme()

    def _default_success() -> None:
        # 同 show_login_page：回跳前先校验 referrer 是否为已登记路由
        ui.navigate.to(
            sanitize_referrer(app.storage.user.get("referrer_path", "/"))
        )

    callback: Callable[[], None] = on_success or _default_success

    with ui.dialog(value=True).props("persistent no-backdrop-dismiss") as _dialog:
        _render_login_card(
            on_success=lambda: [callback(), _dialog.close()],
        )


# ============================================================
#  全局 CSS — 严格匹配原型，重置 NiceGUI/Quasar 的默认样式
# ============================================================


def _inject_prototype_css() -> None:
    """注入原型 CSS，覆盖 Quasar 默认样式以实现像素级匹配。"""
    ui.add_css(f"""
        /* ===== 卡片 ===== */
        .login-card {{
            background-color: {_CARD_BG_FROSTED};
            border: 1px solid {_CARD_BORDER};
            border-radius: 1.25rem;
            padding: 2.5rem 2.5rem 2rem;
            width: 100%;
            max-width: 440px;
            box-shadow: 0 20px 40px {_SHADOW};
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            margin: 0 auto;
        }}
        @media (max-width: 480px) {{
            .login-card {{ padding: 1.5rem; }}
        }}

        /* ===== 品牌 ===== */
        .login-brand {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            justify-content: center;
            margin-bottom: 2rem;
        }}
        .login-brand-icon {{
            width: 2.5rem; height: 2.5rem;
            background: {_BRAND_BG};
            border-radius: 0.75rem;
            display: flex; align-items: center; justify-content: center;
            color: {_ON_ACCENT}; font-size: 1.5rem;
        }}
        .login-brand-text {{
            font-size: 1.75rem; font-weight: 700;
            color: {_BRAND_TEXT}; letter-spacing: -0.02em;
        }}

        /* ===== Tab 切换栏 ===== */
        .login-tabs {{
            display: flex;
            gap: 0.5rem;
            background-color: {_TAB_BG};
            border-radius: 0.75rem;
            padding: 0.25rem;
            margin-bottom: 2rem;
        }}
        .login-tabs .tab-btn {{
            flex: 1;
            padding: 0.6rem 0;
            border: none;
            border-radius: 0.5rem;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            background: transparent;
            color: {_TAB_INACTIVE_TEXT};
            text-align: center;
            user-select: none;
            outline: none;
            line-height: 1.5;
        }}
        .login-tabs .tab-btn.active {{
            background: {_CARD_BG};
            color: {_TAB_ACTIVE_TEXT};
            box-shadow: 0 2px 8px {_SHADOW_SOFT};
        }}
        .login-tabs .tab-btn:hover:not(.active) {{
            color: {_FONT};
        }}

        /* ===== 表单组 ===== */
        .login-card .form-group {{
            margin-bottom: 1.25rem;
        }}
        .login-card .form-group > .form-label {{
            display: block;
            font-size: 0.875rem; font-weight: 500;
            color: {_LABEL_COLOR};
            margin-bottom: 0.3rem;
        }}

        /* ===== 输入框 — 剥离 Quasar 样式，严格匹配原型 ===== */
        .login-card .q-field {{
            padding: 0 !important;
            margin: 0 !important;
        }}
        .login-card .q-field__inner {{
            padding: 0 !important;
        }}
        .login-card .q-field__control {{
            min-height: auto !important;
            height: auto !important;
            padding: 0 !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }}
        .login-card .q-field__control::before,
        .login-card .q-field__control::after {{
            display: none !important;
        }}
        .login-card .q-field__native {{
            width: 100% !important;
            padding: 0.7rem 1rem !important;
            min-height: auto !important;
            height: auto !important;
            background-color: {_INPUT_BG} !important;
            border: 1px solid {_INPUT_BORDER} !important;
            border-radius: 0.75rem !important;
            color: {_FONT} !important;
            font-size: 0.95rem !important;
            transition: border-color 0.2s, box-shadow 0.2s !important;
            outline: none !important;
        }}
        .login-card .q-field--focused .q-field__native {{
            border-color: {_INPUT_FOCUS} !important;
            box-shadow: 0 0 0 3px {_FOCUS_RING} !important;
        }}
        .login-card .q-field__bottom {{
            display: none !important;
        }}
        .login-card .q-field__label {{
            display: none !important;
        }}
        .login-card .q-field__messages {{
            display: none !important;
        }}
        /* 密码眼睛按钮 */
        .login-card .q-field__append {{
            position: absolute; right: 0.75rem; top: 50%;
            transform: translateY(-50%);
            padding: 0 !important;
        }}
        .login-card .q-field__append .q-icon {{
            color: {_MUTED} !important;
            font-size: 1.2rem;
        }}

        /* ===== 表单选项行 ===== */
        .login-form-options {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.875rem;
            margin-bottom: 1.5rem;
        }}
        .login-form-options label {{
            display: flex; align-items: center; gap: 0.4rem;
            color: {_MUTED}; cursor: pointer;
        }}
        .login-form-options .q-checkbox__inner {{
            font-size: 1rem;
        }}
        .login-form-options .forgot-link {{
            color: {_TAB_ACTIVE_TEXT}; font-weight: 500;
            cursor: pointer; text-decoration: none;
        }}
        .login-form-options .forgot-link:hover {{
            text-decoration: underline;
        }}

        /* ===== 主按钮 — 剥离 Quasar 样式 ===== */
        .login-card .btn-primary {{
            width: 100% !important;
            padding: 0.75rem !important;
            min-height: auto !important;
            background: {_BTN_PRIMARY} !important;
            border: none !important;
            border-radius: 0.75rem !important;
            color: {_ON_ACCENT} !important;
            font-weight: 600 !important;
            font-size: 1rem !important;
            cursor: pointer !important;
            text-transform: none !important;
            letter-spacing: normal !important;
            box-shadow: none !important;
        }}
        .login-card .btn-primary:hover {{
            background: {_BTN_HOVER} !important;
        }}
        .login-card .btn-primary .q-btn__content {{
            padding: 0 !important;
        }}

        /* ===== 底部切换文字 ===== */
        .login-switch-text {{
            text-align: center;
            margin-top: 1.5rem;
            font-size: 0.875rem;
            color: {_MUTED};
        }}
        .login-switch-text .switch-link {{
            color: {_TAB_ACTIVE_TEXT};
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
        }}
        .login-switch-text .switch-link:hover {{
            text-decoration: underline;
        }}
    """)


# ============================================================
#  登录卡片主渲染
# ============================================================


def _render_login_card(on_success: Callable[[], None]) -> None:
    """渲染登录/注册卡片内容。

    严格按照 login.html 原型实现：品牌 → Tab → 表单面板（refreshable 切换）。

    :param on_success: 登录成功回调
    """
    _inject_prototype_css()

    page_mode: dict[str, str] = {"value": "login"}
    inputs_ref: dict[str, ui.input | None] = {
        "username": None, "password": None, "phone": None, "email": None,
        "confirm_password": None,
    }

    def _get_input(key: str) -> str:
        inp: ui.input | None = inputs_ref.get(key)
        if inp is None:
            return ""
        return (inp.value or "").strip()

    def _switch_tab(mode: str) -> None:
        """切换登录/注册 Tab。"""
        if page_mode["value"] == mode:
            return
        page_mode["value"] = mode
        for key in inputs_ref:
            inputs_ref[key] = None
        _update_tab_ui()
        form_ui.refresh()

    # ==================== 卡片容器（纯 div，非 ui.card） ====================
    with ui.element("div").classes("login-card"):
        # ---- Brand ----
        with ui.element("div").classes("login-brand"):
            with ui.element("div").classes("login-brand-icon"):
                ui.icon("trending_up")
            ui.label("QTrade").classes("login-brand-text")

        # ---- Tabs — 纯 div，不用 ui.button 避免 Quasar 样式冲突 ----
        with ui.element("div").classes("login-tabs"):
            with ui.element("div").classes("tab-btn active") as login_tab:
                ui.label("登录")
            with ui.element("div").classes("tab-btn") as register_tab:
                ui.label("注册")

        def _update_tab_ui() -> None:
            """更新 tab active 样式 — 显式 add/remove 避免叠加。"""
            is_login = page_mode["value"] == "login"
            if is_login:
                login_tab.classes(add="active")
                register_tab.classes(remove="active")
            else:
                login_tab.classes(remove="active")
                register_tab.classes(add="active")

        login_tab.on("click", lambda: _switch_tab("login"))
        register_tab.on("click", lambda: _switch_tab("register"))

        # ---- 表单面板 — refreshable 实现登录/注册切换 ----
        @ui.refreshable
        def form_ui() -> None:
            is_register: bool = page_mode["value"] == "register"
            if is_register:
                _render_register_panel(on_success, inputs_ref, _get_input, _switch_tab)
            else:
                _render_login_panel(on_success, inputs_ref, _get_input, _switch_tab)

        form_ui()


# ============================================================
#  登录面板
# ============================================================


def _render_login_panel(
    on_success: Callable[[], None],
    inputs_ref: dict[str, ui.input | None],
    get_input: Callable[[str], str],
    switch_tab: Callable[[str], None],
) -> None:
    """登录表单 — 严格匹配原型 #panel-login。"""

    def do_login() -> None:
        account: str = get_input("username")
        pwd: str = get_input("password")
        if not account or not pwd:
            ui.notify("请输入用户名和密码", type="warning")
            return
        try:
            result = AppContext().user_api.login(account, pwd)
            token: str = str(result.get("token", "") or "")
            app.storage.user.update({
                "username": account,
                "authenticated": True,
                "token": token,
                "email": result.get("email", ""),
                "phone": result.get("phone", ""),
                "user_id": result.get("user_id", result.get("id", "")),
            })
            # token 存于 app.storage.user，按会话隔离；后台线程通过
            # core.concurrency.run_blocking() 继承请求上下文后同样可读取，
            # 无需（也不应）使用进程级共享 token。
            ui.notify("登录成功", type="positive")
            on_success()
        except ApiError as e:
            ui.notify(f"登录失败: {e.message}", type="negative")
        except UnauthorizedError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"网络错误: {e!s}", type="negative")

    # 用户名 / 邮箱 / 手机号
    with ui.element("div").classes("form-group"):
        ui.label("用户名 / 邮箱 / 手机号").classes("form-label")
        inp = (
            ui.input(placeholder="请输入用户名、邮箱或手机号")
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        inp.on("keydown.enter", lambda e: do_login())
        inputs_ref["username"] = inp

    # 密码
    with ui.element("div").classes("form-group"):
        ui.label("密码").classes("form-label")
        pwd = (
            ui.input(placeholder="请输入密码", password=True, password_toggle_button=True)
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        pwd.on("keydown.enter", lambda e: do_login())
        inputs_ref["password"] = pwd

    # 记住我 + 忘记密码
    with ui.element("div").classes("login-form-options"):
        with ui.element("label"):
            ui.checkbox(value=True).props("dense")
            ui.label("记住我")
        ui.label("忘记密码？").classes("forgot-link") \
            .on("click", lambda: ui.notify("请联系管理员重置密码", type="info"))

    # 登录按钮
    ui.button("登 录", on_click=do_login).classes("btn-primary")

    # 切换注册
    with ui.element("div").classes("login-switch-text"):
        ui.label("还没有账号？")
        ui.label("立即注册").classes("switch-link") \
            .on("click", lambda: switch_tab("register"))


# ============================================================
#  注册面板
# ============================================================


def _render_register_panel(
    on_success: Callable[[], None],
    inputs_ref: dict[str, ui.input | None],
    get_input: Callable[[str], str],
    switch_tab: Callable[[str], None],
) -> None:
    """注册表单 — 严格匹配原型 #panel-register。"""

    def do_register() -> None:
        username: str = get_input("username")
        email: str = get_input("email")
        phone: str = get_input("phone")
        pwd: str = get_input("password")
        confirm: str = get_input("confirm_password")

        if not username or not pwd:
            ui.notify("用户名和密码不能为空", type="warning")
            return
        if len(username) < 4 or len(username) > 16:
            ui.notify("用户名应为4-16位字母或数字", type="warning")
            return
        if len(pwd) < 8:
            ui.notify("密码至少8位，含字母和数字", type="warning")
            return
        if pwd != confirm:
            ui.notify("两次输入的密码不一致", type="warning")
            return

        try:
            result: str = AppContext().user_api.register(
                account=username, password=pwd, phone=phone, email=email,
            )
            ui.notify(result or "注册成功，请登录", type="positive")
            # 注册成功后自动登录
            try:
                login_result = AppContext().user_api.login(username, pwd)
                reg_token: str = str(login_result.get("token", "") or "")
                app.storage.user.update({
                    "username": username,
                    "authenticated": True,
                    "token": reg_token,
                    "email": login_result.get("email", email),
                    "phone": login_result.get("phone", phone),
                    "user_id": login_result.get("user_id", login_result.get("id", "")),
                })
                if reg_token:
                    # token 仅存本会话：后台线程的鉴权由 core.concurrency.run_blocking
                    # 沿用请求上下文（contextvars）解析，不再需要进程级 fallback。
                    app.storage.user["token"] = reg_token
                ui.notify("登录成功", type="positive")
                on_success()
            except Exception:  # noqa: BLE001
                switch_tab("login")
        except ApiError as e:
            ui.notify(f"注册失败: {e.message}", type="negative")
        except Exception as e:  # noqa: BLE001
            ui.notify(f"网络错误: {e!s}", type="negative")

    # 用户名
    with ui.element("div").classes("form-group"):
        ui.label("用户名").classes("form-label")
        inp = (
            ui.input(placeholder="请设置用户名（4-16位字母或数字）")
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        inp.on("keydown.enter", lambda e: do_register())
        inputs_ref["username"] = inp

    # 邮箱
    with ui.element("div").classes("form-group"):
        ui.label("邮箱").classes("form-label")
        inp = (
            ui.input(placeholder="请输入邮箱")
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        inputs_ref["email"] = inp

    # 手机号
    with ui.element("div").classes("form-group"):
        ui.label("手机号").classes("form-label")
        inp = (
            ui.input(placeholder="请输入手机号")
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        inputs_ref["phone"] = inp

    # 密码
    with ui.element("div").classes("form-group"):
        ui.label("密码").classes("form-label")
        pwd = (
            ui.input(placeholder="至少8位，含字母和数字", password=True, password_toggle_button=True)
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        pwd.on("keydown.enter", lambda e: do_register())
        inputs_ref["password"] = pwd

    # 确认密码
    with ui.element("div").classes("form-group"):
        ui.label("确认密码").classes("form-label")
        confirm = (
            ui.input(placeholder="再次输入密码", password=True, password_toggle_button=True)
            .props("dense hide-bottom-space borderless")
            .classes("w-full")
        )
        confirm.on("keydown.enter", lambda e: do_register())
        inputs_ref["confirm_password"] = confirm

    # 注册按钮
    ui.button("注 册", on_click=do_register).classes("btn-primary")

    # 切换登录
    with ui.element("div").classes("login-switch-text"):
        ui.label("已有账号？")
        ui.label("去登录").classes("switch-link") \
            .on("click", lambda: switch_tab("login"))

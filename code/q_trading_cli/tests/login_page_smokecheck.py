#!/usr/bin/env python3
"""
Description: 登录/注册页（pages/login_page.py）离线冒烟检查 —— 用桩替代
    nicegui / fastapi / app_context / colors / components，真跑一次
    _render_register_panel 与 _render_login_panel，断言各类失败给出的提示文案。

为什么需要这个脚本：
    后端用 HTTP 4xx 表达业务错误（409「账号已存在」、401「账号或密码错误」、
    422 校验失败），响应体里的 detail 才是该给用户看的那句话。页面若只捕
    ApiError / UnauthorizedError，这类错误会落到最后的 `except Exception`
    分支，界面上弹出的是
        "网络错误: Client error '409 Conflict' for url 'http://.../api/user/register'"
    —— 不报错、不崩溃，只是把一句正常业务提示显示成了故障。静态工具查不出来，
    只有真跑一次拿到 ui.notify 的文案才能钉住。

口径（后端契约 q_trading_server/web_api/routers/user.py）：
    POST /api/user/register 重复账号 / 邮箱 / 手机号 ⇒ 409 + {"detail": "..."}
    POST /api/user/login    账号或密码错误       ⇒ 401 + {"detail": "..."}
    422 的 detail 是字段级错误列表（不是给用户看的句子）⇒ 用状态码兜底文案

运行：python tests/login_page_smokecheck.py   （exit 0 = 通过）
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import types
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 允许用环境变量指向改写过的副本，用于"反向验证"（改坏实现后断言必须报错）
PAGE_SRC = pathlib.Path(
    os.environ.get("SMOKE_LOGIN_PAGE_SRC", str(ROOT / "pages" / "login_page.py"))
)
CLIENT_SRC = pathlib.Path(
    os.environ.get("SMOKE_API_CLIENT_SRC", str(ROOT / "api" / "client.py"))
)

logging.disable(logging.CRITICAL)

import httpx  # noqa: E402  （本机隔离 venv 已装；错误响应必须用真 httpx 对象构造）

_fail: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    """记录一条断言结果。"""
    if cond:
        print(f"[OK]   {label}" + (f" -> {detail}" if detail else ""))
    else:
        _fail.append(label)
        print(f"[FAIL] {label}" + (f" -> {detail}" if detail else ""))
    return cond


# ---------------------------------------------------------------- nicegui 桩

_STACK: list["_Node"] = []
_ROOTS: list["_Node"] = []
_NOTIFY: list[tuple[str, str]] = []


class _Node:
    """极简元素桩：只保留检查需要的属性。"""

    def __init__(self, kind: str, **kw: Any) -> None:
        self.kind: str = kind
        self.kw: dict[str, Any] = kw
        self.children: list[_Node] = []
        self.classes_list: list[str] = []
        self.styles: list[str] = []
        self.props_list: list[str] = []
        self.handlers: dict[str, list[Any]] = {}
        self.value: Any = kw.get("text", "")

    def __enter__(self) -> "_Node":
        _STACK.append(self)
        return self

    def __exit__(self, *exc: Any) -> bool:
        _STACK.pop()
        return False

    def classes(self, *args: Any, **kw: Any) -> "_Node":
        self.classes_list.extend(str(a) for a in args)
        return self

    def style(self, *args: Any, **kw: Any) -> "_Node":
        self.styles.extend(str(a) for a in args)
        return self

    def props(self, *args: Any, **kw: Any) -> "_Node":
        self.props_list.extend(str(a) for a in args)
        return self

    def on(self, event: str, handler: Any = None, **kw: Any) -> "_Node":
        self.handlers.setdefault(event, []).append(handler)
        return self

    def walk(self) -> list["_Node"]:
        out: list[_Node] = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


def _make(kind: str, **kw: Any) -> _Node:
    node = _Node(kind, **kw)
    if _STACK:
        _STACK[-1].children.append(node)
    else:
        _ROOTS.append(node)
    return node


def _reset_tree() -> None:
    _STACK.clear()
    del _ROOTS[:]
    del _NOTIFY[:]
    _STACK.append(_Node("root"))


def _all(kind: str) -> list[_Node]:
    out: list[_Node] = []
    for root in _STACK:
        out.extend(n for n in root.walk() if n.kind == kind)
    return out


def _register_button() -> Any:
    """注册面板的「注 册」按钮（按文案取，避免依赖第几个按钮）。"""
    for node in _all("button"):
        if str(node.kw.get("text", "")).replace(" ", "") == "注册":
            return node.kw.get("on_click")
    return None


def _login_button() -> Any:
    """登录面板的「登 录」按钮。"""
    for node in _all("button"):
        if str(node.kw.get("text", "")).replace(" ", "") == "登录":
            return node.kw.get("on_click")
    return None


def _messages() -> list[str]:
    return [m for m, _ in _NOTIFY]


# ------------------------------------------------- app_context / user_api 桩

class _UserApiStub:
    """user_api 桩：行为可控（抛什么、返回什么），并记录调用参数。"""

    def __init__(self) -> None:
        self.register_error: Exception | None = None
        self.register_result: str = "注册成功"
        self.login_error: Exception | None = None
        self.login_result: dict[str, Any] = {
            "token": "tk-1", "email": "a@b.c", "phone": "138", "user_id": "u-1",
        }
        self.calls: list[tuple[Any, ...]] = []

    def register(self, account: str, password: str, phone: str = "",
                 email: str = "") -> str:
        self.calls.append(("register", account, password, phone, email))
        if self.register_error is not None:
            raise self.register_error
        return self.register_result

    def login(self, account: str, password: str) -> dict[str, Any]:
        self.calls.append(("login", account, password))
        if self.login_error is not None:
            raise self.login_error
        return dict(self.login_result)


_USER_API = _UserApiStub()


def _install_stubs() -> types.ModuleType:
    """装配所有依赖桩（nicegui / fastapi / app_context / colors / components）。"""
    mod = types.ModuleType("nicegui")
    ui = types.ModuleType("nicegui.ui")

    ui.add_css = lambda css, shared=False: None
    ui.element = lambda tag="div", *a, **k: _make(f"element:{tag}")
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.input = lambda *a, **k: _make("input", **k)
    ui.button = lambda *a, **k: _make("button", text=str(a[0]) if a else k.get("text", ""), **k)
    ui.checkbox = lambda *a, **k: _make("checkbox", **k)
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.notify = lambda message="", **kw: _NOTIFY.append((str(message), str(kw.get("type", ""))))
    ui.navigate = types.SimpleNamespace(to=lambda url: None)
    ui.dialog = lambda *a, **k: _make("dialog")
    ui.timer = lambda *a, **k: _make("timer")

    class _Refreshable:
        def __init__(self, fn: Any) -> None:
            self._fn = fn

        def __call__(self, *a: Any, **k: Any) -> Any:
            return self._fn(*a, **k)

        def refresh(self) -> Any:
            return self._fn()

    ui.refreshable = lambda fn=None: (_Refreshable(fn) if fn is not None else _Refreshable)

    napp = types.ModuleType("nicegui.app")
    napp.is_started = True
    napp.storage = types.SimpleNamespace(user={})
    mod.ui = ui
    mod.app = napp
    sys.modules["nicegui"] = mod
    sys.modules["nicegui.ui"] = ui
    sys.modules["nicegui.app"] = napp

    # fastapi.responses.RedirectResponse —— login() 里 import，模块级执行时也要有
    fastapi = types.ModuleType("fastapi")
    responses = types.ModuleType("fastapi.responses")

    class _RedirectResponse:
        def __init__(self, url: str = "", **kw: Any) -> None:
            self.url = url

    responses.RedirectResponse = _RedirectResponse
    fastapi.responses = responses
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses

    # app_context：AppContext() 返回同一个桩（真实现是单例）
    ac = types.ModuleType("app_context")

    class _Ctx:
        def __init__(self) -> None:
            self.user_api = _USER_API

    _ctx = _Ctx()
    ac.AppContext = lambda: _ctx
    sys.modules["app_context"] = ac

    # colors.theme：页面模块级就用 theme_manager.var() 拼 CSS
    theme_stub = types.ModuleType("colors.theme")
    theme_stub.theme_manager = types.SimpleNamespace(
        var=lambda name, *a, **k: f"var(--qs-{name})",
        var_rgba=lambda name, alpha=1.0, *a, **k: f"rgba(var(--qs-{name}-rgb), {alpha})",
    )
    theme_stub.resolve_mode_from_prefs = lambda prefs: "light"
    colors_pkg = types.ModuleType("colors")
    colors_pkg.__path__ = [str(ROOT / "colors")]
    sys.modules["colors"] = colors_pkg
    sys.modules["colors.theme"] = theme_stub

    components_pkg = types.ModuleType("components")
    components_pkg.__path__ = [str(ROOT / "components")]
    sys.modules["components"] = components_pkg
    lb = types.ModuleType("components.login_background")
    lb.install_login_background = lambda *a, **k: None
    sys.modules["components.login_background"] = lb

    # api.client 默认用真模块（extract_error_detail 就在这里）；反向验证时
    # 允许换成改坏副本。
    if CLIENT_SRC != ROOT / "api" / "client.py":
        stub = types.ModuleType("api.client")
        stub.__file__ = str(CLIENT_SRC)
        exec(compile(CLIENT_SRC.read_text(encoding="utf-8"), str(CLIENT_SRC), "exec"),
             stub.__dict__)
        sys.modules["api.client"] = stub

    return mod


def _load_page_module() -> types.ModuleType:
    """exec 页面源码，返回模块对象。"""
    module = types.ModuleType("login_page_under_test")
    module.__file__ = str(PAGE_SRC)
    exec(compile(PAGE_SRC.read_text(encoding="utf-8"), str(PAGE_SRC), "exec"),
         module.__dict__)
    return module


# ------------------------------------------------------------------ 断言工具

def _http_error(status: int, body: Any, url: str = "http://x/api/user/register") -> httpx.HTTPStatusError:
    """构造真实的 httpx.HTTPStatusError（与 ApiClient 抛出来的是同一类型）。"""
    req = httpx.Request("POST", url)
    resp = httpx.Response(status, json=body, request=req)
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url '{url}'", request=req, response=resp,
    )


def _render_register(page: types.ModuleType) -> dict[str, Any]:
    """渲染注册面板并返回 {inputs, click}，click() 即点「注 册」。"""
    _reset_tree()
    app = sys.modules["nicegui.app"]
    app.storage.user.clear()
    inputs_ref: dict[str, Any] = {}
    switched: list[str] = []
    _on_success: list[bool] = []

    def _get_input(key: str) -> str:
        inp = inputs_ref.get(key)
        return (inp.value or "").strip() if inp is not None else ""

    page._render_register_panel(
        lambda: _on_success.append(True), inputs_ref, _get_input, switched.append,
    )
    return {
        "inputs": inputs_ref, "click": _register_button(), "switched": switched,
        "success": _on_success, "storage": app.storage.user,
    }


def _render_login(page: types.ModuleType) -> dict[str, Any]:
    """渲染登录面板并返回 {inputs, click}。"""
    _reset_tree()
    app = sys.modules["nicegui.app"]
    app.storage.user.clear()
    inputs_ref: dict[str, Any] = {}
    switched: list[str] = []
    _on_success: list[bool] = []

    def _get_input(key: str) -> str:
        inp = inputs_ref.get(key)
        return (inp.value or "").strip() if inp is not None else ""

    page._render_login_panel(
        lambda: _on_success.append(True), inputs_ref, _get_input, switched.append,
    )
    return {
        "inputs": inputs_ref, "click": _login_button(), "switched": switched,
        "success": _on_success, "storage": app.storage.user,
    }


def _fill(inputs: dict[str, Any], **values: str) -> None:
    for key, value in values.items():
        node = inputs.get(key)
        if node is not None:
            node.value = value


def main() -> int:
    _install_stubs()
    page = _load_page_module()

    # ------------------------------------------------ 第 1 节 错误详情提取
    print("--- 第 1 节 extract_error_detail（后端 detail 才是给用户看的那句话） ---")
    from api.client import extract_error_detail

    check("FastAPI HTTPException 的 detail 原样取出",
          extract_error_detail(_http_error(409, {"detail": "账号已存在"}).response)
          == "账号已存在")
    check("统一 ApiResponse 的 message 也能取（detail 缺失时回退）",
          extract_error_detail(_http_error(400, {"code": 1001, "message": "参数不合法"}).response)
          == "参数不合法")
    check("detail 优先于 message",
          extract_error_detail(
              _http_error(409, {"detail": "账号已存在", "message": "Conflict"}).response
          ) == "账号已存在")
    check("422 的 detail 是错误列表 ⇒ 不硬拼成句子，返回空串走状态码兜底",
          extract_error_detail(
              _http_error(422, {"detail": [{"loc": ["body", "account"], "msg": "too short"}]}).response
          ) == "")
    check("空串 detail 不算数（避免弹出一句空的「注册失败: 」）",
          extract_error_detail(_http_error(409, {"detail": "   "}).response) == "")
    check("响应体不是 JSON（网关 HTML 错误页）时返回空串",
          extract_error_detail(
              httpx.Response(409, text="<html>409 Conflict</html>",
                             request=httpx.Request("POST", "http://x/"))
          ) == "")

    # ------------------------------------------------ 第 2 节 注册 409
    print("\n--- 第 2 节 注册重名（409）必须弹业务提示，不是「网络错误」 ---")
    _USER_API.register_error = _http_error(409, {"detail": "账号已存在"})
    state = _render_register(page)
    _fill(state["inputs"], username="testuser", password="abcd1234",
          confirm_password="abcd1234", phone="13800000000", email="a@b.c")
    state["click"]()
    messages = _messages()
    check("注册 409 ⇒ 提示「注册失败: 账号已存在」",
          messages[-1] == "注册失败: 账号已存在", f"{messages}")
    check("不再出现「网络错误」文案",
          not any("网络错误" in m for m in messages), f"{messages}")
    check("不再把 httpx 的英文异常串暴露给用户",
          not any("Client error" in m or "409 Conflict" in m for m in messages),
          f"{messages}")
    check("注册失败时不写登录态、也不切到登录页",
          state["storage"].get("authenticated") is not True
          and "login" not in state["switched"],
          f"{dict(state['storage'])} / {state['switched']}")

    for detail in ("邮箱已被注册", "手机号已被注册"):
        _USER_API.register_error = _http_error(409, {"detail": detail})
        state = _render_register(page)
        _fill(state["inputs"], username="testuser", password="abcd1234",
              confirm_password="abcd1234")
        state["click"]()
        check(f"注册 409 的其它分支也原样透出（{detail}）",
              _messages()[-1] == f"注册失败: {detail}", f"{_messages()}")

    # ------------------------------------------------ 第 3 节 其它状态码
    print("\n--- 第 3 节 没有可读 detail 时用状态码兜底 ---")
    _USER_API.register_error = _http_error(
        422, {"detail": [{"loc": ["body", "account"], "msg": "too short"}]})
    state = _render_register(page)
    _fill(state["inputs"], username="testuser", password="abcd1234",
          confirm_password="abcd1234")
    state["click"]()
    check("422（字段校验列表）⇒ 「注册失败 (HTTP 422)，请稍后重试」",
          _messages()[-1] == "注册失败 (HTTP 422)，请稍后重试", f"{_messages()}")

    _USER_API.register_error = _http_error(500, {"detail": "内部错误"}, url="http://x/api/user/register")
    state = _render_register(page)
    _fill(state["inputs"], username="testuser", password="abcd1234",
          confirm_password="abcd1234")
    state["click"]()
    check("5xx 带 detail 时同样透出后端原文",
          _messages()[-1] == "注册失败: 内部错误", f"{_messages()}")

    from api.client import ApiError
    _USER_API.register_error = ApiError(1001, "用户名只能是字母或数字")
    state = _render_register(page)
    _fill(state["inputs"], username="testuser", password="abcd1234",
          confirm_password="abcd1234")
    state["click"]()
    check("ApiError（后端 code != 0）分支未被新逻辑影响",
          _messages()[-1] == "注册失败: 用户名只能是字母或数字", f"{_messages()}")

    _USER_API.register_error = RuntimeError("connection reset")
    state = _render_register(page)
    _fill(state["inputs"], username="testuser", password="abcd1234",
          confirm_password="abcd1234")
    state["click"]()
    check("真·网络异常仍显示「网络错误」（没把它一并吞成业务提示）",
          _messages()[-1].startswith("网络错误"), f"{_messages()}")

    # ------------------------------------------------ 第 4 节 登录面板
    print("\n--- 第 4 节 登录失败提示 ---")
    from api.client import UnauthorizedError

    _USER_API.login_error = UnauthorizedError("账号或密码错误")
    state = _render_login(page)
    _fill(state["inputs"], username="testuser", password="abcd1234")
    state["click"]()
    check("登录 401（UnauthorizedError）⇒ 显示后端的「账号或密码错误」",
          _messages()[-1] == "账号或密码错误", f"{_messages()}")

    # 匿名请求的 401 不带 token，ApiClient 会原样抛 HTTPStatusError
    _USER_API.login_error = _http_error(
        401, {"detail": "账号或密码错误"}, url="http://x/api/user/login")
    state = _render_login(page)
    _fill(state["inputs"], username="testuser", password="abcd1234")
    state["click"]()
    check("登录 401 若走到 HTTPStatusError ⇒ 也显示「登录失败: 账号或密码错误」",
          _messages()[-1] == "登录失败: 账号或密码错误", f"{_messages()}")
    check("登录失败不出现「网络错误」",
          not any("网络错误" in m for m in _messages()), f"{_messages()}")

    # ------------------------------------------------ 第 5 节 成功路径
    print("\n--- 第 5 节 注册成功仍然自动登录 ---")
    _USER_API.register_error = None
    _USER_API.register_result = "注册成功"
    _USER_API.login_error = None
    state = _render_register(page)
    _fill(state["inputs"], username="newuser", password="abcd1234",
          confirm_password="abcd1234", phone="13800000000", email="a@b.c")
    state["click"]()
    messages = _messages()
    check("注册成功提示后端返回的文案", "注册成功" in messages, f"{messages}")
    check("注册成功后自动登录并写入会话 token",
          state["storage"].get("token") == "tk-1"
          and state["storage"].get("authenticated") is True
          and state["storage"].get("username") == "newuser",
          f"{dict(state['storage'])}")
    check("调用了 register(account=…, phone=…, email=…)",
          ("register", "newuser", "abcd1234", "13800000000", "a@b.c") in _USER_API.calls,
          f"{_USER_API.calls[-2:]}")

    # ------------------------------------------------ 第 6 节 源码接线
    print("\n--- 第 6 节 源码接线（反向验证的锚点） ---")
    src = PAGE_SRC.read_text(encoding="utf-8")
    check("登录页引用了 extract_error_detail（不是自己手抠 JSON）",
          "extract_error_detail" in src and "import httpx" in src)
    check("注册与登录面板各有一个 except httpx.HTTPStatusError 分支",
          src.count("except httpx.HTTPStatusError as e:") == 2,
          f"{src.count('except httpx.HTTPStatusError as e:')}")
    check("HTTPStatusError 分支排在「except Exception」之前（否则永远走不到）",
          src.index("except httpx.HTTPStatusError as e:")
          < src.index('ui.notify(f"网络错误: {e!s}", type="negative")'))
    check("两处分支都真的取了后端 detail",
          src.count("detail: str = extract_error_detail(e.response)") == 2,
          f"{src.count('detail: str = extract_error_detail(e.response)')}")

    print()
    if _fail:
        print(f"失败 {len(_fail)} 项：")
        for name in _fail:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

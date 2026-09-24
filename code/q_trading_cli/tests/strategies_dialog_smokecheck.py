#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-17
Description: 策略列表页「创建/编辑对话框运行状态」离线冒烟检查 —— 桩替 nicegui /
    httpx / app_context / core.user_strategy_manager，真跑一次 show_strategies_page()，
    点开对话框、操作控件、点保存，断言**送到后端的参数**与**写回会话缓存的字段**。

为什么需要（本机没有 nicegui / httpx，这些问题只有真跑一次才暴露）：
    1. 「立即运行」默认值写错（或读反）→ 用户以为创建后就在跑，实际停着，
       页面上看不出任何异常；
    2. 取消勾选后仍传 running → 用户可以"创建即运行"任何策略，无法阻止；
    3. 编辑对话框状态下拉的 value 与 options 不匹配 → 下拉框显示空白，
       用户一保存就把策略状态改成别的值（静默改数据）；
    4. 状态切换后不刷新 update_time → 监控页的「已运行」从上次启动起算，越显示越长；
    5. 后端已回填 status（例如启动失败被判为 error）时被前端选择覆盖 → 掩盖故障。

运行：python tests/strategies_dialog_smokecheck.py     （exit 0 = 通过）
反向验证：把页面改坏后另存一份，用
    SMOKE_STRATEGIES_SRC=<改坏副本> python tests/strategies_dialog_smokecheck.py
跑一遍，断言必须报错 —— 否则说明断言是空的。
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import types
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE_SRC = pathlib.Path(
    os.environ.get("SMOKE_STRATEGIES_SRC", str(ROOT / "pages" / "strategies.py"))
)

logging.disable(logging.CRITICAL)

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


class _Node:
    """极简元素桩：只保留检查需要的属性。

    ``value`` 与 ``text`` 直接放实例属性上（不是 kw 里的死值），
    因为检查要像真实控件那样读/写 ``.value``。
    """

    def __init__(self, kind: str, **kw: Any) -> None:
        self.kind: str = kind
        self.kw: dict[str, Any] = kw
        self.children: list[_Node] = []
        self.classes_list: list[str] = []
        self.styles: list[str] = []
        self.props_list: list[str] = []
        self.handlers: dict[str, list[Any]] = {}
        self.value_change_handlers: list[Any] = []
        self.disabled: bool = False
        self.value: Any = kw.get("value")
        self.opened: bool = False
        self.closed: bool = False

    # 上下文协议：NiceGUI 创建元素时即挂到当前父节点，with 只是压栈
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

    def on_value_change(self, handler: Any = None, **kw: Any) -> "_Node":
        self.value_change_handlers.append(handler)
        return self

    def set_value(self, value: Any) -> "_Node":
        self.value = value
        return self

    def disable(self) -> "_Node":
        self.disabled = True
        return self

    def clear(self) -> "_Node":
        self.children.clear()
        return self

    def open(self) -> "_Node":
        self.opened = True
        return self

    def close(self) -> "_Node":
        self.closed = True
        return self

    def walk(self) -> list["_Node"]:
        out: list[_Node] = [self]
        for child in self.children:
            out.extend(child.walk())
        return out

    def style_text(self) -> str:
        return " ".join(self.styles)

    def class_text(self) -> str:
        return " ".join(self.classes_list)

    def text(self) -> str:
        return str(self.kw.get("text", ""))


def _make(kind: str, **kw: Any) -> _Node:
    """创建一个元素并挂到当前父节点下（与 NiceGUI 语义一致）。"""
    node = _Node(kind, **kw)
    if _STACK:
        _STACK[-1].children.append(node)
    return node


_ADDED_CSS: list[str] = []
_NOTIFY: list[tuple[str, str]] = []
_NAVIGATED: list[str] = []


def _build_nicegui_stub() -> types.ModuleType:
    """构造 nicegui 模块桩。"""
    mod = types.ModuleType("nicegui")
    ui = types.ModuleType("nicegui.ui")

    ui.add_css = lambda css, shared=False: _ADDED_CSS.append(str(css))
    ui.column = lambda *a, **k: _make("column")
    ui.row = lambda *a, **k: _make("row")

    def _card(*a: Any, **k: Any) -> _Node:
        return _make("card")

    ui.card = _card
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.space = lambda *a, **k: _make("space")
    ui.element = lambda tag="div", *a, **k: _make(f"element:{tag}")
    ui.dialog = lambda *a, **k: _make("dialog")
    ui.tabs = lambda *a, **k: _make("tabs")
    ui.tab = lambda *a, **k: _make("tab", text=str(a[0]) if a else "")
    ui.tab_panels = lambda *a, **k: _make("tab_panels")
    ui.tab_panel = lambda *a, **k: _make("tab_panel", text=str(a[0]) if a else "")
    ui.scroll_area = lambda *a, **k: _make("scroll_area")

    def _button(*args: Any, **kw: Any) -> _Node:
        """ui.button 的第一个位置参数是文案；图标按钮则只有 icon=。

        真实 NiceGUI 用 on_click= 绑定点击；桩必须把它登记进 handlers，
        否则"点了没反应"会被误判成页面 bug。
        """
        text: str = str(args[0]) if args else ""
        node: _Node = _make("button", text=text, icon=str(kw.get("icon", "")))
        on_click = kw.get("on_click")
        if on_click is not None:
            node.handlers.setdefault("click", []).append(on_click)
        return node

    ui.button = _button

    def _input(*a: Any, **k: Any) -> _Node:
        return _make("input", value=k.get("value"), placeholder=str(k.get("placeholder", "")))

    def _number(*a: Any, **k: Any) -> _Node:
        return _make("number", value=k.get("value"))

    def _select(*a: Any, **k: Any) -> _Node:
        return _make("select", options=k.get("options"), value=k.get("value"))

    def _checkbox(*a: Any, **k: Any) -> _Node:
        return _make("checkbox", text=str(a[0]) if a else "", value=k.get("value", False))

    ui.input = _input
    ui.number = _number
    ui.select = _select
    ui.checkbox = _checkbox

    def _notify(message: str = "", **kw: Any) -> None:
        _NOTIFY.append((str(message), str(kw.get("type", ""))))

    ui.notify = _notify
    ui.timer = lambda *a, **k: _make("timer")
    ui.navigate = types.SimpleNamespace(to=lambda url: _NAVIGATED.append(str(url)))

    mod.ui = ui
    mod.app = types.ModuleType("nicegui.app")
    mod.app.storage = types.SimpleNamespace(user={})
    mod.app.is_started = True
    return mod


# ------------------------------------------------------- 其余依赖桩 + 假数据

TEMPLATE_ID: str = "tplA"
POOL_ID: str = "pool1"

# 策略模板：default_params 只放**数字**参数，避免参数页额外生成文本输入框
# （检查要按 kind=="input" 唯一定位「自定义名称」输入框）
TEMPLATES: list[dict[str, Any]] = [
    {
        "_id": TEMPLATE_ID,
        "name": "强势反弹策略",
        "strategy_type": "选股策略",
        "description": "放量突破后回踩买入",
        "default_params": {"match": {"limit": {"desc": "选股数量", "value": 10}}},
    },
]

POOLS: list[dict[str, Any]] = [{"_id": POOL_ID, "name": "沪深300成分"}]

# 用户策略缓存：editing 的目标就是它
USER_STRATEGIES: list[dict[str, Any]] = [
    {
        "_id": "us1",
        "strategy_id": TEMPLATE_ID,
        "name": "我的反弹策略",
        "status": "stopped",
        "pool_id": POOL_ID,
        "initial_amount": 100000.0,
        "max_stock_count": 4,
        "strategy_params": {},
        "total_profit": 0.0,
    },
]

CREATE_CALLS: list[dict[str, Any]] = []
UPDATE_CALLS: list[dict[str, Any]] = []
CACHE_WRITES: list[tuple[str, dict[str, Any]]] = []
# 会话级缓存的**真实**内容：桩的 update_user_strategy 会真合并进来，
# 页面随后再从它读回来（见 _UserStrategyManagerStub 的说明）
SESSION_CACHE: list[dict[str, Any]] = []
# 创建接口的返回值：默认**不回填 status**（用来验证前端的兜底分支）
CREATE_RESPONSE: list[dict[str, Any]] = [{"_id": "usnew"}]
# 最新执行记录：默认无持仓（不影响前 9 节），第 10 节临时灌入真实持仓
LATEST_EXECUTION: list[dict[str, Any]] = [{}]
# 实时行情：默认空（页面此时回退到持仓自带的 current_price）
REAL_TIME: list[dict[str, Any]] = []


class _UserStrategyApiStub:
    """user_strategy_api 桩：记录调用参数。"""

    def list(self) -> list[dict[str, Any]]:
        return [dict(u) for u in USER_STRATEGIES]

    def create(self, **kw: Any) -> dict[str, Any]:
        CREATE_CALLS.append(dict(kw))
        return dict(CREATE_RESPONSE[0])

    def update(self, **kw: Any) -> str:
        UPDATE_CALLS.append(dict(kw))
        return "ok"

    def get_latest_execution(self, user_strategy_id: str) -> dict[str, Any]:
        return dict(LATEST_EXECUTION[0])

    def get_runlog(self, user_strategy_id: str, skip: int = 0, limit: int = 0) -> list[Any]:
        return []


class _StrategyApiStub:
    def list(self) -> list[dict[str, Any]]:
        return [dict(t) for t in TEMPLATES]


class _PoolApiStub:
    def list(self) -> list[dict[str, Any]]:
        return [dict(p) for p in POOLS]


class _EmptyApiStub:
    """market_api / order_api / stock_info_api / signal_manager 的通用替身。"""

    def get_real_time(self, **kw: Any) -> list[Any]:
        # 持仓信息页的「现价」列走这里；返回 REAL_TIME 的内容
        return [dict(h) for h in REAL_TIME]

    def get_latest(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    def get_by_codes(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    def get_latest_signals(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {}


class _ThemeStub:
    """与真实 ThemeManager 的取色 API 形状一致。"""

    def var(self, name: str) -> str:
        return f"var(--qs-{name})"

    def var_rgba(self, name: str, alpha: float) -> str:
        return f"rgba(var(--qs-{name}-rgb), {alpha})"


class _StrategyCacheStub:
    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {t["_id"]: t for t in TEMPLATES if t["_id"] in ids}

    def get(self, strategy_id: str) -> dict[str, Any]:
        for t in TEMPLATES:
            if t["_id"] == strategy_id:
                return dict(t)
        return {}


class _AppContextStub:
    theme_manager = _ThemeStub()
    user_strategy_api = _UserStrategyApiStub()
    strategy_api = _StrategyApiStub()
    pool_api = _PoolApiStub()
    strategy_cache = _StrategyCacheStub()
    market_api = _EmptyApiStub()
    order_api = _EmptyApiStub()
    stock_info_api = _EmptyApiStub()
    signal_manager = _EmptyApiStub()


class _UserStrategyManagerStub:
    """会话级缓存的替身：**如实复刻真实现的浅合并语义**。

    ⚠ 只记录写入是不够的。真实现 ``update_user_strategy`` 会把传入的
    ``strategy_info`` 浅合并进缓存的用户策略对象（``merged.update(info)``），
    而页面紧接着又会 ``_load_rows()`` 把这些字段读回来重新渲染。

    桩若只 append 到记录表，这条「写缓存 → 重读 → 重渲染」的回路就**完全没被覆盖**，
    于是漏掉了真 bug：编辑保存时把展示用的行数据整份合并进缓存，
    ``total_profit`` 从数值 0.0 被覆盖成字符串 "0.0%"，下一次
    ``_load_rows()`` 里 ``float("0.0%")`` 抛 ValueError，
    表格容器已 clear 而分页还留着旧文案 ⇒ 页面看着「什么都不显示了」。
    """

    def __init__(self) -> None:
        pass

    def list_user_strategies(self) -> list[dict[str, Any]]:
        return [dict(u) for u in SESSION_CACHE]

    def update_user_strategy(self, user_strategy_id: str, info: dict[str, Any]) -> None:
        CACHE_WRITES.append((str(user_strategy_id), dict(info)))
        target: str = str(user_strategy_id)
        for i, strategy in enumerate(SESSION_CACHE):
            if str(strategy.get("_id", strategy.get("id", ""))) == target:
                merged: dict[str, Any] = dict(strategy)
                merged.update(info)
                SESSION_CACHE[i] = merged
                return
        SESSION_CACHE.append(dict(info))


def _install_stubs() -> None:
    """把桩模块注入 sys.modules。"""
    sys.modules["nicegui"] = _build_nicegui_stub()

    # 本机没装 httpx（页面用它捕获 HTTPStatusError），必须桩掉
    httpx = types.ModuleType("httpx")

    class HTTPStatusError(Exception):
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a)
            self.response = types.SimpleNamespace(status_code=0)

    httpx.HTTPStatusError = HTTPStatusError
    sys.modules["httpx"] = httpx

    ac = types.ModuleType("app_context")
    ac.AppContext = _AppContextStub
    sys.modules["app_context"] = ac

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["core"] = core_pkg

    # core.routes 无外部依赖，直接 exec 真源码（页面用它构造回测页 URL）
    routes = types.ModuleType("core.routes")
    exec(compile((ROOT / "core" / "routes.py").read_text(encoding="utf-8"),
                 "core/routes.py", "exec"), routes.__dict__)
    sys.modules["core.routes"] = routes

    # 用户策略管理器：真实现依赖 nicegui.app.storage 的会话语义，这里用替身
    usm = types.ModuleType("core.user_strategy_manager")
    usm.UserStrategyManager = _UserStrategyManagerStub
    sys.modules["core.user_strategy_manager"] = usm

    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = [str(ROOT / "utils")]
    sys.modules["utils"] = utils_pkg

    # timeutils 无外部依赖，exec 真源码（update_time 的格式由它决定）
    timeutils = types.ModuleType("utils.timeutils")
    exec(compile((ROOT / "utils" / "timeutils.py").read_text(encoding="utf-8"),
                 "utils/timeutils.py", "exec"), timeutils.__dict__)
    sys.modules["utils.timeutils"] = timeutils

    # utils.tools：**exec 真源码**，这样 to_float 的解析语义（"12.5%" -> 12.5）
    # 是被真代码验证过的，而不是靠桩自证。只把依赖配置文件的 load_strategy_type
    # 换成固定值，避免检查结果受本机 cfg 影响。
    tools = types.ModuleType("utils.tools")
    exec(compile((ROOT / "utils" / "tools.py").read_text(encoding="utf-8"),
                 "utils/tools.py", "exec"), tools.__dict__)
    tools.load_strategy_type = lambda: ["选股策略", "盯盘策略", "复盘策略"]
    sys.modules["utils.tools"] = tools


def _load_page_module() -> types.ModuleType:
    """exec 页面真源码拿到模块对象（不 import，避免触发真实依赖链）。"""
    module = types.ModuleType("strategies_under_test")
    module.__dict__["__file__"] = str(PAGE_SRC)
    exec(compile(PAGE_SRC.read_text(encoding="utf-8"), str(PAGE_SRC), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------ 断言工具

def _reset_tree() -> _Node:
    _STACK.clear()
    root = _Node("root")
    _STACK.append(root)
    return root


def _find(root: _Node, predicate: Any) -> list[_Node]:
    return [n for n in root.walk() if predicate(n)]


def _click(node: _Node, event: str = "click") -> None:
    for handler in node.handlers.get(event, []):
        handler()


def _dialogs(root: _Node) -> list[_Node]:
    return _find(root, lambda n: n.kind == "dialog")


def _button(node: _Node, text: str) -> _Node | None:
    return next((n for n in node.walk() if n.kind == "button" and n.text() == text), None)


def _icon_button(node: _Node, icon: str) -> _Node | None:
    return next(
        (n for n in node.walk() if n.kind == "button" and n.kw.get("icon") == icon), None
    )


def _checkboxes(node: _Node) -> list[_Node]:
    return [n for n in node.walk() if n.kind == "checkbox"]


def _select_by_options(node: _Node, options_type: type) -> _Node | None:
    """按 options 的类型定位下拉框：dict=状态（值→文案），list=股票池（名称列表）。"""
    return next(
        (n for n in node.walk()
         if n.kind == "select" and isinstance(n.kw.get("options"), options_type)),
        None,
    )


def _open_page(page: types.ModuleType) -> _Node:
    root = _reset_tree()
    page.show_strategies_page()
    return root


def _last_cache_write(us_id: str) -> dict[str, Any]:
    """取针对某条用户策略的最后一次缓存写入。"""
    for written_id, info in reversed(CACHE_WRITES):
        if written_id == us_id:
            return info
    return {}


def _reset_session_cache() -> None:
    """把会话缓存与写入记录都复位到初始假数据。

    每节独立开跑，避免上一节写进缓存的字段影响下一节（桩现在会真合并）。
    """
    SESSION_CACHE.clear()
    SESSION_CACHE.extend(dict(u) for u in USER_STRATEGIES)
    CACHE_WRITES.clear()


# ------------------------------------------------------------------ 主流程

def main() -> int:
    _install_stubs()
    page = _load_page_module()
    _reset_session_cache()

    # ---------- 1) 状态文案单一来源 ----------
    print("=" * 72)
    print("1) 状态选项：文案取自 _STATUS_STYLES（单一来源）")
    print("=" * 72)
    check("可选状态为 运行中/已暂停/已停止（按 running,paused,stopped 顺序）",
          list(page._EDITABLE_STATUS_TEXTS.items())
          == [("running", "运行中"), ("paused", "已暂停"), ("stopped", "已停止")],
          str(page._EDITABLE_STATUS_TEXTS))
    check("「异常」不作为用户可选项（后端故障态）",
          "error" not in page._EDITABLE_STATUS_TEXTS,
          str(sorted(page._EDITABLE_STATUS_TEXTS)))
    check("文案与 _STATUS_STYLES 一致（不重复维护）",
          all(page._EDITABLE_STATUS_TEXTS[k] == page._STATUS_STYLES[k][0]
              for k in page._EDITABLE_STATUS_TEXTS),
          str(page._STATUS_STYLES))

    # ---------- 2) 创建对话框：默认立即运行 ----------
    print()
    print("=" * 72)
    print("2) 创建对话框：「立即运行」默认勾选 → status=running")
    print("=" * 72)
    CREATE_CALLS.clear()
    _reset_session_cache()
    root = _open_page(page)
    check("策略列表页渲染未抛异常", True)

    create_btn = _button(root, "创建策略")
    check("存在「创建策略」按钮", create_btn is not None)
    if create_btn is None:
        return _report()

    _click(create_btn)
    dialogs = _dialogs(root)
    check("点击后打开创建对话框", len(dialogs) == 1, str(len(dialogs)))
    if not dialogs:
        return _report()
    dlg = dialogs[0]

    run_now = next((n for n in _checkboxes(dlg) if n.text() == "立即运行"), None)
    check("创建对话框有「立即运行」勾选框", run_now is not None,
          str([n.text() for n in _checkboxes(dlg)]))
    if run_now is None:
        return _report()
    check("「立即运行」默认勾选（value=True）", run_now.value is True, repr(run_now.value))

    # 勾选策略模板卡片（点击卡片本身，勾选框是 pointer-events:none 的展示件）
    cards = _find(dlg, lambda n: n.kind == "card" and "click" in n.handlers)
    check("创建对话框渲染出可点击的策略模板卡片", len(cards) == 1, str(len(cards)))
    if len(cards) != 1:
        return _report()
    _click(cards[0])

    name_inputs = _find(dlg, lambda n: n.kind == "input")
    check("创建对话框有且只有一个文本输入框（自定义名称）", len(name_inputs) == 1,
          str(len(name_inputs)))
    if len(name_inputs) != 1:
        return _report()
    name_inputs[0].value = "我的新策略"

    save_btn = _button(dlg, "保存")
    check("创建对话框有「保存」按钮", save_btn is not None)
    if save_btn is None:
        return _report()
    _click(save_btn)

    check("默认勾选时 create 以 status=running 提交",
          bool(CREATE_CALLS) and CREATE_CALLS[-1].get("status") == "running",
          str(CREATE_CALLS[-1].get("status") if CREATE_CALLS else None))
    check("create 提交的是所选模板 ID 与股票池 ID",
          bool(CREATE_CALLS)
          and CREATE_CALLS[-1].get("strategy_id") == TEMPLATE_ID
          and CREATE_CALLS[-1].get("pool_id") == POOL_ID,
          f"{CREATE_CALLS[-1].get('strategy_id')} / {CREATE_CALLS[-1].get('pool_id')}"
          if CREATE_CALLS else "无调用")
    check("create 提交的是用户填的自定义名称",
          bool(CREATE_CALLS) and CREATE_CALLS[-1].get("name") == "我的新策略",
          str(CREATE_CALLS[-1].get("name") if CREATE_CALLS else None))
    check("对话框保存后关闭", dlg.closed)
    check("创建成功给出提示（并说明已开始运行）",
          any("已开始运行" in m for m, _ in _NOTIFY), str([m for m, _ in _NOTIFY]))

    # 后端未回填 status ⇒ 前端兜底写入，否则列表会把新策略显示成「已停止」
    written = _last_cache_write("usnew")
    check("后端未回填 status 时前端兜底写入 running",
          written.get("status") == "running", str(written.get("status")))
    check("以 running 创建时就地写入 update_time（监控页「已运行」的起算点）",
          bool(written.get("update_time")), str(written.get("update_time")))

    # ---------- 3) 取消勾选 → stopped ----------
    print()
    print("=" * 72)
    print("3) 创建对话框：取消「立即运行」→ status=stopped")
    print("=" * 72)
    CREATE_CALLS.clear()
    _reset_session_cache()
    root_b = _open_page(page)
    _click(_button(root_b, "创建策略"))
    dlg_b = _dialogs(root_b)[0]
    run_now_b = next(n for n in _checkboxes(dlg_b) if n.text() == "立即运行")
    run_now_b.value = False
    _click(_find(dlg_b, lambda n: n.kind == "card" and "click" in n.handlers)[0])
    _find(dlg_b, lambda n: n.kind == "input")[0].value = "我的新策略2"
    _click(_button(dlg_b, "保存"))
    check("取消勾选时 create 以 status=stopped 提交",
          bool(CREATE_CALLS) and CREATE_CALLS[-1].get("status") == "stopped",
          str(CREATE_CALLS[-1].get("status") if CREATE_CALLS else None))
    written_b = _last_cache_write("usnew")
    check("stopped 创建不写 update_time（没有「运行中」可起算）",
          "update_time" not in written_b or not written_b.get("update_time"),
          str(written_b.get("update_time")))
    check("stopped 创建的提示不带「已开始运行」",
          any(m == "创建成功" for m, _ in _NOTIFY), str([m for m, _ in _NOTIFY]))

    # ---------- 4) 后端已回填 status 时不覆盖 ----------
    print()
    print("=" * 72)
    print("4) 后端已回填 status（如启动失败被判 error）时不覆盖")
    print("=" * 72)
    CREATE_CALLS.clear()
    _reset_session_cache()
    CREATE_RESPONSE[0] = {"_id": "usnew", "status": "error"}
    root_c = _open_page(page)
    _click(_button(root_c, "创建策略"))
    dlg_c = _dialogs(root_c)[0]
    _click(_find(dlg_c, lambda n: n.kind == "card" and "click" in n.handlers)[0])
    _find(dlg_c, lambda n: n.kind == "input")[0].value = "我的新策略3"
    _click(_button(dlg_c, "保存"))
    check("提交时仍是前端选定的 running",
          bool(CREATE_CALLS) and CREATE_CALLS[-1].get("status") == "running",
          str(CREATE_CALLS[-1].get("status") if CREATE_CALLS else None))
    check("缓存里保留后端返回的 error（不被前端选择覆盖）",
          _last_cache_write("usnew").get("status") == "error",
          str(_last_cache_write("usnew").get("status")))
    CREATE_RESPONSE[0] = {"_id": "usnew"}

    # ---------- 5) 编辑对话框：状态下拉 ----------
    print()
    print("=" * 72)
    print("5) 编辑对话框：状态下拉的选项与默认值")
    print("=" * 72)
    UPDATE_CALLS.clear()
    _reset_session_cache()
    root_e = _open_page(page)
    edit_btn = _icon_button(root_e, "edit")
    check("操作列有「编辑」图标按钮", edit_btn is not None)
    if edit_btn is None:
        return _report()
    _click(edit_btn)
    dialogs_e = _dialogs(root_e)
    check("点击后打开编辑对话框", len(dialogs_e) == 1, str(len(dialogs_e)))
    if not dialogs_e:
        return _report()
    edlg = dialogs_e[0]
    status_select = _select_by_options(edlg, dict)
    check("编辑对话框有状态下拉框（options 为 值→文案 的 dict）", status_select is not None)
    if status_select is None:
        return _report()
    check("状态下拉默认选中当前状态（stopped）",
          status_select.value == "stopped", repr(status_select.value))
    check("状态下拉的选项就是三个可切换状态",
          dict(status_select.kw.get("options") or {})
          == {"running": "运行中", "paused": "已暂停", "stopped": "已停止"},
          str(status_select.kw.get("options")))
    check("状态下拉的默认值一定在选项里（否则显示空白）",
          status_select.value in dict(status_select.kw.get("options") or {}),
          repr(status_select.value))
    check("编辑对话框不渲染模板选择卡片（模板不可更换）",
          not _find(edlg, lambda n: n.kind == "card" and "click" in n.handlers))

    # ---------- 6) 编辑保存：状态落库 + 缓存同步 ----------
    print()
    print("=" * 72)
    print("6) 编辑保存：status 提交后端，缓存同步 status 与 update_time")
    print("=" * 72)
    status_select.value = "paused"
    _click(_button(edlg, "保存"))
    check("update 带上了新状态 paused",
          bool(UPDATE_CALLS) and UPDATE_CALLS[-1].get("status") == "paused",
          str(UPDATE_CALLS[-1].get("status") if UPDATE_CALLS else None))
    check("update 仍带上名称 / 股票池 / 资金等原有字段",
          bool(UPDATE_CALLS)
          and UPDATE_CALLS[-1].get("user_strategy_id") == "us1"
          and UPDATE_CALLS[-1].get("pool_id") == POOL_ID
          and UPDATE_CALLS[-1].get("name") == "我的反弹策略",
          str({k: UPDATE_CALLS[-1].get(k) for k in
               ("user_strategy_id", "pool_id", "name")} if UPDATE_CALLS else None))
    written_e = _last_cache_write("us1")
    check("会话缓存同步为 paused", written_e.get("status") == "paused",
          str(written_e.get("status")))
    check("状态变更时就地刷新 update_time",
          bool(written_e.get("update_time")), str(written_e.get("update_time")))
    check("切换状态后给出明确提示",
          any("状态已切换为" in m and "已暂停" in m for m, _ in _NOTIFY),
          str([m for m, _ in _NOTIFY]))
    check("编辑对话框保存后关闭", edlg.closed)

    # ---------- 7) 状态未变时不刷新 update_time ----------
    print()
    print("=" * 72)
    print("7) 状态未变时：不改写 update_time（无意义的缓存改写）")
    print("=" * 72)
    UPDATE_CALLS.clear()
    _reset_session_cache()
    root_f = _open_page(page)
    _click(_icon_button(root_f, "edit"))
    edlg_f = _dialogs(root_f)[0]
    sel_f = _select_by_options(edlg_f, dict)
    check("下拉默认值仍是当前状态 stopped", sel_f.value == "stopped", repr(sel_f.value))
    _click(_button(edlg_f, "保存"))
    written_f = _last_cache_write("us1")
    check("update 仍会带 status（幂等提交，后端自行判断）",
          bool(UPDATE_CALLS) and UPDATE_CALLS[-1].get("status") == "stopped",
          str(UPDATE_CALLS[-1].get("status") if UPDATE_CALLS else None))
    check("状态没变 ⇒ 不写 update_time",
          not written_f.get("update_time"), str(written_f.get("update_time")))

    # ---------- 8) 当前状态为「异常」时的兜底 ----------
    print()
    print("=" * 72)
    print("8) 当前状态为「异常」：补进选项，避免下拉框空白")
    print("=" * 72)
    saved = USER_STRATEGIES[0]["status"]
    try:
        USER_STRATEGIES[0]["status"] = "error"
        # 桩现在会真合并会话缓存、页面也从缓存读 ⇒ 改完假数据要重新灌一次
        # （否则页面仍看到上一节留下的 status=stopped）
        _reset_session_cache()
        root_g = _open_page(page)
        _click(_icon_button(root_g, "edit"))
        edlg_g = _dialogs(root_g)[0]
        sel_g = _select_by_options(edlg_g, dict)
        options_g = dict(sel_g.kw.get("options") or {})
        check("「异常」被临时补进选项",
              options_g.get("error") == "异常", str(options_g))
        check("默认值仍等于当前状态（不是空白）",
              sel_g.value == "error", repr(sel_g.value))
        check("三个可切换状态没有被挤掉",
              all(k in options_g for k in ("running", "paused", "stopped")),
              str(sorted(options_g)))
        UPDATE_CALLS.clear()
        sel_g.value = "running"
        _click(_button(edlg_g, "保存"))
        check("可从「异常」切换到 running 并落库",
              bool(UPDATE_CALLS) and UPDATE_CALLS[-1].get("status") == "running",
              str(UPDATE_CALLS[-1].get("status") if UPDATE_CALLS else None))
    finally:
        USER_STRATEGIES[0]["status"] = saved

    # ---------- 9) 编辑保存后的「写缓存 → 重读 → 重渲染」回路 ----------
    # 这一节守的是一个真实线上 bug：编辑保存时把**展示行数据**整份合并进会话缓存，
    # 缓存的 total_profit 由数值 0.0 被覆盖成字符串 "0.0%"，下一次 _load_rows()
    # float("0.0%") 抛 ValueError ⇒ 表格容器已 clear 而渲染中断 ⇒
    # 页面表现成「列表什么都不显示了」（分页还留着上一次的旧文案）。
    print()
    print("=" * 72)
    print("9) 编辑保存后列表不能空白（展示行数据 vs 缓存形状冲突）")
    print("=" * 72)
    UPDATE_CALLS.clear()
    _reset_session_cache()
    root_h = _open_page(page)
    rows_before = _find(root_h, lambda n: "s-table-row" in n.class_text())
    check("初始渲染出表格行", len(rows_before) == 1, str(len(rows_before)))

    _click(_icon_button(root_h, "edit"))
    edlg_h = _dialogs(root_h)[0]
    _select_by_options(edlg_h, dict).value = "running"
    _click(_button(edlg_h, "保存"))

    written_h = _last_cache_write("us1")
    # row 里的展示专用键：写进会话缓存会污染缓存形状（total_profit 尤其致命）
    display_only: list[str] = [
        "total_profit", "profit_color", "stype", "description",
        "_signal_text", "_signal_color", "_status_text", "_status_color",
        "_status_bg", "_desc", "sid", "us_id", "custom_name",
    ]
    leaked: list[str] = [k for k in display_only if k in written_h]
    check("缓存写入不含展示专用键（只写业务字段）", not leaked, str(leaked))
    check("缓存写入覆盖全部业务字段",
          {"name", "pool_id", "status", "initial_amount",
           "max_stock_count", "strategy_params"} <= set(written_h),
          str(sorted(written_h)))
    check("缓存里的 total_profit 仍是数值（没被 '0.0%' 覆盖）",
          isinstance(SESSION_CACHE[0].get("total_profit"), (int, float)),
          repr(SESSION_CACHE[0].get("total_profit")))

    rows_after = _find(root_h, lambda n: "s-table-row" in n.class_text())
    check("保存后重新渲染：表格行仍在（页面没有空白）",
          len(rows_after) == 1, str(len(rows_after)))
    labels_after = [n.text() for n in _find(root_h, lambda n: n.kind == "label")]
    check("总收益仍按百分比展示", "0.0%" in labels_after,
          str([t for t in labels_after if t.endswith("%")]))
    check("行内状态文案已切到「运行中」", "运行中" in labels_after,
          str([t for t in labels_after if t in ("运行中", "已暂停", "已停止", "异常")]))

    # 兼容历史脏缓存：旧版本已经把 "12500.0%" 这类展示字符串写进了会话缓存，
    # 用户不刷新会话就仍会读到它 —— 容错解析必须能算回数值而不是整页炸掉
    SESSION_CACHE[0]["total_profit"] = "12500.0%"
    root_i = _open_page(page)
    check("会话缓存里是脏字符串时不炸（容错解析）",
          len(_find(root_i, lambda n: "s-table-row" in n.class_text())) == 1)
    labels_i = [n.text() for n in _find(root_i, lambda n: n.kind == "label")]
    check("脏字符串按数值解析后重算收益率（12500/100000*100=12.5%）",
          "12.5%" in labels_i, str([t for t in labels_i if t.endswith("%")]))

    # ---------- 10) 持仓信息「盈亏」列：率(额) 格式 + 口径 ----------
    # 需求（2026-09-22）：盈亏列要同时给出百分率与金额，形如 +10.00%(2000.00)。
    # 口径上两者都用**实时价**重算，不取 PositionItem 的 profit_rate/profit_amount
    # 快照 —— 因为本页「现价」列已经用实时价覆盖，若盈亏取快照会出现
    # 「现价是新的、盈亏是旧的」这种同一行自相矛盾。下面的假数据把快照值
    # 故意写成离谱的数字，用来钉住"必须忽略快照"这一点。
    print()
    print("=" * 72)
    print("10) 持仓信息「盈亏」列：展示 收益率(收益额)")
    print("=" * 72)
    saved_execution = LATEST_EXECUTION[0]
    saved_real_time = list(REAL_TIME)
    LATEST_EXECUTION[0] = {
        "positions": [
            # 盈利：成本 10.00 → 实时价 12.00 ⇒ +20.00%；量 1000 ⇒ 2000.00
            # 快照字段故意填成不相干的 88.88 / 7777.0 / 999.0
            {"code": "600519", "name": "贵州茅台", "quantity": 1000,
             "cost_price": 10.0, "current_price": 999.0,
             "profit_rate": 88.88, "profit_amount": 7777.0},
            # 亏损：成本 10.00 → 实时价 9.00 ⇒ -10.00%；量 500 ⇒ 500.00
            {"code": "000001", "name": "平安银行", "quantity": 500,
             "cost_price": 10.0, "current_price": 111.0,
             "profit_rate": -66.66, "profit_amount": -8888.0},
            # 成本价为 0：收益率无定义，不能除零
            {"code": "300750", "name": "宁德时代", "quantity": 100,
             "cost_price": 0.0, "current_price": 0.0},
        ]
    }
    REAL_TIME[:] = [
        {"code": "600519", "price": 12.0},
        {"code": "000001", "price": 9.0},
        {"code": "300750", "price": 5.0},
    ]
    try:
        _reset_session_cache()
        root_j = _open_page(page)
        row_j = _find(root_j, lambda n: "s-table-row" in n.class_text())[0]
        _click(row_j)  # 点击整行 → 展开详情面板（默认停在「持仓信息」标签页）

        labels_j = _find(root_j, lambda n: n.kind == "label")
        texts_j = [n.text() for n in labels_j]
        check("详情面板渲染出「盈亏」表头", "盈亏" in texts_j,
              str([t for t in texts_j if t == "盈亏"]))
        check("持仓行按实时价渲染出「现价」", texts_j.count("12.00") == 1,
              str([t for t in texts_j if t in ("9.00", "12.00", "5.00")]))

        profit_j = [n for n in labels_j if "(" in n.text()]
        profit_texts = [n.text() for n in profit_j]
        check("盈利行展示 +20.00%(2000.00)（与需求示例同形）",
              "+20.00%(2000.00)" in profit_texts, str(profit_texts))
        check("亏损行展示 -10.00%(500.00)（金额取绝对值，正负号由百分率承担）",
              "-10.00%(500.00)" in profit_texts, str(profit_texts))
        check("成本价为 0 时不除零，收益率退化为 +0.00%（金额仍按价差算）",
              "+0.00%(500.00)" in profit_texts, str(profit_texts))

        # 快照字段必须被忽略：取快照则会显示 999.00 / 88.88% / 7777.00
        check("盈亏不取 PositionItem 的快照字段（profit_rate/profit_amount）",
              not any(t in texts_j for t in ("88.88", "-66.66", "7777.00", "-8888.00")),
              str([t for t in texts_j if t in ("88.88", "-66.66", "7777.00", "-8888.00")]))
        check("现价取实时价而非持仓快照的 current_price",
              "999.00" not in texts_j and "111.00" not in texts_j,
              str([t for t in texts_j if t in ("999.00", "111.00")]))

        by_text = {n.text(): n for n in profit_j}
        gain_style = by_text["+20.00%(2000.00)"].style_text() if "+20.00%(2000.00)" in by_text else ""
        loss_style = by_text["-10.00%(500.00)"].style_text() if "-10.00%(500.00)" in by_text else ""
        check("盈利行为红色（A 股惯例 positive）",
              "var(--qs-positive)" in gain_style, gain_style)
        check("亏损行为绿色（A 股惯例 negative）",
              "var(--qs-negative)" in loss_style, loss_style)
        check("盈亏列禁止折行（「率(额)」文案近乎翻倍）",
              "white-space: nowrap" in gain_style, gain_style)

        # 缺实时价（停牌 / 行情接口失败）：现价与盈亏一并显示 "--"，**不得**退回持仓自带的
        # current_price —— 它与 profit_* 同属成交时点写入，大概率是买入时的价，
        # 拿它算出的数字看着像实时、其实是建仓当刻的收益。
        REAL_TIME.clear()
        root_nq = _open_page(page)
        row_nq = _find(root_nq, lambda n: "s-table-row" in n.class_text())[0]
        _click(row_nq)
        labels_nq = _find(root_nq, lambda n: n.kind == "label")
        texts_nq = [n.text() for n in labels_nq]
        # 每行的右对齐列依次是 成本价 / 现价 / 盈亏；表头这三列同样右对齐，
        # 故取尾部 9 项（= 3 行数据）
        right_cols = [
            n.text() for n in labels_nq if "text-align: right" in n.style_text()
        ][-9:]
        check("缺实时价时：成本价照常显示，现价与盈亏均为 --",
              right_cols == ["10.00", "--", "--", "10.00", "--", "--", "0.00", "--", "--"],
              str(right_cols))
        check("缺实时价时不退回持仓快照 current_price（999.00 / 111.00 不出现）",
              "999.00" not in texts_nq and "111.00" not in texts_nq,
              str([t for t in texts_nq if t in ("999.00", "111.00")]))
    finally:
        LATEST_EXECUTION[0] = saved_execution
        REAL_TIME[:] = saved_real_time

    return _report()


def _report() -> int:
    print()
    print("=" * 72)
    if _fail:
        print(f"RESULT: {len(_fail)} 项失败")
        for item in _fail:
            print(f"  - {item}")
        print("=" * 72)
        return 1
    print("RESULT: 全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())

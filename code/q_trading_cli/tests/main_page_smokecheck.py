#!/usr/bin/env python3
"""
Description: 概要页（pages/main_page.py）离线冒烟检查 —— 用桩替代 nicegui /
    app_context / core.* 等，真跑一次 _build_profit_chart、
    _build_recent_strategies_table、_build_recent_activity 与 _build_topbar，
    再断言图表数据、表格单元格文本、活动列表的策略名与通知铃铛的已读行为。

为什么需要这个脚本：
    概要页两处展示「收益率」，口径都依赖 core.holdings 的现算逻辑，而这类错误
    **不会报错**——页面照常渲染，只是数字悄悄少了一段（漏掉持仓浮盈）或多了
    一段（把成交时点快照当浮盈）。静态工具全查不出来，只有真跑一次拿到渲染结果
    才能钉住。
    通知铃铛同理：角标数错、已读状态只在内存里没落库、点开一条却把整批都标成已读，
    界面上都只是"数字不对"，不会抛异常。

关键口径（后端契约见 core/holdings.py 头部）：
    current_return_rate = current_profit / initial_amount（只含已实现）
    页面展示的当前收益率 = (current_profit + Σ(实时价 − 成本价) × 数量) / initial_amount
    资金曲线的历史点保持已实现口径，只有代表「当前」的末点叠加未实现。

通知口径（后端契约见 q_trading_server/web_api/routers/system_message.py）：
    未读数 = is_read 为假的消息条数（缺字段的历史数据按未读算）；
    已读即历史，前端按 is_read 把列表分成未读 / 历史两段；
    只有未读行挂点击handler，点开单条才调 /api/system_message/read。

运行：python tests/main_page_smokecheck.py   （exit 0 = 通过）
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
import types
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 允许用环境变量指向改写过的页面副本，用于"反向验证"：
# 把实现改坏后跑一遍，断言必须报错，否则说明断言是空的。
PAGE_SRC = pathlib.Path(
    os.environ.get("SMOKE_MAIN_PAGE_SRC", str(ROOT / "pages" / "main_page.py"))
)
# 口径模块也可替换：多数断言守的是 core.holdings 里的算法，
# 只换页面副本覆盖不到那些分支。
HOLDINGS_SRC = pathlib.Path(
    os.environ.get("SMOKE_HOLDINGS_SRC", str(ROOT / "core" / "holdings.py"))
)
# API 层只做文本断言（不 exec），但也要能被替换才能做反向验证。
API_SRC = pathlib.Path(
    os.environ.get("SMOKE_API_SRC", str(ROOT / "api" / "user_strategy.py"))
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
_ROOTS: list["_Node"] = []


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

    def on_value_change(self, handler: Any = None, **kw: Any) -> "_Node":
        """ui.select 的取值变化回调（概要页用它重画对比曲线）。"""
        self.handlers.setdefault("value_change", []).append(handler)
        return self

    def fire_value_change(self, value: Any) -> None:
        """在检查里手动触发取值变化回调。"""
        event = types.SimpleNamespace(value=value, sender=self)
        for handler in self.handlers.get("value_change", []):
            if handler is not None:
                handler(event)

    def clear(self) -> "_Node":
        self.children.clear()
        return self

    def set_text(self, text: Any) -> "_Node":
        self.kw["text"] = str(text)
        self.value = str(text)
        return self

    def set_visibility(self, visible: bool) -> "_Node":
        self.visible = visible
        return self

    def open(self) -> "_Node":
        """ui.dialog.open()：只记录已打开，界面上无对应动作。"""
        self.opened = True
        return self

    def close(self) -> None:
        """ui.dialog.close()：对话框关闭按钮的处理器。"""
        self.opened = False

    def update(self) -> None:
        self.updates = getattr(self, "updates", 0) + 1

    def walk(self) -> list["_Node"]:
        out: list[_Node] = [self]
        for child in self.children:
            out.extend(child.walk())
        return out

    def style_text(self) -> str:
        return " ".join(self.styles)

    def class_text(self) -> str:
        return " ".join(self.classes_list)


def _make(kind: str, **kw: Any) -> _Node:
    """创建一个元素并挂到当前父节点下（与 NiceGUI 语义一致）。"""
    node = _Node(kind, **kw)
    if _STACK:
        _STACK[-1].children.append(node)
    else:
        _ROOTS.append(node)
    return node


def _make_echart(options: Any = None) -> _Node:
    """EChart 桩：保留 options 字典，供断言读取 series.data。"""
    node = _make("echart")
    node.options = options if isinstance(options, dict) else {}
    node.updates = 0
    return node


_QUOTE_CALLS: list[str] = []
_EXEC_CALLS: list[str] = []
_RANGE_CALLS: list[tuple[str, str, int]] = []
_NOTIFY: list[str] = []
_NAVIGATED: list[str] = []


def _build_nicegui_stub() -> types.ModuleType:
    """构造 nicegui 模块桩。"""
    mod = types.ModuleType("nicegui")
    ui = types.ModuleType("nicegui.ui")

    ui.add_css = lambda css, shared=False: None
    ui.column = lambda *a, **k: _make("column")
    ui.row = lambda *a, **k: _make("row")
    ui.card = lambda *a, **k: _make("card")
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.badge = lambda text="", *a, **k: _make("badge", text=str(text), **k)
    ui.button = lambda *a, **k: _make("button", **k)
    ui.element = lambda tag="div", *a, **k: _make(f"element:{tag}")
    ui.space = lambda *a, **k: _make("space")
    ui.echart = lambda options=None, *a, **k: _make_echart(options)
    ui.notify = lambda message="", **kw: _NOTIFY.append(str(message))
    ui.navigate = types.SimpleNamespace(to=lambda url: _NAVIGATED.append(str(url)))
    ui.timer = lambda *a, **k: _make("timer")
    ui.dialog = lambda *a, **k: _make("dialog")
    ui.select = lambda *a, **k: _make("select", **k)
    ui.input = lambda *a, **k: _make("input", **k)
    ui.switch = lambda *a, **k: _make("switch", **k)
    ui.avatar = lambda *a, **k: _make("avatar")
    ui.separator = lambda *a, **k: _make("separator")

    class _Refreshable:
        """@ui.refreshable 桩：保持可调用 + 提供 refresh()。"""

        def __init__(self, fn: Any) -> None:
            self._fn = fn
            self.refresh_count = 0

        def __call__(self, *a: Any, **k: Any) -> Any:
            return self._fn(*a, **k)

        def refresh(self) -> Any:
            self.refresh_count += 1
            return self._fn()

    ui.refreshable = lambda fn=None: (_Refreshable(fn) if fn is not None else _Refreshable)

    napp = types.ModuleType("nicegui.app")
    napp.is_started = True
    napp.storage = types.SimpleNamespace(user={})

    mod.ui = ui
    mod.app = napp
    return mod


# ------------------------------------------------- 其余依赖模块桩 + 假数据

# 执行记录。三个策略的数据刻意错开，让每条口径各有一个"算错的取值"可对照：
#   us-1（初始 100,000）09-01 / 09-02 两条 / 09-03
#        09-02 有两条（1% 与 2%）⇒ 当天取值取**最后一条** = 2.0%。
#        「当天多条相加」会是 3.0、「取平均」会是 1.5 ⇒ 这条数据专门用来区分三种日内口径。
#        09-03 那条持仓 600000×100@10，实时价 12 ⇒ 未实现 +200
#        ⇒ 含浮盈收益率 (3,000+200)/100,000 = 3.2%
#   us-2（初始 100,000）两条都在 7 月 ⇒ **30 天窗口内没有它**
#        窗口内它只能靠「沿用最近一次取值」出现在均值里（5.0），不补这一份就少一个分母。
#        持仓 300750×200@5，行情里没有这只 ⇒ 算不出未实现 ⇒ 退化成 0.05×100 = 5.0%
#        末点若误用「窗口内记录」平均，us-2 会消失、结果从 4.4 变成 4.1
#   us-4（初始 10,000）08-10 一条（只在 30 天窗口里，用来区分 7 天 / 30 天）
#        09-03 一条已清仓（positions 空）⇒ 未实现 0 ⇒ (500+0)/10,000 = 5.0%
# 全量逐日平均值（收益为 0 的策略不计入）：07-10=4.0、07-20=5.0、08-10=3.5、
# 09-01=(1+5+2)/3=2.67、09-02=(2+5+2)/3=3.0、09-03=(3+5+5)/3=4.33
EXECUTIONS: list[dict[str, Any]] = [
    {"user_strategy_id": "us-1", "current_return_rate": 0.01, "current_profit": 1000.0,
     "initial_amount": 100000.0, "update_time": "2026-09-01 10:00:00", "positions": []},
    {"user_strategy_id": "us-1", "current_return_rate": 0.01, "current_profit": 1000.0,
     "initial_amount": 100000.0, "update_time": "2026-09-02 09:00:00", "positions": []},
    {"user_strategy_id": "us-1", "current_return_rate": 0.02, "current_profit": 2000.0,
     "initial_amount": 100000.0, "update_time": "2026-09-02 10:00:00", "positions": []},
    # 全局最新一条（update_time 最大）⇒ 曲线的窗口基准取到 09-03
    {"user_strategy_id": "us-1", "current_return_rate": 0.03, "current_profit": 3000.0,
     "initial_amount": 100000.0, "update_time": "2026-09-03 10:00:00",
     "positions": [
         # 实时价 12.00、成本 10.00、量 100 ⇒ 未实现 +200.00
         # current_price / profit_rate / profit_amount 是成交时点值，故意填成不相干的数
         {"code": "600000", "quantity": 100, "cost_price": 10.0,
          "current_price": 888.0, "profit_rate": 88.88, "profit_amount": 999999.0},
     ]},
    {"user_strategy_id": "us-2", "current_return_rate": 0.04, "current_profit": 4000.0,
     "initial_amount": 100000.0, "update_time": "2026-07-10 11:00:00",
     "positions": [{"code": "300750", "quantity": 200, "cost_price": 5.0}]},
    {"user_strategy_id": "us-2", "current_return_rate": 0.05, "current_profit": 5000.0,
     "initial_amount": 100000.0, "update_time": "2026-07-20 11:00:00",
     "positions": [{"code": "300750", "quantity": 200, "cost_price": 5.0}]},
    {"user_strategy_id": "us-4", "current_return_rate": 0.02, "current_profit": 200.0,
     "initial_amount": 10000.0, "update_time": "2026-08-10 15:00:00", "positions": []},
    {"user_strategy_id": "us-4", "current_return_rate": 0.05, "current_profit": 500.0,
     "initial_amount": 10000.0, "update_time": "2026-09-03 09:00:00", "positions": []},
]

# market_api.get_real_time 的返回值：故意不给 300750（模拟缺实时价）
REAL_TIME: list[dict[str, Any]] = [
    {"code": "600000", "price": 12.0},
]

USER_STRATEGIES: list[dict[str, Any]] = [
    {"_id": "us-1", "strategy_id": "tpl-1", "name": "策略一", "status": "running",
     "create_time": "2026-09-01 09:00:00", "initial_amount": 100000.0, "total_profit": 3000.0},
    {"_id": "us-2", "strategy_id": "tpl-1", "name": "策略二", "status": "running",
     "create_time": "2026-08-25 09:00:00", "initial_amount": 100000.0, "total_profit": 5000.0},
    {"_id": "us-3", "strategy_id": "tpl-2", "name": "策略三", "status": "stopped",
     "create_time": "2026-08-01 09:00:00", "initial_amount": 50000.0, "total_profit": 0.0},
    # us-4 已清仓：会话缓存里的 total_profit 故意写成不可解析的展示格式，
    # 用来钉住"已实现段必须取执行记录，不能取会话缓存"。
    {"_id": "us-4", "strategy_id": "tpl-2", "name": "策略四", "status": "stopped",
     "create_time": "2026-08-01 09:00:00", "initial_amount": 10000.0,
     "total_profit": "+¥9,999"},
]

TEMPLATES: dict[str, dict[str, Any]] = {
    "tpl-1": {"name": "模板一", "strategy_type": "选股策略"},
    "tpl-2": {"name": "模板二", "strategy_type": "盯盘策略"},
}

# 「近期运行状态」用的信号。三条刻意设计成能区分「按用户策略名」与「按模板名」：
#   1) us-2 与 us-1 共用模板 tpl-1（模板名 = 模板一）⇒ 显示「策略二」而不是「模板一」
#   2) user_strategy_id 不在会话缓存里（us-9）⇒ 回退模板名「模板二」
#   3) 两者都没有 ⇒ 显示 "--"
# 顺序即页面的渲染顺序（真实实现按 create_time 倒序，桩只负责给数据）。
ACTIVITY_SIGNALS: list[dict[str, Any]] = [
    {"user_strategy_id": "us-2", "strategy_id": "tpl-1", "action": "买入",
     "stock_code": "600000", "create_time": "2026-09-03 10:02:03"},
    {"user_strategy_id": "us-9", "strategy_id": "tpl-2", "action": "卖出",
     "stock_code": "000001", "create_time": "2026-09-03 09:30:00"},
    {"user_strategy_id": "", "strategy_id": "", "action": "买入",
     "stock_code": "300750", "create_time": "2026-09-03 09:00:00"},
]


# 通知铃铛用的系统消息。三条刻意设计成：
#   1) m1 明确 is_read=False ⇒ 未读
#   2) m2 **缺 is_read 字段**（后端加字段之前落库的历史数据）⇒ 必须按未读算，
#      否则老消息会被当成已读、角标永远不亮
#   3) m3 明确 is_read=True ⇒ 已读，归入历史消息
NOTIF_MESSAGES: list[dict[str, Any]] = [
    {"id": "m1", "title": "公告A", "message": "内容A",
     "create_time": "2026-09-30 10:00:00", "is_read": False},
    {"id": "m2", "title": "公告B", "message": "内容B",
     "create_time": "2026-09-29 10:00:00"},
    {"id": "m3", "title": "公告C", "message": "内容C",
     "create_time": "2026-09-28 10:00:00", "is_read": True},
]


class _SystemMessageApiStub:
    """system_message_api：查询消息 + 标记已读。

    mark_read 会真的改掉桩数据的 is_read，否则"点开单条 → 移入历史段 → 角标减一"
    这条链路的断言全是空验证（页面重画时又拿到未读的旧数据）。
    """

    def __init__(self) -> None:
        self.marked: list[str] = []
        self.messages: list[dict[str, Any]] = [dict(m) for m in NOTIF_MESSAGES]

    def reset(self) -> None:
        del self.marked[:]
        self.messages = [dict(m) for m in NOTIF_MESSAGES]

    def user_messages(self) -> list[dict[str, Any]]:
        return [dict(m) for m in self.messages]

    def mark_read(self, message_id: str) -> None:
        self.marked.append(message_id)
        for msg in self.messages:
            if str(msg.get("id", "")) == message_id:
                msg["is_read"] = True


SYSTEM_MESSAGE_API = _SystemMessageApiStub()


class _ThemeStub:
    """与真实 ThemeManager 的取色 API 形状一致。

    ``get_chart_colors`` 的返回值本检查不断言，用非字面色值占位即可 ——
    theme_selfcheck 的「应用代码不写死色值」扫描覆盖全项目（含 tests/），
    在桩里写真实十六进制色值会让那条检查误报。
    """

    def var(self, name: str) -> str:
        return f"var(--qs-{name})"

    def var_rgba(self, name: str, alpha: float) -> str:
        return f"rgba(var(--qs-{name}-rgb), {alpha})"

    def get_chart_colors(self) -> dict[str, str]:
        return {
            "axis_line": "chart-axis-line", "axis": "chart-axis", "grid": "chart-grid",
            "line": "chart-line", "line_area_top": "chart-area-top",
            "line_area_bottom": "chart-area-bottom",
            "line2": "chart-line-2", "line2_area_top": "chart-area-top-2",
            "line2_area_bottom": "chart-area-bottom-2",
        }


class _SignalManagerStub:
    def get_today_count(self) -> int:
        return 0

    def get_signals(self, limit: int = 4, only_running: bool = True) -> list[dict[str, Any]]:
        return [dict(s) for s in ACTIVITY_SIGNALS[:limit]]


class _StrategyCacheStub:
    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: TEMPLATES[i] for i in ids if i in TEMPLATES}


class _UserStrategyApiStub:
    """user_strategy_api：全量查询与按时间范围查询。

    按时间范围的实现**复刻后端语义**（闭区间、字符串比较、只传日期补 23:59:59），
    否则「窗口参数算错」这类 bug 会被宽松的桩放过去。
    """

    def list_executions(self) -> list[dict[str, Any]]:
        _EXEC_CALLS.append("all")
        return [dict(ex) for ex in EXECUTIONS]

    def list_executions_by_range(
        self,
        start_time: str = "",
        end_time: str = "",
        target_user_id: str = "",
        skip: int = 0,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        _RANGE_CALLS.append((start_time, end_time, limit))
        start: str = start_time or ""
        end: str = end_time or ""
        if len(end) == 10:  # 只传日期时后端按当天 23:59:59 处理
            end = f"{end} 23:59:59"
        out: list[dict[str, Any]] = []
        for ex in EXECUTIONS:
            stamp: str = str(ex.get("update_time") or ex.get("create_time") or "")
            if start and stamp < start:
                continue
            if end and stamp > end:
                continue
            out.append(dict(ex))
        return out


class _MarketApiStub:
    """market_api：按页面传入的 codes 返回实时价（只回 REAL_TIME 命中的）。"""

    def get_real_time(self, codes: str = "", **kw: Any) -> list[dict[str, Any]]:
        _QUOTE_CALLS.append(str(codes))
        wanted: set[str] = {c.strip() for c in str(codes).split(",") if c.strip()}
        return [dict(q) for q in REAL_TIME if q["code"] in wanted]


class _AppContextStub:
    theme_manager = _ThemeStub()
    signal_manager = _SignalManagerStub()
    strategy_cache = _StrategyCacheStub()
    user_strategy_api = _UserStrategyApiStub()
    market_api = _MarketApiStub()
    system_message_api = SYSTEM_MESSAGE_API


class _UserStrategyManagerStub:
    def list_user_strategies(self) -> list[dict[str, Any]]:
        return [dict(d) for d in USER_STRATEGIES]


def _install_stubs() -> None:
    """把桩模块注入 sys.modules。"""
    sys.modules["nicegui"] = _build_nicegui_stub()

    ac = types.ModuleType("app_context")
    ac.AppContext = _AppContextStub
    sys.modules["app_context"] = ac

    usm = types.ModuleType("core.user_strategy_manager")
    usm.UserStrategyManager = _UserStrategyManagerStub
    sys.modules["core.user_strategy_manager"] = usm

    # utils.tools 无外部依赖，直接 exec 真源码，让容错解析语义由真代码保证
    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = [str(ROOT / "utils")]
    sys.modules["utils"] = utils_pkg
    tools = types.ModuleType("utils.tools")
    exec(compile((ROOT / "utils" / "tools.py").read_text(encoding="utf-8"),
                 "utils/tools.py", "exec"), tools.__dict__)
    sys.modules["utils.tools"] = tools

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["core"] = core_pkg

    # ⚠ core.holdings 是被测口径本身，必须 exec 真源码而不是桩 ——
    # 页面里调用的是它，桩掉就等于什么都没测。
    holdings = types.ModuleType("core.holdings")
    exec(compile(HOLDINGS_SRC.read_text(encoding="utf-8"),
                 str(HOLDINGS_SRC), "exec"), holdings.__dict__)
    sys.modules["core.holdings"] = holdings

    routes = types.ModuleType("core.routes")
    exec(compile((ROOT / "core" / "routes.py").read_text(encoding="utf-8"),
                 "core/routes.py", "exec"), routes.__dict__)
    sys.modules["core.routes"] = routes

    concurrency = types.ModuleType("core.concurrency")

    async def run_blocking(fn: Any, *args: Any, **kwargs: Any) -> Any:
        # 真实实现是丢线程池；冒烟检查里同步调用即可覆盖被调函数本身
        return fn(*args, **kwargs)

    class PollingTask:
        """PollingTask 桩：只记录是否启动，不真正轮询。"""

        started: list["PollingTask"] = []

        def __init__(self, period: float, callback: Any, *,
                     in_thread: bool = True, name: str = "") -> None:
            self.period = period
            self.callback = callback
            self.name = name

        def start(self) -> None:
            PollingTask.started.append(self)

    concurrency.run_blocking = run_blocking
    concurrency.PollingTask = PollingTask
    sys.modules["core.concurrency"] = concurrency

    theme_stub = types.ModuleType("colors.theme")
    theme_stub.resolve_mode_from_prefs = lambda prefs: "light"
    colors_pkg = types.ModuleType("colors")
    colors_pkg.__path__ = [str(ROOT / "colors")]
    sys.modules["colors"] = colors_pkg
    sys.modules["colors.theme"] = theme_stub

    components_pkg = types.ModuleType("components")
    components_pkg.__path__ = [str(ROOT / "components")]
    sys.modules["components"] = components_pkg
    sys.modules["components.custom_tabs"] = types.ModuleType("components.custom_tabs")

    menu_pkg = types.ModuleType("menu")
    menu_pkg.__path__ = [str(ROOT / "menu")]
    sys.modules["menu"] = menu_pkg
    top_menu = types.ModuleType("menu.top_menu")
    top_menu._do_logout = lambda *a, **k: None
    top_menu._show_profile_dialog = lambda *a, **k: None
    sys.modules["menu.top_menu"] = top_menu


def _load_page_module() -> types.ModuleType:
    """exec 页面源码，返回模块对象。"""
    module = types.ModuleType("main_page_under_test")
    module.__file__ = str(PAGE_SRC)
    exec(compile(PAGE_SRC.read_text(encoding="utf-8"), str(PAGE_SRC), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------ 断言工具

def _reset_tree() -> _Node:
    _STACK.clear()
    del _ROOTS[:]
    root = _Node("root")
    _STACK.append(root)
    return root


def _find_all(root: _Node, predicate: Any) -> list[_Node]:
    return [n for n in root.walk() if predicate(n)]


def _text_of(node: _Node) -> str:
    return str(node.kw.get("text", ""))


def _rate_cells(root: _Node) -> list[str]:
    """收益率列的单元格文本（正负号 + 百分号，或算不出时的 "-"）。"""
    out: list[str] = []
    for node in _find_all(root, lambda n: n.kind == "label"):
        text = _text_of(node)
        if text == "-" or (text.endswith("%") and text[:1] in "+-"):
            out.append(text)
    return out


def _chart(root: _Node) -> _Node | None:
    for node in root.walk():
        if node.kind == "echart":
            return node
    return None


def _reset_caches(page: types.ModuleType) -> None:
    """清掉概要页的 TTL 缓存，让下一次渲染真去打接口。

    执行记录与实时价都是模块级状态（页面本意是跨组件复用），检查里需要能重置。
    """
    page._exec_state.update({"at": 0.0, "data": []})
    page._price_state.update({"key": "", "at": 0.0, "map": {}})


# 表格表头可能出现的文案。把「收益率 (30d)」也列进来是故意的：
# 原型 home.html 里就是这个名字，一旦有人按原型回抄，断言要能抓到。
_HEADER_TEXTS = ("策略名称", "类型", "创建日期", "状态", "收益率", "收益率 (30d)")


def _header_texts(root: _Node) -> list[str]:
    """表格表头文本，按渲染顺序（行内单元格的百分比/``-``不算）。"""
    out: list[str] = []
    for node in _find_all(root, lambda n: n.kind == "label"):
        text = _text_of(node)
        if text in _HEADER_TEXTS:
            out.append(text)
    return out


def _activity_names(root: _Node) -> list[str]:
    """「近期运行状态」每条活动的策略名（取「触发…信号: …」那条的前一个文本）。"""
    out: list[str] = []
    for row in _find_all(root, lambda n: n.kind == "row"):
        labels: list[str] = [_text_of(n) for n in row.walk() if n.kind == "label"]
        for index, text in enumerate(labels):
            if text.startswith("触发") and "信号:" in text and index > 0:
                out.append(labels[index - 1])
                break
    return out


def _activity_actions(root: _Node) -> list[str]:
    """「近期运行状态」每条活动的「触发X信号: 代码」文案。"""
    out: list[str] = []
    for node in _find_all(root, lambda n: n.kind == "label"):
        text = _text_of(node)
        if text.startswith("触发") and "信号:" in text:
            out.append(text)
    return out


# ------------------------------------------------------------- 通知铃铛取数

# 面板分区标题（与 pages/main_page.py 的两段文案一致，页面改了这里要同步）
_SECTION_TITLES = ("未读消息", "历史消息")


def _notif_badge(root: _Node) -> _Node | None:
    """顶栏铃铛角标节点。"""
    nodes = _find_all(root, lambda n: n.kind == "badge")
    return nodes[0] if nodes else None


def _notif_button(root: _Node) -> _Node | None:
    """顶栏通知按钮（唯一挂了 on_click 的 button）。"""
    nodes = _find_all(root, lambda n: n.kind == "button" and "on_click" in n.kw)
    return nodes[0] if nodes else None


def _row_texts(row: _Node) -> list[str]:
    """一行内的全部 label 文本，按渲染顺序（消息行的第 0 条即标题）。"""
    return [_text_of(n) for n in row.walk() if n.kind == "label"]


def _is_section_header(node: _Node) -> bool:
    """是否是「未读消息 / 历史消息」分区标题行。"""
    return (node.kind == "row" and bool(node.children)
            and _text_of(node.children[0]) in _SECTION_TITLES)


def _panel_groups(root: _Node) -> list[dict[str, Any]]:
    """消息面板的分区：[{title, count, items:[消息标题…]}]，按渲染顺序。

    面板体是那个把分区标题行当直接子节点的 column；消息行是它的 column 子节点。
    """
    for container in _find_all(root, lambda n: n.kind == "column"):
        if not any(_is_section_header(c) for c in container.children):
            continue
        groups: list[dict[str, Any]] = []
        for child in container.children:
            if _is_section_header(child):
                groups.append({"title": _text_of(child.children[0]),
                               "count": _text_of(child.children[1]),
                               "items": []})
            elif child.kind == "column" and groups:
                texts = _row_texts(child)
                groups[-1]["items"].append(texts[0] if texts else "")
        return groups
    return []


def _clickable_rows(root: _Node) -> list[_Node]:
    """挂了 click 处理器的消息行。

    页面只给**未读**行绑处理器，所以这个列表的长度 = 当前可点开置为已读的条数；
    历史行必须一个都不在里。
    """
    return [n for n in _find_all(root, lambda n: n.kind == "column") if n.handlers.get("click")]


def _dialog_opened(root: _Node) -> bool:
    """是否已经打开过对话框。"""
    return any(getattr(n, "opened", False) for n in root.walk() if n.kind == "dialog")


def main() -> int:
    _install_stubs()
    page = _load_page_module()

    # ------------------------------------------------ 第 1 节 core.holdings 口径
    print("\n--- 第 1 节 core.holdings 口径 ---")
    holdings = sys.modules["core.holdings"]

    price_map = holdings.build_price_map([
        {"code": "600000", "price": 12.0},
        {"code": "300750", "price": 0},        # 非正价 ⇒ 视为没有实时价
        {"code": "", "price": 9.9},            # 空代码 ⇒ 丢弃
        {"code": "601318", "close": 45.5},     # 只有 close ⇒ 也要认
    ])
    check("build_price_map 只认正价且有代码", price_map == {"600000": 12.0, "601318": 45.5},
          f"{price_map}")

    check("unrealized_profit 空持仓 = 0.0（不是 None）",
          holdings.unrealized_profit([], {}) == 0.0)
    check("unrealized_profit 缺实时价 ⇒ None",
          holdings.unrealized_profit([{"code": "300750", "quantity": 200, "cost_price": 5.0}], {}) is None)
    check("unrealized_profit 现算 Σ(实时价−成本价)×数量 = 200.0",
          holdings.unrealized_profit([{"code": "600000", "quantity": 100, "cost_price": 10.0}],
                                     {"600000": 12.0}) == 200.0)
    check("unrealized_profit 接受字符串型数量/成本价",
          holdings.unrealized_profit([{"code": "600000", "quantity": "100", "cost_price": "10.0"}],
                                     {"600000": 12.0}) == 200.0)

    latest_map = holdings.latest_execution_map(EXECUTIONS)
    check("latest_execution_map 按 user_strategy_id 归并（3 个策略）",
          sorted(latest_map) == ["us-1", "us-2", "us-4"], f"{sorted(latest_map)}")
    check("latest_execution_map 取 update_time 最新的一条",
          latest_map["us-1"]["update_time"] == "2026-09-03 10:00:00",
          f"{latest_map['us-1']['update_time']}")

    newest = holdings.latest_execution(EXECUTIONS)
    check("latest_execution 取全局最新（跨策略）",
          newest is not None and newest["update_time"] == "2026-09-03 10:00:00",
          f"{newest and newest['update_time']}")

    check("base_amount 优先取 initial_amount",
          holdings.base_amount({"initial_amount": 100000.0, "positions": []}) == 100000.0)
    check("base_amount 为 0 时退化为持仓成本合计（与后端口径一致）",
          holdings.base_amount({"initial_amount": 0.0, "positions": [
              {"cost_price": 10.0, "quantity": 100},
              {"cost_price": 20.0, "quantity": 50},
          ]}) == 2000.0)

    rec = latest_map["us-1"]
    check("current_return_rate_pct = (已实现+未实现)/初始资金×100 = 3.2",
          holdings.current_return_rate_pct(rec, 200.0) == 3.2,
          f"{holdings.current_return_rate_pct(rec, 200.0)}")
    check("current_return_rate_pct 缺未实现 ⇒ None（不退回已实现口径）",
          holdings.current_return_rate_pct(rec, None) is None)
    check("current_return_rate_pct 缺 current_profit ⇒ None",
          holdings.current_return_rate_pct({"initial_amount": 100.0, "positions": []}, 0.0) is None)
    check("current_return_rate_pct 分母为 0 ⇒ None",
          holdings.current_return_rate_pct(
              {"initial_amount": 0.0, "current_profit": 100.0, "positions": []}, 0.0) is None)

    # ---- 曲线「各策略收益率平均值」口径（零收益策略不进分子、不占分母） ----
    check("execution_day 取 update_time 的日期部分",
          holdings.execution_day({"update_time": "2026-09-03 10:00:00"}) == "2026-09-03")
    check("execution_day 回退 create_time；时间串不足 10 位 ⇒ 空串",
          holdings.execution_day({"create_time": "2026-09-03"}) == "2026-09-03"
          and holdings.execution_day({"update_time": "2026-09"}) == "")
    check("last_execution_day 取最晚日期（曲线窗口基准）",
          holdings.last_execution_day(EXECUTIONS) == "2026-09-03",
          holdings.last_execution_day(EXECUTIONS))
    check("last_execution_day 无有效记录 ⇒ 空串", holdings.last_execution_day([]) == "")

    day_rates = holdings.strategy_day_rates(EXECUTIONS)
    check("strategy_day_rates 同一天同一策略取**最后一条**记录"
          "（us-1 的 09-02 = 2.0，不是相加的 3.0 也不是平均的 1.5）",
          day_rates["2026-09-02"] == {"us-1": 2.0}
          and day_rates["2026-09-03"] == {"us-1": 3.0, "us-4": 5.0},
          f"{day_rates.get('2026-09-02')} / {day_rates.get('2026-09-03')}")
    check("strategy_day_rates 输入顺序不影响结果（乱序传入也取时间最大的那条）",
          holdings.strategy_day_rates([
              {"user_strategy_id": "us-1", "current_return_rate": 0.02,
               "update_time": "2026-09-02 10:00:00"},
              {"user_strategy_id": "us-1", "current_return_rate": 0.01,
               "update_time": "2026-09-02 09:00:00"},
          ])["2026-09-02"] == {"us-1": 2.0})
    check("strategy_day_rates 缺 user_strategy_id 的记录无法归并 ⇒ 跳过",
          holdings.strategy_day_rates(
              [{"current_return_rate": 0.5, "update_time": "2026-09-03 10:00:00"}]) == {})

    daily = holdings.daily_average_rates(EXECUTIONS)
    check("daily_average_rates = 各策略取值的**平均值**，当天没交易的策略沿用最近一次取值"
          "（08-10 = (5.0+2.0)/2 = 3.5；09-02 = (2.0+5.0+2.0)/3 = 3.0；09-03 = 13/3 = 4.33）",
          daily == {"2026-07-10": 4.0, "2026-07-20": 5.0, "2026-08-10": 3.5,
                    "2026-09-01": 2.67, "2026-09-02": 3.0, "2026-09-03": 4.33},
          f"{daily}")

    # 收益为 0 的策略既不进分子、也不占分母：us-5 在 09-02 有记录但收益率为 0，
    # 算进去 09-02 会是 9/4 = 2.25，排除后是 9/3 = 3.0。
    zero_series = [
        {"user_strategy_id": "us-1", "current_return_rate": 0.02,
         "update_time": "2026-09-02 10:00:00"},
        {"user_strategy_id": "us-2", "current_return_rate": 0.05,
         "update_time": "2026-09-02 11:00:00"},
        {"user_strategy_id": "us-5", "current_return_rate": 0.0,
         "update_time": "2026-09-02 12:00:00"},
    ]
    check("daily_average_rates 收益为 0 的策略不计入平均（7/2 = 3.5，不是 7/3 = 2.33）",
          holdings.daily_average_rates(zero_series)["2026-09-02"] == 3.5,
          f"{holdings.daily_average_rates(zero_series)}")
    check("daily_average_rates 沿用值也是 0 时同样排除（us-5 沿用 0 ⇒ 分母仍为 3）",
          holdings.daily_average_rates(
              [{"user_strategy_id": "us-1", "current_return_rate": 0.03,
                "update_time": "2026-09-03 10:00:00"},
               {"user_strategy_id": "us-5", "current_return_rate": 0.0,
                "update_time": "2026-09-02 12:00:00"}],
              {"us-2": 5.0},
          )["2026-09-03"] == 4.0,
          "us-1 3.0 + us-2 5.0，除以 2")
    check("daily_average_rates 全部策略收益为 0 ⇒ 该点 0.0（曲线不断线）",
          holdings.daily_average_rates(
              [{"user_strategy_id": "us-5", "current_return_rate": 0.0,
                "update_time": "2026-09-02 12:00:00"}],
          )["2026-09-02"] == 0.0,
          "没有非零取值时取 0.0")
    check("effective_rates 只留非零取值（含浮点残差判定）",
          holdings.effective_rates([0.0, -0.0, 1e-12, 2.0, -3.0]) == [2.0, -3.0],
          f"{holdings.effective_rates([0.0, -0.0, 1e-12, 2.0, -3.0])}")

    carry = holdings.strategy_levels_before(EXECUTIONS, "2026-08-10")
    check("strategy_levels_before 取窗口前各策略的最后取值（us-2 = 5.0，us-4 当时还没有记录）",
          carry == {"us-2": 5.0}, f"{carry}")
    check("strategy_levels_before 无窗口日期 ⇒ 空字典",
          holdings.strategy_levels_before(EXECUTIONS, "") == {})

    # us-2 的记录全在 7 月 ⇒ 不带 carry_in 时它在整个窗口里凭空消失，
    # 09-03 从 (3+5+5)/3 = 4.33 掉成 (3+5)/2 = 4.0。
    # 这条断言把 carry_in 的必要性钉死（页面必须把全量记录传进来做起始基准）。
    window_only = [ex for ex in EXECUTIONS if ex["update_time"] >= "2026-08-04"]
    with_carry = holdings.daily_average_rates(window_only, carry)
    without_carry = holdings.daily_average_rates(window_only)
    check("daily_average_rates 带 carry_in ⇒ 窗口外停交易的策略仍计入平均",
          with_carry["2026-08-10"] == 3.5 and with_carry["2026-09-03"] == 4.33,
          f"{with_carry}")
    check("daily_average_rates 不带 carry_in ⇒ 窗口外策略消失、均值偏小（页面必须传全量）",
          without_carry["2026-08-10"] == 2.0 and without_carry["2026-09-03"] == 4.0,
          f"{without_carry}")

    check("daily_average_rates 空输入 ⇒ 空字典", holdings.daily_average_rates([]) == {})

    days = ["2026-08-10", "2026-09-01", "2026-09-02", "2026-09-03"]
    check("strategy_levels_on_days 单策略当天无记录时沿用最近一次的取值",
          holdings.strategy_levels_on_days(
              [{"user_strategy_id": "us-2", "current_return_rate": 0.05,
                "update_time": "2026-09-02 10:00:00"}],
              days, 5.0,
          ) == [5.0, 5.0, 5.0, 5.0],
          "窗口前有基准时整段都是该基准")
    check("strategy_levels_on_days 在它第一条记录之前 ⇒ None（不补 0）",
          holdings.strategy_levels_on_days(
              [{"user_strategy_id": "us-1", "current_return_rate": 0.01,
                "update_time": "2026-09-01 10:00:00"}],
              days, None,
          ) == [None, 1.0, 1.0, 1.0],
          "尚未有数据与「值就是 0」必须区分开")
    check("strategy_levels_on_days 取值 = 该策略当天最后一条记录（传入的记录必须已按策略过滤）",
          holdings.strategy_levels_on_days(
              [{"user_strategy_id": "us-1", "current_return_rate": 0.01,
                "update_time": "2026-09-01 10:00:00"},
               {"user_strategy_id": "us-1", "current_return_rate": 0.02,
                "update_time": "2026-09-01 14:00:00"}],
              ["2026-09-01"], None,
          ) == [2.0],
          "当天两条取后一条，不是相加的 3.0")

    fallback_ok = holdings.current_rate_pct_with_fallback(
        latest_map["us-1"], {"600000": 12.0})
    check("current_rate_pct_with_fallback 有实时价 ⇒ (已实现+未实现)/初始资金 = 3.2",
          fallback_ok == 3.2, f"{fallback_ok}")
    fallback_miss = holdings.current_rate_pct_with_fallback(latest_map["us-2"], {})
    check("current_rate_pct_with_fallback 缺实时价 ⇒ 退化成已实现口径 5.0",
          fallback_miss == 5.0, f"{fallback_miss}")

    total_rate = holdings.average_current_rate_pct(EXECUTIONS, {"600000": 12.0})
    check("average_current_rate_pct = 各策略含浮盈收益率的**平均值**"
          " (3.2+5.0+5.0)/3 = 4.4",
          total_rate == 4.4, f"{total_rate}")

    # us-2 的记录全在 7 月 ⇒ 只用窗口内记录求平均时它整个消失，结果从 4.4 掉到 4.1。
    rate_window = holdings.average_current_rate_pct(window_only, {"600000": 12.0})
    check("average_current_rate_pct 必须传全量（只用窗口内记录会漏掉窗口外的策略）",
          rate_window == 4.1 and total_rate == 4.4,
          f"窗口 {rate_window} vs 全量 {total_rate}")
    check("average_current_rate_pct 空输入 ⇒ None",
          holdings.average_current_rate_pct([], {"600000": 12.0}) is None)
    check("average_current_rate_pct 排除收益为 0 的策略（(3.2+5.0+5.0+0)/4 是错的）",
          holdings.average_current_rate_pct(
              [*EXECUTIONS,
               {"user_strategy_id": "us-5", "current_return_rate": 0.0,
                "current_profit": 0.0, "initial_amount": 100000.0,
                "update_time": "2026-09-03 11:00:00", "positions": []}],
              {"600000": 12.0},
          ) == 4.4,
          "新增一条零收益策略后均值不变")

    # ------------------------------------------------ 第 2 节 收益曲线序列
    print("\n--- 第 2 节 收益曲线序列（各策略收益率平均值口径） ---")
    series = [
        {"user_strategy_id": "us-1", "current_return_rate": 0.01, "update_time": "2026-09-01 10:00:00"},
        {"user_strategy_id": "us-1", "current_return_rate": 0.02, "update_time": "2026-09-02 10:00:00"},
        {"user_strategy_id": "us-2", "current_return_rate": 0.04, "update_time": "2026-09-02 14:00:00"},
        {"user_strategy_id": "us-1", "current_return_rate": 0.03, "update_time": "2026-09-03 10:00:00"},
    ]
    labels, total, single = page._build_cumulative_series(series, 30)
    # 09-02 = (2+4)/2 = 3.0；09-03 us-2 当天没记录 ⇒ 沿用 4.0 ⇒ (3+4)/2 = 3.5
    # （若是"当天没记录就不算"，09-03 会掉成 3.0）
    check("_build_cumulative_series 总曲线 = 各策略取值的平均值，无记录的策略沿用最近一次取值",
          labels == ["09-01", "09-02", "09-03"] and total == [1.0, 3.0, 3.5],
          f"{labels} {total}")
    check("_build_cumulative_series 未选策略时第三条序列为空列表", single == [],
          f"{single}")

    labels, total, _ = page._build_cumulative_series(series, 30, 4.07)
    check("_build_cumulative_series 末点被覆盖成各策略含浮盈收益率的平均值",
          labels == ["09-01", "09-02", "09-03"] and total == [1.0, 3.0, 4.07],
          f"{labels} {total}")
    check("_build_cumulative_series 末点覆盖为 None 时不改动",
          page._build_cumulative_series(series, 30, None)[1] == [1.0, 3.0, 3.5])
    check("_build_cumulative_series 空输入不炸",
          page._build_cumulative_series([], 30, 4.07) == ([], [], []))

    labels, total, _ = page._build_cumulative_series(series, 1, 4.07)
    check("_build_cumulative_series 周期截取后末点仍是最后一天",
          labels[-1] == "09-03" and total[-1] == 4.07, f"{labels} {total}")

    # 零收益策略：us-6 在 09-02 有记录但收益率为 0 ⇒ 不进分子也不占分母。
    zero_series_page = [
        {"user_strategy_id": "us-1", "current_return_rate": 0.02,
         "update_time": "2026-09-02 10:00:00"},
        {"user_strategy_id": "us-6", "current_return_rate": 0.0,
         "update_time": "2026-09-02 11:00:00"},
    ]
    check("_build_cumulative_series 零收益策略不计入平均（2.0/1 = 2.0，不是 2.0/2 = 1.0）",
          page._build_cumulative_series(zero_series_page, 30)[1] == [2.0],
          f"{page._build_cumulative_series(zero_series_page, 30)[1]}")

    labels, total, single = page._build_cumulative_series(series, 30, 4.07, "us-2", 4.0)
    check("_build_cumulative_series 叠加单策略曲线：日期取并集、缺失补 None（不补 0）",
          labels == ["09-01", "09-02", "09-03"] and single == [None, 4.0, 4.0],
          f"{labels} {single}")

    labels, total, single = page._build_cumulative_series(
        series, 30, 4.07, "us-9", 9.9)
    check("_build_cumulative_series 所选策略无记录 ⇒ 全 None，不凭空补一个孤立末点",
          single == [None, None, None], f"{single}")

    # 对比曲线同样按「当天没记录沿用最近一次取值」走：us-1 只在 09-01 有记录，
    # 09-05 是 us-2 的交易日在图上，但 us-1 那天该显示它自己的 2.0，不是 None。
    gap_series = [
        {"user_strategy_id": "us-1", "current_return_rate": 0.02, "update_time": "2026-09-01 10:00:00"},
        {"user_strategy_id": "us-2", "current_return_rate": 0.04, "update_time": "2026-09-05 10:00:00"},
    ]
    labels, total, single = page._build_cumulative_series(gap_series, 30, None, "us-1")
    check("对比曲线在它没交易的日期沿用最近一次取值（不是断线、更不是 0）",
          labels == ["09-01", "09-05"] and single == [2.0, 2.0] and total == [2.0, 3.0],
          f"{labels} {total} {single}")

    # 窗口内只有 us-1 的记录，us-3 只在窗口外交易过 ⇒ 全靠 history 提供的起始基准。
    # 不传 history（只按窗口数据平均）时 us-3 完全消失，均值从 1.5 掉成 2.0。
    window_execs = [
        {"user_strategy_id": "us-1", "current_return_rate": 0.02,
         "update_time": "2026-09-01 10:00:00"},
    ]
    history = [
        {"user_strategy_id": "us-3", "current_return_rate": 0.01,
         "update_time": "2026-07-01 10:00:00"},
        *window_execs,
    ]
    labels, total, _ = page._build_cumulative_series(window_execs, 30)
    check("不传 history ⇒ 窗口外已停交易的策略不进平均（只剩 us-1 的 2.0）",
          labels == ["09-01"] and total == [2.0], f"{labels} {total}")
    labels, total, _ = page._build_cumulative_series(
        window_execs, 30, None, "", None, history)
    check("传 history ⇒ 起始基准补上窗口外 us-3 的 1.0（均值 (2+1)/2 = 1.5）",
          labels == ["09-01"] and total == [1.5], f"{labels} {total}")

    # ------------------------------------------------ 第 3 节 收益图接线
    print("\n--- 第 3 节 收益图接线（总曲线 + 单策略对比曲线） ---")
    _reset_caches(page)
    del _QUOTE_CALLS[:]
    del _EXEC_CALLS[:]
    del _RANGE_CALLS[:]
    root = _reset_tree()
    page._build_profit_chart("var(--qs-surface)", "var(--qs-border)",
                             "var(--qs-text)", "var(--qs-primary)")
    chart = _chart(root)
    if chart is None:
        check("收益图被创建", False, "没找到 echart 节点")
    else:
        series_list = chart.options.get("series", [])
        labels = chart.options.get("xAxis", {}).get("data", [])
        check("默认只画一条曲线（未选策略）", len(series_list) == 1, f"{len(series_list)}")
        check("日期 = 最后数据点(09-03)往前 30 天内的全部日期",
              labels == ["08-10", "09-01", "09-02", "09-03"], f"{labels}")
        # 08-10 = (us-2 沿用 5.0 + us-4 2.0)/2 = 3.5；09-01 = (1+5+2)/3 = 2.67；
        # 09-02 = (2+5+2)/3 = 3.0（当天取最后一条）；末点 = (3.2+5.0+5.0)/3 = 4.4
        check("总收益曲线 = 各策略收益率的平均值（窗口外策略沿用最近一次取值），末点 = 含浮盈均值",
              series_list[0]["data"] == [3.5, 2.67, 3.0, 4.4],
              f"{series_list[0]['data']}")
        check("主曲线名 = 系统总收益", series_list[0]["name"] == "系统总收益",
              str(series_list[0]["name"]))
        check("主曲线用主色 + 渐变面积",
              series_list[0]["lineStyle"]["color"] == "chart-line"
              and series_list[0]["lineStyle"]["width"] == 3
              and "areaStyle" in series_list[0],
              f"{series_list[0]['lineStyle']['color']}")
        check("单条曲线时不画图例（省出垂直空间）", "legend" not in chart.options)
        check("tooltip 模板按曲线数量拼，不引用不存在的 series 索引",
              chart.options.get("tooltip", {}).get("formatter") == "{b}<br/>{a0}: {c0}%",
              str(chart.options.get("tooltip", {}).get("formatter")))

    check("曲线走时间范围接口，窗口 = 最后数据点往前 30 天（闭区间、limit 取满）",
          _RANGE_CALLS == [("2026-08-04 00:00:00", "2026-09-03 23:59:59", 10000)],
          f"{_RANGE_CALLS}")
    check("全量执行记录只拉一次（窗口基准 + 末点共用）",
          _EXEC_CALLS == ["all"], f"{_EXEC_CALLS}")
    check("曲线拉取了实时价（按全部策略最新记录的持仓代码）",
          _QUOTE_CALLS == ["300750,600000"], f"{_QUOTE_CALLS}")

    # ---- 下拉框：选中策略后追加一条对比曲线 ----
    selects = _find_all(root, lambda n: n.kind == "select")
    check("卡片上有策略下拉框", len(selects) == 1, f"{len(selects)}")
    if selects and chart is not None:
        select = selects[0]
        check("下拉框选项 = 用户策略 ID → 名称（未选策略时默认空）",
              select.kw.get("options") == {
                  "us-1": "策略一", "us-2": "策略二",
                  "us-3": "策略三", "us-4": "策略四",
              } and select.kw.get("value") is None,
              f"{select.kw.get('options')} value={select.kw.get('value')!r}")

        range_before = len(_RANGE_CALLS)
        select.fire_value_change("us-1")
        series_list = chart.options.get("series", [])
        check("选中策略后追加第二条曲线", len(series_list) == 2, f"{len(series_list)}")
        if len(series_list) == 2:
            check("对比曲线名 = 所选策略名", series_list[1]["name"] == "策略一",
                  str(series_list[1]["name"]))
            check("对比曲线按 user_strategy_id 过滤（09-02 当天两条取最后一条 = 2.0）、末点含浮盈 3.2",
                  series_list[1]["data"] == [None, 1.0, 2.0, 3.2],
                  f"{series_list[1]['data']}")
            check("对比曲线用副色、更细且不铺面积（不与主曲线互相遮盖）",
                  series_list[1]["lineStyle"]["color"] == "chart-line-2"
                  and series_list[1]["lineStyle"]["width"] == 2
                  and "areaStyle" not in series_list[1],
                  f"{series_list[1]['lineStyle']}")
        check("两条曲线时才画图例", "legend" in chart.options)
        check("两条曲线时 tooltip 同时给出两个值",
              chart.options.get("tooltip", {}).get("formatter")
              == "{b}<br/>{a0}: {c0}%<br/>{a1}: {c1}%",
              str(chart.options.get("tooltip", {}).get("formatter")))
        check("切换策略复用窗口数据，不重查时间范围接口",
              len(_RANGE_CALLS) == range_before, f"{_RANGE_CALLS}")

        # us-2 的记录全在 7 月 ⇒ 30 天窗口内没有它。按「当天没记录沿用最近一次取值」
        # 的口径，它以 5.0 的平线参与（与总曲线把它算进去一致），不再显示"没有记录"提示。
        select.fire_value_change("us-2")
        series_list = chart.options.get("series", [])
        hints = _find_all(root, lambda n: n.kind == "label"
                          and "没有执行记录" in _text_of(n))
        check("窗口内没交易的策略 ⇒ 按最近一次取值画平线，不显示「没有记录」提示",
              len(series_list) == 2 and len(hints) == 0,
              f"series={len(series_list)} hint={[_text_of(h) for h in hints]}")
        if len(series_list) == 2:
            check("该平线 = 它最后一条记录的收益率 5.0（不是断线、也不是 0）",
                  series_list[1]["data"] == [5.0, 5.0, 5.0, 5.0],
                  f"{series_list[1]['data']}")

        # us-3 从未有过执行记录 ⇒ 没有可沿用的取值：不追加空曲线，并给出提示
        select.fire_value_change("us-3")
        series_list = chart.options.get("series", [])
        hints = _find_all(root, lambda n: n.kind == "label"
                          and "没有执行记录" in _text_of(n))
        check("从未有过执行记录的策略 ⇒ 不追加空曲线，并给出提示",
              len(series_list) == 1 and len(hints) == 1,
              f"series={len(series_list)} hint={[_text_of(h) for h in hints]}")

        # ---- 周期切换 ----
        select.fire_value_change(None)   # 清空下拉（clearable）
        del _RANGE_CALLS[:]
        period_btn = _find_all(root, lambda n: n.kind == "label" and _text_of(n) == "7天")
        for handler in period_btn[0].handlers.get("click", []):
            handler()
        check("切 7 天后窗口起点 = 最后数据点 − 7 天",
              _RANGE_CALLS == [("2026-08-27 00:00:00", "2026-09-03 23:59:59", 10000)],
              f"{_RANGE_CALLS}")
        labels = chart.options.get("xAxis", {}).get("data", [])
        check("切 7 天后 08-10 落到窗口外",
              labels == ["09-01", "09-02", "09-03"], f"{labels}")
        check("清空下拉后回到单条曲线（只画系统总收益）",
              len(chart.options.get("series", [])) == 1,
              f"{len(chart.options.get('series', []))}")

    # ------------------------------------------------ 第 4 节 近期更新策略表
    print("\n--- 第 4 节 近期更新策略表 ---")
    root = _reset_tree()
    page._build_recent_strategies_table("var(--qs-surface)", "var(--qs-border)",
                                       "var(--qs-text)", "var(--qs-negative)",
                                       "var(--qs-positive)")
    cells = _rate_cells(root)
    # 表头：末列是「收益率」而不是「收益率 (30d)」——该列展示最新收益率，
    # 不是 30 天窗口收益，带周期后缀名实不符（2026-09-23 用户定）。
    headers = _header_texts(root)
    check("表头 5 列、末列是「收益率」（不带 30d 后缀）",
          headers == ["策略名称", "类型", "创建日期", "状态", "收益率"], f"{headers}")
    # 行序与 USER_STRATEGIES 一致：us-1 / us-2 / us-3 / us-4
    check("表格收益率单元格数量为 4", len(cells) == 4, f"{cells}")
    check("us-1 已实现 3,000 + 未实现 200 ⇒ +3.2%（忽略成交时点快照 999999/888）",
          cells[:1] == ["+3.2%"], f"{cells}")
    check("us-2 缺实时价 ⇒ 【-】（不回退已实现，也不拿部分持仓凑数）",
          cells[1:2] == ["-"], f"{cells}")
    check("us-3 无执行记录 ⇒ 【-】", cells[2:3] == ["-"], f"{cells}")
    check("us-4 已清仓 ⇒ 已实现段取自执行记录 current_profit ⇒ +5.0%（不是缓存里的 +¥9,999）",
          cells[3:4] == ["+5.0%"], f"{cells}")

    calls_after_table = len(_QUOTE_CALLS)
    check("表格与曲线共用同一份行情缓存，未再打接口",
          _QUOTE_CALLS == ["300750,600000"], f"{_QUOTE_CALLS}")
    check("表格复用曲线已拉取的全量执行记录，未再打接口",
          _EXEC_CALLS == ["all"], f"{_EXEC_CALLS}")

    _reset_tree()
    page._build_recent_strategies_table("var(--qs-surface)", "var(--qs-border)",
                                       "var(--qs-text)", "var(--qs-negative)",
                                       "var(--qs-positive)")
    check("重复渲染命中 TTL 缓存，不再打行情接口",
          len(_QUOTE_CALLS) == calls_after_table, f"{_QUOTE_CALLS}")

    # ------------------------------------------------ 第 5 节 近期运行状态
    print("\n--- 第 5 节 近期运行状态（策略名取用户策略名称）---")
    root = _reset_tree()
    page._build_recent_activity("var(--qs-surface)", "var(--qs-border)", "var(--qs-text)",
                               "var(--qs-positive)", "var(--qs-negative)")
    activity_names = _activity_names(root)
    check("近期运行状态渲染出全部活动", len(activity_names) == len(ACTIVITY_SIGNALS),
          f"{activity_names}")
    check("策略名取用户策略名称（us-2 → 策略二，不是模板名「模板一」）",
          activity_names[:1] == ["策略二"], f"{activity_names}")
    check("会话缓存里没有该策略时回退模板名（us-9 → 模板二）",
          activity_names[1:2] == ["模板二"], f"{activity_names}")
    check("用户策略 ID 与模板 ID 都缺失时显示 --",
          activity_names[2:3] == ["--"], f"{activity_names}")
    check("活动正文仍是「触发X信号: 代码」",
          _activity_actions(root) == [
              "触发买入信号: 600000", "触发卖出信号: 000001", "触发买入信号: 300750",
          ], f"{_activity_actions(root)}")

    # ------------------------------------------------ 第 6 节 接线
    print("\n--- 第 6 节 接线 ---")
    check("页面用的是 core.holdings 的实现，而非自带副本",
          page.current_return_rate_pct is holdings.current_return_rate_pct
          and page.unrealized_profit is holdings.unrealized_profit
          and page.fetch_price_map is holdings.fetch_price_map
          and page.daily_average_rates is holdings.daily_average_rates
          and page.strategy_levels_before is holdings.strategy_levels_before
          and page.strategy_levels_on_days is holdings.strategy_levels_on_days
          and page.average_current_rate_pct is holdings.average_current_rate_pct
          and page.current_rate_pct_with_fallback is holdings.current_rate_pct_with_fallback,
          "模块级函数必须是同一对象")

    src = PAGE_SRC.read_text(encoding="utf-8")
    check("概要页不再直接读执行记录的 current_return_rate（聚合口径收进 core.holdings）",
          'get("current_return_rate"' not in src,
          "出现该模式说明有地方绕过了 core.holdings 的口径")
    check("概要页按时间范围取数走 user_strategy_api.list_executions_by_range",
          "list_executions_by_range(" in src,
          "不要在页面里自己拼 URL")

    api_src = API_SRC.read_text(encoding="utf-8")
    check("API 层对接 /api/user_strategy/executions/range，limit 默认取满 10000",
          '"/api/user_strategy/executions/range"' in api_src
          and "limit: int = 10000" in api_src,
          "后端 limit 默认只有 500，一年数据会被静默截断")

    # 历史信号必须由**所有子页面共用的外壳**加载。SignalManager 的信号容器是进程级的，
    # 应用热重载/重启后即为空，不补历史信号则所有展示信号的地方都是空的：
    # 监控页信号表与策略卡片、策略列表的「最近信号」列、首页近期运行状态。
    # ⚠ 原先只有 main_page()（首页 /）做了这一步，子页面直接访问或刷新时取不到信号，
    #   表现为"页面上信号都没有了"且刷新也无法恢复。
    # 断言按函数体切分，避免 main_page() 里同样的一行把断言蒙混过去。
    start: int = src.index("def main_page_with_content(")
    end: int = src.find("\ndef ", start + 1)
    wrapper_src: str = src[start:end if end != -1 else len(src)]
    check("子页面外壳（main_page_with_content）加载历史信号",
          "signal_manager.load_history(" in wrapper_src,
          "缺这一步时，直接访问/刷新子页面（如监控页）信号表恒空")
    check("子页面外壳（main_page_with_content）准备会话策略缓存",
          "_ensure_user_strategies()" in wrapper_src,
          "会话策略缓存为空时，按 user_strategy_id 过滤取不到任何信号")

    # ------------------------------------------------ 第 7 节 通知铃铛
    print("\n--- 第 7 节 通知铃铛（未读数、未读/历史分区、点开单条才置已读）---")
    SYSTEM_MESSAGE_API.reset()
    del page.PollingTask.started[:]
    root = _reset_tree()
    page._build_topbar("仪表盘", "var(--qs-text)", "var(--qs-primary)",
                       "var(--qs-border)", "var(--qs-surface)")

    badge = _notif_badge(root)
    bell = _notif_button(root)
    check("顶栏渲染出通知按钮与角标，角标初始隐藏",
          bell is not None and badge is not None and badge.visible is False,
          f"bell={bell is not None} badge_visible={badge and badge.visible}")
    check("消息面板在点铃铛之前不打开", not _dialog_opened(root))

    polls = [t for t in page.PollingTask.started if t.name == "顶栏未读消息角标"]
    check("通知角标按 10 秒轮询", len(polls) == 1 and polls[0].period == 10.0,
          f"{[(t.name, t.period) for t in page.PollingTask.started]}")

    asyncio.run(polls[0].callback())
    check("未读数 = is_read 为假的消息条数（缺 is_read 字段的历史数据也算未读）"
          "⇒ 3 条消息里 2 条未读，角标显示 2 并可见",
          badge is not None and badge.value == "2" and badge.visible is True,
          f"值={badge and badge.value} 可见={badge and badge.visible}")

    asyncio.run(bell.kw["on_click"]())
    check("点铃铛后才打开面板", _dialog_opened(root))

    groups = _panel_groups(root)
    check("面板按「未读消息 / 历史消息」两段渲染，顺序与文案固定",
          [g["title"] for g in groups] == ["未读消息", "历史消息"], f"{groups}")
    check("未读段 = is_read 为假的两条（含缺字段的公告B），标注条数 2",
          len(groups) == 2 and groups[0]["count"] == "2"
          and groups[0]["items"] == ["公告A", "公告B"],
          f"{groups[0] if groups else None}")
    check("历史段 = 已读的一条（已读即历史），标注条数 1",
          len(groups) == 2 and groups[1]["count"] == "1" and groups[1]["items"] == ["公告C"],
          f"{groups[1] if len(groups) == 2 else None}")

    rows = _clickable_rows(root)
    check("只有未读行可点（历史行不挂点击处理器）",
          [_row_texts(r)[0] for r in rows] == ["公告A", "公告B"],
          f"{[_row_texts(r)[0] for r in rows]}")

    # 点开「公告B」——缺 is_read 字段的那条，顺带验证老数据也能被置为已读
    target = [r for r in rows if _row_texts(r)[0] == "公告B"]
    asyncio.run(target[0].handlers["click"][0]())
    check("点开单条只标记该条（不是整批标记，也不是只改本地内存）",
          SYSTEM_MESSAGE_API.marked == ["m2"], f"{SYSTEM_MESSAGE_API.marked}")
    check("角标随该条已读同步减一 ⇒ 1",
          badge is not None and badge.value == "1", f"{badge and badge.value}")

    groups = _panel_groups(root)
    # 段内顺序 = 服务端返回顺序（后端按 create_time 倒序），页面只做过滤不重排：
    # 所以历史段是 公告B(09-29) → 公告C(09-28)，公告B 不会跳到公告C 后面。
    check("被点开的公告B 移入历史段，未读段只剩公告A；段内沿用服务端顺序",
          len(groups) == 2 and groups[0]["items"] == ["公告A"]
          and groups[1]["items"] == ["公告B", "公告C"],
          f"{[g['items'] for g in groups]}")
    rows = _clickable_rows(root)
    check("重画后只剩公告A可点（已读行不再挂处理器）",
          len(rows) == 1 and _row_texts(rows[0])[0] == "公告A",
          f"{[_row_texts(r)[0] for r in rows]}")

    # 会话内不再自己记「见过的消息 ID」：刷新页面 / 换端登录后未读数必须仍是服务端那份，
    # 所以角标与面板数据只能来自 user_messages 的 is_read。
    src = PAGE_SRC.read_text(encoding="utf-8")
    check("已读状态不再走会话内存（去掉 seen_msg_ids 那套判重）",
          "seen_msg_ids" not in src,
          "会话内存判重会导致刷新页面后红点重新亮起、换端看不到同一份已读")
    check("页面按 is_read 统计未读，且点开单条才调标记接口",
          'm.get("is_read", False)' in src and "mark_read(" in src,
          "缺后者就是「打开面板即全部已读」，与「点开单条才算已读」不符")
    check("面板按 is_read 分成未读 / 历史两段",
          all(f'"{title}"' in src for title in _SECTION_TITLES),
          f"{_SECTION_TITLES}")

    # ---- 空态 ----
    SYSTEM_MESSAGE_API.reset()
    SYSTEM_MESSAGE_API.messages = []
    root = _reset_tree()
    del page.PollingTask.started[:]
    page._build_topbar("仪表盘", "var(--qs-text)", "var(--qs-primary)",
                       "var(--qs-border)", "var(--qs-surface)")
    badge = _notif_badge(root)
    bell = _notif_button(root)
    asyncio.run(page.PollingTask.started[0].callback())
    check("没有任何消息时不亮角标", badge is not None and badge.visible is False,
          f"{badge and badge.visible}")
    asyncio.run(bell.kw["on_click"]())
    labels = [_text_of(n) for n in root.walk() if n.kind == "label"]
    check("没有任何消息时面板显示空态文案", "暂无系统消息" in labels, f"{labels}")

    print(f"\n{'=' * 60}")
    if _fail:
        print(f"失败 {len(_fail)} 项：")
        for item in _fail:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

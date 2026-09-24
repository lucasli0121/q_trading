#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-17
Description: 策略监控页离线冒烟检查 —— 用桩替代 nicegui / app_context / api，
    真跑一次 show_strategy_monitor_page() 并断言结构与行为。

为什么需要这个脚本：
    本机没有 nicegui / fastapi / httpx（只有 PyInstaller 产物），页面代码里
    以下几类问题**只有真跑一次才会暴露**，静态工具全都拦不住：
      1. f-string 里的裸花括号 —— py_compile 只查语法，会静默通过，运行时 NameError；
      2. 闭包引用顺序 —— 嵌套函数引用了尚未绑定的外层变量；
      3. ui.* 的用法/参数写错 —— pyflakes 只查名称，不查签名；
      4. 归组逻辑串卡 —— 信号归组若误用 strategy_id（模板 ID），
         同模板的多个用户策略会共用同一条"最新信号"，页面上看起来完全正常。

做法：把 nicegui / app_context / core.* 换成最小桩注入 sys.modules，
     exec 页面源码后直接调用入口函数，再遍历桩元素树做断言。

运行：python tests/strategy_monitor_smokecheck.py   （exit 0 = 通过）
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pathlib
import re
import sys
import types
from datetime import datetime, timedelta
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 允许用环境变量指向改写过的页面副本，用于"反向验证"：
# 把待验证的实现改坏后跑一遍，断言必须报错，否则说明断言是空的。
# ⚠ SMOKE_PAGE_SRC 这个名字**回测检查也在用**（指向回测页），两边指向不同文件；
# 反向验证驱动要精确定位监控页，优先读本页专用的 SMOKE_MONITOR_PAGE_SRC，
# 否则改坏监控页会把回测页也换掉、误伤到别的检查。
PAGE_SRC = pathlib.Path(
    os.environ.get(
        "SMOKE_MONITOR_PAGE_SRC",
        os.environ.get("SMOKE_PAGE_SRC", str(ROOT / "pages" / "strategy_monitor.py")),
    )
)
# 未实现盈亏 / 行情批量取价的口径已提到 core.holdings（概要页共用同一套），
# 反向验证要改坏那部分算法时得能替换它 —— 只换页面副本覆盖不到那些分支。
HOLDINGS_SRC = pathlib.Path(
    os.environ.get("SMOKE_HOLDINGS_SRC", str(ROOT / "core" / "holdings.py"))
)
# 信号管理器：load_history 的「本进程本会话只加载一次」语义只在它上面能验
# （页面侧只调用它），所以反向验证要能替换这个文件。
SIGNAL_MGR_SRC = pathlib.Path(
    os.environ.get("SMOKE_SIGNAL_MGR_SRC", str(ROOT / "mq" / "signal_manager.py"))
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
    """极简元素桩：只保留检查需要的属性。"""

    def __init__(self, kind: str, **kw: Any) -> None:
        self.kind: str = kind
        self.kw: dict[str, Any] = kw
        self.children: list[_Node] = []
        self.classes_list: list[str] = []
        self.styles: list[str] = []
        self.props_list: list[str] = []
        self.handlers: dict[str, list[Any]] = {}
        self.disabled: bool = False

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

    def disable(self) -> "_Node":
        self.disabled = True
        return self

    def enable(self) -> "_Node":
        self.disabled = False
        return self

    def clear(self) -> "_Node":
        self.children.clear()
        return self

    def set_value(self, value: Any) -> "_Node":
        self.value = value
        return self

    def cancel(self) -> None:
        self.cancelled = True

    # 便于遍历
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
    ui.card = lambda *a, **k: _make("card")
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.button = lambda *a, **k: _make("button", **k)
    ui.tooltip = lambda text="", *a, **k: _make("tooltip", text=str(text))
    ui.element = lambda tag="div", *a, **k: _make(f"element:{tag}")
    ui.space = lambda *a, **k: _make("space")

    def _notify(message: str = "", **kw: Any) -> None:
        _NOTIFY.append((str(message), str(kw.get("type", ""))))

    ui.notify = _notify
    ui.timer = lambda *a, **k: _make("timer")

    navigate = types.SimpleNamespace(to=lambda url: _NAVIGATED.append(str(url)))
    ui.navigate = navigate

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

    # nicegui.app（core.user_strategy_manager 会用到）
    napp = types.ModuleType("nicegui.app")
    napp.is_started = True
    storage = types.SimpleNamespace(user={})
    napp.storage = storage

    mod.ui = ui
    mod.app = napp
    return mod


# ------------------------------------------------- 其余依赖模块桩 + 假数据

NOW: datetime = datetime.now()  # noqa: DTZ005
RUNNING_SINCE: str = (NOW - timedelta(days=15, hours=4, minutes=22)).strftime("%Y-%m-%d %H:%M:%S")

USER_STRATEGIES: list[dict[str, Any]] = [
    {"_id": "us1", "strategy_id": "tplA", "name": "均线金叉策略 A", "status": "running",
     "update_time": RUNNING_SINCE, "total_profit": 1234.0},
    {"_id": "us2", "strategy_id": "tplB", "name": "小盘绩优成长股", "status": "stopped",
     "total_profit": -840.0},
    {"_id": "us3", "strategy_id": "tplA", "name": "均线金叉策略 B", "status": "paused",
     "total_profit": 0.0},
    # us4：已清仓（最新执行记录的 positions 为空），但挂着累计已实现收益
    {"_id": "us4", "strategy_id": "tplB", "name": "已清仓策略", "status": "stopped",
     "total_profit": 2500.0},
]

TEMPLATES: dict[str, dict[str, Any]] = {
    "tplA": {"name": "均线金叉策略 A", "strategy_type": "选股策略"},
    "tplB": {"name": "小盘绩优成长股", "strategy_type": "盯盘策略"},
}

# us1 与 us3 共用同一模板 tplA：若页面的"最新信号"按 strategy_id 归组，
# 两张卡片会显示同一条信号 —— 这是本检查要守住的关键回归点。
# 信号表同理：us3 的**用户策略名**（均线金叉策略 B）与模板名（均线金叉策略 A）不同，
# 用它区分「按 user_strategy_id 取名」与「按模板取名」。
SIGNALS: list[dict[str, Any]] = [
    {"id": "sig-1", "user_strategy_id": "us1", "strategy_id": "tplA", "action": "卖出",
     "stock_code": "000001", "trade_price": 12.45, "profit_rate": -1.2,
     "profit_amount": -120.0, "reason": "止损", "create_time": "2026-07-10 14:35:01"},
    {"user_strategy_id": "us3", "strategy_id": "tplA", "action": "买入", "stock_code": "600030",
     "trade_price": 21.80, "profit_rate": 0.8, "profit_amount": 80.0, "reason": "金叉",
     "create_time": "2026-07-10 10:15:22"},
    # 第三条挂在 us3 名下、时间最早：既不影响 us3 卡片「最新信号」与 us1 的信号计数，
    # 又能提供一条「股票信息里查不到该代码」的信号（表格应显示 "--" 而不是空白）。
    {"user_strategy_id": "us3", "strategy_id": "tplA", "action": "卖出", "stock_code": "999999",
     "trade_price": 8.88, "profit_rate": -2.5, "profit_amount": -250.0, "reason": "退市股",
     "create_time": "2026-07-10 09:00:00"},
]

# 股票信息接口（/api/stock_info/code）返回值：故意不给 999999，用来钉住"查不到就显示 --"。
# 600030 用带交易所后缀的形式返回，验证 lookup 的"去后缀比对"能对上信号里的 600030。
STOCK_INFOS: list[dict[str, Any]] = [
    {"code": "000001", "name": "平安银行"},
    {"code": "600030.SH", "name": "中信证券"},
]
STOCK_INFO_CALLS: list[str] = []

# 持仓明细：浮动盈亏 = 已实现（执行记录 current_profit）+ 未实现（实时价与成本价现算）。
# 其中的 current_price / profit_rate / profit_amount 是**成交时点值**（买入时写入、
# 持仓期间后端不参与计算、卖出才重算），故意填成不相干的数，用来钉住"不得取快照"。
EXECUTIONS: list[dict[str, Any]] = [
    {"user_strategy_id": "us1", "update_time": "2026-07-10 14:35:01",
     "current_profit": 1234.0,
     "positions": [
         # 实时价 12.00、成本 10.00、量 1000 ⇒ 未实现 +2,000.00
         {"code": "000001", "quantity": 1000, "cost_price": 10.0,
          "current_price": 999.0, "profit_rate": 88.88, "profit_amount": 7777.0},
         # 实时价 9.00、成本 10.00、量 500 ⇒ 未实现 -500.00
         {"code": "600519", "quantity": 500, "cost_price": 10.0,
          "current_price": 111.0, "profit_rate": -66.66, "profit_amount": -8888.0},
     ]},
    # us2：实时价 25.00、成本 20.00、量 100 ⇒ 未实现 +500.00
    {"user_strategy_id": "us2", "update_time": "2026-07-10 11:20:15",
     "current_profit": -840.0,
     "positions": [{"code": "600030", "quantity": 100, "cost_price": 20.0}]},
    # us4：已清仓（有执行记录但没有任何持仓）⇒ 未实现 0，浮动盈亏 = 已实现段
    {"user_strategy_id": "us4", "update_time": "2026-07-09 15:00:00",
     "current_profit": 2500.0, "positions": []},
]

# market_api.get_real_time 的返回值（页面按 codes 批量请求）
REAL_TIME: list[dict[str, Any]] = [
    {"code": "000001", "price": 12.0},
    {"code": "600519", "price": 9.0},
    {"code": "600030", "price": 25.0},
]

UPDATE_CALLS: list[dict[str, Any]] = []
CACHE_WRITES: list[tuple[str, dict[str, Any]]] = []


class _ThemeStub:
    """与真实 ThemeManager 的取色 API 形状一致（返回 var() 字符串）。"""

    def var(self, name: str) -> str:
        return f"var(--qs-{name})"

    def var_rgba(self, name: str, alpha: float) -> str:
        return f"rgba(var(--qs-{name}-rgb), {alpha})"


class _SignalManagerStub:
    def get_signals_by_user_strategy(self, user_strategy_ids: list[str], limit: int = 50) -> list[dict[str, Any]]:
        return [s for s in SIGNALS if s["user_strategy_id"] in set(user_strategy_ids)][:limit]

    def get_today_count(self) -> int:
        return 2

    def load_history(self, limit: int = 10) -> None:
        """必须存在：页面侧若调用它而桩没有，会抛 AttributeError 并被页面里的
        except 吞掉 —— 历史信号没加载却测不出来（本文件第 9 节的回归点）。"""
        return None


class _StrategyCacheStub:
    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: TEMPLATES[i] for i in ids if i in TEMPLATES}


class _UserStrategyApiStub:
    def list_executions(self) -> list[dict[str, Any]]:
        return list(EXECUTIONS)

    def update(self, user_strategy_id: str, status: str | None = None, **kw: Any) -> str:
        UPDATE_CALLS.append({"user_strategy_id": user_strategy_id, "status": status})
        return "ok"


class _MarketApiStub:
    """market_api：按页面传入的 codes 返回实时价（只回 REAL_TIME 命中的）。"""

    def get_real_time(self, codes: str = "", **kw: Any) -> list[dict[str, Any]]:
        wanted: set[str] = {c.strip() for c in str(codes).split(",") if c.strip()}
        return [dict(q) for q in REAL_TIME if q["code"] in wanted]


class _StockInfoApiStub:
    """stock_info_api：信号表用它按代码取股票名称（页面侧有进程级缓存）。

    按「去后缀」比对返回，复刻后端「按代码命中」的语义：请求 600030 时
    返回库里那条 600030.SH，由页面负责把两种写法对上。
    """

    def get_by_codes(self, codes: str) -> list[dict[str, Any]]:
        STOCK_INFO_CALLS.append(str(codes))
        wanted: set[str] = {
            c.strip().split(".")[0] for c in str(codes).split(",") if c.strip()
        }
        return [dict(i) for i in STOCK_INFOS if i["code"].split(".")[0] in wanted]


class _TradeSignalApiStub:
    """trade_signal_api：SignalManager.load_history 用它从后端补历史信号。

    第 10 节会把这个属性换成可计数的实现，验证「本进程本会话只加载一次」。
    """

    def list(self, limit: int = 10) -> list[dict[str, Any]]:
        return [dict(SIGNALS[0])]


class _AppContextStub:
    theme_manager = _ThemeStub()
    signal_manager = _SignalManagerStub()
    strategy_cache = _StrategyCacheStub()
    user_strategy_api = _UserStrategyApiStub()
    market_api = _MarketApiStub()
    stock_info_api = _StockInfoApiStub()
    trade_signal_api = _TradeSignalApiStub()


class _UserStrategyManagerStub:
    def ensure_loaded(self, *, force: bool = False) -> None:
        """必须存在（同 _SignalManagerStub.load_history 的理由）。"""
        return None

    def list_user_strategies(self) -> list[dict[str, Any]]:
        return [dict(d) for d in USER_STRATEGIES]

    def get_user_strategy_ids(self) -> list[str]:
        return [str(d["_id"]) for d in USER_STRATEGIES]

    def update_user_strategy(self, us_id: str, info: dict[str, Any]) -> None:
        CACHE_WRITES.append((us_id, dict(info)))


def _install_stubs() -> None:
    """把桩模块注入 sys.modules。"""
    sys.modules["nicegui"] = _build_nicegui_stub()

    ac = types.ModuleType("app_context")
    ac.AppContext = _AppContextStub
    sys.modules["app_context"] = ac

    usm = types.ModuleType("core.user_strategy_manager")
    usm.UserStrategyManager = _UserStrategyManagerStub
    sys.modules["core.user_strategy_manager"] = usm

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["core"] = core_pkg

    # core.routes 无外部依赖，直接 exec 真实源码，避免复制一份路由逻辑
    routes = types.ModuleType("core.routes")
    exec(compile((ROOT / "core" / "routes.py").read_text(encoding="utf-8"),
                 "core/routes.py", "exec"), routes.__dict__)
    sys.modules["core.routes"] = routes

    # utils.tools：页面用它做 total_profit 的容错解析（float() 会因缓存里的
    # 展示格式字符串 "12.5%" 抛 ValueError）。exec 真源码，让解析语义由真代码保证。
    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = [str(ROOT / "utils")]
    sys.modules["utils"] = utils_pkg
    tools = types.ModuleType("utils.tools")
    exec(compile((ROOT / "utils" / "tools.py").read_text(encoding="utf-8"),
                 "utils/tools.py", "exec"), tools.__dict__)
    tools.load_strategy_type = lambda: ["选股策略", "盯盘策略", "复盘策略"]
    sys.modules["utils.tools"] = tools

    # core.holdings：页面调的「未实现盈亏 / 批量取价」口径真源码（不是桩）。
    # 必须在 utils.tools 之后就位——它 import 了 to_float。
    holdings = types.ModuleType("core.holdings")
    exec(compile(HOLDINGS_SRC.read_text(encoding="utf-8"),
                 str(HOLDINGS_SRC), "exec"), holdings.__dict__)
    sys.modules["core.holdings"] = holdings

    concurrency = types.ModuleType("core.concurrency")

    async def run_blocking(fn: Any, *args: Any, **kwargs: Any) -> Any:
        # 真实实现是丢线程池；冒烟检查里同步调用即可覆盖被调函数本身
        return fn(*args, **kwargs)

    class PollingTask:
        """PollingTask 桩：只记录是否启动，不真正轮询。"""

        started: list["PollingTask"] = []

        def __init__(self, period: float, callback: Any, *, in_thread: bool = True, name: str = "") -> None:
            self.period = period
            self.callback = callback
            self.name = name

        def start(self) -> None:
            PollingTask.started.append(self)

    concurrency.run_blocking = run_blocking
    concurrency.PollingTask = PollingTask
    sys.modules["core.concurrency"] = concurrency


def _load_page_module() -> types.ModuleType:
    """exec 页面源码，返回模块对象。"""
    module = types.ModuleType("strategy_monitor_under_test")
    module.__file__ = str(PAGE_SRC)
    exec(compile(PAGE_SRC.read_text(encoding="utf-8"), str(PAGE_SRC), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------ 断言工具

def _reset_tree() -> _Node:
    _STACK.clear()
    root = _Node("root")
    _STACK.append(root)
    return root


def _find_all(root: _Node, predicate: Any) -> list[_Node]:
    return [n for n in root.walk() if predicate(n)]


def _text_of(node: _Node) -> str:
    return str(node.kw.get("text", ""))


def _card_rows(root: _Node) -> list[_Node]:
    return _find_all(root, lambda n: "monitor-card-row" in n.class_text())


def _strategy_cards(root: _Node) -> list[_Node]:
    """策略卡片本体：定宽 style 的 card 节点。"""
    return _find_all(root, lambda n: n.kind == "card" and "flex: 0 0 440px" in n.style_text())


def _card_by_name(root: _Node, name: str) -> _Node | None:
    for card in _strategy_cards(root):
        labels = [_text_of(n) for n in card.walk() if n.kind == "label"]
        if name in labels:
            return card
    return None


def _button_state(card: _Node) -> dict[str, str]:
    """{tooltip 文案: 'enabled'|'disabled'}"""
    result: dict[str, str] = {}
    for button in [n for n in card.walk() if n.kind == "button"]:
        tips = [_text_of(c) for c in button.children if c.kind == "tooltip"]
        if not tips:
            continue
        result[tips[0]] = "disabled" if button.disabled else "enabled"
    return result


def _metric_value(card: _Node, label: str) -> str | None:
    """取三列指标里某个标签对应的值。"""
    labels = card.walk()
    for index, node in enumerate(labels):
        if node.kind == "label" and _text_of(node) == label:
            for nxt in labels[index + 1:]:
                if nxt.kind == "label":
                    return _text_of(nxt)
    return None


def _subtitle(card: _Node) -> str:
    """卡片副标题（已运行时长 / 状态文案）。"""
    for node in card.walk():
        if node.kind == "label" and (
            _text_of(node).startswith("已运行") or _text_of(node) in ("已停止", "已暂停", "异常")
        ):
            return _text_of(node)
    return ""


def _latest_signal(card: _Node) -> str:
    """卡片底部「最新信号」的值。"""
    nodes = card.walk()
    for index, node in enumerate(nodes):
        if node.kind == "label" and _text_of(node) == "最新信号:":
            for nxt in nodes[index + 1:]:
                if nxt.kind == "label":
                    return _text_of(nxt)
    return ""


def _log_rows(root: _Node) -> list[_Node]:
    """实时信号日志的行（每行是带 log-row 类的 row 节点）。"""
    return _find_all(root, lambda n: "log-row" in n.class_text())


def _row_cells(row: _Node) -> list[str]:
    """一行里按渲染顺序取出的文本。

    注意「股票代码」列是**两行单元格**（代码 + 名称），因此会贡献两个文本。
    """
    return [_text_of(n) for n in row.walk() if n.kind == "label"]


# 信号表表头可能出现的文案。把「盈亏额」也留在候选集里是故意的：它是本次
# 明确要删掉的列，一旦被回抄回来，断言要能抓到。
_LOG_HEADER_TEXTS: tuple[str, ...] = (
    "时间", "策略", "股票代码", "信号类型", "成交价格", "盈亏率", "盈亏额", "原因",
)


def _log_headers(root: _Node) -> list[str]:
    """信号表表头文本，按渲染顺序。"""
    out: list[str] = []
    for node in _find_all(root, lambda n: n.kind == "label"):
        text = _text_of(node)
        if text in _LOG_HEADER_TEXTS:
            out.append(text)
    return out


# ---------------------------------------------------------------------- 主流程

def main() -> int:
    _install_stubs()
    page = _load_page_module()

    # ---------- 1) 纯函数 ----------
    print("=" * 72)
    print("1) 纯函数：时长 / 盈亏 / 时间解析")
    print("=" * 72)
    check("_format_duration 天时分", page._format_duration(15 * 86400 + 4 * 3600 + 22 * 60) == "15天 4小时 22分",
          page._format_duration(15 * 86400 + 4 * 3600 + 22 * 60))
    check("_format_duration 无天", page._format_duration(4 * 3600 + 22 * 60) == "4小时 22分",
          page._format_duration(4 * 3600 + 22 * 60))
    check("_format_duration 不足 1 分钟", page._format_duration(30) == "不足 1 分钟",
          page._format_duration(30))
    check("_fmt_profit 盈利带 + 与千分位", page._fmt_profit(4520.0) == "+¥4,520", page._fmt_profit(4520.0))
    check("_fmt_profit 亏损带 -", page._fmt_profit(-840.0) == "-¥840", page._fmt_profit(-840.0))
    check("_fmt_profit 零值", page._fmt_profit(0.0) == "¥0", page._fmt_profit(0.0))
    check("_parse_time 支持秒级格式", page._parse_time("2026-07-10 14:35:01") is not None)
    check("_parse_time 非法值返回 None", page._parse_time("not-a-time") is None)

    # ---------- 2) 页面渲染 ----------
    print()
    print("=" * 72)
    print("2) 页面渲染结构")
    print("=" * 72)
    root = _reset_tree()
    page.show_strategy_monitor_page()
    check("页面渲染未抛异常", True)

    rows = _card_rows(root)
    check("存在策略卡片横向滚动行", len(rows) == 1, f"找到 {len(rows)} 个")
    if rows:
        row_style = rows[0].style_text()
        check("滚动行 flex-wrap: nowrap", "flex-wrap: nowrap" in row_style, row_style[:80])
        check("滚动行 overflow-x: auto（可左右滚动）", "overflow-x: auto" in row_style, row_style[:80])

    cards = _strategy_cards(root)
    check("策略卡片数量与用户策略数一致", len(cards) == len(USER_STRATEGIES),
          f"{len(cards)} vs {len(USER_STRATEGIES)}")
    for card in cards:
        if "flex: 0 0 440px" not in card.style_text():
            check("卡片定宽（不随容器伸缩）", False, card.style_text()[:80])
            break
    else:
        check("卡片定宽（不随容器伸缩）", True, "flex: 0 0 440px")

    # ---------- 3) 卡片内容 ----------
    print()
    print("=" * 72)
    print("3) 卡片指标与文案")
    print("=" * 72)
    card1 = _card_by_name(root, "均线金叉策略 A")
    card2 = _card_by_name(root, "小盘绩优成长股")
    card3 = _card_by_name(root, "均线金叉策略 B")
    card4 = _card_by_name(root, "已清仓策略")
    check("四张卡片均渲染", all(x is not None for x in (card1, card2, card3, card4)))

    if card1 and card2 and card3 and card4:
        check("信号触发列（数据来自内存计数）", _metric_value(card1, "信号触发") == "1 次",
              str(_metric_value(card1, "信号触发")))
        check("当前持仓列（来自执行记录 positions）",
              _metric_value(card1, "当前持仓") == "2 只", str(_metric_value(card1, "当前持仓")))
        check("当前持仓列为 0 时不报错", _metric_value(card3, "当前持仓") == "0 只",
              str(_metric_value(card3, "当前持仓")))
        # 浮动盈亏 = 已实现（执行记录 current_profit） + 未实现（Σ(实时价 − 成本价) × 数量）
        # us1：已实现 1,234 + [(12−10)×1000 + (9−10)×500 = +1,500] = +2,734
        check("浮动盈亏含已实现段（us1: 1234 + 1500 = +2734）",
              _metric_value(card1, "浮动盈亏") == "+¥2,734", str(_metric_value(card1, "浮动盈亏")))
        check("已实现为亏损时两段相加（us2: −840 + 500 = −340）",
              _metric_value(card2, "浮动盈亏") == "-¥340", str(_metric_value(card2, "浮动盈亏")))
        check("无执行记录（从未成交）时浮动盈亏显示 --",
              _metric_value(card3, "浮动盈亏") == "--", str(_metric_value(card3, "浮动盈亏")))
        check("已清仓（有执行记录但无持仓）= 已实现段（us4: +2500）",
              _metric_value(card4, "浮动盈亏") == "+¥2,500", str(_metric_value(card4, "浮动盈亏")))
        texts1 = [_text_of(n) for n in card1.walk() if n.kind == "label"]
        check("浮动盈亏不取持仓快照 profit_rate/profit_amount",
              not any(t in texts1 for t in ("88.88", "-66.66", "7777", "-8,888")),
              str([t for t in texts1 if t in ("88.88", "-66.66", "7777", "-8,888")]))

        # 副标题：运行中算时长，其余显示状态
        check("running 卡片显示已运行时长",
              re.match(r"^已运行: 15天 4小时 2[12]分$", _subtitle(card1)) is not None, _subtitle(card1))
        check("stopped 卡片显示状态文案而非时长", _subtitle(card2) == "已停止", _subtitle(card2))
        check("paused 卡片显示状态文案", _subtitle(card3) == "已暂停", _subtitle(card3))

        # 最新信号：必须各归各卡（同模板不同用户策略不得串卡）
        sig1 = _latest_signal(card1)
        sig3 = _latest_signal(card3)
        check("卡片1 显示自身最新信号", sig1 == "卖出 000001 (14:35:01)", sig1)
        check("卡片3 显示自身最新信号（不与他人串卡）", sig3 == "买入 600030 (10:15:22)", sig3)
        check("同模板的两张卡片信号互不相同", sig1 != sig3, f"{sig1!r} vs {sig3!r}")
        check("无信号卡片显示 --", _latest_signal(card2) == "--", _latest_signal(card2))

    # ---------- 4) 启停暂停按钮 ----------
    print()
    print("=" * 72)
    print("4) 启动 / 暂停 / 停止按钮状态机")
    print("=" * 72)
    if card1 and card2 and card3:
        states1 = _button_state(card1)
        states2 = _button_state(card2)
        states3 = _button_state(card3)
        check("三个按钮齐备", set(states1) == {"启动", "暂停", "停止"}, str(sorted(states1)))
        check("running：启动不可用、暂停可用、停止可用",
              states1 == {"启动": "disabled", "暂停": "enabled", "停止": "enabled"}, str(states1))
        check("stopped：启动可用、暂停不可用、停止不可用",
              states2 == {"启动": "enabled", "暂停": "disabled", "停止": "disabled"}, str(states2))
        check("paused：启动可用（恢复）、暂停不可用、停止可用",
              states3 == {"启动": "enabled", "暂停": "disabled", "停止": "enabled"}, str(states3))

    # ---------- 5) 状态变更行为 ----------
    print()
    print("=" * 72)
    print("5) 点击按钮的状态变更行为")
    print("=" * 72)
    if card1:
        pause_btn = next(
            (n for n in card1.walk() if n.kind == "button"
             and any(_text_of(c) == "暂停" for c in n.children)),
            None,
        )
        handler = pause_btn.handlers.get("click", [None])[0] if pause_btn else None
        check("暂停按钮已绑定点击回调", handler is not None)
        if handler is not None:
            UPDATE_CALLS.clear()
            CACHE_WRITES.clear()
            asyncio.run(handler())
            check("调用 user_strategy_api.update(status='paused')",
                  UPDATE_CALLS == [{"user_strategy_id": "us1", "status": "paused"}], str(UPDATE_CALLS))
            check("同步写入会话缓存（status + update_time）",
                  len(CACHE_WRITES) == 1
                  and CACHE_WRITES[0][0] == "us1"
                  and CACHE_WRITES[0][1].get("status") == "paused"
                  and bool(CACHE_WRITES[0][1].get("update_time")), str(CACHE_WRITES))
            check("变更后给出提示", any("已暂停" in msg for msg, _ in _NOTIFY),
                  str([m for m, _ in _NOTIFY][-2:]))
            # 变更后卡片应就地重渲染为 paused 状态
            card1_after = _card_by_name(root, "均线金叉策略 A")
            check("重渲染后卡片状态切到 paused",
                  card1_after is not None and _subtitle(card1_after) == "已暂停",
                  _subtitle(card1_after) if card1_after else "卡片丢失")

    # ---------- 6) 容错 ----------
    print()
    print("=" * 72)
    print("6) 容错：update_time 缺失 / 无策略 / 执行记录查询失败")
    print("=" * 72)
    check("update_time 缺失时不臆造时长",
          page._runtime_text({"status": "running", "update_time": ""}) == "已运行: --",
          page._runtime_text({"status": "running", "update_time": ""}))
    check("update_time 非法时不臆造时长",
          page._runtime_text({"status": "running", "update_time": "2026/07/10"}) == "已运行: --",
          page._runtime_text({"status": "running", "update_time": "2026/07/10"}))

    # 无策略：应渲染空态且不抛异常
    saved = list(USER_STRATEGIES)
    try:
        USER_STRATEGIES.clear()
        root_empty = _reset_tree()
        page.show_strategy_monitor_page()
        empty_labels = [_text_of(n) for n in root_empty.walk() if n.kind == "label"]
        check("无策略时渲染空态提示", any("暂无策略" in t for t in empty_labels), str(empty_labels[:3]))
        check("无策略时不渲染卡片", len(_strategy_cards(root_empty)) == 0)
    finally:
        USER_STRATEGIES.extend(saved)

    # 执行记录查询失败：不应抛异常，持仓退化为 0
    class _BoomApi(_UserStrategyApiStub):
        def list_executions(self) -> list[dict[str, Any]]:
            raise RuntimeError("后端不可用")

    _AppContextStub.user_strategy_api = _BoomApi()
    try:
        root_boom = _reset_tree()
        page.show_strategy_monitor_page()
        check("执行记录查询失败时页面仍能渲染", True)
        check("查询失败时持仓退化为 0 只而不报错",
              any(_metric_value(c, "当前持仓") == "0 只" for c in _strategy_cards(root_boom)))
    finally:
        _AppContextStub.user_strategy_api = _UserStrategyApiStub()

    # 会话缓存是**多页共享**的：策略列表页曾把展示格式字符串（"12.5%" / "+¥1,234"）
    # 写进 total_profit。本页已实现段优先取执行记录的 current_profit（与持仓同一次拉取、
    # 跟随 30s 快照刷新），缓存里的脏值不该影响结果。
    _saved_profit = USER_STRATEGIES[0]["total_profit"]
    try:
        # 故意用一个**能解析但不等于执行记录 current_profit** 的脏值：
        # 若代码改了主意去读缓存，结果会变成 +¥11,499 而被抓出来；
        # 若代码用裸 float() 解析它，直接抛 ValueError。
        USER_STRATEGIES[0]["total_profit"] = "+¥9,999"
        root_dirty = _reset_tree()
        page.show_strategy_monitor_page()
        check("total_profit 是展示格式字符串时页面仍能渲染", True)
        check("已实现段取执行记录，不受被写脏的 total_profit 影响",
              any(_metric_value(c, "浮动盈亏") == "+¥2,734"
                  for c in _strategy_cards(root_dirty)),
              str([_metric_value(c, "浮动盈亏") for c in _strategy_cards(root_dirty)]))
    finally:
        USER_STRATEGIES[0]["total_profit"] = _saved_profit

    # 回退分支：执行记录缺 current_profit（历史数据）时改取 total_profit，
    # 此时必须是**容错解析**（to_float），裸 float("+¥999") 会抛 ValueError 打断整页渲染。
    _saved_cp = EXECUTIONS[0]["current_profit"]
    try:
        del EXECUTIONS[0]["current_profit"]
        USER_STRATEGIES[0]["total_profit"] = "+¥999"
        root_fb = _reset_tree()
        page.show_strategy_monitor_page()
        check("记录缺 current_profit 时回退 total_profit（容错解析 999 + 1500 = +2499）",
              any(_metric_value(c, "浮动盈亏") == "+¥2,499"
                  for c in _strategy_cards(root_fb)),
              str([_metric_value(c, "浮动盈亏") for c in _strategy_cards(root_fb)]))
    finally:
        EXECUTIONS[0]["current_profit"] = _saved_cp
        USER_STRATEGIES[0]["total_profit"] = _saved_profit

    # 缺实时价（停牌 / 行情接口失败）：整张卡片的浮动盈亏显示 "--"，**不得**退回持仓
    # 快照 current_price（999.00 / 111.00）硬算出一个看着像实时的数；也**不得**只显示
    # 已实现段（+¥1,234）—— 那是个残缺值，却会被读成完整浮盈。
    _saved_rt = list(REAL_TIME)
    try:
        REAL_TIME.clear()
        root_noquote = _reset_tree()
        page.show_strategy_monitor_page()
        card_nq = _card_by_name(root_noquote, "均线金叉策略 A")
        check("缺实时价时卡片仍渲染", card_nq is not None)
        if card_nq is not None:
            check("缺实时价时浮动盈亏显示 --", _metric_value(card_nq, "浮动盈亏") == "--",
                  str(_metric_value(card_nq, "浮动盈亏")))
            nq_texts = [_text_of(n) for n in card_nq.walk() if n.kind == "label"]
            check("缺实时价时不退回持仓快照价硬算（999.00 / 111.00 不出现）",
                  not any(t in nq_texts for t in ("999.00", "111.00")), str(nq_texts))
            check("缺实时价时也不只显示已实现段（+¥1,234 不出现）",
                  "+¥1,234" not in nq_texts, str(nq_texts))
            check("缺实时价不影响当前持仓只数（仍按执行记录计）",
                  _metric_value(card_nq, "当前持仓") == "2 只",
                  str(_metric_value(card_nq, "当前持仓")))
        # 已清仓的卡片没有持仓 ⇒ 不需要实时价，已实现段照常显示（不受行情故障影响）
        card_nq4 = _card_by_name(root_noquote, "已清仓策略")
        check("已清仓卡片在缺实时价时仍显示已实现段（+¥2,500）",
              card_nq4 is not None and _metric_value(card_nq4, "浮动盈亏") == "+¥2,500",
              str(_metric_value(card_nq4, "浮动盈亏")) if card_nq4 is not None else "无卡片")
    finally:
        REAL_TIME[:] = _saved_rt

    # ---------- 7) 「详情回测」入口 ----------
    print()
    print("=" * 72)
    print("7) 「详情回测」跳转到策略回测记录页")
    print("=" * 72)

    def _backtest_link(card: _Node | None) -> _Node | None:
        """取卡片右下角的「详情回测 >」入口。"""
        if card is None:
            return None
        return next((n for n in card.walk()
                     if n.kind == "label" and _text_of(n) == "详情回测 >"), None)

    link1 = _backtest_link(card1)
    check("卡片上有「详情回测 >」入口", link1 is not None)
    if link1 is not None:
        handler = link1.handlers.get("click", [None])[0]
        check("入口已绑定点击回调", handler is not None)
        if handler is not None:
            _NAVIGATED.clear()
            handler()
            # 回测按策略模板 ID 关联 ⇒ URL 带的是 tplA（us1 的模板），不是 us1；
            # source=monitor 让回测页的「返回」按钮回到本页（策略监控）
            check("跳转 URL 带策略模板 ID 与来源 monitor",
                  _NAVIGATED == ["/strategy-backtest?strategy_id=tplA&source=monitor"],
                  str(_NAVIGATED))

    # 未关联模板的历史数据：不能跳到没有 ID 的页面上（那只会看到空态）
    saved_tpl = list(USER_STRATEGIES)
    try:
        USER_STRATEGIES.append(
            {"_id": "us9", "strategy_id": "", "name": "无模板策略", "status": "stopped"}
        )
        root_no_tpl = _reset_tree()
        page.show_strategy_monitor_page()
        card9 = _card_by_name(root_no_tpl, "无模板策略")
        check("未关联模板的策略也能渲染卡片", card9 is not None)
        link9 = _backtest_link(card9)
        if link9 is not None:
            _NAVIGATED.clear()
            _NOTIFY.clear()
            link9.handlers["click"][0]()
            check("缺少模板 ID 时不跳转、只给提示",
                  _NAVIGATED == [] and any("未关联策略模板" in m for m, _ in _NOTIFY),
                  f"导航={_NAVIGATED} 提示={[m for m, _ in _NOTIFY]}")
    finally:
        USER_STRATEGIES[:] = saved_tpl

    # ---------- 8) 实时信号日志表 ----------
    print()
    print("=" * 72)
    print("8) 实时信号日志表：用户策略名 / 去掉盈亏额 / 代码下有名称")
    print("=" * 72)
    calls_before = len(STOCK_INFO_CALLS)
    check("首次渲染按信号代码批量取过股票名称", calls_before >= 1, f"{STOCK_INFO_CALLS}")
    root_log = _reset_tree()
    page.show_strategy_monitor_page()
    check("股票名称走进程级缓存：重复渲染不再请求股票信息接口",
          len(STOCK_INFO_CALLS) == calls_before, f"{STOCK_INFO_CALLS}")

    headers = _log_headers(root_log)
    check("信号表 7 列、已去掉「盈亏额」列",
          headers == ["时间", "策略", "股票代码", "信号类型", "成交价格", "盈亏率", "原因"],
          f"{headers}")

    log_rows = _log_rows(root_log)
    check("信号表行数与信号条数一致", len(log_rows) == len(SIGNALS),
          f"{len(log_rows)} vs {len(SIGNALS)}")
    if len(log_rows) == len(SIGNALS):
        cells = [_row_cells(r) for r in log_rows]
        # 行内顺序与 SIGNALS 一致（us1 14:35:01 / us3 10:15:22 / us3 09:00:00）
        check("策略列显示**用户策略名称**（us1）",
              cells[0][1] == "均线金叉策略 A", f"{cells[0]}")
        # 关键回归点：us3 与 us1 共用模板 tplA（模板名 = 均线金叉策略 A），
        # 若按 strategy_id 取模板名，这里会显示成 us1 的名字。
        check("同模板的另一个用户策略显示自己的名称（us3 而非模板名）",
              cells[1][1] == "均线金叉策略 B", f"{cells[1]}")
        check("股票代码单元格：代码在上、名称在下",
              cells[0][2] == "000001" and cells[0][3] == "平安银行", f"{cells[0]}")
        check("名称比对兼容带交易所后缀的代码（600030 ↔ 600030.SH）",
              cells[1][2] == "600030" and cells[1][3] == "中信证券", f"{cells[1]}")
        check("查不到名称的代码显示 --（不留空）",
              cells[2][2] == "999999" and cells[2][3] == "--", f"{cells[2]}")
        check("其余列不受影响（信号类型/价格）",
              cells[0][4] == "卖出" and cells[0][5] == "¥12.45", f"{cells[0]}")

        flat: list[str] = [text for row in cells for text in row]
        amounts: list[str] = [f"{s['profit_amount']:+.2f}" for s in SIGNALS]
        check("行内不再出现盈亏额（原「盈亏额」列的格式）",
              all(a not in flat for a in amounts), f"{amounts} vs 行内={flat}")

    # ---------- 9) 历史信号加载标志 ----------
    # 回归现象：改代码触发 NiceGUI 热重载后，监控页信号表一条都不显示、刷新也不恢复。
    # 原因：内存信号容器（进程级）被清空，而「已加载历史信号」标志存在跨进程保留的
    # 会话里、值是布尔 → load_history 直接返回，历史信号再也不加载。
    # 本页不负责加载（由 main_page_with_content 统一准备，见概要页检查），这里只守
    # load_history 自身的标志语义。
    print()
    print("=" * 72)
    print("9) 历史信号加载标志（信号表恒空的回归点）")
    print("=" * 72)
    mgr_src = SIGNAL_MGR_SRC.read_text(encoding="utf-8")
    # 两处判断（load_history 的入口判断 + 失败回滚里的判断）都必须按进程 token 比对。
    # 只查「出现过 token 比对」不够：把入口判断退回布尔后，回滚那处仍含该子串，
    # 断言会照常通过（反向验证实测漏网）。
    check("标志位按**进程 token** 比对（不是布尔）",
          mgr_src.count('== _PROCESS_TOKEN') >= 2
          and "app.storage.user.get(_HISTORY_FLAG_KEY, False)" not in mgr_src,
          "只存布尔时，热重载后进程内存已空但标志仍为 True，历史信号永不加载")
    check("标志位写入的是当前进程 token",
          "app.storage.user[_HISTORY_FLAG_KEY] = _PROCESS_TOKEN" in mgr_src,
          "存 True 会让下一代的进程误判为已加载")
    check("加载失败时回滚标志（下次页面加载可重试）",
          "del app.storage.user[_HISTORY_FLAG_KEY]" in mgr_src,
          "失败后若留着标志，本会话在本进程内再也不会加载历史信号")
    # _PROCESS_TOKEN 在**模块导入时**就要求值，漏掉 import 会让整个应用起不来
    check("进程 token 的依赖已导入（uuid4）",
          "from uuid import uuid4" in mgr_src,
          "模块级 _PROCESS_TOKEN = uuid4().hex，漏 import 时应用启动即 NameError")

    # ---------- 10) 真跑 SignalManager.load_history ----------
    # 第 9 节只是源码文本断言；这里把真实现跑起来，验证「本进程本会话只加载一次」
    # 的各条分支：首载请求 / 同进程不重复 / 换了一代进程必须重载 / 失败回滚。
    # 必须真跑：这些分支里只有"正常首载"落在常规路径上，其余靠文本断言证明不了。
    print()
    print("=" * 72)
    print("10) 真跑 load_history：热重载后必须重新加载历史信号")
    print("=" * 72)

    # mq.signal_manager 顶层 import 了 mq.mqtt_client（依赖 paho，本机没装），桩掉；
    # 其余依赖（core.singleton）走真源码，口径不被桩改写。
    mq_pkg = types.ModuleType("mq")
    mq_pkg.__path__ = [str(ROOT / "mq")]
    sys.modules.setdefault("mq", mq_pkg)
    mqtt_stub = types.ModuleType("mq.mqtt_client")
    mqtt_stub.MqttClient = type("MqttClient", (), {})
    mqtt_stub.MqttTopic = type("MqttTopic", (), {})
    sys.modules["mq.mqtt_client"] = mqtt_stub

    spec = importlib.util.spec_from_file_location("mq.signal_manager", SIGNAL_MGR_SRC)
    assert spec is not None and spec.loader is not None
    sm = importlib.util.module_from_spec(spec)
    sys.modules["mq.signal_manager"] = sm
    spec.loader.exec_module(sm)

    app_stub = sys.modules["nicegui"].app
    mgr = sm.SignalManager()
    mgr._signals.clear()
    scanned: list[int] = []

    # 历史信号接口的返回值可替换，便于构造「同一批记录被加载两次」的场景
    feed: list[dict[str, Any]] = [dict(SIGNALS[0])]

    class _CountingApi:
        def list(self, limit: int = 10) -> list[dict[str, Any]]:
            scanned.append(limit)
            return [dict(d) for d in feed]

    _AppContextStub.trade_signal_api = _CountingApi()  # type: ignore[assignment]
    app_stub.storage.user.clear()

    mgr.load_history()
    check("首次加载：取历史信号并按 user_strategy_id 归组进内存",
          scanned == [10] and "us1" in mgr._signals
          and len(mgr._signals["us1"].signal) == 1,
          f"请求={scanned} 内存键={list(mgr._signals)}")
    check("首次加载：标志位写入当前进程 token",
          app_stub.storage.user.get(sm._HISTORY_FLAG_KEY) == sm._PROCESS_TOKEN,
          str(dict(app_stub.storage.user)))

    mgr.load_history()
    check("同进程同会话不重复加载（不重复打后端）",
          scanned == [10], f"请求={scanned}")

    # 热重载场景：进程内存被清空，会话里留着**上一代**进程的标志。
    # 旧实现往里存布尔值，这一步会被直接挡回去 ⇒ 信号表恒空且刷新也无法恢复。
    app_stub.storage.user[sm._HISTORY_FLAG_KEY] = "previous-process-token"
    mgr._signals.clear()
    mgr.load_history()
    check("标志属于上一代进程时必须重新加载（热重载后信号表不再恒空）",
          scanned == [10, 10] and "us1" in mgr._signals, f"请求={scanned}")

    # ---- 去重：容器是**进程级**的（所有会话共享），而加载标志是**会话级**的 ----
    # 每个会话各加载一次，无脑追加就会把同一条历史信号复制多份。真机实测：3 个会话
    # ⇒ 表格里每条信号出现 3 次、统计卡从 10 条虚增到 30 条。
    app_stub.storage.user.clear()          # 换一个会话（新会话里没有标志）
    mgr.load_history()
    check("跨会话重复加载同一批历史信号：按去重键合并，不复制",
          len(mgr._signals["us1"].signal) == 1
          and scanned[-1] == 10,
          f"容器内条数={len(mgr._signals['us1'].signal)} 请求={scanned}")

    # MQ 实时路径与历史路径必须用**同一个**去重键：后端记录带 id、MQ 载荷没有 id，
    # 只按 id 去重时同一条信号会留下两份（先到 MQ、后到历史）。
    mgr._signals.clear()
    app_stub.storage.user.clear()
    mgr.load_history()                      # 历史记录（带 id）
    # 冻结清理：假信号的时间在 7 月，prune_expired 会删掉它，那样断言就变成
    # "被清理掉所以只剩 1 条"，与去重无关（实测踩过一次）。
    mgr._last_prune = sm.time.monotonic()
    mq_payload: dict[str, Any] = dict(SIGNALS[0])
    mq_payload.pop("id")                    # MQ 载荷没有 id
    mgr._on_signal("q_share/trading_signal", json.dumps(mq_payload))
    check("MQ 实时信号与历史记录按同一去重键合并（不是按 id 去重）",
          len(mgr._signals["us1"].signal) == 1,
          f"容器内条数={len(mgr._signals['us1'].signal)}")

    class _FailingApi:
        def list(self, limit: int = 10) -> list[dict[str, Any]]:
            scanned.append(limit)
            raise RuntimeError("后端不可用")

    _AppContextStub.trade_signal_api = _FailingApi()  # type: ignore[assignment]
    app_stub.storage.user.clear()
    before_fail: int = len(scanned)
    mgr.load_history()
    check("加载失败后不留下「已加载」标志",
          sm._HISTORY_FLAG_KEY not in app_stub.storage.user,
          str(dict(app_stub.storage.user)))
    mgr.load_history()
    check("失败后重试会再次请求后端（标志已回滚）",
          len(scanned) == before_fail + 2,
          f"两次失败调用各请求一次：{before_fail} -> {len(scanned)}")

    # ---------- 结果 ----------
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

#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-17
Description: 策略回测记录页离线冒烟检查 —— 桩替 nicegui / app_context / core.*，
    真跑一次 show_strategy_backtest_page() 并断言结构与行为。

为什么需要（本机没有 nicegui / fastapi，页面代码里这些问题只有真跑一次才暴露）：
    1. f-string 里的裸花括号 —— py_compile 只查语法，运行时才 NameError；
    2. 闭包引用顺序（嵌套函数引用了后定义的外层变量）；
    3. 指标口径 —— 后端 ``backtest_return_rate`` 是**比例**（0.0543 = 5.43%），
       漏乘 100 页面上看着完全正常，只是所有百分比都小了两个数量级；
    4. 指标在 ``result_data`` 里而不是记录顶层（dao/backtest_dao.py 的字段说明），
       读错层级不会报错，只会显示一排 0；
    5. 排序方向错了 → 默认选中的是**最旧**那条，页面照常渲染。
    6. 「返回」按钮写死父页面 —— 从策略列表进来也会跑回策略监控；
       来源标识不做白名单校验则可能被构造成跳转到任意地址。

覆盖 8 节：纯函数口径 / 页面结构 / 默认选中 / 切换详情 / 刷新 / 容错 /
返回按钮跟随入口 / 路由接线一致性（``@ui.page`` 路径必须在中间件白名单内）。

假数据取自需求方给的真实库记录（ObjectId 换成字符串 _id，数值原样保留）。

运行：python tests/strategy_backtest_smokecheck.py     （exit 0 = 通过）
反向验证：把页面改坏后另存一份，用
    SMOKE_PAGE_SRC=<改坏副本> python tests/strategy_backtest_smokecheck.py
跑一遍，断言必须报错 —— 否则说明断言是空的。
（接线一节另有 ``SMOKE_MAIN_SRC`` 指向改坏的 main.py 副本。）
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
PAGE_SRC = pathlib.Path(
    os.environ.get("SMOKE_PAGE_SRC", str(ROOT / "pages" / "strategy_backtest.py"))
)
# 接线检查要读 main.py 的源码；同样支持指向改坏副本做反向验证
MAIN_SRC = pathlib.Path(os.environ.get("SMOKE_MAIN_SRC", str(ROOT / "main.py")))
# core.routes 是 exec 真源码的，改坏它也要能用同一套反向验证手段
ROUTES_SRC = pathlib.Path(
    os.environ.get("SMOKE_ROUTES_SRC", str(ROOT / "core" / "routes.py"))
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

    def clear(self) -> "_Node":
        self.children.clear()
        return self

    def set_text(self, text: str) -> "_Node":
        self.kw["text"] = str(text)
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
    ui.card = lambda *a, **k: _make("card")
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.element = lambda tag="div", *a, **k: _make(f"element:{tag}")

    def _button(*args: Any, **kw: Any) -> _Node:
        """ui.button 的第一个位置参数是文案（本页的返回/重试/刷新都靠它定位）。

        真实 NiceGUI 用 on_click= 绑定点击；桩这边要把它登记进 handlers，
        否则"点了没反应"会被误判成页面 bug（踩过一次）。
        """
        text: str = str(args[0]) if args else ""
        node: _Node = _make("button", text=text, **kw)
        on_click = kw.get("on_click")
        if on_click is not None:
            node.handlers.setdefault("click", []).append(on_click)
        return node

    ui.button = _button

    def _notify(message: str = "", **kw: Any) -> None:
        _NOTIFY.append((str(message), str(kw.get("type", ""))))

    ui.notify = _notify
    ui.timer = lambda *a, **k: _make("timer")
    ui.navigate = types.SimpleNamespace(to=lambda url: _NAVIGATED.append(str(url)))

    mod.ui = ui
    mod.app = types.ModuleType("nicegui.app")
    mod.app.storage = types.SimpleNamespace(user={})
    return mod


# ------------------------------------------- 其余依赖桩 + 需求方给的真实记录

STRATEGY_ID: str = "6a5884d528893aa08c3e805b"

# 记录 A：2 笔交易，收益率 +5.43%（有交易明细可断言）
RECORD_A: dict[str, Any] = {
    "_id": "6aa81b646f92057685a06598",
    "strategy_id": STRATEGY_ID,
    "result_data": {
        "strategy_id": STRATEGY_ID,
        "strategy_name": "强势反弹策略",
        "backtest_return_rate": 0.054299999999999994,
        "backtest_profit": 5430.0,
        "benchmark_return_rate": 0.0,
        "start_date": "2026-06-01",
        "end_date": "2026-09-12",
        "initial_amount": 100000.0,
        "max_drawdown": 0.0,
        "frequency": "daily",
        "create_time": "2026-09-15 00:05:55",
        "trades": [
            {"code": "002415", "buy_date": "2026-07-29 00:00:00", "sell_date": "2026-08-05 00:00:00",
             "buy_price": 35.890000000000001, "sell_price": 37.838999999999999,
             "profit_pct": 5.4299999999999997},
            {"code": "002415", "buy_date": "2026-08-04 00:00:00", "sell_date": "2026-08-11 00:00:00",
             "buy_price": 37.632300000000001, "sell_price": 35.801400000000001,
             "profit_pct": -4.8700000000000001},
        ],
        "summary": {
            "total_trades": 2, "win_count": 1, "loss_count": 1, "win_rate": 50.0,
            "avg_profit": 0.28000000000000003, "max_profit": 5.4299999999999997,
            "max_loss": -4.8700000000000001, "annualized_return_pct": 20.030000000000001,
            "benchmark_annualized_pct": 0.0, "sharpe_ratio": 9.125, "max_drawdown_pct": 0.0,
            "alpha": 0.0, "initial_capital": 100000.0, "final_capital": 105430.0,
            "start_date": "2026-06-01", "end_date": "2026-09-12", "benchmark_code": "",
            "trading_days": 73,
        },
    },
    "create_time": "2026-09-15 00:05:56",
}

# 记录 B：0 笔交易（更**新**的一条 ⇒ 默认应选中它）
RECORD_B: dict[str, Any] = {
    "_id": "6aa81cbcd097eabd87115d76",
    "strategy_id": STRATEGY_ID,
    "result_data": {
        "strategy_id": STRATEGY_ID,
        "strategy_name": "强势反弹策略",
        "backtest_return_rate": 0.0,
        "backtest_profit": 0.0,
        "benchmark_return_rate": 0.0,
        "start_date": "2026-06-01",
        "end_date": "2026-09-12",
        "initial_amount": 100000.0,
        "max_drawdown": 0.0,
        "frequency": "daily",
        "create_time": "2026-09-15 00:11:39",
        "trades": [],
        "summary": {
            "total_trades": 0, "win_count": 0, "loss_count": 0, "win_rate": 0.0,
            "avg_profit": 0.0, "max_profit": 0.0, "max_loss": 0.0,
            "annualized_return_pct": 0.0, "benchmark_annualized_pct": 0.0,
            "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "alpha": 0.0,
            "initial_capital": 100000.0, "final_capital": 100000.0,
            "start_date": "2026-06-01", "end_date": "2026-09-12",
            "benchmark_code": "", "trading_days": 0,
        },
    },
    "create_time": "2026-09-15 00:11:40",
}

TEMPLATES: dict[str, dict[str, Any]] = {
    STRATEGY_ID: {"name": "强势反弹策略", "strategy_type": "选股策略"},
}

API_CALLS: list[str] = []
BACKEND_ERROR: list[Exception | None] = [None]


class _StrategyApiStub:
    """strategy_api 桩：记录调用参数，可注入异常。"""

    def get_backtest(self, strategy_id: str) -> list[dict[str, Any]]:
        API_CALLS.append(str(strategy_id))
        error = BACKEND_ERROR[0]
        if error is not None:
            raise error
        # 返回顺序刻意"旧在前"，用来验证页面自己会倒序排
        return [dict(RECORD_A), dict(RECORD_B)]


class _ThemeStub:
    """与真实 ThemeManager 的取色 API 形状一致。"""

    def var(self, name: str) -> str:
        return f"var(--qs-{name})"

    def var_rgba(self, name: str, alpha: float) -> str:
        return f"rgba(var(--qs-{name}-rgb), {alpha})"


class _StrategyCacheStub:
    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {i: TEMPLATES[i] for i in ids if i in TEMPLATES}


class _AppContextStub:
    theme_manager = _ThemeStub()
    strategy_api = _StrategyApiStub()
    strategy_cache = _StrategyCacheStub()


def _install_stubs() -> None:
    """把桩模块注入 sys.modules。"""
    sys.modules["nicegui"] = _build_nicegui_stub()

    ac = types.ModuleType("app_context")
    ac.AppContext = _AppContextStub
    sys.modules["app_context"] = ac

    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["core"] = core_pkg

    # core.routes 无外部依赖（只用 urllib.parse），直接 exec 真源码，
    # 免得把路由/URL 构造逻辑复制一份到测试里
    routes = types.ModuleType("core.routes")
    exec(compile(ROUTES_SRC.read_text(encoding="utf-8"),
                 str(ROUTES_SRC), "exec"), routes.__dict__)
    sys.modules["core.routes"] = routes

    concurrency = types.ModuleType("core.concurrency")

    async def run_blocking(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """真实实现丢线程池；冒烟检查同步调用即可覆盖被调函数本身。"""
        return fn(*args, **kwargs)

    class PollingTask:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def start(self) -> None:
            pass

    concurrency.run_blocking = run_blocking
    concurrency.PollingTask = PollingTask
    sys.modules["core.concurrency"] = concurrency

    # utils.timeutils 同样 exec 真源码（本页的时间排序/展示依赖它的解析语义）
    utils_pkg = types.ModuleType("utils")
    utils_pkg.__path__ = [str(ROOT / "utils")]
    sys.modules["utils"] = utils_pkg
    timeutils = types.ModuleType("utils.timeutils")
    exec(compile((ROOT / "utils" / "timeutils.py").read_text(encoding="utf-8"),
                 "utils/timeutils.py", "exec"), timeutils.__dict__)
    sys.modules["utils.timeutils"] = timeutils


def _load_page_module() -> types.ModuleType:
    """exec 页面源码，返回模块对象。"""
    module = types.ModuleType("strategy_backtest_under_test")
    module.__file__ = str(PAGE_SRC)
    exec(compile(PAGE_SRC.read_text(encoding="utf-8"), str(PAGE_SRC), "exec"), module.__dict__)
    return module


# ------------------------------------------------------------------ 断言工具

def _reset_tree() -> _Node:
    _STACK.clear()
    root = _Node("root")
    _STACK.append(root)
    return root


def _labels(root: _Node) -> list[str]:
    return [n.text() for n in root.walk() if n.kind == "label"]


def _find(root: _Node, predicate: Any) -> list[_Node]:
    return [n for n in root.walk() if predicate(n)]


def _history_items(root: _Node) -> list[_Node]:
    return _find(root, lambda n: "bt-history-item" in n.class_text())


def _button(root: _Node, text: str) -> _Node | None:
    for node in root.walk():
        if node.kind == "button" and node.text() == text:
            return node
    return None


def _click(node: _Node, event: str = "click") -> None:
    """触发元素的某个事件处理器（同步或异步都能跑）。"""
    for handler in node.handlers.get(event, []):
        result = handler()
        if asyncio.iscoroutine(result):
            asyncio.run(result)


def _value_after(root: _Node, label: str) -> str | None:
    """取「指标卡/汇总项」里某个标签紧邻的值。"""
    nodes: list[_Node] = root.walk()
    for index, node in enumerate(nodes):
        if node.kind == "label" and node.text() == label:
            for nxt in nodes[index + 1:]:
                if nxt.kind == "label":
                    return nxt.text()
    return None


def _render_page(strategy_id: str = STRATEGY_ID, source: str = "") -> _Node:
    root = _reset_tree()
    module = _load_page_module()
    module.show_strategy_backtest_page(strategy_id=strategy_id, source=source)
    return root


# ---------------------------------------------------------------------- 主流程

def main() -> int:
    _install_stubs()
    page = _load_page_module()

    # ---------- 1) 纯函数：归一化 / 排序 / 格式化 ----------
    print("=" * 72)
    print("1) 纯函数：记录归一化 / 排序 / 数值格式化")
    print("=" * 72)
    norm_a = page._normalize_record(RECORD_A)
    check("归一化：create_time 取记录顶层",
          norm_a["create_time"] == "2026-09-15 00:05:56", norm_a["create_time"])
    check("归一化：指标取自 result_data（收益率比例原样）",
          abs(norm_a["return_rate"] - 0.0543) < 1e-9, repr(norm_a["return_rate"]))
    check("归一化：收益额", norm_a["profit"] == 5430.0, repr(norm_a["profit"]))
    check("归一化：trades 2 笔", len(norm_a["trades"]) == 2, str(len(norm_a["trades"])))
    check("归一化：summary 非空", norm_a["summary"].get("sharpe_ratio") == 9.125,
          str(norm_a["summary"].get("sharpe_ratio")))
    check("归一化：strategy_name 取自 result_data",
          norm_a["strategy_name"] == "强势反弹策略", norm_a["strategy_name"])

    # 打平形态（指标直接在记录顶层、没有 result_data）也要能读出来
    flat = page._normalize_record({
        "_id": "flat1", "create_time": "2026-09-14 10:00:00",
        "backtest_return_rate": 0.12, "backtest_profit": 12000.0,
        "max_drawdown": 0.2, "frequency": "weekly",
    })
    check("归一化：兼容打平形态（无 result_data）",
          abs(flat["return_rate"] - 0.12) < 1e-9 and flat["profit"] == 12000.0,
          f"{flat['return_rate']} / {flat['profit']}")

    sorted_records = page._sort_records([norm_a, page._normalize_record(RECORD_B)])
    check("排序：按创建时间倒序（最新在前）",
          [r["record_id"] for r in sorted_records] == [RECORD_B["_id"], RECORD_A["_id"]],
          str([r["create_time"] for r in sorted_records]))
    no_time = page._normalize_record({"_id": "x1"})
    check("排序：时间缺失的记录排到最后且不丢",
          page._sort_records([no_time, norm_a])[-1]["record_id"] == "x1")

    check("格式化：比例→百分数带符号 _fmt_pct(5.43) == '+5.43%'",
          page._fmt_pct(5.43) == "+5.43%", page._fmt_pct(5.43))
    check("格式化：_fmt_pct(0.0, signed=False) == '0.00%'",
          page._fmt_pct(0.0, signed=False) == "0.00%", page._fmt_pct(0.0, signed=False))
    check("格式化：_fmt_pct(-4.87) == '-4.87%'", page._fmt_pct(-4.87) == "-4.87%",
          page._fmt_pct(-4.87))
    check("格式化：_fmt_money(5430) == '+¥5,430'", page._fmt_money(5430.0) == "+¥5,430",
          page._fmt_money(5430.0))
    check("格式化：_fmt_money(0) == '¥0'", page._fmt_money(0.0) == "¥0", page._fmt_money(0.0))
    check("格式化：_fmt_number(9.125) 保留有效位", page._fmt_number(9.125) == "9.125",
          page._fmt_number(9.125))
    check("格式化：_fmt_number(0) == '0'", page._fmt_number(0.0) == "0", page._fmt_number(0.0))
    check("配色：A 股惯例 正=positive / 负=negative / 零=muted",
          (page._sign_token(1.0), page._sign_token(-1.0), page._sign_token(0.0))
          == ("positive", "negative", "muted"),
          str((page._sign_token(1.0), page._sign_token(-1.0), page._sign_token(0.0))))

    metrics_a = dict((label, value) for label, value, _token in page._key_metrics(norm_a))
    check("指标卡：收益率由比例换算成 +5.43%", metrics_a["回测收益率"] == "+5.43%",
          metrics_a["回测收益率"])
    check("指标卡：收益额 +¥5,430", metrics_a["回测收益额"] == "+¥5,430", metrics_a["回测收益额"])
    check("指标卡：年化取 summary 的百分数（不再乘 100）", metrics_a["年化收益率"] == "+20.03%",
          metrics_a["年化收益率"])
    check("指标卡：夏普 9.125", metrics_a["夏普比率"] == "9.125", metrics_a["夏普比率"])
    check("指标卡：最大回撤为 0 时不带符号", metrics_a["最大回撤"] == "0.00%",
          metrics_a["最大回撤"])

    items = dict((label, value) for label, value, _t in page._summary_items(
        {"total_trades": 2, "win_rate": 50.0, "sharpe_ratio": 9.125}))
    check("汇总：缺失字段显示 '-'（不把'后端没给'误读成 0）",
          items["年化收益率"] == "-" and items["交易日数"] == "-", str(items["年化收益率"]))
    check("汇总：胜率显示 50.00%", items["胜率"] == "+50.00%", items["胜率"])
    check("汇总：基准代码为空时显示 '-'", items["基准代码"] == "-", items["基准代码"])

    rows = page._trade_rows(norm_a["trades"])
    check("交易明细：2 行且日期截到天",
          len(rows) == 2 and rows[0]["buy_date"] == "2026-07-29" and rows[0]["sell_date"] == "2026-08-05",
          str(rows[0]))
    check("交易明细：价格 ¥35.89 / ¥37.84",
          rows[0]["buy_price"] == "¥35.89" and rows[0]["sell_price"] == "¥37.84",
          f"{rows[0]['buy_price']} / {rows[0]['sell_price']}")
    check("交易明细：盈亏率与配色（盈红亏绿）",
          rows[0]["profit_pct"] == "+5.43%" and rows[0]["token"] == "positive"
          and rows[1]["profit_pct"] == "-4.87%" and rows[1]["token"] == "negative",
          f"{rows[0]['profit_pct']}/{rows[0]['token']} {rows[1]['profit_pct']}/{rows[1]['token']}")

    # ---------- 2) 页面渲染结构 ----------
    print()
    print("=" * 72)
    print("2) 页面渲染（带 strategy_id）")
    print("=" * 72)
    API_CALLS.clear()
    root = _render_page()
    check("页面渲染未抛异常", True)
    check("向后端查询的是传入的策略模板 ID",
          API_CALLS == [STRATEGY_ID], str(API_CALLS))
    check("标题为「策略回测记录」", "策略回测记录" in _labels(root))
    subtitle = next((t for t in _labels(root) if "条回测记录" in t), "")
    check("副标题含策略名与记录条数",
          "强势反弹策略" in subtitle and "2 条回测记录" in subtitle, subtitle)

    back_btn = _button(root, "返回策略监控")
    check("来源缺失时返回按钮默认指向策略监控", back_btn is not None)
    if back_btn is not None:
        _NAVIGATED.clear()
        _click(back_btn)
        check("返回按钮跳父页面 /strategy-monitor",
              _NAVIGATED == ["/strategy-monitor"], str(_NAVIGATED))

    items_nodes = _history_items(root)
    check("左栏列出全部 2 条回测记录", len(items_nodes) == 2, str(len(items_nodes)))

    # ---------- 3) 默认选中最新一条 ----------
    print()
    print("=" * 72)
    print("3) 默认选中最新一条（倒序）")
    print("=" * 72)
    selected = [n for n in items_nodes if "rgba(var(--qs-primary-rgb), 0.18)" in n.style_text()]
    check("有且只有一条处于选中态", len(selected) == 1, str(len(selected)))
    if selected:
        # 历史条目的时间戳展示到分钟（create_time[:16]）
        check("默认选中的是最新的那条（2026-09-15 00:11）",
              "2026-09-15 00:11" in " ".join(t for t in
                                             [n.text() for n in selected[0].walk() if n.kind == "label"]),
              " / ".join(n.text() for n in selected[0].walk() if n.kind == "label"))
    # 最新那条没有交易 ⇒ 明细区应显式说明，而不是留一片空白
    check("最新记录（0 笔交易）明细区给出说明",
          any("没有产生任何委托成交" in t for t in _labels(root)),
          str([t for t in _labels(root) if "成交" in t][:2]))
    check("最新记录指标卡：收益率 0.00%", _value_after(root, "回测收益率") == "+0.00%",
          str(_value_after(root, "回测收益率")))
    check("汇总区展示总交易次数", _value_after(root, "总交易次数") == "0",
          str(_value_after(root, "总交易次数")))

    # ---------- 4) 切换到另一条记录 ----------
    print()
    print("=" * 72)
    print("4) 点击历史条目切换详情")
    print("=" * 72)
    old_item = next((n for n in _history_items(root)
                     if "2026-09-15 00:05" in " ".join(x.text() for x in n.walk() if x.kind == "label")),
                    None)
    check("找到较早那条记录的条目", old_item is not None)
    if old_item is not None:
        _click(old_item)
        labels_after = _labels(root)
        check("切换后详情显示该记录的收益率 +5.43%",
              _value_after(root, "回测收益率") == "+5.43%", str(_value_after(root, "回测收益率")))
        check("切换后收益额 +¥5,430", _value_after(root, "回测收益额") == "+¥5,430",
              str(_value_after(root, "回测收益额")))
        check("切换后汇总显示总交易次数 2", _value_after(root, "总交易次数") == "2",
              str(_value_after(root, "总交易次数")))
        check("切换后汇总显示胜率", _value_after(root, "胜率") == "+50.00%",
              str(_value_after(root, "胜率")))
        check("切换后出现交易明细表头",
              all(h in labels_after for h in ("股票代码", "买入日期", "卖出日期", "买入价", "卖出价", "盈亏率")),
              str([t for t in labels_after if t in ("股票代码", "买入日期", "盈亏率")]))
        check("交易明细渲染 2 行（含股票代码 002415）",
              labels_after.count("002415") == 2, str(labels_after.count("002415")))
        check("交易明细显示盈亏率 +5.43% / -4.87%",
              "+5.43%" in labels_after and "-4.87%" in labels_after)
        check("切换后选中态跟着移动（仍是唯一一条高亮）",
              len([n for n in _history_items(root)
                   if "rgba(var(--qs-primary-rgb), 0.18)" in n.style_text()]) == 1)

    # ---------- 5) 刷新按钮 ----------
    print()
    print("=" * 72)
    print("5) 刷新按钮")
    print("=" * 72)
    refresh_btn = _button(root, "刷新")
    check("存在「刷新」按钮", refresh_btn is not None)
    if refresh_btn is not None:
        API_CALLS.clear()
        _NOTIFY.clear()
        _click(refresh_btn)
        check("刷新会重新请求后端", API_CALLS == [STRATEGY_ID], str(API_CALLS))
        check("刷新后给出提示", any("已刷新" in m for m, _ in _NOTIFY),
              str([m for m, _ in _NOTIFY]))

    # ---------- 6) 容错 ----------
    print()
    print("=" * 72)
    print("6) 容错：无 strategy_id / 无记录 / 请求失败 / 字段缺失")
    print("=" * 72)
    API_CALLS.clear()
    root_blank = _render_page("")
    check("无 strategy_id 时不请求后端", API_CALLS == [], str(API_CALLS))
    check("无 strategy_id 时给出「未指定要查看的策略」",
          any("未指定要查看的策略" in t for t in _labels(root_blank)))
    check("无 strategy_id 时仍有返回入口", _button(root_blank, "返回策略监控") is not None)
    check("无 strategy_id 时历史区也不空着",
          any("未指定策略" in t for t in _labels(root_blank)))

    # 后端返回空列表
    saved_records = _StrategyApiStub.get_backtest
    try:
        _StrategyApiStub.get_backtest = lambda self, sid: []  # type: ignore[assignment]
        root_none = _render_page()
        check("无回测记录时给出说明",
              any("还没有回测记录" in t for t in _labels(root_none)),
              str([t for t in _labels(root_none) if "回测记录" in t][:3]))
        check("无回测记录时不渲染历史条目", len(_history_items(root_none)) == 0)
    finally:
        _StrategyApiStub.get_backtest = saved_records  # type: ignore[assignment]

    # 请求失败 → 错误态 + 重试
    BACKEND_ERROR[0] = RuntimeError("后端不可用")
    try:
        root_err = _render_page()
        check("请求失败时页面仍能渲染", True)
        check("失败给出错误标题（不退化成空白页）",
              any("回测记录加载失败" in t for t in _labels(root_err)))
        check("失败时带出异常原因", any("后端不可用" in t for t in _labels(root_err)))
        retry_btn = _button(root_err, "重试")
        check("失败时有「重试」按钮", retry_btn is not None)
        if retry_btn is not None:
            # 后端恢复后重试应能正常渲染
            BACKEND_ERROR[0] = None
            API_CALLS.clear()
            _click(retry_btn)
            check("重试会重新请求后端", API_CALLS == [STRATEGY_ID], str(API_CALLS))
            check("重试成功后渲染出记录", len(_history_items(root_err)) == 2,
                  str(len(_history_items(root_err))))
    finally:
        BACKEND_ERROR[0] = None

    check("字段缺失的记录不会让页面崩",
          isinstance(page._normalize_record({}), dict)
          and page._normalize_record({})["trades"] == []
          and page._normalize_record({})["return_rate"] == 0.0)

    # ---------- 7) 返回按钮跟随入口 ----------
    print()
    print("=" * 72)
    print("7) 返回按钮跟随入口（source 查询参数）")
    print("=" * 72)
    import re  # noqa: E402  （只在本节与下一节用，放这里避免与上面重名）

    routes = sys.modules["core.routes"]

    # 从策略列表进入 → 文案与跳转都指向策略列表
    root_from_list = _render_page(source="strategies")
    list_back = _button(root_from_list, "返回策略列表")
    check("从策略列表进来时按钮文案为「返回策略列表」", list_back is not None,
          str([n.text() for n in root_from_list.walk() if n.kind == "button"]))
    if list_back is not None:
        _NAVIGATED.clear()
        _click(list_back)
        check("从策略列表进来时返回到 /strategies",
              _NAVIGATED == ["/strategies"], str(_NAVIGATED))

    # 从策略监控进入 → 回监控页
    root_from_monitor = _render_page(source="monitor")
    monitor_back = _button(root_from_monitor, "返回策略监控")
    check("从策略监控进来时按钮文案为「返回策略监控」", monitor_back is not None)
    if monitor_back is not None:
        _NAVIGATED.clear()
        _click(monitor_back)
        check("从策略监控进来时返回到 /strategy-monitor",
              _NAVIGATED == ["/strategy-monitor"], str(_NAVIGATED))

    # 未指定策略的空态下，**每一个**返回入口都要跟随来源
    # （断言「存在某个返回按钮」不够：页头那个按钮已经跟随来源，
    #   空态里写死的那个会被漏掉，所以这里按文案逐个比对）
    root_blank_list = _render_page("", source="strategies")
    blank_backs: list[_Node] = [
        n for n in root_blank_list.walk()
        if n.kind == "button" and n.text().startswith("返回")
    ]
    check("未指定策略的空态下有返回入口（页头 + 空态各一个）",
          len(blank_backs) == 2, str([n.text() for n in blank_backs]))
    check("未指定策略的空态下所有返回入口都跟随来源（没有写死的）",
          bool(blank_backs) and all(n.text() == "返回策略列表" for n in blank_backs),
          str([n.text() for n in blank_backs]))
    blank_back = blank_backs[-1] if blank_backs else None
    if blank_back is not None:
        _NAVIGATED.clear()
        _click(blank_back)
        check("空态下返回按钮跳 /strategies", _NAVIGATED == ["/strategies"], str(_NAVIGATED))

    # 来源标识按白名单归一化：非法值绝不能变成"跳到外站"或不可达路径
    check("来源白名单：合法值原样返回",
          routes.backtest_source("strategies") == "strategies"
          and routes.backtest_source("monitor") == "monitor",
          f"{routes.backtest_source('strategies')} / {routes.backtest_source('monitor')}")
    check("来源白名单：空值 / 非法值回退 monitor",
          routes.backtest_source("") == "monitor"
          and routes.backtest_source("https://evil.example.com") == "monitor"
          and routes.backtest_source("../login") == "monitor",
          f"{routes.backtest_source('')} / {routes.backtest_source('https://evil.example.com')}")
    check("来源白名单：每个合法来源都在路由表里（否则退化成 404）",
          all(k in routes.PAGE_ROUTES for k in routes.BACKTEST_SOURCE_KEYS),
          str(routes.BACKTEST_SOURCE_KEYS))

    # ---------- 8) 路由接线一致性 ----------
    print()
    print("=" * 72)
    print("8) 路由接线：登记 / 白名单 / 参数传递 / 入口")
    print("=" * 72)
    check("PAGE_ROUTES 登记了 backtest → /strategy-backtest",
          routes.PAGE_ROUTES.get("backtest") == "/strategy-backtest",
          str(routes.PAGE_ROUTES.get("backtest")))
    check("backtest_route 用查询参数传策略 ID 与来源",
          routes.backtest_route("abc123")
          == "/strategy-backtest?strategy_id=abc123&source=monitor",
          routes.backtest_route("abc123"))
    check("backtest_route 支持指定来源（策略列表入口）",
          routes.backtest_route("abc123", "strategies")
          == "/strategy-backtest?strategy_id=abc123&source=strategies",
          routes.backtest_route("abc123", "strategies"))
    check("backtest_route 空 ID 时仍带来源（空态也能返回正确的入口页）",
          routes.backtest_route("") == "/strategy-backtest?source=monitor",
          routes.backtest_route(""))
    check("backtest_route 对非法来源做白名单归一化",
          routes.backtest_route("abc123", "http://evil.example.com")
          == "/strategy-backtest?strategy_id=abc123&source=monitor",
          routes.backtest_route("abc123", "http://evil.example.com"))
    check("回测页在中间件白名单内（否则会被当未知路径计入限流）",
          "/strategy-backtest" in routes.ALL_ROUTES
          and "/strategy-backtest" in routes.REFERRER_ROUTES)

    main_txt: str = MAIN_SRC.read_text(encoding="utf-8")
    registered: list[str] = re.findall(r"@ui\.page\('([^']+)'\)", main_txt)
    unregistered: list[str] = [p for p in registered if p not in routes.ALL_ROUTES]
    check("main.py 注册的每个页面路径都在白名单里（历史 bug：漏登记 ⇒ 被限流误拦）",
          not unregistered, f"已注册 {registered}，漏登记 {unregistered}")
    check("/strategy-backtest 已注册到 main.py", "/strategy-backtest" in registered)
    check("页函数接收查询参数 strategy_id 与 source",
          "def strategy_backtest_page_view(strategy_id: str = '', source: str = '')"
          in main_txt)
    check("查询参数被传给页面内容函数（来源已归一化）",
          "partial(_show, strategy_id, source=back_key)" in main_txt)
    check("main.py 引入了 backtest_source 做白名单归一化",
          "from core.routes import backtest_source" in main_txt)
    check("侧边栏高亮跟随入口（不再是写死的 monitor）",
          "back_key: str = backtest_source(source)" in main_txt
          and "page_key=back_key" in main_txt)
    # 高亮值本身必须是合法 page_key，否则侧边栏会一项都不高亮
    check("侧边栏高亮的取值来自白名单",
          all(k in routes.PAGE_ROUTES for k in routes.BACKTEST_SOURCE_KEYS),
          str(routes.BACKTEST_SOURCE_KEYS))

    monitor_txt: str = pathlib.Path(
        os.environ.get("SMOKE_MONITOR_SRC", str(ROOT / "pages" / "strategy_monitor.py"))
    ).read_text(encoding="utf-8")
    strategies_txt: str = pathlib.Path(
        os.environ.get("SMOKE_STRATEGIES_SRC", str(ROOT / "pages" / "strategies.py"))
    ).read_text(encoding="utf-8")
    check("策略监控页用 backtest_route 构造跳转并带 source=monitor（不是硬编码路径）",
          'ui.navigate.to(backtest_route(sid, "monitor"))' in monitor_txt)
    check("策略列表页入口用 backtest_route 并带 source=strategies",
          'ui.navigate.to(backtest_route(sid, "strategies"))' in strategies_txt)
    check("策略列表页入口带的是行里的策略模板 ID（row['sid']）",
          'str(row.get("sid", "") or "").strip()' in strategies_txt)

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

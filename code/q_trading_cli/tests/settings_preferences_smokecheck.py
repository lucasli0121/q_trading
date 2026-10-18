#!/usr/bin/env python3
"""
Date: 2026-09-30
Description: 个人喜好页离线冒烟检查 —— 用桩替代 nicegui / app_context / colors.theme，
    真跑一次 show_preferences_page() 并断言「推送星级」那一组滑块的行为。

为什么需要这个脚本：
    本机没有 nicegui，页面代码里以下几类问题静态工具全都拦不住：
      1. 闭包引用顺序 —— 星级区的显隐函数在「定义之前被调用」时，pyflakes 报
         undefined name，但真正致命的是改回去之后**初始显隐没生效**；
      2. ui.slider 的参数与 props 写错（min/max/step 传反、props 拼错）；
      3. 保存载荷漏字段 —— PUT 是全量替换，少了 push_buy_min_star /
         push_sell_min_star 就等于把用户的星级清成 0（不报错，静默丢失）；
      4. 拖动滑块后没写回 state / 没刷新文案 —— 看起来能拖，保存的还是旧值。

做法：注入最小桩到 sys.modules，exec 页面源码后调用入口函数，
    遍历桩元素树读 slider 的 min/max/step/value、容器 visible 与保存载荷。

运行：python tests/settings_preferences_smokecheck.py   （exit 0 = 通过）
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import types
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 允许用环境变量指向改写过的页面副本，用于反向验证（改坏后重跑必须报错）
PAGE_SRC = pathlib.Path(
    os.environ.get(
        "SMOKE_PREFERENCES_SRC", str(ROOT / "pages" / "settings_preferences.py")
    )
)

logging.disable(logging.CRITICAL)

_FAILED: list[str] = []
_PASSED: list[str] = []


def check(name: str, ok: bool, detail: Any = "") -> None:
    """记录一条断言结果。"""
    if ok:
        _PASSED.append(name)
        print(f"[OK]   {name}")
    else:
        _FAILED.append(name)
        print(f"[FAIL] {name}  -> {detail}")


# ---------------------------------------------------------------- 元素桩

_ROOTS: list["_Node"] = []
_STACK: list["_Node"] = []


class _Node:
    """元素桩：保留 kind / 关键字 / classes / style / props，支持嵌套与遍历。"""

    def __init__(self, kind: str, **kw: Any) -> None:
        self.kind = kind
        self.kw = dict(kw)
        self.value = kw.get("value")
        self.text = str(kw.get("text", "") or "")
        self.visible = True
        self.props_list: list[str] = []
        self.styles: list[str] = []
        self.classes_list: list[str] = list(str(kw.get("classes", "") or "").split())
        self.children: list[_Node] = []
        self.handlers: list[tuple[str, Any]] = []

    def __enter__(self) -> "_Node":
        _STACK.append(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        _STACK.pop()

    def classes(self, add: str) -> "_Node":
        self.classes_list.extend(str(add).split())
        return self

    def style(self, add: str) -> "_Node":
        self.styles.append(str(add))
        return self

    def props(self, add: str) -> "_Node":
        self.props_list.append(str(add))
        return self

    def set_text(self, text: Any) -> None:
        self.text = str(text)

    def set_visibility(self, visible: bool) -> None:
        self.visible = bool(visible)

    def on(self, event: str, handler: Any) -> "_Node":
        self.handlers.append((event, handler))
        return self

    def style_text(self) -> str:
        return " ".join(self.styles)

    def class_text(self) -> str:
        return " ".join(self.classes_list)

    def props_text(self) -> str:
        return " ".join(self.props_list)

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


# 偏好保存记录：[(载荷字典)]
_SAVED: list[dict[str, Any]] = []


def _build_nicegui_stub() -> types.ModuleType:
    mod = types.ModuleType("nicegui")
    ui = types.ModuleType("nicegui.ui")

    ui.card = lambda *a, **k: _make("card", **k)
    ui.column = lambda *a, **k: _make("column", **k)
    ui.row = lambda *a, **k: _make("row", **k)
    # 页面里文字既有位置参数（ui.button("保存更改")）也有关键字，桩两边都要接住
    ui.label = lambda text="", *a, **k: _make("label", text=str(text))
    ui.button = lambda *a, **k: _make("button", text=str(a[0]) if a else "", **k)
    ui.input = lambda *a, **k: _make("input", **k)
    ui.switch = lambda *a, **k: _make("switch", **k)
    ui.slider = lambda *a, **k: _make("slider", **k)
    ui.dialog = lambda *a, **k: _make("dialog", **k)
    ui.icon = lambda name="", *a, **k: _make("icon", name=str(name))
    ui.notify = lambda *a, **k: None
    ui.add_css = lambda *a, **k: None
    ui.timer = lambda *a, **k: _make("timer", **k)

    mod.ui = ui
    mod.app = types.SimpleNamespace(storage=types.SimpleNamespace(user={}))
    return mod


class _PreferenceApiStub:
    """偏好接口桩：update 记录载荷，get 返回注入的偏好。"""

    def __init__(self, prefs: dict[str, Any]) -> None:
        self._prefs = dict(prefs)

    def get(self) -> dict[str, Any]:
        return dict(self._prefs)

    def update(self, preference: dict[str, Any]) -> dict[str, Any]:
        _SAVED.append(dict(preference))
        self._prefs.update(preference)
        return dict(preference)


class _ThemeStub:
    """主题管理器桩：只提供页面用到的接口，颜色返回非字面量占位值。"""

    def __init__(self) -> None:
        self.mode = "light"
        self.remembered: list[str] = []

    def session_mode(self) -> str:
        return self.mode

    def normalize_mode(self, mode: str | None) -> str:
        return "dark" if str(mode) == "dark" else "light"

    def set_theme(self, mode: str) -> None:
        self.mode = mode

    def remember_mode(self, mode: str) -> None:
        self.remembered.append(str(mode))

    def is_dark(self) -> bool:
        return self.mode == "dark"

    def toggle(self) -> str:
        self.mode = "dark" if self.mode == "light" else "light"
        return self.mode

    def var(self, name: str) -> str:
        return f"var(--qs-{name})"

    def get_color(self, token: str) -> str:
        return f"token-{token}"


def _install(prefs: dict[str, Any]) -> tuple[types.ModuleType, _ThemeStub]:
    """装配桩并返回 (页面模块, 主题桩)。"""
    for name in ("nicegui", "app_context", "colors", "colors.theme"):
        sys.modules.pop(name, None)

    theme_stub = _ThemeStub()
    sys.modules["nicegui"] = _build_nicegui_stub()

    colors_mod = types.ModuleType("colors")
    theme_mod = types.ModuleType("colors.theme")

    def resolve_mode_from_prefs(prefs_dict: dict[str, Any]) -> str | None:
        """复刻真实现语义：只有「用户真的存过」才认记录里的主题。"""
        if not prefs_dict:
            return None
        if str(prefs_dict.get("id", "") or "") or str(prefs_dict.get("update_time", "") or ""):
            return str(prefs_dict.get("theme_mode", "") or "") or None
        return None

    theme_mod.resolve_mode_from_prefs = resolve_mode_from_prefs
    theme_mod.ThemeManager = lambda *a, **k: theme_stub
    colors_mod.theme = theme_mod
    sys.modules["colors"] = colors_mod
    sys.modules["colors.theme"] = theme_mod

    app_ctx = types.ModuleType("app_context")

    class AppContext:
        _instance = None

        def __new__(cls) -> "AppContext":
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

        def __init__(self) -> None:
            if not hasattr(self, "_init"):
                self._init = True
                self.theme_manager = theme_stub
                self.preference_api = _PreferenceApiStub(prefs)

    app_ctx.AppContext = AppContext
    sys.modules["app_context"] = app_ctx

    spec_src = PAGE_SRC.read_text(encoding="utf-8")
    page = types.ModuleType("settings_preferences_page")
    page.__file__ = str(PAGE_SRC)
    exec(compile(spec_src, str(PAGE_SRC), "exec"), page.__dict__)  # noqa: S102
    return page, theme_stub


def _render(prefs: dict[str, Any]) -> tuple[types.ModuleType, list[_Node]]:
    """渲染一次页面，返回 (页面模块, 根节点列表)。"""
    del _ROOTS[:]
    del _SAVED[:]
    page, _ = _install(prefs)
    page.show_preferences_page()
    return page, list(_ROOTS)


def _all(root: list[_Node], kind: str) -> list[_Node]:
    out: list[_Node] = []
    for node in root:
        out.extend(n for n in node.walk() if n.kind == kind)
    return out


def _labeled_sliders(root: list[_Node]) -> dict[str, _Node]:
    """按标题找到两个滑块：买入 / 卖出。"""
    result: dict[str, _Node] = {}
    for slider in _all(root, "slider"):
        parent = _parent_column(slider, root)
        if parent is None:
            continue
        for label in parent.children:
            if label.kind == "row":
                texts = [c.text for c in label.walk() if c.kind == "label"]
                if "买入信号最小星级" in texts:
                    result["buy"] = slider
                if "卖出信号最小星级" in texts:
                    result["sell"] = slider
    return result


def _parent_column(node: _Node, root: list[_Node]) -> _Node | None:
    for top in root:
        for cand in top.walk():
            if any(child is node for child in cand.children):
                return cand
    return None


def _save_button(root: list[_Node]) -> _Node:
    """取「保存更改」按钮。"""
    for button in _all(root, "button"):
        if button.kw.get("text") == "保存更改":
            return button
    return _Node("button")


def _handler(node: _Node, name: str = "on_click") -> Any:
    """取控件回调：页面既用关键字（on_click=）也用 .on()，两边都要能取到。"""
    fn = node.kw.get(name)
    if fn is not None:
        return fn
    event = "click" if name == "on_click" else "change"
    for evt, handler in node.handlers:
        if evt == event:
            return handler
    return None


def _star_card(root: list[_Node]) -> _Node | None:
    """找到「推送星级」那张卡片。"""
    for card in _all(root, "card"):
        texts = [c.text for c in card.walk() if c.kind == "label"]
        if "推送星级" in texts:
            return card
    return None


# ---------------------------------------------------------------- 用例

print("--- 第 1 节 滑块本身的参数（0~5、步长 0.5、默认值） ---")
PREFS = {
    "id": "pref-1",
    "user_id": "u-1",
    "theme_mode": "light",
    "enable_wx_push": True,
    "wx_push_url": "https://example.com/hook",
    "enable_phone_text": False,
    "phone": "",
    "push_buy_min_star": 3.5,
    "push_sell_min_star": 0.0,
    "update_time": "2026-09-30 10:00:00",
}
page, roots = _render(PREFS)
sliders = _labeled_sliders(roots)
check("两个星级滑块都渲染出来了", set(sliders) == {"buy", "sell"}, f"{sorted(sliders)}")
if set(sliders) == {"buy", "sell"}:
    buy = sliders["buy"]
    check("滑块 min = 0", buy.kw.get("min") == 0.0, f"{buy.kw.get('min')}")
    check("滑块 max = 5.0（与服务端 MAX_PUSH_STAR 一致）",
          buy.kw.get("max") == 5.0, f"{buy.kw.get('max')}")
    check("滑块 step = 0.5", buy.kw.get("step") == 0.5, f"{buy.kw.get('step')}")
    check("滑块初值 = 服务端保存的买入星级 3.5",
          buy.value == 3.5, f"{buy.value}")
    check("卖出滑块初值 = 0", sliders["sell"].value == 0.0,
          f"{sliders['sell'].value}")
    check("滑块带 label-always（拖动时显示数值气泡）",
          "label-always" in buy.props_text(), buy.props_text())

print("\n--- 第 2 节 星级区的显隐跟推送开关联动 ---")
card = _star_card(roots)
check("找到了星级卡片", card is not None)
if card is not None:
    check("微信推送开着 ⇒ 星级区显示", card.visible is True, f"{card.visible}")

# 两个开关都关 ⇒ 整块隐藏
_, roots_off = _render({**PREFS, "enable_wx_push": False, "enable_phone_text": False})
card_off = _star_card(roots_off)
check("两个推送开关都关 ⇒ 星级区隐藏",
      card_off is not None and card_off.visible is False,
      f"{card_off and card_off.visible}")

# 只开短信 ⇒ 也要显示（星级是两个通道共用的）
_, roots_sms = _render({**PREFS, "enable_wx_push": False, "enable_phone_text": True})
card_sms = _star_card(roots_sms)
check("只开短信推送 ⇒ 星级区同样显示",
      card_sms is not None and card_sms.visible is True,
      f"{card_sms and card_sms.visible}")

# 页面上切换开关 ⇒ 显隐跟着变
page, roots = _render({**PREFS, "enable_wx_push": False, "enable_phone_text": False})
card = _star_card(roots)
switches = _all(roots, "switch")
# 顺序：深色模式 → 企业微信 → 短信
check("三个开关（深色模式 / 微信 / 短信）", len(switches) == 3, f"{len(switches)}")
wx_switch = switches[1]
on_wx = _handler(wx_switch, "on_change")
check("微信开关挂了 on_change", on_wx is not None)
if on_wx is not None and card is not None:
    on_wx(types.SimpleNamespace(value=True))
    check("打开微信开关 ⇒ 星级区立即显示", card.visible is True, f"{card.visible}")
    on_wx(types.SimpleNamespace(value=False))
    check("关掉微信开关 ⇒ 星级区重新隐藏", card.visible is False, f"{card.visible}")

print("\n--- 第 3 节 拖动滑块 → 写回 state + 刷新文案 + 存进保存载荷 ---")
page, roots = _render(PREFS)
sliders = _labeled_sliders(roots)
buy = sliders["buy"]
on_change = buy.kw.get("on_change")
check("滑块挂了 on_change", on_change is not None)
if on_change is not None:
    on_change(types.SimpleNamespace(value=4.5))
    star_texts = [n.text for n in _all(roots, "label") if "星" in n.text]
    check("拖动后右侧文案刷新为「4.5 星」", "4.5 星" in star_texts, f"{star_texts}")
    on_change(types.SimpleNamespace(value=0.0))
    star_texts = [n.text for n in _all(roots, "label") if n.text in ("不限",)]
    check("拖回 0 ⇒ 文案变「不限」（0 就是不限制星级）",
          "不限" in star_texts, f"{star_texts}")

    on_change(types.SimpleNamespace(value=2.5))
    on_save = _handler(_save_button(roots))
    check("「保存更改」按钮挂了 on_click", on_save is not None)
    if on_save is not None:
        on_save()
        saved = _SAVED[-1] if _SAVED else {}
        check("保存载荷带 push_buy_min_star（PUT 全量替换，漏了就被清成 0）",
              saved.get("push_buy_min_star") == 2.5, f"{saved}")
        check("保存载荷带 push_sell_min_star",
              "push_sell_min_star" in saved, f"{saved}")

print("\n--- 第 4 节 脏数据夹紧（_star_val） ---")
sv = page._star_val
check("_star_val 字符串转成浮点", sv("3.5") == 3.5, f"{sv('3.5')}")
check("_star_val 无法解析 ⇒ 0（不限）", sv("abc") == 0.0 and sv(None) == 0.0)
check("_star_val 负值夹到 0", sv(-2.0) == 0.0, f"{sv(-2.0)}")
check("_star_val 超过 5 夹到 5.0（否则服务端 400）", sv(9.0) == 5.0, f"{sv(9.0)}")
check("_star_val 保留一位小数", sv(3.33) == 3.3, f"{sv(3.33)}")

print("\n--- 第 5 节 主题即时保存时不能把星级冲掉 ---")
# 服务端旧记录里没有星级字段（PUT 全量替换 ⇒ 缺字段会按默认 0 落库）
OLD_PREFS = {
    "id": "pref-1",
    "user_id": "u-1",
    "theme_mode": "light",
    "enable_wx_push": True,
    "wx_push_url": "",
    "enable_phone_text": False,
    "phone": "",
    "update_time": "2026-09-30 10:00:00",
}
page, roots = _render(OLD_PREFS)
sliders = _labeled_sliders(roots)
check("服务端缺星级字段时滑块默认 0", sliders["buy"].value == 0.0,
      f"{sliders['buy'].value}")
on_change = sliders["buy"].kw.get("on_change")
if on_change is not None:
    on_change(types.SimpleNamespace(value=4.0))
theme_switch = _all(roots, "switch")[0]
on_theme = _handler(theme_switch, "on_change")
check("深色模式开关挂了 on_change", on_theme is not None)
if on_theme is not None:
    on_theme(types.SimpleNamespace(value=True))
    saved = _SAVED[-1] if _SAVED else {}
    check("切主题时补上页面当前星级（服务端记录里没有也不清零）",
          saved.get("push_buy_min_star") == 4.0, f"{saved}")

print("\n--- 第 6 节 保存走的是 /api/user/preference 全量字段 ---")
page, roots = _render(PREFS)
on_save = _handler(_save_button(roots))
if on_save is not None:
    on_save()
    saved = _SAVED[-1] if _SAVED else {}
    check("保存载荷 = 全量 7 个字段（不多不少）",
          set(saved) == {"theme_mode", "enable_wx_push", "wx_push_url",
                         "enable_phone_text", "phone",
                         "push_buy_min_star", "push_sell_min_star"},
          f"{sorted(saved)}")

print()
if _FAILED:
    print(f"RESULT: {len(_FAILED)} 项失败 / {len(_PASSED)} 项通过")
    for name in _FAILED:
        print(f"  - {name}")
    sys.exit(1)
print(f"RESULT: 全部通过（{len(_PASSED)} 项）")

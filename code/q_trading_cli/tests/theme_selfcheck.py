"""设计令牌自检 —— 校验 colors/theme.py 与全项目的取色约定是否自洽。

用法（无需 pytest、无需安装 nicegui）::

    python tests/theme_selfcheck.py

退出码 0 = 全部通过；1 = 有失败项；2 = 有可疑项。

它覆盖的**静态检查抓不到**的问题
--------------------------------
1. **属性级失效**：``theme_manager.get_current_theme()`` 这种调用在接口被删除后
   仍留在代码里，``py_compile`` / ``pyflakes`` / ``ruff`` 都发现不了，
   要等页面渲染时才抛 ``AttributeError`` 白屏。本脚本把全项目对
   ``theme_manager`` / ``theme`` 的属性访问逐个对照 ``ThemeManager`` 的真实成员。
2. **颜色通道漏配**：CSS 无法给 ``var()`` 追加透明度，半透明色必须走
   ``rgba(var(--qs-<名>-rgb), a)``。若某令牌没派生通道，声明会被浏览器静默丢弃
   （表现为"选中态底色没了"）。本脚本扫描实际用到的通道名并核对是否已生成。
3. **令牌表结构**：每项必须是 (深色, 浅色) 两元组，别名必须指向存在的令牌，
   且**浅色必须挂在 ``:root``、深色挂 ``body.qs-dark``**（两套挂反会整体偏色）。
4. **深色外观零回归**：通道拼出的 rgba 必须与改造前的写死字面量逐字符一致。
5. **写死色值残留**：应用代码里不应再出现十六进制 / ``rgba(`` 字面量。
6. **登录页背景层结构**：背景是 ``position:fixed`` 浮层，一旦被不透明祖先容器
   盖住或 z-index 高于卡片，**页面照常渲染、日志无异常**，只是背景看不见——
   只能靠这几条结构断言守住。

实现说明：本机没有安装 nicegui（只有 PyInstaller 产物里的 static/templates），
因此这里用 types.ModuleType 造一个最小桩，直接在内存里 exec theme.py 的源码。
"""
from __future__ import annotations

import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "colors" / "theme.py"
SKIP_PARTS = ("_internal", "dist", "build", ".venv", "node_modules", "doc")

_fail: list[str] = []
_warn: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    """记录一条断言结果。"""
    if cond:
        print(f"[OK]   {label}{(' -> ' + detail) if detail else ''}")
    else:
        _fail.append(label)
        print(f"[FAIL] {label}{(' -> ' + detail) if detail else ''}")
    return cond


# --------------------------------------------------------------------------
# 加载 theme.py（桩掉 nicegui）
# --------------------------------------------------------------------------
def load_theme() -> dict:
    """在最小 nicegui 桩下执行 theme.py，返回其结果命名空间。"""
    ng = types.ModuleType("nicegui")
    ng.ui = types.SimpleNamespace(
        add_css=lambda *a, **k: None,
        add_body_html=lambda *a: None,
        colors=lambda **k: None,
        run_javascript=lambda *a: None,
    )
    ng.app = types.SimpleNamespace(storage=types.SimpleNamespace(user={}))
    ng.context = types.SimpleNamespace(client=types.SimpleNamespace(has_socket_connection=False))
    saved = {k: sys.modules.get(k) for k in ("nicegui", "nicegui.ui", "nicegui.app", "nicegui.context")}
    sys.modules["nicegui"] = ng
    for sub in ("nicegui.ui", "nicegui.app", "nicegui.context"):
        sys.modules[sub] = types.ModuleType(sub)
    try:
        ns: dict = {"__name__": "colors.theme", "__file__": str(SRC)}
        exec(compile(SRC.read_text(encoding="utf-8"), str(SRC), "exec"), ns)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return ns


def project_files() -> list[pathlib.Path]:
    """返回需要扫描的项目 Python 文件。

    排除：打包产物（``_internal`` / ``dist`` 等）、文档工具（``doc/``，其预览页
    自身需要字面色值来画框架）、以及**本脚本自己**（脚本里的期望值表必然
    包含字面色值，扫自己会永远误报）。
    """
    self_path = pathlib.Path(__file__).resolve()
    return sorted(
        p for p in ROOT.rglob("*.py")
        if not any(s in p.parts for s in SKIP_PARTS)
        and p != SRC
        and p.resolve() != self_path
    )


def rel(p: pathlib.Path) -> str:
    return p.as_posix().replace(ROOT.as_posix() + "/", "")


# --------------------------------------------------------------------------
def main() -> int:
    if not SRC.exists():
        print(f"[FAIL] 找不到 {SRC}")
        return 1
    ns = load_theme()
    TOKENS: dict[str, tuple[str, str]] = ns["TOKENS"]
    ALIASES: dict[str, str] = ns["_RGB_ALIASES"]
    tm = ns["theme_manager"]
    css: str = ns["_THEME_CSS"]
    channels = list(TOKENS) + [a for a in ALIASES if a not in TOKENS]

    print("=" * 72)
    print("1) 令牌表结构")
    print("=" * 72)
    check("TOKENS 非空", bool(TOKENS), f"{len(TOKENS)} 项")
    bad = [n for n, v in TOKENS.items()
           if not (isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, str) and x for x in v))]
    check("每项均为 (深色, 浅色) 非空两元组", not bad, str(bad))
    check("别名不覆盖同名令牌", all(a not in TOKENS for a in ALIASES), str(ALIASES))
    check("别名指向已存在的令牌", all(ALIASES[a] in TOKENS for a in ALIASES))
    # 结构性断言：浅色是系统默认（:root），深色挂在 body.qs-dark 覆盖。
    # 用 _tokens_css 的确定性输出做子串比对，防止两套取值挂反。
    check("默认(:root)挂浅色取值", ns["_tokens_css"](":root", 1) in css, "系统默认主题为浅色")
    check("深色挂 body.qs-dark", ns["_tokens_css"](f"body.{ns['DARK_CLASS']}", 0) in css)

    print()
    print("=" * 72)
    print("2) 颜色通道：每个令牌都应派生 --qs-<名>-rgb（深/浅各一份）")
    print("=" * 72)
    missing = []
    for name in channels:
        vals = re.findall(rf"--qs-{re.escape(name)}-rgb:\s*([0-9, ]+);", css)
        if len(vals) != 2 or not all(len(v.split(",")) == 3 for v in vals):
            missing.append((name, vals))
    check(f"全部 {len(channels)} 个通道均有深浅两个合法值", not missing, str(missing[:5]))

    print()
    print("=" * 72)
    print("3) 项目里实际用到的通道名，逐个核对已生成")
    print("=" * 72)
    used: dict[str, set[str]] = {}
    pat_var = re.compile(r'var_rgba\(\s*"([^"]+)"')
    pat_get = re.compile(r'get_rgba\(\s*"([^"]+)"')
    # filter_tags 的 (标签, 令牌) 对。这里**必须锚定中文标签**：
    # 早先只要求形如 ("a", "b")，于是把 _CARD_ACCENTS 的 ("purple", "layers")、
    # 状态判断的 ("paused", "stopped") 也当成通道名，报出
    # "通道 'layers' 有定义" 这类误报（令牌表里当然没有 layers）。
    pat_pair = re.compile(r'\("[\u4e00-\u9fa5]+",\s*"([a-z0-9-]+)"\)')
    for p in project_files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        name = rel(p)
        for m in pat_var.finditer(txt):
            used.setdefault(m.group(1), set()).add(f"{name}:var_rgba")
        for m in pat_get.finditer(txt):
            used.setdefault(m.group(1), set()).add(f"{name}:get_rgba")
        if "filter_tags" in txt:
            for m in pat_pair.finditer(txt):
                used.setdefault(m.group(1), set()).add(f"{name}:filter_tags")
    if not used:
        _warn.append("未扫描到任何 var_rgba/get_rgba 调用点")
        print("[WARN] 未扫描到调用点")
    for name in sorted(used):
        check(f"通道 {name!r} 有定义", name in channels, ",".join(sorted(used[name])))

    print()
    print("=" * 72)
    print("4) 深色外观零回归：通道拼出的 rgba 与改造前字面量一致")
    print("=" * 72)
    EXPECT = {
        ("primary", 0.15): "rgba(59,130,246, 0.15)",
        ("text", 0.53): "rgba(229,231,235, 0.53)",
        ("border", 0.2): "rgba(51,65,85, 0.2)",
        ("primary-strong", 0.25): "rgba(37,99,235, 0.25)",
        ("shadow", 0.6): "rgba(0,0,0, 0.6)",
        ("link", 0.25): "rgba(96,165,250, 0.25)",
    }
    tm.set_theme("dark")
    for (name, a), want in EXPECT.items():
        got = tm.get_rgba(name, a)
        check(f"get_rgba({name!r}, {a})", got == want, f"got={got!r}")

    print()
    print("=" * 72)
    print("5) 取色接口")
    print("=" * 72)
    check("var('text')", tm.var("text") == "var(--qs-text)", tm.var("text"))
    check("var_rgba('primary', 0.15)", tm.var_rgba("primary", 0.15) == "rgba(var(--qs-primary-rgb), 0.15)",
          tm.var_rgba("primary", 0.15))
    tm.set_theme("dark")
    dark_text = tm.get_color("text")
    tm.set_theme("light")
    light_text = tm.get_color("text")
    check("深浅两套取值不同（text）", dark_text != light_text, f"{dark_text} vs {light_text}")
    check("未知令牌回退兜底色", tm.get_color("__nope__") == ns["_FALLBACK_COLOR"])
    tm.set_theme("dark")
    cc = tm.get_chart_colors()
    check("chart_colors 含全部必要键",
          {"up", "down", "axis", "grid", "axis_line", "line", "border", "volume",
           "line_area_top", "line_area_bottom"} <= set(cc), str(sorted(cc)))
    check("chart_colors 为真实色值（非 var()）", all("var(" not in v for v in cc.values()))

    print()
    print("=" * 72)
    print("6) 属性级审计：项目对 theme_manager / theme 的访问是否都存在")
    print("=" * 72)
    members = {n for n in dir(type(tm)) if not n.startswith("__")}
    pat_attr = re.compile(r"\b(theme_manager|theme|_theme|tm|theme_mgr)\.([A-Za-z_][A-Za-z0-9_]*)")
    suspicious: list[str] = []
    for p in project_files():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for m in pat_attr.finditer(line):
                attr = m.group(2)
                if attr in members:
                    continue
                # ``colors/theme.py`` 这类文档/注释写法，不是属性访问
                if attr == "py":
                    continue
                suspicious.append(f"{rel(p)}:{i}  {m.group(0)}")
    check("无指向已删除/不存在接口的调用", not suspicious, "\n       ".join(suspicious))

    print()
    print("=" * 72)
    print("7) 应用代码不应残留写死色值")
    print("=" * 72)
    pat_color = re.compile(r"'#[0-9a-fA-F]{3,6}'|\"#[0-9a-fA-F]{3,6}\"|:\s*#[0-9a-fA-F]{3,6}|rgba?\([0-9]")
    leftovers: list[str] = []
    for p in project_files():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "var(--qs-" in line or "#{" in line:
                continue
            if pat_color.search(line):
                leftovers.append(f"{rel(p)}:{i}  {line.strip()[:80]}")
    check("无写死色值（应一律引用 var(--qs-*) 或走 ThemeManager）", not leftovers,
          "\n       ".join(leftovers[:10]))

    print()
    print("=" * 72)
    print("8) 登录页背景层结构不变量")
    print("=" * 72)
    # 背景是 position:fixed 的浮层，被不透明容器盖住时**页面照常渲染、不报任何错**，
    # 只是背景看不见——静态工具与运行时日志都发现不了，只能靠这几条断言守住。
    login_path = ROOT / "pages" / "login_page.py"
    login_txt = login_path.read_text(encoding="utf-8")
    login_body = login_txt.split("def login(", 1)[1].split("\ndef ", 1)[0]
    i_theme = login_body.find("apply_session_theme()")
    i_bg = login_body.find("install_login_background()")
    check("login() 内先 apply_session_theme 再装背景",
          0 <= i_theme < i_bg, f"theme@{i_theme} bg@{i_bg}")
    # 容器样式已从内联迁到 .login-viewport（内联样式无法被媒体查询覆盖，
    # 而窄屏需要回退为居中），因此这里断言规则本身，而不是内联样式字符串。
    m_view = re.search(r"\.login-viewport\s*\{\{(.*?)\}\}", login_txt, re.S)
    view_css = m_view.group(1) if m_view else ""
    check("登录容器背景透明（否则盖住 fixed 背景层）",
          "background-color: transparent;" in view_css, view_css[:60])

    # 黄金分割定位：容器水平居中卡片，故卡片中心 = (padding-left + W − padding-right)/2。
    # 取 padding-left = p% + r、padding-right = r ⇒ 中心 = (1 + p/100)·W / 2，
    # 要落在黄金分割点 0.618W 就必须 p = 23.6 且两侧偏移量相等。
    m_pad = re.search(
        r"padding:\s*\S+\s+(\S+)\s+\S+\s+calc\(([\d.]+)%\s*\+\s*(\S+)\)", view_css)
    if not m_pad:
        check("卡片中心按黄金分割点 0.618 定位", False, "未匹配到 padding 简写")
    else:
        pad_right, pct, offset = m_pad.group(1), float(m_pad.group(2)), m_pad.group(3)
        center = (1 + pct / 100) / 2
        check("卡片中心按黄金分割点 0.618 定位",
              abs(center - 0.618) < 5e-4 and pad_right == offset,
              f"中心={center:.4f}W，p={pct}%，padding-right={pad_right} 偏移={offset}")
        # 窄屏必须回退为居中：否则卡片会被压到右缘（如 480px 视口下仅剩 16px 右边距）
        check("窄屏（≤767px）回退为居中",
              re.search(r"@media\s*\(max-width:\s*767px\)\s*\{\{\s*"
                        r"\.login-viewport\s*\{\{\s*padding:\s*1rem;\s*\}\}", login_txt) is not None)
    check("Quasar 容器不再带不透明底色",
          re.search(r"#nicegui, \.q-layout, \.q-page-container, \.q-page \{\{?\s*"
                    r"background-color: transparent", login_txt) is not None)
    qbg_path = ROOT / "components" / "login_background.py"
    if not qbg_path.exists():
        check("背景模块存在", False, str(qbg_path))
    else:
        qbg_txt = qbg_path.read_text(encoding="utf-8")
        # 只取样式常量段：模块正文里也出现过 _SCRIPT / z-index 字样，
        # 按第一个出现的标记切会把样式段切掉（自己踩过一次）
        qbg_style = qbg_txt.split('_CSS = """', 1)[1].split('"""', 1)[0]
        bg_z = sorted({int(z) for z in re.findall(r"z-index:\s*(\d+)\s*;", qbg_style)})
        m_card = re.search(r"z-index:\s*(\d+)\s*;", login_txt)
        card_z = int(m_card.group(1)) if m_card else 0
        check("背景层 z-index 均低于登录卡片容器",
              bool(bg_z) and card_z > 0 and max(bg_z) < card_z,
              f"背景 {bg_z} < 卡片 {card_z}")
        # 断言"取色处确实引用了令牌"，而不是"文件里出现过令牌字面量"——
        # 后者只要别处提一句就会通过（自己踩过：断言过松，反向验证测不出来）
        check("网格/辉光确实由 --qs-bg-grid / --qs-bg-glow 取色",
              "linear-gradient(var(--qs-bg-grid) 1px" in qbg_style
              and "radial-gradient(600px 320px at 18% 8%, var(--qs-bg-glow)" in qbg_style)
        for token in ("bg-grid", "bg-glow"):
            check(f"令牌 {token!r} 已定义", token in TOKENS, str(TOKENS.get(token)))

    print()
    print("=" * 72)
    if _fail:
        print(f"RESULT: {len(_fail)} 项失败")
        for f in _fail:
            print("  -", f)
    else:
        print("RESULT: 全部通过")
    for w in _warn:
        print("  [WARN]", w)
    print("=" * 72)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())

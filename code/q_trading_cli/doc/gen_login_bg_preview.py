#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-16
Description: 生成登录页背景预览页，深浅各一份，便于在浏览器里直接看效果。

    python doc/gen_login_bg_preview.py
    → doc/login-bg-preview-light.html
      doc/login-bg-preview-dark.html

为什么需要它：本机（以及 CI）没有浏览器、也没有后端依赖，无法把应用跑起来看登录页。
这个生成器在 nicegui 桩下执行 colors/theme.py 与 components/login_background.py，
取它们**真实产出**的 CSS / DOM / 脚本拼成独立页面——不是另抄一份，
所以改完 theme.py 或 login_background.py 后重跑本脚本，预览页会自动跟上。

两点说明：
  - 卡片只画「毛玻璃面板」示意（同样的表面色 + 14px 模糊 + 圆角/投影），
    表单内容是 NiceGUI 渲染的，预览页里没有。判断背景与卡片的层次关系够用了。
  - 运行时行为（状态机是否推进、保持时长、吸附几何）肉眼看不出来，
    那部分用技能验证：~/.workbuddy/skills/browserless-canvas-page-check/
"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "doc"

# 预览页的容器重置：与 pages/login_page.py 里那段一致——背景色只给 html/body，
# 其余容器透明，否则 fixed 的背景层会被不透明祖先盖住（见 MEMORY「登录页背景」）
_RESET_CSS = """
html, body {
    height: 100%;
    min-height: 100vh;
    margin: 0;
    padding: 0;
    background-color: var(--qs-page);
}
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    color: var(--qs-text);
    overflow: hidden;
}
/* 卡片位置示意：仅复刻「毛玻璃面板」这一层；表单由 NiceGUI 渲染。
   位置须与 pages/login_page.py 的 .login-viewport 一致：
   卡片中心落在窗口横向黄金分割点 0.618（窄屏回退为居中）。 */
.qtbg-card-ghost {
    position: fixed;
    z-index: 10;
    top: 50%;
    left: 61.8%;
    transform: translate(-50%, -50%);
    width: min(92vw, 440px);
    height: min(72vh, 520px);
    border-radius: 1.25rem;
    background-color: rgba(var(--qs-surface-rgb), 0.82);
    border: 1px solid var(--qs-border);
    box-shadow: 0 20px 40px var(--qs-shadow);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 2rem;
    font-size: 0.8rem;
    line-height: 1.9;
    color: var(--qs-muted);
}
@media (max-width: 767px) {
    .qtbg-card-ghost { left: 50%; }
}
"""

_GHOST_HTML = """
<div class="qtbg-card-ghost">
    登录卡片位置（毛玻璃示意）<br />
    表单内容由 NiceGUI 渲染，此处省略<br />
    —— 判断背景与卡片的层次是否合适即可
</div>
"""


def load_modules() -> tuple[types.ModuleType, dict[str, list[str]]]:
    """在最小 nicegui 桩下加载 theme.py 与 login_background.py。

    :return: (login_background 模块, 捕获到的 css/body 片段容器)
    """
    captured: dict[str, list[str]] = {"css": [], "body": []}
    stub = types.ModuleType("nicegui")
    stub.ui = types.SimpleNamespace(
        add_css=lambda c, *a, **k: captured["css"].append(c),
        add_body_html=lambda h: captured["body"].append(h),
        colors=lambda **k: None,
        run_javascript=lambda *a: None,
    )
    stub.app = types.SimpleNamespace(storage=types.SimpleNamespace(user={}))
    stub.context = types.SimpleNamespace(
        client=types.SimpleNamespace(has_socket_connection=False)
    )
    saved = sys.modules.get("nicegui")
    sys.modules["nicegui"] = stub
    try:
        for pkg_name, pkg_dir in (("colors", "colors"), ("components", "components")):
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [str(ROOT / pkg_dir)]
            sys.modules[pkg_name] = pkg

        loaded: list[types.ModuleType] = []
        for name, rel in (
            ("colors.theme", "colors/theme.py"),
            ("components.login_background", "components/login_background.py"),
        ):
            path = ROOT / rel
            mod = types.ModuleType(name)
            mod.__file__ = str(path)
            sys.modules[name] = mod
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
            loaded.append(mod)
        return loaded[1], captured
    finally:
        if saved is None:
            sys.modules.pop("nicegui", None)
        else:
            sys.modules["nicegui"] = saved


def build(theme: types.ModuleType, bg: types.ModuleType, dark: bool,
          captured: dict[str, list[str]]) -> str:
    """拼出一份预览页 HTML。

    :param theme: colors.theme 模块
    :param bg: components.login_background 模块
    :param dark: True 生成深色版
    :param captured: load_modules() 返回的捕获容器（会被清空后重新填充）
    :return: 完整 HTML
    """
    # 先切主题：这一步会顺带安装令牌样式表（幂等）。必须在清空之前调用——
    # 它自己的产出不进预览页（令牌样式表已显式写在 <head> 里）。
    theme.theme_manager.set_theme("dark" if dark else "light")
    captured["css"].clear()
    captured["body"].clear()
    bg.install_login_background()

    label = "深色" if dark else "浅色"
    body_attr = ' class="qs-dark"' if dark else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>QTrade 登录页背景预览（{label}）</title>
<style>
/* ===== 由 colors/theme.py 生成的真实令牌样式 ===== */
{theme._THEME_CSS}
</style>
<style>
/* ===== 预览页容器重置 ===== */
{_RESET_CSS}
</style>
<style>
/* ===== 由 components/login_background.py 生成的真实样式 ===== */
{chr(10).join(captured["css"])}
</style>
</head>
<body{body_attr}>
{_GHOST_HTML}
{chr(10).join(captured["body"])}
</body>
</html>
"""


def main() -> int:
    bg, captured = load_modules()
    theme = sys.modules["colors.theme"]
    for dark in (False, True):
        html = build(theme, bg, dark, captured)
        name = "login-bg-preview-dark.html" if dark else "login-bg-preview-light.html"
        (OUT_DIR / name).write_text(html, encoding="utf-8")
        print(f"[OK] 已生成 {OUT_DIR / name}（{'深色' if dark else '浅色'}，{len(html)} 字符）")
    print(f"[OK] 令牌 {len(theme.TOKENS)} 项；背景装饰令牌 "
          f"bg-grid={theme.TOKENS['bg-grid']} bg-glow={theme.TOKENS['bg-glow']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

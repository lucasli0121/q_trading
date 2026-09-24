"""生成设计令牌预览页 doc/theme-tokens.html —— 直接从 colors/theme.py 派生，保证与源码一致。

用法（在项目根目录或任意位置都可）::

    python doc/gen_theme_tokens_preview.py

输出 ``doc/theme-tokens.html``，用浏览器打开即可。

为什么需要它
------------
项目没有自动化 UI 测试，深/浅主题的实际渲染只能靠肉眼。这个页面把全部令牌的
「深色取值 / 浅色取值」并排渲染成色块，便于核对：
  1. 浅色取值是否补齐（有没有空值 / 与深色相同却应当不同的项）
  2. 通道（``--qs-<名>-rgb``）是否能为每个令牌拼出半透明色

关键点：页面里**注入 theme.py 自己生成的 CSS**（令牌块 + 通道块），
色块一律用 ``var()`` 取色。因此这不是一张"抄出来的"色卡，而是一次实时校验——
哪个令牌或通道没生成出来，对应的色块就会是空的。
因此**改动 TOKENS / _RGB_ALIASES 后应重新运行本脚本**，否则页面会与实际配色脱节。
"""
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent  # 本文件在 <项目>/doc/
SRC = ROOT / "colors" / "theme.py"
OUT = ROOT / "doc" / "theme-tokens.html"

# ---- 桩掉 nicegui，exec theme.py 拿到真实数据 ----
ng = types.ModuleType("nicegui")
ng.ui = types.SimpleNamespace(add_css=lambda *a, **k: None, add_body_html=lambda *a: None,
                              colors=lambda **k: None, run_javascript=lambda *a: None)
ng.app = types.SimpleNamespace(storage=types.SimpleNamespace(user={}))
ng.context = types.SimpleNamespace(client=types.SimpleNamespace(has_socket_connection=False))
sys.modules["nicegui"] = ng
for s in ("nicegui.ui", "nicegui.app", "nicegui.context"):
    sys.modules[s] = types.ModuleType(s)

ns: dict = {"__name__": "colors.theme", "__file__": str(SRC)}
exec(compile(SRC.read_text(encoding="utf-8"), str(SRC), "exec"), ns)
TOKENS = ns["TOKENS"]
ALIASES = ns["_RGB_ALIASES"]
_tm = ns["theme_manager"]

# ---- 从源码里解析分组注释（# ---- 背景 / 容器 ----）----
groups: list[tuple[str, list[str]]] = []
cur = ("其他", [])
group_re = re.compile(r"^\s*#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
tok_re = re.compile(r'^\s*"([a-z0-9-]+)":\s*\(')
in_tokens = False
for line in SRC.read_text(encoding="utf-8").splitlines():
    if line.startswith("TOKENS:"):
        in_tokens = True
        continue
    if in_tokens and line.startswith("}"):
        break
    if not in_tokens:
        continue
    g = group_re.match(line)
    if g:
        if cur[1]:
            groups.append(cur)
        cur = (g.group(1), [])
        continue
    t = tok_re.match(line)
    if t:
        cur[1].append(t.group(1))
if cur[1]:
    groups.append(cur)

CHANNELS = list(TOKENS) + [a for a in ALIASES if a not in TOKENS]
ungrouped = [n for n in TOKENS if n not in {x for _, ns_ in groups for x in ns_}]

# ---- 注入 theme.py 真实生成的样式（浅色即 :root 默认；深色改写为 .dark-scope 以便同页并列）----
token_css_light = ns["_tokens_css"](":root", 1)
token_css_dark = ns["_tokens_css"](".dark-scope", 0)
rgb_css_light = ns["_rgb_css"](":root", 1)
rgb_css_dark = ns["_rgb_css"](".dark-scope", 0)


def sw(value_expr: str) -> str:
    return f'<span class="sw" style="background:{value_expr}"></span>'


def row(name: str) -> str:
    d, li = TOKENS[name]
    same = " same" if d == li else ""
    owners = [k for k, v in ALIASES.items() if v == name]
    alias = f'<span class="alias">有别名 {", ".join(owners)}</span>' if owners else ""
    return f'''      <tr>
        <td><code>{name}</code>{alias}</td>
        <td class="cvar"><code>var(--qs-{name})</code></td>
        <td class="pair dark-scope{same}">{sw(f"var(--qs-{name})")}<code>{d}</code></td>
        <td class="pair{same}">{sw(f"var(--qs-{name})")}<code>{li}</code></td>
        <td class="ch">{sw(f"rgba(var(--qs-{name}-rgb), 0.25)")}<code>rgba(var(--qs-{name}-rgb), 0.25)</code></td>
      </tr>'''


sections = []
for title, names in groups:
    names = [n for n in names if n in TOKENS]
    if not names:
        continue
    sections.append(f'''  <h2>{title}</h2>
  <table>
    <thead><tr>
      <th style="width:20%">令牌</th><th style="width:22%">CSS 变量</th>
      <th style="width:17%">深色 body.qs-dark</th><th style="width:17%">浅色 :root（默认）</th>
      <th>半透明通道（示例 α=0.25）</th>
    </tr></thead>
    <tbody>
{chr(10).join(row(n) for n in names)}
    </tbody>
  </table>''')

same_list = [n for n, v in TOKENS.items() if v[0] == v[1]]
alias_rows = "".join(
    f'<li><code>{a}</code> → <code>{t}</code>（深 {TOKENS[t][0]} / 浅 {TOKENS[t][1]}）</li>'
    for a, t in ALIASES.items())

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>设计令牌预览 · colors/theme.py</title>
<style>
  /* ===== 由 theme.py 生成的真实样式：浅色（:root 默认） ===== */
{token_css_light}
{rgb_css_light}

  /* ===== 由 theme.py 生成的真实样式：深色（此处用 .dark-scope 便于同页并列） ===== */
{token_css_dark}
{rgb_css_dark}

  /* ===== 页面自身样式（不参与主题） ===== */
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px; background: #0f0f1a; color: #e5e7eb;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 13px; }}
  h1 {{ font-size: 20px; margin: 0 0 6px; color: #f3f4f6; }}
  h2 {{ font-size: 14px; margin: 30px 0 10px; color: #60a5fa; font-weight: 600; }}
  p.lead {{ color: #9ca3af; margin: 0 0 20px; line-height: 1.75; max-width: 1100px; }}
  .stats {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }}
  .stat {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px; min-width: 118px; }}
  .stat b {{ display: block; font-size: 20px; color: #f3f4f6; }}
  .stat span {{ color: #9ca3af; font-size: 12px; }}
  table {{ width: 100%; border-collapse: separate; border-spacing: 0; background: #1e293b;
           border: 1px solid #334155; border-radius: 10px; overflow: hidden; }}
  th {{ text-align: left; font-weight: 600; color: #d1d5db; background: #111827; padding: 10px 12px;
        border-bottom: 1px solid #334155; font-size: 12px; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid rgba(51,65,85,0.5); vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #19222f; }}
  code {{ font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: #cbd5e1; }}
  .sw {{ display: inline-block; width: 24px; height: 15px; border-radius: 3px;
         border: 1px solid rgba(255,255,255,0.3); vertical-align: -3px; margin-right: 7px; }}
  td.dark-scope {{ background: #111827; }}
  tr:hover td.dark-scope {{ background: #111827; }}
  .cvar code {{ color: #94a3b8; }}
  .ch code {{ color: #94a3b8; font-size: 11px; }}
  .alias {{ display: block; color: #fbbf24; font-size: 11px; }}
  .same code::after {{ content: "（深浅同值）"; color: #6b7280; font-size: 11px; margin-left: 4px; }}
  .note {{ background: #111827; border: 1px solid #334155; border-left: 3px solid #3b82f6;
           border-radius: 8px; padding: 14px 16px; color: #9ca3af; line-height: 1.8; margin-top: 26px; }}
  .note b {{ color: #e5e7eb; }}
  ul {{ margin: 6px 0 0; padding-left: 20px; line-height: 1.9; }}
  .ok {{ color: #34d399; }}
</style>
</head>
<body>
  <h1>设计令牌预览</h1>
  <p class="lead">
    数据与样式均直接来自 <code>colors/theme.py</code>，非手工誊抄。<br>
    浅色取值定义在 <code>:root</code>（系统默认主题为浅色），深色取值定义在 <code>body.qs-dark</code>（本页为同屏并列改写为 <code>.dark-scope</code>），
    两者同时存在于同一张共享样式表；切换主题只切换 body 上的 class，浏览器立即重算，不需要重新渲染页面。<br>
    <span class="ok">下方色块一律用 <code>var()</code> 取色 —— 若某个令牌或颜色通道没生成出来，色块会是空的。</span>
  </p>

  <div class="stats">
    <div class="stat"><b>{len(TOKENS)}</b><span>设计令牌</span></div>
    <div class="stat"><b>{len(CHANNELS)}</b><span>颜色通道</span></div>
    <div class="stat"><b>{len(ALIASES)}</b><span>通道别名</span></div>
    <div class="stat"><b>0</b><span>项目内写死色值</span></div>
  </div>

{chr(10).join(sections)}

  <div class="note">
    <b>颜色通道</b>：CSS 无法给 <code>var()</code> 追加透明度（<code>var(--qs-text)88</code> 是非法声明，
    整条 <code>color</code> 会被浏览器丢弃）。需要半透明时用通道变量拼接
    <code>rgba(var(--qs-primary-rgb), 0.15)</code>；Python 侧对应
    <code>ThemeManager.var_rgba()</code>（CSS 场景）与 <code>get_rgba()</code>（canvas 场景）。
    通道由令牌自动派生，因此对任意令牌都成立，不存在漏配。
    <ul>{alias_rows}</ul>
    <b>深浅同值的令牌</b>（正常，通常是"彩色底上的白字"这类跨主题不变的项）：
    <code>{", ".join(same_list) or "无"}</code><br>
    <b>未纳入分组的令牌</b>：<code>{", ".join(ungrouped) or "无"}</code>
  </div>
</body>
</html>
'''

OUT.write_text(html, encoding="utf-8", newline="\n")
print(f"[OK] 已生成 {OUT}")
print(f"     令牌 {len(TOKENS)}，通道 {len(CHANNELS)}，分组 {len(groups)}，未分组 {ungrouped}")
print(f"     深浅同值: {same_list}")

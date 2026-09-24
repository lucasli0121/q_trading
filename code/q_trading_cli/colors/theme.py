"""
Author: liguoqiang
Date: 2026-05-25 17:58:34
LastEditors: liguoqiang
LastEditTime: 2026-09-16
Description: 主题管理 —— 深色 / 浅色两套配色，支持会话级持久化与运行时即时切换。

设计要点
--------
1. **设计令牌（CSS 变量）驱动**
   界面里所有颜色都写成 `var(--qs-xxx)`（见 ``TOKENS``），而不是写死十六进制。
   浅色取值定义在 ``:root``（**系统默认主题是浅色**），深色取值定义在
   ``body.qs-dark`` 下，两者同时存在于同一张共享样式表里。

2. **切换主题 = 切换 body 上的 class**
   ``set_theme()`` 只做两件事：更新 Quasar 主题色 + 切换 ``body.qs-dark``。
   由于所有颜色都是 ``var()`` 引用，浏览器会立刻重算，**不需要重新渲染页面**。
   也正因为只有一处样式表，反复切换不会累积重复的 <style> 节点。
   默认（浅色）下 body 上没有任何主题 class——即使首屏脚本未执行，浏览器
   画出来的第一帧也是浅色，不会闪深色。

3. **取值结构稳定**
   ``TOKENS`` 每项仍是 ``(深色值, 浅色值)`` 两元组，深色取值与历史实现逐字节一致；
   变化的只是「哪一套挂在 ``:root``」——默认从深色换成了浅色。

4. **不启用 Quasar 的 ``body--dark``**
   本项目的深色是由自身令牌实现的（历史如此），并未启用 Quasar Dark 插件。
   若后续希望原生控件（滚动条、自动填充等）跟随主题，可另加一个
   ``ui.dark_mode()`` 元素；那会改变现有深色外观，需先评估。

5. **图表（ECharts）例外**
   ECharts 用 canvas 绘制，不解析 CSS 变量，因此图表配色必须取**真实色值**：
   用 ``get_color()`` / ``get_rgba()`` / ``get_chart_colors()``。

6. **半透明色走「颜色通道」**
   CSS 无法给 ``var()`` 追加透明度——``var(--qs-text)88`` 是非法声明，整条
   ``color`` 会被浏览器丢弃。需要半透明时用 ``--qs-<名>-rgb`` 通道变量拼接：
   ``rgba(var(--qs-primary-rgb), 0.15)``。Python 侧对应 :meth:`ThemeManager.var_rgba`
   （CSS 场景）与 :meth:`ThemeManager.get_rgba`（canvas 场景）。

   通道**由 ``TOKENS`` 自动派生**（每个令牌一个 ``-rgb``），不手工登记——
   手工表曾漏掉 ``link``，导致选中标签的半透明底色静默失效。
   通道名与令牌名不同的少数情况登记在 ``_RGB_ALIASES``。

唯一取色入口
------------
界面代码里**不应再出现写死的色值**，一律从这里取：

===============================  ==================================================
``var(name)``                    CSS 变量引用（``var(--qs-text)``）
``var_rgba(name, a)``            带透明度的 CSS 引用（通道拼接）
``get_color(name)``              真实色值（Quasar 属性 / canvas 图表）
``get_rgba(name, a)``            真实 rgba（canvas 图表）
``get_chart_colors()``           ECharts 常用色值集合
===============================  ==================================================
"""
from __future__ import annotations

import logging
import re
from typing import Any

from nicegui import app, context, ui

logger = logging.getLogger(__name__)

# ============================================================================
# 设计令牌：变量名 -> (深色取值, 浅色取值)
# 新增颜色时只在这里加一行，界面里统一用 var(--qs-<name>) 引用。
# ============================================================================
TOKENS: dict[str, tuple[str, str]] = {
    # ---- 背景 / 容器 ----
    "page": ("#0f0f1a", "#f3f4f6"),           # 页面背景
    "surface": ("#1e293b", "#ffffff"),        # 卡片 / 顶栏 / 对话框 / 标签页
    "surface-alt": ("#111827", "#ffffff"),    # 侧边栏 / 表头 / 输入框
    "row-alt": ("#19222f", "#f9fafb"),        # 表格斑马行
    "row-alt-2": ("#151c29", "#f9fafb"),      # 表格斑马行（第二套）
    # ---- 文字 ----
    "text": ("#e5e7eb", "#374151"),           # 正文
    "heading": ("#f3f4f6", "#111827"),        # 标题 / 强调文字
    "label": ("#d1d5db", "#4b5563"),          # 表单标签
    "muted": ("#9ca3af", "#6b7280"),          # 次要文字 / 图表坐标轴
    "faint": ("#6b7280", "#9ca3af"),          # 更弱提示文字
    "icon-muted": ("#4b5563", "#d1d5db"),     # 空状态图标
    "disabled": ("#94a3b8", "#9ca3af"),       # 禁用态文字
    "disabled-border": ("#64748b", "#d1d5db"),  # 禁用态边框
    # ---- 边框 / 分割线 ----
    "border": ("#334155", "#e5e7eb"),         # 主边框、分割线
    "border-soft": ("#2d3748", "#eef2f6"),    # 弱边框、图表网格线
    # ---- 品牌 / 语义色 ----
    "primary": ("#3b82f6", "#2563eb"),        # 主色
    "secondary": ("#34d399", "#16a34a"),      # 辅助色
    "primary-hover": ("#2563eb", "#1d4ed8"),  # 主色 hover
    "primary-active": ("#1d4ed8", "#1e40af"),  # 主色按下
    "link": ("#60a5fa", "#2563eb"),           # 链接 / 激活态文字
    "info": ("#93c5fd", "#1d4ed8"),           # 提示文字
    "info-border": ("#1e40af", "#bfdbfe"),    # 提示框边框
    "danger-border": ("#991b1b", "#fecaca"),  # 危险区域边框
    "accent": ("#fbbf24", "#d97706"),         # 强调色（琥珀）
    "positive": ("#f87171", "#dc2626"),       # 涨 / 买入（红）
    "negative": ("#34d399", "#059669"),       # 跌 / 卖出（绿）
    "danger": ("#ef4444", "#dc2626"),         # 危险 / 删除
    "purple": ("#c084fc", "#7e22ce"),         # 统计卡片图标（紫）
    "orange": ("#fb923c", "#c2410c"),         # 统计卡片图标（橙）
    "on-accent": ("#ffffff", "#ffffff"),      # 彩色底上的文字
    "avatar-bg": ("#1e3a5f", "#dbeafe"),      # 头像底色
    "chart-volume": ("#4b5563", "#9ca3af"),   # K 线成交量柱
    "chip-neutral": ("rgba(55,65,81,0.8)", "#e5e7eb"),  # 中性状态标签底色
    # ---- 半透明 / 派生色（深浅取值不同，供界面直接引用） ----
    "text-secondary": ("rgba(229,231,235,0.53)", "rgba(55,65,81,0.66)"),  # 次要文字（半透明）
    "divider": ("rgba(51,65,85,0.2)", "#eef2f6"),          # 极弱分割线（表格行）
    "divider-strong": ("rgba(45,55,72,0.6)", "#e5e7eb"),   # 区块分割线 / 表头底线
    "shadow": ("rgba(0,0,0,0.6)", "rgba(15,23,42,0.12)"),  # 卡片 / 对话框投影
    "primary-glow": ("rgba(37,99,235,0.2)", "rgba(37,99,235,0.28)"),  # 主色外发光
    "table-header-bg": ("#65b6ff", "#3b82f6"),             # 表格表头底色
    "surface-soft": ("#fcfeff", "#f8fafc"),                # 近白卡片底
    # ---- 登录页背景装饰层（见 components/login_background.py） ----
    # 这两项是纯装饰色（网格线、辉光），深浅两套单独取值——
    # 靠颜色通道拼透明度做不到这一点：同一个 alpha 叠在浅底/深底上的
    # 观感差异很大，必须各给一份。
    "bg-grid": ("rgba(148,163,184,0.06)", "rgba(15,23,42,0.06)"),  # 蓝图网格线 / 品牌水印
    "bg-glow": ("rgba(59,130,246,0.14)", "rgba(37,99,235,0.10)"),  # 背景品牌辉光
}

# 颜色通道**自动派生**：``TOKENS`` 里每个令牌都会生成一个
# ``--qs-<令牌名>-rgb: r,g,b``，因此在界面里对**任意**令牌都能拼透明度：
# ``rgba(var(--qs-positive-rgb), 0.25)``。
# 这里只登记"通道名与令牌名不同"的别名，避免同一颜色写两遍。
_RGB_ALIASES: dict[str, str] = {
    "primary-strong": "primary-hover",  # 主色加深一档（hover / 选中底）
}

# 未知令牌 / 无法解析色值时的兜底中性灰（防御性占位，正常路径不会命中）
_FALLBACK_COLOR = "#888888"

_PREFIX = "--qs-"
DARK_CLASS = "qs-dark"
DEFAULT_MODE = "light"  # 系统默认主题：浅色

_styles_installed = False


def _var(token: str) -> str:
    """返回令牌的 CSS 变量引用。"""
    return f"var({_PREFIX}{token})"


def resolve_mode_from_prefs(prefs: Any) -> str | None:
    """从后端偏好响应里解析「用户显式选择过」的主题模式。

    重要：``/api/user/preference`` 在用户**从未保存过偏好**时返回的是模型默认值
    ``theme_mode="light"``（见 q_trading_server/web_api/models.py），那并不是用户的选择。
    此时响应里的 ``id`` / ``update_time`` 都为空字符串，用它来区分"记录不存在"。

    :param prefs: 偏好 API 返回的字典
    :return: "dark" / "light"；记录不存在或取值非法时返回 None（由调用方用默认主题）
    """
    if not isinstance(prefs, dict):
        return None
    if not (prefs.get("id") or prefs.get("update_time")):
        return None
    mode = prefs.get("theme_mode")
    if isinstance(mode, str) and mode.strip().lower() in ("dark", "light"):
        return mode.strip().lower()
    return None


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_FN_RE = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _to_rgb(value: str) -> str:
    """把颜色字面量解析成 ``"r,g,b"`` 通道串。

    支持 ``#rgb`` / ``#rrggbb`` / ``rgb(r,g,b)`` / ``rgba(r,g,b,a)``。
    只取 RGB 三个分量，透明度由调用方在拼接时给出（``TOKENS`` 里有若干
    令牌本身就是 ``rgba(...)``，必须能一并解析）。

    :param value: 颜色字面量
    :return: 形如 ``"59,130,246"``；无法解析时回退中性灰并告警
    """
    text = value.strip()
    match = _HEX_RE.match(text)
    if match:
        digits = match.group(0)[1:]
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return f"{int(digits[0:2], 16)},{int(digits[2:4], 16)},{int(digits[4:6], 16)}"
    fn = _RGB_FN_RE.match(text)
    if fn:
        return f"{int(fn.group(1))},{int(fn.group(2))},{int(fn.group(3))}"
    logger.warning("无法解析颜色通道: %s", value)
    return "136,136,136"


def _channel_token(name: str) -> str:
    """把颜色通道名归一化为令牌名。

    :param name: 通道名（通常与令牌同名，少数见 ``_RGB_ALIASES``）
    :return: 令牌名
    """
    return _RGB_ALIASES.get(name, name)


def _channel_rgb(name: str, index: int) -> str | None:
    """取颜色通道的 ``"r,g,b"`` 串。

    :param name: 通道名（令牌名或别名）
    :param index: 取值下标（0=深色，1=浅色）
    :return: 形如 ``"59,130,246"``；通道不存在时告警并返回 None
    """
    pair = TOKENS.get(_channel_token(name))
    if pair is None:
        logger.warning("未知颜色通道: %s", name)
        return None
    return _to_rgb(pair[index])


def _rgb_css(selector: str, index: int) -> str:
    """生成某个选择器下的颜色通道声明块（每个令牌一个，别名一并生成）。

    :param selector: CSS 选择器（如 ":root" / "body.qs-dark"）
    :param index: 取值下标（0=深色，1=浅色）
    :return: CSS 片段
    """
    names = list(TOKENS) + [a for a in _RGB_ALIASES if a not in TOKENS]
    lines = [f"    {_PREFIX}{name}-rgb: {_channel_rgb(name, index)};" for name in names]
    return f"{selector} {{\n" + "\n".join(lines) + "\n}"


def _tokens_css(selector: str, index: int) -> str:
    """生成某个选择器下的令牌声明块。

    :param selector: CSS 选择器（如 ":root" / "body.qs-dark"）
    :param index: 取值下标（0=深色，1=浅色）
    :return: CSS 片段
    """
    lines = [f"    {_PREFIX}{name}: {value[index]};" for name, value in TOKENS.items()]
    return f"{selector} {{\n" + "\n".join(lines) + "\n}"


# 共享样式表：令牌定义 + Quasar 表单控件配色覆盖。
# 浅色为默认（:root），深色挂 body.qs-dark 覆盖；两套同时存在，
# 因此只需要安装一次，且反复切换主题不会产生任何新的 <style> 节点。
_THEME_CSS = "\n".join([
    "/* ===== 设计令牌：浅色（默认） ===== */",
    _tokens_css(":root", 1),
    "/* ===== 设计令牌：深色 ===== */",
    _tokens_css(f"body.{DARK_CLASS}", 0),
    "/* ===== 颜色通道：供 rgba(var(--qs-<名>-rgb), a) 拼透明度 ===== */",
    _rgb_css(":root", 1),
    _rgb_css(f"body.{DARK_CLASS}", 0),
    """
/* ===== Quasar 组件配色覆盖（跟随令牌，切主题即时生效） ===== */
.q-field__native,
.q-field__input {
    color: var(--qs-text) !important;
}
.q-field__native span,
.q-select__dropdown .q-item__label {
    color: var(--qs-text) !important;
}
.q-field__label {
    color: var(--qs-muted) !important;
}
.q-field__native::placeholder {
    color: var(--qs-muted) !important;
}
.q-textarea .q-field__native {
    min-height: auto !important;
}
.q-btn__content,
.q-btn__content .block,
.q-btn__content > * {
    color: inherit !important;
}
body:not(.qs-dark) .q-field--outlined .q-field__control:before {
    border-color: var(--qs-border);
}
body:not(.qs-dark) .q-table tbody td {
    color: var(--qs-text);
}
""",
])


def install_theme_css() -> None:
    """安装全局主题样式表（幂等，进程内只装一次）。

    必须在任何页面渲染之前调用；``set_theme()`` 也会兜底调用一次。
    """
    global _styles_installed
    if _styles_installed:
        return
    ui.add_css(_THEME_CSS, shared=True)
    _styles_installed = True


def _current_client() -> Any:
    """返回当前 NiceGUI 客户端；不在请求 / 页面上下文时返回 None。"""
    try:
        return context.client
    except Exception:  # noqa: BLE001 - 非请求上下文（启动期、后台线程）
        return None


class ThemeManager:
    """深色 / 浅色主题管理器。

    线程安全说明：``self._mode`` 仅作为"当前进程内最近一次应用的模式"，
    真正的主题归属存在 ``app.storage.user["theme_mode"]``（会话级），
    页面渲染入口一律走 :meth:`apply_session_theme`，因此多用户不会串号。
    """

    def __init__(self) -> None:
        self._mode: str = DEFAULT_MODE

    # ---- 模式解析 ----

    @staticmethod
    def normalize_mode(mode: Any) -> str:
        """把任意输入规范化为 "dark" / "light"。

        :param mode: 待规范化值（可能来自接口、会话或用户输入）
        :return: "dark" 或 "light"，无法识别时回退默认主题
        """
        if isinstance(mode, str) and mode.strip().lower() == "light":
            return "light"
        if isinstance(mode, str) and mode.strip().lower() == "dark":
            return "dark"
        return DEFAULT_MODE

    def session_mode(self) -> str | None:
        """读取当前会话已记住的主题模式。

        :return: "dark" / "light"；会话不可用或未记录时返回 None
        """
        try:
            storage = app.storage.user  # 未配置 storage_secret / 无请求上下文时会抛异常
        except Exception:  # noqa: BLE001
            return None
        try:
            raw = storage.get("theme_mode")
        except Exception:  # noqa: BLE001
            return None
        if isinstance(raw, str) and raw.strip().lower() in ("dark", "light"):
            return raw.strip().lower()
        return None

    def remember_mode(self, mode: str) -> None:
        """把主题模式记到会话里（供后续页面渲染直接使用）。

        :param mode: 主题模式
        """
        try:
            app.storage.user["theme_mode"] = self.normalize_mode(mode)
        except Exception:  # noqa: BLE001 - 无会话上下文时静默跳过
            logger.debug("无法写入会话主题模式（无会话上下文）")

    # ---- 应用主题 ----

    def apply_session_theme(self) -> str:
        """按当前会话记住的模式应用主题（页面渲染入口统一调用）。

        会话里还没有记录（首次进入该会话、或会话存储不可用）时回退到
        :data:`DEFAULT_MODE`——**不回退到进程级状态**，否则多用户会串号。

        :return: 实际生效的模式
        """
        mode = self.session_mode() or DEFAULT_MODE
        self.set_theme(mode)
        return self._mode

    def set_theme(self, mode: Any) -> None:
        """应用主题：更新 Quasar 色板 + 切换 ``body.qs-dark``。

        :param mode: 主题模式（"dark" / "light"）
        """
        mode = self.normalize_mode(mode)
        self._mode = mode
        is_dark = mode == "dark"

        install_theme_css()

        # Quasar 语义色需要真实色值（不是 CSS 变量）
        ui.colors(
            primary=self.get_color("primary"),
            secondary=self.get_color("secondary"),
            accent=self.get_color("accent"),
            positive=self.get_color("positive"),
            negative=self.get_color("negative"),
            background=self.get_color("page"),
            text=self.get_color("text"),
        )

        client = _current_client()
        if client is None:
            return

        class_js = (
            f'document.body.classList.toggle("{DARK_CLASS}", {str(is_dark).lower()});'
        )
        # 首屏：写进 body_html —— 浏览器解析到 <div id="app"> 之前就执行。
        # 浅色是 CSS 默认（:root），首帧本来就是浅色；这里主要是让深色用户
        # 在首帧就带上 qs-dark，避免"先按浅色画一帧再闪成深色"。
        try:
            ui.add_body_html(f"<script>{class_js}</script>")
        except Exception:  # noqa: BLE001 - 无 body 上下文时忽略，下一帧会补上
            logger.debug("首屏主题类注入失败", exc_info=True)
        # 已连接（如点击开关触发的重设）：立即执行，无需等下一次渲染
        if client.has_socket_connection:
            ui.run_javascript(class_js)

    def toggle(self) -> str:
        """在深色 / 浅色之间切换并立即生效。

        :return: 切换后的模式
        """
        self.set_theme("light" if self.is_dark() else "dark")
        return self._mode

    # ---- 取色 ----

    def var(self, name: str) -> str:
        """取令牌的 CSS 变量引用（HTML / 内联样式场景）。

        :param name: 令牌名，如 "text" / "surface-alt"
        :return: 形如 ``var(--qs-text)``
        """
        if name not in TOKENS:
            logger.warning("未知主题令牌: %s", name)
        return _var(name)

    def var_rgba(self, name: str, alpha: float) -> str:
        """取「令牌 + 透明度」的 CSS 引用（界面里拼半透明色专用）。

        CSS 无法给 ``var()`` 追加透明度（``var(--qs-text)88`` 是非法声明，
        整条会被丢弃），因此必须走颜色通道。

        :param name: 颜色通道名（默认与令牌同名，别名见 ``_RGB_ALIASES``）
        :param alpha: 透明度 0~1
        :return: 形如 ``rgba(var(--qs-primary-rgb), 0.15)``
        """
        if _channel_token(name) not in TOKENS:
            logger.warning("未知颜色通道: %s", name)
        return f"rgba(var({_PREFIX}{name}-rgb), {alpha:g})"

    def get_color(self, token: str) -> str:
        """取令牌的**真实色值**（Quasar 属性 / ECharts 等 canvas 场景必须用这个）。

        :param token: 令牌名，如 "positive" / "border"
        :return: 十六进制 / rgba 色值字符串
        """
        pair = TOKENS.get(token)
        if pair is None:
            logger.warning("未知主题令牌: %s", token)
            return _FALLBACK_COLOR
        return pair[0] if self.is_dark() else pair[1]

    def get_rgba(self, name: str, alpha: float) -> str:
        """取「真实色值 + 透明度」的 rgba（ECharts 等 canvas 场景专用）。

        :param name: 颜色通道名（与 :meth:`var_rgba` 同一套命名，含别名）
        :param alpha: 透明度 0~1
        :return: 形如 ``rgba(59,130,246, 0.15)``；通道未知时回退中性灰
        """
        rgb = _channel_rgb(name, 0 if self.is_dark() else 1) or "136,136,136"
        return f"rgba({rgb}, {alpha:g})"

    def get_chart_colors(self) -> dict[str, str]:
        """返回 ECharts 需要的真实色值集合。

        ``line*`` 是主曲线（系统总收益），``line2*`` 是叠加在其上的**对比曲线**
        （概要页选中的单个用户策略）。副线用琥珀（accent）：与蓝色主线圈反差
        最大，且不带 A 股涨跌语义——用绿色（secondary）当曲线会与「跌」混淆。

        :return: {up, down, axis, grid, axis_line, line, line2, border, volume,
                  line_area_top, line_area_bottom, line2_area_top, line2_area_bottom}
        """
        return {
            "up": self.get_color("positive"),          # 涨 / 买入（红）
            "down": self.get_color("negative"),        # 跌 / 卖出（绿）
            "axis": self.get_color("muted"),           # 坐标轴文字
            "grid": self.get_color("border-soft"),     # 网格线
            "axis_line": self.get_color("border"),     # 轴线
            "line": self.get_color("primary"),         # 折线主色
            "line2": self.get_color("accent"),         # 对比曲线（单策略）
            "border": self.get_color("border"),
            "volume": self.get_color("chart-volume"),  # 成交量柱
            "line_area_top": self.get_rgba("primary", 0.3),     # 折线下渐变起点
            "line_area_bottom": self.get_rgba("primary", 0.0),  # 折线下渐变终点
            "line2_area_top": self.get_rgba("accent", 0.22),    # 对比曲线渐变起点
            "line2_area_bottom": self.get_rgba("accent", 0.0),  # 对比曲线渐变终点
        }

    def is_dark(self) -> bool:
        """当前是否为深色主题。"""
        return self._mode == "dark"


theme_manager = ThemeManager()

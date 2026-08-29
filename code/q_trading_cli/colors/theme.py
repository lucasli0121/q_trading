"""
Author: liguoqiang
Date: 2026-05-25 17:58:34
LastEditors: liguoqiang
LastEditTime: 2026-05-25 17:58:34
Description: 
"""
from nicegui import ui


class ThemeManager:
    def __init__(self):
        self.dark = True
        self.themes = {
            'light': {
                'primary': '#2563eb',           # 主品牌色（蓝）
                'background': '#f9fafb',        # 页面背景（浅灰）
                'text': '#111827',              # 主文字（深灰）
                'placeholder': '#6b7280',       # 占位符/次要文字
                'accent': '#f59e0b',            # 强调色（琥珀黄）
                'secondary': '#16a34a',         # 辅助色（绿）
                'positive': '#f87171',          # 正向/买入
                'negative': '#34d399',          # 负向/卖出
                'tab_normal_color': '#f3f4f6',  # 标签页常规背景
                'tab_active_color': '#2563eb',  # 标签页激活色
                'font_color': '#111827',        # 通用文字颜色
                'separator_color': '#e5e7eb',   # 分割线
                'main_border_color': '#e5e7eb', # 主要边框
                'widget_border_color': '#d1d5db'# 小部件边框
            },
            'dark': {
                'primary': '#3b82f6',           # 主品牌色（亮蓝）
                'background': '#0f0f1a',        # 页面背景（深蓝黑）
                'text': '#e5e7eb',              # 主文字（浅灰白）
                'placeholder': '#9ca3af',       # 占位符/次要文字
                'accent': '#fbbf24',            # 强调色（亮黄）
                'secondary': '#34d399',         # 辅助色（翠绿）
                'positive': '#f87171',          # 正向/买入
                'negative': '#34d399',          # 负向/卖出
                'tab_normal_color': '#1e293b',  # 标签页常规背景（卡片色）
                'tab_active_color': '#3b82f6',  # 标签页激活色
                'font_color': '#e5e7eb',        # 通用文字颜色
                'separator_color': '#334155',   # 分割线
                'main_border_color': '#334155', # 主要边框
                'widget_border_color': '#334155'# 小部件边框（也可用带透明度）
            }
        }
    
    def set_theme(self, mode):
        theme = self.themes[mode]
        # 设置NiceGUI全局主题色
        ui.colors(
            primary=theme['primary'],
            background=theme['background'],
            text=theme['text'],
            accent=theme['accent'],
            secondary=theme['secondary'],
            positive=theme['positive'],
            negative=theme['negative']
        )
        self.dark = (mode == 'dark')
        # 设置全局dark mode
        ui.dark_mode.enable = self.dark
        # 注入全局组件样式 — Quasar QInput/QSelect/QBtn 文字不受 ui.colors() 控制
        font_color: str = theme.get("font_color", "#e5e7eb")
        placeholder_color: str = theme.get("placeholder", "#9ca3af")
        ui.add_css(f"""
            /* ---- 输入框 / select 文字 ---- */
            .q-field__native,
            .q-field__input {{
                color: {font_color} !important;
            }}
            .q-field__native span,
            .q-select__dropdown .q-item__label {{
                color: {font_color} !important;
            }}
            /* placeholder / label */
            .q-field__label {{
                color: {placeholder_color} !important;
            }}
            .q-field__native::placeholder {{
                color: {placeholder_color} !important;
            }}

            /* ---- textarea — 允许自定义高度 ---- */
            .q-textarea .q-field__native {{
                min-height: auto !important;
            }}

            /* ---- 按钮文字 — 强制继承外层，覆盖 Quasar .block/.q-icon 等内层 ---- */
            .q-btn__content,
            .q-btn__content .block,
            .q-btn__content > * {{
                color: inherit !important;
            }}
        """)

    def get_current_theme(self) -> dict:
        return self.themes['dark'] if self.is_dark() else self.themes['light']
    
    def is_dark(self) -> bool:
        return self.dark
    
    def toggle(self):
        if self.dark:
            self.set_theme('light')
        else:
            self.set_theme('dark')

theme_manager = ThemeManager()
#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-05-24 15:40:48
LastEditors: liguoqiang
LastEditTime: 2026-05-25 21:57:49
Description: 首页显示的内容
"""
from nicegui import ui

from app_context import AppContext
from components import inputs, labels
from menu.top_menu import top_menu


def show_header_page():
    current_theme = AppContext().theme_manager.get_current_theme()
    bg_color = current_theme.get('background')
    widget_border_color = current_theme.get('widget_border_color', '#80808033')
    with ui.row().classes('w-full h-full gap-0') \
        .props('flat bordered') \
        .style(f'padding: 5px; border: 1px solid {widget_border_color}; border-radius: 10px; background: {bg_color}'):
        with ui.column().classes('w-[30%] h-full gap-0'):
            with ui.row().classes('items-left gap-1').style('margin: 0 !important; padding: 0 !important;'):
                labels.normal_label('最近走势:', w='auto', font_size='xs', align='self-start')
                labels.normal_label('2026-05-25 10:54:09', w='auto', font_size='xs', align='self-start')
            with ui.row().classes('items-left gap-1').style('margin: 0 !important; padding: 0 !important;'):
                labels.normal_label('观点:', w='auto', font_size='xs', align='self-start')
                labels.normal_label('近期市场震荡加剧，建议关注强势反弹机会。', w='auto', font_size='xs', align='self-start')
        with ui.row().classes('w-[40%] h-full gap-0 place-content-center'):
            inputs.input_search(w='60', placeholder='搜索股票...', on_enterkey=lambda: ui.notify('搜索功能待实现')).classes('self-center')
        ui.space()
        with ui.row().classes('h-full gap-0 place-content-center'):
            top_menu()
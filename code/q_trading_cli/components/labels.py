#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-05-24 15:46:01
LastEditors: liguoqiang
LastEditTime: 2026-05-24 15:57:54
Description: 
"""
from nicegui import ui

from app_context import AppContext


def normal_label(text, w = '20%', align = 'self-center', font_size = 'sm') -> ui.label:
    current_theme = AppContext().theme_manager.get_current_theme()
    font_color = current_theme.get('font_color', '#c9d1d9')
    return ui.label(text) \
        .classes(f'font-normal text-[{font_size}] text-[{font_color}] {align}') \
        .style(f'padding: 5px; margin: 0; width: {w};')

def medium_label(text, w = '20%', align = 'self-center', font_size = 'xs') -> ui.label:
    current_theme = AppContext().theme_manager.get_current_theme()
    font_color = current_theme.get('font_color', '#c9d1d9')
    return ui.label(text) \
        .classes(f'font-medium text-[{font_size}] text-[{font_color}] {align}') \
        .style(f'padding: 5px; margin: 0; width: {w};')

def bold_label(text, w = '20%', align = 'self-center', font_size = '1g') -> ui.label:
    current_theme = AppContext().theme_manager.get_current_theme()
    font_color = current_theme.get('font_color', '#c9d1d9')
    return ui.label(text) \
        .classes(f'font-bold text-[{font_size}] text-[{font_color}] {align}') \
        .style(f'padding: 5px; margin: 0; width: {w};')
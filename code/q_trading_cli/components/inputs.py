#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-05-25 16:28:39
LastEditors: liguoqiang
LastEditTime: 2026-05-25 21:16:53
Description: 
"""
from nicegui import ui

from app_context import AppContext


# 登录用户输入框
def input_user_w60(placeholder, on_enterkey) -> ui.input:
    current_theme = AppContext().theme_manager.get_current_theme()
    text_color = current_theme.get("text", "#d4d4d4")
    placeholder_color = current_theme.get("placeholder", "#808080")
    primary_color = current_theme.get("primary", "#569cd6")
    ui.add_css(f"""
        .login-input-ph input::placeholder {{
            color: {placeholder_color} !important;
            opacity: 1;
        }}
    """)
    with ui.input(placeholder=placeholder) \
        .props(f"autofocus outlined rounded color=\"{primary_color}\" input-style=\"color: {text_color}\"") \
        .classes("w-[370px] h-[64px] self-center item-center login-input-ph") as input, \
        input.add_slot("prepend"):
        ui.icon("person").on("click", on_enterkey) \
            .classes("cursor-pointer") \
            .style(f"color: {text_color};")
    input.on("keydown.enter", on_enterkey)
    return input

def input_password_w60(placeholder, on_enterkey) -> ui.input:
    current_theme = AppContext().theme_manager.get_current_theme()
    text_color = current_theme.get("text", "#d4d4d4")
    placeholder_color = current_theme.get("placeholder", "#808080")
    primary_color = current_theme.get("primary", "#569cd6")
    ui.add_css(f"""
        .login-input-ph input::placeholder {{
            color: {placeholder_color} !important;
            opacity: 1;
        }}
    """)
    with ui.input(placeholder=placeholder, password=True, password_toggle_button=True) \
        .props(f"autofocus outlined rounded color=\"{primary_color}\" input-style=\"color: {text_color}\"") \
        .classes("w-[370px] h-[64px] self-center item-center login-input-ph") as input, \
        input.add_slot("prepend"):
        ui.icon("lock").on("click", on_enterkey) \
            .classes("cursor-pointer") \
            .style(f"color: {text_color};")
    input.on("keydown.enter", on_enterkey)
    return input

def input_search(w: str = "60px", placeholder: str = "", on_enterkey=None) -> ui.input:
    current_theme = AppContext().theme_manager.get_current_theme()
    text_color = current_theme.get("text", "#d4d4d4")
    widget_border_color = current_theme.get("widget_border_color", "#80808033")
    placeholder_color = current_theme.get("placeholder", "#808080")
    bg_color = current_theme.get("background", "#1e1e1e")
    with ui.input(placeholder=placeholder) \
        .props(f"outlined dense input-style=\"color: {placeholder_color}\"") \
        .classes(f"w-{w} text-[{text_color}] rounded-md self-center") \
        .style(f"border: 1px solid {widget_border_color}; background-color: {bg_color};") as input, \
        input.add_slot("append"):
        ui.icon("search").on("click", on_enterkey).classes(f"text-[{text_color}] cursor-pointer")
    input.on('on_enterkey', on_enterkey)
    input.on('keydown.enter', on_enterkey)
    return input

def date_input_w40(placeholder, on_enterkey) -> ui.input:
    with ui.input(placeholder=placeholder) \
        .props('autofocus rounded-md outlined dense') \
        .classes('w-40 self-center item-center ') as date_input, \
        ui.menu().props('no-parent-event') as menu, \
        ui.date().bind_value(date_input).on_value_change(on_enterkey), \
        ui.row().classes('justify-end'):
        ui.button('Close', on_click=menu.close).props('flat')
    with date_input.add_slot('append'):
        ui.icon('calendar_month').on('click', menu.open).classes('cursor-pointer')
    date_input.on('keydown.enter', on_enterkey)
    return date_input


def selection_w40(options, value, need_input:bool, on_change) -> ui.select:
    if on_change is None:
        def default_on_change(v):
            pass
        on_change = default_on_change
    return ui.select(options=options, value=value, with_input=need_input, on_change=lambda e: on_change(e.value)) \
        .props('autofocus rounded-md outlined dense') \
        .classes('w-40 self-center item-center transition-all')

def selection_w60(options, value, need_input:bool, on_change) -> ui.select:
    if on_change is None:
        def default_on_change(v):
            pass
        on_change = default_on_change
    return ui.select(options=options, value=value, with_input=need_input, on_change=lambda e: on_change(e.value)) \
        .props('autofocus rounded-md outlined dense') \
        .classes('w-60 self-center item-center transition-all')

def selection_w80(options, value, need_input:bool, on_change) -> ui.select:
    if on_change is None:
        def default_on_change(v):
            pass
        on_change = default_on_change
    return ui.select(options=options, value=value, with_input=need_input, on_change=lambda e: on_change(e.value)) \
        .props('autofocus rounded-md outlined dense') \
        .classes('w-80 self-center item-center transition-all')

def show_add_device_input(placeholder) -> ui.input:
    intput = ui.input(placeholder=placeholder) \
        .props('rounded-md outlined dense') \
        .classes('size-full self-center item-center custom-border')
    return intput
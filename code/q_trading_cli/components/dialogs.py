#!/usr/bin/env python3
'''
Author: liguoqiang
Date: 2025-03-16 17:25:54
LastEditors: liguoqiang
LastEditTime: 2025-03-19 17:27:38
Description: 
'''
from collections.abc import Callable

from nicegui import ui


#
# 显示确认对话框
#
def make_sure_dialog(message: str, on_ok: Callable) -> ui.dialog:
    with ui.dialog().props('persistent') as dialog, ui.card() \
        .style('background-color: var(--qs-surface) !important; border-radius: 10px;'):
        ui.label(message).classes('w-full text-[16px] text-[var(--qs-heading)] font-normal')
        with ui.row().classes('w-full place-content-end'):
            ui.button('取消', color=None, on_click=dialog.close) \
                .props('flat') \
                .classes('w-[120px] text-[16px] text-[var(--qs-muted)] font-[400]') \
                .style('background-color: var(--qs-surface) !important;border-radius: 10px;border: 1px solid var(--qs-border);')
            def make_ok():
                try:
                    on_ok()
                    dialog.close()
                except Exception as e:  # noqa: BLE001
                    ui.notify(f'操作失败: {e!s}')
            ui.button('确定', color=None, on_click=make_ok) \
                .props('flat') \
                .classes('w-[120px] text-[16px] text-white font-[400]') \
                .style('background-color: var(--qs-primary) !important; border-radius: 10px')
    dialog.open()
    return dialog

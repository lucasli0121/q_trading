#!/usr/bin/env python3

from nicegui import ui


def show_concentration_progress(concentration) -> ui.linear_progress:
    with ui.row().classes('w-full gap-0 item-center place-content-start'):
        ui.icon('visibility').classes('gb-blue-500 place-self-bottom').style('font-size: 24px; width: 10%;')
        with ui.column().classes('gap-0').style('width: 85%;'):
            with ui.row().classes('w-full item-center place-content-between'):
                ui.label('学习专注度').classes('text-sm text-normal text-black-500')
                ui.label(f'{concentration}分').classes('text-sm text-black text-normal')
            progress = ui.linear_progress(0.0, size='10px', show_value=False, color='yellow').classes('w-full').props('rounded')
            v = concentration / 100
            progress.set_value(v)
    return progress

def show_position_progress(position) -> ui.linear_progress:
    with ui.row().classes('w-full gap-0 item-center place-content-start'):
        ui.icon('airline_seat_recline_normal').classes('round dense gb-blue-500' ).style('font-size: 24px; width: 10%;')
        with ui.column().classes('gap-0').style('width: 85%;'):
            with ui.row().classes('w-full item-center place-content-between'):
                ui.label('坐姿管理').classes('text-sm text-normal text-black-500')
                ui.label(f'{position}分').classes('text-sm text-black text-normal')
            progress = ui.linear_progress(0.0, size='10px', show_value=False, color='red').classes('w-full').props('rounded')
            v = position / 100
            progress.set_value(v)
    return progress

def show_study_progress(study) -> ui.linear_progress:
    with ui.row().classes('w-full gap-0 item-center place-content-start'):
        ui.icon('auto_graph').classes('round dense gb-blue-500').style('font-size: 24px; width: 10%;')
        with ui.column().classes('gap-0').style('width: 85%;'):
            with ui.row().classes('w-full item-center place-content-between'):
                ui.label('坐姿管理').classes('text-sm text-normal text-black-500')
                ui.label(f'{study}分').classes('text-sm text-black text-normal')
            progress = ui.linear_progress(0.0, size='10px', show_value=False, color='green').classes('w-full').props('rounded')
            v = study / 100
            progress.set_value(v)
    return progress
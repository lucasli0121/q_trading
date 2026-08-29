#!/usr/bin/env python3
import logging

from nicegui import ui

logger = logging.getLogger(__name__)

def show_paytax_card(tax):
    with ui.card().classes('w-[150px] h-[120px]') \
        .props('flat bordered') \
        .style('padding: 5px; border: 1px solid #65B6FF; border-radius: 10px; background: #FCFEFF'), \
        ui.column().classes('size-full items-center place-content-start gap-0 p-0 m-0'):
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('月份:').classes('text-xs text-red-500')
            ui.label(f'{tax["month"]}月').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('增值税:').classes('text-xs text-red-500')
            ui.label(f'{tax["added_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('印花税:').classes('text-xs text-red-500')
            ui.label(f'{tax["stamp_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('所得税:').classes('text-xs text-red-500')
            ui.label(f'{tax["income_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('合计:').classes('text-xs text-red-500')
            ui.label(f'{tax["total_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
                
def show_brief_report_card(tax):
    with ui.card().classes('w-[150px] h-[120px]') \
        .props('flat bordered') \
        .style('padding: 5px; border: 1px solid #65B6FF; border-radius: 10px; background: #FCFEFF'), \
        ui.column().classes('size-full items-center place-content-start gap-0 p-0 m-0'):
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('月份:').classes('text-xs text-red-500')
            ui.label(f'{tax["month"]}月').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('未认证税额:').classes('text-xs text-red-500')
            ui.label(f'{tax["uncertified_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('留抵税额:').classes('text-xs text-red-500')
            ui.label(f'{tax["retained_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
        with ui.row().classes('w-full items-center place-content-start gap-0'):
            ui.label('收入:').classes('text-xs text-red-500')
            ui.label(f'{tax["income_tax"]}').classes('text-[14px] text-[#888888] self-center font-bold')
            

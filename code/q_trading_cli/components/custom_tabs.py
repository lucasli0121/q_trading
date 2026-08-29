#!/usr/bin/env python3
from nicegui import ui

from app_context import AppContext


def load_left_drawer_tab_css():
    current_theme = AppContext().theme_manager.get_current_theme()

    tab_selected_bg = current_theme.get('tab_active_color', '#21262d')
    font_color = current_theme.get('font_color', '#c9d1d9')
    bg_color = current_theme.get('background')

    ui.add_css(f"""
        .left-drawer-tabs .q-tab__indicator {{
            display: none !important;
        }}
        .left-drawer-tabs .q-tab__label {{
            font-size: 18px !important;
            color: {font_color} !important;
        }}
        .left-drawer-tabs .q-tab--active,
        .left-drawer-tabs .q-tab.q-tab--active,
        .left-drawer-tabs .q-tab[aria-selected="true"] {{
            background-color: {tab_selected_bg} !important;
        }}
        .left-drawer-tabs {{
            padding: 0 !important;
            margin-top: 30px !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
            width: 100% !important;
            height: 100% !important;
            align-items: center !important;
        }}
        .left-drawer-tabs .q-tab {{
            background-color: {bg_color} !important;
            border-radius: 0 !important;
            margin: 0 !important;
            margin-bottom: 30px !important;
            padding: 0 !important;
            width: 100% !important;
            height: 40px !important;
        }}
    """)


def load_page_tab_css():
    current_theme = AppContext().theme_manager.get_current_theme()

    bg_color = current_theme.get('background')

    ui.add_css(f"""
        .page-tabs .q-tab {{
            background-color: {bg_color} !important;
            border-radius: 10px !important;
            margin-left: 0px !important;
            margin-right: 0px !important;
            margin-bottom: 0px !important;
            padding: 5px !important;
            width: 100px !important;
            align-items: left !important;
        }}
    """)    
#!/usr/bin/env python3
'''
Author: liguoqiang
Date: 2024-04-06 10:48:29
LastEditors: liguoqiang
LastEditTime: 2024-07-21 11:13:24
Description: 
'''

from datetime import datetime


def str_to_date(dateStr: str):
    """解析日期字符串，失败返回 None（不再抛 ValueError）。"""
    if not isinstance(dateStr, str) or not dateStr.strip():
        return None
    text: str = dateStr.strip()
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None

def str_to_date_time(dateStr: str):
    """解析日期时间字符串，失败返回 None（不再抛 ValueError）。"""
    if not isinstance(dateStr, str) or not dateStr.strip():
        return None
    text: str = dateStr.strip()
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
    ):
        try:
            return datetime.strptime(text, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

def cur_date_str():
    return datetime.now().strftime('%Y-%m-%d')  # noqa: DTZ005

def cur_date_time_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # noqa: DTZ005

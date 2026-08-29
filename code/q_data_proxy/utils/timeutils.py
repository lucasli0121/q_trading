'''
Author: liguoqiang
Date: 2024-04-06 10:48:29
LastEditors: liguoqiang
LastEditTime: 2024-07-21 11:13:24
Description: 
'''

from datetime import datetime, timedelta


def str_to_date(dateStr: str):
    if isinstance(dateStr, str):
        if len(dateStr) < 11:
            return datetime.strptime(dateStr, '%Y-%m-%d').date()
        else:
            return datetime.strptime(dateStr, '%Y-%m-%d %H:%M:%S').date()
    else:
        return None

def str_to_date_time(dateStr: str):
    if isinstance(dateStr, str) and len(dateStr) > 0 and len(dateStr) < 20:
        return datetime.strptime(dateStr, '%Y-%m-%d %H:%M:%S')
    else:
        return None
    
def cur_date_str():
    return datetime.now().strftime('%Y-%m-%d')

def cur_date_time_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
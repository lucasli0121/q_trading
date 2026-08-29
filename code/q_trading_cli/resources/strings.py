#!/usr/bin/env python3

# General
string_resources = {
    'app_name': "QTrade",
    'app_version': "1.0.0",
    'app_author': "Li guo qiang",
    'log_content': "日志内容",
    'log_status': "状态",
    'log_datetime': "时间",
    'operation': "操作",
    'first_page': "策略",
    'second_page': "复盘",
    'settings': "设置",
    
}

def get(key: str) -> str:
    """
    获取字符串资源
    :param key: 字符串资源的键
    :return: 对应的字符串资源
    """
    if key in string_resources:
        return string_resources[key]
    else:
        # 如果键不存在，返回默认值或抛出异常
        # raise KeyError(f"String resource '{key}' not found.")
        # 或者返回键本身作为默认值
        return ""  # 返回空字符串作为默认值
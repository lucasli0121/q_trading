
from configparser import ConfigParser
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo
import pandas as pd
import urllib3 as ulib

class TickPeriodEnum(Enum):
    ONE_MINUTE = "1m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    THIRTY_MINUTE = "30m"
    SIXTY_MINUTE = "60m"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"
    ONE_MONTH = "1M"

class TickAdjustEnum(Enum):
    NO_ADJUST = "none"
    PRE_ADJUST = "forward"
    POST_ADJUST = "backward"

class TickFlowBase():
    def __init__(self, **kargs) -> None:
        if "api_token" in kargs:
            self.api_token = kargs["api_token"]
        else:
            self.api_token = ""
        if "server_url" in kargs:
            self.server_url = kargs["server_url"]
        else:
            self.server_url = ""
        self.headers = {"Content-Type": "application/json", "x-api-key": self.api_token}
        self.http_client = ulib.PoolManager(headers=self.headers, timeout=30.0, retries=3)
        self.logger = logging.getLogger(__name__)

    """
    function: milliseconds_utc_to_date_str
    description: 把国标的毫秒时间值转换为中国时间字符串，格式为%Y-%m-%d %H:%M:%S
    param {*} self
    param {*} symbols
    return {*}
    """
    def milliseconds_utc_to_date_str(self, timestamp: Any) -> str:
        # 转为 UTC 时间
        utc_time = pd.to_datetime(timestamp, unit='ms', utc=True)
        # 转为上海时区
        if isinstance(utc_time, pd.Series):
            # Series 必须使用 dt 访问器进行时区转换，直接调用 .tz_convert 会尝试转换索引导致 TypeError
            cn_time = utc_time.dt.tz_convert('Asia/Shanghai')
            # 只返回第一个
            return cn_time.dt.strftime('%Y-%m-%d %H:%M:%S').iloc[0]
        else:
            cn_time = utc_time.tz_convert('Asia/Shanghai')
            return cn_time.strftime('%Y-%m-%d %H:%M:%S')
    """
    function: date_str_to_milliseconds_utc
    description: 把时间字符串格式为%Y-%m-%d %H:%M:%S转换成utc毫秒
    param {*} self
    param {*} symbols
    return {*}
    """
    def date_str_to_milliseconds_utc(self, date_str: str) -> int:
        # 构造上海时区的日期时间
        cn_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        # 获取UTC时间戳
        utc_seconds = cn_dt.astimezone(ZoneInfo("UTC")).timestamp()
        return int(utc_seconds * 1000)

    """
    function: is_valid_stock_code
    description: 检查股票代码，如果以.SZ或者.SH结尾即为正确的tickflow代码
    param {*} self
    param {*} 
    return {*}
    """
    def is_valid_stock_code(self, s: str) -> bool:
        # ^\d+ 表示以一个或多个数字开头；(\.SZ|\.SH)$ 表示以.SZ或.SH结尾
        result = re.match(r'^\d+(\.SZ|\.SH|\.BJ)$', s) is not None
        if result:
            prefix2 = s[:2]
            prefix1 = s[:1]
            tail = s[-2:]
            if prefix2 in ("60", "68") and tail != "SH":
                result = False
            elif prefix2 in ("00", "30") and tail != "SZ":
                result = False
            elif prefix1 in ("4", "8") and tail != "BJ":
                result = False
        return result


    """
    function: 
    description: 获取实时行情数据
    param {*} self
    param {*} symbols
    return {*}
    """
    def get_real_time_hq(self, symbols: list[str]) -> tuple[bool, Any]:
        payload = json.dumps({"symbols": symbols})
        url = self.server_url + "/quotes"
        return self.post_tickflow(url, payload)


    """
    function:
    description: 获取单个股票代码的K线数据
    param {*} self
    param {*} symbol
    param {*} start_date
    param {*} end_date
    param {*} period
    param {*} adjust
    return {*}
    """
    def query_stock_kline(self, symbol: str, start_date: str, end_date: str, period: str = "1d", adjust: str = "forward") -> tuple[bool, Any]:
        url = self.server_url + "/klines"
        if not end_date or len(end_date) <= 0:
            end_date = datetime.now().strftime("%Y-%m-%d")
        start_timestamp = self.date_str_to_milliseconds_utc(start_date)
        end_timestamp = self.date_str_to_milliseconds_utc(end_date)
        delta = datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")
        count = delta.days
        params = {
            "symbol": symbol,
            "start_time": start_timestamp,
            "end_time": end_timestamp,
            "period": period,
            "adjust": adjust
        }
        return self.get_tickflow(url, params)
    """
    function: 
    description: 获取行业基本信息列表
    param {*} self
    return {*}
    """
    def get_industry_base_info_list(self) -> tuple[bool, Any]:
        url = self.server_url + "/universes"
        return self.get_tickflow(url)
    """
    function: 
    description: 获取行业成分股列表
    param {*} self
    param {*} industry_ids
    return {*}
    """
    def get_more_industry_stock_list(self, industry_ids: list[str]) -> tuple[bool, Any]:
        url = self.server_url + "/universes/batch"
        payload = {"ids": industry_ids}
        return self.post_tickflow(url, payload)
    """
    function: 
    description: 获取行业成分股列表
    param {*} self
    param {*} industry_id
    return {*}
    """
    def get_one_industry_stock_list(self, industry_id: str) -> tuple[bool, Any]:
        url = self.server_url + f"/universes/{industry_id}"
        return self.get_tickflow(url)
    """
    function: 
    description: 实现一个GET方法，向服务器发送GET请求
    param {*} self
    param {*} url
    param {*} params
    return {*}
    """
    def get_tickflow(self, url, params=None) -> tuple[bool, Any]:
        try:
            rsp = self.http_client.request("GET", url, headers=self.headers, fields=params)
            if rsp.status == 200:
                response_jsons = json.loads(rsp.data)
                return True, response_jsons.get("data")
            else:
                response_text = rsp.data.decode("utf-8", errors="replace") if isinstance(rsp.data, bytes) else rsp.data
                self.logger.error(f"request tickflow api failed, status code: {rsp.status}, response: {response_text}")
                return False, response_text
        except ulib.exceptions.HTTPError as err:
            self.logger.error(err)
        return False, {"code": 500, "message": "Internal Server Error"}
    """
    function: 
    description: 实现一个POST方法，向服务器发送POST请求
    param {*} self
    param {*} url
    param {*} payload
    return {*}
    """

    def post_tickflow(self, url, payload) -> tuple[bool, Any]:
        try:
            rsp = self.http_client.request("POST", url, headers=self.headers, body=payload)
            if rsp.status == 200:
                response_jsons = json.loads(rsp.data)
                return True, response_jsons.get("data", [])
            else:
                response_text = rsp.data.decode("utf-8", errors="replace") if isinstance(rsp.data, bytes) else rsp.data
                self.logger.error(f"request tickflow api failed, status code: {rsp.status}, response: {response_text}")
                return False, response_text
        except ulib.exceptions.HTTPError as err:
            self.logger.error(err)
        return False, {"code": 500, "message": "Internal Server Error"}
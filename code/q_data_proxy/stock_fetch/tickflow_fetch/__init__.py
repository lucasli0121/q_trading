
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import re
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo
import pandas as pd
import urllib3 as ulib
from utils.config import load_stock_cfg

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
        # 本地限速与重试参数。
        # tickflow 免费套餐限制 10 次/分钟、单次最多 5 个标的，实测按 6 秒一次（10 次/分钟）
        # 发送时正好压在硬上限上，滚动窗口稍有抖动就被 429 拒绝，故本地按 9 次/分钟留出余量。
        cp = load_stock_cfg()
        self.max_requests_per_minute = int(kargs.get(
            "max_requests_per_minute",
            cp.get("tickflow", "max_requests_per_minute", fallback=9),
        ))
        self.rate_limit_retry_times = int(kargs.get(
            "rate_limit_retry_times",
            cp.get("tickflow", "rate_limit_retry_times", fallback=2),
        ))
        self.rate_limit_retry_delay_cap = float(kargs.get(
            "rate_limit_retry_delay_cap",
            cp.get("tickflow", "rate_limit_retry_delay_cap", fallback=3.0),
        ))
        if self.max_requests_per_minute <= 0:
            self.max_requests_per_minute = 9
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0

    """
    function: _acquire_rate_slot
    description: 获取本地限速配额，控制请求发送节奏，返回实际等待秒数。
        以「相邻请求间隔 >= 60 / max_requests_per_minute」为约束：满足该间隔时，
        任意滑动 60 秒窗口内的请求数必然不超过 max_requests_per_minute，
        从而不会贴着服务端硬上限发送而被 429 拒绝。
    param {*} self
    return {float} 实际等待的秒数
    """
    def _acquire_rate_slot(self) -> float:
        interval = 60.0 / self.max_requests_per_minute
        with self._rate_lock:
            now = time.monotonic()
            wait = self._last_request_ts + interval - now
            if wait < 0:
                wait = 0.0
            # 先占位再 sleep，保证并发调用同样按同一间距排队
            self._last_request_ts = now + wait
        if wait > 0:
            time.sleep(wait)
        return wait

    """
    function: _parse_retry_delay
    description: 从限流响应体中解析服务端建议的重试等待时间（秒）
        tickflow 的 429 响应形如 {"code":"RATE_LIMITED","message":"请求频率超限 (10/min)，请 265ms 后重试"}
    param {str} response_text: 响应体文本
    param {float} default: 无法解析时的默认等待秒数
    return {float} 等待秒数
    """
    @staticmethod
    def _parse_retry_delay(response_text: str, default: float = 0.5) -> float:
        if not isinstance(response_text, str) or not response_text:
            return default
        matched = re.search(r"(\d+(?:\.\d+)?)\s*ms\s*后重试", response_text)
        if matched:
            return float(matched.group(1)) / 1000.0 + 0.1
        matched = re.search(r"(\d+(?:\.\d+)?)\s*s\s*后重试", response_text)
        if matched:
            return float(matched.group(1)) + 0.1
        return default

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
        return re.match(r'^\d+(\.SZ|\.SH)$', s) is not None

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
    function: _send
    description: 统一的请求发送实现，集中处理本地限速、429 退避重试与网络异常重试。
        - 本地限速：每次请求前先获取配额，保证发送速率不超过 max_requests_per_minute
        - 429：按服务端返回的「请 Xms 后重试」提示退避后重试，最多 rate_limit_retry_times 次
        - 网络异常（读超时等）：接口为只读查询，可安全重试，退避时间递增且不超过 delay_cap
    param {str} method: "GET" 或 "POST"
    param {str} url: 请求地址
    param {*} fields: GET 查询参数
    param {*} body: POST 请求体
    param {*} data_default: 响应 data 字段缺省值
    return {tuple[bool, Any]} (是否成功, 响应 data 或错误信息)
    """
    def _send(
            self,
            method: str,
            url: str,
            fields: Any = None,
            body: Any = None,
            data_default: Any = None,
    ) -> tuple[bool, Any]:
        attempts = max(self.rate_limit_retry_times, 0) + 1
        for attempt in range(attempts):
            self._acquire_rate_slot()
            try:
                if method == "GET":
                    rsp = self.http_client.request("GET", url, headers=self.headers, fields=fields)
                else:
                    rsp = self.http_client.request("POST", url, headers=self.headers, body=body)
            except ulib.exceptions.HTTPError as err:
                if attempt < attempts - 1:
                    delay = min(0.5 * (attempt + 1), self.rate_limit_retry_delay_cap)
                    self.logger.warning("tickflow 请求异常，%.2fs 后重试: %s", delay, err)
                    time.sleep(delay)
                    continue
                self.logger.error("tickflow 请求异常，已重试 %d 次仍失败: %s", attempt, err)
                return False, {"code": 500, "message": "Internal Server Error"}
            if rsp.status == 200:
                response_jsons = json.loads(rsp.data)
                return True, response_jsons.get("data", data_default)
            response_text = rsp.data.decode("utf-8", errors="replace") if isinstance(rsp.data, bytes) else rsp.data
            if rsp.status == 429 and attempt < attempts - 1:
                delay = min(self._parse_retry_delay(response_text), self.rate_limit_retry_delay_cap)
                self.logger.warning(
                    "tickflow 限流 429，%.2fs 后重试（第 %d/%d 次）: %s",
                    delay, attempt + 1, attempts - 1, response_text,
                )
                time.sleep(delay)
                continue
            self.logger.error(f"request tickflow api failed, status code: {rsp.status}, response: {response_text}")
            return False, response_text
        return False, {"code": 500, "message": "Internal Server Error"}

    """
    function: 
    description: 实现一个GET方法，向服务器发送GET请求
    param {*} self
    param {*} url
    param {*} params
    return {*}
    """
    def get_tickflow(self, url, params=None) -> tuple[bool, Any]:
        return self._send("GET", url, fields=params, data_default=None)
    """
    function: 
    description: 实现一个POST方法，向服务器发送POST请求
    param {*} self
    param {*} url
    param {*} payload
    return {*}
    """

    def post_tickflow(self, url, payload) -> tuple[bool, Any]:
        return self._send("POST", url, body=payload, data_default=[])
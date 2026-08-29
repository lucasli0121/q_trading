"""
Author: liguoqiang
Date: 2022-06-07 15:10:35
LastEditors: liguoqiang
LastEditTime: 2024-04-07 19:16:46
Description: 
"""

import abc
from configparser import ConfigParser
from datetime import datetime, timedelta
import logging
import pandas as pd
import exchange_calendars as xcals

class FetchBase(metaclass=abc.ABCMeta):
    def __init__(self, **kargs) -> None:
        if "start_date" in kargs:
            start_date_str = kargs["start_date"]
        else:
            cp = ConfigParser()
            cp.read("cfg/stock.cfg")
            start_date_str = cp.get("stock", "start_date", fallback="2004-01-01")
        self.start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        self.xshg = xcals.get_calendar("XSHG", start=self.start_date)
        self.header = {"Content-Type": "application/json"}
        self.scheduler = None
        self.logger = logging.getLogger(__name__)
        if self.start_date < self.xshg.first_session.date():
            self.start_date = self.xshg.first_session.date()
        while not self.xshg.is_session(self.start_date):
            self.start_date = self.start_date + timedelta(days=1)

    @abc.abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def stop(self):
        raise NotImplementedError

    """
    function: get_previous_trading_day
    description: 获取指定日期的前一个交易日
    param {*} self
    param {str} date_str
    return {*}
    """    
    def get_previous_trading_day(self, date_str: str) -> str:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        while True:
            date -= timedelta(days=1)
            if self.xshg.is_session(date):
                return date.strftime("%Y-%m-%d")

    """
    function: get_recent_trading_day
    description: 获取最近一个交易日
    param {*} self
    return {*}
    """    
    def get_recent_trading_day(self) -> str:
        today = datetime.now().date()
        while True:
            if self.xshg.is_session(today):
                return today.strftime("%Y-%m-%d")
            today -= timedelta(days=1)

    @abc.abstractmethod
    def get_stock_day_his_hq(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取股票历史日线数据
        """
        return pd.DataFrame()

    @abc.abstractmethod
    def get_stock_week_his_hq(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取股票历史周线数据
        """
        return pd.DataFrame()

    @abc.abstractmethod
    def get_stock_month_his_hq(self, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取股票历史月线数据
        """
        return pd.DataFrame()
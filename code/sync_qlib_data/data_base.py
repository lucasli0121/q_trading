"""
Author: liguoqiang
Date: 2024-08-27 22:32:26
LastEditors: liguoqiang
LastEditTime: 2026-03-29 14:01:59
Description: 
"""
import abc
from datetime import datetime, timedelta
import json
import logging
import string
import pandas as pd
import akshare as ak
from pandas import json_normalize, read_json
from xmlrpc.client import boolean
from urllib3 import PoolManager
import exchange_calendars as xcals


class DataBase(metaclass=abc.ABCMeta):
    def __init__(self) -> None:
        self.xshg = xcals.get_calendar("XSHG")
        self.logger = logging.getLogger(__name__)

    """
    function: get_his_day_stocks
    description: 获取指定股票在指定日期范围内的日线历史数据
    param {*} self
    param {str} code 股票代码
    """
    def get_his_day_stocks(self, code: str, start_date: str, end_date: str) -> pd.DataFrame|None:
        return ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    """
    function: get_all_stock_codes
    description: 获取所有股票代码和名称
    param {*} self
    return {*}
    """        
    def get_all_stock_codes(self) -> pd.DataFrame|None:
        real_data = ak.stock_zh_a_spot_em()
        if real_data is None or real_data.empty:
            self.logger.error("get all stock codes failed")
            return None

        records = []
        for code, name in real_data[["代码", "名称"]].values:
            industry = ""
            try:
                stock_info_df = ak.stock_individual_info_em(symbol=code)
                if stock_info_df is not None and not stock_info_df.empty:
                    listing_date = stock_info_df.query("item == '上市时间'")["value"].values[0]
                    listing_date = pd.to_datetime(listing_date, format="%Y%m%d")
                    industry = stock_info_df.query("item == '行业'")["value"].values[0]
                else:
                    listing_date = None

            except Exception as e:
                self.logger.warning(f"获取 {code} 失败: {e}")
                listing_date = None

            records.append({
                "代码": code,
                "名称": name,
                "行业": industry,
                "上市时间": listing_date
            })
        return pd.DataFrame(records)
    """
    function: get_all_real_hq
    description: 获取所有股票的实时行情数据
    param {*} self
    return {*}
    """    
    def get_all_real_hq(self) -> pd.DataFrame|None:
        return ak.stock_zh_a_spot_em()
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
    """
    function: 
    description: 获取所有行业信息
    param {*} self
    return {*}
    """    
    def get_all_industry(self) -> pd.DataFrame|None:
        return ak.stock_board_industry_summary_ths()
    
    """
    function: get_all_industry_concept
    description: 获取所有行业概念信息(东方财经接口)
    param {*} self
    return {*}
    """    
    def get_all_industry_concept(self) -> pd.DataFrame|None:
        return ak.stock_board_industry_name_em()
    
    """
    function: get_industry_dayly_data
    description: 获取行业的日线历史数据(东方财经接口)
    param {*} self
    param {str} industry_code
    param {str} start_date
    param {str} end_date
    return {*}
    """    
    def get_industry_dayly_data(self, industry_code: str, start_date: str, end_date: str) -> pd.DataFrame|None:
        return ak.stock_board_industry_hist_em(symbol=industry_code, start_date=start_date, end_date=end_date)
    
    @abc.abstractmethod
    def create_all(self):
        pass

    def __call__(self, *args, **kwargs):
        self.create_all()
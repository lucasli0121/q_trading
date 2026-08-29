'''
Author: liguoqiang
Date: 2023-05-04 15:30:46
LastEditors: liguoqiang
LastEditTime: 2026-03-21 08:49:10
Description: with http request to sync data through rhserver api to qlib format data
include 3 function
    1. create calendars txt files which name is day.txt and day_future.txt if stock data frequency is day
    2. create instruments txt files which name is all.txt 上证指数.txt ... those files profix is index name
    3. create stock csv files which name like stock code.csv such as 000001.csv ...
    
'''

import csv
import logging
import os
import sys
import json
import time
from pandas import DataFrame
import pandas as pd
from urllib3 import PoolManager
from datetime import datetime, timedelta
from data_base import DataBase
import exchange_calendars as xcals
from concurrent.futures import ThreadPoolExecutor

from dump_bin import DumpDataAll

'''
description: 同步akshare数据到qlib数据格式
1. create calendars txt files which name is day.txt and day_future.txt if stock data frequency is day
2. create instruments txt files which name is all.txt 上证指数.txt ... those files profix is index name
3. create stock csv files which name like stock code.csv such as 000001.csv ...
 subclass from DataBase
'''
class SyncToQlib(DataBase):
    '''
    data_path: qlib数据存储路径
    start_date: 同步数据的开始日期
    '''
    def __init__(self, data_path: str, start_date: str) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.xshg = xcals.get_calendar("XSHG")
        self.data_path = data_path
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        # if data_path is not exist then create it
        if not os.path.exists(data_path):
            os.makedirs(data_path)
    '''
    description: create calendars files
    temporarily only support day frequency in the future we will also support weekly or monthly
    return {bool} True or False
    '''
    def create_calendars_files(self) -> bool:
        # figure out date from start_date to today into day.txt file
        calendar_path = self.data_path + "/calendars"
        if not os.path.exists(calendar_path):
            os.makedirs(calendar_path)
        day_file = calendar_path + "/day.txt"
        # open day.txt file with write mode
        try:
            with open(day_file, "w") as f:
                # get all date from start_date to today
                dates = self.get_calendars_dates(self.start_date, datetime.now().date())
                # write date into day.txt file
                f.write("\n".join(dates))
        except Exception as err:
            self.logger.error("create day.txt file failed: %s" % err)
            return False
        # figure out date from start_date to end of this year into day_future.txt file
        day_future_file = calendar_path + "/day_future.txt"
        # open day_future.txt file with write mode
        try:
            with open(day_future_file, "w") as f:
                # get all date from start_date to end of this year
                dates = self.get_calendars_dates(self.start_date, datetime(datetime.now().year, 12, 31).date())
                # write date into day_future.txt file
                f.write("\n".join(dates))
        except Exception as err:
            self.logger.error("create day_future.txt file failed: %s" % err)
            return False
        return True
    '''
    function: get_calendars_dates
    description:  figure out date from start_date to end date
    return [] a string list
    '''
    def get_calendars_dates(self, startdt, enddt):
        dates = []
        while startdt <= enddt:
            if self.xshg.is_session(startdt):
                dates.append(startdt.strftime("%Y-%m-%d"))
            # add 1day
            startdt = startdt + timedelta(days=1)
        return dates
    '''
    function: create_instrumens_files
    description: create instruments files which name is all.txt 上证指数.txt ... those files profix is index name
        this files content include stock index code, market_date and end_date
    return {bool} True or False
    '''
    def create_instruments_files(self):
        instruments_path = self.data_path + "/instruments"
        if not os.path.exists(instruments_path):
            os.makedirs(instruments_path)
        all_stock_df = self.get_all_stock_codes()
        if all_stock_df is None or len(all_stock_df) == 0:
            self.logger.error("get all stock codes failed")
            return False
        try:
            start = pd.Series(pd.Timestamp(self.start_date), index=all_stock_df.index)
            all_stock_df["上市时间"] = all_stock_df["上市时间"].where(
                all_stock_df["上市时间"] > start,
                start
            )
            trade_day = self.get_recent_trading_day()
            all_stock_df["最近交易日期"] = trade_day
            all_stock_df["代码"] = all_stock_df["代码"].astype(str)
            all_stock_df.to_csv(instruments_path + "/all.txt", sep="\t", columns=["代码", "上市时间", "最近交易日期"], header=False, index=False)
            #把行业作为代码追加到all.txt文件中
            df_unique = (
                all_stock_df
                .sort_values("上市时间", ascending=True)
                .drop_duplicates(subset=["行业"])
            )
            df_unique.to_csv(instruments_path + "/all.txt", sep="\t", mode='a', columns=["行业", "上市时间", "最近交易日期"], header=False, index=False)
        except Exception as err:
            self.logger.error("create all.txt file failed: %s" % err)
            return False
        try:
            for industry, df in all_stock_df.groupby("行业"):
                file_path = os.path.join(instruments_path, f"{industry}.txt")
                df.to_csv(file_path, sep="\t", columns=["代码", "上市时间", "最近交易日期"], header=False, index=False)
        except Exception as err:
            self.logger.error("create index file failed: %s" % err)
            return False
        return True
    '''
    function: create_stock_files
    description: create stock files which name like stock code.csv such as 000001.csv ...
        this files content include stock code, date, open, high, low, close, volume, amount
    return {bool} True or False
    '''
    def create_stock_files(self):
        os.makedirs(self.data_path, exist_ok=True)
        try:
            all_stock_df = self.get_all_stock_codes()
            if all_stock_df is None or len(all_stock_df) == 0:
                self.logger.error("get all stock codes failed")
                return False
            recent_day = self.get_recent_trading_day().replace("-", "")
            start_date = self.start_date.strftime("%Y%m%d")
            codes = all_stock_df["代码"].values
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = []
                for code in codes:
                    future = executor.submit(
                        self.process_single_stock,
                        code,
                        start_date,
                        recent_day
                    )
                    futures.append(future)
                for future in futures:
                    future.result()
        except Exception as err:
            self.logger.error("create stock file failed: %s" % err)
            return False
        # 获取行业历史行情数据并保存到文件
        try:
            all_industry_df = self.get_all_industry()
            if all_industry_df is None or len(all_industry_df) == 0:
                self.logger.error("get all industry failed")
                return False
            recent_day = self.get_recent_trading_day().replace("-", "")
            start_date = self.start_date.strftime("%Y%m%d")
            industrys = all_industry_df["板块"].values
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = []
                for industry in industrys:
                    future = executor.submit(
                        self.process_single_industry,
                        industry,
                        start_date,
                        recent_day
                    )
                    futures.append(future)
                for future in futures:
                    future.result()
        except Exception as err:
            self.logger.error("convert to qlib format failed: %s" % err)
            return False
        return True

    def process_single_stock(self, code, start_date, recent_day):
        try:
            his_stocks = self.get_his_day_stocks(code, start_date, recent_day)
            if his_stocks is None or len(his_stocks) == 0:
                return
            his_stocks = his_stocks[["股票代码", "日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "涨跌额", "成交量", "成交额"]]
            his_stocks["factor"] = 1
            his_stocks = his_stocks.rename(columns={
                "股票代码": "symbol",
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "涨跌幅": "change_pct",
                "涨跌额": "change_amount",
                "成交量": "volume",
                "成交额": "amount"
            })
            his_stocks.columns = ["symbol", "date", "open", "high", "low", "close", "change_pct", "change_amount", "volume", "amount", "factor"]
            # 确保日期格式正确
            his_stocks["date"] = pd.to_datetime(his_stocks["date"])
            file_path = os.path.join(self.data_path, f"{code}.csv")
            his_stocks.to_csv(file_path,
                header=["symbol", "date", "open", "high", "low", "close", "change_pct", "change_amount", "volume", "amount", "factor"],
                index=False,
                mode="w",
                float_format="%.2f",
                date_format="%Y-%m-%d"
            )
        except Exception as e:
            self.logger.warning(f"{code} failed: {e}")
    
    def process_single_industry(self, industry, start_date, recent_day):
        try:
            his_stocks = self.get_industry_dayly_data(industry, start_date, recent_day)
            if his_stocks is None or len(his_stocks) == 0:
                return
            his_stocks["板块名称"] = industry
            his_stocks = his_stocks[["板块名称", "日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "涨跌额", "成交量", "成交额"]]
            his_stocks["factor"] = 1
            his_stocks = his_stocks.rename(columns={
                "板块名称": "symbol",
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "涨跌幅": "change_pct",
                "涨跌额": "change_amount",
                "成交量": "volume",
                "成交额": "amount"
            })
            his_stocks.columns = ["symbol", "date", "open", "high", "low", "close", "change_pct", "change_amount", "volume", "amount", "factor"]
            # 确保日期格式正确
            his_stocks["date"] = pd.to_datetime(his_stocks["date"])
            file_path = os.path.join(self.data_path, f"{industry}.csv")
            his_stocks.to_csv(file_path,
                header=["symbol", "date", "open", "high", "low", "close", "change_pct", "change_amount", "volume", "amount", "factor"],
                index=False,
                mode="w",
                float_format="%.2f",
                date_format="%Y-%m-%d"
            )
        except Exception as e:
            self.logger.warning(f"{industry} failed: {e}")
    def convert_to_qlib_format(self):
        data_path = self.data_path
        qlib_path = self.data_path
        include_fields = "date, open, high, low, close, change_pct, change_amount, volume, amount, factor"
        date_field_name = "date"
        dump_all = DumpDataAll(
            data_path=data_path,
            qlib_dir=qlib_path,
            include_fields=include_fields,
            date_field_name=date_field_name
        )
        dump_all()

    """
    function: create_all
    description: create all data including calendars files, instruments files and stock files
    param {*} self
    return {*}
    """    
    def create_all(self):
        logger = logging.getLogger(__name__)
        logger.info("create calendars files")
        if not self.create_calendars_files():
            logger.error("create calendars files failed")
            return False
        logger.info("create instruments files")
        if not self.create_instruments_files():
            logger.error("create instruments files failed")
            return False
        logger.info("create stock files")
        if not self.create_stock_files():
            logger.error("create stock files failed")
            return False
        logger.info("convert to qlib format")
        self.convert_to_qlib_format()
        logger.info("create all files successfully")
        return True
'''
Author: liguoqiang
Date: 2022-06-16 20:50:39
LastEditors: liguoqiang
LastEditTime: 2024-07-21 16:14:26
Description: 股票行情基类
'''
import abc

from datetime import datetime
from enum import Enum
import logging
import re
import akshare as ak
import pandas as pd
from pandas import DataFrame

class AkPeriodEnum(Enum):
    DailyPeriod = "daily"
    WeekPeriod = "weekly"
    MonthPeriod = "monthly"

class AkAdjustEnum(Enum):
    QfqAdjust = "qfq"
    HfqAdjust = "hfq"

class AkStockBase(metaclass=abc.ABCMeta):
    def __init__(self, **kargs) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)

    def is_yyyy_mm_dd(self, s: str) -> bool:
        # 判断格式为 4数字-2数字-2数字
        return re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) is not None

    def to_yyyymmdd(self, date_str: str) -> str:
        # 尝试按 YYYY-MM-DD 解析输入字符串
        try:
            if self.is_yyyy_mm_dd(date_str):
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                return dt.strftime("%Y%m%d")
            return date_str.replace('-', '')
        except ValueError:
            return date_str
    
    """
    function: get_stocks_his_hq
    description: 获取指定股票在指定日期范围内的日线历史数据
    param {*} self
    param {str} code 股票代码
    param {str} start_date 开始日期
    param {str} end_date 结束日期
    param {str} adjust 调整方式
    返回数据格式
        名称	类型	描述
        日期	object	交易日
        股票代码	object	不带市场标识的股票代码
        开盘	float64	开盘价
        收盘	float64	收盘价
        最高	float64	最高价
        最低	float64	最低价
        成交量	int64	注意单位: 手
        成交额	float64	注意单位: 元
        振幅	float64	注意单位: %
        涨跌幅	float64	注意单位: %
        涨跌额	float64	注意单位: 元
        换手率	float64	注意单位: %
    """
    def get_stocks_his_hq(self, code: str, start_date: str, end_date: str, period: str = "daily", adjust: str = "qfq") -> pd.DataFrame|None:
        try:
            start_date = self.to_yyyymmdd(start_date)
            end_date = self.to_yyyymmdd(end_date)
            return ak.stock_zh_a_hist(symbol=code, period=period, start_date=start_date, end_date=end_date, adjust=adjust)
        except Exception as e:
            self.logger.error(f"获取 {code} 日线历史数据失败: {e}")
            return None
    """
    function: get_stock_base_info
    description: 获取所有股票代码和名称
    param {*} self
    return {*}
        0    最新               7.05
        1  股票代码             000002
        2  股票简称            万  科Ａ
        3   总股本       11930709471.0
        4   流通股        9716935865.0
        5   总市值  84111501770.550003
        6  流通市值      68504397848.25
        7    行业              房地产开发
        8  上市时间            19910129
    说明: 该接口获取个股基本信息，但是经常访问不了，所以暂时不使用了
    """        
    def get_stock_base_info(self, stock_code: str) -> DataFrame|None:
        try:
            return ak.stock_individual_info_em(symbol=stock_code)
        except Exception as e:
            self.logger.warning(f"获取 {stock_code} 基本信息失败: {e}")
            return None
    
    """
    function: get_sh_stock_list
    description: 获取上证股票列表
    param {*} self
    param {str} symbol 取值范围: 主板A股、科创板
    return {*}
        证券代码	object	-
        证券简称	object	-
        公司全称	object	-
        上市日期	object
    """
    def get_sh_stock_list(self, symbol: str = "主板A股") -> pd.DataFrame|None:
        try:
            return ak.stock_info_sh_name_code(symbol=symbol)
        except Exception as e:
            self.logger.error(f"获取上证股票列表失败: {e}")
            return None
    """
    function: get_sz_stock_list
    description: 获取深证股票列表
    param {*} self
    param {str} symbol 取值范围: A股列表
    return {*}
        板块	object	-
        A股代码	object	-
        A股简称	object	-
        A股上市日期	object	-
        A股总股本	object	-
        A股流通股本	object	-
        所属行业	object
    """
    def get_sz_stock_list(self, symbol: str = "A股列表") -> pd.DataFrame|None:
        try:
            return ak.stock_info_sz_name_code(symbol=symbol)
        except Exception as e:
            self.logger.error(f"获取深证股票列表失败: {e}")
            return None
    """
    function: get_bj_stock_list
    description: 获取北交所股票列表
    param {*} self
    return {*}
        证券代码	object	-
        证券简称	object	-
        总股本	int64	注意单位: 股
        流通股本	int64	注意单位: 股
        上市日期	object	-
        所属行业	object	-
        地区	object	-
        报告日期	object
    """
    def get_bj_stock_list(self) -> pd.DataFrame|None:
        try:
            return ak.stock_info_bj_name_code()
        except Exception as e:
            self.logger.error(f"获取北交所股票列表失败: {e}")
            return None
    """
    function: get_all_real_hq
    description: 获取所有股票的实时行情数据
    param {*} self
    return {*}
        输出参数
        名称	类型	描述
        序号	int64	-
        代码	object	-
        名称	object	-
        最新价	float64	-
        涨跌幅	float64	注意单位: %
        涨跌额	float64	-
        成交量	float64	注意单位: 手
        成交额	float64	注意单位: 元
        振幅	float64	注意单位: %
        最高	float64	-
        最低	float64	-
        今开	float64	-
        昨收	float64	-
        量比	float64	-
        换手率	float64	注意单位: %
        市盈率-动态	float64	-
        市净率	float64	-
        总市值	float64	注意单位: 元
        流通市值	float64	注意单位: 元
        涨速	float64	-
        5分钟涨跌	float64	注意单位: %
        60日涨跌幅	float64	注意单位: %
        年初至今涨跌幅	float64	注意单位: %
    """    
    def get_all_real_hq(self) -> pd.DataFrame|None:
        df: DataFrame|None = None
        try:
            df = ak.stock_zh_a_spot_em()
        except Exception as e:
            self.logger.error(f"获取所有股票实时行情失败: {e}")
        return df

    """
    function: 
    description: 获取所有行业信息
    param {*} self
    return {*}
    返回数据格式
        名称	类型	描述
        序号	int64	-
        板块	object	-
        涨跌幅	object	注意单位: %
        总成交量	float64	注意单位: 万手
        总成交额	float64	注意单位: 亿元
        净流入	float64	注意单位: 亿元
        上涨家数	float64	-
        下跌家数	float64	-
        均价	float64	-
        领涨股	str	-
        领涨股-最新价	object	-
        领涨股-涨跌幅	object	注意单位: %
    """    
    def get_all_industry(self) -> pd.DataFrame|None:
        try:
            return ak.stock_board_industry_summary_ths()
        except Exception as e:
            self.logger.error(f"获取所有行业信息失败: {e}")
            return None
    
    """
    function: get_all_industry_codes
    description: 获取所有行业名称及代码
    param {*} self
    return {*}
    返回数据格式
       name    code
0    阿尔茨海默概念  308614
1      AI PC  309121
2       AI手机  309120
3       AI语料  309126
4     阿里巴巴概念  301558
    """
    def get_all_industry_codes(self) -> pd.DataFrame|None:
        try:
            return ak.stock_board_industry_name_ths()
        except Exception as e:
            self.logger.error(f"获取所有行业代码失败: {e}")
            return None
    """
    function: get_industry_cons_em
    description: 获取行业成分股信息
    param {*} self
    param {str} industry_code
    return {*}
        名称	类型	描述
        序号	int64	-
        代码	object	-
        名称	object	-
        最新价	float64	-
        涨跌幅	float64	注意单位: %
        涨跌额	float64	-
        成交量	float64	注意单位: 手
        成交额	float64	-
        振幅	float64	注意单位: %
        最高	float64	-
        最低	float64	-
        今开	float64	-
        昨收	float64	-
        换手率	float64	注意单位: %
        市盈率-动态	float64	-
        市净率	float64	-
    """
    def get_industry_cons_em(self, industry_code: str) -> pd.DataFrame|None:
        try:
            return ak.stock_board_industry_cons_em(symbol=industry_code)
        except Exception as e:
            self.logger.error(f"获取行业 {industry_code} 信息失败: {e}")
            return None
    """
    function: get_all_industry_concept
    description: 获取所有行业概念信息(东方财经接口)
        单次返回当前时刻所有行业板的实时行情数据
    param {*} self
    return {*}
    返回数据格式
        名称	类型	描述
        排名	int64	-
        板块名称	object	-
        板块代码	object	-
        最新价	float64	-
        涨跌额	float64	-
        涨跌幅	float64	注意单位：%
        总市值	int64	-
        换手率	float64	注意单位：%
        上涨家数	int64	-
        下跌家数	int64	-
        领涨股票	object	-
        领涨股票-涨跌幅	float64	注意单位：%
    """    
    def get_all_industry_concept(self) -> pd.DataFrame|None:
        try:
            return ak.stock_board_industry_name_em()
        except Exception as e:
            self.logger.error(f"获取所有行业实时行情失败: {e}")
            return None
    
    """
    function: get_industry_dayly_data
    description: 获取行业的日线历史数据(东方财经接口)
    param {*} self
    param {str} industry_code
    param {str} start_date
    param {str} end_date
    param {str} period # 取值范围: 日K、周K、月K
    param {str} adjust # 取值范围: qfq、hfq
    return {*}
    返回数据格式
        名称	类型	描述
        日期	object	-
        开盘	float64	-
        收盘	float64	-
        最高	float64	-
        最低	float64	-
        涨跌幅	float64	注意单位: %
        涨跌额	float64	-
        成交量	int64	-
        成交额	float64	-
        振幅	float64	注意单位: %
        换手率	float64	注意单位: %
    """    
    def get_industry_dayly_data(self, industry_code: str, start_date: str, end_date: str, period: str = "日K", adjust: str = "qfq") -> pd.DataFrame|None:
        try:
            return ak.stock_board_industry_hist_em(symbol=industry_code, start_date=start_date, end_date=end_date, period=period, adjust=adjust)
        except Exception as e:
            self.logger.error(f"获取 {industry_code} 日线历史数据失败: {e}")
            return None
    """
    function: get_stock_valuation
    description: 获取个股估值信息
    param {*} self
    param {str} stock_code
    return {*}
    返回数据格式
        名称	类型	描述
        数据日期	object	-
        当日收盘价	float64	注意单位: 元
        当日涨跌幅	float64	注意单位: %
        总市值	float64	注意单位: 元
        流通市值	float64	注意单位: 元
        总股本	float64	注意单位: 股
        流通股本	float64	-
        PE(TTM)	float64	-
        PE(静)	float64	-
        市净率	float64	-
        PEG值	float64	-
        市现率	float64	-
        市销率	float64	
    """
    def get_stock_valuation(self, stock_code: str) -> pd.DataFrame|None:
        try:
            return ak.stock_value_em(symbol=stock_code)
        except Exception as e:
            self.logger.error(f"获取 {stock_code} 估值信息失败: {e}")
            return None

    """
    function: get_stock_financial
    description: 获取个股财务摘要信息
    param {*} self
    param {str} stock_code
    return {*}
    返回数据格式
         选项        指标      20220930  ...      20020630      20011231      20001231
    0   常用指标     归母净利润 -6.271849e+08  ...  1.365679e+08  2.967902e+08  1.967577e+08
    1   常用指标     营业总收入  3.307660e+09  ...  4.381153e+08  8.515877e+08  7.833500e+08
    2   常用指标      营业成本  4.212766e+09  ...  2.664150e+08  5.317826e+08  4.654766e+08
    3   常用指标       净利润 -6.321978e+08  ...  1.406112e+08  3.009773e+08  2.019293e+08
    4   常用指标     扣非净利润 -6.532030e+08  ...  1.364059e+08  1.985243e+08  1.967577e+08
    """
    def get_stock_financial(self, stock_code: str) -> pd.DataFrame|None:
        try:
            return ak.stock_financial_abstract(symbol=stock_code)
        except Exception as e:
            self.logger.error(f"获取 {stock_code} 财务摘要信息失败: {e}")
            return None
        
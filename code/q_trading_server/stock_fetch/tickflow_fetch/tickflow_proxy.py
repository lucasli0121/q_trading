
import asyncio
from configparser import ConfigParser
from datetime import datetime
import logging
import re
from typing import Any, cast

import pandas as pd
from dao.industry_base_info_dao import IndustryBaseInfoDao
from dao.stock_info_dao import StockInfoDao
from db.mongo.mongo_industry_impl import MongoIndustryImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from stock_fetch.tickflow_fetch import TickFlowBase
import exchange_calendars as xcals

class TickFlowProxy(TickFlowBase):
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        cp = ConfigParser()
        cp.read("cfg/stock.cfg")
        api_token = cp.get("tickflow", "api_token")
        server_url = cp.get("tickflow", "server_url")
        super().__init__(api_token=api_token, server_url=server_url)
        self.real_time_skip = 0
        self.real_time_limit = int(cp.get("tickflow", "real_time_limit", fallback=5))
        self.xshg = xcals.get_calendar("XSHG")
        

    """
    function: stock_real_hq_task
    description: get real time hq data for monitor stock list
    return {*}
    """
    def stock_real_hq_sync_task(self) -> None|pd.DataFrame:
        """实时行情同步任务 — 从股票池中获取待监控股票代码，分页查询 tickflow 实时行情。

        1. 检查当前是否为交易时段
        2. 从股票池 stock_pool_stock_tbl 获取去重后的股票代码列表
        3. 根据 real_time_limit 分页获取，每次取一批调用 tickflow 实时行情接口
        4. 当所有股票都获取完毕后，skip 归零重新轮询

        :return: 实时行情 DataFrame，非交易时段或无数据时返回 None
        """
        if not self.xshg.is_session(datetime.now().date()):
            return None
        now = datetime.now()
        if now.hour < 9 \
            or (now.hour == 9 and now.minute < 30) \
            or (now.hour == 11 and now.minute > 30) \
            or (now.hour > 11 and now.hour < 13) \
            or now.hour > 15:
            return None
        pool_impl = MongoStockPoolImpl()
        res, stocks_count = pool_impl.get_all_pool_stocks_count()
        if not res or stocks_count <= 0:
            self.logger.info("股票池中无股票，跳过实时行情同步")
            return None
        """
        1. 从股票池获取去重后的股票代码列表，分页获取，每次取 real_time_limit 条
        因为 tickflow 的接口一次只能获取有限的股票数据，所以需要分页获取，直到获取完所有股票
        2. 如果数据为空或获取失败，返回 None
        3. 调用 do_stock_real_hq 方法获取实时行情数据，并返回 DataFrame
        """
        res, pool_stock_list = pool_impl.query_all_pool_stocks(skip=self.real_time_skip, limit=self.real_time_limit)
        if not res or pool_stock_list is None or len(pool_stock_list) <= 0:
            self.logger.info("股票池股票列表为空，跳过实时行情同步")
            return None
        self.real_time_skip = self.real_time_skip + self.real_time_limit
        if self.real_time_skip >= stocks_count:
            self.real_time_skip = 0
        return self.do_stock_real_hq(stock_list=pool_stock_list)
            
    
    def do_stock_real_hq(self, stock_list: list[Any]) -> pd.DataFrame:
        params = [stock_info["code"] for stock_info in stock_list if "code" in stock_info]
        res, real_hq_values = self.get_real_time_hq(params)
        if not res or real_hq_values is None:
            self.logger.error(f"stock_real_hq_task_async failed, params:{params}, response:{real_hq_values}")
            return pd.DataFrame()
        if not isinstance(real_hq_values, list):
            self.logger.error(
                "stock_real_hq_task_async 返回数据类型异常, params:%s, type:%s, response:%s",
                params, type(real_hq_values).__name__, real_hq_values,
            )
            return pd.DataFrame()

        #用字典解包，把 ext 的内容合并到上层，并且把 ext 字段删除掉
        real_hq_df = pd.DataFrame([{**item, **item.pop('ext')} for item in real_hq_values])
        # 把字段重命名一下，和数据库字段保持一致
        real_hq_df.rename(
            columns={
                "symbol": "code", 
                "last_price": "price",
                "prev_close":"preclose",
                "timestamp": "create_time",
                "change_pct": "change_percent",
                "amplitude": "amp",
                "turnover_rate": "turnover",
            },
            inplace=True
        )
        if "ext" in real_hq_df:
            del real_hq_df["ext"]
        timestamp = real_hq_df['create_time']
        real_hq_df["create_time"] = self.milliseconds_utc_to_date_str(timestamp)
        return real_hq_df
        

    """
    function: industry_base_sync_task
    description: 同步行业信息以及成分股票到数据库
    param {*} self
    return {*}
    """
    def industry_base_sync_task(self):
        # 获取所有行业信息
        res, industry_base_list = self.get_industry_base_info_list()
        if not res or industry_base_list is None or len(industry_base_list) <= 0:
            self.logger.info("industry_base_list count is 0, return")
            return
        industry_db = MongoIndustryImpl()
        for industry_base in industry_base_list:
            industry_base_dao = IndustryBaseInfoDao()
            industry_base_dao.tick_id = industry_base.get("id", "")
            name = industry_base.get("name", "")
            pattern = r"^[^\u4e00-\u9fff]*([\u4e00-\u9fff].*)$"
            match = re.match(pattern, name)
            if match:
                industry_base_dao.name = match.group(1)
            industry_db.insert_or_update_industry_base_info(industry_base_dao.to_db())
            # 获取A股成分股票列表，并保存到数据库
            res, industry_stock_dict = self.get_one_industry_stock_list(industry_base_dao.tick_id)
            if not res or industry_stock_dict is None or len(industry_stock_dict) <= 0:
                self.logger.info(f"industry_stock_dict count is 0 for industry {industry_base_dao.name}, continue")
                continue
            symbols = industry_stock_dict.get("symbols", [])
            stock_db = MongoStockInfoImpl()
            for stock_code in symbols:
                if stock_code is None or len(stock_code) <= 0:
                    continue
                # 保存股票代码
                if not self.is_valid_stock_code(stock_code):
                    self.logger.info(f"同步的股票代码格式不正确 {stock_code}，暂时不同步指数代码, continue")
                    continue
                stock_info_dao = StockInfoDao()
                res, values = stock_db.query_by_codes([stock_code])
                if res and values and len(values) > 0:
                    stock_info_dao.from_db(values[0])
                if not hasattr(stock_info_dao, "industry"):
                    stock_info_dao.industry = ""
                stock_info_dao.code = stock_code
                if stock_info_dao.industry is None or len(stock_info_dao.industry) <= 0:
                    stock_info_dao.industry = industry_base_dao.name
                else:
                    if industry_base_dao.name not in stock_info_dao.industry:
                        stock_info_dao.industry = stock_info_dao.industry + "," + industry_base_dao.name
                stock_db.insert_or_update_stock_info(stock_info_dao.to_db())

    """
    function: industry_hq_sync_task
    description: 同步行业实时行情
    param {*} self
    return {*}
    """
    def industry_hq_sync_task(self):
        pass


    """
    function: get_stocks_his_hq
    description: 获取股票的历史行情数据，并转换成StockHisHqDao数据
    param {*} self
    return {*}
    """
    @staticmethod
    def _guess_exchange_suffix(code: str) -> str:
        """根据纯数字股票代码推断交易所后缀。

        规则：
        - 60xxxx / 68xxxx → 上证 → .SH
        - 00xxxx / 30xxxx → 深证 → .SZ
        - 4xxxxx / 8xxxxx → 北交所 → .BJ
        - 其他 → 原样返回
        """
        if not re.match(r"^\d{6}$", code):
            return code
        prefix2 = code[:2]
        prefix1 = code[:1]
        if prefix2 in ("60", "68"):
            return code + ".SH"
        elif prefix2 in ("00", "30"):
            return code + ".SZ"
        elif prefix1 in ("4", "8"):
            return code + ".BJ"
        return code

    def get_stocks_his_hq(self, symbol: str, start_date: str, end_date: str, period: str = "1d", adjust: str = "forward") -> pd.DataFrame:
        # 纯数字代码自动补全交易所后缀
        if re.match(r"^\d+$", symbol):
            symbol = self._guess_exchange_suffix(symbol)
        if not self.is_valid_stock_code(symbol):
            db_impl = MongoStockInfoImpl()
            res, stocks_list = db_impl.query_stock_info(symbol)
            if res and stocks_list and len(stocks_list) > 0:
                symbol = stocks_list[0]["code"]
        res, result_values = super().query_stock_kline(
            symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust
        )
        # 2. 如果数据为空或获取失败，返回一个空的 DataFrame (包含预期列名)
        if not res or result_values is None:
            return pd.DataFrame()
    
         # 3. 构建 DataFrame
        # 直接将原始字典数据传入 pd.DataFrame
        result_dict: dict[str, Any] = cast(dict[str, Any], result_values)
        new_result_dict = {}
        for key in ['amount','close','high','low','open','volume','prev_close','open_interest','timestamp']:
            if key in result_dict:
                new_result_dict[key] = result_dict.get(key, [])

        df = pd.DataFrame(new_result_dict)
         # 4. 数据处理：转换时间格式
        if not df.empty and 'timestamp' in df.columns:
            df['create_time'] = df['timestamp'].apply(self.milliseconds_utc_to_date_str)
            # 如果不需要原始时间戳列，可以删除
            df = df.drop(columns=['timestamp'])
        return df

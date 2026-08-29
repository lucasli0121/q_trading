"""
Author: liguoqiang
Date: 2022-06-16 20:54:12
LastEditors: liguoqiang
LastEditTime: 2023-05-09 19:26:14
Description: 此类主要实现可以定时或者实时被调度执行的功能
    用于获取和股票相关的实时的数据，比如行情的分时数据等
    然后把获取到的数据通过db接口保存
    db接口是myDb的实例，具体是否保存或者转发由myDb实例决定
"""

import asyncio
from datetime import datetime, date, timedelta
from typing import Any, Hashable, Mapping, cast
import pandas as pd
import logging
from dao.stock_info_dao import StockInfoDao
from dao.company_valuation import CompanyValuationDao
from dao.company_finance import CompanyFinanceDao
from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from db.mongo.mongo_company_finance_impl import MongoCompanyFinanceImpl
from db.mongo.mongo_industry_impl import MongoIndustryImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from utils.tools import to_float
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from stock_fetch.akshare_fetch import AkStockBase
import exchange_calendars as xcals

# AkStockHq实现了akshare的股票接口，AkStockProxy继承了AkStockHq
class AkStockProxy(AkStockBase):
    # 数据操作对象
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        super().__init__()
        self.xshg = xcals.get_calendar("XSHG")


    """
    function: stock_real_hq_task
    description: 获取实时行情，给任务调度
    param {*} self
    return {*}
        stockDf DataFrame
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

    def stock_real_hq_sync_task(self) -> None|pd.DataFrame:
        if not self.xshg.is_session(datetime.now().date()):
            return None
        now = datetime.now()
        if now.hour < 9 \
            or (now.hour == 9 and now.minute < 30) \
            or (now.hour == 11 and now.minute > 30) \
            or (now.hour > 11 and now.hour < 13) \
            or now.hour > 15:
            return None
        real_data_pd = self.get_all_real_hq()
        if real_data_pd is not None:
            createtm = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取数据的时间
            return self.do_real_stock_df(createtm, real_data_pd)
        return None

    '''
    function: do_real_stock_df
    description: 整理分时股票数据,把从akshare获取的实时数据转义,
        增加数据库字段,然后插入到数据库
    param {*} self
    param {*} createtm
    param {*} real_data_pd
    return {*}
    '''    
    def do_real_stock_df(self, createtm, real_data_pd: pd.DataFrame) -> pd.DataFrame:
        if "序号" in real_data_pd.columns:
            del real_data_pd["序号"]
        # 重新设置列的顺序，增加create_time列
        real_data_pd.rename(
            columns={
                "代码": "code",
                "名称": "name",
                "最新价": "price",
                "涨跌幅": "change_percent",
                "涨跌额": "change_amount",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amp",
                "最高": "high",
                "最低": "low",
                "今开": "open",
                "昨收": "preclose",
                "量比": "qrr",
                "换手率": "turnover",
                "市盈率-动态": "pe",
                "市净率": "pb",
                "总市值": "cap",
                "流通市值": "fcap",
                "涨速": "accer",
                "5分钟涨跌": "five_minpchg",
                "60日涨跌幅": "sixty_day_pchg",
                "年初至今涨跌幅": "ytd_pchg"
            },
            inplace=True)
        real_data_pd["create_time"] = createtm
        return real_data_pd

    """
    function: industry_base_sync_task
    description: 同步行业信息到数据库，行业代码和名称
    param {*} self
    return {*}
    """
    def industry_base_sync_task(self):
        # 获取行业代码及名称
        industry_codes_df = self.get_all_industry_codes()
        if industry_codes_df is not None and not industry_codes_df.empty:
            if '序号' in industry_codes_df:
                del industry_codes_df['序号']
            db_impl = MongoIndustryImpl()
            # 先保存行业基础信息到数据库
            records = industry_codes_df.to_dict(orient="records")
            db_impl.bulk_upsert_industry_base_info(records=records)

    """
    function: industry_hq_sync_task
    description: 同步行业实时行情信息到数据库
    param {*} self
    return {*}
    """
    def industry_hq_sync_task(self):
        if not self.xshg.is_session(datetime.now().date()):
            return
        now = datetime.now()
        if now.hour < 9 \
            or (now.hour == 9 and now.minute < 30) \
            or (now.hour == 11 and now.minute > 30) \
            or (now.hour > 11 and now.hour < 13) \
            or now.hour > 15:
            return
        # 获取所有行业信息（此信息缺少行业代码)
        industry_df = self.get_all_industry()
        # 获取行业代码
        industry_codes_df = self.get_all_industry_codes()
        if industry_df is not None and not industry_df.empty and industry_codes_df is not None and not industry_codes_df.empty:
            # 合并两个dataframe，把行业代码合并到行业信息中
            industry_df = industry_df.merge(
                industry_codes_df[["name", "code"]].rename(columns={"name": "板块"}),
                on="板块",
                how="left",
            )
            if '序号' in industry_df.columns:
                del industry_df['序号']
            db_impl = MongoIndustryImpl()

            industry_df.rename(
                columns={
                    "code": "code",
                    "板块": "name",
                    "涨跌幅": "change_percent",
                    "总成交量": "volume",
                    "总成交额": "amount",
                    "净流入": "net_inflow",
                    "上涨家数": "up_count",
                    "下跌家数": "down_count",
                    "均价": "avg_price",
                    "领涨股": "leading_stock",
                    "领涨股-最新价": "leading_price",
                    "领涨股-涨跌幅": "leading_change_percent"
                },
                inplace=True
            )
            update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            industry_df["update_time"] = update_time
            records = industry_df.to_dict(orient="records")
            db_impl.bulk_upsert_industry_info(records=records)
    
    """
    function: sync_all_stocks_base_info
    description: 同步所有股票基本信息到数据库
    param {*} self
    return {*}
    """
    def sync_all_stocks_base_info(self):
        db_impl = MongoStockInfoImpl()
        res, stocks_list = db_impl.query_all_stock_info()
        if not res or stocks_list is None or len(stocks_list) <= 0:
            return
        asyncio.run(self.do_async_all_stocks_base_info(stocks_list=stocks_list))
            
    async def do_async_all_stocks_base_info(self, stocks_list: list[Any]):
        db_impl = MongoStockInfoImpl()
        for stock_data in stocks_list:
            stock_dao = StockInfoDao()
            stock_dao.from_db(stock_data)
            code = stock_dao.code
            pure_code = db_impl.normalize_code(code)
            stock_info_df = self.get_stock_base_info(pure_code)
            if stock_info_df is not None:
                stock_dict = dict(zip(stock_info_df['item'], stock_info_df['value']))
                stock_dao.name = stock_dict["股票简称"]
                stock_dao.industry = stock_dict["行业"]
                stock_dao.list_date = stock_dict["上市时间"]
                db_impl.insert_or_update_stock_info(stock_dao.to_db())
            await asyncio.sleep(0.2)

    """
    function: sync_all_stock_lists
    description: 获取上证、深证、北交所的股票列表，转换为 `StockInfoDao` 格式并保存到数据库。
    return: dict 表示每个市场的同步结果
    """
    def sync_all_stock_lists(self) -> dict[str, bool]:
        db_impl = MongoStockInfoImpl()
        results = {"sh": False, "sh_kj": False, "sz": False, "bj": False}

        def _date_to_str(v: object) -> str:
            """把可能的 date/datetime 转为 YYYY-MM-DD 字符串；其他值转 str。"""
            if v is None:
                return ""
            try:
                if isinstance(v, date):
                    return v.strftime("%Y-%m-%d")
            except Exception:
                pass
            return str(v)

        # 上证
        try:
            sh_df = self.get_sh_stock_list()
            if sh_df is not None and not sh_df.empty:
                records = []
                for _, row in sh_df.iterrows():
                    code = str(row.get('证券代码', row.get('代码', ''))).strip()
                    board = row.get('板块', '上证')
                    list_date = row.get('上市日期', row.get('上市时间', ''))
                    name = row.get('证券简称', row.get('证券全称', row.get('公司简称', '')))
                    full_name = row.get('公司全称', '')
                    industry = row.get('所属行业', '')
                    rec = {
                        "code": code,
                        "name": name,
                        "full_name": full_name,
                        "board": board,
                        "list_date": _date_to_str(list_date),
                        "industry": industry,
                    }
                    res, values = db_impl.query_by_codes([code])
                    org_stock_info = values[0] if res and values and len(values) > 0 else None
                    if org_stock_info is not None:
                        if org_stock_info.get("name", "") == "":
                            org_stock_info["name"] = name
                        if org_stock_info.get("full_name", "") == "":
                            org_stock_info["full_name"] = full_name
                        if org_stock_info.get("board", "") == "":
                            org_stock_info["board"] = board
                        if org_stock_info.get("list_date", "") == "":
                            org_stock_info["list_date"] = _date_to_str(list_date)
                        if org_stock_info.get("industry", "") == "":
                            org_stock_info["industry"] = industry
                        elif industry != "" and industry not in org_stock_info.get("industry", ""):
                            org_stock_info["industry"] += f",{industry}"
                        rec = org_stock_info.copy()
                    records.append(rec)
                if records:
                    results['sh'] = db_impl.bulk_upsert_stock_info(records)
        except Exception as e:
            self.logger.error(f"同步上证列表失败: {e}")

        # 科创
        try:
            sh_df = self.get_sh_stock_list(symbol="科创板")
            if sh_df is not None and not sh_df.empty:
                records = []
                for _, row in sh_df.iterrows():
                    code = str(row.get('证券代码', row.get('代码', ''))).strip()
                    board = row.get('板块', '科创板')
                    raw_list_date = row.get('上市日期', row.get('上市时间', ''))
                    list_date = _date_to_str(raw_list_date)
                    name = row.get('证券简称', row.get('证券全称', row.get('公司简称', '')))
                    full_name = row.get('公司全称', '')
                    industry = row.get('所属行业', '')
                    rec = {
                        "code": code,
                        "name": name,
                        "full_name": full_name,
                        "board": board,
                        "list_date": list_date,
                        "industry": industry,
                    }
                    res, values = db_impl.query_by_codes([code])
                    org_stock_info = values[0] if res and values and len(values) > 0 else None
                    if org_stock_info is not None:
                        if org_stock_info.get("name", "") == "":
                            org_stock_info["name"] = name
                        if org_stock_info.get("full_name", "") == "":
                            org_stock_info["full_name"] = full_name
                        if org_stock_info.get("board", "") == "":
                            org_stock_info["board"] = board
                        if org_stock_info.get("list_date", "") == "":
                            org_stock_info["list_date"] = list_date
                        if org_stock_info.get("industry", "") == "":
                            org_stock_info["industry"] = industry
                        elif industry != "" and industry not in org_stock_info.get("industry", ""):
                            org_stock_info["industry"] += f",{industry}"
                        rec = org_stock_info.copy()
                    records.append(rec)
                if records:
                    results['sh_kj'] = db_impl.bulk_upsert_stock_info(records)
        except Exception as e:
            self.logger.error(f"同步科创板列表失败: {e}")


        # 深证
        try:
            sz_df = self.get_sz_stock_list()
            if sz_df is not None and not sz_df.empty:
                records = []
                for _, row in sz_df.iterrows():
                    code = row.get('A股代码', row.get('代码', '')).strip()
                    board = "深证" if row.get('板块', '') == '主板' else row.get('板块', '')
                    raw_list_date = row.get('A股上市日期', row.get('上市日期', ''))
                    list_date = _date_to_str(raw_list_date)
                    name = row.get('A股简称', row.get('证券简称', ''))
                    full_name = row.get('公司全称', '')
                    industry = row.get('所属行业', '')
                    rec = {
                        "code": code,
                        "name": name,
                        "full_name": full_name,
                        "board": board,
                        "list_date": list_date,
                        "industry": industry,
                    }

                    res, values = db_impl.query_by_codes([code])
                    org_stock_info = values[0] if res and values and len(values) > 0 else None
                    if org_stock_info is not None:
                        if org_stock_info.get("name", "") == "":
                            org_stock_info["name"] = name
                        if org_stock_info.get("full_name", "") == "":
                            org_stock_info["full_name"] = full_name
                        if org_stock_info.get("board", "") == "":
                            org_stock_info["board"] = board
                        if org_stock_info.get("list_date", "") == "":
                            org_stock_info["list_date"] = list_date
                        if org_stock_info.get("industry", "") == "":
                            org_stock_info["industry"] = industry
                        elif industry != "" and industry not in org_stock_info.get("industry", ""):
                            org_stock_info["industry"] += f",{industry}"
                        rec = org_stock_info.copy()
                    records.append(rec)
                if records:
                    results['sz'] = db_impl.bulk_upsert_stock_info(records)
        except Exception as e:
            self.logger.error(f"同步深证列表失败: {e}")

        # 北交所
        try:
            bj_df = self.get_bj_stock_list()
            if bj_df is not None and not bj_df.empty:
                records = []
                for _, row in bj_df.iterrows():
                    code = row.get('证券代码', row.get('代码', '')).strip()
                    board = '北交所'
                    raw_list_date = row.get('上市日期', '')
                    list_date = _date_to_str(raw_list_date)
                    name = row.get('证券简称', row.get('证券全称', row.get('公司简称', '')))
                    full_name = row.get('公司全称', '')
                    industry = row.get('所属行业', '')
                    rec = {
                        "code": code,
                        "name": name,
                        "full_name": full_name,
                        "board": board,
                        "list_date": list_date,
                        "industry": industry,
                    }
                    res, values = db_impl.query_by_codes([code])
                    org_stock_info = values[0] if res and values and len(values) > 0 else None
                    if org_stock_info is not None:
                        if org_stock_info.get("name", "") == "":
                            org_stock_info["name"] = name
                        if org_stock_info.get("full_name", "") == "":
                            org_stock_info["full_name"] = full_name
                        if org_stock_info.get("board", "") == "":
                            org_stock_info["board"] = board
                        if org_stock_info.get("list_date", "") == "":
                            org_stock_info["list_date"] = list_date
                        if org_stock_info.get("industry", "") == "":
                            org_stock_info["industry"] = industry
                        elif industry != "" and industry not in org_stock_info.get("industry", ""):
                            org_stock_info["industry"] += f",{industry}"
                        rec = org_stock_info.copy()
                    records.append(rec)
                if records:
                    results['bj'] = db_impl.bulk_upsert_stock_info(records)
        except Exception as e:
            self.logger.error(f"同步北交所列表失败: {e}")

        return results


    """
    function: sync_stock_valuation
    description: 使用 akshare 的 `stock_value_em` 接口获取单只股票估值指标，
    并按 `CompanyValuationDao` 结构保存到 MongoDB。
    param {str} code: 股票代码，支持带或不带市场前缀
    return {bool} 是否成功
    """
    def sync_stock_valuation(self, code: str) -> bool:
        db_impl = MongoCompanyValuationImpl()
        pure_code = db_impl.normalize_code(code)
        try:
            # akshare 返回的数据通常为两列：指标(item) 和 值(value)
            df = self.get_stock_valuation(stock_code=pure_code)
        except Exception as e:
            self.logger.error(f"获取 {code} 估值信息失败: {e}")
            return False

        if df is None or df.empty:
            self.logger.warning(f"stock_value_em 返回空数据: {code}")
            return False

        try:
            # 尝试支持多种返回格式：
            # 1) 两列 DataFrame，列名为 ['item','value']
            # 2) 两列但列名不确定，第一列为指标，第二列为值
            # 3) 单行 DataFrame，列名即为指标
            # 4) 单列 DataFrame，索引为指标，列为值
            # 5) 多行 DataFrame 含"数据日期"列（多个报告期）
            # 6) Series 或 dict-like
            stock_dict_list: list[dict[Hashable, Any]] = []
            if isinstance(df, pd.DataFrame):
                cols = list(df.columns)
                if 'item' in df.columns and 'value' in df.columns:
                    stock_dict_list = [dict(zip(df['item'].astype(str), df['value']))]
                elif len(cols) >= 2 and df.shape[1] == 2:
                    # 使用第一列作为 key，第二列作为 value
                    stock_dict_list = [dict(zip(df.iloc[:, 0].astype(str), df.iloc[:, 1]))]
                elif df.shape[0] == 1:
                    # 单行，列名为指标
                    stock_dict_list = [df.iloc[0].to_dict()]
                elif df.shape[1] == 1:
                    # 单列，索引为指标
                    col = df.columns[0]
                    stock_dict_list = [dict(zip(df.index.astype(str), df[col]))]
                else:
                    # 数据可能包含多行（多个报告期），按"数据日期"排序取最近一个月内数据
                    date_col: str|None = None
                    for col_name in df.columns:
                        col_str = str(col_name)
                        if "数据日期" in col_str or ("日期" in col_str and "上市" not in col_str):
                            date_col = col_name
                            break

                    if date_col:
                        # 按日期降序排列，过滤最近一个月内的数据
                        df_sorted = df.copy()
                        df_sorted[date_col] = pd.to_datetime(df_sorted[date_col], errors="coerce")
                        df_sorted = df_sorted.dropna(subset=[date_col])
                        df_sorted = df_sorted.sort_values(by=date_col, ascending=False)
                        one_day_ago = datetime.now() - timedelta(days=1)
                        df_recent = df_sorted[df_sorted[date_col] >= one_day_ago]
                        if not df_recent.empty:
                            stock_dict_list = df_recent.to_dict(orient="records")
                        else:
                            # 如果没有一个月内的数据，取最新的一条
                            stock_dict_list = df_sorted.head(1).to_dict(orient="records")
                    else:
                        records = df.to_dict(orient="records")
                        if records and isinstance(records[0], dict):
                            stock_dict_list = records
                        else:
                            # 无法解析的 DataFrame，直接返回失败
                            self.logger.error(f"无法解析估值数据格式: {code}, columns={cols}, shape={df.shape}")
                            return False
            elif isinstance(df, pd.Series):
                stock_dict_list = [df.to_dict()]
            else:
                # 如果 akshare 返回 dict-like
                try:
                    stock_dict_list = [dict(df)]
                except Exception:
                    self.logger.error(f"无法解析估值数据类型: {type(df)} for {code}")
                    return False

            if not stock_dict_list:
                self.logger.warning(f"估值数据解析后为空: {code}")
                return False

            # 批量构建 DAO 并保存到数据库
            daos_to_save: list[Mapping[Hashable, Any]] = []
            for stock_dict in stock_dict_list:
                def _get_float(*keys: str) -> float:
                    for k in keys:
                        if k in stock_dict and stock_dict[k] not in (None, ""):
                            return to_float(stock_dict[k])
                    return 0.0

                dao = CompanyValuationDao()
                dao.code = code
                dao.name = stock_dict.get("名称", stock_dict.get("name", dao.name))
                dao.total_market_cap = _get_float("总市值", "总市值(元)", "总市值(亿元)")
                dao.flow_market_cap = _get_float("流通市值", "流通市值(元)")
                dao.total_shares = _get_float("总股本")
                dao.flow_shares = _get_float("流通股本")
                dao.ttm_pe = _get_float("PE(TTM)", "市盈率(TTM)", "市盈率-动态")
                dao.pe = _get_float("PE(静)", "静态市盈率")
                dao.pb = _get_float("市净率", "市净率(倍)")
                dao.peg = _get_float("PEG值")
                dao.pc = _get_float("市现率")
                dao.ps = _get_float("市销率")
                raw_report_date = stock_dict.get("数据日期", stock_dict.get("report_date", ""))
                if isinstance(raw_report_date, (datetime, date)):
                    dao.report_date = raw_report_date.strftime("%Y-%m-%d")
                else:
                    dao.report_date = str(raw_report_date) if raw_report_date not in (None, "") else ""

                daos_to_save.append(cast(Mapping[Hashable, Any], dao.to_db()))

            ok = db_impl.bulk_upsert_company_valuation(daos_to_save)
            if not ok:
                self.logger.error(f"批量保存估值信息失败: {code}")
            else:
                self.logger.info(f"批量保存估值信息成功: {code}")
            return ok
        except Exception as e:
            self.logger.error(f"处理 {code} 估值数据失败: {e}")
            return False

    """
    function: sync_stock_financial
    description: 使用 akshare 的 `stock_financial_abstract` 接口获取单只股票财务摘要数据，
    并按 `CompanyFinanceDao` 结构保存到 MongoDB。
    param {str} code: 股票代码，支持带或不带市场前缀
    return {bool} 是否成功
    """
    def sync_stock_financial(self, code: str) -> bool:
        db_impl = MongoCompanyFinanceImpl()
        pure_code = db_impl.normalize_code(code)
        try:
            df = self.get_stock_financial(stock_code=pure_code)
        except Exception as e:
            self.logger.error(f"获取 {code} 财务摘要信息失败: {e}")
            return False

        if df is None or df.empty:
            self.logger.warning(f"stock_financial_abstract 返回空数据: {code}")
            return False

        try:
            if '指标' not in df.columns:
                self.logger.error(f"财务摘要数据缺少 '指标' 列: {code}")
                return False

            # 取最后一个报告期（最近一期）指标值
            report_columns = [c for c in df.columns if c != '选项' and c != '指标']
            if not report_columns:
                self.logger.error(f"财务摘要数据没有报告期列: {code}")
                return False
            report_date = report_columns[0]
            latest_cols = report_columns[0]

            def _find_value(metric_names: tuple[str, ...], col: str | None = None) -> float:
                """从指定列（默认 latest_cols）获取指标值"""
                target_col = col if col is not None else latest_cols
                for metric in metric_names:
                    row = df.loc[df['指标'] == metric]
                    if not row.empty:
                        value = row.iloc[0].get(target_col)
                        return to_float(value)
                return 0.0

            def _calc_yoy_growth(metric_names: tuple[str, ...]) -> float:
                """计算指标同比增长率：(本期 - 去年同期) / |去年同期| * 100"""
                # 推算去年同期列名: 如 "20260331" -> "20250331"
                year = int(latest_cols[:4])
                prev_col = f"{year - 1}{latest_cols[4:]}"
                if prev_col not in report_columns:
                    return 0.0
                current = _find_value(metric_names, col=latest_cols)
                previous = _find_value(metric_names, col=prev_col)
                if previous == 0.0:
                    return 0.0
                return (current - previous) / abs(previous) * 100.0

            dao = CompanyFinanceDao()
            dao.code = code
            dao.name = ''
            dao.total_revenue = _find_value(('营业总收入', '营业收入'))
            dao.operating_cost = _find_value(('营业成本', '营业成本合计'))
            dao.net_profit = _find_value(('净利润',))
            dao.net_profit_parent = _find_value(('归母净利润', '归属于母公司股东的净利润'))
            dao.net_profit_excl_nonrecurring = _find_value(('扣非净利润', '扣非后净利润'))
            dao.net_profit_growth_rate = _calc_yoy_growth(('净利润',))
            dao.total_revenue_growth_rate = _calc_yoy_growth(('营业总收入', '营业收入'))
            dao.goodwill = _find_value(('商誉',))
            dao.asset_liability_ratio = _find_value(('资产负债率',))
            dao.report_date = str(report_date)

            ok, _ = db_impl.insert_or_update_company_finance(dao.to_db())
            if not ok:
                self.logger.error(f"保存财务摘要信息失败: {code}")
            return ok
        except Exception as e:
            self.logger.error(f"处理 {code} 财务摘要信息失败: {e}")
            return False

    """
    function: get_stocks_his_hq
    description: 获取股票的历史行情数据，并转换成StockHisHqDao数据
    param {*} self
    return {*}
    """
    def get_stocks_his_hq(
            self,
            code: str,
            start_date: str,
            end_date: str,
            period: str = "daily",
            adjust: str = "qfq") -> pd.DataFrame|None:
        stocks_hq_df = super().get_stocks_his_hq(
            code,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust)
        if stocks_hq_df is not None and not stocks_hq_df.empty:
            stocks_hq_df.rename(
                    columns={
                        "股票代码": "code",
                        "日期": "create_time",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                        "振幅": "amp",
                        "涨跌幅": "change_percent",
                        "涨跌额": "change_amount",
                        "换手率": "turnover"
                    },
                    inplace=True
                )
            stocks_hq_df.columns = stocks_hq_df.columns.astype(str)
            # hq_dict = stocks_hq_df.to_dict(orient="records")
            # for data in hq_dict:
            #     dao = StockHisHqDao()
            #     dao.from_db(data=cast(dict[str, Any], data))
            #     stocks_his_hq_list.append(dao)
        return stocks_hq_df
    
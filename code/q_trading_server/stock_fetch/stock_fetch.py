# stock_fetch package initializer

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from configparser import ConfigParser
from datetime import datetime, timedelta
import json
import logging
import re
import time
import threading
import pandas as pd
from typing import Any
from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from db.mongo.mongo_data_agent_industry_stocks_impl import MongoDataAgentIndustryStocksImpl
from db.mongo.mongo_hot_industry_impl import MongoHotIndustryImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from db.mongo.mongo_runlog_impl import MongoRunLogImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from db.mongo.mongo_stock_minute_hq_impl import MongoStockMinuteHqImpl
from db.mongo.mongo_stocks_his_hq_impl import MongoStockHisHqImpl
from db.mongo.mongo_strategy_select_stocks_impl import MongoStrategySelectStocksImpl
from db.mongo.mongo_trade_signal_impl import MongoTradeSignalImpl
from db.mongo.mongo_data_agent_impl import MongoDataAgentImpl
from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from db.mongo.mongo_user_strategy_impl import MongoUserStrategyImpl
from db.mongo.mongo_workflow_service_impl import MongoWorkFlowServiceImpl
from db.mongo.mongo_workflow_service_user_strategy_impl import MongoWorkFlowServiceUserStrategyImpl
from stock_fetch import FetchBase
from stock_fetch.akshare_fetch import AkPeriodEnum
from stock_fetch.akshare_fetch.ak_stock_proxy import AkStockProxy
from stock_fetch.tickflow_fetch import TickPeriodEnum
from stock_fetch.tickflow_fetch.tickflow_proxy import TickFlowProxy
from apscheduler.schedulers.background import BackgroundScheduler
from app_context import AppContext
from mq.mqtt_client import MqttTopic
from apscheduler.events import (
    JobEvent,
    JobExecutionEvent,
    JobSubmissionEvent,
    EVENT_ALL,
    EVENT_JOB_ERROR,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_REMOVED,
    EVENT_ALL_JOBS_REMOVED,
    EVENT_SCHEDULER_SHUTDOWN
)


class StockFetch(FetchBase):

    CACHE_EXPIRE = 60 * 60 * 24

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        cp = ConfigParser()
        cp.read("cfg/stock.cfg")
        self.real_time_sync_interval = cp.get("stock_fetch", "real_time_sync_interval", fallback=6)
        self.akshare_industry_hq_sync_interval = cp.get("akshare", "industry_hq_sync_interval", fallback=30)
        self.tickflow_real_time_sync_interval = cp.get("tickflow", "real_time_sync_interval", fallback=6)
        self.tickflow_industry_hq_sync_interval = cp.get("tickflow", "industry_hq_sync_interval", fallback=6)
        self.sync_workers = cp.getint("stock_fetch", "sync_workers", fallback=5)
        start_date = cp.get("stock", "start_date", fallback="2024-01-01")
        super().__init__(start_date = start_date)
        self.ak_proxy = AkStockProxy()
        self.tick_proxy = TickFlowProxy()
        self.stock_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        

    


    '''
    function: 
    description: 调度入口，根据配置的参数，设置调度任务
    param {*} self
    return {*}
    '''    
    def start(self) -> None:
        # 分配股票池中的股票给数据代理对象
        asyncio.run(self.distribute_pool_stocks_to_data_agents())
        asyncio.run(self.distribute_industry_stocks_to_data_agents())
        asyncio.run(self.distribute_user_strategy_to_workflow())
        if self.stock_scheduler is not None:
            """
            注释掉的，不再server模块中运行
            """
            # interval = int(self.real_time_sync_interval)
            # minute = int(interval / 60)
            # if minute <= 0:
            #     minute = str("*")
            # else:
            #     minute = "*/" + str(minute)
            # interval %= 60
            # if interval <= 0:
            #     second_interval = "0"
            # else:
            #     second_interval = "*/" + str(interval)
            # # 实时的时间取每周1-周5，每天9点-15点，每隔interval秒
            # self.stock_scheduler.add_job(
            #     self.real_time_hq_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="9-15",
            #     minute=str(minute),
            #     second=second_interval,
            #     id="real_time_hq_sync_task",
            # )

            # # 每分钟行情快照任务：开盘期间每分钟整点执行
            # self.stock_scheduler.add_job(
            #     self.minute_hq_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="9-15",
            #     minute="*",
            #     second="0",
            #     id="minute_hq_sync_task",
            # )

            # 同步akshare行业行情数据9-15点执行
            interval = int(self.akshare_industry_hq_sync_interval)
            minute = int(interval / 60)
            if minute <= 0:
                minute = "*"
            else:
                minute = "*/" + str(minute)
            interval %= 60
            if interval <= 0:
                ak_industry_hq_interval = "0"
            else:
                ak_industry_hq_interval = "*/" + str(interval)
            self.stock_scheduler.add_job(
                self.ak_proxy.industry_hq_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="9-15",
                minute=str(minute),
                second=ak_industry_hq_interval,
                id="ak_industry_hq_interval",
            )
            # 同步tickflow的行业行情数据9-15点执行
            interval = int(self.tickflow_industry_hq_sync_interval)
            minute = int(interval / 60)
            if minute <= 0:
                minute = "*"
            else:
                minute = "*/" + str(minute)
            interval %= 60
            if interval <= 0:
                tick_industry_hq_interval = "0"
            else:
                tick_industry_hq_interval = "*/" + str(interval)
            self.stock_scheduler.add_job(
                self.tick_proxy.industry_hq_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="9-15",
                minute=str(minute),
                second=tick_industry_hq_interval,
                id="tick_industry_hq_interval",
            )
            # 每天18执行一次，删除过久的实时行情数据
            self.stock_scheduler.add_job(
                self.remove_over_time_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="18",
                minute="0",
                second="0",
                id="remove_over_time_task",
            )
            # 每天5执行一次，用于同步行业基本数据
            self.stock_scheduler.add_job(
                self.industry_base_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="5",
                minute="0",
                second="0",
                id="industry_base_sync_task",
            )
            # 每天6点，用于同步股票代码列表数据
            self.stock_scheduler.add_job(
                self.stock_list_info_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="6",
                minute="0",
                second="0",
                id="stock_list_info_sync_task",
            )
            # 每天4点执行一次，用于同步财务摘要数据
            self.stock_scheduler.add_job(
                self.stock_financial_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="4",
                minute="0",
                second="0",
                id="stock_financial_sync_task",
            )
            # 每天3点执行一次，用于同步股票估值数据
            self.stock_scheduler.add_job(
                self.stock_valuation_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="3",
                minute="0",
                second="0",
                id="stock_valuation_sync_task",
            )
            # 交易日15执行一次，获取历史K线行情并入库
            # self.stock_scheduler.add_job(
            #     self.his_hq_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="15",
            #     minute="0",
            #     second="0",
            #     id="his_hq_sync_task",
            # )
            # add job listener
            self.stock_scheduler.add_listener(self.jobListener, EVENT_ALL)
            self.stock_scheduler.start()
            
    def stop(self):
        self.stock_scheduler.remove_all_jobs()
        self.stock_scheduler.shutdown()

    """
    function: jobListener
    description: job listener for scheduler
    return {*}
    """
    def jobListener(self, event: JobEvent) -> None:
        code = event.code
        exception = event.exception if isinstance(event, JobExecutionEvent) else None
        if isinstance(event, JobExecutionEvent):
            self.logger.info(
                "jobListener, code:%d, job_id:%s, jobstore:%s, exception:%s",
                code,
                event.job_id,
                event.jobstore,
                exception)
        if code == EVENT_JOB_ERROR:
            # 任务执行抛出异常，记录错误详情
            self.logger.error(
                "jobListener, code:%d, job_id:%s, jobstore:%s, exception:%s",
                code,
                event.job_id,
                event.jobstore,
                exception)
        elif code == EVENT_JOB_MISSED:
            # 任务错过了预定时间，通常是前一次执行未完成导致
            self.logger.warning(
                "jobListener, code:%d, job_id:%s, jobstore:%s, 任务错过了预定时间",
                code,
                event.job_id,
                event.jobstore)
        elif code == EVENT_JOB_MAX_INSTANCES:
            # 任务达到最大实例数，自动增加上限
            self.logger.warning(
                "jobListener, code:%d, job_id:%s, jobstore:%s, 任务达到最大实例数，自动扩容",
                code,
                event.job_id,
                event.jobstore)
            job = self.stock_scheduler.get_job(event.job_id)
            if job is not None:
                jobCount = job.max_instances
                jobCount = jobCount + 10
                self.stock_scheduler.modify_job(event.job_id, max_instances=jobCount)
    
    """
    function: real_time_hq_sync_task
    description: 实时行情同步任务 — 获取实时行情数据，保存到 MongoDB 并通过 MQTT 推送通知
    param {*} self
    return {*}
    """
    def real_time_hq_sync_task(self):
        self.logger.info("开始执行实时行情同步任务...")
        real_hq_pd = self.tick_proxy.stock_real_hq_sync_task()
        if real_hq_pd is not None and not real_hq_pd.empty:
            db_impl = MongoRtStocksImpl()
            records = real_hq_pd.to_dict("records")
            db_impl.bulk_upsert_rt_stock_info(records)
            # 通过 MQTT 推送实时行情通知
            self._publish_real_time_hq(records)

    """
    function: _publish_real_time_hq
    description: 通过 MQTT 推送每条实时行情到对应股票 topic
    param {list} records: 实时行情记录列表
    return {*}
    """
    def _publish_real_time_hq(self, records: list[dict]) -> None:
        mqtt_client = AppContext().mqtt
        if not mqtt_client.is_connected:
            self.logger.warning("MQTT 未连接，跳过实时行情推送")
            return
        try:
            payload = json.dumps(records, ensure_ascii=False)
            if mqtt_client.publish(MqttTopic.STOCK_REAL_TIME, payload):
                self.logger.info(
                    "MQTT 实时行情推送成功, topic: %s, 股票数: %d",
                    MqttTopic.STOCK_REAL_TIME,
                    len(records),
                )
            else:
                self.logger.error("MQTT 实时行情推送失败")
        except Exception as err:
            self.logger.error("MQTT 实时行情推送异常: %s", err)

    """
    function: minute_hq_sync_task
    description: 每分钟行情快照任务
        1. 从股票池获取所有待监控的股票代码
        2. 从 rt_stocks_tbl 聚合当前分钟内所有实时行情记录，计算分钟OHLC
        3. 查询前一分钟分频行情收盘价作为 preclose，重算涨跌幅/涨跌额
        4. 组装 StockMinuteHqDao 记录，minute_time 取当前分钟整点
        5. 保存到 minute_hq_tbl
        6. 通过 MQTT 推送到分频行情 topic（q_share/stock/minute）
    param {*} self
    return {*}
    """
    def minute_hq_sync_task(self):
        self.logger.info("开始执行每分钟行情快照任务...")
        
        # 1. 计算当前分钟时间
        now = datetime.now() - timedelta(minutes=1)
        
        res, minute_records = self.do_minute_hq_sync_with_time([], now)
        if res and minute_records is not None:
            # 7. 通过 MQTT 推送分频行情
            self._publish_minute_hq(minute_records)
    
    def do_minute_hq_sync_with_time(self, codes: list, now: datetime) -> tuple[bool, list|None]:
        """执行分钟行情快照同步（按指定时间）

        通过 query_rt_stocks 查询当前分钟内的实时行情记录，按 code 分组后
        手动统计分钟 OHLC 及指标：
        - volume / amount：计算每条记录的差值之和
        - turnover / amp / qrr：求平均值

        :param codes: 股票代码列表，为空时查询当前分钟内全部实时行情记录
        :param now: 当前时间（分钟整点）
        :return: (是否成功, 分钟行情记录列表或 None)
        """
        minute_time = now.strftime("%Y-%m-%d %H:%M:00")
        minute_start = now.strftime("%Y-%m-%d %H:%M:00")
        minute_end = now.strftime("%Y-%m-%d %H:%M:59")

        rt_impl = MongoRtStocksImpl()

        # 查询当前分钟内的实时行情记录
        # codes 为空时 code="" 查询全部，非空时传入列表一次查询
        query_code: str | list[str] = codes if codes else ""
        res, all_rt_records = rt_impl.query_rt_stocks(
            code=query_code,
            start_time=minute_start,
            end_time=minute_end,
        )
        if not res or all_rt_records is None or len(all_rt_records) == 0:
            self.logger.info("当前分钟无实时行情数据，跳过分钟行情快照")
            return False, None

        # 按 code 分组：通过提取纯数字归一化 key
        grouped: dict[str, list[dict]] = {}
        for r in all_rt_records:
            raw_code = r.get("code", "")
            m = re.search(r"\d+", raw_code)
            key = m.group() if m else raw_code
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(r)

        # 每组内按 create_time 排序
        for key in grouped:
            grouped[key].sort(key=lambda r: r.get("create_time", ""))

        # 组装分频行情记录
        minute_records: list[dict] = []
        prev_day = now - timedelta(days=1)

        for pure_code, rt_records in grouped.items():
            count = len(rt_records)

            # OHLC：取首条为 open，末条为 price（收盘），最高/最低价取 price 字段极值
            first_rec = rt_records[0]
            last_rec = rt_records[-1]
            agg_price = first_rec.get("price", 0.0)
            agg_open = first_rec.get("open", agg_price)
            agg_close = last_rec.get("price", 0.0)
            agg_high = first_rec.get("high", max(r.get("price", 0.0) for r in rt_records))
            agg_low = first_rec.get("low", min(r.get("price", 0.0) for r in rt_records))
            agg_preclose = first_rec.get("preclose", 0.0)
            change_percent = 0.0
            if agg_preclose > 0:
                change_percent = first_rec.get("change_percent", round((agg_price - agg_preclose) / agg_preclose * 100, 2))
            change_amount = first_rec.get("change_amount", round(agg_price - agg_preclose, 2))
            # 从末条记录取名称、原始 code
            name = last_rec.get("name", "")
            code = last_rec.get("code", pure_code)

            # volume, amount：计算每条记录的差值之和
            volume_sum_diff = sum(
                rt_records[i].get("volume", 0) - rt_records[i - 1].get("volume", 0)
                for i in range(1, count)
            )
            amount_sum_diff = sum(
                rt_records[i].get("amount", 0.0) - rt_records[i - 1].get("amount", 0.0)
                for i in range(1, count)
            )

            # turnover, amp, qrr：求平均值
            turnover_avg = sum(r.get("turnover", 0.0) for r in rt_records) / count
            amp_avg = sum(r.get("amp", 0.0) for r in rt_records) / count
            qrr_avg = sum(r.get("qrr", 0.0) for r in rt_records) / count

            minute_rec = {
                "code": code,
                "name": name,
                "price": agg_price,
                "change_percent": change_percent,
                "change_amount": change_amount,
                "volume": volume_sum_diff,
                "amount": amount_sum_diff,
                "amp": amp_avg,
                "high": agg_high,
                "low": agg_low,
                "open": agg_open,
                "close": agg_close,
                "preclose": agg_preclose,
                "qrr": qrr_avg,
                "turnover": turnover_avg,
                "minute_time": minute_time,
                "create_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            minute_records.append(minute_rec)

        if len(minute_records) == 0:
            self.logger.info("当前分钟无实时行情数据，跳过分钟行情快照")
            return False, None

        # 保存到分频行情表
        minute_impl = MongoStockMinuteHqImpl()
        saved = minute_impl.bulk_upsert_minute_hq(minute_records)
        self.logger.info(
            "分钟行情快照保存完成, 数量: %d, 结果: %s",
            len(minute_records),
            "成功" if saved else "失败",
        )
        return True, minute_records

    """
    function: _publish_minute_hq
    description: 通过 MQTT 推送分钟行情快照到分频行情 topic
    param {list} minute_records: 分钟行情记录列表
    return {*}
    """
    def _publish_minute_hq(self, minute_records: list[dict]) -> None:
        mqtt_client = AppContext().mqtt
        if not mqtt_client.is_connected:
            self.logger.warning("MQTT 未连接，跳过分钟行情推送")
            return
        try:
            payload = json.dumps(minute_records, ensure_ascii=False)
            if mqtt_client.publish(MqttTopic.STOCK_MINUTE, payload):
                self.logger.info(
                    "MQTT 分钟行情推送成功, topic: %s, 股票数: %d",
                    MqttTopic.STOCK_MINUTE,
                    len(minute_records),
                )
            else:
                self.logger.error("MQTT 分钟行情推送失败")
        except Exception as err:
            self.logger.error("MQTT 分钟行情推送异常: %s", err)

    """
    function: remove_over_time_task
    description: 删除超过60天的实时股票数据,mongo中不保留过久的实时股票数据，避免数据过多，占用存储空间
    param {*} self
    return {*}
    """
    def remove_over_time_task(self):
        self.logger.info("开始执行删除超过60天的实时股票数据任务...")
        
        db_impl = MongoRtStocksImpl()
        recent_trading_day = self.get_recent_trading_day()
        sixty_days_ago = (datetime.strptime(recent_trading_day, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        thirty_days_ago = (datetime.strptime(recent_trading_day, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        ninty_days_ago = (datetime.strptime(recent_trading_day, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")

        # 删除超过30天的实时股票数据
        db_impl.delete_rt_stocks(code="", start_time="", end_time=thirty_days_ago)
        # 删除超过30天的估值数据
        self.logger.info("开始执行删除超过30天的估值数据任务...")
        valuation_impl = MongoCompanyValuationImpl()
        valuation_impl.delete_company_valuation_by_date(report_date=thirty_days_ago)
        # 删除30日前的选股记录
        select_impl = MongoStrategySelectStocksImpl()
        select_impl.delete_select_stocks_by_strategy_ids(strategy_ids=[], end_time = thirty_days_ago)
        
        # 删除超过90天的分钟行情数据
        self.logger.info("开始执行删除超过90天的分钟行情数据任务...")
        minute_impl = MongoStockMinuteHqImpl()
        minute_impl.delete_minute_hq(code="", end_time=ninty_days_ago)
        # 删除超过90天的历史K线数据
        self.logger.info("开始执行删除超过90天的历史K线数据任务...")
        hq_impl = MongoStockHisHqImpl()
        hq_impl.delete_day_hq(end_time=ninty_days_ago)
        hq_impl.delete_week_hq(end_time=ninty_days_ago)
        hq_impl.delete_month_hq(end_time=ninty_days_ago)

        # 删除60天前运行日志
        runlog_impl = MongoRunLogImpl()
        runlog_impl.delete_runlog_by_time(end_time=sixty_days_ago)
        # 删除60天前的交易信号
        signal_impl = MongoTradeSignalImpl()
        signal_impl.delete_signals_by_strategy_ids(strategy_ids=[], end_time=sixty_days_ago)


    """
    function: industry_base_sync_task
    description: 同步行业基础信息
    param {*} self
    return {*}
    """
    def industry_base_sync_task(self):
        self.logger.info("开始执行行业基础信息同步任务...")
        self.tick_proxy.industry_base_sync_task()

    """
    function: stock_list_info_sync_task
    description: 同步股票代码列表信息
    param {*} self
    return {*}
    """
    def stock_list_info_sync_task(self):
        self.logger.info("开始执行股票代码列表同步任务...")
        self.ak_proxy.sync_all_stock_lists()


    def query_stock_list_info(
            self,
            code: str = "",
            skip: int = 0,
            limit: int = 0,
    ) -> tuple[bool, list|None]:
        """
        function: query_stock_list_info
        description: 从数据库查询股票列表信息，支持按股票代码过滤或分页查询
        param {*} self
        param {str} code
        param {int} skip
        param {int} limit
        return {*}
        """
        db_impl = MongoStockInfoImpl()
        if code and len(code) > 0:
            return db_impl.query_stock_info(code)
        return db_impl.query_all_stock_info(skip=skip, limit=limit)

    def stock_valuation_sync_task(self):
        """
        function: stock_valuation_sync_task
        description: 同步数据库中所有股票的估值信息，使用线程池并发调用 akshare 估值接口并保存结果。
        param {*} self
        return {*}
        """
        self.logger.info("开始执行股票估值同步任务...")
        db_impl = MongoStockInfoImpl()
        res, stocks_list = db_impl.query_all_stock_info()
        if not res or stocks_list is None or len(stocks_list) == 0:
            self.logger.info("stock_valuation_sync_task: no stock info found, skip valuation sync")
            return
        codes = [
            s.get("code", "") for s in stocks_list if s.get("code", "")
        ]
        total = len(codes)
        self.logger.info("股票估值同步: %d 只待处理, 并发数=%d", total, self.sync_workers)
        completed = 0
        with ThreadPoolExecutor(max_workers=self.sync_workers) as executor:
            futures = {
                executor.submit(self.ak_proxy.sync_stock_valuation, code): code
                for code in codes
            }
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0 or completed == total:
                    self.logger.info(
                        "股票估值同步进度: %d/%d", completed, total
                    )

    def stock_financial_sync_task(self):
        """
        function: stock_financial_sync_task
        description: 同步数据库中所有股票的财务摘要数据，使用线程池并发调用 akshare 财务接口并保存结果。
        param {*} self
        return {*}
        """
        self.logger.info("开始执行股票财务摘要同步任务...")
        db_impl = MongoStockInfoImpl()
        res, stocks_list = db_impl.query_all_stock_info()
        if not res or stocks_list is None or len(stocks_list) == 0:
            self.logger.info("stock_financial_sync_task: no stock info found, skip financial sync")
            return
        codes = [
            s.get("code", "") for s in stocks_list if s.get("code", "")
        ]
        total = len(codes)
        self.logger.info("股票财务同步: %d 只待处理, 并发数=%d", total, self.sync_workers)
        completed = 0
        with ThreadPoolExecutor(max_workers=self.sync_workers) as executor:
            futures = {
                executor.submit(self.ak_proxy.sync_stock_financial, code): code
                for code in codes
            }
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0 or completed == total:
                    self.logger.info(
                        "股票财务同步进度: %d/%d", completed, total
                    )

    """
    function: his_hq_sync_task
    description: 交易日每2小时执行一次，获取股票池中所有股票的历史K线（日线/周线/月线）并入库
    param {*} self
    return {*}
    """
    def his_hq_sync_task(self):
        self.logger.info("开始执行历史K线同步任务...")
        if not self.xshg.is_session(datetime.now().date()):
            self.logger.info("非交易日，跳过历史K线同步")
            return
        today = datetime.now().date()
        if self.xshg.is_session(today):
            start_day = today.strftime("%Y-%m-%d")
            end_day = today.strftime("%Y-%m-%d")
            self.do_his_hq_sync_task(start_day, end_day)
        self.logger.info("历史K线同步任务完成")

    def do_his_hq_sync_task(self, start_day: str, end_day: str):
        # 先获取热门板块所有去重代码
        hot_industry_impl = MongoHotIndustryImpl()
        res, hot_values = hot_industry_impl.list_hot_industries()
        if not res or hot_values is None or len(hot_values) == 0:
            self.logger.info("热门行业为空，跳过历史K线同步")
            return
        hot_codes = []
        stock_impl = MongoStockInfoImpl()
        for hot_industry in hot_values:
            industry_name = hot_industry.get("name", "")
            if not industry_name:
                continue
            res, stocks = stock_impl.query_by_industry(industry=industry_name)
            if not res or stocks is None or len(stocks) == 0:
                self.logger.info("股票信息为空，跳过历史K线同步")
                continue
            hot_codes.extend([s["code"] for s in stocks if s.get("code")])
        # 再获取股票池中的去重代码
        pool_impl = MongoStockPoolImpl()
        res, pool_stocks = pool_impl.query_all_pool_stocks(skip=0, limit=0)
        if res and pool_stocks is not None and len(pool_stocks) > 0:
            hot_codes.extend([s["code"] for s in pool_stocks if s.get("code")])
        # 去重
        hot_codes = list(set(hot_codes))        
        if not hot_codes:
            self.logger.info("股票中无有效代码，跳过历史K线同步")
            return

        self.logger.info(
            "历史K线同步: %d 只股票, 开始日期=%s， 结束日期=%s",
            len(hot_codes), start_day, end_day,
        )
        cp = ConfigParser()
        cp.read("cfg/stock.cfg")
        number_of_once = int(cp.get("tickflow", "history_hq_sync_number_of_once", fallback=1))
        work_interval = int(cp.get("tickflow", "history_hq_sync_interval", fallback=5))
        # 分批提交到线程池，批次间 sleep 控制 API 请求频率
        completed = 0
        total = len(hot_codes)
        total_batches = (total + number_of_once - 1) // number_of_once
        for i in range(0, total, number_of_once):
            batch_codes = hot_codes[i : i + number_of_once]
            batch_idx = i // number_of_once + 1
            self.logger.info(
                "历史K线同步: 批次 %d/%d, 股票数=%d, 代码=%s",
                batch_idx,
                total_batches,
                len(batch_codes),
                batch_codes,
            )
            thread = threading.Thread(
                target=self._fetch_and_save_his_hq,
                args=(batch_codes, start_day, end_day, "day"),
                daemon=True,
            )
            thread.start()
            thread.join()
            completed += len(batch_codes)
            self.logger.info(
                "历史K线同步进度: %d/%d", completed, total
            )
            # 批次间休眠，控制 API 请求频率，最后一轮不需要 sleep
            if batch_idx < total_batches:
                time.sleep(work_interval)
    def _extract_pure_code(self, code: str) -> str:
        """从股票代码中提取纯数字部分，用于代码匹配"""
        m = re.search(r"\d+", code)
        return m.group() if m else code

    def _fetch_and_save_his_hq(
            self,
            missing_codes: list[str],
            start_date: str,
            end_date: str,
            period_label: str,
    ) -> list[dict]:
        """对缺失的代码从 API 获取历史K线并保存到数据库

        :param missing_codes: 数据库中缺失的股票代码列表
        :param start_date: 开始日期
        :param end_date: 结束日期
        :param period_label: "day" / "week" / "month"
        :return: 获取到的记录列表
        """
        db_impl = MongoStockHisHqImpl()
        new_records: list[dict] = []
        for code in missing_codes:
            if period_label == "day":
                result = self.tick_proxy.get_stocks_his_hq(code, start_date, end_date)
                if result is None or result.empty:
                    pure_match = re.search(r"\d+", code)
                    pure_code = pure_match.group() if pure_match else code
                    result = self.ak_proxy.get_stocks_his_hq(pure_code, start_date, end_date)
            elif period_label == "week":
                result = self.tick_proxy.get_stocks_his_hq(
                    code, start_date, end_date, period=TickPeriodEnum.ONE_WEEK.value
                )
                if result is None or result.empty:
                    pure_match = re.search(r"\d+", code)
                    pure_code = pure_match.group() if pure_match else code
                    result = self.ak_proxy.get_stocks_his_hq(pure_code, start_date, end_date, period=AkPeriodEnum.WeekPeriod.value)
            else:  # month
                result = self.tick_proxy.get_stocks_his_hq(
                    code, start_date, end_date, period=TickPeriodEnum.ONE_MONTH.value
                )
                if result is None or result.empty:
                    pure_match = re.search(r"\d+", code)
                    pure_code = pure_match.group() if pure_match else code
                    result = self.ak_proxy.get_stocks_his_hq(pure_code, start_date, end_date, period=AkPeriodEnum.MonthPeriod.value)
            if result is not None and not result.empty:
                result["code"] = code  # 确保 code 字段为原始代码
                new_records.extend(result.to_dict("records"))

        # 保存到数据库
        if new_records:
            if period_label == "day":
                db_impl.bulk_upsert_day_hq(new_records)
            elif period_label == "week":
                db_impl.bulk_upsert_week_hq(new_records)
            else:
                db_impl.bulk_upsert_month_hq(new_records)

        return new_records

    """
    function: get_stock_day_his_hq
    description: 查询股票代码列表的历史行情日K线，先查数据库，数据库没有则调用代理接口获取并入库
    param {*} self
    param {list[str]} codes: 股票代码列表
    param {str} start_date: 开始日期，格式 %Y-%m-%d
    param {str} end_date: 结束日期，格式 %Y-%m-%d
    return {pd.DataFrame} 日K线 DataFrame
    """
    def get_stock_day_his_hq(
            self,
            codes: list[str],
            start_date: str,
            end_date: str
    ) -> pd.DataFrame:
        db_impl = MongoStockHisHqImpl()
        res, db_records = db_impl.query_day_hq(
            codes=codes, start_time=start_date, end_time=end_date
        )
        all_records: list[dict] = []
        db_codes: set[str] = set()
        if res and db_records:
            all_records.extend(db_records)
            for rec in db_records:
                pure = self._extract_pure_code(rec.get("code", ""))
                if pure:
                    db_codes.add(pure)

        # 找出数据库中缺失的代码
        missing_codes: list[str] = [
            code for code in codes
            if self._extract_pure_code(code) not in db_codes
        ]

        if missing_codes:
            self.logger.info(
                "日K线数据库缺失 %d/%d 只股票，调用API获取",
                len(missing_codes), len(codes),
            )
            new_records = self._fetch_and_save_his_hq(
                missing_codes, start_date, end_date, "day"
            )
            all_records.extend(new_records)

        return pd.DataFrame(all_records) if all_records else pd.DataFrame()

    """
    function: get_stock_week_his_hq
    description: 查询股票代码列表的历史行情周K线，先查数据库，数据库没有则调用代理接口获取并入库
    param {*} self
    param {list[str]} codes: 股票代码列表
    param {str} start_date: 开始日期，格式 %Y-%m-%d
    param {str} end_date: 结束日期，格式 %Y-%m-%d
    return {pd.DataFrame} 周K线 DataFrame
    """
    def get_stock_week_his_hq(
            self,
            codes: list[str],
            start_date: str,
            end_date: str
    ) -> pd.DataFrame:
        db_impl = MongoStockHisHqImpl()
        res, db_records = db_impl.query_week_hq(
            codes=codes, start_time=start_date, end_time=end_date
        )
        all_records: list[dict] = []
        db_codes: set[str] = set()
        if res and db_records:
            all_records.extend(db_records)
            for rec in db_records:
                pure = self._extract_pure_code(rec.get("code", ""))
                if pure:
                    db_codes.add(pure)

        missing_codes: list[str] = [
            code for code in codes
            if self._extract_pure_code(code) not in db_codes
        ]

        if missing_codes:
            self.logger.info(
                "周K线数据库缺失 %d/%d 只股票，调用API获取",
                len(missing_codes), len(codes),
            )
            new_records = self._fetch_and_save_his_hq(
                missing_codes, start_date, end_date, "week"
            )
            all_records.extend(new_records)

        return pd.DataFrame(all_records) if all_records else pd.DataFrame()

    """
    function: get_stock_month_his_hq
    description: 查询股票代码列表的历史行情月K线，先查数据库，数据库没有则调用代理接口获取并入库
    param {*} self
    param {list[str]} codes: 股票代码列表
    param {str} start_date: 开始日期，格式 %Y-%m-%d
    param {str} end_date: 结束日期，格式 %Y-%m-%d
    return {pd.DataFrame} 月K线 DataFrame
    """
    def get_stock_month_his_hq(
            self,
            codes: list[str],
            start_date: str,
            end_date: str
    ) -> pd.DataFrame:
        db_impl = MongoStockHisHqImpl()
        res, db_records = db_impl.query_month_hq(
            codes=codes, start_time=start_date, end_time=end_date
        )
        all_records: list[dict] = []
        db_codes: set[str] = set()
        if res and db_records:
            all_records.extend(db_records)
            for rec in db_records:
                pure = self._extract_pure_code(rec.get("code", ""))
                if pure:
                    db_codes.add(pure)

        missing_codes: list[str] = [
            code for code in codes
            if self._extract_pure_code(code) not in db_codes
        ]

        if missing_codes:
            self.logger.info(
                "月K线数据库缺失 %d/%d 只股票，调用API获取",
                len(missing_codes), len(codes),
            )
            new_records = self._fetch_and_save_his_hq(
                missing_codes, start_date, end_date, "month"
            )
            all_records.extend(new_records)

        return pd.DataFrame(all_records) if all_records else pd.DataFrame()

    """
    function: get_real_time_stock_hq
    description: 查询股票实时行情，先根据时间段查询数据库，如果数据库没有数据，则调用 tick_proxy 的 get_real_time_hq 接口获取实时行情
    param {*} self
    param {list[str]} codes: 股票代码列表
    param {str} start_time: 开始时间，格式 %Y-%m-%d %H:%M:%S
    param {str} end_time: 结束时间，格式 %Y-%m-%d %H:%M:%S
    return {pd.DataFrame} 实时行情 DataFrame
    """
    def get_real_time_stock_hq(
            self,
            codes: list[str],
            start_time: str,
            end_time: str
    ) -> pd.DataFrame:
        db_impl = MongoRtStocksImpl()
        res, all_records = db_impl.query_rt_stocks(
            code=codes,
            start_time=start_time,
            end_time=end_time,
        )

        if all_records:
            self.logger.info(
                "从数据库查询到实时行情数据, 股票数: %d, 记录数: %d",
                len(codes), len(all_records),
            )
            return pd.DataFrame(all_records)

        # 数据库没有数据，调用 tick_proxy 获取实时行情
        self.logger.info(
            "数据库无实时行情数据，调用 tickflow 接口获取, 股票数: %d",
            len(codes),
        )
        stock_list = [{"code": code} for code in codes]
        result = self.tick_proxy.do_stock_real_hq(stock_list=stock_list)
        return pd.DataFrame() if result is None or result.empty else result


    async def distribute_pool_stocks_to_data_agents(self, pool_name: str = "") -> bool:

        """查询所有股票池股票并将其平均分配给所有数据代理。"""
        self.logger.info("开始将池内股票平均分配给所有数据代理")
        pool_impl = MongoStockPoolImpl()
        if pool_name and len(pool_name) > 0:
            res, pool_records = pool_impl.query_stocks_by_pool_name(pool_name)
        else:
            res, pool_records = pool_impl.query_all_pool_stocks()
        if not res or not pool_records:
            self.logger.warning("未查询到任何池内股票")
            return False

        stock_codes = [r.get("code", "") for r in pool_records if r.get("code", "")]
        if not stock_codes:
            self.logger.warning("池内股票代码列表为空")
            return False

        agent_impl = MongoDataAgentImpl()
        res, agents = agent_impl.query_data_agents()
        if not res or not agents:
            self.logger.warning("未查询到任何数据代理")
            return False

        agent_names = [a.get("agent_name", "") for a in agents if a.get("agent_name", "")]
        if not agent_names:
            self.logger.warning("数据代理名称列表为空")
            return False
        
        pool_records_len = len(pool_records)
        agent_names_len = len(agent_names)
        assignment_count = max(1, pool_records_len // agent_names_len)
        skip = 0
        stocks_impl = MongoDataAgentPoolStocksImpl()
        # 清空数据代理股票分配表
        if pool_name == "":
            stocks_impl.delete_data_agent_pool_stocks()
        else:
            stocks_impl.delete_data_agent_pool_stocks_by_pool_name(pool_name=pool_name)
        for skip in range(0, pool_records_len, assignment_count):
            agent_name = agent_names[(skip // assignment_count) % agent_names_len]
            batch_codes: list[dict[str, Any]] = []
            end = min(skip + assignment_count, pool_records_len)
            batch_records = pool_records[skip:end]
            batch_codes = [
                {"code": r.get("code", ""), "pool_name": [r.get("pool_name", "")] if not isinstance(r.get("pool_name", ""), list) else r.get("pool_name", [])}
                for r in batch_records if r.get("code", "")
            ]
            res, existing_records = stocks_impl.query_data_agent_pool_stock_by_agent_name(agent_name)
            if res and existing_records:
                for item in existing_records:
                    codes_list = item.get("stock_codes_pool", [])
                    batch_codes.extend(codes_list)
            stocks_impl.upsert_data_agent_pool_stock(
                {
                    "agent_name": agent_name,
                    "stock_codes_pool": batch_codes,
                }
            )
        self.logger.info(
            "池内股票已平均分配给数据代理, 股票数: %d, 数据代理数: %d, 每个代理分配约 %d 只股票",
            pool_records_len,
            agent_names_len,
            assignment_count,
        )
        return True

    async def distribute_industry_stocks_to_data_agents(self) -> bool:
    
        """查询所有热门行业股票并将其平均分配给所有数据代理。"""
        self.logger.info("开始将热门行业股票平均分配给所有数据代理")
        stock_impl = MongoStockInfoImpl()
        stock_record = []
        hot_impl = MongoHotIndustryImpl()
        res, hot_list = hot_impl.list_hot_industries()
        if res and hot_list is not None and len(hot_list) > 0:
            for item in hot_list:
                res, stock_list = stock_impl.query_by_industry(industry=item["name"])
                if res and stock_list is not None and len(stock_list) > 0:
                    stock_record.extend(stock_list)
        if not res or not stock_record:
            self.logger.warning("未查询到任何行业股票")
            return False

        stock_codes = [r.get("code", "") for r in stock_record if r.get("code", "")]
        if not stock_codes:
            self.logger.warning("行业股票代码列表为空")
            return False

        agent_impl = MongoDataAgentImpl()
        res, agents = agent_impl.query_data_agents()
        if not res or not agents:
            self.logger.warning("未查询到任何数据代理")
            return False

        agent_names = [a.get("agent_name", "") for a in agents if a.get("agent_name", "")]
        if not agent_names:
            self.logger.warning("数据代理名称列表为空")
            return False
        
        stock_records_len = len(stock_record)
        agent_names_len = len(agent_names)
        assignment_count = max(1, stock_records_len // agent_names_len)
        skip = 0
        agent_industry_impl = MongoDataAgentIndustryStocksImpl()
        # 清空数据代理股票分配表
        agent_industry_impl.delete()
        for skip in range(0, stock_records_len, assignment_count):
            end = min(skip + assignment_count, stock_records_len)
            batch_records = stock_record[skip:end]
            batch_codes = [
                {"code": r.get("code", ""), "industry": [i for i in r.get("industry", "").split(",") if i]}
                for r in batch_records if r.get("code", "")
            ]
            agent_name = agent_names[(skip // assignment_count) % agent_names_len]
            res, existing_records = agent_industry_impl.query_by_agent_name(agent_name)
            if res and existing_records:
                for item in existing_records:
                    codes_list = item.get("stock_codes_industry", [])
                    batch_codes.extend(codes_list)
            agent_industry_impl.upsert(
                {
                    "agent_name": agent_name,
                    "stock_codes_industry": batch_codes,
                }
            )
        self.logger.info(
            "行业股票已平均分配给数据代理, 股票数: %d, 数据代理数: %d, 每个代理分配约 %d 只股票",
            stock_records_len,
            agent_names_len,
            assignment_count,
        )
        return True

    async def distribute_user_strategy_to_workflow(self) -> bool:
        
        """查询所有user_strategy并将其平均分配给所有工作流微服务。"""
        self.logger.info("开始将user_strategy平均分配给所有工作流微服务")
        user_strategy_ids_list = []
        user_strategy_impl = MongoUserStrategyImpl()
        res, user_strategy_list = user_strategy_impl.query_all_user_strategies()
        if res and user_strategy_list and isinstance(user_strategy_list, list):
            user_strategy_ids_list = [str(s.get("_id", s.get("id"))) for s in user_strategy_list if str(s.get("_id", s.get("id", "")))]

        if len(user_strategy_ids_list) == 0:
            self.logger.warning("未查询到user_strategy_id")
            return False

        service_impl = MongoWorkFlowServiceImpl()
        res, workflow_service_list = service_impl.query_workflow_services()
        if not res or not workflow_service_list:
            self.logger.warning("未查询到任何工作流微服务")
            return False

        service_names_list = [a.get("service_name", "") for a in workflow_service_list if a.get("service_name", "")]
        if not service_names_list or len(service_names_list) == 0:
            self.logger.warning("流程处理微服务列表为空")
            return False
        
        user_strategy_len = len(user_strategy_ids_list)
        service_names_len = len(service_names_list)
        assignment_count = max(1, user_strategy_len // service_names_len)
        skip = 0
        workflow_service_user_strategy_impl = MongoWorkFlowServiceUserStrategyImpl()
        # 清空数据代理股票分配表
        workflow_service_user_strategy_impl.delete()
        for skip in range(0, user_strategy_len, assignment_count):
            end = min(skip + assignment_count, user_strategy_len)
            batch_records = user_strategy_ids_list[skip:end]
            service_name = service_names_list[(skip // assignment_count) % service_names_len]
            res, existing_records = workflow_service_user_strategy_impl.query_by_service_name(service_name)
            if res and existing_records:
                batch_records.extend(existing_records)
            workflow_service_user_strategy_impl.upsert(
                {
                    "service_name": service_name,
                    "user_strategy_ids": batch_records,
                }
            )
        self.logger.info(
            "user_strategy已平均分配给流程微服务, user_strategy_len: %d, 流程处理微服务个数: %d, 平均分配：%d",
            user_strategy_len,
            service_names_len,
            assignment_count,
        )
        return True
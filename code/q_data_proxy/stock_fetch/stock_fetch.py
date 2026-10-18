# stock_fetch package initializer

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import logging
import re
import time
from typing import Any
import pandas as pd
from db.mongo.mongo_company_valuation_impl import MongoCompanyValuationImpl
from db.mongo.mongo_data_agent_industry_stocks_impl import MongoDataAgentIndustryStocksImpl
from db.mongo.mongo_data_agent_pool_stocks_impl import MongoDataAgentPoolStocksImpl
from db.mongo.mongo_rt_stocks_impl import MongoRtStocksImpl
from db.mongo.mongo_stock_info_impl import MongoStockInfoImpl
from db.mongo.mongo_stock_pool_impl import MongoStockPoolImpl
from db.mongo.mongo_stock_minute_hq_impl import MongoStockMinuteHqImpl
from db.mongo.mongo_stocks_his_hq_impl import MongoStockHisHqImpl
from stock_fetch import FetchBase
from stock_fetch.akshare_fetch import AkPeriodEnum
from stock_fetch.akshare_fetch.ak_stock_proxy import AkStockProxy
from stock_fetch.tickflow_fetch import TickPeriodEnum
from stock_fetch.tickflow_fetch.tickflow_proxy import TickFlowProxy
from apscheduler.schedulers.background import BackgroundScheduler
from app_context import AppContext
from mq.mqtt_client import MqttTopic
from utils.config import load_stock_cfg, stock_instance_name
from apscheduler.events import (
    JobEvent,
    JobExecutionEvent,
    EVENT_ALL,
    EVENT_JOB_ERROR,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
)


def pure_stock_code(raw_code: str) -> str:
    """取股票代码的纯数字部分，用于跨格式归组。

    库里可能混存 000001 / 000001.SZ / sh000001，只做字符串精确比较会把同一只
    股票拆成多组，进而生成多条 minute_time 相同的分钟记录在 upsert 时互相覆盖。
    """
    if not raw_code:
        return ""
    matched = re.search(r"\d+", raw_code)
    return matched.group() if matched else raw_code


def session_open_of(xshg: Any, day) -> datetime:
    """取指定交易日的开盘时间（返回 naive 本地时间）。

    交易所日历不可用时退回 09:30，保证调用方逻辑仍然确定。
    """
    try:
        opened = xshg.session_open(day)
        if getattr(opened, "tzinfo", None) is not None:
            opened = opened.tz_localize(None)
        if hasattr(opened, "to_pydatetime"):
            opened = opened.to_pydatetime()
        return opened
    except Exception:
        return datetime(day.year, day.month, day.day, 9, 30)


def split_minute_window_records(
    records: list[dict], minute_start: str
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """按窗口左边界切分查询结果。

    实时快照的 volume / amount 是当日累计值，窗口内的量要靠「窗口开始前的末条
    累计值」（基线）差分得到，因此查询起点比窗口起点前移一段，取回后在这里切成
    两部分。

    :param records: 实时行情查询结果（含前移部分）
    :param minute_start: 窗口左边界，格式 %Y-%m-%d %H:%M:%S
    :return: (窗口内记录，按 code 归组且组内升序；窗口开始前的末条记录，按 code 索引)
    """
    grouped: dict[str, list[dict]] = {}
    baselines: dict[str, dict] = {}
    for r in records:
        key = pure_stock_code(r.get("code", ""))
        if key == "":
            continue
        create_time = r.get("create_time", "")
        if create_time < minute_start:
            prev = baselines.get(key)
            if prev is None or create_time > prev.get("create_time", ""):
                baselines[key] = r
        else:
            grouped.setdefault(key, []).append(r)
    for key in grouped:
        grouped[key].sort(key=lambda r: r.get("create_time", ""))
    return grouped, baselines


def cumulative_delta(
    window_records: list[dict],
    baseline_rec: dict | None,
    field: str,
    zero_baseline: bool,
) -> tuple[float, bool]:
    """按累计值口径算窗口内的增量（成交量 / 成交额）。

    沪深实时快照的成交量、成交额是当日累计值，行业口径为：
    窗口内的量 = 窗口末条累计值 − 窗口开始前的末条累计值。

    不能用窗口内首尾作差，那会丢掉窗口内首条快照的增量（上一窗口结束到本窗口
    首条快照之间成交的部分）；窗口内只有 n 条采样时，偏差恒为 -1/n。

    :param window_records: 窗口内记录，按 create_time 升序
    :param baseline_rec: 窗口开始前的末条记录，可为 None
    :param field: 字段名，volume 或 amount
    :param zero_baseline: 基线缺失时是否以 0 为基线（当日首窗，含早盘集合竞价成交量）
    :return: (增量, 是否用到了基线)
    """
    last_value = window_records[-1].get(field, 0) or 0
    if baseline_rec is not None:
        return last_value - (baseline_rec.get(field, 0) or 0), True
    if zero_baseline:
        return last_value, False
    # 基线缺失且非当日首窗（服务中途启动、数据缺口）：退回首尾作差，
    # 避免把当日累计量整体灌进单分钟记录
    first_value = window_records[0].get(field, 0) or 0
    return last_value - first_value, False


class StockFetch(FetchBase):

    CACHE_EXPIRE = 60 * 60 * 24

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        cp = load_stock_cfg()
        self.real_time_sync_interval = cp.get("stock_fetch", "real_time_sync_interval", fallback=6)
        self.akshare_industry_hq_sync_interval = cp.get("akshare", "industry_hq_sync_interval", fallback=30)
        self.tickflow_real_time_sync_interval = cp.get("tickflow", "real_time_sync_interval", fallback=6)
        self.tickflow_industry_hq_sync_interval = cp.get("tickflow", "industry_hq_sync_interval", fallback=6)
        self.sync_workers = cp.getint("stock_fetch", "sync_workers", fallback=5)
        self.minute_sync_interval = cp.getint("stock_fetch", "minute_sync_interval", fallback=1)
        start_date = cp.get("stock", "start_date", fallback="2024-01-01")
        self.real_time_skip = 0
        # real_time_stock_limit 配置位于 [stock_fetch] 段（兼容历史上写在 [tickflow] 段的配置）
        self.real_time_limit = cp.getint(
            "stock_fetch",
            "real_time_stock_limit",
            fallback=cp.getint("tickflow", "real_time_stock_limit", fallback=5),
        )
        super().__init__(start_date = start_date)
        self.real_time_sync_stocks = []
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
        if self.stock_scheduler is not None:
            interval = int(self.real_time_sync_interval)
            # cron 只能精确表达 60 的约数（秒级）或 60 的整数倍（分钟级）。
            # 例如配置 90 秒会落入 minute="*/1" + second="*/30"，实际每 30 秒触发一次，
            # 与配置严重不符且无任何提示，因此在此显式校正并告警。
            if interval < 60:
                if interval <= 0 or 60 % interval != 0:
                    candidates = [v for v in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30) if v <= interval]
                    adjusted = candidates[-1] if candidates else 1
                    self.logger.warning(
                        "real_time_sync_interval=%s 秒无法被 cron 精确表达，已就近调整为 %s 秒",
                        interval,
                        adjusted,
                    )
                    interval = adjusted
            elif interval % 60 != 0:
                adjusted = (interval // 60) * 60
                self.logger.warning(
                    "real_time_sync_interval=%s 秒无法被 cron 精确表达（60 秒以上需为 60 的整数倍），已就近调整为 %s 秒",
                    interval,
                    adjusted,
                )
                interval = adjusted
            minute_interval = int(interval / 60)
            minute: str = "*"
            if minute_interval <= 0:
                minute = "*"
            else:
                minute = "*/" + str(minute_interval)
            interval %= 60
            if interval <= 0:
                second_interval = "0"
            else:
                second_interval = "*/" + str(interval)
            # 实时的时间取每周1-周5，每天9点-15点，每隔interval秒
            self.stock_scheduler.add_job(
                self.real_time_hq_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="9-15",
                minute=minute,
                second=second_interval,
                id="real_time_hq_sync_task",
                # 任务内 real_time_skip / real_time_sync_stocks 是实例级共享状态，
                # 读改写不原子，显式锁定单实例执行
                max_instances=1,
            )

            # 每分钟行情快照任务：开盘期间每分钟整点执行
            interval = int(self.minute_sync_interval)
            minute_interval = "*"
            if interval > 1:
                minute_interval = "*/" + str(interval)
            self.stock_scheduler.add_job(
                self.minute_hq_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="9-15",
                minute=minute_interval,
                second="0",
                id="minute_hq_sync_task",
                max_instances=1,
            )
            # 交易日 15:00 执行一次，获取历史K线行情并入库。
            # 原实现为 11:00 与 15:00 各一次：11:00 处于盘中，取到的当日K线未收市，
            # 且 15:00 会把全部股票再拉一遍，两次请求大部分重复，故只保留 15:00。
            self.stock_scheduler.add_job(
                self.his_hq_sync_task,
                trigger="cron",
                day_of_week="mon-fri",
                hour="15",
                minute="0",
                second="0",
                id="his_hq_sync_task",
                max_instances=1,
            )
            # 同步akshare行业行情数据9-15点执行
            # interval = int(self.akshare_industry_hq_sync_interval)
            # minute_interval = int(interval / 60)
            # minute: str = "*"
            # if minute_interval <= 0:
            #     minute = "*"
            # else:
            #     minute = "*/" + str(minute_interval)
            # interval %= 60
            # if interval <= 0:
            #     ak_industry_hq_interval = "0"
            # else:
            #     ak_industry_hq_interval = "*/" + str(interval)
            # self.stock_scheduler.add_job(
            #     self.ak_proxy.industry_hq_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="9-15",
            #     minute=minute,
            #     second=ak_industry_hq_interval,
            #     id="ak_industry_hq_interval",
            # )
            # # 同步tickflow的行业行情数据9-15点执行
            # interval = int(self.tickflow_industry_hq_sync_interval)
            # minute_interval = int(interval / 60)
            # minute = "*"
            # if minute_interval <= 0:
            #     minute = "*"
            # else:
            #     minute = "*/" + str(minute_interval)
            # interval %= 60
            # if interval <= 0:
            #     second_interval = "0"
            # else:
            #     second_interval = "*/" + str(interval)
            # self.stock_scheduler.add_job(
            #     self.tick_proxy.industry_hq_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="9-15",
            #     minute=minute,
            #     second=second_interval,
            #     id="tickflow_industry_hq_sync_task",
            # )

            # 每天5执行一次，用于同步行业基本数据
            # self.stock_scheduler.add_job(
            #     self.industry_base_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="5",
            #     minute="0",
            #     second="0",
            #     id="industry_base_sync_task",
            # )
            # # 每天6点，用于同步股票代码列表数据
            # self.stock_scheduler.add_job(
            #     self.stock_list_info_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="6",
            #     minute="0",
            #     second="0",
            #     id="stock_list_info_sync_task",
            # )
            # # 每天4点执行一次，用于同步财务摘要数据
            # self.stock_scheduler.add_job(
            #     self.stock_financial_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="4",
            #     minute="0",
            #     second="0",
            #     id="stock_financial_sync_task",
            # )
            # # 每天3点执行一次，用于同步股票估值数据
            # self.stock_scheduler.add_job(
            #     self.stock_valuation_sync_task,
            #     trigger="cron",
            #     day_of_week="mon-fri",
            #     hour="3",
            #     minute="0",
            #     second="0",
            #     id="stock_valuation_sync_task",
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
            # APScheduler 在实例数达到上限时会跳过本次执行并抛出该事件。
            # 原实现在此把 max_instances 每次 +10 自动扩容，会让同一个 job 并发执行，
            # 而 real_time_skip / real_time_sync_stocks 是实例级共享状态，读改写三步不原子，
            # 并发实例会重复同步同一批股票或错乱分批指针，故不再扩容，只记录告警。
            self.logger.warning(
                "jobListener, code:%d, job_id:%s, jobstore:%s, 任务达到最大实例数，本次执行已被跳过",
                code,
                event.job_id,
                event.jobstore)

    """
    function: load_agent_pool_stocks
    description: 加载实时同步的股票数据
    param {*} self
    return {*}
    """
    def load_agent_pool_stocks(self) -> list:
        cp = load_stock_cfg()
        agent_name = stock_instance_name(cp)
        agent_pool_impl = MongoDataAgentPoolStocksImpl()
        real_time_stocks = []
        res, agent_pool_stocks = agent_pool_impl.query_data_agent_pool_stock_by_agent_name(agent_name)
        if res and agent_pool_stocks and len(agent_pool_stocks) > 0:
            pool_impl = MongoStockPoolImpl()
            for agent_pool_stock_item in agent_pool_stocks:
                stock_codes_pool_list = agent_pool_stock_item.get("stock_codes_pool", [])
                if len(stock_codes_pool_list) == 0:
                    continue
                real_time_stocks.extend(stock_codes_pool_list)
        return real_time_stocks
    """
    function: find_pool_id_in_sync_stocks
    description: 根据股票代码查找对应的pool_id
    param {*} self
    return {*}
    """
    def find_pool_id_in_sync_stocks(self, code: str, sync_stocks: list) -> None | list[str]:
        if code == "":
            return None
        # 纯数字归一化兜底：tickflow 返回的 symbol（如 000001/SZ）与库内存储格式
        # （000001、000001.SZ、sh000001）可能不一致，仅用精确比较会导致 MQTT 推送被静默跳过。
        pure_code = re.sub(r"\D", "", code)
        for stocks in sync_stocks:
            stock_code = stocks.get("code", "")
            if stock_code != code:
                if not pure_code or re.sub(r"\D", "", stock_code) != pure_code:
                    continue
            pool_id_list = stocks.get("pool_ids", [])
            if not isinstance(pool_id_list, list):
                if isinstance(pool_id_list, str):
                    return [pool_id_list]
                continue
            return pool_id_list
        return None

    def get_real_time_stocks(self) -> list:
        real_time_stocks = self.load_agent_pool_stocks()
        res, industry_stocks_list = self.load_agent_hot_industry_stocks()
        if not res or not industry_stocks_list:
            return real_time_stocks
        # 已存在的 code（来自 pool_stocks），后续 extend 不覆盖，避免 pool_id 被默认值 [] 覆盖
        existing_codes = {item.get("code") for item in real_time_stocks if item.get("code")}
        for industry_stocks in industry_stocks_list:
            codes_industry_list = industry_stocks.get("stock_codes_industry", [])
            real_time_stocks.extend(
                {"code": s.get("code"), "pool_ids": []}
                for s in codes_industry_list
                if s.get("code") and s.get("code") not in existing_codes
            )
        return real_time_stocks
    """
    function: real_time_hq_sync_task
    description: 实时行情同步任务 — 获取实时行情数据，保存到 MongoDB 并通过 MQTT 推送通知
    param {*} self
    return {*}
    """
    def real_time_hq_sync_task(self):
        self.logger.info("开始执行实时行情同步任务...")
        if not self.xshg.is_session(datetime.now().date()):
            return
        now = datetime.now()
        if now.hour < 9 \
            or (now.hour == 9 and now.minute < 30) \
            or (now.hour == 11 and now.minute > 30) \
            or (now.hour > 11 and now.hour < 13) \
            or now.hour > 15:
            return
        if len(self.real_time_sync_stocks) == 0:
            self.real_time_sync_stocks = self.get_real_time_stocks()
        stocks_count = len(self.real_time_sync_stocks)
        self.logger.info(f"real_time_sync_stocks length:{stocks_count}")
        if stocks_count == 0:
            return
        limit_count = min(self.real_time_skip + self.real_time_limit, stocks_count)
        sync_stock_list = self.real_time_sync_stocks[self.real_time_skip : limit_count]
        self.real_time_skip += self.real_time_limit
        if self.real_time_skip >= stocks_count:
            self.real_time_skip = 0
            self.real_time_sync_stocks.clear() # 同步一遍了重新获取一次代码
        real_hq_pd = self.tick_proxy.do_stock_real_hq(sync_stock_list)
        if real_hq_pd is not None and not real_hq_pd.empty:
            db_impl = MongoRtStocksImpl()
            records = real_hq_pd.to_dict("records")
            db_impl.bulk_upsert_rt_stock_info(records)
            # 通过 MQTT 推送实时行情通知
            self._publish_real_time_hq(stocks_list = sync_stock_list, records = records)

    """
    function: _publish_real_time_hq
    description: 通过 MQTT 推送每条实时行情到对应股票 topic
    param {list} records: 实时行情记录列表
    return {*}
    """
    def _publish_real_time_hq(self, stocks_list: list, records: list[dict]) -> None:
        mqtt_client = AppContext().mqtt
        if not mqtt_client.is_connected:
            self.logger.warning("MQTT 未连接，跳过实时行情推送")
            return
        try:
            for item in records:
                code = item.get("code", "")
                if code == "":
                    continue
                # 统一 code 格式：将 002281/SZ 转为 002281.SZ，避免 topic 多出一层
                code = code.replace("/", ".")
                pool_ids_list = self.find_pool_id_in_sync_stocks(code, stocks_list)
                if not pool_ids_list or len(pool_ids_list) == 0:
                    continue
                payload = json.dumps(item, ensure_ascii=False)
                for pool_id in pool_ids_list:
                    topic = MqttTopic.stock_real_time_pool_code(pool_id=pool_id, code=code)
                    if mqtt_client.publish(topic=topic, payload=payload):
                        self.logger.info(
                            "MQTT 实时行情推送成功, topic: %s, payload: %s",
                            topic,
                            payload,
                        )
                    else:
                        self.logger.error("MQTT 实时行情推送失败")
        except Exception as err:
            self.logger.error("MQTT 实时行情推送异常: %s", err)

    """
    function: minute_hq_sync_task
    description: 每分钟行情快照任务
        1. 从股票池获取所有待监控的股票代码
        2. 从 rt_stocks_tbl 聚合窗口内（当前时间往前 minute_sync_interval 分钟）的实时行情记录，
           查询起点额外前移一段用于取累计值基线
        3. 逐只股票统计分钟行情：open/high/low/preclose 取窗口首条记录的当日字段，
           close 取窗口末条记录的 price；volume/amount 取当日累计值的窗口增量
        4. 组装 StockMinuteHqDao 记录，minute_time 取窗口开始分钟整点
        5. 保存到 minute_hq_tbl
        6. 通过 MQTT 推送到分频行情 topic（q_share/stock/minute）
    param {*} self
    return {*}
    """
    def minute_hq_sync_task(self):
        self.logger.info("开始执行每分钟行情快照任务...")
        # 1. 计算当前分钟时间
        start_time = datetime.now() - timedelta(minutes=self.minute_sync_interval)
        end_time = datetime.now() - timedelta(minutes=1)
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:00")
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:59")
        real_time_stocks = self.load_agent_pool_stocks()
        stocks_count = len(real_time_stocks)
        if stocks_count == 0:
            return
        self.logger.info(f"分时行情，需要处理股票数:{stocks_count}")
        codes_list = [s.get("code") for s in real_time_stocks if s.get("code", "") != ""]
        res, minute_records = self.do_minute_hq_sync_with_time(codes_list, start_time_str, end_time_str)
        if res and minute_records is not None:
            # 7. 通过 MQTT 推送分频行情
            self._publish_minute_hq(real_time_stocks, minute_records)

    def do_minute_hq_sync_with_time(
        self,
        codes: list,
        start_time: str,
        end_time: str,
        baseline_lookback_minutes: int = 30,
    ) -> tuple[bool, list|None]:
        """执行分钟行情快照同步（按指定时间）

        通过 query_rt_stocks 查询窗口内的实时行情记录，按 code 归组后手动统计
        分钟 OHLC 及指标：

        - volume / amount：实时快照的成交量、成交额是当日累计值，按行业口径取
          「窗口末条累计值 − 窗口开始前的末条累计值」；当日首窗以 0 为基线，
          因此包含早盘集合竞价的成交量
        - turnover / amp / qrr：求平均值

        :param codes: 股票代码列表，为空时查询窗口内全部实时行情记录
        :param start_time: 窗口左边界，格式 %Y-%m-%d %H:%M:%S
        :param end_time: 窗口右边界，格式 %Y-%m-%d %H:%M:%S
        :param baseline_lookback_minutes: 查询起点相对窗口左边界前移的分钟数，
            用于取「窗口开始前的末条累计值」作为基线
        :return: (是否成功, 分钟行情记录列表或 None)
        """
        minute_time = start_time
        minute_start = start_time
        minute_end = end_time

        minute_start_dt = datetime.strptime(minute_start, "%Y-%m-%d %H:%M:%S")
        # 查询起点前移，以便取到窗口开始前的末条累计值；同时不早于当日开盘，
        # 开盘前的快照累计值只含集合竞价成交量，不能当作连续竞价的基线
        session_open_dt = session_open_of(self.xshg, minute_start_dt.date())
        query_start_dt = minute_start_dt - timedelta(minutes=baseline_lookback_minutes)
        if query_start_dt < session_open_dt:
            query_start_dt = session_open_dt
        query_start = query_start_dt.strftime("%Y-%m-%d %H:%M:%S")
        # 窗口起点落在开盘后的第一个窗口内 → 当日首窗，基线取 0（含集合竞价成交量）
        first_window = minute_start_dt < session_open_dt + timedelta(minutes=self.minute_sync_interval)

        rt_impl = MongoRtStocksImpl()

        # 查询窗口内的实时行情记录（起点已前移，含基线部分）
        # codes 为空时 code="" 查询全部，非空时传入列表一次查询
        query_code: str | list[str] = codes if codes else ""
        # 去重
        query_code = list(set(query_code))
        res, all_rt_records = rt_impl.query_rt_stocks(
            code=query_code,
            start_time=query_start,
            end_time=minute_end,
        )
        if not res or all_rt_records is None or len(all_rt_records) == 0:
            self.logger.info("当前分钟无实时行情数据，跳过分钟行情快照")
            return False, None

        # 按窗口左边界切分：窗口内记录按 code 归组（纯数字归一化，避免同一只股票
        # 因 code 格式不同被拆成多组），窗口前的末条记录作为累计值基线
        grouped, baselines = split_minute_window_records(all_rt_records, minute_start)
        if len(grouped) == 0:
            self.logger.info("当前分钟无实时行情数据，跳过分钟行情快照")
            return False, None

        missing_baseline = len(grouped) - len([k for k in grouped if k in baselines])
        if missing_baseline > 0:
            self.logger.warning(
                "分钟行情未取到累计值基线, 窗口: %s ~ %s, 股票数: %d, 查询起点: %s, 当日首窗: %s",
                minute_start,
                minute_end,
                missing_baseline,
                query_start,
                first_window,
            )

        # 组装分频行情记录
        minute_records: list[dict] = []

        for pure_code, rt_records in grouped.items():
            count = len(rt_records)
            baseline_rec = baselines.get(pure_code)

            # open/high/low/preclose 取首条记录的当日字段（不是窗口内 price 极值），
            # close 取末条记录的 price，price 取首条记录的 price
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
                change_percent = first_rec.get("change_percent", round((agg_price - agg_preclose) / agg_preclose, 2))
            change_amount = first_rec.get("change_amount", round(agg_price - agg_preclose, 2))
            # 从末条记录取名称、原始 code
            name = last_rec.get("name", "")
            code = last_rec.get("code", pure_code)

            # volume, amount：当日累计值差分，窗口末条累计 − 窗口开始前的末条累计
            volume_diff, _ = cumulative_delta(
                rt_records, baseline_rec, "volume", first_window
            )
            amount_diff, _ = cumulative_delta(
                rt_records, baseline_rec, "amount", first_window
            )
            if volume_diff < 0 or amount_diff < 0:
                # 累计值回落（跨日残留、数据源异常）时按 0 处理，避免写入负数
                self.logger.warning(
                    "分钟行情累计值回落, code: %s, 窗口: %s ~ %s, volume: %s, amount: %s, 按 0 处理",
                    code,
                    minute_start,
                    minute_end,
                    volume_diff,
                    amount_diff,
                )
                volume_diff = max(volume_diff, 0)
                amount_diff = max(amount_diff, 0)

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
                "volume": volume_diff,
                "amount": amount_diff,
                "amp": amp_avg,
                "high": agg_high,
                "low": agg_low,
                "open": agg_open,
                "close": agg_close,
                "preclose": agg_preclose,
                "qrr": qrr_avg,
                "turnover": turnover_avg,
                "minute_time": minute_time,
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    def _publish_minute_hq(self, sync_stocks_list: list, minute_records: list[dict]) -> None:
        mqtt_client = AppContext().mqtt
        if not mqtt_client.is_connected:
            self.logger.warning("MQTT 未连接，跳过分钟行情推送")
            return
        try:
            for record in minute_records:
                code = record.get("code", "")
                if code == "":
                    continue
                pool_ids_list = self.find_pool_id_in_sync_stocks(code, sync_stocks_list)
                if not pool_ids_list or len(pool_ids_list) == 0:
                    continue
                payload = json.dumps(record, ensure_ascii=False)
                for pool_id in pool_ids_list:
                    topic = MqttTopic.stock_minute_pool_code(pool_id=pool_id, code = code)
                    if mqtt_client.publish(topic, payload):
                        self.logger.info(
                            "MQTT 分钟行情推送成功, topic: %s, payload: %s",
                            topic,
                            payload,
                        )
                    else:
                        self.logger.error("MQTT 分钟行情推送失败")
        except Exception as err:
            self.logger.error("MQTT 分钟行情推送异常: %s", err)

    """
    function: remove_real_stock_over_time
    description: 删除超过60天的实时股票数据,mongo中不保留过久的实时股票数据，避免数据过多，占用存储空间
    param {*} self
    return {*}
    """
    def remove_real_stock_over_time(self):
        self.logger.info("开始执行删除超过60天的实时股票数据任务...")
        # 删除超过60天的实时股票数据
        db_impl = MongoRtStocksImpl()
        recent_trading_day = self.get_recent_trading_day()
        sixty_days_ago = (datetime.strptime(recent_trading_day, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        db_impl.delete_rt_stocks(code="", start_time="", end_time=sixty_days_ago)

        # 删除超过60天的分钟行情数据
        self.logger.info("开始执行删除超过60天的分钟行情数据任务...")
        minute_impl = MongoStockMinuteHqImpl()
        minute_impl.delete_minute_hq(code="", end_time=sixty_days_ago)

        # 删除超过60天的历史K线数据
        self.logger.info("开始执行删除超过60天的历史K线数据任务...")
        hq_impl = MongoStockHisHqImpl()
        hq_impl.delete_day_hq(end_time=sixty_days_ago)
        hq_impl.delete_week_hq(end_time=sixty_days_ago)
        hq_impl.delete_month_hq(end_time=sixty_days_ago)

        # 删除超过30天的估值数据
        thirty_days_ago = (datetime.strptime(recent_trading_day, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info("开始执行删除超过30天的估值数据任务...")
        valuation_impl = MongoCompanyValuationImpl()
        valuation_impl.delete_company_valuation_by_date(report_date=thirty_days_ago)

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

    def load_agent_hot_industry_stocks(self) -> tuple[bool, Any | None]:
        cp = load_stock_cfg()
        agent_name = stock_instance_name(cp)
        agent_industry_impl = MongoDataAgentIndustryStocksImpl()
        return agent_industry_impl.query_by_agent_name(agent_name=agent_name)
                    
    """
    function: his_hq_sync_task
    description: 交易日15:00执行一次，补拉股票池与热门行业中尚未入库的日K线
    param {*} self
    return {*}
    """
    def his_hq_sync_task(self):
        self.logger.info("开始执行历史K线同步任务...")
        today = datetime.now().date()
        if not self.xshg.is_session(today):
            self.logger.info("非交易日，跳过历史K线同步")
            return
        start_day = (today - timedelta(days=3)).strftime("%Y-%m-%d")
        end_day = today.strftime("%Y-%m-%d")
        self.do_his_hq_sync_task(start_day, end_day)
        self.logger.info("历史K线同步任务完成")

    def get_hot_industry_stocks(self) -> list:
        # 先获取热门板块所有去重代码
        res, industry_stocks_list = self.load_agent_hot_industry_stocks()
        if not res or not industry_stocks_list:
            return []
        hot_codes = []
        for industry_stocks in industry_stocks_list:
            codes_industry_list = industry_stocks.get("stock_codes_industry", [])
            # 必须用 extend：用 = 赋值会被每轮循环覆盖，最终只剩最后一个行业的代码
            hot_codes.extend(s["code"] for s in codes_industry_list if s.get("code"))
        return hot_codes

    def get_industry_and_pool_stocks(self) -> list:
        # 先获取热门板块所有去重代码
        all_codes = self.get_hot_industry_stocks()
        pool_stocks_list = self.load_agent_pool_stocks()
        if pool_stocks_list and len(pool_stocks_list) > 0:
            all_codes.extend([s["code"] for s in pool_stocks_list if s.get("code")])
        # 再获取股票池中的去重代码
        # 去重
        all_codes = list(set(all_codes))
        return all_codes
    
    def do_his_hq_sync_task(self, start_day: str, end_day: str):
        # 先获取热门板块和股票池的所有去重代码
        all_codes = self.get_industry_and_pool_stocks()
        if len(all_codes) == 0:
            self.logger.info("股票中无有效代码，跳过历史K线同步")
            return
        # 只拉取「最近一个交易日的日线尚未入库」的股票：
        # 判断基准是最近交易日而非拉取区间，这样同一交易日内重复执行不会重复请求 API，
        # 同时区间内若有缺口（上次请求失败或进程被杀死），下一轮拉取 3 天窗口时会一并补齐。
        latest_session = self.get_recent_trading_day()
        missing_codes = self.filter_codes_without_day_hq(all_codes, latest_session)
        self.logger.info(
            "历史K线同步: 股票总数=%d, 最近交易日=%s, 已有日线=%d, 待拉取=%d, 拉取区间=%s ~ %s",
            len(all_codes),
            latest_session,
            len(all_codes) - len(missing_codes),
            len(missing_codes),
            start_day,
            end_day,
        )
        if len(missing_codes) == 0:
            self.logger.info("历史K线同步: 最近交易日日线已全部入库，无需拉取")
            return
        cp = load_stock_cfg()
        number_of_once = int(cp.get("tickflow", "history_hq_sync_number_of_once", fallback=1))
        work_interval = int(cp.get("tickflow", "history_hq_sync_interval", fallback=5))
        # 分批提交，批次间 sleep 控制 API 请求频率
        completed = 0
        total = len(missing_codes)
        total_batches = (total + number_of_once - 1) // number_of_once
        for i in range(0, total, number_of_once):
            batch_codes = missing_codes[i : i + number_of_once]
            batch_idx = i // number_of_once + 1
            self.logger.info(
                "历史K线同步: 批次 %d/%d, 股票数=%d, 代码=%s",
                batch_idx,
                total_batches,
                len(batch_codes),
                batch_codes,
            )
            # 直接同步调用：原实现 Thread.start()+join() 等于串行阻塞，
            # 线程只带来创建/销毁开销，没有任何并发收益，故移除。
            self._fetch_and_save_his_hq(batch_codes, start_day, end_day, "day")
            completed += len(batch_codes)
            self.logger.info(
                "历史K线同步进度: %d/%d", completed, total
            )
            # 批次间休眠，控制 API 请求频率，最后一轮不需要 sleep
            if batch_idx < total_batches:
                time.sleep(work_interval)

    """
    function: filter_codes_without_day_hq
    description: 筛选出在指定交易日尚未入库日K线的股票代码
    param {list[str]} codes: 待检查的股票代码列表
    param {str} session_day: 交易日，格式 %Y-%m-%d
    return {list[str]} 缺失日K线的股票代码列表
    """
    def filter_codes_without_day_hq(self, codes: list[str], session_day: str) -> list[str]:
        db_impl = MongoStockHisHqImpl()
        res, db_records = db_impl.query_day_hq(
            codes=codes, start_time=session_day, end_time=session_day
        )
        # 库内 code 为归一化后的格式（000001.SZ），调用方传入的可能是 000001 / sh000001，
        # 统一取纯数字比较，避免格式不一致导致已入库的股票被误判为缺失
        have_codes: set[str] = set()
        if res and db_records:
            for rec in db_records:
                matched = re.search(r"\d+", rec.get("code", ""))
                if matched:
                    have_codes.add(matched.group())
        missing_codes: list[str] = []
        for code in codes:
            matched = re.search(r"\d+", code)
            pure_code = matched.group() if matched else code
            if pure_code in have_codes:
                continue
            have_codes.add(pure_code)
            missing_codes.append(code)
        return missing_codes

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
                code = rec.get("code", "")
                if code:
                    db_codes.add(code)

        # 找出数据库中缺失的代码
        missing_codes: list[str] = [
            code for code in codes
            if code not in db_codes
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
            have_codes: list = [rec.get("code") for rec in db_records]
            db_codes = set(have_codes)

        missing_codes: list[str] = [
            code for code in codes
            if code not in db_codes
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
            have_codes: list = [rec.get("code") for rec in db_records]
            db_codes = set(have_codes)

        missing_codes: list[str] = [
            code for code in codes
            if code not in db_codes
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
        all_records: list[dict] = []
        res, records = db_impl.query_rt_stocks(
            code=codes,
            start_time=start_time,
            end_time=end_time
        )
        if res and records:
            all_records = list(records)

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
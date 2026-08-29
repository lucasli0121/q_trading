"""
Author: liguoqiang
Date: 2024-08-12 09:07:02
LastEditors: liguoqiang
LastEditTime: 2026-08-11
Description: q_trading_work 项目入口，读取配置文件，连接 MQTT，启动策略工作流。
"""

# coding="utf8"

import logging
import logging.config
import os
import signal
import sys
import threading
from configparser import ConfigParser
import yaml

from app_context import AppContext


def init_logger(config_path: str) -> None:
    """初始化日志系统。

    优先从 cfg/log.yaml 加载日志配置，文件不存在时使用 basicConfig 兜底。

    :param config_path: log.yaml 配置文件路径
    """
    # 确保 log 目录存在
    os.makedirs("log", exist_ok=True)
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config: dict = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s-%(name)s-%(lineno)s-%(levelname)s-%(message)s",
            filename="log/q_trading_work.log",
            filemode="w",
        )


def main() -> None:
    """主函数：读取配置，初始化日志，启动 MQTT 和策略工作流。

    流程：
    1. 读取 cfg/stock.cfg 配置
    2. 初始化日志
    3. 连接 MQTT 服务器
    4. 启动策略工作流（加载策略、订阅行情、启动定时器）
    5. 等待退出信号
    """
    cp: ConfigParser = ConfigParser()
    cp.read("cfg/stock.cfg", encoding="utf-8")
    cfg_name: str = cp.get("log", "config", fallback="cfg/log.yaml")

    # 初始化日志
    init_logger(cfg_name)
    logger: logging.Logger = logging.getLogger(__name__)
    logger.info("===== q_trading_work 启动中 =====")

    # 连接 MQTT
    logger.info("正在连接 MQTT...")
    if not AppContext().mqtt_client.connect():
        logger.error("MQTT 连接失败，程序退出")
        sys.exit(1)
    logger.info("MQTT 连接成功")

    # 启动策略工作流
    logger.info("正在启动策略工作流...")
    if not AppContext().strategy_workflow.start():
        logger.error("策略工作流启动失败，程序退出")
        AppContext().mqtt_client.disconnect()
        sys.exit(1)
    logger.info("策略工作流启动成功")

    # 等待退出信号（Ctrl+C 或 SIGTERM）
    stop_event: threading.Event = threading.Event()

    def _signal_handler(signum: int, frame: object) -> None:
        """处理退出信号。"""
        logger.info("收到退出信号 (signal=%d)，正在关闭...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("q_trading_work 运行中，按 Ctrl+C 退出")
    stop_event.wait()

    # 优雅关闭
    logger.info("===== q_trading_work 关闭中 =====")
    AppContext().strategy_workflow.stop()
    AppContext().mqtt_client.disconnect()
    logger.info("===== q_trading_work 已关闭 =====")


if __name__ in {"__main__", "__mp_main__"}:
    import multiprocessing
    multiprocessing.freeze_support()
    main()

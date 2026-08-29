'''
Author: liguoqiang
Date: 2024-08-12 09:07:02
LastEditors: liguoqiang
LastEditTime: 2024-08-24 09:53:18
Description: 项目main， 读取配置文件，启动scheduler，并启动web服务
'''

# coding="utf8"

import os
import shutil
import sys
import signal
import threading
from configparser import ConfigParser
import logging
import logging.config
from typing import Optional
import yaml
from app_context import AppContext


def _get_app_root() -> str:
    """获取应用根目录。

    PyInstaller 打包后 sys.frozen 为 True，可执行文件所在目录即为应用根目录；
    开发环境下则以 main.py 所在目录为根目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _init_frozen_env() -> None:
    """PyInstaller 冻结环境初始化。

    1. 切换到可执行文件所在目录，使所有相对路径引用 (cfg/stock.cfg 等) 保持兼容。
    2. 从 PyInstaller 的 _MEIPASS 中拷贝默认配置文件到运行目录（若不存在），
       方便用户修改配置后重启即可生效。
    3. 确保 log/ 目录存在。
    """
    if not getattr(sys, "frozen", False):
        return

    app_root = os.path.dirname(sys.executable)
    os.chdir(app_root)

    # 拷贝默认配置文件与 TLS 证书（若目标不存在）
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        for src_dir in ("cfg", "cert"):
            src_path = os.path.join(meipass, src_dir)
            dst_path = os.path.join(app_root, src_dir)
            if os.path.isdir(src_path) and not os.path.exists(dst_path):
                shutil.copytree(src_path, dst_path)

    # 确保日志目录存在
    log_dir = os.path.join(app_root, "log")
    os.makedirs(log_dir, exist_ok=True)

def initLogger(configPath):
    if os.path.exists(configPath):
        with open(configPath, "r", encoding="utf-8") as f:
            config = yaml.load(f, yaml.FullLoader)
            logging.config.dictConfig(config)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s-%(name)s-%(lineno)s-%(levelname)s-%(message)s",
            filename="log/q_share.log",
            filemode="w",
        )
"""
function: start_app
description: 启动APP，初始化 MQTT 连接并启动 web 服务
return {*}
"""
def start_app():
    logger = logging.getLogger(__name__)

    # 初始化 MQTT 连接
    mqtt_client = AppContext().mqtt
    if mqtt_client.connect():
        logger.info("MQTT 客户端连接成功")
    else:
        logger.warning("MQTT 客户端连接失败，继续启动 web 服务")

    # 启动 StockFetch 调度器（实时行情、分钟快照、行业同步等定时任务）
    logger.info("正在启动 StockFetch 调度器...")
    AppContext().stock_fetch.start()
    logger.info("StockFetch 调度器已启动")


"""
function: stop_app
description: 停止APP，断开 MQTT 连接并释放资源
return {*}
"""
def stop_app():
    logger = logging.getLogger(__name__)
    logger.info("正在停止应用...")
    try:
        logger.info("正在停止 StockFetch 调度器...")
        AppContext().stock_fetch.stop()
        logger.info("StockFetch 调度器已停止")
    except Exception as err:
        logger.error("StockFetch 停止失败: %s", err)
    try:
        AppContext().mqtt.disconnect()
        logger.info("MQTT 客户端已断开")
    except Exception as err:
        logger.error("MQTT 断开失败: %s", err)

'''
function:
description:
return {*}P
'''
if __name__ in {"__main__", "__mp_main__"}:
    # PyInstaller 冻结环境初始化：切换工作目录、拷贝默认配置、创建日志目录
    _init_frozen_env()

    cp = ConfigParser()
    cp.read("cfg/stock.cfg")
    cfgname = cp.get("log", "config")
    # 初始化日志
    initLogger(cfgname)
    start_app()

    # 阻塞主线程，等待退出信号后优雅关闭
    _stop_event = threading.Event()

    def _shutdown(signum, frame) -> None:
        """接收 SIGINT/SIGTERM 信号，触发优雅关闭"""
        logger = logging.getLogger(__name__)
        logger.info("收到退出信号 signum=%s，正在关闭...", signum)
        _stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger = logging.getLogger(__name__)
    logger.info("应用已启动，按 Ctrl+C 退出")
    _stop_event.wait()
    stop_app()


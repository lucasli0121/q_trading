"""配置文件定位与读取。

所有模块的配置读取统一走这里，不再各自硬编码 `cfg/stock.cfg`。

多实例部署时，同一份代码目录下并行启动多个进程，每个进程用环境变量
`Q_PROXY_CFG` 指向自己的配置文件，从而使用各自的 tickflow api_token、
`[data_proxy] name` 与 MQTT client_id 前缀：

    Q_PROXY_CFG=cfg/stock_1.cfg python main.py
    Q_PROXY_CFG=cfg/stock_2.cfg python main.py
    Q_PROXY_CFG=cfg/stock_3.cfg python main.py

未设置该环境变量时行为与改造前一致，读取工作目录下的 `cfg/stock.cfg`。
"""

import os
from configparser import ConfigParser

# 默认配置文件（相对进程工作目录）
DEFAULT_STOCK_CFG = "cfg/stock.cfg"
# 覆盖配置文件的的环境变量名
ENV_STOCK_CFG = "Q_PROXY_CFG"
# [data_proxy] name 缺失时的兜底实例名
FALLBACK_INSTANCE_NAME = "q_share_proxy_1"


def stock_cfg_path() -> str:
    """返回当前进程使用的配置文件路径。"""
    env_path = os.environ.get(ENV_STOCK_CFG, "").strip()
    return env_path or DEFAULT_STOCK_CFG


def load_stock_cfg() -> ConfigParser:
    """读取并返回配置文件。文件不存在时返回空 ConfigParser，由调用方用 fallback 兜底。"""
    cp = ConfigParser()
    cp.read(stock_cfg_path(), encoding="utf-8")
    return cp


def stock_instance_name(cp: ConfigParser | None = None) -> str:
    """返回当前实例名（`[data_proxy] name`），用于隔离 MQTT client_id 与日志文件名。"""
    cfg = cp if cp is not None else load_stock_cfg()
    name = cfg.get("data_proxy", "name", fallback="").strip()
    return name or FALLBACK_INSTANCE_NAME

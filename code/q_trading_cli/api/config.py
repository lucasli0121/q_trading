#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-06-27
Description: API 配置 — 从 cfg/stock.cfg 读取 [server] 节配置，
             提供后端 API 服务器的 base_url。
             包含 DNS 预解析，以兼容 PyInstaller 打包环境
             （PyInstaller 可能影响 glibc NSS 模块加载导致 DNS 解析失败）。
"""

import ipaddress
import logging
import socket
import subprocess
from configparser import ConfigParser, NoSectionError


def _resolve_via_getent(host: str) -> str | None:
    """使用系统 getent 命令解析域名（绕过 PyInstaller NSS 库加载问题）。

    PyInstaller 的 bootloader 可能影响 glibc NSS 模块加载，
    导致进程内 socket.getaddrinfo() 返回 [Errno -2]。
    但子进程使用独立的 glibc 环境，不受影响。

    :param host: 要解析的域名
    :return: 解析到的 IP 地址，失败返回 None
    """
    import shutil
    getent_path: str | None = shutil.which("getent")
    if getent_path is None:
        return None
    try:
        result = subprocess.run(
            [getent_path, "hosts", host],
            capture_output=True, text=True, timeout=5, check=False,
            env={"LD_LIBRARY_PATH": ""},  # 清除可能被 PyInstaller 污染的环境变量
        )
        if result.returncode == 0 and result.stdout.strip():
            # getent hosts 输出格式: "192.168.1.1 hostname.example.com hostname"
            ip_addr: str = result.stdout.strip().split()[0]
            # 验证是否为合法的 IP 地址
            ipaddress.ip_address(ip_addr)
            return ip_addr
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def _try_resolve_hostname(host: str, port: int) -> str:
    """尝试将域名解析为 IP 地址，解析失败时返回原域名。

    解析顺序：
    1. 如果已经是 IP 地址，直接返回
    2. 尝试 socket.getaddrinfo()（进程内 DNS）
    3. 如果进程内 DNS 失败，使用 getent 子进程作为兜底
    4. 全部失败则返回原域名（让 httpx 自行处理，此时可能会报错）

    :param host: 服务器地址（域名或 IP）
    :param port: 服务器端口（仅用于 getaddrinfo 的 hints）
    :return: 解析后的 IP 地址，如果已是 IP 或解析失败则返回原 host
    """
    # 如果已经是 IP 地址则直接返回
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    # 尝试进程内 DNS 解析
    try:
        for res in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
            ip: str = res[4][0]
            logging.getLogger(__name__).info("DNS 预解析(进程内): %s → %s", host, ip)
            return ip
    except socket.gaierror:
        logging.getLogger(__name__).debug("进程内 DNS 解析失败，尝试 getent 子进程")

    # 兜底：使用 getent 子进程解析（绕过 PyInstaller NSS 问题）
    ip = _resolve_via_getent(host)
    if ip:
        logging.getLogger(__name__).info("DNS 预解析(getent): %s → %s", host, ip)
        return ip

    logging.getLogger(__name__).warning("DNS 预解析完全失败，将使用原始域名 %s", host)
    return host


class ApiConfig:
    """API 服务器配置，从 cfg/stock.cfg [server] 节读取连接参数。

    在 PyInstaller 打包环境下，会自动将域名预解析为 IP 地址，
    以避免 NSS 模块加载失败导致 DNS 解析不可用的问题。

    Attributes:
        host: 原始服务器地址（配置文件中的值，未修改）
        port: 服务器端口
        resolved_host: DNS 预解析后的地址（IP 或原始域名）
        base_url: 完整 API 基础 URL，如 http://192.168.1.63:8000
    """

    def __init__(self, config_path: str = "") -> None:
        """初始化配置，从指定配置文件读取 [server] 节。

        :param config_path: 配置文件路径，空字符串表示使用默认路径 cfg/stock.cfg
        :raises NoSectionError: 配置文件缺失 [server] 节时抛出
        """
        from utils.tools import resource_path
        self.logger = logging.getLogger(__name__)
        cp = ConfigParser()
        # 配置含中文注释，Windows 默认 GBK 编码读取 UTF-8 文件会失败，统一指定 UTF-8
        cp.read(config_path or resource_path("cfg/stock.cfg"), encoding="utf-8")
        try:
            self.host: str = cp.get("server", "host")
            self.port: int = int(cp.get("server", "port"))
            # DNS 预解析：在 PyInstaller 环境下可能失败，回退到原域名
            self.resolved_host: str = _try_resolve_hostname(self.host, self.port)
        except NoSectionError as err:
            self.logger.error("not find section: %s", err)
            raise

    @property
    def base_url(self) -> str:
        """返回 API 基础 URL，使用预解析后的地址，格式为 http://{resolved_host}:{port}"""
        return f"http://{self.resolved_host}:{self.port}"

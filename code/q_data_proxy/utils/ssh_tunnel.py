"""MongoDB 的 SSH 本地端口转发（隧道）。

云服务器把 MongoDB 的默认端口对外屏蔽时，进程无法直连 `host:27017`，
只能先登录云服务器，再由服务器本机访问数据库。本模块用 paramiko +
sshtunnel 在进程内建立一条本地转发：

    本机 127.0.0.1:<local_bind_port>  --SSH-->  跳板机  -->  <remote_host>:<remote_port>

`MongoExec` 在建立连接前调用 `ensure_mongo_tunnel()`，隧道启用时把
MongoClient 的 host/port 换成隧道本地端点，其余代码无需改动。

配置示例（cfg/stock.cfg）：

    [ssh_tunnel]
    enable=true
    ssh_host=example.com
    ssh_port=2201
    ssh_username=lucasli
    ssh_private_key=~/.ssh/id_rsa
    remote_host=127.0.0.1
    remote_port=27017
    local_bind_port=0

注意：
1. `remote_host` / `remote_port` 是**跳板机视角**的 Mongo 地址。数据库若与
   跳板机同机，填 127.0.0.1:27017；若是另一台内网机器，填其内网地址。
2. `ssh_private_key` 只支持 OpenSSH 格式私钥，PuTTY 的 .ppk 需先转换。
3. `local_bind_port=0` 表示自动选择空闲端口。同一台机器跑多个实例时保持 0，
   各实例会各自占用一个端口；需要固定端口（便于外部工具复查）时显式指定。
4. 隧道断开后由看门狗线程自动重建，本地端口保持不变，pymongo 的既有连接
   在重连时会命中同一端口，不需要重启进程。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from configparser import ConfigParser
from dataclasses import dataclass
from typing import Optional

from utils.config import load_stock_cfg

# 隧道所在配置段
CFG_SECTION = "ssh_tunnel"
# 自动选择本地端口
AUTO_LOCAL_PORT = 0
# 隧道断开检测周期（秒），0 表示不启用看门狗
DEFAULT_CHECK_INTERVAL = 30
# SSH 连接超时（秒）
DEFAULT_CONNECT_TIMEOUT = 10


class SshTunnelError(RuntimeError):
    """隧道建立失败或配置非法。"""


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    """解析 INI 里的布尔值，兼容 true/yes/on/1 等写法。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "y", "t"):
        return True
    if text in ("0", "false", "no", "off", "n", "f"):
        return False
    return default


def _as_int(value: Optional[str], default: int) -> int:
    """解析整数，空值或非法值退回默认值。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _resolve_path(value: str) -> str:
    """展开 ~ 并转成绝对路径，空字符串原样返回。"""
    text = (value or "").strip()
    if not text:
        return ""
    return os.path.abspath(os.path.expanduser(text))


@dataclass
class SshTunnelConfig:
    """`[ssh_tunnel]` 段的解析结果。"""

    enable: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_password: str = ""
    ssh_private_key: str = ""
    ssh_private_key_password: str = ""
    remote_host: str = "127.0.0.1"
    remote_port: int = 27017
    local_bind_host: str = "127.0.0.1"
    local_bind_port: int = AUTO_LOCAL_PORT
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT
    check_interval: int = DEFAULT_CHECK_INTERVAL

    @classmethod
    def from_config(cls, cp: Optional[ConfigParser] = None) -> "SshTunnelConfig":
        """从配置文件读取隧道配置。配置段缺失时返回 enable=False 的默认值。"""
        cfg = cp if cp is not None else load_stock_cfg()

        def _get(key: str, fallback: str = "") -> str:
            return cfg.get(CFG_SECTION, key, fallback=fallback)

        return cls(
            enable=_as_bool(_get("enable", "false"), False),
            ssh_host=_get("ssh_host", "").strip(),
            ssh_port=_as_int(_get("ssh_port", "22"), 22),
            ssh_username=_get("ssh_username", "").strip(),
            ssh_password=_get("ssh_password", ""),
            ssh_private_key=_resolve_path(_get("ssh_private_key", "")),
            ssh_private_key_password=_get("ssh_private_key_password", ""),
            remote_host=_get("remote_host", "127.0.0.1").strip() or "127.0.0.1",
            remote_port=_as_int(_get("remote_port", "27017"), 27017),
            local_bind_host=_get("local_bind_host", "127.0.0.1").strip() or "127.0.0.1",
            local_bind_port=_as_int(_get("local_bind_port", "0"), AUTO_LOCAL_PORT),
            connect_timeout=_as_int(_get("connect_timeout", str(DEFAULT_CONNECT_TIMEOUT)), DEFAULT_CONNECT_TIMEOUT),
            check_interval=_as_int(_get("check_interval", str(DEFAULT_CHECK_INTERVAL)), DEFAULT_CHECK_INTERVAL),
        )

    @property
    def auth_desc(self) -> str:
        """认证方式描述，仅用于日志，不输出密钥内容。"""
        if self.ssh_private_key:
            return f"私钥 {os.path.basename(self.ssh_private_key)}"
        if self.ssh_password:
            return "密码"
        return "未配置"


class SshTunnel:
    """一条持久化的 SSH 本地转发，可自动重建。"""

    def __init__(self, cfg: SshTunnelConfig, logger: Optional[logging.Logger] = None) -> None:
        self.cfg = cfg
        self.logger = logger or logging.getLogger(__name__)
        self._server = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watchdog: Optional[threading.Thread] = None
        self._local_port = cfg.local_bind_port
        self._restart_count = 0

    # ------------------------------------------------------------------ 属性
    @property
    def local_host(self) -> str:
        """隧道本地监听地址，供 MongoClient 使用。"""
        return self.cfg.local_bind_host

    @property
    def local_port(self) -> int:
        """隧道本地监听端口。转发器已启动时以它实际绑定的端口为准。

        转发器断开后 `local_bind_port` 会抛 BaseSSHTunnelForwarderError，
        这里吞掉并退回上次记录的端口，保证重建流程能拿到对比基准。
        """
        server = self._server
        if server is not None:
            try:
                bound = server.local_bind_port
            except Exception:
                bound = None
            if bound:
                return int(bound)
        return int(self._local_port)

    @property
    def restart_count(self) -> int:
        """隧道重建次数，供监控观察链路稳定性。"""
        return self._restart_count

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> "SshTunnel":
        """建立隧道并启动看门狗；已存活则直接返回。"""
        with self._lock:
            if self.is_alive():
                return self
            self._start_locked()
        self.start_watchdog()
        return self

    def restart(self) -> "SshTunnel":
        """关闭后重建隧道，沿用原本地端口。"""
        with self._lock:
            old_port = self.local_port
            self._close_server()
            self._restart_count += 1
            self._start_locked()
            if self.local_port != old_port:
                # 原端口被占用时才会换端口，此时既有 MongoClient 指向旧端口，
                # 需要重启进程才能恢复，必须显式提示。
                self.logger.error(
                    "SSH 隧道重建后本地端口从 %s 变为 %s，已建立的 MongoDB 连接需重启进程才能恢复",
                    old_port,
                    self.local_port,
                )
            self.logger.warning(
                "SSH 隧道已重建（第 %d 次），本地端口 %s",
                self._restart_count,
                self.local_port,
            )
        return self

    def stop(self) -> None:
        """停止看门狗并关闭隧道。"""
        self._stop_event.set()
        watchdog = self._watchdog
        if watchdog is not None and watchdog.is_alive() and watchdog is not threading.current_thread():
            watchdog.join(timeout=5)
        self._watchdog = None
        with self._lock:
            self._close_server()
        self.logger.info("SSH 隧道已关闭")

    def is_alive(self) -> bool:
        """转发器活跃且本地端口可连通才算存活。"""
        server = self._server
        if server is None:
            return False
        try:
            if not server.is_active:
                return False
        except Exception:
            # 转发器已停止时部分属性会抛异常，按不可用处理
            return False
        try:
            with socket.create_connection((self.local_host, self.local_port), timeout=3):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------------ 内部实现
    def _start_locked(self) -> None:
        """建立转发器；自动选端口撞上端口占用时换端口重试一次。"""
        self._check_reachable()
        auto_port = not self.cfg.local_bind_port
        attempts = 2 if auto_port else 1
        last_err: Optional[Exception] = None
        for attempt in range(attempts):
            forwarder = self._build_forwarder()
            try:
                forwarder.start()
            except Exception as err:  # 端口占用、认证失败、网络不通都会走到这里
                last_err = err
                # 注意：不能在 start() 失败后调用 forwarder.stop()。
                # sshtunnel 在失败路径里已自行关闭 transport，此时再 stop() 会阻塞
                # 在内部的线程 join 上（0.5.1 实测永久挂住），把启动流程一起卡死。
                if attempt == 0 and auto_port and self._is_bind_error(err):
                    # 自动选到的端口被占用，换一个再试
                    self._local_port = 0
                    continue
                raise SshTunnelError(
                    f"SSH 隧道建立失败（{self.cfg.ssh_username}@{self.cfg.ssh_host}:{self.cfg.ssh_port}，"
                    f"认证方式 {self.cfg.auth_desc}）: {err}"
                ) from err
            self._server = forwarder
            self.logger.info(
                "SSH 隧道已建立: 本地 %s:%s -> %s:%s（跳板 %s@%s:%s，认证 %s）",
                self.local_host,
                self.local_port,
                self.cfg.remote_host,
                self.cfg.remote_port,
                self.cfg.ssh_username,
                self.cfg.ssh_host,
                self.cfg.ssh_port,
                self.cfg.auth_desc,
            )
            return
        raise SshTunnelError(f"SSH 隧道建立失败: {last_err}")

    @staticmethod
    def _is_bind_error(err: Exception) -> bool:
        """判断是否是本地端口占用，只有这种情况值得换端口重试。"""
        text = str(err).lower()
        return isinstance(err, OSError) or any(
            key in text for key in ("address already in use", "only one usage of each socket address", "10048")
        )

    def _check_reachable(self) -> None:
        """先探测 SSH 端口是否可达，把「网络不通」和「认证失败」区分开。"""
        if not self.cfg.ssh_host:
            raise SshTunnelError("[ssh_tunnel] ssh_host 未配置")
        try:
            with socket.create_connection(
                (self.cfg.ssh_host, self.cfg.ssh_port), timeout=self.cfg.connect_timeout
            ):
                return
        except OSError as err:
            raise SshTunnelError(
                f"SSH 端口不通: {self.cfg.ssh_host}:{self.cfg.ssh_port}（{err}），"
                "请检查 ssh_host/ssh_port 与网络策略"
            ) from err

    def _build_forwarder(self):
        """构造 SSHTunnelForwarder，延迟导入避免未启用时强制依赖 paramiko。"""
        try:
            from sshtunnel import SSHTunnelForwarder
        except ImportError as err:
            raise SshTunnelError(
                "未安装 sshtunnel/paramiko，无法建立 SSH 隧道，执行: pip install sshtunnel paramiko"
            ) from err

        if not self.cfg.ssh_host:
            raise SshTunnelError("[ssh_tunnel] ssh_host 未配置")
        if not self.cfg.ssh_username:
            raise SshTunnelError("[ssh_tunnel] ssh_username 未配置")
        key = self.cfg.ssh_private_key
        if key and not os.path.isfile(key):
            raise SshTunnelError(f"[ssh_tunnel] ssh_private_key 文件不存在: {key}")
        if not key and not self.cfg.ssh_password:
            raise SshTunnelError("[ssh_tunnel] 需要配置 ssh_private_key 或 ssh_password")

        if not self._local_port:
            self._local_port = self._pick_free_port()

        kwargs = {
            "ssh_address_or_host": (self.cfg.ssh_host, self.cfg.ssh_port),
            "ssh_username": self.cfg.ssh_username,
            "remote_bind_address": (self.cfg.remote_host, self.cfg.remote_port),
            "local_bind_address": (self.cfg.local_bind_host, self._local_port),
            # 空闲连接每 30 秒发一次 keepalive，避免被中间的 NAT/防火墙静默断开
            "set_keepalive": 30,
            "logger": self.logger,
            # 只用配置里指定的密钥/密码，不扫描 ssh-agent 与 ~/.ssh 下的默认密钥，
            # 否则认证失败时会逐个试本机其他私钥，既慢又可能触发无关密钥的告警
            "allow_agent": False,
            "host_pkey_directories": [],
            # 不读 ~/.ssh/config：host/port/user/key 全部由配置显式给出，行为可预期
            "ssh_config_file": None,
        }
        if key:
            kwargs["ssh_pkey"] = key
            if self.cfg.ssh_private_key_password:
                kwargs["ssh_private_key_password"] = self.cfg.ssh_private_key_password
        else:
            kwargs["ssh_password"] = self.cfg.ssh_password

        try:
            return SSHTunnelForwarder(**kwargs)
        except Exception as err:
            raise SshTunnelError(f"构造 SSH 隧道失败: {err}") from err

    def _pick_free_port(self) -> int:
        """在绑定地址上取一个当前空闲的端口。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((self.cfg.local_bind_host, 0))
            return int(sock.getsockname()[1])

    def _close_server(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            self._safe_stop(server)

    def _safe_stop(self, server) -> None:
        """在守护线程里关闭转发器并限时等待。

        sshtunnel 的 stop() 在转发器异常（transport 已断开）时可能阻塞在内部
        join 上，直接调用会卡死看门狗或退出流程，这里最多等 5 秒。
        """
        done = threading.Event()

        def _closer() -> None:
            try:
                server.stop()
            except Exception as err:
                self.logger.debug("关闭 SSH 转发器时出错: %s", err)
            finally:
                done.set()

        threading.Thread(target=_closer, name="ssh-tunnel-close", daemon=True).start()
        if not done.wait(5):
            self.logger.warning("关闭 SSH 转发器超时（5s），已放弃等待")

    # ------------------------------------------------------------------ 看门狗
    def start_watchdog(self) -> None:
        """启动后台线程，周期性检查隧道并在断开时重建。"""
        if self.cfg.check_interval <= 0:
            return
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop_event.clear()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="ssh-tunnel-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(self.cfg.check_interval):
            if self.is_alive():
                continue
            self.logger.warning("SSH 隧道连接中断，正在重建...")
            try:
                self.restart()
            except Exception as err:
                self.logger.error("SSH 隧道重建失败，%ss 后重试: %s", self.cfg.check_interval, err)


# ---------------------------------------------------------------------- 进程级单例
_tunnel_lock = threading.Lock()
_tunnel: Optional[SshTunnel] = None


def ensure_mongo_tunnel(cp: Optional[ConfigParser] = None) -> Optional[SshTunnel]:
    """按配置返回已启动的隧道；未启用时返回 None。

    重复调用只会启动一条隧道，MongoExec 等多处调用方共享同一实例。
    """
    global _tunnel
    cfg = SshTunnelConfig.from_config(cp)
    if not cfg.enable:
        return None

    with _tunnel_lock:
        if _tunnel is not None and _tunnel.is_alive():
            return _tunnel
        if _tunnel is not None:
            # 存在但已断开，直接重建而不是重复创建
            _tunnel.restart()
            return _tunnel
        tunnel = SshTunnel(cfg, logging.getLogger("ssh_tunnel"))
        tunnel.start()
        _tunnel = tunnel
        return tunnel


def stop_mongo_tunnel() -> None:
    """关闭进程级隧道，供应用退出时调用。"""
    global _tunnel
    with _tunnel_lock:
        tunnel = _tunnel
        _tunnel = None
    if tunnel is not None:
        tunnel.stop()


def mongo_tunnel_enabled(cp: Optional[ConfigParser] = None) -> bool:
    """隧道开关是否打开（不建立连接，供启动日志使用）。"""
    return SshTunnelConfig.from_config(cp).enable

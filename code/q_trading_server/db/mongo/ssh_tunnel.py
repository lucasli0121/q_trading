"""
Author: 臭臭
Date: 2026-11-15
Description: SSH 本地端口转发（等价 ssh -L），用于穿透只放开 SSH 的安全组访问 MongoDB。

背景
    云服务器只开放 SSH 端口，MongoDB 的 27017 不允许直连。本模块在进程内建一条
    SSH 隧道，把服务器的回环端口映射到本机回环端口，pymongo 连本地端口即可，
    上层 DAO / router 完全不需要感知。

设计要点
    1. 开关关闭时本模块不参与任何逻辑，MongoExec 仍直连 [mongo] host/port。
    2. 隧道是**进程级单例**：MongoExec 会被多次实例化（AppContext、独立脚本各一份），
       重复建隧道会白占 SSH 连接数并泄漏本地端口，统一走 ensure_tunnel()。
    3. 本地监听端口默认 0（内核分配空闲端口），避免固定端口被占用导致启动失败；
       需要固定端口时配 local_port。
    4. SSH 断线由后台监控线程重建 transport，本地监听 socket 不动。
       这样 pymongo 看到的连接地址始终不变，其自带的重连逻辑仍然有效。
    5. paramiko 延迟导入：未启用隧道时不导入，老部署即使没装 paramiko 也能启动。
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import logging
import os
import socket
import threading
from configparser import ConfigParser
from dataclasses import dataclass

DEFAULT_SSH_PORT = 22
DEFAULT_REMOTE_HOST = "127.0.0.1"
DEFAULT_LOCAL_BIND_HOST = "127.0.0.1"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_KEEPALIVE_INTERVAL = 30.0
DEFAULT_CONNECT_RETRY = 3
DEFAULT_RETRY_INTERVAL = 2.0
BUFFER_SIZE = 32 * 1024
LISTEN_BACKLOG = 64


class SshTunnelError(RuntimeError):
    """SSH 隧道配置错误或建立失败。

    单独定义一个异常类型，是为了让启动阶段能把它与 pymongo 的连接错误区分开：
    隧道建不起来时错误信息里要带「哪个配置项不对」，而不是一句 Connection refused。
    """


def _fingerprint(key_bytes: bytes) -> str:
    """主机密钥指纹，格式与 `ssh-keygen -l` 的 SHA256 一致。"""
    digest = hashlib.sha256(key_bytes).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _resolve_path(path: str) -> str:
    """展开 ~ 与相对路径，统一成绝对路径。

    相对路径按**调用时**的工作目录解析，与 cfg/stock.cfg 的相对定位保持同一基准；
    不在模块导入时缓存 cwd，因为打包后 main.py 会 chdir 到可执行文件目录。
    """
    expanded = os.path.expanduser(path.strip())
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return expanded


@dataclass(frozen=True)
class SshTunnelConfig:
    """隧道配置，字段与 cfg/stock.cfg 的 [mongo_ssh] 段一一对应。"""

    host: str
    user: str
    port: int = DEFAULT_SSH_PORT
    auth_method: str = "key"
    password: str = ""
    key_file: str = ""
    key_passphrase: str = ""
    remote_host: str = DEFAULT_REMOTE_HOST
    remote_port: int = 27017
    local_bind_host: str = DEFAULT_LOCAL_BIND_HOST
    local_port: int = 0
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL
    host_key_policy: str = "auto_add"
    known_hosts: str = ""

    @property
    def endpoint(self) -> str:
        """日志用的端点描述。"""
        return f"{self.user}@{self.host}:{self.port} -> {self.remote_host}:{self.remote_port}"

    @classmethod
    def from_config(cls, cp: ConfigParser, mongo_port: int = 27017) -> "SshTunnelConfig":
        """从 ConfigParser 构造配置。

        所有键都带 fallback：老配置文件没有 [mongo_ssh] 段时不会抛 NoSectionError。

        :param cp: 已 read 过 cfg/stock.cfg 的解析器
        :param mongo_port: [mongo] port，作为 remote_port 的缺省值
        :return: 隧道配置
        :raises SshTunnelError: 必填项缺失或取值非法
        """
        section = "mongo_ssh"

        def text(key: str, fallback: str = "") -> str:
            return (cp.get(section, key, fallback=fallback) or "").strip()

        host = text("host")
        user = text("user")
        if not host:
            raise SshTunnelError("已开启 SSH 隧道，但 cfg/stock.cfg 的 [mongo_ssh] host 未配置")
        if not user:
            raise SshTunnelError("已开启 SSH 隧道，但 cfg/stock.cfg 的 [mongo_ssh] user 未配置")

        auth_method = (text("auth_method", "key") or "key").lower()
        if auth_method not in ("key", "password"):
            raise SshTunnelError(
                f"[mongo_ssh] auth_method={auth_method!r} 不支持，只能是 key 或 password"
            )

        host_key_policy = (text("host_key_policy", "auto_add") or "auto_add").lower()
        if host_key_policy not in ("auto_add", "reject"):
            raise SshTunnelError(
                f"[mongo_ssh] host_key_policy={host_key_policy!r} 不支持，只能是 auto_add 或 reject"
            )

        key_file = text("key_file")
        key_passphrase = text("key_passphrase")
        password = text("password")
        if auth_method == "key":
            if not key_file:
                raise SshTunnelError("已开启 SSH 隧道且 auth_method=key，但 [mongo_ssh] key_file 未配置")
            key_file = _resolve_path(key_file)
            if not os.path.isfile(key_file):
                raise SshTunnelError(f"SSH 私钥文件不存在: {key_file}")
        elif not password:
            raise SshTunnelError("已开启 SSH 隧道且 auth_method=password，但 [mongo_ssh] password 未配置")

        known_hosts = text("known_hosts", "~/.ssh/known_hosts")
        if known_hosts:
            known_hosts = _resolve_path(known_hosts)

        return cls(
            host=host,
            user=user,
            port=_as_int("[mongo_ssh] port", text("port", str(DEFAULT_SSH_PORT))),
            auth_method=auth_method,
            password=password,
            key_file=key_file,
            key_passphrase=key_passphrase,
            remote_host=text("remote_host", DEFAULT_REMOTE_HOST) or DEFAULT_REMOTE_HOST,
            remote_port=_as_int("[mongo_ssh] remote_port", text("remote_port", str(mongo_port))),
            local_bind_host=text("local_bind_host", DEFAULT_LOCAL_BIND_HOST) or DEFAULT_LOCAL_BIND_HOST,
            local_port=_as_int("[mongo_ssh] local_port", text("local_port", "0")),
            connect_timeout=_as_float(
                "[mongo_ssh] connect_timeout", text("connect_timeout", str(DEFAULT_CONNECT_TIMEOUT))
            ),
            keepalive_interval=_as_float(
                "[mongo_ssh] keepalive_interval",
                text("keepalive_interval", str(DEFAULT_KEEPALIVE_INTERVAL)),
            ),
            host_key_policy=host_key_policy,
            known_hosts=known_hosts,
        )


def _as_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise SshTunnelError(f"{name} 必须是整数，当前值 {raw!r}") from exc


def _as_float(name: str, raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise SshTunnelError(f"{name} 必须是数字，当前值 {raw!r}") from exc


def _shutdown_write(obj: object) -> None:
    """半关闭写方向。

    socket 用 shutdown(SHUT_WR)，paramiko Channel 用 shutdown_write()。
    只关写不关读，避免一个方向结束后把另一个方向的在途数据截断。
    """
    try:
        if hasattr(obj, "shutdown_write"):
            obj.shutdown_write()  # type: ignore[attr-defined]
        elif hasattr(obj, "shutdown"):
            obj.shutdown(socket.SHUT_WR)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _pump(src: object, dst: object) -> None:
    """单向搬运字节，直到 src 读到 EOF 或任一方向异常。"""
    try:
        while True:
            data = src.recv(BUFFER_SIZE)  # type: ignore[attr-defined]
            if not data:
                break
            dst.sendall(data)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    finally:
        _shutdown_write(dst)


class SshTunnel:
    """一条本地端口转发隧道。

    生命周期：start() 后一直工作，close() 释放。不要直接 new，用 ensure_tunnel()。
    """

    def __init__(self, config: SshTunnelConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.local_host = config.local_bind_host
        self.local_port = 0
        self._listen_sock: socket.socket | None = None
        self._transport = None
        self._closed = threading.Event()

    # ---------------------------------------------------------------- 对外接口

    def start(self) -> tuple[str, int]:
        """建立隧道并开始监听。

        :return: 本地 (host, port)，pymongo 用这个地址连接
        :raises SshTunnelError: SSH 连接、认证、绑定端口任一环节失败
        """
        try:
            self._bind()
            self._transport = self._connect_with_retry()
            self._spawn(self._accept_loop, "ssh-tunnel-accept")
            self._spawn(self._monitor_loop, "ssh-tunnel-monitor")
        except Exception:
            self.close()
            raise
        self.logger.info(
            "SSH 隧道已建立：本地 %s:%s → %s（%s 认证）",
            self.local_host,
            self.local_port,
            self.config.endpoint,
            self.config.auth_method,
        )
        return self.local_host, self.local_port

    def close(self) -> None:
        """关闭监听 socket 与 SSH 连接。可重复调用。"""
        self._closed.set()
        sock, self._listen_sock = self._listen_sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_alive(self) -> bool:
        """本地监听是否还在、SSH 连接是否可用。"""
        transport = self._transport
        return (
            not self._closed.is_set()
            and self._listen_sock is not None
            and transport is not None
            and transport.is_active()
        )

    # ---------------------------------------------------------------- 内部实现

    def _bind(self) -> None:
        """绑定本地监听端口。local_port=0 时由内核分配。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "posix":
            # Linux 上允许快速重启；Windows 的 SO_REUSEADDR 语义是「抢占端口」，不能设。
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.config.local_bind_host, self.config.local_port))
            sock.listen(LISTEN_BACKLOG)
        except OSError as exc:
            sock.close()
            raise SshTunnelError(
                f"SSH 隧道本地端口 {self.config.local_bind_host}:{self.config.local_port} 绑定失败: {exc}"
                "（local_port=0 表示由系统分配，可避免端口占用）"
            ) from exc
        self._listen_sock = sock
        self.local_port = sock.getsockname()[1]

    def _connect_with_retry(self):
        """连接 + 握手 + 认证，失败重试 DEFAULT_CONNECT_RETRY 次。"""
        last_error: Exception | None = None
        for attempt in range(1, DEFAULT_CONNECT_RETRY + 1):
            try:
                return self._connect_once()
            except SshTunnelError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < DEFAULT_CONNECT_RETRY:
                    self.logger.warning(
                        "SSH 隧道连接失败（第 %d/%d 次）：%s，%s 秒后重试",
                        attempt,
                        DEFAULT_CONNECT_RETRY,
                        exc,
                        DEFAULT_RETRY_INTERVAL,
                    )
                    if self._closed.wait(DEFAULT_RETRY_INTERVAL):
                        break
        raise SshTunnelError(
            f"连接 SSH 服务器 {self.config.host}:{self.config.port} 失败: {last_error}"
        )

    def _connect_once(self):
        import paramiko  # 延迟导入，未启用隧道时无需安装

        cfg = self.config
        sock = socket.create_connection((cfg.host, cfg.port), timeout=cfg.connect_timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = cfg.connect_timeout
        transport.handshake_timeout = cfg.connect_timeout
        transport.auth_timeout = cfg.connect_timeout
        try:
            transport.start_client(timeout=cfg.connect_timeout)
            self._verify_host_key(transport)
            self._authenticate(transport)
            if cfg.keepalive_interval > 0:
                transport.set_keepalive(int(cfg.keepalive_interval))
        except SshTunnelError:
            transport.close()
            raise
        except Exception as exc:  # noqa: BLE001
            transport.close()
            raise SshTunnelError(
                f"SSH 握手/认证失败（{cfg.endpoint}）：{exc}"
            ) from exc
        return transport

    def _verify_host_key(self, transport) -> None:
        """校验服务器主机密钥。

        auto_add：known_hosts 里没有该主机时信任并记录指纹（首次部署友好，有告警）。
        reject：必须已在 known_hosts 中，否则拒绝连接。
        两种策略下「已记录但不匹配」都直接拒绝，防止中间人。
        """
        import paramiko

        cfg = self.config
        key = transport.get_remote_server_key()
        lookup = cfg.host if cfg.port == DEFAULT_SSH_PORT else f"[{cfg.host}]:{cfg.port}"
        fingerprint = _fingerprint(key.asbytes())

        recorded = None
        if cfg.known_hosts and os.path.isfile(cfg.known_hosts):
            try:
                entries = paramiko.HostKeys(cfg.known_hosts).lookup(lookup) or {}
                recorded = entries.get(key.get_name())
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("读取 known_hosts 失败（%s）：%s", cfg.known_hosts, exc)

        if recorded is not None:
            if recorded.asbytes() != key.asbytes():
                raise SshTunnelError(
                    f"SSH 主机 {lookup} 的主机密钥与 known_hosts 记录不一致（{fingerprint}），"
                    "可能存在中间人攻击；确认服务器更换过密钥后请先清理 known_hosts 中的旧记录"
                )
            return

        if cfg.host_key_policy == "reject":
            raise SshTunnelError(
                f"SSH 主机 {lookup} 的主机密钥未记录在 {cfg.known_hosts or 'known_hosts'}，"
                "按 host_key_policy=reject 拒绝连接"
            )
        self.logger.warning(
            "SSH 主机 %s 的主机密钥未在 known_hosts 中记录，按 auto_add 策略信任（指纹 %s）",
            lookup,
            fingerprint,
        )

    def _authenticate(self, transport) -> None:
        """认证。

        paramiko 的 auth_* 返回「服务器还允许的认证方式列表」：认证成功时是空列表
        或 None，失败时是 ['publickey'] 这类非空列表。所以判据必须是「非空即失败」，
        写成 `is not None` 会把成功当失败。
        """
        import paramiko

        cfg = self.config
        try:
            if cfg.auth_method == "password":
                allowed = transport.auth_password(cfg.user, cfg.password)
            else:
                allowed = transport.auth_publickey(cfg.user, self._load_pkey())
        except paramiko.AuthenticationException as exc:
            # paramiko 在这里抛的是光秃秃的 Authentication failed，看不出该改什么
            raise SshTunnelError(
                f"SSH 认证被拒绝（{cfg.user}@{cfg.host}:{cfg.port}，方式 {cfg.auth_method}）。"
                "常见原因：公钥未加入服务器 authorized_keys、用户名不对、"
                "私钥与服务器上登记的不是同一把、或服务器只允许密码认证"
            ) from exc
        if allowed:
            raise SshTunnelError(
                f"SSH 认证失败（{cfg.user}@{cfg.host}:{cfg.port}，方式 {cfg.auth_method}），"
                f"服务器允许的方式: {allowed}"
            )

    def _load_pkey(self):
        """加载私钥，把「需要口令」「格式不对」翻译成可操作的提示。"""
        import paramiko

        cfg = self.config
        # paramiko 的 PKey.from_path 要求口令是 bytes，传 str 会报 "password must be bytes"
        passphrase = cfg.key_passphrase.encode("utf-8") if cfg.key_passphrase else None
        try:
            return paramiko.PKey.from_path(cfg.key_file, passphrase)
        except paramiko.PasswordRequiredException as exc:
            raise SshTunnelError(
                f"SSH 私钥 {cfg.key_file} 已加密，请在 [mongo_ssh] key_passphrase 配置口令"
            ) from exc
        except paramiko.SSHException as exc:
            raise SshTunnelError(
                f"加载 SSH 私钥失败（{cfg.key_file}）：{exc}；"
                "私钥若带口令请在 [mongo_ssh] key_passphrase 配置口令"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # 口令错误时 cryptography 抛的是 ValueError("Could not deserialize key data ... bad decrypt")，
            # 原文对排查没有帮助，统一补上「去改哪个配置项」。
            raise SshTunnelError(
                f"加载 SSH 私钥失败（{cfg.key_file}）：{exc}；"
                "私钥若带口令，请核对 [mongo_ssh] key_passphrase（口令错误也会走到这里）"
            ) from exc

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, daemon=True, name=name)
        thread.start()

    def _accept_loop(self) -> None:
        """接受本地连接，为每个连接开一条 direct-tcpip 通道。"""
        sock = self._listen_sock
        if sock is None:
            return
        while not self._closed.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return  # socket 已关闭，正常退出
            self._spawn(lambda c=conn, a=addr: self._bridge(c, a), "ssh-tunnel-bridge")

    def _bridge(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        """把一条本地连接桥接到 SSH 通道。"""
        cfg = self.config
        transport = self._transport
        if transport is None or not transport.is_active():
            conn.close()
            return
        try:
            chan = transport.open_channel(
                "direct-tcpip",
                (cfg.remote_host, cfg.remote_port),
                addr,
                timeout=cfg.connect_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            # 这里失败通常是 mongod 未监听或隧道刚断，pymongo 会自行重试，不升级成 ERROR
            self.logger.warning(
                "SSH 隧道转发失败（%s:%s）：%s", cfg.remote_host, cfg.remote_port, exc
            )
            conn.close()
            return
        self._spawn(lambda: _pump(conn, chan), "ssh-tunnel-up")
        self._spawn(lambda: _pump(chan, conn), "ssh-tunnel-down")

    def _monitor_loop(self) -> None:
        """监控 SSH 连接，断开后重建，本地监听端口保持不变。"""
        cfg = self.config
        interval = cfg.keepalive_interval if cfg.keepalive_interval > 0 else DEFAULT_KEEPALIVE_INTERVAL
        interval = max(interval, 5.0)
        while not self._closed.wait(interval):
            transport = self._transport
            if transport is not None and transport.is_active():
                continue
            self.logger.warning("SSH 隧道连接已断开，正在重建（%s）", cfg.endpoint)
            try:
                new_transport = self._connect_with_retry()
            except SshTunnelError as exc:
                self.logger.error("SSH 隧道重建失败，下个周期继续重试：%s", exc)
                continue
            old_transport, self._transport = self._transport, new_transport
            if old_transport is not None:
                try:
                    old_transport.close()
                except Exception:  # noqa: BLE001
                    pass
            self.logger.info("SSH 隧道已重建，本地端口 %s 保持不变", self.local_port)


# ------------------------------------------------------------------ 进程级单例

_TUNNEL_LOCK = threading.Lock()
_TUNNEL: SshTunnel | None = None


def ensure_tunnel(config: SshTunnelConfig) -> tuple[str, int]:
    """获取（必要时建立）进程级隧道。

    :param config: 隧道配置，仅首次调用时生效
    :return: 本地 (host, port)
    """
    global _TUNNEL
    with _TUNNEL_LOCK:
        if _TUNNEL is not None and _TUNNEL.is_alive:
            return _TUNNEL.local_host, _TUNNEL.local_port
        if _TUNNEL is not None:
            # 曾经建过但已失效（监听线程异常退出），关掉重来
            _TUNNEL.close()
            _TUNNEL = None
        tunnel = SshTunnel(config)
        host, port = tunnel.start()
        _TUNNEL = tunnel
        return host, port


def close_tunnel() -> None:
    """关闭进程级隧道。可重复调用。"""
    global _TUNNEL
    with _TUNNEL_LOCK:
        tunnel, _TUNNEL = _TUNNEL, None
    if tunnel is not None:
        tunnel.close()


def active_tunnel() -> SshTunnel | None:
    """当前进程级隧道实例，供健康检查与测试使用。"""
    return _TUNNEL


atexit.register(close_tunnel)

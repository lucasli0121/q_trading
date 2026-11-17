"""SSH 本地端口转发，给「mongo 不对外」的云服务器用。

背景：云服务器的安全组只放行 SSH，mongo 的 27017 不对公网开放。归档进程
在本机监听一个本地端口，把接入的连接通过 SSH 的 ``direct-tcpip`` 通道送到
服务器侧的 mongo。对 ``MongoClient`` 而言只是一个普通的本地端口，不需要
改连接串格式，也不需要目标机安装 mongo 客户端。

为什么用 paramiko 而不是调系统 ssh：
- 部署产物是 PyInstaller onedir，目标机不一定装了 ssh 客户端，也不一定有
  ssh-agent；paramiko 是纯 Python 依赖，能随包一起走。
- 进程内可以拿到连接状态，断开时能重连并留下日志，而不是依赖子进程的
  退出码。子进程方案在容器里还会多一层进程管理。

线程模型：一个后台线程只负责 accept；每个连接一个线程做双向泵送。
paramiko 的 ``Transport`` 支持多线程 ``open_channel``，内部有锁。
"""

from __future__ import annotations

import logging
import select
import socket
import socketserver
import threading
from typing import Any

from .config import SshTunnelConfig

LOCAL_HOST = "127.0.0.1"
_BUFSIZE = 65536
_SELECT_TIMEOUT = 1.0


class SshTunnelError(RuntimeError):
    """隧道建立失败。调用方应立即中止，不要降级成「直连试试」。"""


class _ForwardServer(socketserver.ThreadingTCPServer):
    """本地监听。端口由系统分配时，从 ``server_address`` 取实际值。"""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], tunnel: "SshTunnel") -> None:
        self.tunnel = tunnel
        super().__init__(address, _ForwardHandler)


class _ForwardHandler(socketserver.BaseRequestHandler):
    """把一条本地 TCP 连接接到 SSH 通道上。"""

    def handle(self) -> None:
        tunnel: SshTunnel = self.server.tunnel  # type: ignore[attr-defined]
        try:
            channel = tunnel.open_channel(self.request.getpeername())
        except Exception as exc:  # noqa: BLE001 - 单条连接失败不该拖垮监听
            tunnel.logger.warning("SSH 通道建立失败: %s", exc)
            return
        try:
            _pump(self.request, channel)
        finally:
            try:
                channel.close()
            except Exception:  # pragma: no cover - 关闭失败无补救手段
                pass


def _pump(sock: socket.socket, channel: Any) -> None:
    """双向转发，直到任意一端关闭。

    用 ``select`` 而不是两个线程：一条连接一个线程已经够用，再拆成两半会
    让关闭路径变复杂（一端关闭要能叫醒另一端）。
    """
    while True:
        readable, _, _ = select.select([sock, channel], [], [], _SELECT_TIMEOUT)
        if sock in readable:
            data = sock.recv(_BUFSIZE)
            if not data:
                return
            channel.sendall(data)
        if channel in readable:
            data = channel.recv(_BUFSIZE)
            if not data:
                return
            sock.sendall(data)


class SshTunnel:
    """一条 SSH 连接 + 一个本地监听端口。

    生命周期：``start()`` 建立连接并开始监听，``stop()`` 关闭监听与连接。
    ``start()`` 可重复调用，返回同一个本地端点。
    """

    def __init__(
        self,
        cfg: SshTunnelConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._cfg = cfg
        self.logger = logger or logging.getLogger("archive.tunnel")
        self._lock = threading.RLock()
        self._client: Any = None
        self._transport: Any = None
        self._server: _ForwardServer | None = None
        self._local_port: int = 0

    # ---- 状态 ----

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def endpoint(self) -> tuple[str, int]:
        return LOCAL_HOST, self._local_port

    def is_running(self) -> bool:
        return self._server is not None

    # ---- 生命周期 ----

    def start(self) -> tuple[str, int]:
        """建立隧道并开始监听，返回 ``(本地地址, 本地端口)``。"""
        with self._lock:
            if self._server is not None:
                return self.endpoint

            self._connect()

            try:
                server = _ForwardServer((LOCAL_HOST, self._cfg.local_port), self)
            except OSError as exc:
                self._close_client_locked()
                raise SshTunnelError(
                    "本地端口 %d 无法监听: %s" % (self._cfg.local_port, exc)
                ) from exc

            self._server = server
            self._local_port = int(server.server_address[1])
            thread = threading.Thread(
                target=server.serve_forever,
                name="ssh-tunnel-accept",
                daemon=True,
            )
            thread.start()
            self.logger.info(
                "SSH 隧道就绪: %s:%d -> %s:%d（经 %s:%d，用户 %s）",
                LOCAL_HOST,
                self._local_port,
                self._cfg.remote_host,
                self._cfg.remote_port,
                self._cfg.host,
                self._cfg.port,
                self._cfg.user,
            )
            return self.endpoint

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.shutdown()
                    self._server.server_close()
                except Exception:  # pragma: no cover - 退出路径尽力而为
                    pass
                self._server = None
            self._close_client_locked()
            self._local_port = 0

    # ---- 连接 ----

    def _connect(self) -> None:
        """建立 SSH 连接。paramiko 延迟导入，隧道关闭时不要求安装。"""
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - 打包缺失时才有意义
            raise SshTunnelError(
                "SSH 隧道已启用但缺少 paramiko，请安装 requirements.txt"
            ) from exc

        client = paramiko.SSHClient()
        if self._cfg.strict_host_key_checking:
            client.load_system_host_keys()
            if self._cfg.known_hosts:
                try:
                    client.load_host_keys(self._cfg.known_hosts)
                except OSError as exc:
                    raise SshTunnelError(
                        "读取 known_hosts 失败: %s（%s）" % (self._cfg.known_hosts, exc)
                    ) from exc
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.logger.warning(
                "SSH 主机密钥校验已关闭（strict_host_key_checking=false），"
                "生产环境建议打开并配置 known_hosts"
            )

        timeout = max(int(self._cfg.connect_timeout), 1)
        kwargs: dict[str, Any] = {
            "hostname": self._cfg.host,
            "port": self._cfg.port,
            "username": self._cfg.user,
            "timeout": timeout,
            "banner_timeout": max(timeout, 15),
            "auth_timeout": max(timeout, 15),
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self._cfg.key_file:
            kwargs["key_filename"] = self._cfg.key_file
            if self._cfg.key_passphrase:
                kwargs["passphrase"] = self._cfg.key_passphrase
        if self._cfg.password:
            kwargs["password"] = self._cfg.password

        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成可读错误
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass
            raise SshTunnelError(
                "SSH 连接失败 %s@%s:%d: %s"
                % (self._cfg.user, self._cfg.host, self._cfg.port, exc)
            ) from exc

        transport = client.get_transport()
        if transport is None or not transport.is_active():
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass
            raise SshTunnelError("SSH 连接建立后 transport 不可用")

        if self._cfg.keepalive > 0:
            transport.set_keepalive(int(self._cfg.keepalive))

        self._client = client
        self._transport = transport
        self.logger.info(
            "SSH 已连接 %s@%s:%d", self._cfg.user, self._cfg.host, self._cfg.port
        )

    def _close_client_locked(self) -> None:
        transport, client = self._transport, self._client
        self._transport = None
        self._client = None
        if transport is not None:
            try:
                transport.close()
            except Exception:  # pragma: no cover
                pass
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass

    def _ensure_transport_locked(self) -> Any:
        """连接断了就重连一次。归档是常驻进程，跨天之后连接可能已被回收。"""
        if self._transport is not None and self._transport.is_active():
            return self._transport
        self.logger.warning("SSH 连接已断开，尝试重连")
        self._close_client_locked()
        self._connect()
        return self._transport

    def open_channel(self, peer: tuple[str, int] | Any) -> Any:
        """为一条本地连接打开 SSH 通道。"""
        with self._lock:
            transport = self._ensure_transport_locked()
            try:
                return transport.open_channel(
                    "direct-tcpip",
                    (self._cfg.remote_host, self._cfg.remote_port),
                    tuple(peer) if peer else (LOCAL_HOST, 0),
                    timeout=max(int(self._cfg.connect_timeout), 1),
                )
            except Exception as exc:  # noqa: BLE001
                raise SshTunnelError(
                    "无法转发到 %s:%d: %s"
                    % (self._cfg.remote_host, self._cfg.remote_port, exc)
                ) from exc

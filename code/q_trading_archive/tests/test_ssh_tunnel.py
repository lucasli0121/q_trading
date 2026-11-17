"""SSH 隧道：本地起一个真实 SSH 服务端做端到端验证。

不连外网：测试内用 paramiko 起一个最小 SSH 服务端（密码认证 + 允许
direct-tcpip），后面接一个回显服务。隧道的职责就是把「本地连接」变成
「SSH 通道里的字节流」，这条链路能跑通，说明 OpenSSH 服务端上同样成立。

守四件事：
1. 转发确实工作：写进去的字节能原样回来；
2. 隧道用系统分配的端口，且重复 start 不重复监听；
3. stop 之后本地端口关闭，不再接受连接；
4. MongoReader 只在开关打开时接隧道，关闭时不尝试任何 SSH 连接。
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading
import unittest
from dataclasses import replace

import paramiko

from archive.config import MongoConfig, SshTunnelConfig
from archive.mongo_reader import MongoReader
from archive.ssh_tunnel import SshTunnel

PASSWORD = "test-password"
USER = "tester"


class _EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            data = self.request.recv(4096)
            if not data:
                return
            self.request.sendall(data)


class _SshServerInterface(paramiko.ServerInterface):
    """最小 SSH 服务端：只支持密码认证与 direct-tcpip。"""

    def __init__(self) -> None:
        self.destination: tuple[str, int] | None = None

    def check_auth_password(self, username: str, password: str) -> int:
        if username == USER and password == PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination) -> int:
        self.destination = destination
        return paramiko.OPEN_SUCCEEDED


class _SshServer:
    """在随机端口上跑一个 SSH 服务端，把 direct-tcpip 转到 ``target``。"""

    def __init__(self, target_port: int) -> None:
        self._target_port = target_port
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(5)
        self.port: int = self._listener.getsockname()[1]
        self._host_key = paramiko.RSAKey.generate(2048)
        self._transports: list[paramiko.Transport] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._serve, name="fake-sshd", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """每个用例都可能新建一条隧道，这里必须持续 accept。"""
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handle, args=(conn,), name="fake-sshd-conn", daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(self._host_key)
        try:
            transport.start_server(server=_SshServerInterface())
            channel = transport.accept(10)
        except Exception:  # noqa: BLE001 - 测试清理阶段连接被关是正常路径
            transport.close()
            return
        if channel is None:
            transport.close()
            return
        with self._lock:
            self._transports.append(transport)
        upstream = socket.create_connection(("127.0.0.1", self._target_port), timeout=5)
        try:
            self._pump(channel, upstream)
        finally:
            upstream.close()
            channel.close()
            transport.close()

    @staticmethod
    def _pump(channel: paramiko.Channel, upstream: socket.socket) -> None:
        while True:
            readable, _, _ = select.select([channel, upstream], [], [], 1.0)
            if channel in readable:
                data = channel.recv(4096)
                if not data:
                    return
                upstream.sendall(data)
            if upstream in readable:
                data = upstream.recv(4096)
                if not data:
                    return
                channel.sendall(data)

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            transports = list(self._transports)
            self._transports.clear()
        for transport in transports:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass


class TunnelTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.echo = _EchoServer(("127.0.0.1", 0), _EchoHandler)
        cls.echo_port: int = cls.echo.server_address[1]
        cls.echo_thread = threading.Thread(target=cls.echo.serve_forever, daemon=True)
        cls.echo_thread.start()

        cls.sshd = _SshServer(cls.echo_port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.sshd.close()
        cls.echo.shutdown()
        cls.echo.server_close()

    def tunnel_config(self, **overrides) -> SshTunnelConfig:
        base = SshTunnelConfig(
            enabled=True,
            host="127.0.0.1",
            port=self.sshd.port,
            user=USER,
            key_file="",
            key_passphrase="",
            password=PASSWORD,
            remote_host="127.0.0.1",
            remote_port=self.echo_port,
            local_port=0,
            connect_timeout=5,
            keepalive=0,
            strict_host_key_checking=False,
            known_hosts="",
        )
        return replace(base, **overrides)


class SshTunnelTest(TunnelTestBase):
    def test_forwarding_round_trip(self) -> None:
        tunnel = SshTunnel(self.tunnel_config())
        host, port = tunnel.start()
        try:
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 0)

            with socket.create_connection((host, port), timeout=5) as sock:
                payload = b"archive-tunnel-check-12345"
                sock.sendall(payload)
                received = b""
                while len(received) < len(payload):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    received += chunk
            self.assertEqual(received, payload)
        finally:
            tunnel.stop()

    def test_start_is_idempotent(self) -> None:
        tunnel = SshTunnel(self.tunnel_config())
        first = tunnel.start()
        second = tunnel.start()
        try:
            self.assertEqual(first, second)
        finally:
            tunnel.stop()

    def test_stop_closes_local_port(self) -> None:
        tunnel = SshTunnel(self.tunnel_config())
        _, port = tunnel.start()
        tunnel.stop()
        self.assertFalse(tunnel.is_running())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()

    def test_wrong_password_fails_fast(self) -> None:
        tunnel = SshTunnel(self.tunnel_config(password="wrong"))
        with self.assertRaises(Exception):
            tunnel.start()
        self.assertFalse(tunnel.is_running())


class MongoReaderTunnelTest(TunnelTestBase):
    def mongo_cfg(self) -> MongoConfig:
        # host 故意写成不可达地址：启用隧道后不应该用到它
        return MongoConfig(
            host="192.0.2.1",
            port=27017,
            username="u",
            password="p",
            database="q_share",
        )

    def test_disabled_uses_direct_endpoint(self) -> None:
        cfg = self.mongo_cfg()
        reader = MongoReader(cfg, tunnel_cfg=None)
        try:
            self.assertEqual(reader.endpoint, ("192.0.2.1", 27017))
        finally:
            reader.close()

    def test_enabled_rewrites_to_local_port(self) -> None:
        reader = MongoReader(self.mongo_cfg(), tunnel_cfg=self.tunnel_config())
        try:
            host, port = reader.endpoint
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 0)
        finally:
            reader.close()
        # close 之后本地端口应已释放
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

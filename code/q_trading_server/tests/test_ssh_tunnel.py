"""
Description: SSH 隧道（db/mongo/ssh_tunnel.py）的离线验证。

覆盖两类问题：
    1. 配置解析：开关语义、必填项缺失、非法取值、~ 与相对路径展开。
    2. 链路行为：不复用外部服务器，在本机起一个最小 SSH 服务端（paramiko 的
       ServerInterface + direct-tcpip），把「握手 → 认证 → 端口转发 → 字节往返」
       整条链路真跑一遍，外加断线重建、单例复用、host key 策略。

为什么不能用 mock 代替这几项：
    隧道最典型的故障是「配置看着对、连上不转发」——握手成功但 direct-tcpip 通道
    建不起来、或 pump 只做了单向，这类错误不会抛异常，只会表现为 pymongo 一直
    超时。只有真跑一次字节往返还对得上，才能确认隧道是通的。

运行：python -m pytest tests/test_ssh_tunnel.py -v
     （必须在项目根目录执行，依赖 cfg/stock.cfg 的相对路径约定）
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from configparser import ConfigParser
from contextlib import contextmanager
from pathlib import Path

sys.path.append(os.getcwd())

import paramiko

from db.mongo import ssh_tunnel
from db.mongo.ssh_tunnel import (
    SshTunnel,
    SshTunnelConfig,
    SshTunnelError,
    active_tunnel,
    close_tunnel,
    ensure_tunnel,
)

# 生成一次 RSA 主机密钥给所有用例复用（每次 2048 位约 0.2 秒）
_HOST_KEY = paramiko.RSAKey.generate(2048)


# ------------------------------------------------------------------ 测试用服务端


class _ServerInterface(paramiko.ServerInterface):
    """最小 SSH 服务端：谁都能认证，只开 direct-tcpip 通道。"""

    def __init__(self) -> None:
        self.destinations: dict[int, tuple[str, int]] = {}

    def check_auth_publickey(self, username, key):  # noqa: ANN001
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_password(self, username, password):  # noqa: ANN001
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):  # noqa: ANN001
        return "publickey,password"

    def check_channel_request(self, kind, chanid):  # noqa: ANN001
        return (
            paramiko.OPEN_SUCCEEDED
            if kind == "direct-tcpip"
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):  # noqa: ANN001
        self.destinations[chanid] = destination
        return paramiko.OPEN_SUCCEEDED


def _copy(src, dst) -> None:
    """单向搬运，用于服务端的通道转发。"""
    try:
        while True:
            data = src.recv(32768)
            if not data:
                break
            dst.sendall(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            if hasattr(dst, "shutdown_write"):
                dst.shutdown_write()
            else:
                dst.shutdown(socket.SHUT_WR)
        except Exception:  # noqa: BLE001
            pass


class _FakeSshServer:
    """本机假 SSH 服务端，把 direct-tcpip 转发到 target_port。

    支持 stop() 后在同一端口重新 start()，用于验证隧道的断线重建。
    """

    def __init__(self, target_port: int, port: int = 0) -> None:
        self.target_port = target_port
        self._lock = threading.Lock()
        self._transports: list[paramiko.Transport] = []
        self._stop = threading.Event()
        self._srv = self._bind(port)
        self.port = self._srv.getsockname()[1]
        self._thread: threading.Thread | None = None

    def _bind(self, port: int) -> socket.socket:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(8)
        return srv

    def start(self) -> "_FakeSshServer":
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_transport, args=(client,), daemon=True).start()

    def _serve_transport(self, client: socket.socket) -> None:
        try:
            transport = paramiko.Transport(client)
            transport.add_server_key(_HOST_KEY)
            iface = _ServerInterface()
            transport.start_server(server=iface)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._transports.append(transport)
        while transport.is_active() and not self._stop.is_set():
            chan = transport.accept(0.2)
            if chan is None:
                continue
            dest = iface.destinations.get(chan.get_id(), ("127.0.0.1", self.target_port))
            threading.Thread(target=self._pipe, args=(chan, dest), daemon=True).start()

    def _pipe(self, chan, dest: tuple[str, int]) -> None:
        try:
            target = socket.create_connection(dest, timeout=5)
        except OSError:
            chan.close()
            return
        threading.Thread(target=_copy, args=(chan, target), daemon=True).start()
        threading.Thread(target=_copy, args=(target, chan), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        with self._lock:
            transports, self._transports = self._transports, []
        for transport in transports:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass


class _EchoTarget:
    """回显服务，模拟「隧道另一端的 mongod 端口」。"""

    def __init__(self) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(conn,), daemon=True).start()

    @staticmethod
    def _echo(conn: socket.socket) -> None:
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        try:
            self._srv.close()
        except OSError:
            pass


# ------------------------------------------------------------------ 工具函数


def _recv_exactly(sock: socket.socket, size: int, timeout: float = 10.0) -> bytes:
    """读满 size 字节，超时或提前 EOF 即返回已读部分。"""
    sock.settimeout(timeout)
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(min(65536, size - len(buf)))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _roundtrip(port: int, payload: bytes, host: str = "127.0.0.1") -> bytes:
    """经隧道端口发一段数据并读回同样长度。"""
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(payload)
        return _recv_exactly(sock, len(payload))


def _config(**overrides) -> SshTunnelConfig:
    base = dict(
        host="127.0.0.1",
        user="tester",
        port=22,
        auth_method="password",
        password="secret",
        remote_host="127.0.0.1",
        remote_port=27017,
        local_bind_host="127.0.0.1",
        local_port=0,
        connect_timeout=5.0,
        keepalive_interval=5.0,
        host_key_policy="auto_add",
        known_hosts="",
    )
    base.update(overrides)
    return SshTunnelConfig(**base)


def _parser(section_body: str) -> ConfigParser:
    cp = ConfigParser()
    cp.read_string(section_body)
    return cp


# ------------------------------------------------------------------ 配置解析


class SshTunnelConfigTest(unittest.TestCase):
    """配置解析：开关语义与非法取值必须给出可操作的错误信息。"""

    def test_full_section(self) -> None:
        cp = _parser(
            """
[redis]
enable=false
[mongo]
host=example.com
port=27017
ssh_tunnel_enable=true
[mongo_ssh]
host=example.com
port=2201
user=ops
auth_method=key
key_file=~/.ssh/id_rsa
remote_host=127.0.0.1
remote_port=27017
local_port=0
connect_timeout=8
keepalive_interval=15
host_key_policy=reject
known_hosts=~/.ssh/known_hosts
"""
        )
        cfg = SshTunnelConfig.from_config(cp, 27017)
        self.assertEqual(cfg.host, "example.com")
        self.assertEqual(cfg.port, 2201)
        self.assertEqual(cfg.user, "ops")
        self.assertEqual(cfg.auth_method, "key")
        self.assertTrue(os.path.isabs(cfg.key_file))
        self.assertTrue(cfg.key_file.endswith("id_rsa"))
        self.assertEqual(cfg.connect_timeout, 8.0)
        self.assertEqual(cfg.keepalive_interval, 15.0)
        self.assertEqual(cfg.host_key_policy, "reject")

    def test_remote_port_defaults_to_mongo_port(self) -> None:
        """remote_port 未配置时应沿用 [mongo] port，而不是写死 27017。"""
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=password
password=p
"""
        )
        cfg = SshTunnelConfig.from_config(cp, 37017)
        self.assertEqual(cfg.remote_port, 37017)
        self.assertEqual(cfg.port, 22)
        self.assertEqual(cfg.local_port, 0)
        self.assertEqual(cfg.host_key_policy, "auto_add")

    def test_missing_host(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
user=ops
auth_method=password
password=p
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("[mongo_ssh] host", str(ctx.exception))

    def test_missing_section_entirely(self) -> None:
        """整段缺失时报错信息要指向段落，而不是抛 NoSectionError。"""
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(_parser("[mongo]\nhost=x\n"), 27017)
        self.assertIn("mongo_ssh", str(ctx.exception))

    def test_bad_auth_method(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=agent
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("auth_method", str(ctx.exception))

    def test_password_auth_requires_password(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=password
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("password", str(ctx.exception))

    def test_key_file_missing(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=key
key_file=/nonexistent/path/id_rsa
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("私钥文件不存在", str(ctx.exception))

    def test_bad_port(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=password
password=p
port=abc
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("port", str(ctx.exception))

    def test_bad_host_key_policy(self) -> None:
        cp = _parser(
            """
[mongo_ssh]
host=example.com
user=ops
auth_method=password
password=p
host_key_policy=trust_me
"""
        )
        with self.assertRaises(SshTunnelError) as ctx:
            SshTunnelConfig.from_config(cp, 27017)
        self.assertIn("host_key_policy", str(ctx.exception))


# ------------------------------------------------------------------ 链路行为


class SshTunnelEndToEndTest(unittest.TestCase):
    """真跑一次握手 + 转发 + 字节往返。"""

    def setUp(self) -> None:
        self.target = _EchoTarget()
        self.addCleanup(self.target.stop)
        self.ssh = _FakeSshServer(self.target.port)
        self.addCleanup(self.ssh.stop)
        self.ssh.start()
        self.tunnels: list[SshTunnel] = []
        self.addCleanup(self._close_tunnels)

    def _close_tunnels(self) -> None:
        for tunnel in self.tunnels:
            tunnel.close()

    def _start(self, **overrides) -> SshTunnel:
        config = _config(
            port=self.ssh.port,
            remote_port=self.target.port,
            **overrides,
        )
        tunnel = SshTunnel(config)
        tunnel.start()
        self.tunnels.append(tunnel)
        return tunnel

    def test_password_auth_roundtrip(self) -> None:
        tunnel = self._start()
        self.assertNotEqual(tunnel.local_port, 0)
        self.assertEqual(_roundtrip(tunnel.local_port, b"ping"), b"ping")
        self.assertTrue(tunnel.is_alive)

    def test_key_auth_with_passphrase(self) -> None:
        """加密私钥 + key_passphrase 能认证成功，这是部署最常见的一种形态。"""
        key = paramiko.RSAKey.generate(2048)
        tmpdir = tempfile.mkdtemp()
        key_path = os.path.join(tmpdir, "id_rsa")
        key.write_private_key_file(key_path, password="ptest")

        tunnel = self._start(
            auth_method="key",
            password="",
            key_file=key_path,
            key_passphrase="ptest",
        )
        self.assertEqual(_roundtrip(tunnel.local_port, b"keyauth"), b"keyauth")

    def test_wrong_passphrase_message(self) -> None:
        """口令不对时要提示去改 key_passphrase，而不是丢一个 base64 异常。"""
        key = paramiko.RSAKey.generate(2048)
        tmpdir = tempfile.mkdtemp()
        key_path = os.path.join(tmpdir, "id_rsa")
        key.write_private_key_file(key_path, password="right")

        tunnel = SshTunnel(
            _config(
                port=self.ssh.port,
                remote_port=self.target.port,
                auth_method="key",
                password="",
                key_file=key_path,
                key_passphrase="wrong",
            )
        )
        with self.assertRaises(SshTunnelError) as ctx:
            tunnel.start()
        self.assertIn("key_passphrase", str(ctx.exception))

    def test_large_payload(self) -> None:
        """超过单次 recv 缓冲（32KB）的数据必须完整往返，验证双向 pump 不截断。"""
        tunnel = self._start()
        payload = os.urandom(300 * 1024)
        self.assertEqual(_roundtrip(tunnel.local_port, payload), payload)

    def test_concurrent_connections(self) -> None:
        """多个并发连接各自独立通道。pymongo 连接池就是这个形态。"""
        tunnel = self._start()
        results: dict[int, bytes] = {}
        errors: list[BaseException] = []

        def worker(idx: int) -> None:
            try:
                payload = f"conn-{idx}".encode() * 1000
                results[idx] = _roundtrip(tunnel.local_port, payload)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        for idx, data in results.items():
            self.assertEqual(data, f"conn-{idx}".encode() * 1000)

    def test_reconnect_after_ssh_drop(self) -> None:
        """SSH 连接断开后隧道应自动重建，且本地端口保持不变。"""
        tunnel = self._start(keepalive_interval=5.0)
        port = tunnel.local_port
        self.assertEqual(_roundtrip(port, b"before"), b"before")

        # 关掉 SSH 服务端：transport 断开
        self.ssh.stop()
        deadline = time.time() + 10
        while time.time() < deadline and tunnel.is_alive:
            time.sleep(0.2)
        self.assertFalse(tunnel.is_alive, "SSH 服务端已关闭，隧道不应仍报告存活")

        # 同端口重启服务端，等监控线程重建（检查周期最短 5 秒）
        revived = _FakeSshServer(self.target.port, port=self.ssh.port)
        self.addCleanup(revived.stop)
        revived.start()

        deadline = time.time() + 25
        recovered = False
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                if _roundtrip(port, b"after") == b"after":
                    recovered = True
                    break
            except OSError:
                continue
        self.assertTrue(recovered, "SSH 服务端恢复后隧道未在 25 秒内重建")
        self.assertEqual(tunnel.local_port, port, "重建后本地端口不应变化")

    def test_host_key_reject_without_record(self) -> None:
        """host_key_policy=reject 且 known_hosts 无记录时应拒绝连接。"""
        tunnel = SshTunnel(
            _config(
                port=self.ssh.port,
                remote_port=self.target.port,
                host_key_policy="reject",
                known_hosts=os.path.join(tempfile.mkdtemp(), "known_hosts"),
            )
        )
        with self.assertRaises(SshTunnelError) as ctx:
            tunnel.start()
        self.assertIn("reject", str(ctx.exception))

    def test_host_key_recorded(self) -> None:
        """known_hosts 中有正确记录时放行。"""
        known_hosts = os.path.join(tempfile.mkdtemp(), "known_hosts")
        host_keys = paramiko.HostKeys()
        host_keys.add(f"[127.0.0.1]:{self.ssh.port}", _HOST_KEY.get_name(), _HOST_KEY)
        host_keys.save(known_hosts)

        tunnel = self._start(host_key_policy="reject", known_hosts=known_hosts)
        self.assertEqual(_roundtrip(tunnel.local_port, b"known"), b"known")

    def test_host_key_mismatch(self) -> None:
        """known_hosts 记录的是另一把密钥时必须拒绝，防中间人。"""
        known_hosts = os.path.join(tempfile.mkdtemp(), "known_hosts")
        other_key = paramiko.RSAKey.generate(2048)
        host_keys = paramiko.HostKeys()
        host_keys.add(f"[127.0.0.1]:{self.ssh.port}", other_key.get_name(), other_key)
        host_keys.save(known_hosts)

        tunnel = SshTunnel(
            _config(
                port=self.ssh.port,
                remote_port=self.target.port,
                host_key_policy="auto_add",
                known_hosts=known_hosts,
            )
        )
        with self.assertRaises(SshTunnelError) as ctx:
            tunnel.start()
        self.assertIn("中间人", str(ctx.exception))


class SingleInstanceTest(unittest.TestCase):
    """ensure_tunnel 必须复用同一个隧道，否则每次 MongoExec() 都新开 SSH 连接。"""

    def setUp(self) -> None:
        close_tunnel()
        self.addCleanup(close_tunnel)
        self.target = _EchoTarget()
        self.addCleanup(self.target.stop)
        self.ssh = _FakeSshServer(self.target.port)
        self.addCleanup(self.ssh.stop)
        self.ssh.start()

    def test_same_port_and_instance(self) -> None:
        config = _config(port=self.ssh.port, remote_port=self.target.port)
        first = ensure_tunnel(config)
        second = ensure_tunnel(config)
        self.assertEqual(first, second)
        self.assertIsNotNone(active_tunnel())
        self.assertEqual(_roundtrip(first[1], b"single"), b"single")

        close_tunnel()
        self.assertIsNone(active_tunnel())


# ------------------------------------------------------------------ 与 MongoExec 的接线


_BASE_CFG = """[redis]
enable=false
host=127.0.0.1
port=6379
password=
db=0
enable_ssl=false
cert=
key=
ca=
[mongo]
host=example.com
port=27017
username=qshare
password=secret
database=q_share
ssh_tunnel_enable={enable}
{mongo_ssh}
[log]
config=cfg/log.yaml
"""


class _FakeMongoClient:
    """替换 pymongo.MongoClient，只记录实际连的地址。"""

    instances: list["_FakeMongoClient"] = []

    def __init__(self, host=None, port=None, **kwargs) -> None:  # noqa: ANN001
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.closed = False
        _FakeMongoClient.instances.append(self)

    def __getitem__(self, name: str) -> dict:
        return {}

    def close(self) -> None:
        self.closed = True


@contextmanager
def _project_dir(enable: str, ssh_section: str):
    """在临时目录里铺一份 cfg/stock.cfg，并切换工作目录。

    DbBaseImpl 用相对路径读 cfg/stock.cfg，切目录是唯一能替换配置的方式。
    """
    from db.db_base import DbBaseImpl

    tmpdir = tempfile.mkdtemp()
    cfg_dir = Path(tmpdir) / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "stock.cfg").write_text(
        _BASE_CFG.format(enable=enable, mongo_ssh=ssh_section), encoding="utf-8"
    )
    previous = os.getcwd()
    os.chdir(tmpdir)
    try:
        yield DbBaseImpl
    finally:
        os.chdir(previous)


class MongoExecWiringTest(unittest.TestCase):
    """验证开关真的改变了 pymongo 的连接目标，而不是只解析了配置。"""

    def setUp(self) -> None:
        _FakeMongoClient.instances.clear()
        self._orig_client = ssh_tunnel.__dict__.get("ensure_tunnel")
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        from db.mongo import mongo_exec

        if hasattr(mongo_exec, "_ORIG_CLIENT"):
            mongo_exec.MongoClient = mongo_exec._ORIG_CLIENT
            del mongo_exec._ORIG_CLIENT

    def _patch(self) -> None:
        from db.mongo import mongo_exec

        if not hasattr(mongo_exec, "_ORIG_CLIENT"):
            mongo_exec._ORIG_CLIENT = mongo_exec.MongoClient
        mongo_exec.MongoClient = _FakeMongoClient

    def test_disabled_connects_directly(self) -> None:
        self._patch()
        from db.mongo.mongo_exec import MongoExec

        with _project_dir("false", ""):
            MongoExec()
        self.assertEqual(_FakeMongoClient.instances[-1].host, "example.com")
        self.assertEqual(_FakeMongoClient.instances[-1].port, 27017)

    def test_enabled_connects_to_tunnel_port(self) -> None:
        self._patch()
        from db.mongo.mongo_exec import MongoExec

        calls: list[SshTunnelConfig] = []
        original = ssh_tunnel.ensure_tunnel

        def fake_ensure(config: SshTunnelConfig) -> tuple[str, int]:
            calls.append(config)
            return "127.0.0.1", 45678

        ssh_tunnel.ensure_tunnel = fake_ensure
        self.addCleanup(setattr, ssh_tunnel, "ensure_tunnel", original)

        with _project_dir(
            "true",
            "[mongo_ssh]\nhost=127.0.0.1\nport=2201\nuser=ops\n"
            "auth_method=password\npassword=p\nremote_port=27017",
        ):
            MongoExec()

        self.assertEqual(len(calls), 1, "开关打开时必须调用一次 ensure_tunnel")
        self.assertEqual(calls[0].port, 2201)
        self.assertEqual(_FakeMongoClient.instances[-1].host, "127.0.0.1")
        self.assertEqual(_FakeMongoClient.instances[-1].port, 45678)
        # 认证参数不能被隧道改造弄丢
        self.assertEqual(_FakeMongoClient.instances[-1].kwargs["username"], "qshare")
        self.assertEqual(_FakeMongoClient.instances[-1].kwargs["authSource"], "q_share")


if __name__ == "__main__":
    unittest.main(verbosity=2)

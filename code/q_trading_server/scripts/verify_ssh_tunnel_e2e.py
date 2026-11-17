"""
Description: SSH 隧道端到端验证 —— 真 mongod + 真 SSH 协议 + 真 pymongo。

与 tests/test_ssh_tunnel.py 的分工：
    单测用「本机假 SSH 服务端 + 回显服务」验证隧道自身的字节转发；
    本脚本再把隧道另一头换成**真实的 mongod**（Docker 里的 mongo:7.0，带认证），
    走完整链路：MongoExec → SSH 隧道 → mongod 认证 → 写入 → 读回。
    这条链路上的问题（认证参数丢失、隧道只转单边、BNS 解析失败）单靠单测发现不了。

前置：
    - 本机有 Docker，且已有 mongo:7.0 镜像
    - 在项目根目录执行

运行：python scripts/verify_ssh_tunnel_e2e.py    （exit 0 = 通过）
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

sys.path.insert(0, os.path.join(ROOT_DIR, "tests"))

from test_ssh_tunnel import _FakeSshServer  # noqa: E402  复用测试里的假 SSH 服务端

CONTAINER = "qt-mongo-tunnel-verify"
MONGO_IMAGE = "mongo:7.0"
MONGO_PORT = 37017
MONGO_USER = os.environ.get("QT_VERIFY_MONGO_USER", "qshare")
# 密码通过环境变量注入，不写死在源码里（本脚本使用的是临时验证容器，与生产库无关）
MONGO_PASSWORD = os.environ.get("QT_VERIFY_MONGO_PASSWORD", "")
MONGO_DB = "q_share_tunnel_verify"

_checks: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _checks.append((ok, name, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  ({detail})" if detail else ""))


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def cleanup_container() -> None:
    run(["docker", "rm", "-f", CONTAINER], timeout=60)


def wait_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def start_mongo() -> bool:
    cleanup_container()
    proc = run(
        [
            "docker", "run", "-d", "--name", CONTAINER,
            "-p", f"127.0.0.1:{MONGO_PORT}:27017",
            MONGO_IMAGE, "--auth", "--bind_ip_all",
        ],
        timeout=180,
    )
    if proc.returncode != 0:
        check(False, "启动 mongod 容器", proc.stderr.strip()[:200])
        return False
    check(True, "启动 mongod 容器", f"127.0.0.1:{MONGO_PORT}")

    if not wait_port("127.0.0.1", MONGO_PORT, 60):
        check(False, "等待 mongod 端口就绪")
        return False

    # localhost exception 只允许在 admin 库创建「第一个用户」，所以分两步：
    #   1) 匿名建 admin 库的 root 账号
    #   2) 用 root 认证，在业务库建同名业务账号
    # 第 2 步不能省：MongoExec 把 authSource 设成 database，用户在 admin 库会认证失败。
    bootstrap_user = "bootstrap_root"
    bootstrap_pwd = "bootstrap_root_pwd"
    create_admin = (
        'db.getSiblingDB("admin").createUser({user:"%s",pwd:"%s",'
        'roles:[{role:"root",db:"admin"}]})' % (bootstrap_user, bootstrap_pwd)
    )
    deadline = time.time() + 60
    last_error = ""
    while time.time() < deadline:
        proc = run(["docker", "exec", CONTAINER, "mongosh", "--quiet", "--eval", create_admin], timeout=60)
        if proc.returncode == 0:
            break
        last_error = (proc.stderr or proc.stdout).strip()[:200]
        if "already exists" in last_error:
            break
        time.sleep(2)
    else:
        check(False, "创建 mongod 管理员", last_error)
        return False

    create_user = (
        'db.getSiblingDB("%s").createUser({user:"%s",pwd:"%s",'
        'roles:[{role:"readWrite",db:"%s"}]})'
        % (MONGO_DB, MONGO_USER, MONGO_PASSWORD, MONGO_DB)
    )
    proc = run(
        [
            "docker", "exec", CONTAINER, "mongosh", "--quiet",
            "-u", bootstrap_user, "-p", bootstrap_pwd, "--authenticationDatabase", "admin",
            "--eval", create_user,
        ],
        timeout=60,
    )
    if proc.returncode != 0:
        check(False, "创建业务库用户", (proc.stderr or proc.stdout).strip()[:200])
        return False
    check(True, "创建 mongod 用户", f"{MONGO_USER}（authSource={MONGO_DB}）")
    return True


def write_config(ssh_port: int) -> Path:
    """在临时目录铺一份指向「公网不可直连」地址的配置。

    [mongo] host 故意写成一个不可解析的名字：只要最终连上了，就证明走的是隧道
    而不是碰巧直连到了本机 mongod。
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="qt-tunnel-"))
    cfg_dir = tmpdir / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "stock.cfg").write_text(
        f"""[redis]
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
host=mongo-not-directly-reachable.invalid
port={MONGO_PORT}
username={MONGO_USER}
password={MONGO_PASSWORD}
database={MONGO_DB}
ssh_tunnel_enable=true
[mongo_ssh]
host=127.0.0.1
port={ssh_port}
user=tester
auth_method=password
password=secret
remote_host=127.0.0.1
remote_port={MONGO_PORT}
local_bind_host=127.0.0.1
local_port=0
connect_timeout=10
keepalive_interval=30
host_key_policy=auto_add
known_hosts=
[log]
config=cfg/log.yaml
""",
        encoding="utf-8",
    )
    return tmpdir


def main() -> int:
    if not MONGO_PASSWORD:
        print(
            "[ERROR] 缺少验证库口令：请先设置环境变量 QT_VERIFY_MONGO_PASSWORD。\n"
            "        该口令仅用于本脚本启动的临时验证容器（端口 37017），与生产库无关。"
        )
        return 2

    if not start_mongo():
        cleanup_container()
        return 1

    ssh = _FakeSshServer(MONGO_PORT).start()
    print(f"[INFO] 假 SSH 服务端监听 127.0.0.1:{ssh.port}，转发到 mongod 127.0.0.1:{MONGO_PORT}")

    tmpdir = write_config(ssh.port)
    previous = os.getcwd()
    os.chdir(tmpdir)
    mongo_exec = None
    try:
        # 关键点：这里构造的是真实 MongoExec，会走 DbBaseImpl 读配置 → ensure_tunnel → MongoClient
        from db.mongo.mongo_exec import MongoExec

        started = time.time()
        mongo_exec = MongoExec()
        elapsed = time.time() - started
        check(True, "MongoExec 初始化完成（含隧道建立）", f"{elapsed:.2f}s")

        # 1) ping：证明 SSH 隧道 + mongod 认证都通过
        mongo_exec.conn.admin.command("ping")
        check(True, "经隧道执行 ping")

        # 2) 写
        table = mongo_exec.db["tunnel_verify_tbl"]
        table.delete_many({})
        ok, inserted_id = mongo_exec.add(table, {"note": "via ssh tunnel", "value": 42})
        check(ok and bool(inserted_id), "经隧道写入文档", str(inserted_id))

        # 3) 读
        ok, records = mongo_exec.query_by_condition(table, {"value": 42}, limit=10)
        check(ok and records and records[0]["note"] == "via ssh tunnel", "经隧道读回文档")

        # 4) 大字段：验证 pump 不是只搬一个 buffer（> 32KB 会分多段）
        big = "x" * (200 * 1024)
        ok, big_id = mongo_exec.add(table, {"note": "big", "payload": big})
        ok, records = mongo_exec.query_by_condition(table, {"note": "big"}, limit=1)
        check(
            ok and records and len(records[0]["payload"]) == len(big),
            "经隧道读回 200KB 字段",
            f"{len(records[0]['payload']) if records else 0} 字节",
        )

        # 5) 并发：pymongo 连接池会同时开多条通道
        errors: list[str] = []

        def worker(idx: int) -> None:
            try:
                mongo_exec.db["tunnel_verify_tbl"].insert_one({"note": f"concurrent-{idx}"})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{idx}: {exc}")

        import threading

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        count = mongo_exec.db["tunnel_verify_tbl"].count_documents({"note": {"$regex": "^concurrent-"}})
        check(not errors and count == 6, "6 个并发写入全部成功", f"count={count}, errors={errors[:2]}")

        mongo_exec.db["tunnel_verify_tbl"].drop()
    except Exception as exc:  # noqa: BLE001
        check(False, "端到端链路", f"{type(exc).__name__}: {exc}")
    finally:
        os.chdir(previous)
        try:
            from db.mongo.ssh_tunnel import close_tunnel

            close_tunnel()
        except Exception:  # noqa: BLE001
            pass
        ssh.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)
        cleanup_container()

    failed = [item for item in _checks if not item[0]]
    print("\n" + "=" * 60)
    print(f"通过 {len(_checks) - len(failed)}/{len(_checks)} 项")
    if failed:
        for _, name, detail in failed:
            print(f"  FAIL: {name} {detail}")
        return 1
    print("端到端验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Description: 用 cfg/stock.cfg 里的真实配置验证「SSH 隧道 → MongoDB」是否连通。

用途：
    部署前/排障时跑一次，确认隧道能建、mongod 能认证、业务库能读到集合。
    失败信息会直接指向该改哪个配置项，不需要去猜 Connection refused。

口令不落盘：
    私钥带口令时通过环境变量传入，不写进 stock.cfg，也不出现在脚本参数里：
        Linux:   QT_SSH_PASSPHRASE='口令' python3 scripts/verify_ssh_tunnel_real.py
        Windows: set QT_SSH_PASSPHRASE=口令 && python scripts/verify_ssh_tunnel_real.py

运行：必须在项目根目录执行（cfg/stock.cfg 是相对路径）
退出码：0 = 连通；1 = 失败
"""

from __future__ import annotations

import os
import sys
import time
from configparser import ConfigParser
from dataclasses import replace

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from db.mongo.ssh_tunnel import SshTunnelConfig, close_tunnel, ensure_tunnel  # noqa: E402


def main() -> int:
    cp = ConfigParser()
    if not cp.read("cfg/stock.cfg", encoding="utf-8"):
        print("!! 读不到 cfg/stock.cfg，请在项目根目录执行")
        return 1

    enable = cp.getboolean("mongo", "ssh_tunnel_enable", fallback=False)
    mongo_port = int(cp.get("mongo", "port", fallback="27017"))
    config = SshTunnelConfig.from_config(cp, mongo_port)

    passphrase = os.environ.get("QT_SSH_PASSPHRASE", "")
    if passphrase:
        config = replace(config, key_passphrase=passphrase)

    print(f"SSH 隧道开关: {enable}")
    print(f"端点: {config.endpoint}（{config.auth_method} 认证）")

    if not enable:
        print("!! [mongo] ssh_tunnel_enable=false，隧道不会建立；若要验证隧道请先改成 true")
        return 1

    started = time.time()
    try:
        host, port = ensure_tunnel(config)
    except Exception as exc:  # noqa: BLE001
        print(f"!! 隧道建立失败: {type(exc).__name__}: {exc}")
        return 1
    print(f"隧道已建立: 本地 {host}:{port}，耗时 {time.time() - started:.2f}s")

    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError

        client = MongoClient(
            host=host,
            port=port,
            w="majority",
            username=cp.get("mongo", "username", fallback=""),
            password=cp.get("mongo", "password", fallback=""),
            authSource=cp.get("mongo", "database", fallback=""),
            serverSelectionTimeoutMS=10000,
        )
        client.admin.command("ping")
        print("MongoDB ping 成功（认证通过）")

        database = cp.get("mongo", "database", fallback="")
        collections = client[database].list_collection_names()
        print(f"库 {database} 可读，集合数 {len(collections)}")
        print("示例集合: " + ", ".join(sorted(collections)[:8]))

        client.close()
    except PyMongoError as exc:
        print(f"!! MongoDB 连接/认证失败: {type(exc).__name__}: {exc}")
        return 1
    finally:
        close_tunnel()

    print("连通性验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

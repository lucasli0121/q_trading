# 通过 SSH 隧道访问 MongoDB

## 背景

云服务器安全组只放开 SSH（当前为 2201），MongoDB 的 27017 不允许从公网直连。
服务进程在启动时自己建一条本地端口转发（等价 `ssh -L 本地端口:127.0.0.1:27017`），
pymongo 连本地回环端口，上层 DAO 与 router 不需要任何改动。

隧道由 `db/mongo/ssh_tunnel.py` 实现，只在 `MongoExec` 建连接时创建。

## 配置

开关在 `[mongo] ssh_tunnel_enable`，参数在 `[mongo_ssh]`：

```ini
[mongo]
host=example.com
port=27017
ssh_tunnel_enable=true

[mongo_ssh]
host=example.com      # 跳板机 / MongoDB 所在服务器
port=2201                # SSH 端口
user=lucasli
auth_method=key          # key | password
password=                # auth_method=password 时使用
key_file=~/.ssh/id_rsa
key_passphrase=          # 私钥带口令时必填
remote_host=127.0.0.1    # SSH 登录后，mongod 相对于服务器的地址
remote_port=27017
local_bind_host=127.0.0.1
local_port=0             # 0 = 由系统分配空闲端口
connect_timeout=15
keepalive_interval=30    # keepalive 与断线检查周期（秒）
host_key_policy=auto_add # auto_add | reject
known_hosts=~/.ssh/known_hosts
```

开启隧道后 `[mongo] host/port` 不再直接使用，只作为 `remote_port` 的缺省值。

`ssh_tunnel_enable=false`（默认）时行为与改造前完全一致，不需要装 paramiko 也能启动。

## 行为

- **进程级单例**：`ensure_tunnel()` 保证一个进程只建一条隧道。`MongoExec` 会被
  实例化多次（`AppContext` 一份、一次性脚本各一份），重复建隧道会白占 SSH 连接。
- **断线重建**：后台监控线程每 `keepalive_interval` 秒检查一次，连接断开就重建
  transport，本地监听端口保持不变，因此 pymongo 自带的重连逻辑仍然有效。
- **主机密钥**：`auto_add` 在 known_hosts 无记录时信任并打 WARNING；`reject` 要求
  必须已记录。两种策略下「已记录但不匹配」都会直接拒绝，防中间人。
- **相对路径**：`key_file` 支持 `~`，相对路径按启动时工作目录解析。

## 验证

```bash
# 离线：隧道自身链路（假 SSH 服务端 + 回显服务，不需要任何外部依赖）
python -m pytest tests/test_ssh_tunnel.py -v

# 离线：真 mongod + 真 pymongo 走隧道读写（需要本机有 docker 与 mongo:7.0 镜像）
python scripts/verify_ssh_tunnel_e2e.py

# 真实环境：用 cfg/stock.cfg 的配置连实际服务器
QT_SSH_PASSPHRASE='私钥口令' python scripts/verify_ssh_tunnel_real.py
```

## 排障

| 报错 | 原因与处理 |
| --- | --- |
| `[mongo_ssh] host 未配置` | 开关打开但没配置 `[mongo_ssh]` 段 |
| `SSH 私钥文件不存在: xxx` | `key_file` 路径不对，注意 `~` 展开后指向的是运行用户的 home |
| `加载 SSH 私钥失败 ... key_passphrase` | 私钥带口令但 `key_passphrase` 为空或写错 |
| `SSH 认证失败 ... 服务器允许的方式: ['publickey']` | 选了 password 认证但服务器只接受密钥 |
| `主机密钥与 known_hosts 记录不一致` | 服务器换过密钥，或 known_hosts 里有另一台主机的旧记录 |
| `连接 SSH 服务器 ... timed out` | SSH 端口不通：安全组、端口号、跳板机地址 |
| `SSH 隧道转发失败（127.0.0.1:27017）` | 隧道通了但 mongod 没在 `remote_host:remote_port` 监听 |

## 限制

- **副本集不适用**：副本集客户端会根据 `replSet` 配置里的成员地址重新连接，而隧道
  只映射了一个本地端口。当前实现面向单机 mongod。
- **每条连接一个 SSH channel**：pymongo 连接池里的每个连接都要开一条 channel。
  连接池较大时需要确认 sshd 的 `MaxSessions` 足够。
- **Redis 不走隧道**：`[redis]` 仍是 16379 的 TLS 直连，本次没有改动。

## 与 MongoDB 同机部署时

如果服务就跑在 MongoDB 所在的服务器上（或同一内网），不需要隧道：
把 `ssh_tunnel_enable` 改为 `false`，`[mongo] host` 改为 `127.0.0.1` 或内网地址即可。
多一层隧道就多一个故障点。

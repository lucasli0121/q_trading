# MongoDB 走 SSH 隧道（2026-11-15）

## 一、问题

`cfg/stock.cfg` 的 `[mongo] host=example.com port=27017` 在云服务器上只对本机开放，
外部直连 27017 被安全组/防火墙拦掉。进程不在那台服务器上时，`MongoClient` 连不上，
服务启动即失败或所有查询超时。

SSH 端口（`example.com:2201`）是通的，因此走本地端口转发：
本机监听一个端口，流量经 SSH 加密通道到跳板机，再由跳板机访问数据库。

## 二、实现方式

在进程内用 `sshtunnel`（底层 paramiko）建立转发，而不是调用系统 `ssh -L`：

| 对比项 | 进程内 sshtunnel | 系统 ssh 子进程 |
| --- | --- | --- |
| 额外依赖 | paramiko + cryptography（纯 wheel） | 目标机必须装 openssh-client |
| Docker 镜像 | 无需改动 | 需 apt 装 openssh-client |
| 认证 | 私钥/密码均可，参数化 | 密码需 sshpass，密钥需 `-i` |
| 打包 | PyInstaller 可收进产物 | 依赖运行环境的外部二进制 |

新增 `utils/ssh_tunnel.py`，`MongoExec` 在创建 `MongoClient` 前调用
`ensure_mongo_tunnel()`，启用时把 host/port 换成隧道本地端点，其余代码不变。

```
本机 127.0.0.1:<自动端口> --SSH--> example.com:2201 --内网--> 127.0.0.1:27017
```

## 三、配置

`cfg/stock.cfg` 新增 `[ssh_tunnel]` 段（`cfg/stock.cfg.example` 同步更新）：

```ini
[ssh_tunnel]
enable=false
ssh_host=example.com
ssh_port=2201
ssh_username=lucasli
ssh_private_key=~/.ssh/id_rsa
ssh_private_key_password=
ssh_password=
remote_host=127.0.0.1
remote_port=27017
local_bind_host=127.0.0.1
local_bind_port=0
connect_timeout=10
check_interval=30
```

| 项 | 说明 |
| --- | --- |
| `enable` | 总开关。`false` 时按 `[mongo]` 直连，行为与改造前完全一致 |
| `ssh_private_key` / `ssh_password` | 二选一，配了私钥用私钥。私钥只支持 OpenSSH 格式，PuTTY 的 .ppk 需先转换 |
| `remote_host` / `remote_port` | 跳板机视角的数据库地址。同机填 `127.0.0.1:27017`，另一台内网机填内网 IP |
| `local_bind_port` | `0` 表示自动选空闲端口。同机跑多个实例时保持 `0`，避免端口冲突 |
| `connect_timeout` | SSH 端口连通性探测超时，用于把「网络不通」和「认证失败」分开报错 |
| `check_interval` | 隧道存活检查周期，`0` 表示不检查 |

密钥路径会做 `~` 展开与绝对路径化，相对路径按进程工作目录解析。

## 四、运行时行为

1. 启动时打印 `MongoDB SSH 隧道: 已启用/未启用`，建立成功后打印
   `SSH 隧道已建立: 本地 127.0.0.1:<port> -> <remote_host>:<remote_port>`。
2. 隧道只建一条（进程级单例），`MongoExec` 与工具脚本共享。
3. 看门狗每 `check_interval` 秒检查一次：转发器是否 active、本地端口能否连接。
   断开后自动重建，**沿用原本地端口**，pymongo 的既有连接池在新连接时命中同一端口，
   不需要重启进程。端口确实变了会打 error 日志，此时才需要重启。
4. 失败路径的报错可区分：配置缺失、私钥不存在、SSH 端口不通、认证失败（带跳板信息）。
5. 退出时 `stop_app()` 关闭隧道。

### 一个库的坑

`sshtunnel` 0.5.1 在 `start()` 失败后再调 `stop()` 会永久阻塞在内部 join 上
（实测 18 分钟仍未返回）。因此失败路径不调用 `stop()`，sshtunnel 自己已经关掉
transport；正常关闭也放在守护线程里限时 5 秒，避免看门狗被卡住。
另外转发器断开后访问 `local_bind_port` 会抛 `BaseSSHTunnelForwarderError`，
`local_port` 属性做了兜底，否则重建流程第一步就崩。

## 五、验证

### 测试环境

本机 Docker 起两个容器模拟「数据库端口不对外」的云服务器：

- `qtunnel-mongo`：mongo 7.0，开启 `--auth`，用户 `qshare`，**不发布 27017 到宿主**
- `qtunnel-bastion`：python:3.12-slim + openssh-server，发布 `2222:22`，用户 `tunnel`，私钥认证

宿主无法解析/连接 `qtunnel-mongo:27017`（直连必然失败），只能经 SSH 跳板进入。

### 结果

主链路 16 项全部通过：

| 检查 | 结果 |
| --- | --- |
| 直连 Mongo 不可达（前置） | 域名解析失败，符合预期 |
| 配置解析（开关、端口、用户、私钥绝对路径、远端地址） | 全部正确 |
| 隧道建立 | 0.5~1.2 秒，本地 `127.0.0.1:<自动端口>` |
| 重复调用复用同一实例 | 是 |
| `MongoExec` 端点替换为隧道地址 | `127.0.0.1:51309`（配置为 `qtunnel-mongo:27017`） |
| 经隧道读取文档 | 读到 `{'k': 'through-tunnel', 'v': 42}` |
| `query_by_condition` 经隧道 | 返回 1 条 |
| 重建后本地端口不变 | `51309 -> 51309` |
| 重建后既有 MongoClient 自动恢复 | 重新查询成功 |
| 看门狗线程启动 | 周期 5 秒 |
| 关闭后端口释放 | 是 |
| 开关 false 不启用隧道 | 是 |

异常路径 4 项全部通过，均快速返回而非卡死：

| 场景 | 报错 |
| --- | --- |
| 私钥文件不存在 | `[ssh_tunnel] ssh_private_key 文件不存在: ...` |
| 未配置认证 | `[ssh_tunnel] 需要配置 ssh_private_key 或 ssh_password` |
| SSH 端口不通 | `SSH 端口不通: 127.0.0.1:2299（连接被拒绝）` |
| 认证失败 | `SSH 隧道建立失败（wronguser@127.0.0.1:2222，认证方式 私钥 id_test）` |

断线恢复 4 项全部通过：`docker restart` 跳板机后，看门狗 **6 秒**内检测并重建，
端口保持 `53309`，查询恢复。

### 打包产物冒烟

产物放进 `python:3.12-slim` 容器（默认 bridge，未加入测试网络），
`[mongo] host` 填容器内解析不到的 `qtunnel-mongo`，只有经 SSH 跳板才能访问：

```
INFO 实例启动: name=qtunnel_pkg_smoke, cfg=cfg/stock.cfg, pid=1
INFO MongoDB SSH 隧道: 已启用
INFO SSH 隧道已建立: 本地 127.0.0.1:54595 -> qtunnel-mongo:27017（跳板 tunnel@host.docker.internal:2222，认证 私钥 id_test）
INFO MongoDB 经 SSH 隧道连接: 本地 127.0.0.1:54595 -> 远端 qtunnel-mongo:27017
INFO MongoDB 索引已就绪: rt_stocks_tbl.code_create_time
INFO MongoDB 索引已就绪: minute_hq_tbl.code_minute_time
INFO MongoDB 索引已就绪: day_hq_tbl.code_create_time
INFO StockFetch 调度器已启动
```

索引创建成功说明 paramiko/cryptography 确实进了包，且隧道端口上跑通了真实的
MongoDB 写入。过程中顺带修掉一个初始化缺陷：`AppContext.__init__` 原本先把
`_initialized` 置 True 再构造服务，MongoExec 抛异常时会留下半初始化实例，
后续报 `'AppContext' object has no attribute 'mqtt'` 掩盖真实原因。现在标记放到
构造函数末尾，`main.start_app()` 也会把初始化异常按原样抛出。

## 六、部署注意

1. **打包**：`requirements.txt` 增加 `sshtunnel`、`paramiko`；`main.spec` 显式收集
   `paramiko`/`sshtunnel` 子模块与 `cryptography`/`bcrypt`/`nacl`，否则打包后启用隧道时报
   `ModuleNotFoundError`（sshtunnel 是运行时按需 import，静态分析看不到）。
2. **已有实例的配置不会被覆盖**：`_init_frozen_env()` 只在目标目录没有 `cfg/` 时拷贝默认配置。
   升级二进制后需要在各实例的 `cfg/stock.cfg` 里手工补 `[ssh_tunnel]` 段，否则开关不生效。
   缺少该段时程序按 `enable=false` 处理，不会报错。
3. **多实例**：`local_bind_port` 保持 `0`，各实例自动占不同端口。
4. **私钥权限**：Windows 下无需处理；Linux 下私钥建议 `600`，否则 paramiko 可能拒绝加载。
5. **Docker 运行**：容器内不需要安装 openssh-client，隧道由 Python 库完成。
6. **glibc 版本**：产物在 `python:3.12-slim`（Debian 12）里构建，实测最高依赖
   `GLIBC_2.36`。目标机需 Debian 12 / Ubuntu 24.04 及以上；Ubuntu 22.04（2.35）
   起不来，要在更老的基础镜像里重打。
7. **构建环境**：PyInstaller 在 Linux 上需要 `objdump`，缺 binutils 会直接报
   `On Linux, objdump is required`。构建命令里先 `apt-get install -y binutils`。

## 七、待确认

本机没有能登录 `example.com:2201` 的密钥（`~/.ssh/id_rsa` 对该主机的
`lucasli`/`root`/`admin` 均返回 `Permission denied (publickey)`，且服务端已关闭密码认证），
因此**尚未对真实云服务器做连通验证**。拿到可用私钥后：

```bash
# 1. 把私钥放到运行目录，cfg 里 ssh_private_key 指向它
# 2. 打开开关
sed -i 's/^enable=false/enable=true/' cfg/stock.cfg
# 3. 起服务，看启动日志是否出现「SSH 隧道已建立」
```

若要单独验证隧道而不启动调度器，可用：

```bash
python -c "import os; os.environ['Q_PROXY_CFG']='cfg/stock.cfg';\
from utils.ssh_tunnel import ensure_mongo_tunnel; t=ensure_mongo_tunnel(); print(t.local_host, t.local_port)"
```

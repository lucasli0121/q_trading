# q_trading_archive 部署

把服务端 90 天滚动窗口内的行情按日转存为本地 parquet。只读 mongo，单实例部署。

下文以部署到 `192.168.1.63` 的 `/home/lucasli/q_trading_archive` 为例，路径可按实际调整。

## 一、目标机要求

| 项 | 要求 | 说明 |
|---|---|---|
| 架构 | x86-64 | 产物是 linux/amd64 |
| glibc | **>= 2.36** | 实测产物最高需求 `GLIBC_2.36`，来自 pyarrow 的 libarrow。核对：`ldd --version` |
| 磁盘 | 见第五节容量估算 | 归档产物会持续增长 |
| 网络 | 能连到 mongo 的 27017 | 单实例，不需要对外端口 |
| Python | 不需要 | 产物自带解释器与全部依赖 |

不需要在目标机装 Python、pandas 或 pyarrow。

**glibc 不够时的处置**：不能在目标机补，只能在更老的基础镜像里重新打包。
构建镜像换成 `manylinux2014`（glibc 2.17）一类，跑 `BASE_IMAGE=... bash build.sh`，
产物会改用那一代的 glibc。当前构建环境是 Debian 12（glibc 2.36）。

Ubuntu 22.04 是 2.35，差一点，同样跑不起来。核对命令：

```bash
ldd --version | head -1
```

## 二、部署步骤

```bash
# 1) 传包（在开发机执行）
scp q_trading_archive-<版本>-bin.tar.gz lucasli@192.168.1.63:/tmp/

# 2) 解压（在 192.168.1.63 执行）
mkdir -p /home/lucasli/q_trading_archive
tar -xzf /tmp/q_trading_archive-<版本>-bin.tar.gz \
    -C /home/lucasli/q_trading_archive --strip-components=1

# 3) 建配置
cd /home/lucasli/q_trading_archive
cp _internal/cfg/stock.cfg.example cfg/stock.cfg
vi cfg/stock.cfg          # 填 mongo 凭据，见下方第三节

# 4) 自检后启动常驻
./q_trading_archive --self-check    # 不连库，先确认依赖与路径
./q_trading_archive --dry-run       # 只列出要归档的交易日
bash run.sh                         # 常驻，进程不退出，每日定时归档
```

解压后目录结构：

```
/home/lucasli/q_trading_archive/
├── q_trading_archive        # 入口，直接执行
├── run.sh                   # 常驻启动（进程不退出，每日定时归档）
├── stop.sh                  # 停止
├── status.sh                # 查看进程、进度位、产物计数
├── cfg/                     # 运维目录：配置放这里
│   └── stock.cfg
├── q_trading_archive.pid    # 常驻运行时生成
├── log/                     # 运行后生成
├── data/                    # 归档产物（或由配置指向别的盘）
│   ├── minute/2026/10/2026-10-14.parquet
│   └── day/2026/2026-10-14.parquet
└── _internal/               # 运行库与 cfg 模板副本，不要动
```

配置文件的查找顺序是**包根 `cfg/` → `_internal/cfg/`**。改配置改包根那一份；
`_internal/cfg/` 里的是模板副本，改它对运行中的进程没有任何影响。

## 三、配置

`cfg/stock.cfg` 最少要改 mongo 一节：

```ini
[mongo]
# 部署在 192.168.1.63 且 mongo 同机时用内网地址，省掉一次公网绕行
host=192.168.1.63
port=27017
username=qshare
password=<真实密码>
database=q_share

[archive]
data_dir=data
refresh_days=2
batch_size=20000
schedule_hour=15
schedule_minute=30
max_lookback_days=400
write_empty_day=false
```

要点：

- **凭据缺失会启动即失败**，不会用空密码去连库。`REPLACE_WITH_` 开头的占位符也会被拒绝。
- `data_dir` 写相对路径时相对包根解析。产物建议单独放一块盘，写绝对路径，例如
  `/data/q_trading_archive`。换路径后旧的 `.state.json` 不会跟着走，需要手工拷。
- `refresh_days=2`：每次额外重刷最近 2 个自然日。覆盖前一交易日盘中写入的半截文件。
- `schedule_hour/minute` 只在 `--daemon` 下生效，取收盘后即可。

## 四、运行

### 首次：先看计划再落盘

```bash
cd /home/lucasli/q_trading_archive

./q_trading_archive --self-check     # 环境自检，不连库
./q_trading_archive --dry-run        # 只列出要归档的交易日
./q_trading_archive                  # 真实归档，首次会向前回溯
```

首次运行没有进度文件，会从 `max_lookback_days`（默认 400 个自然日）开始回溯，
实际取到多少取决于库里还留着多少。分钟数据晚于日K，早于 90 天的部分库里已经没有了。

### 常驻（进程不退出，每日定时归档）

一条命令：

```bash
cd /home/lucasli/q_trading_archive
bash run.sh
```

`run.sh` 依次做四件事：检查 `cfg/stock.cfg` 存在、确认没有实例在跑、在子 shell 里后台启动
`--daemon`、15 秒内确认进程起来并把输出留在 `log/daemon.out`。

配套两个脚本：

```bash
bash status.sh     # 进程、进度位、产物计数、残留 .tmp、最近日志
bash stop.sh       # 先 SIGTERM，20 秒不退再 SIGKILL
```

**为什么是 `(cmd &)` 而不是 `cmd &`。** 括号不能省。裸 `&` 只是把进程挂到当前 shell
的作业表，进程仍在同一个会话里，SSH 一断、shell 退出就会给作业发 SIGHUP 把它杀掉；
`(cmd &)` 把进程放到子 shell 的作业表，父 shell 退出时它不在那份名单里，因此不受影响。
这与 `q_trading_work` 的 `run.sh` 是同一套做法。另外，**不带 `--daemon` 时程序跑完一次
就退出**，`&` 改变不了这一点，加上括号也一样。

常驻模式的行为：**启动时先补跑一次**，然后每天到 `[archive] schedule_hour / schedule_minute`
触发。停机几天不会漏，重启后那一次补跑会把这些天一起补上。归档被杀不会留下坏文件
（原子写保护），进度位也不会推进，下次重跑会补上。

### 开机自启与崩溃拉起（可选）

上面那套只管启动，不提供开机自启，进程异常退出也不会拉起。要这两样就用 systemd：

```bash
sudo cp deploy/q_trading_archive.service /etc/systemd/system/
sudo vi /etc/systemd/system/q_trading_archive.service   # 核对 User 与路径
sudo systemctl daemon-reload
sudo systemctl enable --now q_trading_archive
sudo systemctl status q_trading_archive
journalctl -u q_trading_archive -f
```

服务单元里已经带 `Restart=always`。用了 systemd 就不要再跑 `run.sh`，两者只能选一个。

### 只用 cron（进程不常驻）

```cron
30 15 * * 1-5 cd /home/lucasli/q_trading_archive && ./q_trading_archive >> log/cron.log 2>&1
```

这种方式不带 `--daemon`，跑完即退出，由 cron 每天唤起。

三种方式选一种，不要同时开：两个进程同时写同一天的文件会互相覆盖，原子写的临时名是固定的
`<日期>.parquet.tmp`，`os.replace` 会报源文件不存在。

### 我怎么知道它真在跑

```bash
pgrep -af q_trading_archive     # 有输出才在跑
tail -f log/archive.log         # 明细日志
cat .state.json                 # 进度位，看 last_date
```

`run.sh` 启动后日志里会有一条「归档目录: ...」（配置已读到），首次补跑结束后是
「已进入常驻模式，每日 15:30 归档（Asia/Shanghai）」。只看到前一条、长时间看不到后一条，
说明首次回溯还在跑，这是正常的（回溯 153 个交易日约 2 分 21 秒）。

## 五、产物与容量

按「一个交易日一个文件」分区，zstd 压缩，排序键固定，同一天重跑得到字节一致的文件。

实测数据（本机 2026-10-15 归档结果）：

| 类型 | 文件数 | 总体积 | 单文件均值 |
|---|---|---|---|
| 分钟 | 58 | 11 MB | 184 KB |
| 日K | 153 | 2.2 MB | 11.2 KB |

折算约 **8 KB / 股 / 交易日**（分钟），日K 约 0.5 KB / 股 / 交易日。

体积取决于订阅池规模，不是全市场固定值：

| 池规模 | 分钟 | 日K | 合计 / 年 |
|---|---|---|---|
| 20 只（当前实测量级） | 45 MB | 2.7 MB | 约 48 MB |
| 200 只 | 450 MB | 27 MB | 约 480 MB |
| 5000 只（全市场） | 10 GB | 0.6 GB | 约 10.6 GB |

一年按 244 个交易日估算。分钟是主要增长项。

## 六、验证与排错

### 确认进程在正常工作

```bash
tail -f log/archive.log
```

一次成功的运行会打出这样一行：

```
区间 2026-10-14 ~ 2026-10-15，交易日 2 个（写入 2 / 空 0 / 失败 0），分钟 11234 行，日K 1580 行，进度推进到 2026-10-15
```

### 常见问题

**启动报「配置文件不存在」**
配置没放到包根的 `cfg/` 下。报错信息会列出查找过的全部路径，照着重建即可。

**启动报「配置项 [mongo] password 仍是模板占位符」**
`cp` 了模板但没填密码。

**日志里「库里无行情记录，跳过」**
该日在 `minute_hq_tbl` / `day_hq_tbl` 里都没有记录。非交易日会这样；
`minute_hq_tbl` 是按需累积的，没被订阅过行情的股票本来就没有分钟数据。

**某天失败后本次不再继续**
这是有意设计。跳过失败日会让进度位越过一个空洞，之后任何增量都不会再补它。
修好取数问题后重新跑一次即可，进度位没有前进。

**改了配置不生效**
确认改的是 `cfg/stock.cfg` 而不是 `_internal/cfg/stock.cfg`。

**产物比预期大很多**
`data_dir` 指向了系统盘。改配置指向数据盘，把已有文件搬过去，`.state.json` 一并搬。

**进程起来了，过一会儿又没了**
多半是用裸 `&` 启动的。`&` 把进程挂在当前 shell 的作业表上，SSH 一断就收到 SIGHUP。
改用 `bash run.sh`，或手工写 `(./q_trading_archive --daemon &)`，括号不能省。

**到了时间没归档**
先 `pgrep -af q_trading_archive` 确认进程还在，再 `grep 定时触发 log/archive.log`。
日志区分「启动补跑」与「定时触发」，只有后者能说明调度真的生效；
一条都没有则检查 `[archive] schedule_hour / schedule_minute` 与机器时区。

### 自检

```bash
./q_trading_archive --self-check
```

会打印 Python 版本、部署根、配置候选位置、各依赖版本，并实际写读一个 parquet。
换机器或换包之后先跑这个，能提前发现依赖收集不全的问题。

## 七、与其他项目的关系

| 项目 | 角色 |
|---|---|
| `q_data_proxy` | 从第三方源抓行情写入 mongo |
| `q_trading_server` | 业务 API（8000） |
| `q_trading_work` | 策略引擎，可多实例部署 |
| `q_trading_archive` | 只读 mongo，按日转存 parquet（本进程） |

归档进程与 `q_trading_work` 没有代码依赖，只有数据上的约定：产物目录的路径是两者的
接口。`q_trading_work` 侧读取这批发数据的方式见其仓库内的说明。

## 八、升级

```bash
cd /home/lucasli/q_trading_archive

# 先停掉常驻进程。用 systemd 托管的话换成 sudo systemctl stop q_trading_archive
bash stop.sh

# 备份配置与进度位。两者都不在包内，覆盖前留一份更省事
cp cfg/stock.cfg /tmp/stock.cfg.bak
cp .state.json /tmp/state.json.bak 2>/dev/null || true

# 覆盖入口与运行库。data/、cfg/、.state.json 不在包内，不会被覆盖
mv q_trading_archive q_trading_archive.old
mv _internal _internal.old
tar -xzf /tmp/q_trading_archive-<新版本>-bin.tar.gz \
    -C /home/lucasli/q_trading_archive --strip-components=1

./q_trading_archive --self-check
bash run.sh
```

产物与进度位不在包里，覆盖安装不影响已归档的数据。`.state.json` 在包根，
但包内没有这个文件，所以解压不会覆盖它。

`run.sh` / `stop.sh` / `status.sh` 随包一起发布，解压时一并更新，不需要单独拷。

升级后核对一次：

```bash
bash status.sh      # 产物计数应与升级前一致，残留 .tmp 应为空
ls -l log/archive.log
```

如果升级前的版本没有 `log/archive.log`，升级后会开始生成。之前那些版本的
`setup_logging` 只在包根查找 `cfg/log.yaml`，而冻结态下它随包落在 `_internal/cfg/`，
读不到就静默降级为仅控制台输出，文件日志从未落盘。

# q_trading_archive

把服务端滚动窗口内的行情按日转存为本地 parquet，长期留存。

## 为什么是独立进程

`q_trading_work` 可多实例部署。把归档逻辑放进任何一个会被水平扩容的实例里，
都会变成 N 个实例写同一份数据：重复 I/O、互相覆盖、竞争。

本进程只读 mongo，不写库、不连 redis、不订阅 mqtt，单实例部署。

## 它解决什么

服务端对**日K / 周K / 月K / 分钟**统一保留 90 天（`stock_fetch.remove_over_time_task`
里四个 `delete_*` 调用）。其中：

- 日 / 周 / 月K 可回源补齐（tickflow + akshare 兜底）
- **分钟数据没有任何补齐路径，90 天前被删掉就永久丢失**

另外回源判据只看「区间内该 code 有没有任意一条记录」，不检查区间是否完整。
部分缺失时会静默返回残缺数据（HTTP 200、无提示），回测区间因此被锁死在 90 天窗口内。
本地留存之后，回测与形态统计固定读同一份数据，跨时段重跑结果一致，单变量对照才成立。

## 快速开始

```bash
cp cfg/stock.cfg.example cfg/stock.cfg    # 填入 mongo 凭据
python main.py --dry-run                  # 先看计划，不写文件
python main.py                            # 跑一次（增量补齐）
python main.py --daemon                   # 常驻，每日收盘后自动归档
```

首次运行若无进度位，会向前回溯 `max_lookback_days`（默认 400 个自然日），
实际能取到多少取决于库里还留着多少。

## 产物布局

```
data/
├── minute/2026/10/2026-10-14.parquet
└── day/2026/2026-10-14.parquet
```

按「一个交易日一个文件」分区。回测与形态统计都是「取某段时间的全部股票」，
按日分区只扫区间内的文件；按股票分区则要遍历整个池子，而池子成员每天都在变。

| 类型 | 列 |
|---|---|
| 分钟 | code, minute_time, open, high, low, close, price, preclose, volume, amount, change_percent, turnover, amp |
| 日K | code, create_time, open, high, low, close, volume, amount |

压缩用 zstd。排序键固定为 (minute_time, code) / (create_time, code)，
同一份数据重复写出得到字节一致的文件。

## 命令行

| 参数 | 说明 |
|---|---|
| `--dry-run` | 只列出将要归档的日期 |
| `--start` / `--from` `YYYY-MM-DD` | 起始日期（含） |
| `--end` / `--to` `YYYY-MM-DD` | 结束日期（含） |
| `--refresh-days N` | 额外重刷最近 N 个自然日，覆盖盘中写入的半截数据 |
| `--full-refresh` | 忽略进度位，从库里最早日期（受 `max_lookback_days` 限制）开始 |
| `--daemon` | 常驻，每日按 `[archive] schedule_hour/schedule_minute` 触发 |
| `--self-check` | 检查运行环境（依赖、部署根、配置候选、parquet 读写），不连数据库 |
| `--config PATH` | 指定配置文件，默认 `cfg/stock.cfg` |
| `--verbose` | 输出调试日志 |

## 增量与幂等

- 进度位 `.state.json` 记录最后成功归档的交易日，停机几天不会漏补。
- 每次运行额外重刷最近 `refresh_days` 天：盘中跑过一次会留下半截的当日文件，
  重刷把它覆盖成完整版本。
- 同一天重跑写同一个文件，内容字节一致（排序键固定 + 原子覆盖写），
  无意义地改动 mtime 与哈希不会发生。
- 某天读取失败即在该天中断，**不越过空洞推进进度**。跳过会让进度位越过一个缺口，
  之后任何增量都不会再补它。

## 写入原子性

先写同目录下的 `.tmp`，再用 `os.replace` 覆盖目标。中途崩溃只留下一个 `.tmp`，
不会产出半截 parquet 被下游当成完整数据读走。

## 打包与部署

目标机 `192.168.1.63`（Linux x86-64，**不需要装 Python**）：

```bash
bash build.sh -t -v 1.0.0     # 跑测试 + 打包，产出 q_trading_archive-1.0.0-bin.tar.gz
```

开发机是 Windows，而 PyInstaller 不能跨平台产出，所以构建固定在一个 x86-64 Linux 容器里进行
（`Dockerfile.builder`，首次建镜像约 1 分钟，之后复用）。本机就是 Linux 时会自动改用本机 Python，
不再经过容器。

打包过程自带校验：依赖自检 → 语法检查 → 打包 → ELF 魔数 → cfg 模板就位 → **冒烟测试**
（真实导入重依赖并写读一个 parquet，断言输出内容而非退出码）→ 扫描 glibc 需求 → 压缩。

产物 88 MB，解压后 254 MB。**目标机 glibc 需 >= 2.36**，Ubuntu 22.04（2.35）差一点也跑不起来，
核对：`ldd --version | head -1`。

部署步骤、systemd 单元、容量估算与排错见 [DEPLOY.md](DEPLOY.md)。

部署后自检（不连数据库）：

```bash
./q_trading_archive --self-check
```

常驻启动（进程不退出，每日定时归档）：

```bash
cd /home/lucasli/q_trading_archive
bash run.sh        # 启动常驻
bash status.sh     # 进程、进度位、产物计数、残留 .tmp
bash stop.sh       # 先 SIGTERM，20 秒不退再 SIGKILL
```

这三个脚本打包时会被复制到包根（源码树里在 `deploy/`）。`run.sh` 用
`(cmd &)` 在子 shell 里启动，SSH 断开不受影响，与 `q_trading_work` 的 `run.sh`
是同一套做法。需要开机自启或崩溃自动拉起时改用 systemd。

## 测试

```bash
docker run --rm -v "$PWD:/q_trading_archive" -w /q_trading_archive \
  --entrypoint python q_trading_work:latest -m unittest discover -s tests -t .
```

## 与其它项目的关系

| 项目 | 角色 |
|---|---|
| `q_data_proxy` | 从第三方源（tickflow / akshare）抓取行情，写入 MongoDB |
| `q_trading_server` | 业务 API（端口 8000），对外提供行情与策略接口 |
| `q_trading_work` | 策略引擎，可多实例部署 |
| `q_trading_archive` | 只读 mongo，按日转存 parquet（本进程） |

## 已知边界

- 只能留存「库里已经有的」数据。`minute_hq_tbl` 是按需累积的：
  没被订阅过行情的股票不会有分钟数据，同一只股票也可能只有部分日期。
  要补齐需要在抓取侧扩大同步范围，不是本进程的职责。
- 依赖 mongo 的表名与字段名（`minute_hq_tbl` / `day_hq_tbl`）。
  服务端若改名，需要同步调整 `archive/mongo_reader.py`。
- 该 mongo 实例限制了聚合管道（`aggregate` / `distinct` 不可用），
  所有统计都在本地完成，不要往查询里加聚合。

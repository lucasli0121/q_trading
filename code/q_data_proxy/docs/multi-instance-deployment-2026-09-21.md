# 多实例部署（多账号分摊 tickflow 额度）

日期：2026-09-21

## 一、部署形态

免费套餐单账号采样能力上限 45 标的/分钟，当前股票池约 109 只，单实例算不出可用的分钟成交量。
采用的方案是注册多个账号（当前 3 个），同时运行多个 q_data_proxy 实例，每个实例一份 api_token。

实际形态：

| 项目 | 情况 |
| --- | --- |
| 运行目录 | 各实例独立，代码是同一份源码的各自副本 |
| 配置文件 | 各实例独立的 `cfg/stock.cfg` |
| 实例区分 | 修改各配置里的 `[data_proxy] name`，决定取哪个 agent 的分配数据 |
| 共享资源 | 同一套 MongoDB、Redis、MQTT broker |
| 分发方式 | PyInstaller 打包（`main.spec`）→ `dist/q_data_proxy.tar.gz` → 解到各实例目录 |

这个方案成立需要两个条件：

1. 各实例的同步范围**互不重叠**——重叠的 code 被两个实例各采一遍，实时表出现重复记录、
   MQTT 重复推送；
2. 各实例自己的股票数**都不超过 45**——超过则一轮轮询长于分钟窗口，窗口内采不到 2 条记录，
   分钟线的 volume/amount 会算成 0。

容量关系：

```
窗口内采样点数 = max_requests_per_minute × real_time_stock_limit × minute_sync_interval
单实例股票上限 = 窗口内采样点数 // 2
```

按免费套餐（9 × 5 × 2 = 90）算，单实例上限 45 只，三实例合计 135 只，覆盖 109 只够用——
前提正是**互斥且均衡**。若某个实例分到 60 只，它仍然算不出成交量。

## 二、本轮改造项与适用条件

| 改造项 | 当前形态是否触发 | 说明 |
| --- | --- | --- |
| 配置读取统一走 `utils/config.py` | 不触发 | 各实例目录与配置本就独立，不会读到同一份。保留是为了让体检脚本能在单个进程内切换三份配置，另外若将来改成同目录多实例，直接设 `Q_PROXY_CFG` 即可 |
| MQTT client_id 加实例名 | 仅容器化时触发 | 独立目录、独立进程时 PID 天然不同，本无冲突。但各容器内 PID 通常都是 1，client_id 只拼 PID 会算出同一个值，broker 踢掉先连上的那个形成反复重连 |
| 日志文件名 `{instance}` 占位符 | 不触发 | 各实例目录各有 `log/`，本就不冲突。默认未启用，需要时把 `cfg/log.yaml` 的 filename 改成 `log/info_{instance}.log` |

前两项的改动都保持了默认行为不变：不设 `Q_PROXY_CFG` 时读的还是工作目录下的 `cfg/stock.cfg`，
不启用占位符时日志仍写 `log/info.log`。

### 已做的具体改动

1. **新增 `utils/config.py`**：`stock_cfg_path()`（取 `Q_PROXY_CFG`，默认 `cfg/stock.cfg`）、
   `load_stock_cfg()`、`stock_instance_name()`。全项目 10 处硬编码 `cp.read("cfg/stock.cfg")`
   统一替换，新增代码不要再直接 `ConfigParser().read(...)`。
2. **`mq/mq_base.py`**：client_id 由 `前缀 + PID` 改为 `前缀 + 实例名 + PID`。协议是 MQTTv311，
   client_id 长度不受 23 字节限制。
3. **`main.py`**：`initLogger` 支持 `{instance}` 占位符；启动时打印
   `实例启动: name=..., cfg=..., pid=...`，用于确认各进程读到了各自的配置。

## 三、分片体检

新增 `tools/check_shards.py`，用于确认第一节那两个条件。它复用 `StockFetch` 自身的取数方法
（`load_agent_pool_stocks` + `get_hot_industry_stocks`），口径与 `real_time_hq_sync_task`
真正遍历的列表一致；内部通过设置 `Q_PROXY_CFG` 切换配置文件，所以一个进程就能体检三份配置。

脚本不进 PyInstaller 包（`main.spec` 只从 `main.py` 出发做静态分析），需在源码目录、用带项目依赖的
Python 运行，配置文件路径指向各实例：

```bash
cd code/q_data_proxy
python tools/check_shards.py \
    /path/to/inst1/cfg/stock.cfg \
    /path/to/inst2/cfg/stock.cfg \
    /path/to/inst3/cfg/stock.cfg
```

输出三部分：

- **各实例同步范围**：股票池数、热门行业数、合计、单实例上限，超限的打标记；
- **实例间重叠**：两两交集的具体 code、去重后总数、重复采样只次；
- **结论**：列出需处理项，存在问题时建议按 code 哈希分片。

若确实存在重叠，在 `get_real_time_stocks` 与 `get_industry_and_pool_stocks` 返回前按
`hash(code) % shard_total == shard_index` 过滤即可。两个方法共用同一条规则，历史 K 线与实时同步
就不会交叉。分片参数可以加在 `[data_proxy]` 段，与 `name` 一起逐实例配置。

## 四、同步到各实例目录

源码只有一份（`code/q_data_proxy`，随主仓管理）。改动要生效，需要把下列文件同步到另外两个实例
目录后重启，或重新打包分发：

| 文件 | 类型 |
| --- | --- |
| `utils/config.py` | 新增 |
| `tools/check_shards.py` | 新增 |
| `main.py` | 修改 |
| `mq/mq_base.py` | 修改 |
| `db/db_base.py` | 修改 |
| `stock_fetch/__init__.py` | 修改 |
| `stock_fetch/stock_fetch.py` | 修改 |
| `stock_fetch/tickflow_fetch/__init__.py` | 修改 |
| `stock_fetch/tickflow_fetch/tickflow_proxy.py` | 修改 |
| `cfg/log.yaml` | 修改（仅注释） |

**`cfg/stock.cfg` 不要同步**——各实例的 `[data_proxy] name` 与 `[tickflow] api_token` 就在这份文件里，
覆盖会把实例区分抹掉。PyInstaller 的 `_init_frozen_env()` 也只在目标目录没有 `cfg/` 时才拷贝默认配置，
升级二进制不会动已有配置。

重启时会执行一次 `MongoExec.ensure_indexes()`，幂等，`rt_stocks_tbl` 保留 60 天约在几十万条量级，
建索引为秒级；失败只记 error，不阻塞启动。

## 五、需要确认的两点

### 5.1 各 agent 的同步范围是否互斥

`[data_proxy] name` 决定取哪个 agent 的股票池和热门行业。股票池按 agent 分配，可能天然不重叠；
但「热门行业」只按 agent 分哪些行业热门，**行业成分股是市场级的**——两个 agent 同时看好相关行业时，
成分股会重叠。重叠部分会被两个实例各采一遍：实时表出现重复记录、MQTT 重复推送。

体检脚本会给出重叠的具体数量与代码。这一项需要连上实际数据库跑一次才能确认。

### 5.2 分钟线任务只覆盖股票池，不含热门行业

`minute_hq_sync_task` 取的是 `load_agent_pool_stocks()`，而 `real_time_hq_sync_task` 取的是
`get_real_time_stocks()`（股票池 + 热门行业）。结果是热门行业的股票有实时行情记录，却从不生成分钟线。

这是改造前就有的差异，与多实例无关，但会直接影响整体覆盖：如果某个 agent 的范围里热门行业占比高，
该实例的分钟线产出会明显少于实时行情。体检脚本会把「股票池数 / 热门行业数」分开打印，据此可以判断
缺口大小。是否要让分钟线也覆盖热门行业，待确认。

### 5.3 历史 K 线在 15:00 多实例并发

各实例同时 15:00 触发 `his_hq_sync_task`，各自查库判断「最近交易日是否已有日线」。
范围若重叠，在任一实例写库前各方都会判为缺失，同一只股票被拉多次。分片互斥后自动解决，不额外改动。

## 六、验证

本机 Python 无项目依赖（无 pandas / pymongo / yaml），采用按文件路径加载模块、AST 抽取函数、
stub 替身的方式离线验证，全部通过：

- 语法检查 9 个文件全部 OK；
- 未设置 `Q_PROXY_CFG` 时路径为 `cfg/stock.cfg`、实例名为 `q_share_proxy_1`（与改造前一致）；
- 三份配置下读到三个不同的 api_token 与实例名；
- 模拟三个容器 PID 相同的情况，client_id 互不相同；改回只拼 PID 则三者同为 1 个；
- 日志占位符替换出三个不同文件名，不含占位符的 handler 未被改动，实例名为空时不替换；
- 默认 `cfg/log.yaml` 的 filename 仍为 `log/info.log` 与 `log/errors.log`；
- 生产代码中残留的硬编码配置读取为 0 处；
- 体检脚本用合成数据跑 4 组场景：互斥且在容量内、热门行业重叠、单实例超上限、code 归一化
  （`000001` / `000001.SZ` / `sh600519` / `300750/SZ` 均能归一）。

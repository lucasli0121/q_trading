# q_data_proxy 代码评审：定时同步与性能

评审日期：2026-09-18
评审范围：`stock_fetch/stock_fetch.py`（主）、`db/mongo/*`、`cfg/stock.cfg`

## 结论

已修复 8 处，其中 3 处为功能级缺陷（导致数据缺失或配置不生效）。另有 5 处涉及业务口径或需要运维决策，未擅自改动，列在第三节。

## 一、定时器验收核对

### real_time_hq_sync_task

| 要求 | 实现 | 结论 |
| --- | --- | --- |
| 间隔 = `real_time_sync_interval` | cron `minute="*"`, `second="*/6"` | 满足，但非 60 约数的取值会静默偏差，已加校正 |
| 每次同步 = `real_time_stock_limit` | 按 `real_time_skip` 切片，每批 5 只 | 满足，但此前配置段名读错，已修 |
| 数据源 = 分配股票池 + 分配热门行业 | `get_real_time_stocks()` 合并两源去重 | 满足，但热门行业部分此前被覆盖，已修 |

### his_hq_sync_task

| 要求 | 实现 | 结论 |
| --- | --- | --- |
| 请求间隔 = `history_hq_sync_interval` | 每批之间 `time.sleep(6)`，末批跳过 | 满足 |
| 每次请求 = `history_hq_sync_number_of_once` | 每批切片 1 只 | 满足 |
| 触发时机 | 交易日 15:00 | 已按 D2 调整为 15:00 单次，且只拉缺失 |

## 二、已修复

### P0-1 热门行业股票列表被循环覆盖

位置 `get_hot_industry_stocks`

```python
hot_codes = []
for industry_stocks in industry_stocks_list:
    hot_codes = [s["code"] for s in codes_industry_list if s.get("code")]  # 用 = 覆盖
```

每轮循环用 `=` 重新赋值，最终列表里只剩最后一个行业的最后一批代码。消费方有两处：`get_industry_and_pool_stocks`（历史 K 线同步的股票范围）和 `get_real_time_stocks`。

改为 `hot_codes.extend(...)`，累加全部行业。

### P0-2 实时同步的股票数量上限读错配置段

位置 `StockFetch.__init__`

```python
self.real_time_limit = int(cp.get("tickflow", "real_time_stock_limit", fallback=5))
```

`real_time_stock_limit` 实际写在 `[stock_fetch]` 段，`[tickflow]` 段没有该项，于是永远取 fallback 5。工程上表现为「改了配置不生效」，只在数值恰好等于 5 时看不出来。

改为优先读 `[stock_fetch]`，回退 `[tickflow]`，保留旧配置兼容。

### P0-3 MQTT 推送因 code 格式不一致被静默跳过

位置 `find_pool_id_in_sync_stocks`

原实现用 `stock_code != code` 精确比较。但两侧来源不同：

- `code` 来自 tickflow 返回的 symbol
- `stocks_list` 里的 code 来自库里 `data_agent_pool_stocks_tbl` / `data_agent_industry_stocks_tbl`

任一环节格式不同（`000001` / `000001.SZ` / `sh000001`）就匹配失败，`return None`，推送被跳过且只留一行「未推送」的静默结果。调用方 `_publish_real_time_hq` 里的 `code.replace("/", ".")` 只掩盖了一部分情况。

改为精确匹配优先、纯数字归一化兜底。改完 `000001`、`000001.SZ`、`000001/SZ` 互相都能匹配上。纯数字 6 位唯一，不会误配。

### P1-1 分钟行情分组未按 code 归一化

位置 `do_minute_hq_sync_with_time`

docstring 写的是「按 code 分组：通过提取纯数字归一化 key」，代码实际用原始 code 做 key。同一只股票若在 `rt_stocks_tbl` 中因 `bulk_upsert_rt_stock_info` 的正则匹配而存在多种格式，会被拆成多组，生成多条 `minute_time` 相同的记录。落库时 `bulk_upsert_minute_hq` 按 `(code, minute_time)` 归一化后 upsert，后者覆盖前者，成交量/成交额直接算错。

已按 docstring 的声明补上归一化。

### P1-2 历史 K 线同步的线程无并发收益

位置 `do_his_hq_sync_task`

```python
thread = threading.Thread(target=self._fetch_and_save_his_hq, args=(...))
thread.start()
thread.join()   # 立刻 join，等于同步调用
```

`start()` 后立即 `join()`，批与批之间串行，线程只贡献创建和销毁开销。已改为直接调用。`threading` 导入随之删除。

### P1-3 调度间隔无法被 cron 精确表达时静默偏差

位置 `start()`

间隔 6 秒时表达式 `second="*/6"` 准确。但间隔 90 秒会算出 `minute="*/1"` + `second="*/30"`，实际每 30 秒触发一次，比配置快 3 倍且无任何提示。

已加入校正逻辑：小于 60 秒时取不超过配置值的最大 60 约数（1/2/3/4/5/6/10/12/15/20/30），大于等于 60 秒时向下取整到 60 的倍数，两种情况都打 warning 日志。

### P3 其他清理

- `MongoHotIndustryImpl` 导入后未使用（`stock_fetch.py`）
- `JobSubmissionEvent`、`EVENT_JOB_REMOVED`、`EVENT_ALL_JOBS_REMOVED`、`EVENT_SCHEDULER_SHUTDOWN` 导入后未使用
- `his_hq_sync_task` 中 `is_session` 判断了两次，第二次必然为真

## 三、待决策（未改动）

### D1 分钟 OHLC 的口径与 docstring 不一致

位置 `do_minute_hq_sync_with_time`

| 字段 | docstring 声明 | 代码实现 |
| --- | --- | --- |
| `open` | 首条记录的价格 | `first_rec.get("open")`，即当日开盘价 |
| `high` | price 字段极值 | `first_rec.get("high")`，即当日最高价 |
| `low` | price 字段极值 | `first_rec.get("low")`，即当日最低价 |
| `preclose` | 前一分钟分频行情的收盘价 | `first_rec.get("preclose")`，即当日昨收 |

两种口径都说得通：取窗口内极值才是标准分钟线，取当日值则让每条分钟记录都自带当日高低。但 `preclose` 用当日昨收意味着 `change_percent` 是相对昨收的涨跌幅，与 docstring 描述的「相对前一分钟」不同。

改的是数据语义，需要确认哪边是对的，故未动。

**回复**: preclose为昨收盘价格 close为上一分钟last_rec的price价格，docstring说明有误

**处理**: 代码口径正确，改 docstring。`do_minute_hq_sync_with_time` 与 `minute_hq_sync_task` 的说明改为：open / high / low / preclose 取窗口内首条记录的当日字段，close 取窗口内末条记录的 price，change_percent 相对昨收。`query_minute_hq_by_time` 里「用于获取前一分钟收盘价作为 preclose 计算依据」的过时说明一并删除。

另有一处已确认保持现状：`price` 取窗口内首条记录的 price，与 `close`（末条 price）取值不同，属既有设计，保留不改。

### D2 历史 K 线每轮全量重复拉取

`do_his_hq_sync_task` 直接对全部股票调 `_fetch_and_save_his_hq`，没有复用 `get_stock_day_his_hq` 里的缺失判断。每个交易日 11:00 和 15:00 各把全部股票近 3 天的数据拉一遍，其中绝大多数是重复的。

改成「只拉缺失」有个副作用需要先定：11:00 那次已经写过当天记录，15:00 再按「库中是否存在」判断，当天数据会被判为已存在而不再刷新，收盘价就停留在 11:00 的盘中值。

可选方案是按「区间内最新 `create_time`」判断而非「是否存在」，这需要引入「最近已收盘交易日」的概念。请确认期望行为后再改。

**回复**: 可以把11点取消，只在15:00拉取一次，同时根据[库中是否存在]只拉缺失，源代码拉取日期为3天，是为了担心上次拉取失败或者进程突然杀死，跳过了15点拉取时间，3天为冗余时间，如果有更好的方案可以采纳。

### D3 过期数据清理任务未注册到调度

`remove_real_stock_over_time`（清理 60 天前的实时行情/分钟线/日周月 K，30 天前的估值）已实现，但 `start()` 里没有任何 `add_job` 引用它。当前这些表只增不减。

这是删除操作，清理窗口和触发时间需要你确认，未擅自注册。

**回复**: 这个不用操作，清理工作在q_trading_server里面调度了

**处理**: 无需改动。`remove_real_stock_over_time` 保留在本服务代码中但不注册调度，由 q_trading_server 侧调度触发。

### D4 全部查询带 collation，且无自建索引

`mongo_exec.query_by_condition` 每次查询都挂 `.collation({'locale':'zh', 'strength':1})`。MongoDB 要求索引具备相同 collation 才会被使用，否则走全表扫描。

同时全项目搜索 `create_index` / `ensure_index` 无任何结果，除 `_id` 外没有自建索引。`rt_stocks_tbl` 在交易时段每 6 秒写入一批、保留 60 天，`minute_hq_tbl` 每分钟写入，查询条件都是 `code` + 时间范围，目前均为全表扫描。

建议给以下字段建复合索引，并按需统一 collation：`rt_stocks_tbl(code, create_time)`、`minute_hq_tbl(code, minute_time)`、`day_hq_tbl(code, create_time)`。

**回复**: 可以建立索引

**处理**: 已落地。`MongoExec.ensure_indexes()` 在 `main.py` 的 `start_app()` 中调用一次，建立的索引与 `query_by_condition` 携带的 `Collation(locale="zh", strength=1)` 完全一致，否则索引不会被使用：

| 表 | 索引 | 对应查询 |
| --- | --- | --- |
| `rt_stocks_tbl` | `(code, create_time desc)` | 按 code 过滤 + create_time 范围，排序 create_time 倒序 |
| `minute_hq_tbl` | `(code, minute_time)` | 按 code 过滤 + minute_time 范围，排序 minute_time 正序 |
| `day_hq_tbl` | `(code, create_time)` | 按 code 过滤 + create_time 范围，排序 code + create_time 正序 |

首次在已有大表上建索引会消耗一定时间，任务日志会打印 `MongoDB 索引已就绪: <表>.<索引名>`；索引创建失败只记 error，不阻塞服务启动。

### D5 实时轮询周期超过分钟快照窗口

`real_time_hq_sync_task` 每 6 秒同步 5 只，一只股票被采样一次需要 `6 × ceil(N / 5)` 秒。`minute_hq_sync_task` 每 2 分钟聚合一次窗口内数据，窗口内的采样条数直接决定分钟线的成交量计算是否成立。

> 补充（2026-09-21）：分钟成交量的计算口径同时存在缺陷，已按行业口径修正，见 `docs/issue-minute-volume-2026-09-21.md`。该项决定的是窗口内采样条数 n，口径修正后 n 越小噪声越大，但不再产生 `-1/n` 的系统性偏差。

实测推算：

| 股票数 N | 一轮周期 | 窗口（`minute_sync_interval=2`） | 结果 |
| --- | --- | --- | --- |
| 50 | 60s | 120s | 窗口内每股约 2 条 |
| 100 | 120s | 120s | 窗口内每股约 1 条，临界 |
| 200 | 240s | 120s | 半数股票窗口内无采样 |
| 500 | 600s | 120s | 每分钟线只覆盖约 1/5 股票 |

要保证每分钟每股至少一条，需满足 `ceil(N / limit) × interval ≤ minute_sync_interval × 60`。当前配置（limit=5、interval=6、窗口 120s）的安全上限是 N ≤ 100。股票池若超过 100 只，需要上调 `real_time_stock_limit`（tickflow 的 `/quotes` 接口支持批量传 symbols）或下调 `real_time_sync_interval`。

**回复**: 目前采用的是tickflow免费使用权限，对拉取数据有限制，后面会付费提高限制

**处理**: 无需改动。约束关系 `ceil(N / real_time_stock_limit) × real_time_sync_interval ≤ minute_sync_interval × 60` 与当前安全上限（N ≤ 100）已写入 `cfg/stock.cfg` 中 `real_time_stock_limit` 上方注释，待付费提额后按同一关系重新核算。

> 2026-09-21 补正：免费套餐单次最多 5 个标的，`real_time_stock_limit` 无法上调；且 6 秒一次正好等于 10 次/分钟的硬上限，会触发 429。实际可用采样能力为 45 标的/分钟，N ≤ 45。详见 `docs/issue-429-rate-limit-2026-09-21.md`。

### D6 调度实例数自动扩容与共享状态无锁保护

`jobListener` 在收到 `EVENT_JOB_MAX_INSTANCES` 时会 `modify_job(max_instances=jobCount + 10)`，逐次递增。任务里的 `self.real_time_skip`、`self.real_time_sync_stocks` 是实例级共享状态，读改写三步不原子。一旦并发实例数被放大到 1 以上，这两个状态会产生竞态，表现为同一批股票被重复同步或分批指针错乱。

建议移除自动扩容逻辑，或改为按 job 加锁。

**回复**: 可以移除自动扩容

**处理**: 已移除。`jobListener` 收到 `EVENT_JOB_MAX_INSTANCES` 时只记录「本次执行已被跳过」的告警，不再 `modify_job` 放大 `max_instances`；同时给 `real_time_hq_sync_task`、`minute_hq_sync_task`、`his_hq_sync_task` 显式声明 `max_instances=1`，把「单实例执行」这一约束写在调度定义处。

## 四、本轮改动文件

| 文件 | 改动 |
| --- | --- |
| `stock_fetch/stock_fetch.py` | D1 口径 docstring、D2 调度与缺失筛选、D6 监听器与实例数 |
| `db/mongo/mongo_exec.py` | 新增 `INDEX_SPECS` 与 `ensure_indexes()` |
| `main.py` | 启动时调用 `ensure_indexes()` |
| `cfg/stock.cfg` | D5 参数约束注释 |
| `db/mongo/mongo_stock_minute_hq_impl.py` | D1 过时说明修正 |

## 五、容量与性能小结

| 项目 | 当前值 | 说明 |
| --- | --- | --- |
| 实时行情 API 调用 | 每 6 秒 1 次 | 单次 5 个 symbols |
| 实时轮询安全上限 | N ≤ 45 只 | 见 D5；2026-09-21 按免费套餐额度补正 |
| 历史 K 线每日请求数 | 仅缺失股票数 | 见 D2，全部已入库时为 0 |
| 历史 K 线请求间隔 | 每 6 秒 1 只 | N=100 只全缺时约 10 分钟，N=500 只全缺时约 50 分钟 |
| 数据库索引 | 3 个复合索引（带 zh collation） | 见 D4 |
| 过期数据清理 | 由 q_trading_server 调度 | 见 D3 |

校验脚本与原始输出留存于 `%TEMP%\qreview_check.txt`、`%TEMP%\qreview_behavior.txt`、`%TEMP%\qdecision_check.txt`、`%TEMP%\qdecision_behavior.txt`。

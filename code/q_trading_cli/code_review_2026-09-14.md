# q_trading_cli 代码审查清单

- **审查日期**：2026-09-14
- **审查范围**：`pages/`、`core/`、`api/`、`mq/`、`middleware/`、`main.py`、`dao/`
- **审查方法**：静态阅读 + 后端 `openapi.json` 契约核对 + NiceGUI 源码确认（`nicegui/timer.py`）
- **行号说明**：行号为审查时快照，代码改动后可能偏移，以「位置」中的函数名/代码特征为准

---

## 使用方式

1. 逐条阅读「现象 / 证据 / 影响」，判断是否需要修、怎么修
2. 在每条 **`修改方案`** 处填写你的方案（可以只写一句方向，我来补实现细节）
3. 填写 **`优先级调整`**（如果和我的判定不同）
4. 填完告诉我，我按 `CR 编号` 顺序逐条实施，每条改完回填「状态」和「验证结果」
5. 不想修的条目直接写「不修」并说明原因，我会跳过

**状态取值**：`待确认` → `已确认` → `修改中` → `已完成` / `不修` / `暂缓`

**等级定义**

| 等级 | 含义 |
|------|------|
| P0 | 正在持续造成伤害（打爆后端 / 阻塞全进程 / 安全串号），建议当次修完 |
| P1 | 功能不正确或用户可见缺陷，需要尽快修 |
| P2 | 隐患、体验问题、代码质量，可排期 |

---

## 总览

| 编号 | 等级 | 位置 | 一句话 | 状态 |
|------|------|------|--------|------|
| CR-01 | P0 | `pages/strategy_monitor.py:148` | 监控页 1s 定时器：每秒最多 2000 个 HTTP + 阻塞事件循环 | 已完成 |
| CR-02 | P0 | `pages/main_page.py:855` | 首页「近期运行状态」1s 定时器同型问题 + `limit` 语义错误 + 每秒重建 DOM | 已完成 |
| CR-03 | P0 | 全局（`ui.timer` 用法） | 定时器同步回调跑在 asyncio 事件循环上，且耗时超周期后退化为紧循环 | 已完成 |
| CR-04 | P0 | 全局（`strategy_api.get_by_id`） | 策略模板查询零缓存，占请求量约 95% | 已完成 |
| CR-05 | P0 | `api/client.py`（`_fallback_token`） | fallback token 是进程级单变量 → 多用户会话串号（安全问题） | 已完成 |
| CR-06 | P1 | `core/user_strategy_manager.py` | `user_strategies` 缓存键可能不存在 → KeyError | 已完成 |
| CR-07 | P1 | `core/user_strategy_manager.py` + 各子页面 | 缓存只在首页加载一次，子页面无刷新路径，后端改动看不到 | 已完成 |
| CR-08 | P1 | `core/user_strategy_manager.py` | 缓存无 TTL / 无失效钩子；`remove_user_strategy` 是死代码 | 已完成 |
| CR-09 | P2 | `core/user_strategy_manager.py`（`get_user_strategy`） | 死代码 + O(n·m) 嵌套实现 | 已完成 |
| CR-10 | P1 | `mq/signal_manager.py`（`get_signals_by_user_strategy`） | 不排序；`limit` 是「每策略」而非全局 | 已完成 |
| CR-11 | P1 | `mq/signal_manager.py`（`_history_loaded`） | 进程级标志 → 只有第一个用户能加载历史信号 | 已完成 |
| CR-12 | P2 | `pages/strategy_select.py`（`_load_quotes`） | 表格无客户端排序，去重结果依赖后端顺序 → 数据抖动 | 已完成 |
| CR-13 | P2 | `pages/strategy_select.py` + 后端 | DAO 有 `score` 但接口不返回，前端无法按评分排序 | 已完成 |
| CR-14 | P2 | `pages/strategy_select.py`（`_load_quotes`） | `codes_seen` 是 set，join 出的参数顺序不确定 | 已完成 |
| CR-15 | P2 | `pages/strategy_select.py:249` | 选股首屏同步调用，阻塞首次渲染 | 已完成 |
| CR-16 | P1 | `pages/strategy_monitor.py`（`_set_filter`） | 点击筛选标签，高亮永远不变 | 已完成 |
| CR-17 | P2 | `pages/main_page.py`（近期更新表） | 用策略模板名而非 `user_strategy.name`，与策略列表页不一致 | 已完成 |
| CR-18 | P2 | `pages/main_page.py:855` | 活动流每秒 `clear()` 重建 → 闪屏、丢滚动位置 | 已完成 |
| CR-19 | P1 | `middleware/security.py`（`ALLOWED_PREFIXES`） | 漏 `/strategies`、`/strategy-select`，被扫描器正则检查 + 限流 | 已完成 |
| CR-20 | P1 | `main.py`（`storage_secret`） | 密钥硬编码在源码里 | 已完成 |
| CR-21 | P1 | `pages/strategies.py:546` | 表格每行触发一次 `/api/user_strategy/list`，单次渲染 10 个重复请求 | 已完成 |
| CR-22 | P2 | `mq/signal_manager.py`（`__init__`） | `hasattr(self, "_initialized")` 不是有效的单例守卫 | 已完成 |
| CR-23 | P2 | `mq/signal_manager.py`（`_signals`） | 进程级长期驻留，无清理机制 | 已完成 |
| CR-24 | P2 | `api/order.py:170` | `get_latest` docstring 说返回 dict，实际已改为 list | 已完成 |
| CR-25 | P2 | `api/trade_signal.py`（`list`） | docstring 漏了 `stock_codes` 参数 | 已完成 |

**建议实施顺序**：CR-01 → CR-02 → CR-03 → CR-04 → CR-05 → CR-21 → CR-06 → CR-07 → CR-08 → CR-10 → CR-11 → CR-19 → 其余

---

# A 组：定时器与事件循环阻塞（P0）

## CR-01 监控页 1s 定时器：每秒最多 2000 个 HTTP

- **位置**：`pages/strategy_monitor.py:148`
- **等级**：P0
- **现象**：`ui.timer(1.0, _render_table)`，`_render_table` 内部为：

  ```python
  signals = signal_manager.get_signals_by_user_strategy(user_strategy_ids, limit=200)
  for sig in signals:
      strategy_dao = AppContext().strategy_api.get_by_id(sid)   # 每条信号一次 HTTP
  ```

- **证据**：
  - `get_signals_by_user_strategy([...], limit=200)` 的 `limit` 是**每个策略**的上限（内部切片是 `signal[:limit]`），10 个策略 → 最多 2000 条信号
  - 每条信号都发一次 `strategy_api.get_by_id`，且**无任何缓存**，同一 `sid` 被重复请求
  - 定时器回调是同步函数 → 全部在事件循环上串行执行
- **影响**：每秒钟上千个后端请求，后端 `example.com:8000` 会被打爆；前端事件循环被阻塞，整个服务所有用户一起卡死
- **修改方案**：strategy做缓存，同时要注意多用户不冲突
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：监控页改用 PollingTask(POLL_PERIOD_SECONDS=1.0)（重入保护 + 最小间隔），数据拉取走 run_blocking 线程池；策略名改走 strategy_cache.get_many（进程级缓存）。单次刷新最多 1 次真实后端请求，_render 内已无逐信号 get_by_id。

## CR-02 首页「近期运行状态」1s 定时器：同型问题 + 语义错误

- **位置**：`pages/main_page.py:855`
- **等级**：P0
- **现象**：`ui.timer(1.0, _render)`，`_render` 内每信号一次 `strategy_api.get_by_id`；另外 `_render` 用 `limit=4` 取「最近 4 条」
- **证据**：
  - 每信号一次 HTTP，每秒最多 `4 × 策略数` 个请求，且阻塞事件循环
  - `limit=4` 是**每策略**上限，实际会渲染 `4 × 策略数` 行，不是「最近 4 条」
  - `get_signals_by_user_strategy` **完全不排序**，所以展示的既不是「最近」也不是「4 条」
- **影响**：与 CR-01 同级的后端压力；同时这块 UI 表达的信息本身是错的（标题说「近期运行状态」，实际是随机取的每策略若干条）
- **修改方案**：需要保证根据实际倒序排列，最近时间放在前面，策略做缓存
- **优先级调整**： P0
- **状态**：已完成
- **验证结果**：改用 signal_manager.get_signals(limit=ACTIVITY_COUNT) 取全局最近 N 条（内部按 create_time 倒序）；拉取走 run_blocking + strategy_cache；_render 先比对数据签名，未变化直接跳过 clear()+重建。

## CR-03 定时器同步回调阻塞事件循环 + 负 sleep 退化紧循环（架构级）

- **位置**：全局所有 `ui.timer` 调用点
- **等级**：P0
- **现象**：NiceGUI 的 `ui.timer` 同步回调**直接在 asyncio 事件循环上执行**，不是线程池
- **证据**：NiceGUI 源码 `nicegui/timer.py`：

  ```python
  async def _invoke_callback(self) -> None:
      result = self.callback()          # 同步回调直接在这里执行
      if helpers.should_await(result):
          await result
  ```

  以及 `_run_in_loop` 中的 `await asyncio.sleep(self.interval - dt)`——当回调耗时 `dt > interval` 时，`sleep` 收到负数立即返回，**退化为无间隔紧循环**，比 1 次/秒还猛
- **影响**：这是 CR-01 / CR-02 后果被放大的根因。只要定时器里有同步 HTTP，就是全进程阻塞 + 可能的紧循环
- **修改方案**：检查负的sleep， 同时解决http阻塞问题， ui.timer不能在线程池调用
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：新增 core/concurrency.py：1) run_blocking() 用 contextvars.copy_context() 把请求上下文带进线程池；2) PollingTask 以「本轮结束时刻 + 完整周期」作为下一轮最早开始时间，并在冷却期主动 await asyncio.sleep(min(剩余, tick))，避免 NiceGUI 内部 sleep(interval-dt) 收到负数后退化成紧循环；同步回调走线程池、async 回调直接 await（inspect.iscoroutinefunction 分支）。

> 备注：现有定时器清单，供评估统一改造时参考

| 位置 | 周期 | 每次代价 | 评价 |
|------|------|------|--------|------|
| `strategy_monitor.py:148` | 1s | 最多 2000 个 HTTP + 阻塞 | 必须改 |
| `main_page.py:855` | 1s | 最多 4×N 个 HTTP + 阻塞 + 重建 DOM | 必须改 |
| `main_page.py:613` | 3s | 纯内存 | 可接受 |
| `main_page.py:449` | 10s | 1 个 HTTP | 建议改 async |
| `main_page.py:1021` | 60s | 约 6 个 HTTP + 整表重建 | 建议加缓存 |
| `main_page.py:101` / `283` | 1s | 纯内存心跳 | 可接受 |

## CR-04 策略模板查询零缓存

- **位置**：全局（`AppContext().strategy_api.get_by_id(...)` 所有调用点）
- **等级**：P0
- **现象**：策略模板（`strategy_api.get_by_id`）在项目里被高频调用，但没有任何缓存层
- **证据**：调用点统计——监控页每信号一次、首页每秒 4 次、策略列表每模板一次、选股页每 Tab 一次
- **影响**：这是后端请求量的主要来源（估约 95%）。加一层 `{sid: tpl}` + 短 TTL 缓存即可直接砍掉绝大部分请求
- **修改方案**：需要缓存
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：新增 core/strategy_cache.StrategyTemplateCache：正缓存 TTL 300s、负缓存 5s、RLock 线程安全、返回值一律 deepcopy（防止调用方改 default_params 污染共享缓存），经 AppContext().strategy_cache 懒加载提供。全项目 get_by_id 调用点已全部改走缓存。

---

# B 组：会话与缓存一致性

## CR-05 fallback token 是进程级单变量 → 多用户串号

- **位置**：`api/client.py`（`set_fallback_token` / `clear_fallback_token` / `_resolve_token`）
- **等级**：P0（安全）
- **现象**：

  ```python
  def set_fallback_token(self, token): self._fallback_token = token
  ```

  `ApiClient` 是全局单例，`_fallback_token` 只有一份
- **影响**：
  - 多用户场景下后登录的覆盖先登录的 → A 用户的后台线程（MQTT 回调 / `run_in_executor`）会带着 **B 用户的 token** 发请求
  - 任一用户 401 触发 `clear_fallback_token()` → 所有用户的后台认证一起失效
  - 属于会话串号，可能造成越权数据读取
- **修改方案**：检查nicegui中app.storage源码或者帮助文档，看采用哪种方法修改更合适
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：彻底移除 _fallback_token / set_fallback_token / clear_fallback_token（进程级单变量）。改为依赖 core.concurrency.run_blocking 的 contextvars 继承：NiceGUI Client 长期持有初始 Request，复制上下文后工作线程可解析出**本会话** token，多用户不串号。附带修复：pages/login_page.py 注册成功分支仍在调用已删除的 set_fallback_token（会 AttributeError）。

## CR-06 `user_strategies` 缓存键可能不存在 → KeyError

- **位置**：`core/user_strategy_manager.py`
- **等级**：P1
- **现象**：`load_user_strategies()` 全项目**只在首页 `main_page.py:140` 调用一次**，但 `/strategies`、`/strategy-select`、`/strategy-monitor` 都直接读 `app.storage.user["user_strategies"]`（`list_user_strategies`、`get_user_strategy_ids` 等全是直接下标）
- **证据**：
  - `Singleton` 元类的 `__init__` 全进程只跑一次，所以 `__init__` 里那句 `app.storage.user["user_strategies"] = []` **只对触发首次实例化的那个会话生效**
  - `app.storage.user` 是 dict 语义，缺 key 抛 KeyError
- **复现路径**：用户登录后直接落到 `/strategies`（`referrer_path` 就是它）→ 缓存键不存在 → 目前被 `main_page_with_content` 那层 `try/except` 兜成「页面加载失败」
- **修改方案**：load_user_strategies 提到 main_page_with_content中，同时在load_user_strategies增加时间间隔，避免频繁加载
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：_cache() 全部改为 app.storage.user.get(_CACHE_KEY, []) 兜底，消除 KeyError；不再在 __init__ 里写会话存储。新增 ensure_loaded()，由 main_page() 与 main_page_with_content() 调用（/strategies、/strategy-select、/strategy-monitor 等路由均经后者注册，已逐个核对 main.py）。

## CR-07 缓存只在首页加载一次，子页面无法感知后端变更

- **位置**：`core/user_strategy_manager.py` + `pages/strategies.py` / `strategy_select.py` / `strategy_monitor.py`
- **等级**：P1
- **现象**：子页面从不刷新缓存
- **证据**：`load_user_strategies` 的唯一调用点是 `main_page.py:140`
- **影响**：后端改了 `status`（例如策略被外部停止）→ 策略列表一直显示旧状态，直到重新进首页
- **修改方案**：和CR-06处理逻辑一样
- **优先级调整**：P0
- **状态**：已完成
- **验证结果**：ensure_loaded() 按会话节流（RELOAD_MIN_INTERVAL_SECONDS=60），子页面进入即最多经 60 秒窗口感知后端状态变更。

> 注：`update_user_strategy` 的「整体替换丢字段」问题已于 2026-09-13 修掉（改为合并更新），本条针对的是**跨会话 / 外部变更的失效机制缺失**。

## CR-08 缓存无 TTL / 无失效钩子；`remove_user_strategy` 是死代码

- **位置**：`core/user_strategy_manager.py`
- **等级**：P1
- **现象**：
  - 没有 TTL，没有失效钩子，没有任何「主动拉取」入口
  - `remove_user_strategy` **没有任何调用方**（死代码）→ 删除策略不清理缓存
- **影响**：缓存生命周期与真实数据脱钩，删除/停用策略后页面仍显示
- **修改方案**：当用户在页面删除策略时，需要同步调用 remove_user_strategy
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：load_user_strategies() 失败时不更新 _LOADED_AT_KEY，便于下次重试；remove_user_strategy() 修正为可安全调用；新增 delete_user_strategy()（调用后端 delete 后清缓存）供后续删除入口接入。

## CR-09 `get_user_strategy` 死代码 + O(n·m) 实现

- **位置**：`core/user_strategy_manager.py`（`get_user_strategy`）
- **等级**：P2
- **现象**：无任何调用方，且实现是 `{id: strategy for id in ids for strategy in [...]}` 的嵌套推导
- **影响**：阅读时误导（看起来像在用），写法本身也不对（构造出的是 dict 而非按 id 查记录）
- **修改方案**：检查项目中是否有需要通过user_strategy_id获取用户策略的，如果有就补上，同时修改get_user_strategy内部错误
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：get_user_strategy(user_strategy_id) 改为单 ID 精确查询（原为 O(n·m) 嵌套推导且返回结构错误）；已接入使用：pages/strategies.py 行数据组装与参数对话框默认参数回填。

---

# C 组：数据正确性（你重点问的第 3 点在此）

## CR-10 `get_signals_by_user_strategy` 不排序，`limit` 是「每策略」

- **位置**：`mq/signal_manager.py`（`get_signals_by_user_strategy`）
- **等级**：P1
- **现象**：返回结果「按策略分组后拼接」，不是时间序；`limit` 语义是每策略上限
- **影响**：
  - 监控页「实时信号日志」顺序错乱，不是时间序
  - 调用方普遍误以为 `limit` 是全局条数（CR-01、CR-02 的放大因素）
- **修改方案**：需要排序, limit 依然为每策略上限
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：_collect() 统一按 create_time 倒序（字符串比较，格式固定）；limit 按你的裁定保持「每策略」语义并在 docstring 中写明。

## CR-11 `_history_loaded` 是进程级标志 → 只有第一个用户有历史信号

- **位置**：`mq/signal_manager.py`（`load_history` / `_history_loaded`）
- **等级**：P1
- **现象**：`load_history` 用进程级标志位做「只加载一次」的守卫
- **影响**：只有第一个登录的用户能加载到历史信号，**其他用户永远没有历史数据**（跨用户 bug）
- **修改方案**：修改为每个用户只加载一次标志
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：「历史信号已加载」标志从进程级布尔改为 app.storage.user['signal_history_loaded']，按会话隔离，每个登录用户各自加载一次。

## CR-12 策略选股表格无客户端排序，去重依赖后端顺序

- **位置**：`pages/strategy_select.py`（`_load_quotes`）
- **等级**：P2
- **现象**：表格行的顺序完全依赖后端返回顺序，客户端一次排序都没做
- **证据**：
  - 后端 `list` 接口没有排序参数，返回顺序由后端决定
  - 去重逻辑是「保留首条」，**哪条算首条也取决于后端顺序**——同一 `code` 有多条记录时，展示的 `price` / `rt_update_time` 会在不同刷新之间抖动
- **影响**：用户看到的顺序不稳定；同一只股票显示的价格可能来回跳
- **修改方案**：后台默认排序按照score字段正向排序，前端页面需要显示分数(保留2位小数)，后台做了去重处理
- **优先级调整**： P2
- **状态**：已完成
- **验证结果**：前端不再自行「保留首条」去重（后端已去重），仅按后端返回顺序收集代码；表格新增「选股评分」列（紧随代码/名称），按 score 展示，保留 2 位小数。

## CR-13 `score` 字段 DAO 有、接口不返回

- **位置**：`dao/strategy_select_stocks.py`（`score`）+ 后端 `StrategySelectStockWithRtItem`
- **等级**：P2
- **现象**：DAO 里明明有 `score`（选股评分）字段，但后端 `StrategySelectStockWithRtItem` **不返回 score**
- **影响**：前端没法按评分排序。如果选股结果本就该按评分排，这需要后端补字段
- **修改方案**：后台已经在接口实现了，具体看docs说明:/api/strategy_select_stocks/list
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：接口已返回 score，前端读取并展示（_sf(q.get('score', 0))，f'{score:.2f}'），列位置对应后端按 score 正向排序。
- **是否需后端配合**：是（需确认）后端已经修改了接口，提供score字段

## CR-14 `codes_seen` 是 set，join 出的参数顺序不确定

- **位置**：`pages/strategy_select.py`（`_load_quotes`）

  ```python
  codes_str: str = ",".join(codes_seen)     # codes_seen 是 set
  ```
- **等级**：P2
- **现象**：传给 `finance_api` 的参数顺序不确定
- **影响**：目前无害（不影响结果），但会造成请求参数不可复现，排查问题时干扰判断；若将来后端对顺序敏感就是真 bug
- **修改方案**：当前端通过接口finance_api调用get_valuation时，后台会根据report_date排序
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：不再对 set 做 join；改为按后端返回顺序去重后的 list[str] 传给 finance_api，由 requests 编码为重复同名 query 参数，参数顺序可复现（后端按 report_date 排序）。

## CR-15 选股首屏同步调用，阻塞首次渲染

- **位置**：`pages/strategy_select.py:249`
- **等级**：P2
- **现象**：首次加载 `_load_quotes` 是**同步调用**，内部 3 个 HTTP 串行执行
- **影响**：首次进入页面的渲染被阻塞；与 CR-03 叠加时会放大卡顿
- **修改方案**：需要优化
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：首屏改为 state['loading']=bool(active_us_id) 先渲染占位，再用一次性 ui.timer(0.05, _load_initial, once=True) 异步加载；切 Tab/刷新走 await run_blocking(_load_quotes)。

---

# D 组：交互与 UI

## CR-16 监控页筛选标签高亮永远不变

- **位置**：`pages/strategy_monitor.py`（`_set_filter` / `_filter_tag`）
- **等级**：P1
- **现象**：`_set_filter` 只改 state 不触发重渲染，`_filter_tag` 只在建页时渲染一次
- **影响**：点击筛选标签，**标签选中态永远是高亮不变**（数据靠 1s 定时器碰巧会跟着变，但标签本身是坏的）
- **修改方案**：需要修改
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：filter_tags() 改为 @ui.refreshable；_set_filter 在状态变化时刷新标签组，并用 last_data 立即重渲染表格，不再等下一轮轮询。

## CR-17 首页近期更新表用策略模板名，不用 `user_strategy.name`

- **位置**：`pages/main_page.py`（近期更新策略表格）
- **等级**：P2
- **现象**：用了 `tpl.get("name")`（策略模板名）
- **影响**：与策略列表页不一致，用户自定义名称在这里不显示
- **修改方案**：需要修改
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：名称取值链改为 us['name']（用户自定义）→ 模板名 → 策略 ID，与策略列表页一致。

## CR-18 活动流每秒 `clear()` 重建 → 闪屏、丢滚动位置

- **位置**：`pages/main_page.py:855`
- **等级**：P2
- **现象**：每秒 `activity_container.clear()` 后全量重建 DOM
- **影响**：视觉闪屏；用户正在滚动查看时会被重置到顶部
- **修改方案**：需要修改
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：活动流按 (time, name, action, code) 生成数据签名，与上次一致时直接 return，不执行 clear()/重建，消除闪屏与滚动位置丢失。

---

# E 组：安全与配置

## CR-19 `ALLOWED_PREFIXES` 漏掉两个路由

- **位置**：`middleware/security.py`（`ALLOWED_PREFIXES`）
- **等级**：P1
- **现象**：白名单漏了 `/strategies` 和 `/strategy-select`
- **影响**：这两个路径会走扫描器正则检查，并被计入「60 秒 30 次」的限流；正常用户访问策略列表/策略选股页可能被误拦
- **修改方案**：需要修改
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：ALLOWED_PREFIXES 精简为 /_nicegui、/api、/static；新增 ALLOWED_ROUTES 精确集合（含 /strategies、/strategy-select、/strategy-monitor 等全部应用路由）+ is_allowed_path()，扫描器中间件与限流中间件统一改用它；并补充「新增路由须同步登记」的注释提醒。

## CR-20 `storage_secret` 硬编码在源码里

- **位置**：`main.py`
- **等级**：P1
- **现象**：NiceGUI `storage_secret` 直接写在源码
- **影响**：密钥随代码库分发；任何人拿到源码即可伪造会话存储。建议移到 `cfg/stock.cfg` 或环境变量
- **修改方案**：需要修改
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：storage_secret 优先级：环境变量 Q_TRADING_STORAGE_SECRET > cfg/stock.cfg [web] storage_secret > secrets.token_hex(32) 随机生成（并打 WARNING 提示重启后会话失效）；cfg/stock.cfg.example 增加配置说明。另修复：原实现引用了 secrets 但 main.py 未 import（运行时 NameError）。

---

# F 组：性能细节、死代码与注释

## CR-21 策略列表表格每行一次 `/api/user_strategy/list`

- **位置**：`pages/strategies.py:546`（`_render_table` → `_latest_signal_text` → `get_signals(limit=50)`）
- **等级**：P1
- **现象**：`_latest_signal_text` 内部会调 `user_strategy_api.list()`（HTTP）
- **影响**：`PAGE_SIZE=10`，**单次表格渲染 = 10 个重复的 `/api/user_strategy/list` 请求**。翻页/筛选每次都会再来一轮
- **修改方案**：确认修改
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：_latest_signal_text 改为 _latest_signal_map(us_ids) 批量调用 signal_manager.get_latest_signals()（纯内存，不经 user_strategy_api.list()）；模板查询改 strategy_cache.get_many。每行一次 /api/user_strategy/list 已消除。

## CR-22 `SignalManager.__init__` 不是有效的单例守卫

- **位置**：`mq/signal_manager.py`（`__init__`）
- **等级**：P2
- **现象**：`if hasattr(self, "_initialized"): return` —— 这是**实例级**属性判断，新建实例时永远不具备该属性，所以这句 `return` 永不生效
- **影响**：实际单例性只靠 `AppContext` 缓存撑着。按文档示例直接 `SignalManager()` 会造出第二个空实例，信号数据分裂
- **修改方案**：需要修改
- **优先级调整**：P1
- **状态**：已完成
- **验证结果**：SignalManager 改 class SignalManager(metaclass=Singleton)，删除失效的 hasattr(self,'_initialized') 守卫。

## CR-23 `_signals` 进程级长期驻留，无清理机制

- **位置**：`mq/signal_manager.py`（`_signals`）
- **等级**：P2
- **现象**：上限为 `500 × 策略数`，但没有任何清理/淘汰逻辑
- **影响**：长时间运行内存持续增长；多用户共存时所有信号挤在同一个进程级容器里
- **修改方案**：需要增加清理机制，只记录12小时以内的
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：新增 prune_expired(retention_hours=12)：剔除超过 12 小时的信号，空条目直接回收；由 _maybe_prune() 以 60 秒间隔在 MQ 回调后触发，避免每条信号都全量遍历。

## CR-24 `api/order.py` 的 `get_latest` docstring 过期

- **位置**：`api/order.py:170` 左右
- **等级**：P2
- **现象**：docstring 写「返回单个最新订单字典，无匹配返回空字典」，**实际后端已改为返回 list**（代码实现是对的，注释过期）
- **影响**：后续维护者可能按注释写错调用方；也容易导致别人「按注释把代码改回单对象」
- **修改方案**：需要修改
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：api/order.py get_latest docstring 改为「最新订单列表（list[dict]），无匹配订单时返回空列表」。

## CR-25 `api/trade_signal.py` 的 `list()` docstring 漏参数

- **位置**：`api/trade_signal.py`（`list`）
- **等级**：P2
- **现象**：docstring 漏了 `stock_codes` 参数说明
- **修改方案**：需修改
- **优先级调整**：P2
- **状态**：已完成
- **验证结果**：api/trade_signal.py list() 补充 stock_codes 参数说明，类型由 list | None 收紧为 list[str] | None。

---

# 需后端配合 / 待你确认的项

| 编号 | 事项 | 需确认什么 |
|------|------|------------|
| CR-13 | 选股接口是否应返回 `score` | DAO 有该字段，接口不返回。是否要求后端补上？
- 后端已经补上了
| CR-10 | `get_signals_by_user_strategy` 的 `limit` 语义 | 是保持「每策略」并改文档，还是改成「全局 N 条」？改成全局会影响所有现有调用方
- 保持每策略限制
| CR-12 | 选股结果的期望排序 | 按 `create_time` 倒序？按 `score`？还是保持后端顺序不做客户端排序？
- 按score排序，create_time只有日期不能排序
| CR-05 | fallback token 的隔离粒度 | 按 NiceGUI 会话 ID 隔离，还是改成「不再依赖 fallback、全部走 request context」？后者改动面更大
- 检查nicegui中app.storage源码或者帮助文档，看采用哪种方法修改更合适

---

# 其他补充说明（供你判断，暂未列为独立条目）

- **`SignalManager` 与 `UserStrategyManager` 都是进程级单例**，但承载的是**用户相关数据**（`_signals`、`user_strategies` 缓存）。多用户并发时存在数据互相可见的风险，这是 CR-05 / CR-11 之外的**架构层隐患**，是否要处理取决于这个系统实际是单用户还是多用户部署。
- 项目只部署一套，单独进程。UserStrategyManager和SignalManager 在进程后台承载用户相关数据，并发时不应该让其他用户可见
- **`main_page.py` 有 6 个定时器**，即使单独看每个都可接受，叠加在一个页面上仍然可观。建议借 CR-03 统一收口为一个「轮询调度器」，按数据源分档（内存类 1s / 接口类 5~10s / 重查询 60s）。
- 可以这样修改


---

# 修复汇总（2026-08-30）

- **修复范围**：CR-01 ~ CR-25 全部 25 条，无「不修 / 暂缓」项。
- **新增文件**：`core/concurrency.py`（`run_blocking` / `PollingTask`）、`core/strategy_cache.py`（`StrategyTemplateCache`）。
- **改动文件**：`main.py`、`app_context.py`、`api/client.py`、`api/order.py`、`api/trade_signal.py`、
  `core/user_strategy_manager.py`、`mq/signal_manager.py`、`middleware/security.py`、
  `pages/main_page.py`、`pages/strategy_monitor.py`、`pages/strategy_select.py`、`pages/strategies.py`、
  `pages/stock_quotes.py`、`pages/login_page.py`、`menu/top_menu.py`、`cfg/stock.cfg.example`。
- **验证方式**：
  1. `python -m py_compile`（全部改动文件）→ 通过；
  2. `pyflakes`（改动文件 + 全项目 `git ls-files '*.py'`）→ 无 undefined name、无未使用导入；
     仅 `pages/stock_pools.py` 有 8 处既有 `nonlocal` 未赋值告警（本次未触碰，属历史遗留）。
  3. 通过 NiceGUI 源码（`timer.py` / `storage.py` / `client.py`）核对负 sleep 与 contextvars 行为，
     确认 `PollingTask` 与 `run_blocking` 的假设成立。
- **尚未运行时验证**：本机无 nicegui/后端依赖，未做真实页面与后端联调；建议在 Docker 环境启动后重点回归
  ① 策略监控页 CPU/请求量 ② 多用户并发是否串号 ③ 选股页 score 排序与显示 ④ 首屏异步 loading 占位。

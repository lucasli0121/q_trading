# q_trading_work 代码审查报告

审查范围：`strategy/base_strategy.py`、`strategy/strong_rebound_strategy.py`、`strategy/swing_trading_strategy.py`、`workflow/strategy_workflow.py`、`trade/manager.py`
（附带交叉核对：`workflow/base_workflow.py`、`redis_db/redis_exec.py`、`factor/factor_manager.py`、`app_context.py`、`trade/notifier.py`、`dao/strategy_execution_dao.py`、`api/trade_signal.py`、`backtest/backtest_engine.py`）

审查日期：2026-09-12

---

## 摘要

| 严重级别 | 数量 | 影响 |
|---|---|---|
| P0 严重 | 6 | 会导致错误交易决策、资金/盈亏算错、多用户数据互相污染 |
| P1 中等 | 12 | 数据不一致、丢数据、策略行为偏离预期、状态不清理 |
| P2 性能 | 6 | 线程池堵塞、Redis/HTTP 放大调用、任务堆积无背压 |
| 设计约定（非缺陷） | 2 | 见「六、设计约定」 |

> **修订记录（2026-09-12 二次确认）**：原 P0-1（实时链路不传持仓）与 P0-2（T+1 未拦截）
> 经项目负责人确认属**系统设计意图**，已从缺陷清单移出，改写至「六、设计约定」，
> 并保留其中仍然成立的**衍生待决事项**（回测/实盘口径分叉、T+1 是否需在订单层硬拦截）。
> 对应的设计意图已同步写进代码注释（`workflow/strategy_workflow.py`、`strategy/base_strategy.py`、
> `strategy/strong_rebound_strategy.py`），避免后续维护者重复误判为缺陷。

### 系统定位（本次确认的关键前提）

本系统（`q_trading_work`）定位是**辅助决策工具**，不是自动交易执行器：

1. **信号优先**——设计目标是「尽可能产出买卖信号」；
2. **订单与持仓由信号派生**——系统确实会创建订单、维护持仓，但都发生在信号之后；
3. **人做最终决策**——买卖与否由交易员判断，系统只提供辅助建议。

因此以下两点是**有意为之**，不属于缺陷：

- 实时链路调用策略时**不传持仓**（`strategy_workflow.py:904`、`:1005` 传 `{}`），
  策略以「无持仓」语义运行，持仓中同样产出 BUY 信号；
- 订单层的 T+1 判断**不拦截下单**（仅提示），因为订单本身只是给交易员的参考。

---

## 一、策略层（并发 / 漏数据 / 性能）

### P0-1【已撤销 → 设计约定 D-1】实时链路不给策略传持仓

**位置**：`workflow/strategy_workflow.py:904`、`:1005`

**结论**：**不是缺陷**。经确认，实时链路有意传空持仓 `{}`，让策略以「无持仓」语义运行，
从而在持仓状态下也能继续产出信号——这正是「尽可能提供买卖信号 + 人做最终决策」的设计目标。
代码注释已补写该设计约定，详见「六、设计约定 D-1」。

原先列为缺陷的连锁现象，按设计重新定性如下：

| 原判缺陷 | 按设计重新定性 |
|---|---|
| `handle_minute_bar` 内置 T+1 保护永不生效（死代码） | 实时链路的预期行为：不传持仓即不触发该保护，信号不受抑制；该保护仅回测链路生效（D-1 备注） |
| 卖出检查拿到 `cost_price=0`、`buy_time=""` | 预期行为：卖出检查以「无持仓」语义运行（见 D-3 最高价口径） |
| 持仓中持续产出 BUY 信号并记库/推送 | 预期行为：信号优先，持仓不抑制买入信号 |
| **回测传真实持仓、实盘传空持仓 → 回测与实盘口径分叉** | **⚠️ 仍然成立的衍生问题**（设计外的副作用），见 D-1 衍生的「待决事项 X1」 |

原建议（把 `_load_positions` 提前并传入策略）**不建议采纳**：它会抑制持仓中的买入信号，与设计目标相反。
若将来希望保留「持仓状态感知」，应新增独立的只读入参，而不是改变现有 `position` 的语义。

---

### P0-2【已撤销 → 设计约定 D-2】订单层 T+1 不拦截下单

**位置**：`workflow/strategy_workflow.py:962-971`

**结论**：**拦截行为不是缺陷**——订单仅作交易员参考，系统不做硬性风控拦截。
但原代码里有一处**确属代码缺陷，已修复**：

```python
buy_time = pos.get("buy_time", "")            # "2026-09-11 10:30:00"
if buy_time == stock_create_day:              # ← 与 "2026-09-11" 比较，恒为 False
    self.logger.info("买入当天不能立马卖出T+1规则, ...")
```

`buy_time` 由 `TradeManager._apply_order_to_positions` 写成 `order.create_time`（`trade/manager.py:353`），
格式为 `"YYYY-MM-DD HH:MM:SS"`。与 `"YYYY-MM-DD"` 直接 `==` 恒为 `False`，
导致该**提示日志永不输出**（观测性失效，且给了维护者「T+1 已有保护」的错觉）。

**已修复**：抽出统一提示方法 `StrategyWorkflow._log_t1_advisory`，比较改为日期前缀
`str(buy_time)[:10] == bar_day`，日志改写为「订单仍创建仅供交易员参考」，
并显式注释「本处不拦截下单，如需硬拦截请补 `return`」。
分钟链路（`_process_bar`）与实时链路（`_process_tick`）**统一走该方法**——原先 tick 链路完全没有 T+1 提示。
**下单行为保持不变**（符合设计 D-2）。

已用 5 组边界用例验证日期前缀比较逻辑（当日买入命中 / 隔日不命中 / 空 `buy_time` / 空行情时间 /
`buy_time` 仅日期），全部符合预期；旧写法 `"2026-09-11 10:30:00" == "2026-09-11"` 确认恒为 `False`。

---

### P0-3 评分累计在本地兜底分支失效

**位置**：`strategy/base_strategy.py:394-396` + `:424-426`

```python
# 本地兜底路径（Redis 未启用或不可用）
if code not in self._stock_scores:
    self._stock_scores[code] = entry     # 只在“不存在”时写入 → 永不更新
```

`_get_score_cache` 优先读 Redis，读不到才回落到 `self._stock_scores`。当 Redis 未启用/失败时：
- 第 2 次起 `_save_score_cache` 不再写入 → 缓存里永远是第 1 次的 `entry`（`score`、`check_number` 都冻结）；
- 调用方 `_check_buy_conditions` 每次读回来的 `score` 都被重置为第 1 次的分数，`check_num` 也卡在 2，`avg_score = score / check_num` 的滚动平均彻底失真。

策略在「Redis 不可用」时的行为与「Redis 可用」时完全不同 —— 这正是最难排查的一类问题。

**建议**：

```python
self._stock_scores[code] = entry      # 无条件覆盖
```

---

### P0-4 分钟缓存 Redis key 不含用户维度，多用户重复写入污染因子

**位置**：`strategy/base_strategy.py:466`、`:486-493`

```python
key: str = self._minute_cache_key(code, day)      # f"{code}:{day}" —— 不含 user_strategy_id
redis_exec.push_value_to_rlist(key, json.dumps(stock_data), ...)
```

`_save_minute_cache` 在 `_before_minute_route` 里被**每个策略实例**调用（`strong_rebound_strategy.py:223`、`swing_trading_strategy.py:182`）。而 per-user 架构下，同一模板有 N 个用户就有 N 个实例，同一个 `code` 的同一根分钟 bar 会被 push N 次到同一个 Redis list。

后果：
- `_get_minute_cache` 读回来的是「同一分钟重复 N 条」的数据；
- `VolumeExpansionFactor`（成交量放大）看到成交量被放大 N 倍、`VwapFactor` 权重失真、`MultiTrendFactor` 趋势被重复点拉伸 → **买入/卖出评分随用户数变化**，这是最隐蔽的一类「算错」。

**建议**：key 增加实例/用户维度，例如 `f"{code}:{day}:{self.user_strategy_id or 'default'}"`；或把分钟缓存做成「按 code 只写一次」（由 workflow 统一写入，策略只读）。

---

### P0-5 共享 FactorManager 被 per-user 实例互相覆盖

**位置**：`strategy/base_strategy.py:59`、`strong_rebound_strategy.py:137-146`

```python
self.factor_manager: FactorManager = AppContext().factor_manager    # 全局单例
...
self.factor_manager.register(LowReboundFactor.factor_name, LowReboundFactor(days=self.CONSECUTIVE_DAYS))
self.factor_manager.register(VolumeExpansionFactor.factor_name, VolumeExpansionFactor(days=self.BUY_CONSECUTIVE_MINUTES, ...))
```

`AppContext` 是单例（`app_context.py:30-42`），`FactorManager.register` 是**无条件覆盖**（`factor_manager.py:36`）。于是：

- 用户 A 实例化 → 注册 A 的参数因子；
- 用户 B 实例化 → 用 B 的参数**覆盖**同一 key；
- 用户 A 运行期 `self.factor_manager.get(VolumeExpansionFactor.factor_name)`（`strong_rebound_strategy.py:403`、`:376`）拿到的是 **B 的因子**。

参数（`days`、`min_price_pct`）跨用户串味。注意 `_check_buy_conditions` 里 `MultiTrendFactor` 是本地 `new` 的、`VwapFactor` 也是本地 new 的，唯独这两处走共享注册表 —— 实现方式自相矛盾。

**建议**：因子改为实例私有（`self._factors: dict[str, BaseFactor]`，每个策略实例自建字典），或至少让注册 key 带上 `user_strategy_id`。

---

### P0-6 `max_stock_count = 0`（不限制）被当成 4

**位置**：`workflow/strategy_workflow.py:525`

```python
max_stocks: int = int(us_item.get("max_stock_count", 4) or 4)
```

`api/user_strategy.py:59` 明确写着「`max_stock_count`: 最大持仓数量，默认 **0 表示不限制**」。但这里 `0 or 4` → 4，把「不限制」静默变成了「最多 4 只」。`add_user_strategy`（`:697`）又直接用 `user_strategy.max_stock_count`，两条加载路径语义还不一致。

**建议**：明确区分「未配置」与「0 = 不限制」，统一用一个哨兵值（如 `-1` 表示不限制）并收敛到一处解析。

---

### P0-7 `SELL_MAX_SCORE` 参数来源与默认值不一致（隐患）

**位置**：`strong_rebound_strategy.py:73`、`:126` vs `cfg/strategy.yaml`

类默认 `SELL_MAX_SCORE: float = -15.0`，`apply_params` 用 `sell_config.get("max_score", ...)`。而 `StrategyDao.default_params` 的 sell 节据此前核对只有 `trend_minutes_limit / vwap_minutes_limit / max_score` 三项，缺 `drawdown_pct / daily_drop_pct` —— 这两项会静默落回类默认值（`-8.0` / `-5.0`），与 yaml 中的配置值可能不同。同一份策略在「用户参数字典」与「模板默认参数」两条路径下阈值不同，会表现出「同策略不同用户行为不一致」。

**建议**：把 yaml 作为唯一默认来源，`apply_params` 缺失项从 yaml 补齐，而不是落回硬编码类属性。

---

### P1-8 `_check_above_avg_price` 永远返回 True（条件未真正校验）

**位置**：`strong_rebound_strategy.py:355-392`

```python
"""要求当前价 > 当天 VWAP。"""
...
score = vwap_factor.score(pd.DataFrame(minute_cache))
return True, {"code": code, "score": score, "signal": "BUY"}    # 没有任何比较
```

文档说「要求当前价 > 当天均价」，实现里 `current_price` 甚至没传进来，也没有任何阈值比较。调用方 `if ok and "score" in reason:` 的 `ok` 恒真 → 该「条件」实际只是加分项。要么修正文档，要么补上真正的校验。

---

### P1-9 `swing` 的 `_get_sr_points` 把 Redis 读到的结果无条件覆盖

**位置**：`swing_trading_strategy.py:212-225`

```python
def _get_sr_points(self, code: str) -> pd.DataFrame:
    df: pd.DataFrame = pd.DataFrame()
    if self._redis_enabled:
        value = redis_exec.get_key_string(key)
        if value:
            df = pd.DataFrame.from_records(json.loads(value))   # ← 读到了正确数据
    df = self._inday_sr_points.get(code, pd.DataFrame())        # ← 立刻被覆盖！
    if df.empty:
        trade_day = self.get_recent_trading_day()
        df = self._get_code_prev_sr_point(code, trade_day)      # ← 每次都走 HTTP 拉日线
        self._save_sr_points(code, df)
    return df
```

`_save_sr_points` 在 Redis 写入成功时 `return`（`:208-210`），**不会**填充 `self._inday_sr_points`。因此当 Redis 可用时：`df` 被覆盖成空 → 判定为空 → **每次调用都走 `_get_code_prev_sr_point`（一次 `load_his_daily_data` HTTP 请求 + 因子计算）**。Redis 缓存在这条路径上是纯粹的空转开销。

`_get_sr_points` 被 `_check_buy_conditions`（`:296`）和 `_check_sell_conditions`（`:383`）调用，即**每分钟每只股票每用户至少 1~2 次 HTTP 请求**。

**建议**：删掉覆盖行，改为 `df = self._inday_sr_points.get(code) or df`（等价地：只在 Redis 未命中时才回落本地/重算）。

---

### P1-10 `swing` 的 `_break_max_r` 永不清理 → 永久禁止买入

**位置**：`swing_trading_strategy.py:89`、`:144`、`:171`；对比 `base_strategy.py:284-290`

`is_match_strategy` 中 `self._break_max_r[code] = True`（突破最大阻力位），`handle_minute_bar` 据此跳过买入判断。但 `BaseStrategy.before_trading` 清理的是 `_select_stocks_set / _minute_cache / _stock_scores / _highest_prices`，**没有清 `_break_max_r`**，`SwingTradingStrategy.before_trading` 也只额外算了 SR 点。

结果：某只股票一旦被标记，跨越交易日也永远不再买入。

**建议**：在 `SwingTradingStrategy.before_trading` 里 `self._break_max_r.clear()`（顺带 `self._inday_sr_points.clear()`，避免非股票池代码残留隔日数据）。

---

### P1-11 `_check_hl_price` 是死代码（卖点 5 未生效）

**位置**：`strong_rebound_strategy.py:639-679`

类文档列出「卖点 5: 14:40 之后最低价低于昨日最低价，最高价低于昨日最高价」，但全仓搜索确认 `_check_hl_price` **只有定义、没有任何调用点**（`_check_sell_conditions` 里没有）。这条卖出规则当前完全不在生效。

**建议**：要么在 `_check_sell_conditions` 里接入（并在 14:40 时间窗调用），要么从文档里删除，避免维护者误判策略语义。

---

### P1-12【已更正 → 设计约定 D-3】拾取「持仓最高价」的口径

**位置**：`strong_rebound_strategy.py:434-470`（`_check_sell_conditions`）、`:565`（`_get_highest_since_buy`）、`:606`（`_resolve_highest_price`）

**更正两点**：

1. 原报告称 `_highest_prices` 在多用户下会串味——**不成立**。per-user 实例化重构后
   `_highest_prices` 是 `BaseStrategy.__init__` 里的实例属性，每个用户策略各持一份，无跨用户串味。
2. `buy_time=""` 时并非直接取「当日最高价」，而是先按**最近 2 个交易日**回溯买入基准，
   再与当日最高价取较大值。原报告第 3 点的描述有误，已按 D-3 更正。

当前实现与既定口径一致，仅修正了**自相矛盾的注释**（原注释一处写「最近 3 天」、一处写「最近 2 天」），
并补充了 `_get_highest_since_buy` / `_resolve_highest_price` 的参数与降级链说明。

---

### P2-13 单次检查内多次全量读 Redis + JSON 解析

**位置**：`base_strategy.py:495-520`，被 `strong_rebound_strategy.py:314`、`:366`、`:411` 分别调用

`_get_minute_cache` 每次都会 `get_value_from_rlist(key, -1, False)` 拉取**当天全量**（最多 240 条）并逐条 `json.loads`。`_check_buy_conditions` 一次执行内至少调用 3 次（直接 + `_check_volume_price_condition` + `_check_above_avg_price`），`_check_sell_conditions` 也有 1 次。

**建议**：单次 `handle_minute_bar` 内取一次快照并向下传参；或改用 Redis Hash / 更小窗口（只取最近 N 条）。注意 `MAX_CACHE=240` 与 `max_cache` 裁剪参数已存在，按需查询窗口即可。

---

## 二、workflow 层（加载 / 调用 / 下单 / 盈亏）

### P1-14 信号记录与对外推送发生在所有过滤之前

**位置**：`strategy_workflow.py:917-922`

```python
if not self._save_trade_signal(state.strategy_id, state.user_strategy_id, code, extra):
    return
if state.stock_pool and code not in state.stock_pool:
    return          # ← 已经写库 + 已推送企业微信了
```

`_save_trade_signal` 内部会：写 `/api/trade_signal/add`、发 MQTT、**推企业微信**（`:1165-1222`）。而它跑在「股票池过滤 / 黑名单 / 在途订单 / 持仓上限 / 资金不足」之前。于是会记录并推送大量**永远不会成交**的信号；`code` 不在该用户股票池时也会产生该用户的信号记录。

**建议**：先做纯内存过滤（股票池、黑名单、在途、持仓上限），通过后再记录信号与推送。

---

### P1-15 卖出信号的盈亏按「最近一次买入信号价」计算，且是每股金额

**位置**：`strategy_workflow.py:1142-1156`

```python
latest_result = self._trade_signal_api.latest(strategy_id, user_strategy_id, code, action="买入")
buy_price = float(latest_result.get("trade_price", 0))
profit_rate = (price - buy_price) / buy_price * 100
profit_amount = price - buy_price          # ← 每股价差，未乘数量
```

两个问题：
1. **基准错误**：拿「最近一条买入**信号**」的价格，而不是真实成交价。信号可能被抑制（`_save_trade_signal` 返回 False）或下单失败，也可能如 P1-14 那样是持仓中重复产生的信号 → 收益率算错。
2. **金额口径错误**：`profit_amount` 字段名与 API 文档都是「收益金额」，这里却是**每股**价差。真实总额（乘数量、扣手续费）在 `TradeManager` 里算的，两处口径不一致、会互相矛盾。

**建议**：卖出信号的盈亏从持仓/订单系统取真实 `cost_price` 与 `quantity`（这些数据 `_load_positions` 已有），统一乘数量并扣费。

---

### P1-16 `save_execution` 失败被吞 → 盈亏与持仓永久丢失

**位置**：`trade/manager.py:414-431`、`:317-318`

```python
try:
    self._user_strategy_api.save_execution(...)
except Exception as err:
    self.logger.exception("failed to update strategy execution via API: %s", err)   # 吞掉
```

`_update_strategy_execution` 的模式是 **读最新记录 → 内存累加 `current_profit` / 调整 positions → POST 新记录**。若 POST 失败（网络抖动、服务端 5xx），这一次交易的盈亏与持仓变更**永久丢失**；下一次交易会从旧记录继续累加，账目从此系统性偏移，且没有任何补偿/重试/告警。

**建议**：失败时重试 + 落盘补偿队列；或在服务端做幂等（按 `order_id` 去重），客户端只在成功后再推进本地状态。

---

### P1-17 订单重复创建风险

**位置**：`trade/manager.py:160-163`、`:195-200`

```python
order_id = self._save_order_via_api(order)     # POST /api/order/create
if order_id:
    order.id = order_id
...
if order.id:
    self._update_order_via_api(order)
else:
    order_id = self._save_order_via_api(order)  # ← POST 第二次
```

若第一次 POST 实际已在服务端落库、但响应超时/解析失败（`_save_order_via_api` 返回 `""`），`order.id` 为空 → `_execute_and_notify` 里会**再 POST 一次**，产生同一笔成交的两条订单记录。

**建议**：客户端生成幂等键（如 `client_order_id = user_strategy_id + code + timestamp + uuid`）并传给服务端做唯一约束；或失败时改为「先查后建」。

---

### P1-18 `_load_positions` 异常时返回 `{}`，可能重复买入

**位置**：`strategy_workflow.py:1392-1422`

```python
except Exception as exc:
    self.logger.debug("加载持仓信息失败 ...")
    return {}          # ← 失败与“无持仓”不可区分
```

`_process_bar` 收到空 dict 会认为 `code not in positions` → 在 `pending_codes` 已清空（回调已执行）后可能**重复买入已有持仓的股票**。

**建议**：改为失败时返回 `None` 并让调用方 fail-closed（跳过本 tick），或保留上一次成功快照并标记 `stale`。

---

### P1-19 迭代 `_user_strategies` 期间可能被增删

**位置**：`strategy_workflow.py:808`、`:857`、`:584-588`、`:750-754`、`:160-163`、`:1486`

`handle_bar` / `handle_tick` / `_find_state` / `get_all_strategies` 都在直接迭代该 dict；而 `add_user_strategy`（`:703-705`）与 `delete_strategy`（`:723-729`）会 `del` / 新增键。两者若并发（例如外部通过 MQTT 命令或 HTTP 触发增删），会抛
`RuntimeError: dictionary changed size during iteration`，导致该批行情**整批丢弃**。

当前仓库里 `add_user_strategy` / `delete_strategy` 只被测试调用（无生产调用方），属于**潜在**风险；一旦接入运行时增删就是必现。

**建议**：dict 的全部读写用一把 `_registry_lock` 保护，或迭代前 `list(self._user_strategies.items())` 取快照。

---

### P1-20 盘前选股用「代表实例的参数」为所有用户选股，且只存一份结果

**位置**：`strategy_workflow.py:1486-1491`、`:1519-1599`

```python
for strategy_id, instance in self._strategy_instances.items():   # 代表实例 = 第一个用户的实例
    ...
    results = instance.select(combined, today_str=trade_date, days=20)
...
self._strategy_select_stock_api.add(strategy_id, matched_codes)   # 按 strategy_id 只存一份
```

`select` → `is_match_strategy` 使用 `CONSECUTIVE_DAYS` / `SR_WINDOW` / `PE_LIMIT` / `PROFIT_LIMIT` 等**实例参数**。用 A 用户的参数给 A、B、C 所有用户选股，结果按模板维度存一份，B/C 用户随后 `_load_select_stocks` 读到的是**别人参数下的选股结果**。参数不同的用户选股完全错位。

**建议**：按 `user_strategy_id` 各自 `select` 并分别存储选股结果；或明确「选股结果按模板共享、不随用户参数变化」并在文档与代码里强制（不参与选股的参数不许放进 `default_params`）。

---

### P1-21 `_save_trade_signal` 每次信号都查一次策略名（无缓存）

**位置**：`strategy_workflow.py:1094-1095`

```python
strategy_dao = self._strategy_api.get_by_id(strategy_id)
strategy_name = strategy_dao.get("name", "")
```

每个信号一次额外 HTTP。`strategy_name` 在 `StrategyState` 里已经有了（`state.strategy_name`），且模板名基本不变。改成从 state 取或加一层 dict 缓存即可省掉这条请求。

---

### P2-22 tick 通道无差别派发任务（线程池洪泛）

**位置**：`strategy_workflow.py:850-872`、`base_strategy.py:334-340`

`handle_tick` 对每个 `(tick_data, state)` 都 `submit(self._process_tick, ...)`，而两个现有策略的 `check_tick_buy` / `check_tick_sell` 都返回 `(False, {})`（默认不实现）。实时 tick 频率远高于分钟 bar，结果是线程池被大量空转任务占满，真实的分钟信号任务排队延迟。

**建议**：在 `handle_tick` 派发前先判断策略是否启用 tick 能力（如类属性 `enable_tick = False` / 方法被覆写的标记），或按 `MIN_TICK_INTERVAL` 做节流。

---

### P2-23 线程池无界队列，无背压

**位置**：`base_workflow.py:64-82`、`DEFAULT_POOL_SIZE = 4`

`ThreadPoolExecutor` 的队列无上限。分钟行情是按股票池整批推送的（一次 payload 可能几百只），乘以运行中的用户策略数（P0-4 提到的 N 倍），一批就可能产生上千个任务，而每个任务内部还要串行做多次 HTTP（见 P2-24）。任务积压时内存上涨，且任务真正执行时用的 `bar_data` 已经过期 —— 而策略读的却是「此刻的」全量分钟缓存，**触发条件与评分数据的时间基准不一致**。

**建议**：限制队列长度 + 丢弃策略（如只保留每只股票最新一根 bar，用 `dict[code] -> latest_bar` 覆盖）；或改为「按 code 合并任务」。

---

### P2-24 `state._lock` 内串行多次 HTTP 与 webhook 调用

**位置**：`strategy_workflow.py:928-981`（持锁）→ `:1264/:1309` `_submit_trade` → `trade/manager.py:147-228`

`_submit_trade` 的 `delay_seconds=0.0`，所以 `submit_order` **同步**走到 `_execute_and_notify`。整条链路都在 `with state._lock:` 内：

- `_load_positions` → GET execution/latest
- `_save_order_via_api` → POST order/create
- `_update_order_via_api` → PUT order
- `_update_strategy_execution` → GET execution/latest +（可能）GET user_strategy + POST execution + PATCH user_strategy
- `_get_strategy_name` → GET user_strategy + GET strategy
- `_notifier.notify` → GET user_strategy + GET preference + **`urlopen(timeout=5)`**
- 回调 `_load_positions(force_refresh=True)` → GET execution/latest

单笔订单 8~12 次网络往返，其中一次最坏 5 秒，全部在**同一把 state 锁**内串行。同一用户策略下所有股票的买卖被完全串行化，交易延迟随股票数线性增长。

**建议**：把网络 IO 移出锁。锁内只做「判定 + 占位（`pending_codes` / 资金预留）」的临界区，订单提交/落库/推送放到锁外异步执行。

---

### P2-25 `_load_select_stocks` 每用户各自请求一次

**位置**：`base_strategy.py:630-677`

选股结果按 `strategy_id` 存储（`_strategy_select_stock_api.add(strategy_id, codes)`），但 `_load_select_stocks` 是按实例缓存的（`self._select_stocks_set`）。N 个用户 = N 次相同请求/天。可按 `strategy_id` 提到 workflow 级共享缓存。

---

## 三、TradeManager（订单执行与结果计算）

### P0-26 买入手续费既不计入成本、也不从可用资金扣除

**位置**：`trade/manager.py:108-137`、`:397-401`；`strategy_workflow.py:212-226`、`:1385`

```python
commission_fee = turnover * fee_pct / 100
...
if action == "买入":
    order.position_price = execution_price     # ← 不含手续费
```

- 买入 `profit_amount = 0`（`:124`），`position_price` 只含滑点，**不含手续费**；
- 卖出时 `profit_amount = revenue - cost_basis - commission_fee`（`:118`）只扣了**卖出**一边的费；
- `remaining_cash = initial_amount + current_profit - total_invested`（`manager.py:398-401`）与 `_calc_available_cash`（`workflow:218-226`）都只用 `cost_price * quantity`。

→ 买入手续费在**成本、盈亏、可用资金**三处全部缺失，系统性高估资金与收益。

**建议**：买入成交价（成本）用 `execution_price`，但把买入手续费单独累加到成本或从剩余资金中扣除，例如
`cost_basis = position_price * qty + buy_fee`（需同步调整 `current_profit` 口径，保证三处一致）。

---

### P1-27 年化收益率 / 基准收益率 / 执行天数从不计算

**位置**：`trade/manager.py:414-426`

```python
self._user_strategy_api.save_execution(
    current_return_rate=execution.current_return_rate,
    annualized_return_rate=execution.annualized_return_rate,   # ← 原样透传（来自上一条记录）
    benchmark_return_rate=execution.benchmark_return_rate,     # ← 同上
    execution_days=execution.execution_days,                   # ← 从不自增
    ...
)
```

`annualized_return_rate` 与 `benchmark_return_rate` 没有任何计算代码，`execution_days` 也不自增 —— 用户看到的三项指标恒为 0（或永远停留在初值）。`current_return_rate` 只算已实现收益（`current_profit / initial_amount`），不含浮动盈亏。

**建议**：至少补 `execution_days = 交易日历差(start_date, today) + 1`，并按 `(1+r)^(250/days)-1` 折算年化；基准收益率接沪深 300 同期涨幅。

---

### P1-28 持仓 `current_price` 只更新变动的那只股票 → 浮动盈亏失真

**位置**：`trade/manager.py:332-367`、`:384-395`

买入新建持仓时设 `current_price=execution_price`，加仓/卖出时更新该股 `current_price`；**其他持仓的 `current_price` 保持上一条记录的值不变**（甚至可能是几天前的）。而 `_refresh_execution_metrics` 用这个陈旧值算 `profit_amount` / `profit_rate`（`:391-392`）。

`remaining_cash` 用的是 `cost_price * qty`，不受影响；但持仓浮盈浮亏、个股收益率是错的。

**建议**：每次生成执行记录时按最新行情刷新全部持仓的 `current_price`（可复用 `_get_minute_cache` / 分钟行情快照），或在计算前明确标注这是「按上次成交价估算」。

---

### P1-29 `TradeNotifier._preferences_cache` 是类级字典且永不过期

**位置**：`trade/notifier.py:40-41`、`:59`、`:225-238`

```python
class TradeNotifier:
    # 用户偏好缓存（user_id → preferences dict），避免重复 API 调用
    _preferences_cache: dict[str, dict[str, Any]] = {}     # ← 类属性，全实例共享

    def __init__(...):
        self._cache_lock = threading.Lock()                 # ← 锁却是实例级
```

两个问题：
1. **缓存永不失效**：进程内第一次查到的偏好会一直用到进程重启。用户在企业微信里关掉推送、改 `wx_push_url`，**不会生效**。
2. **共享字典 + 各自加锁**：`TradeManager` 与 `StrategyWorkflow` 各持有一个 `TradeNotifier`，两者共享同一个类级 dict 却用不同的 `Lock`，锁形同虚设。

**建议**：改为实例属性 `self._preferences_cache = {}`，并加 TTL（如 5~10 分钟）。

---

## 四、修复优先级建议

**第一批（立即修，直接影响信号质量与资金账目）**

| 编号 | 修复点 | 位置 |
|---|---|---|
| P0-6 | `max_stock_count=0` 不再被改写成 4 | `strategy_workflow.py:525` |
| P0-3 | 本地评分缓存改为无条件覆盖 | `base_strategy.py:395-396` |
| P0-26 | 买入手续费计入成本与可用资金 | `trade/manager.py:108-137` |

> 原列此批的 **P0-1 / P0-2 已撤销**（属设计约定，见第六节），
> **P1-14 转为待决事项 X2**（是否记录/推送不在该用户股票池内的信号，需产品决策）。

**第二批（数据一致性）**

| 编号 | 修复点 |
|---|---|
| P0-4 | 分钟缓存 key 加用户维度，消除重复写入 |
| P0-5 | 因子改为实例私有，消除跨用户参数串味 |
| P1-9 | 删掉 `_get_sr_points` 的覆盖行，恢复 Redis 缓存意义 |
| P1-10 | `before_trading` 清理 `_break_max_r` / `_inday_sr_points` |
| P1-15 | 卖出信号盈亏改用真实成交成本 × 数量 |
| P1-16 / P1-17 | 执行记录写入失败重试+幂等；订单创建幂等键 |
| P0-26 | 买入手续费计入成本与可用资金 |
| P1-29 | 偏好缓存实例化 + TTL |

**第三批（性能与健壮性）**

| 编号 | 修复点 |
|---|---|
| P2-24 | 把网络 IO 移出 `state._lock` |
| P2-13 | 单次检查内分钟缓存只取一次快照 |
| P2-22 / P2-23 | tick 无实现则不派发；线程池加队列上限与按 code 合并 |
| P1-19 | `_user_strategies` 读写加锁或取快照迭代 |
| P1-20 | 盘前选股按 `user_strategy_id` 分别执行 |
| P1-18 | `_load_positions` 失败 fail-closed |

---

## 五、未纳入本次修改的已知历史问题

1. `tests/strategy/test_strong_rebound_strategy.py` 引用了源文件中不存在的 `_bought_codes` 属性与 `factor.decline_factor.DeclineFactor`（`factor/` 下只有 `high_decline_factor` / `min_close_decline_factor`），测试与源码处于一次未完成重构的中间状态。
2. 本机环境缺 `pandas` / `numpy` / `ta-lib`，本次审查为**静态阅读 + 交叉核对**，未运行测试；上述结论均基于代码路径推演，建议在具备依赖的环境按上表逐项回归。

---

## 六、设计约定（非缺陷，勿按缺陷修复）

> 本节由项目负责人于 2026-09-12 确认。**后续审查与重构请以本节为准**，
> 这些行为是刻意设计，改动它们等于改变产品定位。

### D-1 实时链路不向策略传持仓（信号优先）

- **约定**：`StrategyWorkflow._process_bar` / `_process_tick` 调用策略时**有意传空持仓 `{}`**
  （`strategy_workflow.py:914`、`:1024`）。
- **原因**：系统目标是「尽可能产出买卖信号」。系统确实会创建订单、维护持仓，但都发生在信号之后；
  买卖与否由交易员判断，系统只提供辅助建议。若传入真实持仓，`BaseStrategy.handle_minute_bar`
  会因 `len(position) > 0` 而压制持仓中的买入信号，与设计目标相反。
- **已知副作用（待决事项 X1）**：回测链路（`backtest_engine.py:920`）传**真实持仓**，
  因此回测中的信号集合与实盘不同，**回测指标不能直接等同实盘预期**。
  若需要口径一致，应统一为「回测也传空持仓」或新增参数区分两种模式，需产品确认。
- **代码标注**：已在 `_process_bar` / `_process_tick` 与 `BaseStrategy.handle_minute_bar` 处补写注释。

### D-2 订单层不做 T+1 硬拦截

- **约定**：即使卖出信号针对的是当日买入的标的，仍然创建订单，仅输出提示日志。
- **原因**：订单只是给交易员的参考，系统不做硬性风控拦截；信号本身已先行记录/推送。
- **代码标注**：提示逻辑统一封装为 `StrategyWorkflow._log_t1_advisory`（分钟/tick 两条链路共用），
  方法 docstring 写明「如需硬拦截请在调用处补 `return`」。
- 说明：原实现用 `buy_time == stock_create_day` 比较 `"YYYY-MM-DD HH:MM:SS"` 与 `"YYYY-MM-DD"`，
  恒为 `False`，提示日志从不输出——**这一处是代码缺陷，已修复为日期前缀比较**（行为不变）。

### D-3 「持仓最高价」口径（`strong_rebound_strategy` 卖点 1）

- **约定**：
  1. `position` 中 `buy_time` 有值 → 以真实买入时间为基准，取**买入至今**日K最高价；
  2. 未传入持仓信息（实时链路的常态）→ 以**最近 2 个交易日**为买入基准，
     取 `[最近2交易日, 昨日]` 日K最高价，再与**当日最高价**取较大值。
- **实现**：`_check_sell_conditions` 在 `buy_time == ""` 时用
  `get_previous_trading_day(today_str=create_time, days=2)` 兜底（`strong_rebound_strategy.py:453-465`）；
  `_resolve_highest_price` 以日K查询结果为真源，仅当查询失败（返回 0）时退化为内存追踪的当日最高价。
- **代码标注**：已修正原「最近 3 天」的错误注释，并补充两个方法的参数与降级链说明。

### 待决事项（需产品/负责人拍板，不属缺陷）

| 编号 | 事项 | 关联 |
|---|---|---|
| X1 | 回测是否与实盘对齐（回测也传空持仓 / 或新增模式开关），以保证回测指标可代表实盘 | D-1 |
| X2 | 是否记录并推送「不在该用户股票池内」的信号（当前会记录 + 推企业微信） | P1-14 |
| X3 | 订单层是否需要 T+1 硬拦截（若券商侧必然拒单，可考虑直接跳过下单，减少无效订单记录） | D-2 |

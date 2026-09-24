# max_stock_count=0 / initial_amount=0 语义统一为「限制买入」

日期：2026-09-24
范围：`q_trading_work`（本端判定与日志）、`q_trading_server/web_api/models.py`（字段描述）
决策来源：用户确认「`max_stock_count = 0` 或 `initial_amount = 0` 为限制买入」

## 一、语义定稿

| 字段取值 | 含义 | 买入 | 卖出 |
| --- | --- | --- | --- |
| `max_stock_count = 0` | 限制买入 | 拦（原因记 INFO） | 放行 |
| `max_stock_count = -1`（或任何负数） | 不限制持仓数量 | 放行 | 放行 |
| `max_stock_count > 0` | 持仓上限 | 达上限才拦 | 放行 |
| `max_stock_count` 缺失/非法 | 回退默认 4 | 按 4 判定 | 放行 |
| `initial_amount = 0` | 限制买入 | 拦（原因记 INFO） | 放行 |
| `initial_amount > 0` | 初始资金 | 按资金与上限判定 | 放行 |

「限制买入」同时禁止新建持仓与对已有持仓加仓，卖出（清仓）路径不读这两个字段，因此受限策略仍可正常卖出。

前一版 `api/user_strategy.py` 的 docstring 写「`max_stock_count`：最大持仓数量，默认 0 表示不限制」，与本端 `workflow._parse_max_stock_count` 的「0 = 不允许新建持仓」互相矛盾，服务端 `models.py` 只写了「最大持仓数量」（未定义 0）。本次把三处描述统一到上表。

## 二、改动内容

### 1. 判定收敛为单一真源

`workflow/strategy_workflow.py`

- 删除 `_is_position_limit_reached()`，新增 `_buy_block_reason(state, positions) -> str`：命中时返回原因文本，允许买入时返回空串。判定顺序为「`max_stock_count == 0` → `initial_amount <= 0` → 持仓数达上限 → 允许」。
- `_process_bar()` 与 `_process_tick()` 的 BUY 分支改为共用该方法，原因统一记 INFO：

  ```
  [策略名] code=000001 跳过买入：策略配置 max_stock_count=0（0 表示限制买入，不允许新建持仓或加仓）
  ```

  改动前两个入口各写一份上限判定与日志，容易再次漂移；现在两个入口的结构完全同构。

### 2. 配置的 0 不再被执行记录洗白

`initial_amount` 有两个来源：用户策略配置、历史执行记录。旧实现在两处用执行记录覆盖：

- `_load_execution_data()`：`if ia > 0 and state.initial_amount <= 0: state.initial_amount = ia`
- `_load_positions()`：`if ia > 0: state.initial_amount = ia`

配置为 0（限制买入）时，只要执行记录里存在历史正值，`state.initial_amount` 就被改回正数，限制失效。

处理方式：

- `StrategyState` 新增 `initial_amount_configured: bool`，记录「配置里是否带该字段」。
- `dao/user_strategy_dao.py` 新增同名字段，`from_db()` 用 `data.get("initial_amount") is not None` 赋值。区分的是「显式配置 0」与「字段缺失」，只有后者才允许用执行记录兜底。
- 覆盖条件加 `not state.initial_amount_configured`。配置为 0 且执行记录有正值时，记一条 INFO 说明忽略原因，`state.initial_amount` 保持 0。

### 3. 加载期提示受限策略

`_load_user_strategy()` 在构建 state 后，对 `max_stocks == 0 or initial_amt <= 0` 的策略记一条 INFO：

```
[策略名] 该策略配置为限制买入（max_stock_count=0, initial_amount=0.00，均以 0 表示限制买入），不会创建买单；卖出不受影响
```

这类策略会持续产出信号但永不创建订单，只在「跳过买入」那一刻记日志的话，运维要先翻到对应行情才看得出来。

### 4. 其它一致性改动

- `add_user_strategy()` 路径的 `max_stock_count` 改为走 `_parse_max_stock_count()`，与 `_load_user_strategy()` 同一套解析（原先直接用 DAO 原值，两条路径语义不一致）。
- `api/user_strategy.py::create()` 显式下发 `initial_amount`。旧实现 `if initial_amount:` 会把 0 静默省略，语义依赖服务端默认值。
- `_calc_buy_quantity()` 中「可用资金为 0」的 WARNING 文案补充说明：`initial_amount=0` 属限制买入，建仓后资金耗尽属正常情况。
- `q_trading_server/web_api/models.py` 三个用户策略模型的 `initial_amount` / `max_stock_count` 描述改为「0 表示限制买入（不创建买单，卖出不受影响）」「0 表示限制买入，-1 表示不限制」。执行结果模型（`StrategyExecutionSaveRequest` / `StrategyExecutionItem`）的 `initial_amount` 是实际本金，无此语义，未改。

## 三、测试

`tests/test_strategy_workflow.py` 新增 7 条：

| 用例 | 覆盖点 |
| --- | --- |
| `test_buy_block_reason_matrix` | 判定矩阵：正常 / max=0 / max=-1 / initial=0 / 达上限 / 未达上限 |
| `test_max_stock_count_zero_blocks_buy_as_restriction` | `max_stock_count=0` 拦买单，原因记 INFO |
| `test_initial_amount_zero_blocks_buy_as_restriction` | `initial_amount=0` 在实时链路（`_process_tick`）同样拦，原因记 INFO |
| `test_initial_amount_zero_still_allows_sell` | 受限策略卖出仍放行 |
| `test_max_stock_count_negative_means_unlimited` | `-1` 下持仓 6 只仍可买入 |
| `test_configured_zero_initial_amount_not_overridden_by_execution` | 配置 0 不被执行记录里的 20000 覆盖 |
| `test_missing_initial_amount_field_still_falls_back` | 字段缺失时仍用执行记录兜底 |

其中「原因记 INFO」的断言同时检查 `levelno == logging.INFO` 与文案，避免与 `_calc_buy_quantity()` 的 WARNING（资金不足）混淆。

回归结果（容器内执行）：

```
tests/test_strategy_workflow.py                    95 passed
tests/（排除 tests/strategy、hangle_bar）          301 passed, 1 skipped
tests/strategy                                     60 passed
tests/test_strategy_workflow_hangle_bar.py          1 skipped
```

合计 361 passed、0 failed（分三段执行，含联网用例）。

## 四、影响面（需要关注）

服务端 `UserStrategyItem.max_stock_count` 与 `initial_amount` 的默认值都是 0，且返回 JSON 里恒带这两个字段。按新语义，**未显式配置持仓上限与初始资金的策略会被判定为限制买入，不会下单**。

这与 P0-6 以来的既有行为一致（线上 `6a58d62b28893aa08c3ed4df` 的 `max_stock_count=0` / `initial_amount=0` 就是如此，9 条信号 / 0 条订单），本次只是把「0 = 不允许新建持仓」上升为全体统一语义并补齐可观测性，没有引入新的拦截来源。

需要产品侧决定的剩余项：服务端默认值是否从 0 改为 4（持仓上限）与一个非零初始资金。只要默认为 0，新建策略若不填这两项就不会下单。本次未改默认值，仅改描述。

排查时可直接按 INFO 关键字定位：

- `该策略配置为限制买入`：加载期发现配置受限
- `跳过买入：`：每一条被拦下的买单及其原因

## 五、文件清单

| 文件 | 改动 |
| --- | --- |
| `workflow/strategy_workflow.py` | `_buy_block_reason` 新增、两个入口分支改造、配置真源判定、加载期提示、文案 |
| `dao/user_strategy_dao.py` | 新增 `initial_amount_configured` 字段与 `from_db` 赋值 |
| `api/user_strategy.py` | docstring 语义修正、`create()` 显式下发 `initial_amount` |
| `tests/test_strategy_workflow.py` | 新增 7 条用例、`import logging` |
| `q_trading_server/web_api/models.py` | 三个用户策略模型的字段描述 |

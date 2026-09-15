# CODE 检查报告 — 部署设备 errors.log 检查与 404 误报修复

- 日期：2026-09-15
- 对象：`log/errors.log`、`log/info.log`（**部署目标设备**上生成，非本机容器）
- 结论：errors.log 仅 1 条记录，是**良性 404 误报**，不是系统故障；且这条误报由上一轮我为排查 422 新加的日志产生。已修复。

---

## 一、errors.log 全文（198 字节，仅 1 行）

```
2026-09-15 10:51:08,804-api.client-210-ERROR: HTTP 404
  http://203.0.113.10:8000/api/user_strategy/6a58d62b28893aa08c3ed4df/execution/latest
  响应体: {"detail":"未找到该策略的执行结果"}
```

## 二、info.log 概览

- 覆盖 **10:51:07 – 10:56:01**（进程于 10:51:07 启动），1320 行
- 级别分布：**INFO 43 / DEBUG 1271 / ERROR 1**
- 唯一的 ERROR 就是上面那条 404（`errors.log` 的 ERROR 级别 handler 分流结果）
- **没有任何 WARNING** ⇒ 上一轮加的「取数失败 warning」「保存交易信号失败」均未出现

## 三、重大旁证：422 修复已上线且生效

1. `errors.log` 里**再无 `trade_signal` 422** —— 09:54 / 10:04 / 10:06 那批报错已消失。
2. 报错行的来源可精确定位到版本：该行是 `api.client` 第 **210** 行，正是上一轮
   （commit `e51980a`）新增的 `_log_error_body()` 中 `self.logger.error(...)` 所在行号。
   ⇒ **部署设备跑的就是含 422 修复的新代码**。
3. 也就是说：这条 404 是「新日志能力」首次发挥作用的副产品 —— 它把一条本来就
   被正常处理的 404 也抓了出来，并直接升级成了 ERROR。

## 四、根因：良性 404 被记成 ERROR

启动时序（info.log）：

```
10:51:08,386  共加载 2 个策略模板
10:51:08,490  Service [workflow_service_1] 分配 3 个 user_strategy_ids:
              ['6a58d62b28893aa08c3ed4df', '6a5ce38528893aa08c76bf63', '6a662d5028893aa08cf31225']
10:51:08,555  GET .../6a662d5028893aa08cf31225/execution/latest   → OK
10:51:08,668  GET .../6a5ce38528893aa08c76bf63/execution/latest   → OK
10:51:08,781  GET .../6a58d62b28893aa08c3ed4df/execution/latest   → 404  ← 就是这条
10:51:08,804  DEBUG: 加载策略 强趋势策略 执行数据失败: ...404...
10:51:08,845  用户策略加载: name=强趋势策略, user_strategy_id=6a58d62b..., strategy_id=6a5884d5...
```

关键点：**调用方本来就正确处理了它。**

`workflow/strategy_workflow.py::_load_execution_data`（395 行）整体 `except` 兜底，
记 DEBUG 并初始化 `available_cash`；紧接着 399 行还专门处理「无执行数据（新策略）」。
`api/user_strategy.py::get_latest_execution` 的 docstring 也写明「无记录时返回空字典」。

⇒ 服务端用 **404 表达「该用户策略还没有任何执行记录」**，这是一个**正常业务状态**
（该策略从未成交），却被 `_log_error_body` 无差别按 ERROR 记录，
在 `cfg/log.yaml` 的分流下写进了 `errors.log`。

**性质判定：日志误报（我上一轮改动引入），不是运行缺陷。**
影响仅为「运维翻 errors.log 时会去追一个不存在的故障」，业务逻辑零影响。

## 五、修复

原则：**不放松对真实 4xx 的观测，只让调用方显式声明「404 是正常状态」。**

| 文件 | 改动 |
|---|---|
| `api/client.py` | `_handle_response(resp, *, allow_not_found=False)`：404 且声明可接受时降为 DEBUG，其余 4xx/5xx 照旧 ERROR+响应体；`get()` 透传该参数。抽出 `_request_url()` 供两处复用 |
| `api/user_strategy.py` | `get_latest_execution` 传 `allow_not_found=True`，并说明服务端语义 |

异常**照旧抛出**，兜底责任仍在调用方 —— 只改日志级别，不改控制流（零行为变更风险）。

## 六、验证

1. **单测**：新增 `tests/test_api_client.py`（6 条）
   - 422 仍记 ERROR 且带响应体（防止观测能力退化）
   - 未声明时 404 仍记 ERROR
   - 声明后 404 只记 DEBUG 且**异常照旧抛出**
   - `allow_not_found` 只作用于 404，其他 4xx（400）仍记 ERROR
   - `get_latest_execution` 契约：确实传 `allow_not_found=True`，无记录返回 `{}`
2. **真实实例复现**（对 `203.0.113.10:8000`，只读 GET，带 admin_token）：
   ```
   6a58d62b28893aa08c3ed4df: RAISED HTTPStatusError
      log levels=['DEBUG','DEBUG']   ERROR count=0
      [DEBUG] HTTP 404 .../execution/latest（调用方声明为可接受）
   6a5ce38528893aa08c76bf63: OK    log levels=['DEBUG']  ERROR count=0
   ```
   ⇒ 同一 URL、同样的 404，修复后不再产生 ERROR。
3. **回归**：259 passed，0 失败（workflow / user_strategy_cache / trade_signal /
   order / select_stock / trade_manager / backtest 系列）。

## 七、修复后预期

`cfg/log.yaml` 的 `error_file_handler` 是 **ERROR** 级别专写 `errors.log`。
本修复后该条降为 DEBUG ⇒ **部署设备的 errors.log 应为空**（当前设备也就这一条 ERROR）。

## 八、附带发现（无需处理，仅备案）

1. `Redis: Failed to enable maintenance notifications: Unknown subcommand ...
   'MAINT_NOTIFICATIONS'` 在 info.log 高频出现，是 redis-py 与服务端版本的兼容性告警，
   **仅 DEBUG 级**、不影响读写；如嫌噪音可升级 redis-py 或调该 logger 级别。
2. 3 个 user_strategy 中有 2 个是同一模板（`strategy_id=6a5884d528893aa08c3e805b`
   强趋势策略），其中 `6a58d62b...` 无执行记录、`6a5ce385...` 有。
   若无记录那个**本应有持仓**，才需进一步排查；否则属正常「尚未成交」。
3. 本机容器 `q_trading_work` 的日志与部署设备无关（部署设备为独立部署），
   后续定位线上问题请继续以**部署设备日志 / 真实实例探测**为准。

## 九、待办

- [ ] 将该修复部署到目标设备（部署后 errors.log 应为空）
- [ ] 若在设备上确认某个「无执行记录」的策略本应有持仓，再单独排查该策略

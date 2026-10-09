# 日志 DEBUG 级别修复报告

日期：2026-10-09
改动文件：`cfg/log.yaml`、本报告
状态：已改完，重新打包部署后生效

## 一、问题

把 `cfg/log.yaml` 里 `info_file_handler` 的 `level` 从 `INFO` 改成 `DEBUG` 之后，
`log/info.log` 里依然一条 DEBUG 都没有，`配置对账完成…无变更` 这类日志始终不出现。

## 二、根因

Python logging 的判定是**两道闸门**，顺序固定：

1. **`logger.isEnabledFor(level)`** —— 由 logger 自身及其**祖先**的 effective level 决定。
   名字没被显式配置过的 logger（`workflow.strategy_workflow` 就是），effective level
   逐级向上继承到 `root`。
2. **`handler.level`** —— 记录走到 handler 这一层，要不要写出去。

`root` 的 level 是 `INFO`，所以 `logger.debug(...)` 在第一道就被拦下 ——
**LogRecord 根本不产生**，连 handler 都不会走到。只改 handler 的 level，
等于把第二道门开到最大，而第一道门还锁着。

```yaml
root:
  level: INFO          # ← 真正的闸门
  handlers: [console, info_file_handler, error_file_handler]
```

## 三、修法

`cfg/log.yaml` 改两处，缺一不可：

**1. `info_file_handler` 的 level 改 DEBUG**（用户已做）—— 让记录能落盘。

**2. 新增 `loggers.workflow` 段**（本次补上）—— 让记录能产生：

```yaml
loggers:
  workflow:
    level: DEBUG
    handlers: [console, info_file_handler, error_file_handler]
    propagate: false
```

三个细节：

- **不动 `root`**。把 root 改成 DEBUG 会让 httpx / pymongo / apscheduler /
  urllib3 的 DEBUG 一起进日志，噪音远大于收益。给 `workflow` 包单独放行即可
  （`workflow.strategy_workflow`、`workflow.base_workflow.*` 都是它的子 logger）。
- **`propagate: false` 必须加**。否则记录还会冒泡到 root，被 root 的 handlers
  再输出一遍，表现为每行日志重复两次。
- `handlers` 要列全三个（含 `error_file_handler`），因为 `propagate: false` 之后
  这个 logger 不再借用 root 的 handler 列表，ERROR 不会自己进 `errors.log`。

## 四、验证

**两道闸门分别验证**（探针，两个场景各起一个进程，避免 dictConfig 的
`disable_existing_loggers` 干扰）：

| 场景 | logger effective level | isEnabledFor(DEBUG) | `logger.debug()` 实际输出 |
|---|---|---|---|
| A 只改 handler level=DEBUG | INFO | False | **（空 —— 记录根本没产生）** |
| B handler=DEBUG + workflow 开 DEBUG | DEBUG | True | `配置对账完成（reason=probe）: 无变更` |
| C 本次改动后的真实 `cfg/log.yaml` | DEBUG | True | `配置对账完成（reason=probe）: 无变更` |

**放行范围**（用真实配置加载后逐个 logger 查）：

```
workflow.strategy_workflow       level=DEBUG  DEBUG放行=True
core.tools                       level=INFO   DEBUG放行=False
trade.notifier                   level=INFO   DEBUG放行=False
httpx                            level=INFO   DEBUG放行=False
pymongo                          level=INFO   DEBUG放行=False
apscheduler.scheduler            level=INFO   DEBUG放行=False
```

只有 `workflow` 包放行，第三方库与其它模块照旧被 root 的 INFO 拦住。

**回归**：`test_config_reconcile` + `test_strategy_workflow` + `test_notifier`
共 159 passed。

## 五、风险（已知，写进配置注释了）

`workflow/strategy_workflow.py` 第 2101 行的
`code=%s 未触发信号, reason:%s` 是**逐笔**打点 —— 实时行情下每个未匹配的 tick
都会写一行。开 DEBUG 后它会把 `info.log` 迅速刷大。

**排查完建议把 `loggers.workflow` 这一段改回 `level: INFO` 或直接删掉。**
如果希望长期保留又不想爆日志，可以把那条改成按时间节流（例如同一 code
每 60 秒最多一条），但那属于行为改动，本次没做。

## 六、部署

改的是 `cfg/log.yaml`，生成机器上跑的是打包产物 ⇒ **重新打包再部署**。
只想临时看一眼的话，直接改生成机器上的 `cfg/log.yaml`、重启进程即可。

部署后应能看到（交易时段每 60 秒一行）：

```
配置对账完成（reason=scheduler）: 无变更
```

在界面改一处配置，应能看到：

```
收到配置变更消息: ...
配置对账完成（reason=mqtt）: 新增 0, 移除 0, 更新 1（补跑选股 0）
```

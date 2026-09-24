# q_trading_server 代码 Review：API 性能 / 数据存取 / 索引

审阅时间：2026-09-20
口径：基于当期工作区代码（工作区较镜像略有滞后，线上以容器内代码为准）。未连真实 MongoDB，所有涉及执行计划的结论均标注了依据来源，未验证的部分已明确标出。

## 结论摘要

三条主线，按影响面排序：

1. **索引层**：全项目零索引定义，且 `MongoExec.query_by_condition` 对每次查询强制附加 `collation({'locale':'zh','strength':1})`。按 MongoDB 官方规则，查询 collation 与索引 collation 不一致时，索引无法支持字符串比较。项目里几乎所有过滤字段（`code`、`create_time`、`user_strategy_id`、`stock_code`、`status`、`name`、`action`、`report_date`）都是字符串。结果是：**即使补建普通索引，这些查询仍然会退化成全集合扫描**。
2. **查询层**：全项目 77 个 `query_by_condition` 调用点中，68 个的 `limit` 取值为 `None` / `0` / 未传。在 pymongo 中 `.limit(0)` 等于不限制，配合 `list(...)` 一次性物化，属于无上限读全表。
3. **并发层**：122 个 HTTP 端点全部是 `async def`，但内部全部是同步 pymongo / 同步 Redis / 同步 HTTP。这些调用没有 `run_in_threadpool`，会直接阻塞事件循环，单 worker 下并发被串行化。

另有一组与性能无关但严重度更高的鉴权缺陷，单列在第四节。

---

## 一、索引与查询封装（P0）

### 1.1 collation 让字符串字段的索引全部失效

`db/mongo/mongo_exec.py:107-120`

```python
results = list(table.find(condition).collation({'locale':'zh', 'strength':1})
        .sort(sort).skip(skip).limit(limit))
```

MongoDB 官方文档（Collation and Index Use）的原文：

> To use an index for string comparisons, an operation must also specify the same collation. If an operation specifies a different collation than the index specifies, the index cannot support string comparisons on the indexed fields.

集合级 collation 未设置，因此索引默认是 `simple`（二进制）。查询侧指定了 `zh/strength:1`，两者不一致。

受影响字段（全部为字符串）：

| 表 | 字段 | 用途 |
| --- | --- | --- |
| 几乎所有表 | `code` / `stock_code` | 等值、`$in`、`$regex` |
| 几乎所有表 | `create_time` / `minute_time` / `report_date` | 范围 `$gte/$lte` + 排序 |
| order_tbl / trade_signal_tbl / strategy_execution_tbl 等 | `user_strategy_id` / `strategy_id` | 等值、`$in` |
| user_strategy_tbl | `user_id`、`name`、`status` | 等值、唯一性校验 |
| stock_info_tbl | `industry`、`board` | 等值、`$regex` |

不受影响的字段：`_id`（ObjectId）、`score`、`ttm_pe`、`total_market_cap`、`change_percent` 等非字符串类型。collation 只作用于字符串比较。

排序同样受限。`sort({"code": 1})`、`sort({"create_time": -1})` 这类字符串排序，在 collation 不匹配时无法用索引排序，会退化为内存排序（默认阻塞排序上限 100 MB，超出直接报错，而不是变慢）。

附带一个静默的正确性风险：聚合管道（`.aggregate(pipeline)`）**没有**加 collation，走 simple 二进制比较，而 `find` 走 `zh/strength:1`（忽略大小写和变音符）。同一个 `code` 字段在两条路径下比较语义不同。如果库里存在 `sh600519` 与 `SH600519` 这类大小写混存的记录，`find` 认为它们相等，`aggregate` 认为不等，两边结果会对不上。

**修法（推荐，改动最小）**：把 collation 从硬编码改为可选参数，默认不加。

```python
def query_by_condition(self, table, condition, sort, skip, limit,
                       collation: dict | None = None):
    ...
    cursor = table.find(condition)
    if collation:
        cursor = cursor.collation(collation)
    results = list(cursor.sort(sort).skip(skip).limit(limit))
```

需要中文拼音排序的少数场景（目前只有各类按 `name` 排序的列表接口）显式传 `{"locale": "zh", "strength": 1}`。同时把原先一处 bug 一并修掉：当前代码把 `.collation()` 加在了 `find()` 之后、`.sort()` 之前，实际 `collation` 是 cursor 级设置，位置不影响语义，但裁剪逻辑后需要确认 `sort` 与 `collation` 都作用在同一个 cursor 上。

如果坚持保留全局 collation，则**每一条索引都必须用相同的 collation 创建**，见 1.2 的建索引语句。

**修改确认**： 按照推荐方式修改

### 1.2 没有任何索引定义

全项目 grep `create_index` / `ensure_index` / `createIndex`，零命中。`docker-compose.yml` 只跑应用容器，MongoDB 是外部实例，也不存在 initdb 脚本。代码侧没有任何建索引的地方。

我无法从代码判断线上是否有人手工建过索引，**这一条需要现场核实**：

```javascript
// 在 mongo shell 里逐个集合执行，确认是否为空
db.order_tbl.getIndexes()
db.day_hq_tbl.getIndexes()
db.minute_hq_tbl.getIndexes()
db.rt_stocks_tbl.getIndexes()
db.strategy_execution_tbl.getIndexes()
db.strategy_select_stocks_tbl.getIndexes()
db.trade_signal_tbl.getIndexes()
db.user_strategy_tbl.getIndexes()
db.stock_info_tbl.getIndexes()
db.company_valuation_tbl.getIndexes()
db.company_finance_tbl.getIndexes()
```
**查询结果**:
```
// Command #1 108:1 0.210 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      stock_code: 1,
      create_time: -1
    },
    name: 'user_strategy_id_1_stock_code_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      create_time: -1
    },
    name: 'user_strategy_id_1_create_time_-1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #2 109:1 0.029 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      create_time: -1
    },
    name: 'code_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      create_time: 1
    },
    name: 'code_create_time',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #3 110:1 0.037 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      minute_time: -1
    },
    name: 'code_1_minute_time_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      change_percent: 1,
      turnover: 1,
      volume: 1,
      minute_time: -1
    },
    name: 'code_1_change_percent_1_turnover_1_volume_1_minute_time_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      minute_time: 1
    },
    name: 'code_minute_time',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      minute_time: 1
    },
    name: 'minute_time_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #4 111:1 0.034 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      create_time: -1
    },
    name: 'code_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      change_percent: 1,
      turnover: 1,
      create_time: -1
    },
    name: 'code_1_change_percent_1_turnover_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      create_time: -1
    },
    name: 'code_create_time',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      create_time: 1,
      code: 1
    },
    name: 'create_time_1_code_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #5 112:1 0.055 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      update_time: -1,
      start_date: -1
    },
    name: 'user_strategy_id_1_update_time_-1_start_date_-1'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      update_time: -1
    },
    name: 'user_strategy_id_1_update_time_-1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #6 113:1 0.027 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      strategy_id: 1,
      code: 1,
      create_time: -1
    },
    name: 'strategy_id_1_code_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      create_time: -1,
      score: 1
    },
    name: 'user_strategy_id_1_create_time_-1_score_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #7 114:1 0.026 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      stock_code: 1,
      create_time: -1
    },
    name: 'stock_code_1_create_time_-1'
  },
  {
    v: 2,
    key: {
      user_strategy_id: 1,
      create_time: -1
    },
    name: 'user_strategy_id_1_create_time_-1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      strategy_id: 1,
      create_time: -1
    },
    name: 'strategy_id_1_create_time_-1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #8 115:1 0.036 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      strategy_id: 1,
      _id: 1,
      user_id: 1
    },
    name: 'strategy_id_1__id_1_user_id_1'
  },
  {
    v: 2,
    key: {
      user_id: 1,
      _id: -1
    },
    name: 'user_id_1__id_-1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      user_id: 1,
      name: 1
    },
    name: 'user_id_1_name_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #9 116:1 0.027 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      industry: 1
    },
    name: 'code_1_industry_1'
  },
  {
    v: 2,
    key: {
      code: 1
    },
    name: 'code_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      industry: 1
    },
    name: 'industry_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      board: 1
    },
    name: 'board_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #10 117:1 0.025 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      report_date: -1
    },
    name: 'code_1_report_date_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      report_date: -1,
      ttm_pe: 1,
      total_market_cap: 1
    },
    name: 'code_1_report_date_-1_ttm_pe_1_total_market_cap_1'
  },
  {
    v: 2,
    key: {
      total_market_cap: 1
    },
    name: 'total_market_cap_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  },
  {
    v: 2,
    key: {
      ttm_pe: 1
    },
    name: 'ttm_pe_1',
    collation: {
      locale: 'zh',
      caseLevel: false,
      caseFirst: 'off',
      strength: 1,
      numericOrdering: false,
      alternate: 'non-ignorable',
      maxVariable: 'punct',
      normalization: false,
      backwards: false,
      version: '57.1'
    }
  }
]
// Command #11 118:1 0.036 s
[
  {
    v: 2,
    key: {
      _id: 1
    },
    name: '_id_'
  },
  {
    v: 2,
    key: {
      code: 1,
      report_date: -1
    },
    name: 'code_1_report_date_-1'
  },
  {
    v: 2,
    key: {
      code: 1,
      report_date: -1,
      net_profit_growth_rate: -1
    },
    name: 'code_1_report_date_-1_net_profit_growth_rate_-1'
  }
]
```

按代码里的实际查询条件，需要的索引清单（与 1.1 配套，必须带 collation）：


```javascript
const COL = { locale: "zh", strength: 1 };

// 订单：按用户策略查、按策略+股票+时间查最新
db.order_tbl.createIndex({ user_strategy_id: 1, create_time: -1 }, { collation: COL });
db.order_tbl.createIndex({ user_strategy_id: 1, stock_code: 1, create_time: -1 }, { collation: COL });

// 交易信号：三处查询都是 user_strategy_id/strategy_id + create_time 倒序
db.trade_signal_tbl.createIndex({ user_strategy_id: 1, create_time: -1 }, { collation: COL });
db.trade_signal_tbl.createIndex({ strategy_id: 1, create_time: -1 }, { collation: COL });

// 策略执行：user_strategy_id + update_time 倒序（含取最新一条）
db.strategy_execution_tbl.createIndex({ user_strategy_id: 1, update_time: -1 }, { collation: COL });

// 选股：user_strategy_id + create_time，排序字段是 score（数字，不受 collation 影响）
db.strategy_select_stocks_tbl.createIndex({ user_strategy_id: 1, create_time: -1, score: 1 }, { collation: COL });

// 历史行情：code + create_time 范围扫描，日/周/月各一张
db.day_hq_tbl.createIndex({ code: 1, create_time: 1 }, { collation: COL });
db.week_hq_tbl.createIndex({ code: 1, create_time: 1 }, { collation: COL });
db.month_hq_tbl.createIndex({ code: 1, create_time: 1 }, { collation: COL });

// 分钟线：code + minute_time；另有按 minute_time 单查整分钟全市场
db.minute_hq_tbl.createIndex({ code: 1, minute_time: 1 }, { collation: COL });
db.minute_hq_tbl.createIndex({ minute_time: 1 }, { collation: COL });

// 实时行情：唯一入口是分钟前缀正则 + code $in，见 2.2 的说明
db.rt_stocks_tbl.createIndex({ create_time: 1, code: 1 }, { collation: COL });

// 股票基础信息
db.stock_info_tbl.createIndex({ code: 1 }, { collation: COL });
db.stock_info_tbl.createIndex({ industry: 1 }, { collation: COL });
db.stock_info_tbl.createIndex({ board: 1 }, { collation: COL });

// 估值 / 财务：按 code + report_date 倒序取最新，且按数字范围过滤
db.company_valuation_tbl.createIndex({ code: 1, report_date: -1 }, { collation: COL });
db.company_valuation_tbl.createIndex({ total_market_cap: 1 }, { collation: COL });
db.company_valuation_tbl.createIndex({ ttm_pe: 1 }, { collation: COL });
db.company_finance_tbl.createIndex({ code: 1, report_date: -1 }, { collation: COL });

// 用户策略：按 user 查 + name 唯一性校验（count_documents）
db.user_strategy_tbl.createIndex({ user_id: 1, _id: -1 }, { collation: COL });
db.user_strategy_tbl.createIndex({ user_id: 1, name: 1 }, { collation: COL });

// 股票池
db.stock_pool_tbl.createIndex({ user_id: 1 }, { collation: COL });
db.stock_pool_stock_tbl.createIndex({ pool_id: 1 }, { collation: COL });
db.stock_pool_stock_tbl.createIndex({ code: 1 }, { collation: COL });
```

两点执行注意：

- `{ collation: COL }` 不能省。省了的话索引是 simple collation，在 1.1 未修复前完全用不上。
- 建索引前先确认存量数据没有重复（尤其是 `stock_info_tbl.code`，见 2.4）。如果要建唯一索引，必须先清理。

### 1.3 `limit` 语义混淆，大部分查询无上限

`db/mongo/mongo_exec.py:103-106` 把 `limit=None` 转成 `0`，pymongo 的 `.limit(0)` 等于不限制。统计结果：

| 项目 | 数量 |
| --- | --- |
| `query_by_condition` 调用点总数 | 77 |
| `limit` 为 `None` / `0` / 变量（默认 0） | 68 |

典型无界查询（`limit` 未传，走默认 0）：

- `mongo_stocks_his_hq_impl.py:228-234`：日/周/月 K 线。调用方 `stock_fetch.py:740`、`786`、`831` 都没传 limit。一只票加一个大日期区间，就会把该区间全部 K 线读进内存。
- `mongo_strategy_execution_impl.py:133-139`：`query_executions_by_user_strategy_ids`。`/api/user_strategy/executions` 会返回所有策略 × 全部历史执行记录 × 完整 `positions` 数组。
- `mongo_order_impl.py:95-107`、`109-150`：订单列表接口无上限。
- `mongo_minute_hq_impl.py:137-158`：`query_minute_hq_by_time` 按分钟精确匹配，一次返回全市场该分钟所有股票（5000 条量级），且无 limit。
- `mongo_stock_info_impl.py:115-125`：`query_all_stock_info` 默认 0。
- `mongo_trade_signal_impl.py:143-185`：`/api/trade_signal/list` 的 `limit` 默认 0，接口文档就写着"0 表示不限制"。

**修法**：给 `query_by_condition` 加一个保守的缺省上限，同时把 API 层的分页默认值改成有限值。

```python
DEFAULT_LIMIT = 1000  # 兜底上限，防止漏传 limit 导致全表物化

def query_by_condition(self, table, condition, sort, skip, limit,
                       collation=None, max_limit=DEFAULT_LIMIT):
    if not limit:
        self.logger.warning("query_by_condition 未指定 limit，按 %d 截断: %s",
                            max_limit, condition)
        limit = max_limit
```

同时对确实需要全量的场景（`stock_fetch` 里的同步任务）显式传大值，让"全表"成为有意为之而不是默认行为。

**修改确认**： 增加默认上限，同时要确保默认上限不会影响到查询结果

### 1.4 内存排序风险

字符串排序无法走索引（1.1），因此下列查询都是内存排序，超过 100 MB 阻塞排序上限会直接抛错：

- `mongo_minute_hq_impl.py:112-135` `query_latest_minute_hq_today`：`$match` 当天全部分钟线（按 5000 只 × 240 分钟估算约 120 万文档/日）后 `$sort` 再 `$group`。**没有 `allowDiskUse=True`**，是全项目最可能直接报错的一处。
- `mongo_company_valuation_impl.py:141-220`、`239-286`、`288-335`：估值聚合无 `$match` 时先全表 `$sort`。

对比：`mongo_rt_stocks_impl.py:186` 和 `mongo_order_impl.py:189` 的聚合调用都带了 `allowDiskUse`（后者目前没带，见下），说明这个参数是知道的，只是没有一致地加上。

**修法**：给 `query_latest_minute_hq_today` 的 `aggregate` 加 `allowDiskUse=True`；更彻底的做法是加一层"当期分钟表"（或用 `$match` 收窄到具体分钟）避免每天扫全量。

**修改确认**： query_latest_minute_hq_today这个函数目前项目没有用到，检查一下为啥没有用到，也可以先优化避免以后用到

---

## 二、数据存取性能

### 2.1 async 端点内跑同步 IO（影响全部 122 个端点）

统计：`web_api/routers/` 下 122 个端点全是 `async def`，无一例外。

FastAPI 的调度规则是：`def` 端点会被丢进线程池，`async def` 端点跑在事件循环线程里。现在所有端点都是 `async def`，函数体里却是同步的 pymongo 和同步 `redis.StrictRedis`，因此**没有一次数据库调用会让出事件循环**。单 worker 下，一个慢查询会把所有并发请求一起卡住。

最糟的一条链路：

```
web_api/routers/market.py:66   async def get_day_kline
  -> market.py:79              AppContext().stock_fetch.get_stock_day_his_hq(...)
  -> stock_fetch.py:740        查库（无 limit）
  -> stock_fetch.py:763        库中缺失时逐票发同步 HTTP
  -> stock_fetch.py:688          tickflow timeout=30s, retries=3
  -> stock_fetch.py:692          akshare 回退
```

`tickflow_fetch/__init__.py:39` 的客户端是 `PoolManager(timeout=30.0, retries=3)`。单只票最坏 90 秒，多只缺失票串行叠加。请求 `/api/market/kline/day?codes=...&start=...&end=...` 在缓存缺失时会让整个服务停止响应数十秒。

**修法（按性价比排序）**：

1. 把纯数据库读的端点从 `async def` 改成 `def`，交给 FastAPI 的线程池。改动只是删一个 `async` 关键字，收益最直接。
2. 必须保持 `async` 的端点（内部要 `await` 的），把同步 DB 调用包进 `await run_in_threadpool(...)`（`from starlette.concurrency import run_in_threadpool`）。
3. `market.py` 的三个 K 线端点，缓存未命中时不要在请求内同步抓取。要么改成后台任务 + 返回 202，要么加超时上限。

**修改确认**：确认修改

### 2.2 聚合管道与其 `$match` 位置

`mongo_company_valuation_impl.py:264-283`、`313-335`（`query_codes_by_cap_range` / `query_codes_by_ttm_range`）：

```python
pipeline = [
    {"$sort": {"code": 1, "report_date": -1}},
    {"$group": {"_id": "$code", "doc": {"$first": "$$ROOT"}}},
    {"$replaceRoot": {"newRoot": "$doc"}},
]
if match:
    pipeline.append({"$match": match})   # 过滤被放在分组之后
```

过滤阶段在 `$group` 之后，意味着先对全表做"每股取最新一条"，再按市值/PE 过滤。`total_market_cap`、`ttm_pe` 是数字字段，本来可以走索引缩小输入，现在完全用不上。

`query_valuation_by_ranges`（同文件 179-209）的 `$match` 在最前，两者语义不同但性能差别很大。这里需要先确认业务语义：是"所有股票的最新估值中筛选"，还是"筛选后再取最新"。前者必须过滤在后，后者可以前置。**这一条需要产品侧确认，我不擅自改。**

`web_api/routers/stock_screener.py:40` 是调用方，在默认参数下（四个范围全为 0）`match` 为空字典，管道连 `$match` 阶段都没有，直接全表 `$sort`。

**修改确认**：按照 “先筛选再取最新的”修改

### 2.3 `$regex` 与 `$or` 阻止索引前缀

`mongo_stock_info_impl.py:131-148`：

```python
for code in codes:
    pure_code = self.normalize_code(code)
    regex_conditions.append(
        {"code": {"$regex": f"^(sh|sz)?{pure_code}(\\.(SH|SZ))?$"}}
    )
return self.mongo_exec.query_by_condition(
    table=self.stock_info_table,
    condition={"$or": regex_conditions}, ...)
```

三个问题叠加：

- `$or` 下每个分支都要单独评估，N 个代码等于 N 次索引查找，无法合并。
- 正则开头是 `(sh|sz)?` 交替，MongoDB 无法从交替分支中提取固定前缀，即使有 `{code:1}` 索引也只能退化为扫描全索引。
- `insert_or_update_stock_info`（同文件 42 行）把同样的正则用在 `update_one` 的 upsert 条件上，`sync_all_stock_lists` 对全市场 5000+ 只股票逐个执行，每次都要先扫一遍。

**修法**：`tools.guess_stock_code_suffix`（`utils/tools.py:18-37`）已经把纯数字代码归一化成 `600519.SH` 形式。既然写入路径统一用这个格式，查询侧就该归一化后做等值匹配：

```python
full_codes = [tools.guess_stock_code_suffix(self.normalize_code(c)) for c in codes]
condition = {"code": {"$in": full_codes}}
```

前提是存量数据也统一成带后缀格式。**需要先核对库里 `stock_info_tbl.code` 的实际取值分布**，确认是否还有 `sh600519`、`600519` 混存。如果不一致，得先做一次数据归一再改查询。

**修改确认**：后台在tools.py增加了guess_stock_code_suffix函数，自动猜测股票代码的后缀，不再使用regex适配股票代码, regex会影响索引搜索性能,以后存入数据库的股票代码格式统一为xxxx.SH，xxxx.SZ, xxxx.BJ,没有其他格式

### 2.4 N+1 查询

按严重度排列，全部是确认过的代码：

| 位置 | 模式 | 放大倍数 |
| --- | --- | --- |
| `stock_pool.py:127-139` | `for code in req.codes` 内 `query_by_codes([code])` + `is_stock_blacklisted()` | 2N 次往返，500 只票 = 1000 次 |
| `stock_pool.py:167-173` | `for code in req.codes` 内 `query_by_codes([code])` | N 次 |
| `blacklist.py:51-57`、`77-83` | 同上，两份拷贝 | N 次 |
| `stock_pool.py:71-77` | `for s in strategies` 内 `query_strategy_by_id(sid)` | N 次 |
| `user.py:144-145` | `for pool in pools` 内 `delete_stock_pool(...)` | N 次删除，且无事务，中途失败留下半成品 |
| `tickflow_proxy.py:141-160` | 内层每只成分股一次 `query_by_codes([code])` + 一次写入 | N 次读 + N 次写 |
| `ak_stock_proxy.py:251-284`、`293-327`、`337-372`、`381-415` | 四段 `df.iterrows()` 内 `query_by_codes([code])` | 四份拷贝，各 N 次 |
| `stock_fetch.py:938-957`、`1006-1024`、`1065-1077` | `for skip in range(...)` 内先查后写 | N 次 |

`stock_pool.py` 和 `blacklist.py` 的四处是同一个模式复制出来的。批量能力在 impl 层已经具备（`batch_add_to_blacklist` 用的就是 `bulk_write` + `$or`），只是校验这一步退化成了逐条。

**修法**：校验合并成一次批量查询。

```python
# 改前：N 次
for code in req.codes:
    ok, exists = stock_impl.query_by_codes([code])

# 改后：1 次
ok, exists = stock_impl.query_by_codes(req.codes)
found = {r.get("code", "") for r in (exists or [])}
```

黑名单同理，把 `is_stock_blacklisted` 换成一次 `{"user_id": user_id, "code": {"$in": codes}}` 的查询。

`user.py:144-145` 需要先确认 `MongoStockPoolImpl` 是否提供按 user_id 批量删除的方法，没有的话应该在删除用户时用一条 `delete_many`。

**修改确认**：按照建议修改，在MongoStockPoolImpl增加一个按照user_id删除的函数

### 2.5 写接口内联全量重分配

以下接口在**请求处理过程中同步执行全量重分配**，每次触发都是"读全量股票池 + 读全量热门行业股票 + 清表 + 逐代理写回"：

| 接口 | 调用 |
| --- | --- |
| `user_strategy.py:81` | `distribute_user_strategy_to_workflow()` |
| `stock_pool.py:151`、`178` | `distribute_pool_stocks_to_data_agents(pool_id=...)` |
| `data_agent.py:69-70`、`112-113`、`137-138` | pool + industry 两次全量重分配 |
| `stock_info.py:115` | `distribute_industry_stocks_to_data_agents()` |
| `workflow_service.py:69`、`166` | `distribute_user_strategy_to_workflow()` |

这些函数虽然是 `async def`，内部全是同步 pymongo，`await` 只是让出一次调度点，实际仍在事件循环线程里跑完全程。管理员改一次数据代理的描述，会顺带跑完全量重分配。

被调用方内部还有 N+1：`stock_fetch.py:975-978` 对每个热门行业各查一次 `query_by_industry`（无 limit）。

**修法**：改成后台任务（`BackgroundTasks` 或独立的 asyncio task），接口立即返回；再给重分配加一把锁避免并发叠加。

**修改确认**： 上面这几个任务都是给多个实例分配资源的过程，可以按照建议修改，实现异步任务，并加锁避免重叠，

### 2.6 其他

- `mongo_exec.py:113/120` 返回 `results if results else None`，把空列表和查询失败都变成 `None`，调用方遍地 `(records or [])`。建议区分：失败返回 `(False, None)`，空结果返回 `(True, [])`。
- `redis_exec.py:125-134` `get_key_string` 先 `exists` 再 `get`，两次命令。`auth.py:97` 每个带鉴权的请求都走这条路，等于全站每次请求多一次 Redis 往返。改成一次 `GET`（返回 `None` 即不存在）。`get_value_from_rlist` 同理有 3 次。
- `redis_exec.py:107-112` `set_key_string` 在 Redis 异常时吞掉错误返回 `False`；`auth.py:98` 又把它当成 token 无效，Redis 抖动会让所有已登录用户同时 401。
- `mongo_rt_stocks_impl.py:176` 的排序 tiebreaker 是 `change_percent: 1`，同一秒多条记录时取到的是**涨幅最小**的那条。`mongo_order_impl.py:183` 用的是 `_id: -1`（更合理）。**这条需要确认是否为有意设计。**
- `web_api/api_run.py:112-123` 的 `ExceptionMiddleware` 捕获所有异常后返回统一的 `"统一异常处理"`，不打日志也不带上 traceback，线上排查困难。建议至少 `logger.exception`。
- `api_run.py:47-53` CORS 同时设 `allow_origins=["*"]` 和 `allow_credentials=True`，这个组合浏览器会拒绝。要么去掉 credentials，要么把来源列成白名单。

---

## 三、功能性缺陷（顺带发现）

### 3.1 `stock_screener.py` 双重分页

`web_api/routers/stock_screener.py:40-102`：

1. 第 40 行已把 `skip` / `limit` 传给 `query_valuation_by_ranges`，在库侧切了一刀。
2. 第 64 行 `query_latest_finance_by_codes(val_codes)` 用的是切完之后的 codes。
3. 第 84-87 行按利润率过滤，结果集变小。
4. 第 99-102 行在 Python 里又切一刀 `rlist[skip:][:limit]`。

后果：`skip=100&limit=100` 时，第一层取第 100 到 200 条估值，过滤后剩不了几条，再 `[100:200]` 几乎必然返回空。**分页结果是不对的**，且每翻一页都重跑一次全表聚合。

**修法**：把利润率过滤下沉到聚合管道（`$addFields` 算利润率后 `$match`），分页只在管道末尾做一次。
**修改确认**：确认修改

### 3.2 `insert_user_strategy` 的 name 唯一性校验仍是先查后写

`mongo_user_strategy_impl.py:40-43` 用 `count_documents` 判断重名，然后 `insert_one`，两步之间没有原子性。并发下会漏。这也是之前讨论过、尚未落实的一项。彻底解决需要 `{user_id: 1, name: 1}` 唯一索引（上面索引清单里已列，但建唯一索引前要先清理存量重复数据）。

### 3.3 其他小项

- `stock_fetch/stock_fetch.py:54` 的 `CACHE_EXPIRE = 60 * 60 * 24` 定义后全项目再无引用，是死代码。`stock_fetch` 整个模块 grep `redis` 零命中，K 线/行情完全没走缓存，与 `AGENTS.md` 里"Historical and financial data flows should use Redis caching"的约定不符。
- `stock_fetch.py:263-274` 的 `jobListener` 在收到 `EVENT_JOB_MAX_INSTANCES` 时把 `max_instances` 加 10，任务重叠时会把上面那些 N+1 并发放大。
- `cfg/stock.cfg` 的 `history_hq_sync_number_of_once=1` 加每批 `time.sleep(6)`，即一只票 6 秒。同步 N 只票约需 N×6 秒。

**修改确认**： CACHE_EXPIRE可以删除，查询k线数据不走缓存，去掉AGENTS.md中的redis缓存约定， his_hq_sync_task同步任务在server端不执行了，移到q_data_proxy中执行

---

## 四、鉴权缺陷（非性能，但严重度更高）

这四个是确认过的，建议优先于性能项处理。

### 4.1 任意登录用户可删除他人的交易信号和选股记录

`web_api/routers/trade_signal.py:85-95`：

```python
async def delete_trade_signal(id: str, user_info: tuple[str, int] = Depends(get_current_user_with_role)):
    impl = MongoTradeSignalImpl()
    deleted = impl.delete_signal(id)   # user_info 取了完全没用
```

`web_api/routers/strategy_select_stocks.py:89-99` 是完全相同的写法。对比同文件里的 `/add` 和 `/latest` 都有归属校验，这两处是遗漏。

**修法**：删除前先查记录，比对 `user_strategy_id` 是否属于当前用户（管理员跳过）。`delete_signal` / `delete_select_stock` 都可以用 `_resolve_user_strategy_ids_from_user` 复用现有逻辑。

**修改确认**：确认修改

### 4.2 列表接口可通过传参越权读取他人数据

`web_api/routers/trade_signal.py:113-124`：

```python
if strategy_id or user_strategy_id:
    # 指定了策略 ID，直接按条件查询     <-- 无归属校验
    res, records = impl.query_signals(strategy_id=strategy_id, user_strategy_id=user_strategy_id, ...)
```

`web_api/routers/strategy_select_stocks.py:117-125` 同样：传 `user_strategy_id` 就直接查，不校验该 ID 是否属于当前用户。普通用户只要猜到（或从 `/api/user_strategy/all` 拿到，见 4.3）别人的 `user_strategy_id`，就可以读取其全部信号与选股记录。

对比 `/latest` 接口（`trade_signal.py:181-184`）有正确校验，说明是分支遗漏。

**修法**：把 `if user_strategy_id:` 这一分支改成先做归属校验，或统一走 `_resolve_user_strategy_ids_from_user` 取交集。

**修改确认**：确认修改，做归属检查

### 4.3 两个写接口完全无鉴权

`web_api/routers/stock_info.py` 的 router 没有 `dependencies`，这两个写接口函数也没有 `Depends`：

- `stock_info.py:108-116` `POST /api/stock_info/hot_industry/add`
- `stock_info.py:119-128` `DELETE /api/stock_info/hot_industry/delete`

匿名即可增删热门行业，且 `/add` 会连带触发 `distribute_industry_stocks_to_data_agents()` 全量重分配。这是全项目仅有的两个"无鉴权写操作"（其他路由的写接口都有 `require_admin` 或 `get_current_user`）。

对比参照：`stock_pool.py:30` 和 `order.py:18` 都在 router 级挂了 `dependencies=[Depends(get_current_user)]`。

**修法**：给两个接口加 `user_id: str = Depends(require_admin)`。
**修改确认**：hot_industry/add hot_industry/delete 修改 这两个接口只有管理员可以操作

### 4.4 公开接口泄露 user_id 列表

`web_api/routers/user_strategy.py:175-199` `GET /api/user_strategy/all`，docstring 明写"公开接口，无需登录"，返回全部用户策略的 `user_id`、`strategy_id`、`pool_id`。这是 4.2 越权读取的 ID 来源。

**这条需要确认是否有意设计。** 如果小程序端确实需要匿名读取，建议只返回必要字段并去掉 `user_id`。
**修改确认**：不修改，/api/user_strategy/list 这个接口是针对用户的，/api/user_strategy/all 是针对所有用户，是公开接口提供给q_trading_work调用
---

## 五、建议的处理顺序

| 优先级 | 事项 | 类型 | 原因 |
| --- | --- | --- | --- |
| P0 | 4.1 / 4.2 / 4.3 鉴权修复 | 安全 | 越权可被直接利用 |
| P0 | 1.1 去掉全局 collation（或改为按需） | 性能 | 不解这个，建索引也无效 |
| P0 | 1.2 按清单建索引 | 性能 | 需先核对线上现状 |
| P1 | 1.3 limit 兜底 + API 分页默认值 | 性能/稳定 | 防止全表物化 |
| P1 | 2.1 去掉 `async def`（或包 threadpool） | 并发 | 一处改动，全局受益 |
| P1 | 3.1 stock_screener 分页修复 | 功能 | 当前结果不正确 |
| P1 | 1.4 `allowDiskUse` + minute_hq 聚合收窄 | 稳定 | 可能直接报错 |
| P2 | 2.3 正则改等值匹配 | 性能 | 需先归一化存量数据 |
| P2 | 2.4 N+1 批量改写 | 性能 | 定时任务与写接口 |
| P2 | 2.5 全量重分配改后台任务 | 性能 | 写接口响应时间 |
| P2 | 2.6 杂项（Redis 单次 GET、异常日志、CORS） | 性能/可维护 | 逐条小改 |
| P3 | 3.2 name 唯一索引 | 数据一致性 | 需先清存量重复 |
| P3 | 3.3 死代码与缓存缺失 | 可维护 | 与 AGENTS.md 约定对齐 |

---

## 六、本次未覆盖 / 需要现场确认

1. **线上索引现状**：见 1.2 的 `getIndexes()` 清单，需要实际执行确认。
2. **`stock_info_tbl.code` 的取值分布**：决定 2.3 能否直接改成等值匹配。
3. **`query_codes_by_cap_range` 的过滤位置**：决定 2.2 是改为前置过滤还是保留。
4. **`change_percent: 1` tiebreaker**：确认是否为有意设计。
5. **`/api/user_strategy/all` 公开**：确认是否业务需要。
6. **各表实际行数**：无法连库，文中所有数据量均为按表用途和写入频率的推断，未实测。建议执行 `db.<coll>.estimatedDocumentCount()` 后按实际量级调整索引清单的排序。
7. 本机未安装 pymongo/pydantic，所有结论来自静态阅读，**未在真实环境执行过任何查询或 explain**。
- 安装pymongo的环境是: conda activate py3-12 

---

## 七、实施结果

改动按上表落地。验证环境 `conda activate py3-12`（pymongo 4.9.1，fastapi/apscheduler/pandas 齐全）。本机仍无 MongoDB 与 Redis，以下验证均为静态检查、桩测试与 FastAPI 应用导入，**未连库执行过任何查询**。

### 7.1 逐项状态

| 章节 | 状态 | 落地内容 |
| --- | --- | --- |
| 1.1 | 已改 | collation 改为可选参数，默认不加；新增 `COLLATION_ZH` 常量供调用方显式传入 |
| 1.2 | 无需改动 | 索引线上已存在（用户提供的 `getIndexes()` 输出证明）。本次未新建任何索引 |
| 1.3 | 已改 | 新增 `DEFAULT_QUERY_LIMIT=10000` 与 `UNLIMITED_QUERY_LIMIT=-1`；未传 limit 时兜底截断并打 WARNING |
| 1.4 | 已改 | 估值聚合 `$match` 前置并加 `allowDiskUse=True`；`query_latest_minute_hq_today` 增加 `day` 参数收窄范围 |
| 2.1 | 已改 | 114 个路由端点由 `async def` 改为 `def`；中间件 `dispatch` 保留 async |
| 2.2 | 已改 | 见 1.4，两处聚合（市值、TTM）均已调整 |
| 2.3 | 已改 | 股票代码归一化为 `xxxx.SH/SZ/BJ` 后等值 / `$in` 匹配，`$regex` 与 `$or` 全部移除 |
| 2.4 | 已改 | N+1 改批量 `$in`；新增 `delete_stock_pools_by_user` |
| 2.5 | 已改 | 重分配改后台线程投递，同名任务加锁串行 |
| 2.6 | 部分已改 | 见 7.4 |
| 3.1 | 已改 | stock_screener 分页收敛为末尾一次性；排序改按 code |
| 3.2 | 未改 | 未收到确认，保持先查后写 |
| 3.3 | 部分已改 | 见 7.4 |
| 4.1 | 已改 | 删除接口加归属校验 |
| 4.2 | 已改 | 列表接口加归属校验 |
| 4.3 | 已改 | 两个 hot_industry 写接口加 `Depends(require_admin)` |
| 4.4 | 不改 | 按确认保留公开接口 |

### 7.2 实施中发现并修掉的四个必现缺陷

这四个都不在原始 review 清单里，是改代码过程中静态校验暴露出来的，且都会在线上直接抛异常或静默丢数据。

**一、`web_api/routers/user.py` 注销账号必然 500**

原代码 `pool_impl.delete_stock_pool(pool.get("name", ""))`，而 `MongoStockPoolImpl` 上没有 `delete_stock_pool` 这个方法（tests 里引用了同一个不存在的方法）。改为调用新增的 `delete_stock_pools_by_user(user_id)`，顺带把「按名字删」改成「按 user_id 删」，避免同名池误删。

**二、`insert_or_update_stock_pool` 的 upsert 条件漏了 `user_id`**

filter 只有 `{"name": name}`，第二个用户建同名股票池会直接覆盖第一个用户的记录。已把 `user_id` 加入 filter，`bulk_upsert_stock_pool` 同样修正。

**三、`DELETE /api/data_agent_industry_stocks/{id}` 必然 500**

`web_api/routers/data_agent_industry_stocks.py:61` 调用 `impl.delete_by_id(id)`，而 `MongoDataAgentIndustryStocksImpl` 上只有 `delete(id="")`。方法名对不上，每次调用抛 `AttributeError`，被 `ExceptionMiddleware` 吞成 500「统一异常处理」。已改为 `impl.delete(id)`。

**四、估值与财务同步任务每只股票都抛异常，且异常被静默丢弃**

- `stock_fetch/akshare_fetch/ak_stock_proxy.py:431` 调用 `MongoCompanyValuationImpl().normalize_code(code)`，`:559` 调用 `MongoCompanyFinanceImpl().normalize_code(code)`。但 `normalize_code` 只定义在 `MongoStockInfoImpl` 上，这几个 Impl 都是**无基类的普通类**，也没有 `__getattr__` 兜底。每次调用抛 `AttributeError`。
- 这两个方法分别由 `stock_valuation_sync_task`（每交易日 03:00）和 `stock_financial_sync_task`（每交易日 04:00）调用，两个调度都是**启用状态**。
- 更麻烦的是 `stock_fetch.py:591/622` 的 `for future in as_completed(futures)` 循环**从不调用 `future.result()`**，线程里抛的异常被存进 future 后直接丢弃。日志照常打印「进度 100/100」，实际一条都没写库。

修法：把纯代码提取下沉为 `utils.tools.pure_stock_code()`（纯字符串变换，不依赖数据库连接），`MongoStockInfoImpl.normalize_code` 改为转发，`ak_stock_proxy.py` 两处直接调用它。`tests/test_stock_fetch_financial_sync.py:65` 有同样的写法，一并修正。

**第二个问题仍未处理**：`future.result()` 未调用导致任务失败不可见，属于独立的可观测性缺陷，见 7.5 第 3 条。

### 7.3 关键改动要点

**`db/mongo/mongo_exec.py`（1.1 + 1.3）**

```python
DEFAULT_QUERY_LIMIT: int = 10000
UNLIMITED_QUERY_LIMIT: int = -1
COLLATION_ZH: dict[str, Any] = {"locale": "zh", "strength": 1}
```

`query_by_condition` 的 limit 语义分四态：未传按 `DEFAULT_QUERY_LIMIT` 截断并打 WARNING；`0` 沿用既有「不限制」契约；负数（`UNLIMITED_QUERY_LIMIT`）= 显式全量；正数按原值。collation 默认不传，只有显式传入才 `.collation(...)`。

取 10000 的依据：全项目 76 个 `query_by_condition` 调用点中，48 个传字面量 `None`、20 个透传 `limit`、4 个 `limit=0`、3 个 `limit=1`。必须保证兜底值大于 A 股全市场自然行数（约 5400），否则 `query_all_stock_info` 这类查询会被误截断。

需要显式全量的调用方已改为传 `UNLIMITED_QUERY_LIMIT`：`mongo_stocks_his_hq_impl.py` 的 `query_day_hq` / `query_week_hq` / `query_month_hq`（调用方要靠完整结果集判断缺失股票），以及 `mongo_stock_minute_hq_impl.py` 的 `query_minute_hq_by_time`。

**`stock_fetch/stock_fetch.py`（2.5 + 3.3）**

删除死代码 `CACHE_EXPIRE`。新增模块级投递设施：

```python
_DISTRIBUTE_LOCKS: dict[str, threading.Lock] = {}
_DISTRIBUTE_LOCK_GUARD = threading.Lock()

def submit_distribute_task(name: str, coro_getter) -> bool:
    lock = _get_distribute_lock(name)
    if not lock.acquire(blocking=False):
        logger.warning("重分配任务 %s 正在执行，本次触发跳过", name)
        return False
    def _worker() -> None:
        try:
            asyncio.run(coro_getter())
        except Exception:
            logger.exception("后台重分配任务执行失败: %s", name)
        finally:
            lock.release()
    try:
        threading.Thread(target=_worker, name=f"distribute-{name}", daemon=True).start()
    except Exception:
        lock.release()   # 线程创建失败必须归还锁，否则该任务被永久跳过
        raise
    return True
```

配套 `submit_pool_stocks_distribution` / `submit_industry_stocks_distribution` / `submit_user_strategy_distribution` 三个方法，分别用 `"pool_stocks"` / `"industry_stocks"` / `"user_strategy"` 加锁。12 处路由调用点由 `await ...distribute_*()` 改为 `...submit_*()`。

三个 `distribute_*` 虽然标了 `async def`，AST 检查显示函数体 `await=0`，全是同步 pymongo 调用，因此放进后台线程执行与原先语义一致，不会引入事件循环绑定问题。

`his_hq_sync_task` 的调度注册（`stock_fetch.py:264-272`）确认已是注释状态，server 端不再执行。

**`db/mongo/mongo_stock_info_impl.py` / `mongo_blacklist_impl.py` / `mongo_stock_pool_impl.py`（2.3）**

代码归一化统一为 `tools.guess_stock_code_suffix(tools.pure_stock_code(code))` 得到 `xxxx.SH/SZ/BJ`，查询侧一律等值或 `$in`。`_code_regex` / `_codes_or` 等 regex 构造已删除，两个文件里的 `import re` 也随之下线。

**`web_api/routers/trade_signal.py` / `strategy_select_stocks.py`（4.1 + 4.2）**

`_load_owned_strategy_ids()` 一次查库取出该用户的 `(user_strategy_id 集合, strategy_id 集合)`，供 `_assert_strategy_access()` 与 `_resolve_user_strategy_ids_from_user()` 复用，避免每次校验都往返数据库。管理员（`role == 0`）返回 `None` 表示不限制。校验点覆盖 `/add`、`DELETE /{id}`、`/list`、`/latest`。

**`web_api/routers/stock_screener.py`（3.1）**

估值查询不再传 `skip`/`limit`（改为 `skip=0, limit=0`），利润率过滤完成后在末尾统一 `sort` 再切片一次。

### 7.4 2.6 / 3.3 的分项落地情况

| 条目 | 状态 |
| --- | --- |
| 删除 `CACHE_EXPIRE` 死代码 | 已删（`stock_fetch.py`） |
| K 线数据不走缓存 | 确认属实，未引入缓存 |
| 去掉 `AGENTS.md` 的 Redis 缓存约定 | 已改。两条缓存约定合并为「K 线/历史/财务数据不经 Redis，直读直写 MongoDB」，`When Changing Code` 里的 `Redis cache-aside` 也改成 `persist to MongoDB`，并补了一句「不要把 Redis 缓存加到数据读路径，`db/redis` 只用于登录态」 |
| `his_hq_sync_task` 移出 server | 确认调度注册已注释 |
| `query_by_condition` 区分 `(True, [])` 与 `(False, None)` | **未改**，未收到确认。当前仍是 `return True, results if results else None` |
| `redis_exec.get_key_string` 由 `exists`+`get` 改单次 `GET` | **未改**，未收到确认 |
| `ExceptionMiddleware` 补 `logger.exception` | **未改**，未收到确认 |
| CORS `allow_origins=["*"]` 与 `allow_credentials=True` 冲突 | **未改**，未收到确认 |
| `change_percent: 1` tiebreaker | **未改**，原 review 就标注需先确认是否有意设计 |
| `jobListener` 收到 `EVENT_JOB_MAX_INSTANCES` 时 `max_instances += 10` | **未改**，未收到确认 |

### 7.5 残留项

1. **`query_latest_minute_hq_today` 是死代码**。AST 全量扫描确认 server 内无调用方；进一步扫描兄弟仓库 `q_data_proxy`，发现 `db/mongo/mongo_stock_minute_hq_impl.py` 里有一份近乎逐字相同的副本，**同样没有任何调用方**。函数本身已按 1.4 优化（`day` 参数 + `allowDiskUse=True`），但两个仓库都该决定是删除还是接上调用。

2. **`mongo_stock_info_impl.py:198` 的行业名正则仍在**。

```python
condition={"industry": {"$regex": industry}}
```

这是按行业名做子串匹配，和 2.3 的股票代码不是一回事，因此本次未动。但该正则无 `^` 锚定，MongoDB 无法用索引前缀，走 COLLSCAN。若行业名匹配需要保留模糊语义，可考虑改成前缀锚定 `^industry`（能用上索引）或拆成独立的 industry 字段做等值。**改语义前需产品确认**。

3. **`for future in as_completed(futures)` 吞掉所有异常**。`stock_fetch.py:591` 与 `:622` 两处循环只累加计数、不取结果，任务失败时日志显示正常完成。这是 7.2 第四个问题暴露出来的，建议补上错误计数：

```python
failed = 0
for future in as_completed(futures):
    completed += 1
    try:
        if not future.result():
            failed += 1
    except Exception:
        failed += 1
        self.logger.exception("同步失败: code=%s", futures[future])
if failed:
    self.logger.error("同步完成，失败 %d / %d 只", failed, total)
```

未擅自改动，因为这会改变任务的日志行为。

4. **同步端点改用 `def` 后受 FastAPI 线程池上限约束**。`def` 端点跑在 anyio 线程池里，默认容量 40。114 个端点全部做阻塞 Mongo/Redis I/O，高并发下会在 40 之后排队。仍然远好于原先 `async def` 直接阻塞事件循环（等于并发度 1），但如果压测发现排队，可以用 `anyio.to_thread.current_default_thread_limiter().total_tokens` 调整。

5. **`stock_fetch.start()` 仍是同步阻塞**：`stock_fetch.py:125-127` 顺序 `asyncio.run()` 三个全量重分配，服务启动要等它们跑完才开始接流量。本次未改，因为改成后台投递会让启动后出现一段「分配表已清空、尚未写入完成」的窗口，而阻塞式启动不存在这个窗口。若容器的健康检查因启动超时反复重启，需要单独评估。

6. **`query_latest_minute_hq_today` 之外的索引问题**：`COLLATION_ZH` 常量已加，但线上索引本身带 collation，而查询侧现在默认不加。二者不一致时，**字符串字段仍用不上那些带 collation 的索引**。1.2 的索引清单若要真正生效，需要建**不带 collation** 的索引，或者调用侧显式传 `COLLATION_ZH`。这一点在改索引之前必须和 1.2 一起重新确认。

### 7.6 验证记录

| 项目 | 方法 | 结果 |
| --- | --- | --- |
| 语法 | 128 个 `.py` 内存 `compile()` | 全部通过 |
| 端点结构 | AST 扫描全部路由装饰器函数 | 114 个 `def`，0 个 `async def` 缺 `await`；中间件 `dispatch` 按要求保留 async |
| import 链 | 真实导入 `web_api.api_run` 并生成 OpenAPI | 成功，94 个 path |
| 依赖注入 | 检查 OpenAPI `security` | 两个 hot_industry 写接口均已挂 `HTTPBearer` |
| impl 方法调用 | AST 按作用域解析 233 处调用，比对类定义含继承链 | 86 处命中校验，0 处未定义 |
| limit / collation | 桩 cursor 复刻 pymongo `.limit(0)` 语义 | 五态（未传/0/-1/正数/显式 collation）行为符合预期 |
| 代码归一化 | `pure_stock_code` 与 `guess_stock_code_suffix` 往返 | `600519.SH` / `sh600519` / `600519` / `000001.SZ` / `300750.SZ` / `430047.BJ` / `830799.BJ` 全部正确 |
| 聚合管道顺序 | 源码位置比对 | 两处均为 `$match` → `$sort` → `$group`，且 `allowDiskUse=True` |
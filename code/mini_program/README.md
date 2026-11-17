# QTrade 量化交易小程序

微信小程序前端，功能与 `q_trading_cli`（Web 端）保持一致。
后端复用 `q_trading_server`（FastAPI），**零 npm 依赖**；只有一步配置生成，把后台地址在构建时注入，真实地址不入库。

---

## 快速开始

```bash
# 1. 生成本地配置：把后台地址写进 config/.env.local（该文件不入库）
cp config/.env.local.example config/.env.local   # Windows: copy config\.env.local.example config\.env.local
#    编辑 MP_BASE_URL / MP_WS_URL（留空则用缺省 http://127.0.0.1:8000）
node tools/gen-config.js

# 2. 结构自检（可选，1 秒跑完，不需要装依赖）
node tools/check.js

# 3. 用微信开发者工具「导入项目」，目录选本文件夹
#    详情 → 本地设置 → 勾选「不校验合法域名...」  ← 开发期必做，否则所有请求失败
```

> 真机与体验版**只允许 HTTPS/WSS**，必须先把后端切到 HTTPS 域名。完整流程见 [`doc/上线步骤.md`](doc/上线步骤.md)。

---

## 功能一览

| Tab | 页面 | 对应 Web 页面 |
| --- | --- | --- |
| 首页 | 仪表盘：统计卡 / 累计收益图 / 近期运行状态 / 近期策略 / 系统消息 | `/` |
| 行情 | 股票池列表 → 池内实时行情 → 个股详情（日K/分时/信号） | `/stock-pools`、`/stock-quotes` |
| 策略 | 策略列表（筛选/分页/展开持仓·订单·日志）→ 详情 → 创建编辑 | `/strategies` |
| 我的 | 账号 / 数据概览 / 深色模式 / 各功能入口 / 退出登录 | （Web 无，聚合页） |

另有 8 个二级页面：策略监控、策略选股、搜索与筛选、股票池创建编辑、个人喜好设置、黑名单管理、微信登录与绑定。

**15 个页面 / 8 个组件**，全部原生实现。

---

## 目录结构

```
mini_program/
├── config/
│   ├── index.template.js   ★ 配置模板（入库）：地址用占位符，常量在此维护
│   ├── .env.local.example  本机配置模板（入库，复制为 .env.local）
│   ├── .env.local          本机真实地址（不入库）
│   └── index.js            构建产物（不入库，由 tools/gen-config.js 生成）
├── utils/               纯逻辑层：请求 / 接口 / 缓存 / 格式化 / 主题 / 信号通道
├── components/          8 个自研组件（导航、空态、图表、状态、行情行、骨架屏、字段、标签）
├── pages/               15 个页面
├── custom-tab-bar/      自定义底部导航
├── tools/               离线静态自检 check.js、配置生成 gen-config.js
└── doc/                 代码说明 / 接口契约 / 上线步骤
```

---

## 环境配置（构建时注入）

真实服务地址不写进仓库。配置分两层：

| 文件 | 是否入库 | 作用 |
| --- | --- | --- |
| `config/index.template.js` | 是 | 模板与常量（分页、轮询间隔），地址处是占位符 |
| `config/.env.local.example` | 是 | 本机配置样板 |
| `config/.env.local` | 否 | 本机真实地址，构建输入 |
| `config/index.js` | 否 | 构建产物，小程序实际 require 的文件 |

生成命令：

```bash
node tools/gen-config.js
```

取值优先级：环境变量 > `.env.local` > 缺省值 `http://127.0.0.1:8000`。

```bash
# 也可以直接用环境变量（CI / 发布机）
MP_ENV=production MP_BASE_URL=https://api.example.com node tools/gen-config.js
```

`MP_ENV=production` 时会强制校验：`BASE_URL` 必须是 `https://`，`WS_URL` 必须是 `wss://`，不满足直接失败退出。`MP_WS_URL` 显式留空表示关闭 WebSocket，自动降级为 HTTP 轮询。

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [`doc/代码功能说明.md`](doc/代码功能说明.md) | 架构、全局机制、逐页功能、组件说明、与 Web 的差异、已知边界 |
| [`doc/接口契约.md`](doc/接口契约.md) | 后端接口清单、**字段缩放规则**、需要后端新增的接口契约 |
| [`doc/上线步骤.md`](doc/上线步骤.md) | HTTPS 与 Nginx、微信后台配置、上传发布、自检清单、排错表 |
| [`ui/设计交付说明.md`](ui/设计交付说明.md) | 视觉与交互规范（设计令牌来源） |
| `q_trading_cli/doc/页面操作文档.md` | Web 端页面操作手册（功能基准） |

---

## 几个必须知道的坑

这几条都是对着后端源码逐行核对出来的，踩过一次就够：

1. **两类百分数缩放规则相反**
   行情类（`change_percent` / `turnover`）是**小数比率**，要 ×100；
   财务与信号类（`net_profit_growth_rate` / `profit_rate`）**已经是百分数**，不能再乘。
   → 代码里拆成 `fmtPctRatio()` 与 `fmtPctRaw()` 两个函数，调用点必须显式选择。

2. **`default_params` 是三层结构**
   模板 `{ match: { key: { desc, value } } }`，提交的 `strategy_params` 是两层 `{ match: { key: value } }`。混了就会渲染出空参数。

3. **`PUT /api/user/preference` 是全量替换**
   只传改动的字段会把其它字段清空，必须 5 个字段一起提交。

4. **策略选股的 `start_time` / `end_time` 必须传 `YYYY-MM-DD`**
   带时分秒会查不到数据。而分时行情接口的参数名是 `start` / `end`，不是 `start_time` / `end_time`。

5. **主题「是否用户选过」的判据是 `id` 与 `update_time` 同时为空**
   只看 `theme_mode` 会把「没选过」的用户误判成选了浅色。

6. **`PoolInfo` 没有 `type` 字段**，池类型恒为「手动维护」；`/stocks` 不返回数量，股票池列表需要 N+1 次请求。
   （后端补字段前端无需改代码，详见接口契约 5.4）

7. **信号里 `strategy_id` 是模板 ID，`user_strategy_id` 才是用户策略 ID**
   要把信号挂到具体策略上必须用后者，用前者会让同一模板下的多个策略互相串位，而且去重时会把不同策略的信号误丢。

---

## 后端待补充的接口

小程序侧均已按契约写好，**未实现时有降级行为，不阻塞上线**：

| 接口 | 用途 | 未实现时 |
| --- | --- | --- |
| `POST /api/user/wx_login` | 微信一键登录 | 提示改用账号密码登录 |
| `POST /api/user/wx_bind` | 微信与平台账号绑定 | 同上 |
| `GET /ws/trade_signal` | 信号 WebSocket 推送 | 自动降级为 HTTP 轮询 |

完整契约与自测清单见 [`doc/接口契约.md`](doc/接口契约.md) 第 5 节。

---

## 免责声明

本项目为量化策略的**研究与模拟跟踪工具**，所有行情、收益与信号数据仅供研究参考，**不构成任何投资建议**，也不提供真实交易通道。

/**
 * 后端接口层 —— 与 q_trading_server 的 FastAPI 路由一一对应，
 * 命名与分组对齐 q_trading_cli/api/*.py，便于对照排查。
 *
 * 所有函数返回解包后的 data（见 utils/request.js）。
 */

const { req } = require('./request');
const store = require('./store');
const config = require('../config/index');

/* ============================================================ 用户 */
const user = {
  /** 账号登录：account 支持 用户名 / 邮箱 / 手机号 —— POST /api/user/login */
  login: (account, password) =>
    req({ url: '/api/user/login', method: 'POST', data: { account, password }, auth: false }),

  /** 注册 —— POST /api/user/register（phone/email 为空时不传该键） */
  register: ({ account, password, phone, email }) => {
    const body = { account, password };
    if (phone) body.phone = phone;
    if (email) body.email = email;
    return req({ url: '/api/user/register', method: 'POST', data: body, auth: false });
  },

  /** 微信一键登录 —— POST /api/user/wx_login（⚠️ 后端待新增，契约见 doc/接口契约.md） */
  wxLogin: (code) =>
    req({ url: '/api/user/wx_login', method: 'POST', data: { code }, auth: false }),

  /** 微信账号与平台账号绑定 —— POST /api/user/wx_bind（⚠️ 后端待新增） */
  wxBind: ({ bind_ticket, account, password }) =>
    req({
      url: '/api/user/wx_bind',
      method: 'POST',
      data: { bind_ticket, account, password },
      auth: false
    }),

  /** 退出登录 —— POST /api/user/logout */
  logout: () => req({ url: '/api/user/logout', method: 'POST' }),

  /** 注销账号 —— DELETE /api/user/account */
  deleteAccount: (password) =>
    req({ url: '/api/user/account', method: 'DELETE', data: { password } }),

  /** 查询个人偏好 —— GET /api/user/preference */
  getPreference: () => req({ url: '/api/user/preference' }),

  /** 保存个人偏好（全量替换）—— PUT /api/user/preference */
  updatePreference: (prefs) =>
    req({ url: '/api/user/preference', method: 'PUT', data: prefs })
};

/* ============================================================ 股票池 */
const pool = {
  /** 股票池列表 —— GET /api/pool/list */
  list: (silent) => req({ url: '/api/pool/list', silent }).then((d) => d || []),

  /** 创建股票池 —— POST /api/pool/create */
  create: (name, description) => {
    const body = { name };
    if (description) body.description = description;
    return req({ url: '/api/pool/create', method: 'POST', data: body });
  },

  /** 删除股票池 —— DELETE /api/pool/{name} */
  remove: (name) =>
    req({ url: `/api/pool/${encodeURIComponent(name)}`, method: 'DELETE' }),

  /** 池内股票 —— GET /api/pool/{name}/stocks（返回仅 code + add_time） */
  getStocks: (name, silent) =>
    req({ url: `/api/pool/${encodeURIComponent(name)}/stocks`, silent }).then((d) => d || []),

  /** 添加股票 —— POST /api/pool/{name}/stocks/add */
  addStocks: (name, codes) =>
    req({
      url: `/api/pool/${encodeURIComponent(name)}/stocks/add`,
      method: 'POST',
      data: { codes }
    }),

  /** 剔除股票 —— DELETE /api/pool/{name}/stocks/remove */
  removeStocks: (name, codes) =>
    req({
      url: `/api/pool/${encodeURIComponent(name)}/stocks/remove`,
      method: 'DELETE',
      data: { codes }
    }),

  /** 加载并缓存股票池列表（60s 节流） */
  ensureLoaded: (force) => {
    if (!force && store.isFresh(store.pools)) {
      return Promise.resolve(store.getPools());
    }
    return pool.list(true).then((list) => {
      store.setPools(list);
      return list;
    });
  }
};

/* ============================================================ 策略模板 */
const strategy = {
  /** 全部策略模板 —— GET /api/strategy/list（公开接口） */
  listTemplates: (silent) =>
    req({ url: '/api/strategy/list', silent }).then((d) => d || []),

  /** 策略模板详情 —— GET /api/strategy/id/{id} */
  getById: (id, silent) =>
    req({ url: `/api/strategy/id/${encodeURIComponent(id)}`, silent }).then((d) => d || {}),

  /** 策略模板详情 —— GET /api/strategy/{name} */
  getByName: (name) =>
    req({ url: `/api/strategy/${encodeURIComponent(name)}` }).then((d) => d || {}),

  /** 加载并缓存策略模板（60s 节流） */
  ensureLoaded: (force) => {
    if (!force && store.isFresh(store.strategyTemplates)) {
      return Promise.resolve(store.strategyTemplates.map);
    }
    return strategy.listTemplates(true).then((list) => {
      store.setStrategyTemplates(list);
      return store.strategyTemplates.map;
    });
  }
};

/* ============================================================ 用户策略 */
const userStrategy = {
  /** 我的策略列表 —— GET /api/user_strategy/list */
  list: (silent) => req({ url: '/api/user_strategy/list', silent }).then((d) => d || []),

  /** 创建用户策略关联 —— POST /api/user_strategy/create */
  create: (payload) =>
    req({ url: '/api/user_strategy/create', method: 'POST', data: payload }),

  /** 修改（名称 / 股票池 / 资金 / 持仓数 / 参数 / 状态）—— PATCH /api/user_strategy/{id} */
  update: (id, payload) =>
    req({ url: `/api/user_strategy/${encodeURIComponent(id)}`, method: 'PATCH', data: payload }),

  /** 删除 —— DELETE /api/user_strategy/{id} */
  remove: (id) =>
    req({ url: `/api/user_strategy/${encodeURIComponent(id)}`, method: 'DELETE' }),

  /** 最近一次执行结果（含 positions）—— GET /api/user_strategy/{id}/execution/latest */
  latestExecution: (id, silent) =>
    req({
      url: `/api/user_strategy/${encodeURIComponent(id)}/execution/latest`,
      silent
    }).then((d) => d || {}),

  /** 我的全部执行结果 —— GET /api/user_strategy/executions */
  executions: (silent) =>
    req({ url: '/api/user_strategy/executions', silent }).then((d) => d || []),

  /** 运行日志 —— GET /api/user_strategy/{id}/runlog */
  runlog: (id, skip, limit, silent) => {
    const data = {};
    if (skip) data.skip = skip;
    if (limit) data.limit = limit;
    return req({
      url: `/api/user_strategy/${encodeURIComponent(id)}/runlog`,
      data,
      silent
    }).then((d) => d || []);
  },

  /** 加载并缓存我的策略（60s 节流） */
  ensureLoaded: (force) => {
    if (!force && store.isFresh(store.userStrategies)) {
      return Promise.resolve(store.getUserStrategies());
    }
    return userStrategy.list(true).then((list) => {
      store.setUserStrategies(list);
      return list;
    });
  }
};

/* ============================================================ 订单 */
const order = {
  /** 指定策略下若干股票的最新订单 —— GET /api/order/latest */
  latest: (userStrategyId, stockCodes, action, status, silent) => {
    const codes = (stockCodes || []).filter(Boolean);
    if (!codes.length) return Promise.resolve([]);
    const data = {
      stock_codes: codes.join(','),
      user_strategy_id: userStrategyId
    };
    if (action) data.action = action;
    if (status) data.status = status;
    return req({ url: '/api/order/latest', data, silent }).then((d) => d || []);
  },

  /** 某策略的订单列表 —— GET /api/order/list/{id} */
  listByStrategy: (userStrategyId, silent) =>
    req({ url: `/api/order/list/${encodeURIComponent(userStrategyId)}`, silent }).then(
      (d) => d || []
    )
};

/* ============================================================ 交易信号 */
const signal = {
  /** 信号列表 —— GET /api/trade_signal/list */
  list: ({ stockCodes, strategyId, action, startTime, endTime, skip, limit, silent } = {}) => {
    const data = {};
    if (stockCodes && stockCodes.length) data.stock_codes = stockCodes.join(',');
    if (strategyId) data.strategy_id = strategyId;
    if (action) data.action = action;
    if (startTime) data.start_time = startTime;
    if (endTime) data.end_time = endTime;
    if (skip) data.skip = skip;
    if (limit) data.limit = limit;
    return req({ url: '/api/trade_signal/list', data, silent }).then((d) => d || []);
  },

  /** 近 N 小时的全部信号（用于仪表盘/监控页的兜底轮询） */
  recent: (silent) => {
    const fmt = require('./format');
    const start = `${fmt.daysAgo(1)} 00:00:00`;
    return signal.list({
      startTime: start,
      limit: config.MAX_LIMIT,
      silent
    });
  }
};

/* ============================================================ 策略选股结果 */
const selectStock = {
  /** 某策略的今日选股结果 —— GET /api/strategy_select_stocks/list
   *  ⚠️ start_time / end_time 必须传 YYYY-MM-DD，传带时分秒会查不到数据。 */
  list: (userStrategyId, startDate, endDate, silent) => {
    const data = {};
    if (userStrategyId) data.user_strategy_id = userStrategyId;
    if (startDate) data.start_time = startDate;
    if (endDate) data.end_time = endDate;
    return req({ url: '/api/strategy_select_stocks/list', data, silent }).then((d) => d || []);
  }
};

/* ============================================================ 行情 */
const market = {
  /** 实时行情 —— GET /api/market/real_time */
  realTime: ({ code, codes, startTime, endTime, useDefaultTime, silent } = {}) => {
    const data = {};
    if (code) data.code = code;
    if (codes) data.codes = codes;
    if (startTime) data.start_time = startTime;
    if (endTime) data.end_time = endTime;
    if (useDefaultTime) data.use_default_time = 'true';
    return req({ url: '/api/market/real_time', data, silent }).then((d) => d || []);
  },

  dayKline: (code, start, end, silent) =>
    req({ url: '/api/market/kline/day', data: { code, start, end }, silent }).then(
      (d) => d || []
    ),

  weekKline: (code, start, end, silent) =>
    req({ url: '/api/market/kline/week', data: { code, start, end }, silent }).then(
      (d) => d || []
    ),

  monthKline: (code, start, end, silent) =>
    req({ url: '/api/market/kline/month', data: { code, start, end }, silent }).then(
      (d) => d || []
    ),

  /** 分钟（分时）行情 —— 参数是 start / end，不是 start_time（与 real_time 不同！） */
  minuteKline: (code, start, end, silent) =>
    req({
      url: '/api/market/kline/minute',
      data: { code, start, end },
      silent
    }).then((d) => d || [])
};

/* ============================================================ 财务 */
const finance = {
  /** 估值（批量返回 list，这里转成 {code: item}）—— GET /api/finance/valuation */
  valuation: (codes, silent) => {
    if (!codes) return Promise.resolve({});
    return req({ url: '/api/finance/valuation', data: { codes }, silent }).then((d) =>
      toCodeMap(d)
    );
  },

  /** 利润（批量返回 list，这里转成 {code: item}）—— GET /api/finance/profit */
  profit: (codes, silent) => {
    if (!codes) return Promise.resolve({});
    return req({ url: '/api/finance/profit', data: { codes }, silent }).then((d) =>
      toCodeMap(d)
    );
  },

  /** 公司基本信息 —— GET /api/finance/stock_info */
  stockInfo: (code, silent) =>
    req({ url: '/api/finance/stock_info', data: { code }, silent }).then((d) => d || [])
};

/** 把 list[dict] 或 dict 统一成 {code: item} */
function toCodeMap(result) {
  if (!result) return {};
  if (Array.isArray(result)) {
    const out = {};
    result.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const code = String(item.code || item.stock_code || '');
      if (code) out[code] = item;
    });
    return out;
  }
  if (typeof result === 'object') {
    // 单只时后端直接返回 dict，可能没有 code 键，此时无法建映射，按空处理
    const code = String(result.code || result.stock_code || '');
    if (code) return { [code]: result };
  }
  return {};
}

/* ============================================================ 股票基础信息 */
const stockInfo = {
  /** 按代码批量查名称 —— GET /api/stock_info/code */
  byCodes: (codes, silent) => {
    if (!codes || !codes.length) return Promise.resolve([]);
    return req({
      url: '/api/stock_info/code',
      data: { codes: codes.join(',') },
      silent
    }).then((d) => d || []);
  },

  /** 全量股票（limit 必须显式给，默认只返 100）—— GET /api/stock_info/list */
  list: (skip, limit, silent) =>
    req({
      url: '/api/stock_info/list',
      data: { skip: skip || 0, limit: limit || config.MAX_LIMIT },
      silent
    }).then((d) => d || []),

  /** 热门行业 —— GET /api/stock_info/hot_industry/list */
  hotIndustries: (silent) =>
    req({ url: '/api/stock_info/hot_industry/list', silent }).then((d) => d || []),

  /** 按行业查股 —— GET /api/stock_info/industry */
  byIndustry: (industry, silent) =>
    req({
      url: '/api/stock_info/industry',
      data: { industry, limit: config.MAX_LIMIT },
      silent
    }).then((d) => d || [])
};

/* ============================================================ 条件选股 */
const screener = {
  /** 按 市值 / PE / 利润率 范围筛选 —— GET /api/screener/search */
  search: ({ ttmMin, ttmMax, capMin, capMax, marginMin, marginMax, silent } = {}) =>
    req({
      url: '/api/screener/search',
      data: {
        ttm_min: ttmMin || 0,
        ttm_max: ttmMax || 0,
        cap_min: capMin || 0,
        cap_max: capMax || 0,
        margin_min: marginMin || 0,
        margin_max: marginMax || 0,
        limit: config.MAX_LIMIT
      },
      silent
    }).then((d) => d || [])
};

/* ============================================================ 黑名单 */
const blacklist = {
  /** 黑名单列表（仅 code / add_time / reason）—— GET /api/blacklist/list */
  list: (silent) => req({ url: '/api/blacklist/list', silent }).then((d) => d || []),

  /** 添加（中文逗号会被前端统一替换）—— POST /api/blacklist/add */
  add: (codes, reason) => {
    const body = { codes };
    if (reason) body.reason = reason;
    return req({ url: '/api/blacklist/add', method: 'POST', data: body });
  },

  /** 移除 —— POST /api/blacklist/remove */
  remove: (codes) =>
    req({ url: '/api/blacklist/remove', method: 'POST', data: { codes } })
};

/* ============================================================ 系统消息 */
const systemMessage = {
  /** 当前用户可收的系统消息（每条带 is_read）—— GET /api/system_message/user_messages */
  userMessages: (silent) =>
    req({ url: '/api/system_message/user_messages', silent }).then((d) => d || []),

  /** 标记单条消息已读（幂等）—— POST /api/system_message/read */
  markRead: (id) =>
    req({ url: '/api/system_message/read', method: 'POST', data: { id } })
};

/* ============================================================ 组合查询（页面级复用） */

/**
 * 拉取一个股票池内的实时行情，并补齐估值 / 利润字段。
 * 对齐 pages/stock_quotes.py::load_quotes_for_pool。
 * @returns {Promise<Array>} 行情列表（已合并 ttm_pe / net_profit_growth_rate）
 */
function fetchPoolQuotes(poolName, silent) {
  return pool.getStocks(poolName, silent).then((stocks) => {
    const codes = stocks.map((s) => String(s.code || '')).filter(Boolean);
    if (!codes.length) return [];
    const codesStr = codes.join(',');
    return market.realTime({ codes: codesStr, silent }).then((rows) => {
      const list = dedupeByCode(rows);
      return Promise.all([
        finance.valuation(codesStr, true).catch(() => ({})),
        finance.profit(codesStr, true).catch(() => ({}))
      ]).then(([valMap, profitMap]) => {
        list.forEach((item) => {
          const code = String(item.code || '');
          const val = valMap[code];
          if (val && !item.ttm_pe) item.ttm_pe = val.ttm_pe;
          const pft = profitMap[code];
          if (pft && !item.net_profit_growth_rate) {
            item.net_profit_growth_rate = pft.net_profit_growth_rate;
          }
        });
        // 池内顺序：以池的 code 顺序为准，行情缺失的置后
        const order = {};
        codes.forEach((c, i) => {
          order[c] = i;
          order[String(c).split('.')[0]] = i;
        });
        list.sort((a, b) => {
          const ia = order[String(a.code || '')];
          const ib = order[String(b.code || '')];
          return (ia === undefined ? 9999 : ia) - (ib === undefined ? 9999 : ib);
        });
        return list;
      });
    });
  });
}

/**
 * 关键字搜索股票行情：先按 code / name / 拼音解析成代码，再取行情并补财务字段。
 * 对齐 pages/stock_quotes.py::_search_stocks。
 */
function searchStockQuotes(keyword, silent) {
  const kw = String(keyword || '').trim().toLowerCase();
  if (!kw) return Promise.resolve([]);
  return stockInfo
    .list(0, config.MAX_LIMIT, true)
    .catch(() => [])
    .then((all) => {
      const matched = (all || []).filter(
        (s) =>
          String(s.name || '').toLowerCase().indexOf(kw) >= 0 ||
          String(s.code || '').toLowerCase().indexOf(kw) >= 0 ||
          String(s.pinyin || s.py || '').toLowerCase().indexOf(kw) >= 0
      );
      const codes = matched.slice(0, 30).map((s) => String(s.code || '')).filter(Boolean);
      const codesStr = codes.length ? codes.join(',') : keyword;
      return market.realTime({ codes: codesStr, silent }).then((rows) => {
        const list = dedupeByCode(rows);
        if (!list.length) return [];
        const all2 = list.map((x) => x.code).filter(Boolean).join(',');
        return Promise.all([
          finance.valuation(all2, true).catch(() => ({})),
          finance.profit(all2, true).catch(() => ({}))
        ]).then(([valMap, profitMap]) => {
          list.forEach((item) => {
            const code = String(item.code || '');
            if (!item.ttm_pe && valMap[code]) item.ttm_pe = valMap[code].ttm_pe;
            if (!item.net_profit_growth_rate && profitMap[code]) {
              item.net_profit_growth_rate = profitMap[code].net_profit_growth_rate;
            }
          });
          return list;
        });
      });
    });
}

/** 按 code 去重（保留首个） */
function dedupeByCode(rows) {
  const seen = {};
  const out = [];
  (rows || []).forEach((item) => {
    const c = String(item.code || '');
    if (c && !seen[c]) {
      seen[c] = 1;
      out.push(item);
    }
  });
  return out;
}

/** codes → 名称映射（兼容带后缀的代码） */
function fetchStockNameMap(codes) {
  return stockInfo
    .byCodes((codes || []).filter(Boolean), true)
    .catch(() => [])
    .then((infos) => {
      const map = {};
      (infos || []).forEach((info) => {
        const code = String(info.stock_code || info.code || '');
        const name = String(info.stock_name || info.name || '');
        if (code) {
          map[code] = name;
          map[code.split('.')[0]] = name;
        }
      });
      return map;
    });
}

module.exports = {
  user,
  pool,
  strategy,
  userStrategy,
  order,
  signal,
  selectStock,
  market,
  finance,
  stockInfo,
  screener,
  blacklist,
  systemMessage,
  fetchPoolQuotes,
  searchStockQuotes,
  fetchStockNameMap,
  dedupeByCode
};

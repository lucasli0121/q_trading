/**
 * 策略详情
 *
 * 入口：策略列表「详情」、策略监控的「策略」链接、仪表盘「近期策略」。
 * 与列表页的内嵌展开面板相比，本页是完整视图：
 *   - 概览：名称 / 状态 / 类型 / 描述 / 总收益 / 初始资金 / 最大持仓 / 股票池 / 创建时间
 *   - 策略参数：模板 default_params 与用户已保存参数的合并结果（用户值优先）
 *   - 持仓信息 / 订单明细 / 运行日志 三个标签（数据同源，逻辑与列表页复用同一口径）
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');
const { statusStyle, PARAM_GROUP_LABELS } = require('../../utils/constants');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    usId: '',
    loading: true,
    loadError: '',

    head: null,
    paramGroups: [],

    tab: 'positions',
    detailLoading: true,
    positions: [],
    trades: [],
    runlogs: []
  },

  onLoad(options) {
    theme.applyTo(this);
    if (!auth.guard()) return;
    const usId = String((options && options.id) || '');
    if (!usId) {
      this.setData({ loading: false, loadError: '缺少策略 ID' });
      return;
    }
    this.setData({ usId });
    this.load();
  },

  onShow() {
    theme.applyTo(this);
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  load() {
    this.setData({ loading: true, loadError: '' });
    return Promise.all([
      api.userStrategy.ensureLoaded(true),
      api.strategy.ensureLoaded(false).catch(() => ({})),
      api.pool.ensureLoaded(false).catch(() => [])
    ])
      .then(([list]) => {
        const us = (list || []).filter(
          (x) => String(x._id || x.id || '') === this.data.usId
        )[0];
        if (!us) {
          this.setData({ loading: false, loadError: '策略不存在或已被删除' });
          return null;
        }
        this.buildHead(us);
        this.setData({ loading: false });
        return this.loadTab(this.data.tab, true);
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请稍后重试' });
      });
  },

  buildHead(us) {
    const sid = String(us.strategy_id || '');
    const tpl = store.getStrategyTemplate(sid) || {};
    const initAmount = fmt.sf(us.initial_amount);
    const totalProfit = fmt.sf(us.total_profit);
    const profitPct = initAmount === 0 ? 0 : (totalProfit / initAmount) * 100;
    const st = statusStyle(String(us.status || 'stopped'));

    const poolId = String(us.pool_id || '');
    const pool = store.getPools().filter(
      (p) => String(p._id || p.id || '') === poolId
    )[0];
    const poolName = (pool && pool.name) || (poolId ? `#${poolId}` : '未关联');

    this.setData({
      head: {
        usId: this.data.usId,
        name: String(us.name || tpl.name || sid || '未命名策略'),
        description: String(tpl.description || '') || '暂无描述',
        stype: String(tpl.strategy_type || '-'),
        status: String(us.status || 'stopped'),
        statusText: st.text,
        poolName,
        initialAmount: fmt.money(initAmount),
        maxStockCount: Number(us.max_stock_count || 0),
        createTime: fmt.shortTime(us.create_time, 16) || '--',
        profitText: `${profitPct.toFixed(2)}%`,
        profitTone: fmt.toneOf(profitPct),
        profitAmountText: fmt.signed(totalProfit, 2),
        profitAmountTone: fmt.toneOf(totalProfit)
      },
      paramGroups: this.buildParams(tpl, us.strategy_params || {})
    });
  },

  /**
   * 参数分组展示。
   *
   * ⚠️ 策略模板 default_params 的真实结构是三层的：
   *     { match: { min_price: { desc: '股价下限', value: 3.0 }, ... }, buy: {...}, sell: {...} }
   *   而用户保存的 strategy_params 是两层的：{ match: { min_price: 5.0 }, ... }
   *   因此展示时以 default_params 决定「有哪些参数 + 中文名」，用户值存在则覆盖。
   */
  buildParams(tpl, saved) {
    const def = (tpl && tpl.default_params) || {};
    const groups = [];

    Object.keys(def).forEach((gk) => {
      const groupParams = def[gk];
      if (!groupParams || typeof groupParams !== 'object' || Array.isArray(groupParams)) return;
      const savedGroup = saved[gk] || {};
      const items = Object.keys(groupParams).map((k) => {
        const meta = groupParams[k];
        const nested = meta && typeof meta === 'object' && !Array.isArray(meta);
        const fallback = nested ? meta.value : meta;
        const userVal = savedGroup[k];
        const hasUser = userVal !== undefined && userVal !== null && userVal !== '';
        return {
          k: (nested && meta.desc) || k,
          v: fmtParamValue(hasUser ? userVal : fallback)
        };
      });
      if (items.length) {
        groups.push({ key: gk, title: PARAM_GROUP_LABELS[gk] || gk, items });
      }
    });

    // 用户保存了模板中不存在的分组时补一栏，避免参数「查不到」
    Object.keys(saved).forEach((gk) => {
      if (def[gk]) return;
      const g = saved[gk];
      if (!g || typeof g !== 'object') return;
      const items = Object.keys(g).map((k) => ({ k, v: fmtParamValue(g[k]) }));
      if (items.length) {
        groups.push({ key: gk, title: PARAM_GROUP_LABELS[gk] || gk, items });
      }
    });

    return groups;
  },

  onTab(e) {
    const key = String(e.currentTarget.dataset.key || 'positions');
    if (key === this.data.tab) return;
    this.setData({ tab: key });
    this.loadTab(key);
  },

  loadTab(key, withPositions) {
    const usId = this.data.usId;
    this.setData({ detailLoading: true });

    if (key === 'runlog') {
      return api.userStrategy
        .runlog(usId, 0, 100, true)
        .catch(() => [])
        .then((logs) => {
          this.setData({ runlogs: buildRunlogs(logs), detailLoading: false });
        });
    }

    return api.userStrategy
      .latestExecution(usId, true)
      .catch(() => ({}))
      .then((execution) => {
        const positions = buildPositions(execution);
        this.setData({ positions, detailLoading: false });
        if (key === 'trades' || withPositions) return this.loadTrades(positions);
        return null;
      });
  },

  loadTrades(positions) {
    const usId = this.data.usId;
    const codes = (positions || []).map((p) => p.code).filter(Boolean);
    if (!codes.length) {
      this.setData({ trades: [] });
      return Promise.resolve();
    }
    return api.order
      .latest(usId, codes, '', '', true)
      .then((orders) => {
        const list = orders || [];
        return api.fetchStockNameMap(list.map((o) => String(o.stock_code || ''))).then((names) => {
          this.setData({
            trades: list.map((o, i) => {
              const code = String(o.stock_code || '');
              const price = fmt.sf(o.trade_price);
              const qty = fmt.sf(o.trade_quantity);
              return {
                key: `${o.create_time || ''}|${code}|${i}`,
                date: fmt.shortTime(o.create_time, 16),
                code,
                name: names[code] || '',
                action: String(o.action || '-'),
                actionTone: String(o.action) === '买入' ? 'up' : 'down',
                quantity: fmt.money(o.entrust_quantity),
                price: fmt.price(price),
                amount: fmt.money(price * qty)
              };
            })
          });
        });
      })
      .catch(() => this.setData({ trades: [] }));
  },

  /* ==================================================== 交互 */

  goEdit() {
    ui.navigate(`/pages/strategy-form/strategy-form?mode=edit&id=${encodeURIComponent(this.data.usId)}`);
  },

  goPool() {
    const pool = this.data.head && this.data.head.poolName;
    if (!pool || pool === '未关联') return;
    ui.navigate(`/pages/pool-quotes/pool-quotes?name=${encodeURIComponent(pool)}`);
  },

  goStock(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    ui.navigate(`/pages/stock-detail/stock-detail?code=${encodeURIComponent(code)}`);
  },

  retry() {
    this.load();
  }
});

/** 参数值展示：数字保留原样，字符串直出 */
function fmtParamValue(v) {
  if (v === null || v === undefined) return '--';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number') return String(v);
  const s = String(v);
  return s || '--';
}

function buildPositions(execution) {
  const list = (execution && execution.positions) || [];
  return list.map((p) => {
    const qty = fmt.sf(p.quantity);
    const cost = fmt.sf(p.cost_price);
    const cur = fmt.sf(p.current_price);
    const profit =
      p.profit_amount !== undefined && p.profit_amount !== null
        ? fmt.sf(p.profit_amount)
        : (cur - cost) * qty;
    return {
      code: String(p.code || ''),
      name: String(p.name || ''),
      quantity: fmt.money(qty),
      cost: fmt.price(cost),
      current: fmt.price(cur),
      profitText: fmt.signed(profit, 0),
      profitTone: fmt.toneOf(profit)
    };
  });
}

function buildRunlogs(logs) {
  return (logs || []).map((it, i) => {
    if (typeof it === 'string') return { key: `s${i}`, text: it };
    const t = String(it.create_time || it.time || '');
    const level = String(it.level || 'INFO');
    const msg = String(it.message || it.content || it.msg || '');
    return { key: `${t}|${i}`, text: `${t} [${level}] ${msg}`.trim() };
  });
}

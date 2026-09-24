/**
 * 策略选股 —— 查看各策略当日选出的股票
 *
 * 对齐 Web 端 pages/strategy_select.py（《页面操作文档》5.4）：
 *   - 策略标签栏：每个用户策略一个标签，展示自定义名称（缺失回退模板名）
 *   - 数据源：/api/strategy_select_stocks/list 直接返回带实时行情的
 *     StrategySelectStockWithRtItem（含 name/price/change_percent/
 *     change_amount/turnover/score），只有 ttm_pe 与 net_profit_growth_rate
 *     需要用 /api/finance/* 补两次。
 *   - 后端已按 score 正向排序并去重，前端**不再重排**（重排会让价格在刷新间抖动）。
 *   - 切换标签强制重新拉取（不读缓存），保证行情最新。
 *
 * ⚠️ 两个缩放陷阱（与 Web 源码逐行核对过）：
 *   change_percent / turnover 是小数比率 → ×100；
 *   net_profit_growth_rate 已是百分数 → 不乘。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    today: '',
    tabs: [],
    activeId: '',

    loading: true,
    loadError: '',
    rows: []
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
    this.setData({ today: fmt.today() });
    this.loadTabs();
  },

  onShow() {
    theme.applyTo(this);
  },

  onPullDownRefresh() {
    this.refresh().then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 标签 */

  loadTabs() {
    return Promise.all([
      api.userStrategy.ensureLoaded(true),
      api.strategy.ensureLoaded(false).catch(() => ({}))
    ]).then(([list]) => {
      const tabs = (list || []).map((us) => {
        const sid = String(us.strategy_id || '');
        const tpl = store.getStrategyTemplate(sid) || {};
        return {
          key: String(us._id || us.id || ''),
          label: String(us.name || tpl.name || sid || '未命名策略')
        };
      });
      this.setData({
        tabs,
        activeId: tabs.length ? tabs[0].key : ''
      });
      if (tabs.length) return this.loadQuotes(tabs[0].key);
      this.setData({ loading: false, rows: [] });
      return null;
    });
  },

  onTabTap(e) {
    const id = String(e.currentTarget.dataset.id || '');
    if (!id || id === this.data.activeId) return;
    this.setData({ activeId: id });
    this.loadQuotes(id);
  },

  /* ==================================================== 数据 */

  loadQuotes(usId) {
    this.setData({ loading: true, loadError: '', rows: [] });
    // ⚠️ start_time / end_time 必须传 YYYY-MM-DD，带时分秒会查不到数据
    const day = fmt.today();
    return api.selectStock
      .list(usId, day, day, true)
      .then((list) => this.enrich(list || []))
      .then((rows) => {
        if (this.data.activeId !== usId) return;
        this.setData({ rows, loading: false });
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '选股结果加载失败，请稍后重试' });
      });
  },

  /** 补估值 / 利润（其余字段 list 接口已带） */
  enrich(list) {
    if (!list.length) return Promise.resolve([]);
    const codes = [];
    const seen = {};
    list.forEach((item) => {
      const c = String(item.code || '');
      if (c && !seen[c]) {
        seen[c] = 1;
        codes.push(c);
      }
    });
    const codesStr = codes.join(',');
    return Promise.all([
      api.finance.valuation(codesStr, true).catch(() => ({})),
      api.finance.profit(codesStr, true).catch(() => ({}))
    ]).then(([valMap, profitMap]) => {
      return list.map((q) => {
        const code = String(q.code || '');
        const val = valMap[code];
        if (val && !q.ttm_pe) q.ttm_pe = val.ttm_pe;
        const pft = profitMap[code];
        if (pft && !q.net_profit_growth_rate) {
          q.net_profit_growth_rate = pft.net_profit_growth_rate;
        }
        return this.toVm(q);
      });
    });
  },

  toVm(q) {
    const pct = fmt.sf(q.change_pct !== undefined ? q.change_pct : q.change_percent) * 100;
    const price = fmt.sf(q.price !== undefined ? q.price : q.close);
    const amt = q.change_amt !== undefined && q.change_amt !== null
      ? fmt.sf(q.change_amt)
      : (price * pct) / 100;
    const turnover = fmt.sf(q.turnover !== undefined ? q.turnover : q.turnover_rate) * 100;
    const pe = q.ttm_pe;
    const growth = fmt.sf(q.net_profit_growth_rate);
    const tone = fmt.toneOf(pct);

    return {
      code: String(q.code || ''),
      name: String(q.name || ''),
      score: fmt.sf(q.score).toFixed(2),
      price: fmt.priceGroup(price),
      priceTone: tone,
      pctText: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
      amtText: `${amt >= 0 ? '+' : ''}${fmt.priceGroup(amt)}`,
      turnoverText: fmt.pctPlain(turnover, 2),
      peText: pe === null || pe === undefined || pe === '' ? '--' : fmt.fmtPe(pe),
      growthText: `${growth.toFixed(1)}%`,
      tone
    };
  },

  refresh() {
    return this.loadQuotes(this.data.activeId);
  },

  goStock(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    ui.navigate(`/pages/stock-detail/stock-detail?code=${encodeURIComponent(code)}`);
  },

  retry() {
    this.loadQuotes(this.data.activeId);
  }
});

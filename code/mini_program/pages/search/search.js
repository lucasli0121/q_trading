/**
 * 搜索与筛选
 *
 * 两个标签页，覆盖 Web 端两处入口：
 *   「按代码 / 名称」—— 对齐 pages/stock_quotes.py::_search_stocks：
 *     先用 /api/stock_info/list 全量匹配 code / name / pinyin，取前 30 个代码，
 *     再取实时行情并补估值与利润字段。
 *   「按财务条件」—— 对齐 pages/stock_pools.py 的自动筛选条件区，
 *     走 /api/screener/search：TTM 市盈率、总市值（亿元）、利润率。
 *
 * ⚠️ margin（利润率）入参是**小数比率**：0.15 表示 15%。页面上按 % 输入，
 *    提交前 ÷100；返回后 ×100 展示。
 */

const api = require('../../utils/api');
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

    tab: 'quote', // quote | screen

    // --- 搜索 ---
    keyword: '',
    searching: false,
    searched: false,
    quoteRows: [],

    // --- 条件筛选 ---
    capMin: '',
    capMax: '',
    peMin: '',
    peMax: '',
    marginMin: '',
    marginMax: '',
    screening: false,
    screened: false,
    screenRows: []
  },

  onLoad(options) {
    theme.applyTo(this);
    if (!auth.guard()) return;
    if (options && options.scope === 'screen') this.setData({ tab: 'screen' });
  },

  onShow() {
    theme.applyTo(this);
  },

  onTab(e) {
    this.setData({ tab: String(e.currentTarget.dataset.key || 'quote') });
  },

  /* ==================================================== 按代码 / 名称 */

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
  },

  clearKeyword() {
    this.setData({ keyword: '', quoteRows: [], searched: false });
  },

  doSearch() {
    const kw = String(this.data.keyword || '').trim();
    if (!kw) {
      ui.toast('请输入股票代码或名称');
      return;
    }
    this.setData({ searching: true, searched: true });
    api
      .searchStockQuotes(kw, true)
      .then((list) => {
        this.setData({
          quoteRows: (list || []).map((q) => this.toQuoteVm(q)),
          searching: false
        });
      })
      .catch(() => {
        this.setData({ searching: false, quoteRows: [] });
        ui.error('搜索失败，请稍后重试');
      });
  },

  toQuoteVm(q) {
    const code = String(q.code || '');
    const pct = fmt.sf(
      q.change_pct !== undefined ? q.change_pct : q.change_percent
    ) * 100;
    const price = fmt.sf(q.price !== undefined ? q.price : q.close);
    const amt = q.change_amt !== undefined && q.change_amt !== null
      ? fmt.sf(q.change_amt)
      : (price * pct) / 100;
    const turnover = fmt.sf(q.turnover !== undefined ? q.turnover : q.turnover_rate) * 100;
    const growth = fmt.sf(q.net_profit_growth_rate);
    const tone = fmt.toneOf(pct);

    return {
      code,
      name: String(q.name || ''),
      price: fmt.priceGroup(price),
      priceTone: tone,
      pctText: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
      amtText: `${amt >= 0 ? '+' : ''}${fmt.priceGroup(amt)}`,
      turnoverText: fmt.pctPlain(turnover, 2),
      peText: q.ttm_pe === null || q.ttm_pe === undefined ? '--' : fmt.fmtPe(q.ttm_pe),
      growthText: `${growth.toFixed(1)}%`,
      tone
    };
  },

  /* ==================================================== 条件筛选 */

  onField(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key) return;
    this.setData({ [key]: e.detail.value });
  },

  resetScreen() {
    this.setData({
      capMin: '',
      capMax: '',
      peMin: '',
      peMax: '',
      marginMin: '',
      marginMax: '',
      screenRows: [],
      screened: false
    });
  },

  doScreen() {
    const num = (v) => {
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : 0;
    };
    const capMin = num(this.data.capMin);
    const capMax = num(this.data.capMax);
    const peMin = num(this.data.peMin);
    const peMax = num(this.data.peMax);
    // 界面按 % 输入 → 后端要小数比率
    const marginMin = num(this.data.marginMin) / 100;
    const marginMax = num(this.data.marginMax) / 100;

    if (!capMin && !capMax && !peMin && !peMax && !marginMin && !marginMax) {
      ui.toast('请至少填写一个筛选条件');
      return;
    }
    if (capMin && capMax && capMin > capMax) {
      ui.toast('市值下限不能大于上限');
      return;
    }
    if (peMin && peMax && peMin > peMax) {
      ui.toast('PE 下限不能大于上限');
      return;
    }

    this.setData({ screening: true, screened: true });
    api.screener
      .search({ ttmMin: peMin, ttmMax: peMax, capMin, capMax, marginMin, marginMax, silent: true })
      .then((list) => {
        this.setData({
          screenRows: (list || []).map((r) => ({
            code: String(r.code || ''),
            name: String(r.name || ''),
            peText: r.ttm_pe === null || r.ttm_pe === undefined ? '--' : fmt.fmtPe(r.ttm_pe),
            capText: fmt.fmtCapYi(r.total_market_cap),
            // 后端返回的利润率是小数比率，×100 展示
            marginText: fmt.pctPlain(fmt.sf(r.profit_margin) * 100, 2),
            reportDate: fmt.dateOnly(r.report_date) || '--'
          })),
          screening: false
        });
      })
      .catch(() => {
        this.setData({ screening: false, screenRows: [] });
        ui.error('筛选失败，请稍后重试');
      });
  },

  /* ==================================================== 跳转 */

  goStock(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    ui.navigate(`/pages/stock-detail/stock-detail?code=${encodeURIComponent(code)}`);
  }
});

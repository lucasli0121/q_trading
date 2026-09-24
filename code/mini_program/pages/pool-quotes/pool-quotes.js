/**
 * 股票池行情列表 —— 设计稿第 7 页
 *
 * 对齐 Web 端 pages/stock_quotes.py 的行情表格：
 *   代码/名称 · 最新价 · 涨跌幅 · 涨跌额 · 换手率 · 动态市盈率 · 利润增长率 · 分析
 * Web 端的「个股分析弹窗」在小程序里改为独立页面（stock-detail），
 * 内容一致：实时行情 + 价格走势（日K/分时）+ 买卖信号列表。
 *
 * 数值口径（务必与 Web 一致）：
 *   change_pct / change_amt / turnover 后端返回小数比率，展示前 ×100；
 *   net_profit_growth_rate 已是百分数，不再乘。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const fmt = require('../../utils/format');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const config = require('../../config/index');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    poolName: '',
    loading: true,
    loadError: '',
    quotes: [],
    empty: false,
    updatedAt: ''
  },

  onLoad(query) {
    theme.applyTo(this);
    const name = decodeURIComponent(query.name || '');
    this.setData({ poolName: name });
    if (!auth.guard()) return;
    this.load(true);
    this._timer = setInterval(() => {
      if (store.isLoggedIn()) this.load(false);
    }, config.POLL.quotes);
  },

  onShow() {
    theme.applyTo(this);
  },

  onUnload() {
    if (this._timer) clearInterval(this._timer);
  },

  onPullDownRefresh() {
    this.load(true).then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  load(showLoading) {
    if (showLoading) this.setData({ loading: true, loadError: '' });
    return api
      .fetchPoolQuotes(this.data.poolName, true)
      .then((rows) => {
        this.setData({
          quotes: this.toViewModels(rows || []),
          empty: !(rows || []).length,
          loading: false,
          updatedAt: fmt.shortTime(new Date().toISOString().replace('T', ' ').slice(0, 19), 16)
        });
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请检查网络后重试' });
      });
  },

  /** 行情行 → 视图模型（所有格式化集中在这里，保证三处列表口径一致） */
  toViewModels(rows) {
    return rows.map((q) => {
      const code = String(q.code || '');
      const name = String(q.name || '');
      const price = fmt.sf(q.price !== undefined ? q.price : q.close);
      const pctRatio = fmt.sf(q.change_pct !== undefined ? q.change_pct : q.change_percent);
      const amt = fmt.sf(q.change_amt !== undefined ? q.change_amt : q.change_amount);
      const turnoverRatio = fmt.sf(q.turnover !== undefined ? q.turnover : q.turnover_rate);
      const tone = fmt.toneOf(pctRatio);
      const cls = tone === 'up' ? 'c-up' : tone === 'down' ? 'c-down' : 'c-second';
      return {
        code,
        name,
        price: fmt.price(price),
        priceClass: cls,
        pct: fmt.fmtPctRatio(pctRatio, 2),
        pctClass: cls,
        amt: fmt.fmtChange(amt, 2),
        amtClass: cls,
        turnover: fmt.pctPlain(turnoverRatio * 100, 2),
        pe: fmt.fmtPe(q.ttm_pe),
        growth: fmt.fmtPctRaw(q.net_profit_growth_rate, 1)
      };
    });
  },

  /* ==================================================== 交互 */

  onRowTap(e) {
    const vm = e.detail.vm;
    this.goDetail(vm);
  },

  onAnalyze(e) {
    this.goDetail(e.detail.vm);
  },

  goDetail(vm) {
    if (!vm || !vm.code) return;
    ui.navigate(
      `/pages/stock-detail/stock-detail?code=${encodeURIComponent(vm.code)}&name=${encodeURIComponent(vm.name || '')}`
    );
  },

  goSearch() {
    ui.navigate(`/pages/search/search?scope=quote&pool=${encodeURIComponent(this.data.poolName)}`);
  },

  refresh() {
    this.load(true);
  },

  retry() {
    this.load(true);
  }
});

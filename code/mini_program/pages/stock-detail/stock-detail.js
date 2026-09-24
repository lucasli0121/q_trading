/**
 * 个股详情（个股分析）
 *
 * 对齐 Web 端 pages/stock_quotes.py 的「个股分析」弹窗（《页面操作文档》5.6），
 * 小程序里做成独立页面（弹窗在手机上体验差）。三段内容与 Web 一一对应：
 *
 *   ① 实时行情：大字最新价 / 涨跌幅 / 涨跌额 + 今开 / 最高 / 最低 / 昨收 / 成交量 / 成交额
 *   ② 价格走势（近 15 天）：日K 蜡烛图 ⇄ 分时折线，可切换；分时可选具体交易日
 *   ③ 买卖信号（近 15 天，共 N 条）：时间 / 成交价 / 收益（仅卖出方向展示）/ 原因
 *
 * 取数口径（对齐 Web 源码）：
 *   日K   GET /api/market/kline/day   start=今天-15天, end=今天
 *   分时  GET /api/market/kline/minute start="日期 09:30:00", end="日期 15:00:00"
 *         ⚠️ 分时接口的参数名是 start/end，不是 start_time/end_time
 *   行情  GET /api/market/real_time?code=xxx，取返回列表的最后一条（Web 同源）
 *   信号  GET /api/trade_signal/list?stock_codes=xxx&start_time=... 00:00:00
 */

const api = require('../../utils/api');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');
const config = require('../../config/index');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    code: '',
    name: '',
    mode: 'light', // 供 q-chart 的 theme 属性

    loading: true,
    loadError: '',

    rt: null,
    kline: [],
    signals: [],     // 供列表展示的视图模型
    rawSignals: [],  // 供 q-chart 标注用的原始信号（含 create_time / trade_price）

    chartMode: 'day', // day | minute
    dateOptions: [],
    dateIndex: -1,
    minuteDate: '',
    minuteData: [],
    minuteLoading: false,
    minuteError: '',

    signalCount: 0
  },

  onLoad(options) {
    theme.applyTo(this);
    if (!auth.guard()) return;
    const code = String((options && options.code) || '');
    if (!code) {
      this.setData({ loading: false, loadError: '缺少股票代码' });
      return;
    }
    this.setData({ code, name: String((options && options.name) || ''), mode: theme.current() });
    this.load();
  },

  onShow() {
    theme.applyTo(this);
    this.setData({ mode: theme.current() });
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  load() {
    const code = this.data.code;
    const end = fmt.today();
    const start = fmt.daysAgo(config.ANALYSIS_DAYS);
    this.setData({ loading: true, loadError: '' });

    return Promise.all([
      api.market.realTime({ code, silent: true }).catch(() => []),
      api.market.dayKline(code, start, end, true).catch(() => []),
      api.signal
        .list({
          stockCodes: [code],
          startTime: `${start} 00:00:00`,
          endTime: `${end} 23:59:59`,
          limit: config.MAX_LIMIT,
          silent: true
        })
        .catch(() => [])
    ])
      .then(([rtList, kline, signals]) => {
        const rt = (rtList && rtList.length ? rtList[rtList.length - 1] : null) || null;
        const sigs = (signals || []).slice().sort((a, b) =>
          String(b.create_time || '').localeCompare(String(a.create_time || ''))
        );
        const dates = [];
        (kline || []).forEach((k) => {
          const d = fmt.recordDate(k);
          if (d && dates.indexOf(d) < 0) dates.push(d);
        });
        dates.sort();

        const nameFromRt = rt && rt.name ? String(rt.name) : '';
        const nextName = this.data.name || nameFromRt;

        this.setData({
          loading: false,
          rt: this.toRtVm(rt),
          kline: (kline || []).map((k) => ({
            date: fmt.recordDate(k),
            open: fmt.sf(k.open),
            close: fmt.sf(k.close !== undefined ? k.close : k.price),
            low: fmt.sf(k.low),
            high: fmt.sf(k.high),
            volume: fmt.sf(k.volume)
          })),
          signals: this.toSignalVms(sigs),
          // 图表标注必须用原始信号：q-chart 要读 create_time 与 trade_price，
          // 视图模型里这两个字段已被格式化掉了
          rawSignals: sigs,
          signalCount: sigs.length,
          dateOptions: dates,
          dateIndex: dates.length ? dates.length - 1 : -1,
          minuteDate: dates.length ? dates[dates.length - 1] : ''
        });

        // 标题补名称
        if (nextName && nextName !== this.data.name) {
          this.setData({ name: nextName });
        }
        return null;
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请稍后重试' });
      });
  },

  toRtVm(rt) {
    if (!rt) return null;
    const price = fmt.sf(rt.price !== undefined ? rt.price : rt.close);
    // change_percent 是小数比率 → ×100
    const pct = fmt.sf(rt.change_percent !== undefined ? rt.change_percent : rt.change_pct) * 100;
    const amt = rt.change_amount !== undefined && rt.change_amount !== null
      ? fmt.sf(rt.change_amount)
      : (price * pct) / 100;
    const tone = fmt.toneOf(pct);
    return {
      price: fmt.priceGroup(price),
      pctText: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
      amtText: `${amt >= 0 ? '+' : ''}${fmt.priceGroup(amt)}`,
      tone,
      fields: [
        { k: '今开', v: fmt.priceGroup(fmt.sf(rt.open)) },
        { k: '最高', v: fmt.priceGroup(fmt.sf(rt.high)) },
        { k: '最低', v: fmt.priceGroup(fmt.sf(rt.low)) },
        { k: '昨收', v: fmt.priceGroup(fmt.sf(rt.preclose)) },
        { k: '成交量', v: fmt.money(rt.volume) },
        { k: '成交额', v: fmt.money(rt.amount) }
      ]
    };
  },

  toSignalVms(list) {
    return (list || []).map((s, i) => {
      const action = String(s.action || '');
      const isBuy = fmt.isBuyAction(action);
      const rate = fmt.sf(s.profit_rate);
      return {
        key: `${s.id || ''}|${s.create_time || ''}|${i}`,
        time: fmt.shortTime(s.create_time, 16),
        action: action || '-',
        actionTone: isBuy ? 'up' : 'down',
        price: fmt.priceGroup(fmt.sf(s.trade_price)),
        // Web 只在卖出方向展示收益
        showRate: !isBuy && !!rate,
        rateText: `${rate >= 0 ? '+' : ''}${rate.toFixed(2)}%`,
        rateTone: fmt.toneOf(rate),
        reason: String(s.reason || '')
      };
    });
  },

  /* ==================================================== 图表 */

  onChartMode(e) {
    const m = String(e.currentTarget.dataset.mode || 'day');
    if (m === this.data.chartMode) return;
    this.setData({ chartMode: m });
    if (m === 'minute' && this.data.minuteDate) this.loadMinute(this.data.minuteDate);
  },

  onDateChange(e) {
    const i = Number(e.detail.value);
    const d = this.data.dateOptions[i];
    if (!d) return;
    this.setData({ dateIndex: i, minuteDate: d });
    this.loadMinute(d);
  },

  loadMinute(dateStr) {
    if (!dateStr) return Promise.resolve();
    this.setData({ minuteLoading: true, minuteError: '' });
    return api.market
      .minuteKline(this.data.code, `${dateStr} 09:30:00`, `${dateStr} 15:00:00`, true)
      .then((list) => {
        const data = list || [];
        this.setData({
          minuteData: data,
          minuteLoading: false,
          minuteError: data.length ? '' : '暂无分时数据'
        });
      })
      .catch(() => {
        this.setData({ minuteLoading: false, minuteData: [], minuteError: '分时数据加载失败' });
      });
  },

  /* ==================================================== 操作 */

  goPool() {
    ui.navigate('/pages/quotes/quotes');
  },

  copyCode() {
    ui.copy(this.data.code, '代码已复制');
  },

  retry() {
    this.load();
  }
});

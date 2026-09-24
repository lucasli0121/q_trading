/**
 * 策略监控 —— 实时交易信号流
 *
 * 对齐 Web 端 pages/strategy_monitor.py（《页面操作文档》5.3）：
 *   - 4 张统计卡（全部 / 买入 / 卖出 / 今日），全部取自本地信号窗口，不产生后端请求
 *   - 筛选标签：全部 / 买入 / 卖出，切换后用已拉取的数据立即重渲染
 *   - 列表按时间倒序，列：时间 / 策略 / 股票代码 / 信号类型 / 成交价格 / 盈亏率 / 盈亏额 / 原因
 *   - 仅保留 12 小时内的信号（SIGNAL_RETENTION_HOURS）
 *
 * 与 Web 的差异：Web 是「后端订阅 MQTT → 内存 → 前端每秒读内存」，小程序读的是
 * utils/ws.js 维护的同一份滚动窗口（WS 优先、HTTP 轮询兜底），
 * 因此刷新周期取 config.POLL.monitor（默认 3 秒，弱网下比 1 秒更省电）。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const ws = require('../../utils/ws');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');
const config = require('../../config/index');
const { SIGNAL_FILTERS } = require('../../utils/constants');

const HOUR_MS = 3600 * 1000;

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    filters: SIGNAL_FILTERS,
    filter: '全部',
    retentionHours: config.SIGNAL_RETENTION_HOURS,

    liveText: '连接中...',
    liveOk: false,

    loading: true,
    rows: [],
    statAll: 0,
    statBuy: 0,
    statSell: 0,
    statToday: 0
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;

    // 先把信号窗口灌满（首屏不等 WS）
    Promise.all([
      api.userStrategy.ensureLoaded(true).catch(() => []),
      api.signal.recent(true).catch(() => [])
    ]).then(([, signals]) => {
      store.mergeSignals(signals || []);
      this.setData({ loading: false });
      this.render();
    });

    this.openChannel();
    this.startTick();
  },

  onShow() {
    theme.applyTo(this);
  },

  onUnload() {
    this.closeChannel();
    this.stopTick();
  },

  onHide() {
    this.stopTick();
  },

  /* ==================================================== 信号通道 */

  openChannel() {
    this.stopChannel = ws.start({
      pollInterval: config.POLL.monitor,
      onSignals: () => this.render(),
      onStatus: (info) => {
        this.setData({
          liveOk: !!info.connected,
          liveText: info.connected ? '实时更新中' : '轮询更新中'
        });
      }
    });
  },

  closeChannel() {
    if (this.stopChannel) {
      this.stopChannel();
      this.stopChannel = null;
    }
  },

  /** 定时从窗口重新渲染（WS 无消息时也保证「今日信号」等派生统计跟得上时间） */
  startTick() {
    this.stopTick();
    this._tick = setInterval(() => this.render(), config.POLL.monitor);
  },

  stopTick() {
    if (this._tick) {
      clearInterval(this._tick);
      this._tick = null;
    }
  },

  /* ==================================================== 渲染 */

  render() {
    const cutoff = Date.now() - config.SIGNAL_RETENTION_HOURS * HOUR_MS;
    const today = fmt.today();

    // 策略 id → 名称
    // ⚠️ 用 user_strategy_id 匹配，不能用 strategy_id：
    //    TradeSignalItem.strategy_id 是策略**模板** ID，user_strategy_id 才是
    //    用户策略（UserStrategy._id），列表里的策略名挂在前者上会全部落到「-」。
    const nameMap = {};
    store.getUserStrategies().forEach((us) => {
      nameMap[String(us._id || us.id || '')] = String(us.name || us.strategy_id || '');
    });

    const alive = store
      .getSignals()
      .filter((s) => {
        const t = parseTime(s.create_time);
        return !t || t >= cutoff;
      })
      .sort((a, b) => String(b.create_time || '').localeCompare(String(a.create_time || '')));

    let buy = 0;
    let sell = 0;
    let todayCount = 0;
    alive.forEach((s) => {
      const action = String(s.action || '');
      if (action === '买入') buy += 1;
      else if (action === '卖出') sell += 1;
      if (String(s.create_time || '').slice(0, 10) === today) todayCount += 1;
    });

    const filter = this.data.filter;
    const rows = alive
      .filter((s) => filter === '全部' || String(s.action || '') === filter)
      .slice(0, 200)
      .map((s, i) => {
        const action = String(s.action || '');
        const rate = s.profit_rate;
        const amount = s.profit_amount;
        const usId = String(s.user_strategy_id || '');
        return {
          key: `${s.id || ''}|${s.create_time || ''}|${s.stock_code || ''}|${i}`,
          time: fmt.shortTime(s.create_time, 19),
          strategyId: usId,
          strategyName: nameMap[usId] || usId || '-',
          code: String(s.stock_code || ''),
          action: action || '-',
          actionTone: action === '买入' ? 'up' : action === '卖出' ? 'down' : 'flat',
          price: s.trade_price === null || s.trade_price === undefined || s.trade_price === ''
            ? '--'
            : fmt.price(s.trade_price),
          // profit_rate 后端已是百分数，不再 ×100
          rateText: fmt.fmtPctRaw(rate === null || rate === undefined ? 0 : rate, 2),
          rateTone: fmt.toneOf(fmt.sf(rate)),
          amountText: fmt.signed(amount || 0, 2),
          amountTone: fmt.toneOf(fmt.sf(amount)),
          reason: String(s.reason || '') || '-'
        };
      });

    this.setData({
      rows,
      statAll: alive.length,
      statBuy: buy,
      statSell: sell,
      statToday: todayCount,
      loading: false
    });
  },

  onFilter(e) {
    this.setData({ filter: String(e.currentTarget.dataset.key || '全部') });
    this.render();
  },

  goStrategy(e) {
    const id = String(e.currentTarget.dataset.sid || '');
    if (!id) return;
    ui.navigate(`/pages/strategy-detail/strategy-detail?id=${encodeURIComponent(id)}`);
  },

  goStock(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    ui.navigate(`/pages/stock-detail/stock-detail?code=${encodeURIComponent(code)}`);
  },

  retry() {
    this.setData({ loading: true });
    api.signal
      .recent(true)
      .then((list) => {
        store.mergeSignals(list || []);
        this.render();
      })
      .catch(() => {
        this.setData({ loading: false });
        ui.error('信号加载失败');
      });
  }
});

/** 'YYYY-MM-DD HH:MM:SS' → 毫秒（失败返回 0） */
function parseTime(v) {
  const s = String(v || '');
  if (s.length < 19) return 0;
  const t = Date.parse(s.replace(' ', 'T'));
  return Number.isFinite(t) ? t : 0;
}

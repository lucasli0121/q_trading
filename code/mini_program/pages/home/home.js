/**
 * 首页 · 仪表盘 —— 对齐 Web 端 pages/main_page.py 的首页实现
 *
 * 数据来源与刷新周期（对齐《页面操作文档》第 6 节）：
 *   统计卡片（策略总数 / 运行中 / 股票池数量）：进入页面时拉取
 *   今日信号数：信号通道（WS 优先，降级 HTTP 轮询 10s）
 *   系统累计收益概览：/api/user_strategy/executions，60s 刷新
 *   近期运行状态：最近 4 条信号（仅运行中的策略）
 *   近期更新策略：最多 5 行，策略模板补充类型，执行结果补充收益率
 *   通知角标：/api/system_message/user_messages，10s 检查
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const fmt = require('../../utils/format');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const ws = require('../../utils/ws');
const config = require('../../config/index');
const { PROFIT_PERIODS } = require('../../utils/constants');

const ACTIVITY_COUNT = 4;
const RECENT_TABLE_ROWS = 5;

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    loading: true,
    loadError: '',

    // 统计卡片
    stats: [],
    poolCount: 0,

    // 累计收益
    periods: PROFIT_PERIODS,
    period: '30',
    profitChart: { labels: [], values: [] },
    profitEmpty: false,
    chartHeight: 380,

    // 近期运行状态
    activities: [],
    activityEmpty: true,

    // 近期更新策略
    strategies: [],
    strategyEmpty: false,

    // 通知：未读数与面板状态（is_read 由后端维护，见 applyMessages）
    unreadCount: 0,
    unreadText: '0',
    msgPanelVisible: false,
    msgRows: [],
    msgEmpty: false,
    signalMode: 'idle', // 信号通道状态：ws / poll / idle
    systemRunning: true
  },

  onLoad() {
    theme.applyTo(this);
    this._timers = [];
    if (!auth.guard()) return;
    this.loadAll(true);
    this.startTimers();
  },

  onShow() {
    theme.applyTo(this);
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 0 });
    }
    if (!store.isLoggedIn()) return;
    this.startSignalChannel();
  },

  onHide() {
    this.stopTimers();
    this.stopSignalChannel();
  },

  onUnload() {
    this.stopTimers();
    this.stopSignalChannel();
  },

  onPullDownRefresh() {
    this.loadAll(true).then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  loadAll(withStats) {
    if (withStats) this.setData({ loading: true, loadError: '' });

    return Promise.all([
      api.userStrategy.ensureLoaded(true).catch(() => null),
      api.pool.ensureLoaded(true).catch(() => null),
      api.strategy.ensureLoaded(true).catch(() => null)
    ])
      .then(() => Promise.all([this.loadExecutions(), this.loadMessages()]))
      .then(() => {
        if (withStats) this.buildStats();
        this.buildActivities();
        this.setData({ loading: false });
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请检查网络后重试' });
      });
  },

  loadExecutions() {
    return api.userStrategy
      .executions(true)
      .catch(() => [])
      .then((list) => {
        this._executions = list || [];
        this.buildProfitChart();
        this.buildStrategyTable();
      });
  },

  loadMessages() {
    return api.systemMessage
      .userMessages(true)
      .catch(() => [])
      .then((list) => {
        this._messages = list || [];
        this.applyMessages();
      });
  },

  /**
   * 按服务端返回的 is_read 刷新角标与面板。
   * 已读状态由后端维护（system_message_tbl.read_user_ids），本地不再记「见过的消息 ID」，
   * 否则退出重进 / 换设备都会把同一批消息重新算成未读。
   */
  applyMessages() {
    const msgs = this._messages || [];
    const unread = msgs.filter((m) => !(m && m.is_read));
    const patch = {
      unreadCount: unread.length,
      unreadText: unread.length > 99 ? '99+' : String(unread.length)
    };
    // 面板开着时同步重绘，避免出现「角标已清、列表还标着未读」的不一致
    if (this.data.msgPanelVisible) patch.msgRows = this.buildMsgRows();
    this.setData(patch);
  },

  /**
   * 把消息列表摊平成面板行：分区标题行 + 消息行。
   * 摊平而不是嵌套 wx:for，是为了让 wx:key 落在每一行上，避免列表复用错位。
   * @returns {Array} [{key, type:'section'|'item', ...}]
   */
  buildMsgRows() {
    const msgs = this._messages || [];
    const unread = msgs.filter((m) => !(m && m.is_read));
    const history = msgs.filter((m) => !!(m && m.is_read));
    const rows = [];
    [
      { title: '未读消息', items: unread, read: false },
      { title: '历史消息', items: history, read: true }
    ].forEach((sec) => {
      if (!sec.items.length) return;
      rows.push({ key: `sec-${sec.title}`, type: 'section', title: sec.title, count: sec.items.length });
      sec.items.forEach((m, i) => {
        const id = String((m && m.id) || '');
        rows.push({
          key: `msg-${id}-${i}`,
          type: 'item',
          id,
          read: sec.read,
          title: String((m && m.title) || '系统消息'),
          content: String((m && m.message) || ''),
          time: fmt.shortTime(m && m.create_time, 16)
        });
      });
    });
    return rows;
  },

  /* ==================================================== 统计卡 */

  buildStats() {
    const total = store.countStrategies();
    const running = store.countRunning();
    const monthNew = store.countMonthNew();
    const poolCount = store.getPools().length;
    const todaySignals = this.countTodaySignals();

    const stats = [
      {
        key: 'total',
        icon: '策',
        iconBg: 'rgba(37,99,235,0.16)',
        iconColor: 'var(--color-brand-link)',
        label: '策略总数',
        value: String(total),
        badge: `+${monthNew} 本月`,
        badgeClass: 'badge badge--up'
      },
      {
        key: 'running',
        icon: '▶',
        iconBg: 'var(--color-status-success-bg)',
        iconColor: 'var(--color-market-down)',
        label: '运行中策略',
        value: String(running),
        badge: '运行平稳',
        badgeClass: 'badge'
      },
      {
        key: 'pools',
        icon: '池',
        iconBg: 'rgba(126,34,206,0.14)',
        iconColor: 'var(--color-market-up)',
        label: '股票池数量',
        value: String(poolCount),
        badge: '',
        badgeClass: 'badge'
      },
      {
        key: 'signals',
        icon: '⚡',
        iconBg: 'rgba(194,65,12,0.14)',
        iconColor: 'var(--color-accent-warning)',
        label: '今日信号数',
        value: String(todaySignals),
        badge: todaySignals > 0 ? '今日活跃' : '暂无',
        badgeClass: todaySignals > 0 ? 'badge badge--up' : 'badge'
      }
    ];

    this.setData({ stats, poolCount });
  },

  /** 今日信号数：本地日期前缀匹配（对齐 SignalManager.get_today_count） */
  countTodaySignals() {
    const prefix = fmt.today();
    return store
      .getSignals()
      .filter((s) => String((s && s.create_time) || '').startsWith(prefix)).length;
  },

  /* ==================================================== 累计收益图 */

  buildProfitChart() {
    const period = this.data.period;
    const days = (PROFIT_PERIODS.find((p) => p.key === period) || { days: 30 }).days;

    const points = [];
    (this._executions || []).forEach((ex) => {
      const t = String((ex && (ex.update_time || ex.create_time)) || '').trim();
      if (t.length < 10) return;
      const rate = fmt.sf(ex.current_return_rate) * 100;
      points.push([t.slice(0, 10), rate]);
    });

    if (!points.length) {
      this.setData({ profitChart: { labels: [], values: [] }, profitEmpty: true });
      return;
    }

    points.sort((a, b) => a[0].localeCompare(b[0]));
    const daily = {};
    points.forEach((p) => {
      daily[p[0]] = p[1];
    });
    let days2 = Object.keys(daily).sort();
    let rates = days2.map((d) => daily[d]);

    if (days > 0 && days2.length > 1) {
      const last = new Date(`${days2[days2.length - 1]}T00:00:00`);
      const cutoff = new Date(last.getTime() - days * 86400000);
      const p2 = (n) => String(n).padStart(2, '0');
      const cutoffStr = `${cutoff.getFullYear()}-${p2(cutoff.getMonth() + 1)}-${p2(cutoff.getDate())}`;
      const filtered = days2.filter((d) => d >= cutoffStr);
      if (filtered.length) {
        rates = filtered.map((d) => daily[d]);
        days2 = filtered;
      }
    }

    this.setData({
      profitChart: { labels: days2.map((d) => d.slice(5)), values: rates },
      profitEmpty: false,
      chartHeight: 380
    });
  },

  onPeriodChange(e) {
    this.setData({ period: String(e.detail.key) });
    this.buildProfitChart();
  },

  /* ==================================================== 近期运行状态 */

  buildActivities() {
    const ids = {};
    store.getUserStrategies().forEach((s) => {
      ids[String(s.id || s._id || '')] = String(s.status || '');
    });

    const list = store
      .getSignals()
      .filter((s) => {
        const usid = String((s && s.user_strategy_id) || '');
        // 与 Web 一致：只展示运行中策略的信号
        return usid && ids[usid] === 'running';
      })
      .slice(0, ACTIVITY_COUNT)
      .map((s) => {
        const action = String(s.action || '');
        const tpl = store.getStrategyTemplate(s.strategy_id);
        const name =
          String(s.strategy_name || '') || (tpl && tpl.name) || '--';
        return {
          name,
          action,
          code: String(s.stock_code || ''),
          time: fmt.shortTime(s.create_time, 16),
          toneClass: action === '卖出' ? 'c-down' : 'c-up',
          dotStyle: action === '卖出' ? 'background-color:var(--color-market-down)' : 'background-color:var(--color-market-up)'
        };
      });

    this.setData({ activities: list, activityEmpty: !list.length });
  },

  /* ==================================================== 近期更新策略表 */

  buildStrategyTable() {
    const list = store.getUserStrategies().slice();
    list.sort((a, b) => String(b.create_time || '').localeCompare(String(a.create_time || '')));
    const rows = list.slice(0, RECENT_TABLE_ROWS);

    // 每个用户策略的最新收益率
    const latest = {};
    (this._executions || []).forEach((ex) => {
      const usid = String((ex && ex.user_strategy_id) || '');
      if (!usid) return;
      const t = String(ex.update_time || ex.create_time || '');
      if (!latest[usid] || t >= latest[usid].t) {
        latest[usid] = { t, rate: fmt.sf(ex.current_return_rate) * 100 };
      }
    });

    const vms = rows.map((us) => {
      const usid = String(us.id || us._id || '');
      const tpl = store.getStrategyTemplate(us.strategy_id) || {};
      const rateObj = latest[usid];
      const rateText = rateObj ? fmt.fmtRatePct(rateObj.rate, 1) : '-';
      return {
        id: usid,
        name: String(us.name || tpl.name || us.strategy_id || '未命名策略'),
        type: String(tpl.strategy_type || '-'),
        createDate: String(us.create_time || '').slice(0, 10) || '-',
        status: String(us.status || 'stopped'),
        rateText,
        rateClass: rateObj ? fmt.toneClass(rateObj.rate) : 'c-tertiary'
      };
    });

    this.setData({ strategies: vms, strategyEmpty: !vms.length });
  },

  /* ==================================================== 通知 / 信号 */

  onBell() {
    // 铃铛只负责开面板，不再「一开就算全读」：已读要用户逐条点开
    return this.loadMessages().then(() => {
      const rows = this.buildMsgRows();
      this.setData({
        msgPanelVisible: true,
        msgRows: rows,
        msgEmpty: !rows.length
      });
    });
  },

  closeMsgPanel() {
    this.setData({ msgPanelVisible: false });
  },

  /** 点开单条消息 → 标记已读 → 本地同步读数与分区 */
  onMsgTap(e) {
    const id = String((e && e.currentTarget && e.currentTarget.dataset.id) || '');
    if (!id) return;
    const target = (this._messages || []).find((m) => String((m && m.id) || '') === id);
    if (!target || target.is_read) return; // 已读的不再发请求，也不重复分区
    api.systemMessage
      .markRead(id)
      .then(() => {
        target.is_read = true;
        this.applyMessages();
      })
      .catch(() => ui.error('标记已读失败'));
  },

  startSignalChannel() {
    if (this._unsubscribe) return;
    this._unsubscribe = ws.start({
      pollInterval: config.POLL.homeSignal,
      onSignals: () => {
        this.buildActivities();
        // 只更新今日信号数这一张卡，避免整块重绘
        const today = this.countTodaySignals();
        const stats = this.data.stats.slice();
        const idx = stats.findIndex((s) => s.key === 'signals');
        if (idx >= 0) {
          stats[idx].value = String(today);
          stats[idx].badge = today > 0 ? '今日活跃' : '暂无';
          stats[idx].badgeClass = today > 0 ? 'badge badge--up' : 'badge';
          this.setData({ stats });
        }
      },
      onStatus: (info) => this.setData({ signalMode: info.mode })
    });
  },

  stopSignalChannel() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
  },

  /* ==================================================== 定时器 */

  startTimers() {
    this.stopTimers();
    // 收益图 + 策略表 60s
    this._timers.push(
      setInterval(() => {
        if (store.isLoggedIn()) this.loadExecutions();
      }, config.POLL.homeHeavy)
    );
    // 通知角标 10s
    this._timers.push(
      setInterval(() => {
        if (store.isLoggedIn()) this.loadMessages();
      }, config.POLL.homeSignal)
    );
  },

  stopTimers() {
    (this._timers || []).forEach((t) => clearInterval(t));
    this._timers = [];
  },

  retry() {
    this.loadAll(true);
  },

  /* ==================================================== 跳转 */

  goMonitor() {
    ui.navigate('/pages/monitor/monitor');
  },

  /** 统计卡点击：策略类 → 策略列表；股票池 → 行情 */
  onStatTap(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (key === 'pools') this.goQuotes();
    else if (key === 'total' || key === 'running') this.goStrategies();
  },

  goStrategies() {
    wx.switchTab({ url: '/pages/strategies/strategies' });
  },

  goQuotes() {
    wx.switchTab({ url: '/pages/quotes/quotes' });
  },

  goStrategyDetail(e) {
    const id = String(e.currentTarget.dataset.id || '');
    if (!id) return;
    ui.navigate(`/pages/strategy-detail/strategy-detail?id=${encodeURIComponent(id)}`);
  }
});

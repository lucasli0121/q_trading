/**
 * Tab3 · 策略列表
 *
 * 对齐 Web 端 pages/strategies.py（《页面操作文档》5.2）：
 *   - 筛选：搜索（名称/描述）+ 类型 + 状态 + 重置，任一条件变化回到第 1 页
 *   - 每页 10 条，页脚「显示第 X-Y 条结果，共 N 条」+ 上一页/下一页
 *   - 「总收益」= total_profit / initial_amount × 100，涨红跌绿
 *   - 「最近信号」取信号窗口内该策略最新一条，文案「买入 600519」
 *   - 点行展开详情面板：持仓信息 / 订单明细 / 运行日志 三个标签
 *   - 行尾编辑图标 → 编辑策略（不展开详情）
 *
 * 移动端改造说明：
 *   Web 是 6 列表格 + 行内展开，手机宽度放不下 6 列，因此改为
 *   「卡片 + 内嵌展开」：卡片首行是名称/状态，第二行是类型 + 总收益 + 最近信号。
 *   信息量与 Web 完全一致，只是排布从横向改为纵向。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');
const config = require('../../config/index');
const { statusStyle, STRATEGY_STATUS_OPTIONS } = require('../../utils/constants');

const PAGE_SIZE = config.PAGE_SIZE;

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    loading: true,
    loadError: '',

    keyword: '',
    typeFilter: '全部类型',
    statusFilter: '全部状态',
    typeOptions: ['全部类型'],
    statusOptions: STRATEGY_STATUS_OPTIONS.map((o) => o.value),
    // picker 的 value 是下标，WXML 里不能调 indexOf，因此在 JS 里维护
    typeIndex: 0,
    statusIndex: 0,

    rows: [],      // 全量行
    filtered: [],  // 过滤后
    pageRows: [],  // 当前页
    statTotal: 0,
    statRunning: 0,
    statMonthNew: 0,

    page: 1,
    totalPages: 1,
    rangeText: '',

    // 详情面板
    expandedId: '',
    detailTab: 'positions',
    detailLoading: false,
    positions: [],
    trades: [],
    runlogs: []
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
  },

  onShow() {
    theme.applyTo(this);
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 });
    }
    if (!store.isLoggedIn()) return;
    this.load(true);
  },

  onPullDownRefresh() {
    this.load(true).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.page < this.data.totalPages) {
      this.gotoPage(this.data.page + 1);
    }
  },

  /* ==================================================== 加载 */

  load(force) {
    this.setData({ loading: true, loadError: '' });
    return Promise.all([
      api.userStrategy.ensureLoaded(force),
      api.strategy.ensureLoaded(force).catch(() => ({}))
    ])
      .then(([list]) => {
        return this.ensureSignalWindow().then(() => this.buildRows(list || []));
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请检查网络后重试' });
      });
  },

  /** 信号窗口为空时补一次历史信号（WS 未连上时的首屏兜底） */
  ensureSignalWindow() {
    if (store.getSignals().length) return Promise.resolve();
    return api.signal.recent(true).then((list) => {
      store.mergeSignals(list || []);
    });
  },

  /**
   * 用户策略 × 策略模板 → 行视图模型。
   * 名称回退链与 Web 一致：自定义名称 → 模板名 → sid → 「未命名策略」。
   */
  buildRows(list) {
    const latest = this.latestSignalMap();

    const rows = list.map((us) => {
      const sid = String(us.strategy_id || '');
      const tpl = store.getStrategyTemplate(sid) || {};
      const initAmount = fmt.sf(us.initial_amount);
      const totalProfit = fmt.sf(us.total_profit);
      // 与 Web 同口径：收益率为百分数（已 ×100），直接直出
      const profitPct = initAmount === 0 ? 0 : (totalProfit / initAmount) * 100;
      const st = statusStyle(String(us.status || 'stopped'));
      const sig = latest[String(us._id || us.id || '')] || null;
      return {
        usId: String(us._id || us.id || ''),
        sid,
        name: String(us.name || tpl.name || sid || '未命名策略'),
        customName: String(us.name || ''),
        description: String(tpl.description || ''),
        stype: String(tpl.strategy_type || '-'),
        status: String(us.status || 'stopped'),
        statusText: st.text,
        poolId: String(us.pool_id || ''),
        initialAmount: initAmount,
        initialAmountText: fmt.money(initAmount),
        maxStockCount: Number(us.max_stock_count || 0),
        strategyParams: us.strategy_params || {},
        profitText: `${profitPct.toFixed(2)}%`,
        profitTone: fmt.toneOf(profitPct),
        signalText: sig ? sig.text : '-',
        signalTone: sig ? sig.tone : 'flat'
      };
    });

    rows.sort((a, b) => String(b.usId).localeCompare(String(a.usId)));

    const types = {};
    rows.forEach((r) => {
      if (r.stype && r.stype !== '-') types[r.stype] = 1;
    });

    const typeOptions = ['全部类型'].concat(Object.keys(types));
    const typeIndex = Math.max(0, typeOptions.indexOf(this.data.typeFilter));

    this.setData({
      rows,
      loading: false,
      typeOptions,
      typeIndex,
      statTotal: rows.length,
      statRunning: rows.filter((r) => r.status === 'running').length,
      statMonthNew: store.countMonthNew()
    });
    this.applyFilter(1);
  },

  /**
   * {user_strategy_id: {text, tone}} —— 每个策略只取窗口内最新一条
   *
   * ⚠️ 必须用 `user_strategy_id` 做键，不能用 `strategy_id`：
   *    TradeSignalItem 里 `strategy_id` 是**策略模板 ID**，`user_strategy_id` 才是
   *    用户策略（UserStrategy._id）的 ID —— 只有后者能和列表行对上，
   *    否则多个用户策略共用同一模板时「最近信号」会串行。
   */
  latestSignalMap() {
    const map = {};
    store.getSignals().forEach((s) => {
      const key = String(s.user_strategy_id || '');
      if (!key) return;
      const prev = map[key];
      if (!prev || String(s.create_time || '') > String(prev.time || '')) {
        const action = String(s.action || '');
        map[key] = {
          time: String(s.create_time || ''),
          text: `${action} ${s.stock_code || ''}`.trim() || '-',
          tone: action === '买入' ? 'up' : action === '卖出' ? 'down' : 'flat'
        };
      }
    });
    return map;
  },

  /* ==================================================== 筛选 / 分页 */

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
    this.applyFilter(1);
  },

  clearKeyword() {
    this.setData({ keyword: '' });
    this.applyFilter(1);
  },

  onTypeChange(e) {
    const i = Number(e.detail.value) || 0;
    this.setData({ typeIndex: i, typeFilter: this.data.typeOptions[i] || '全部类型' });
    this.applyFilter(1);
  },

  onStatusChange(e) {
    const i = Number(e.detail.value) || 0;
    this.setData({ statusIndex: i, statusFilter: this.data.statusOptions[i] || '全部状态' });
    this.applyFilter(1);
  },

  resetFilters() {
    this.setData({
      keyword: '',
      typeIndex: 0,
      statusIndex: 0,
      typeFilter: '全部类型',
      statusFilter: '全部状态'
    });
    this.applyFilter(1);
  },

  applyFilter(page) {
    const { keyword, typeFilter, statusFilter } = this.data;
    const kw = String(keyword || '').trim().toLowerCase();

    const filtered = this.data.rows.filter((r) => {
      if (kw && `${r.name}${r.description}`.toLowerCase().indexOf(kw) < 0) return false;
      if (typeFilter !== '全部类型' && r.stype !== typeFilter) return false;
      if (statusFilter !== '全部状态' && r.statusText !== statusFilter) return false;
      return true;
    });

    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    const p = Math.min(Math.max(1, page || this.data.page), totalPages);
    const start = (p - 1) * PAGE_SIZE;
    const pageRows = filtered.slice(start, start + PAGE_SIZE);

    this.setData({
      filtered,
      pageRows,
      page: p,
      totalPages,
      rangeText: filtered.length
        ? `显示第 ${start + 1}-${start + pageRows.length} 条结果，共 ${filtered.length} 条`
        : '共 0 条结果'
    });
  },

  gotoPage(p) {
    this.applyFilter(p);
    this.setData({ expandedId: '' });
  },

  prevPage() {
    if (this.data.page > 1) this.gotoPage(this.data.page - 1);
  },

  nextPage() {
    if (this.data.page < this.data.totalPages) this.gotoPage(this.data.page + 1);
  },

  /* ==================================================== 详情面板 */

  onRowTap(e) {
    const id = String(e.currentTarget.dataset.id || '');
    if (!id) return;
    if (this.data.expandedId === id) {
      this.setData({ expandedId: '' });
      return;
    }
    this.setData({
      expandedId: id,
      detailTab: 'positions',
      positions: [],
      trades: [],
      runlogs: [],
      detailLoading: true
    });
    this.loadDetail(id);
  },

  switchDetailTab(e) {
    const key = String(e.currentTarget.dataset.key || 'positions');
    this.setData({ detailTab: key });
  },

  /** 三个标签的数据一次性拉齐，切换标签不再请求（与 Web「不重新加载整页」一致） */
  loadDetail(usId) {
    return Promise.all([
      api.userStrategy.latestExecution(usId, true).catch(() => ({})),
      api.userStrategy.runlog(usId, 0, 100, true).catch(() => [])
    ]).then(([execution, logs]) => {
      if (this.data.expandedId !== usId) return;
      const positions = this.buildPositions(execution);
      this.setData({
        positions,
        runlogs: this.buildRunlogs(logs),
        detailLoading: false
      });
      return this.loadTrades(usId, positions);
    });
  },

  buildPositions(execution) {
    const list = (execution && execution.positions) || [];
    return list.map((p) => {
      const qty = fmt.sf(p.quantity);
      const cost = fmt.sf(p.cost_price);
      const cur = fmt.sf(p.current_price);
      // 缺 profit_amount 时按 (现价 - 成本) × 数量 补算，与 Web 一致
      const profit = p.profit_amount !== undefined && p.profit_amount !== null
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
  },

  /** 订单明细：以持仓股票为参数取最新订单（Web 同源逻辑） */
  loadTrades(usId, positions) {
    const codes = (positions || []).map((p) => p.code).filter(Boolean);
    if (!codes.length) {
      this.setData({ trades: [] });
      return Promise.resolve();
    }
    return api.order
      .latest(usId, codes, '', '', true)
      .then((orders) => {
        if (this.data.expandedId !== usId) return;
        const list = orders || [];
        return api.fetchStockNameMap(list.map((o) => String(o.stock_code || ''))).then((names) => {
          this.setData({
            trades: list.map((o, i) => {
              const code = String(o.stock_code || '');
              const price = fmt.sf(o.trade_price);
              const qty = fmt.sf(o.trade_quantity);
              return {
                key: `${o.create_time || ''}|${code}|${o.action || ''}|${i}`,
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

  buildRunlogs(logs) {
    return (logs || []).map((it, i) => {
      if (typeof it === 'string') return { key: `s${i}`, text: it };
      const t = String(it.create_time || it.time || '');
      const level = String(it.level || 'INFO');
      const msg = String(it.message || it.content || it.msg || '');
      return { key: `${t}|${i}`, text: `${t} [${level}] ${msg}`.trim() };
    });
  },

  /* ==================================================== 交互 */

  goCreate() {
    ui.navigate('/pages/strategy-form/strategy-form?mode=create');
  },

  goEdit(e) {
    const id = String(e.currentTarget.dataset.id || '');
    if (!id) return;
    ui.navigate(`/pages/strategy-form/strategy-form?mode=edit&id=${encodeURIComponent(id)}`);
  },

  goDetail(e) {
    const id = String(e.currentTarget.dataset.id || '');
    if (!id) return;
    ui.navigate(`/pages/strategy-detail/strategy-detail?id=${encodeURIComponent(id)}`);
  },

  retry() {
    this.load(true);
  }
});

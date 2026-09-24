/**
 * Tab2 · 股票池列表（行情首屏）
 *
 * 对齐 Web 端 pages/stock_pools.py + 设计稿第 6 页：
 *   - 统计栏：总数 / 自动更新 / 静态池
 *   - 搜索（对齐 Web：按名称与描述做前端过滤）
 *   - 股票池卡片：类型徽标 + 名称 + 描述 + 「N 只股票」+ 更新频率 + 查看股票 / 编辑
 *   - 末尾虚线占位卡「创建新股票池」
 *   - 点卡片 → 池内行情列表（pool-quotes）
 *
 * ⚠️ 两个已知边界（与 Web 端同源，已在 doc/代码功能说明.md 记录）：
 *   1. 后端 PoolInfo 没有 type 字段，因此池类型恒为「手动维护」；
 *   2. 后端只返回池内股票的 code，股票数量必须逐池调 /stocks 后取长度（N+1 请求）。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const { poolTypeMeta } = require('../../utils/constants');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    loading: true,
    loadError: '',

    keyword: '',
    pools: [],      // 全量（含 stockCount）
    filtered: [],   // 过滤后

    statTotal: 0,
    statAuto: 0,
    statStatic: 0
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
  },

  onShow() {
    theme.applyTo(this);
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 1 });
    }
    if (!store.isLoggedIn()) return;
    this.load(true);
  },

  onPullDownRefresh() {
    this.load(true).then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  load(force) {
    this.setData({ loading: true, loadError: '' });
    return api.pool
      .ensureLoaded(force)
      .then((pools) => this.attachStockCounts(pools || []))
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请检查网络后重试' });
      });
  },

  /**
   * 逐池补齐「股票数量」并生成展示用视图模型。
   * 后端 /api/pool/{name}/stocks 只返回 code 与 add_time，没有数量字段，
   * 因此这里必须并发拉取每个池的股票列表后取长度。
   */
  attachStockCounts(pools) {
    const tasks = pools.map((p) =>
      api.pool
        .getStocks(p.name, true)
        .then((stocks) => (stocks || []).length)
        .catch(() => 0)
    );

    return Promise.all(tasks).then((counts) => {
      const vms = pools.map((p, i) => {
        const type = String(p.type || '手动维护');
        const meta = poolTypeMeta(type);
        return {
          id: String(p.id || p._id || ''),
          name: String(p.name || '未命名'),
          description: String(p.description || '') || '无描述',
          type,
          typeUpdate: meta.update,
          typeIcon: meta.icon,
          stockCount: counts[i] || 0
        };
      });

      let auto = 0;
      pools.forEach((p) => {
        const t = p.type;
        if (t === 'auto' || t === '自动同步' || t === '条件筛选' || t === '系统自动') auto += 1;
      });

      this.setData({
        pools: vms,
        statTotal: vms.length,
        statAuto: auto,
        statStatic: vms.length - auto,
        loading: false
      });
      this.applyFilter();
    });
  },

  /* ==================================================== 过滤 */

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
    this.applyFilter();
  },

  onSearchConfirm() {
    this.applyFilter();
  },

  clearKeyword() {
    this.setData({ keyword: '' });
    this.applyFilter();
  },

  applyFilter() {
    const kw = String(this.data.keyword || '').trim().toLowerCase();
    const list = this.data.pools.filter((p) => {
      if (!kw) return true;
      return p.name.toLowerCase().indexOf(kw) >= 0 || p.description.toLowerCase().indexOf(kw) >= 0;
    });
    this.setData({ filtered: list });
  },

  /* ==================================================== 交互 */

  goDetail(e) {
    const name = String(e.currentTarget.dataset.name || '');
    if (!name) return;
    ui.navigate(`/pages/pool-quotes/pool-quotes?name=${encodeURIComponent(name)}`);
  },

  goEdit(e) {
    const name = String(e.currentTarget.dataset.name || '');
    ui.navigate(`/pages/pool-form/pool-form?mode=edit&name=${encodeURIComponent(name)}`);
  },

  goCreate() {
    ui.navigate('/pages/pool-form/pool-form?mode=create');
  },

  goSearch() {
    ui.navigate('/pages/search/search?scope=quote');
  },

  retry() {
    this.load(true);
  }
});

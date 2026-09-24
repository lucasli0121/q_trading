/**
 * 创建 / 编辑股票池
 *
 * 对齐 Web 端 pages/stock_pools.py 的创建 / 编辑对话框（《页面操作文档》5.5）：
 *
 *   创建：股票池名称（必填）+ 描述 + 所属行业（多选）+ 自动筛选条件
 *        （市值亿 / PE）+ 手动追加股票（必填其一）+ 实时预览 → 保存
 *   编辑：名称与描述**只读**（服务端限制）+ 当前股票多选移除 + 筛选条件追加新股票
 *        + 危险区「删除股票池」
 *
 * 预览逻辑（与 Web 完全同源）：
 *   - 仅行业      → 逐行业调 /api/stock_info/industry 后取并集
 *   - 仅筛选条件  → /api/screener/search
 *   - 两者都有    → screener 结果 ∩ 行业集合
 *   - 手动追加的股票**无视筛选条件强行入池**（Web 的既有语义）
 *
 * ⚠️ 后端创建接口只接受 name/description，行业与筛选条件不会随创建提交，
 *    它们的作用是「把符合条件的股票算出来放进池子」——即预览 → 追加 → 保存股票代码。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
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

    mode: 'create',
    poolName: '',

    loading: true,
    loadError: '',

    // 基本信息
    name: '',
    description: '',
    readonlyBase: false, // 编辑模式名称/描述只读

    // 行业
    industries: [],
    selectedIndustries: [],

    // 筛选条件
    capMin: '',
    capMax: '',
    peMin: '',
    peMax: '',

    // 手动追加
    manualCodes: '',

    // 预览
    previewRows: [],
    previewLoading: false,
    previewKeyword: '',
    previewFiltered: [],
    previewCount: 0,

    // 编辑模式：当前股票
    currentStocks: [],
    checkedCount: 0,
    pendingRemove: [],

    saving: false,
    removing: false
  },

  onLoad(options) {
    theme.applyTo(this);
    if (!auth.guard()) return;
    const mode = options && options.mode === 'edit' ? 'edit' : 'create';
    const poolName = decodeURIComponent(String((options && options.name) || ''));
    this.setData({
      mode,
      poolName,
      readonlyBase: mode === 'edit',
      name: poolName
    });
    this.load();
  },

  onShow() {
    theme.applyTo(this);
  },

  /* ==================================================== 加载 */

  load() {
    this.setData({ loading: true, loadError: '' });
    return api.stockInfo
      .hotIndustries(true)
      .catch(() => [])
      .then((list) => {
        const industries = (list || [])
          .map((it) => (typeof it === 'string' ? it : String(it.name || it.industry || '')))
          .filter(Boolean);
        this.setData({ industries, loading: false });
        if (this.data.mode === 'edit') return this.loadCurrentStocks();
        return null;
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请稍后重试' });
      });
  },

  /** 编辑模式：拉取当前池内股票 + 实时行情，并补名称 */
  loadCurrentStocks() {
    const name = this.data.poolName;
    return api.pool
      .getStocks(name, true)
      .then((stocks) => {
        const codes = (stocks || []).map((s) => String(s.code || '')).filter(Boolean);
        if (!codes.length) {
          this.setData({ currentStocks: [], checkedCount: 0 });
          return null;
        }
        const codesStr = codes.join(',');
        return Promise.all([
          api.market.realTime({ codes: codesStr, silent: true }).catch(() => []),
          api.fetchStockNameMap(codes)
        ]).then(([quotes, nameMap]) => {
          const qMap = {};
          (quotes || []).forEach((q) => {
            const c = String(q.code || '');
            if (c && !qMap[c]) qMap[c] = q;
          });
          const rows = codes.map((c) => {
            const q = qMap[c] || {};
            const pct = fmt.sf(q.change_percent !== undefined ? q.change_percent : q.change_pct) * 100;
            return {
              code: c,
              name: nameMap[c] || nameMap[c.split('.')[0]] || '--',
              price: q.price !== undefined || q.close !== undefined
                ? fmt.priceGroup(fmt.sf(q.price !== undefined ? q.price : q.close))
                : '--',
              pctText: q.change_percent === undefined && q.change_pct === undefined
                ? '--'
                : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
              tone: fmt.toneOf(pct),
              checked: false
            };
          });
          this.setData({ currentStocks: rows, checkedCount: 0 });
          return null;
        });
      })
      .catch(() => this.setData({ currentStocks: [], checkedCount: 0 }));
  },

  /* ==================================================== 基本信息 */

  onField(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key) return;
    this.setData({ [key]: e.detail.value });
  },

  /* ==================================================== 行业 / 筛选 */

  toggleIndustry(e) {
    const name = String(e.currentTarget.dataset.name || '');
    if (!name) return;
    const list = this.data.selectedIndustries.slice();
    const i = list.indexOf(name);
    if (i >= 0) list.splice(i, 1);
    else list.push(name);
    this.setData({ selectedIndustries: list });
  },

  resetConditions() {
    this.setData({
      selectedIndustries: [],
      capMin: '',
      capMax: '',
      peMin: '',
      peMax: ''
    });
  },

  /* ==================================================== 预览 */

  onPreviewKeyword(e) {
    this.setData({ previewKeyword: e.detail.value });
    this.applyPreviewFilter();
  },

  clearPreviewKeyword() {
    this.setData({ previewKeyword: '' });
    this.applyPreviewFilter();
  },

  applyPreviewFilter() {
    const kw = String(this.data.previewKeyword || '').trim().toLowerCase();
    const rows = this.data.previewRows.filter(
      (r) => !kw || r.code.toLowerCase().indexOf(kw) >= 0 || r.name.toLowerCase().indexOf(kw) >= 0
    );
    this.setData({ previewFiltered: rows });
  },

  /** 按当前条件重新拉取预览（对齐 Web「刷新预览」） */
  refreshPreview() {
    const { selectedIndustries, capMin, capMax, peMin, peMax } = this.data;
    const hasIndustry = selectedIndustries.length > 0;
    const num = (v) => {
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : 0;
    };
    const capA = num(capMin);
    const capB = num(capMax);
    const peA = num(peMin);
    const peB = num(peMax);
    const hasFilter = !!(capA || capB || peA || peB);

    if (!hasIndustry && !hasFilter) {
      ui.toast('请先选择所属行业或设置筛选条件');
      return Promise.resolve();
    }

    this.setData({ previewLoading: true });

    const filterTask = hasFilter
      ? api.screener
          .search({ capMin: capA, capMax: capB, ttmMin: peA, ttmMax: peB, silent: true })
          .catch(() => [])
      : Promise.resolve(null);

    const industryTask = hasIndustry
      ? Promise.all(
          selectedIndustries.map((ind) =>
            api.stockInfo.byIndustry(ind, true).catch(() => [])
          )
        ).then((lists) => {
          const set = {};
          const rows = [];
          lists.forEach((l) => {
            (l || []).forEach((s) => {
              const c = String(s.code || s.stock_code || '');
              if (c && !set[c]) {
                set[c] = 1;
                rows.push({ code: c, name: String(s.name || s.stock_name || '') });
              }
            });
          });
          return { set, rows };
        })
      : Promise.resolve(null);

    return Promise.all([filterTask, industryTask]).then(([filterRows, industryData]) => {
      let codes = [];
      let nameMap = {};

      if (filterRows && industryData) {
        // 交集：以 screener 结果为准，过滤出属于所选行业的
        codes = (filterRows || [])
          .map((r) => String(r.code || ''))
          .filter((c) => c && industryData.set[c]);
        (filterRows || []).forEach((r) => {
          const c = String(r.code || '');
          if (c) nameMap[c] = String(r.name || '');
        });
      } else if (filterRows) {
        codes = (filterRows || []).map((r) => String(r.code || '')).filter(Boolean);
        (filterRows || []).forEach((r) => {
          const c = String(r.code || '');
          if (c) nameMap[c] = String(r.name || '');
        });
      } else if (industryData) {
        codes = industryData.rows.map((r) => r.code);
        industryData.rows.forEach((r) => {
          nameMap[r.code] = r.name;
        });
      }

      codes = codes.slice(0, config.MAX_LIMIT);
      // 名称缺失的批量补一次
      const missing = codes.filter((c) => !nameMap[c]);
      const nameTask = missing.length
        ? api.fetchStockNameMap(missing).then((m) => Object.assign(nameMap, m))
        : Promise.resolve();

      return nameTask.then(() => {
        const manual = this.parseCodes(this.data.manualCodes);
        const manualSet = {};
        manual.forEach((c) => (manualSet[c] = 1));

        const rows = codes.map((c) => ({
          code: c,
          name: nameMap[c] || '--',
          manual: !!manualSet[c],
          checked: false
        }));
        // 手动追加但不在预览结果里的，也一并列出，让用户看到最终入池清单
        manual.forEach((c) => {
          if (!codes.length || codes.indexOf(c) < 0) {
            rows.push({ code: c, name: nameMap[c] || '--', manual: true, checked: false });
          }
        });

        this.setData({
          previewRows: rows,
          previewLoading: false,
          previewCount: rows.length
        });
        this.applyPreviewFilter();
      });
    });
  },

  /** 预览列表勾选 —— 按 code 定位（previewFiltered 是预览全量的子集，下标不能直接复用） */
  onPreviewToggleRow(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    const rows = this.data.previewRows.slice();
    const i = rows.findIndex((r) => r.code === code);
    if (i < 0) return;
    rows[i] = Object.assign({}, rows[i], { checked: !rows[i].checked });
    this.setData({ previewRows: rows });
    this.applyPreviewFilter();
  },

  /** 把勾选的预览股票追加到「手动追加股票」框 */
  appendChecked() {
    const picked = this.data.previewRows.filter((r) => r.checked).map((r) => r.code);
    if (!picked.length) {
      ui.toast('请先在预览表格中勾选股票');
      return;
    }
    const exist = this.parseCodes(this.data.manualCodes);
    const added = [];
    picked.forEach((c) => {
      if (exist.indexOf(c) < 0) {
        exist.push(c);
        added.push(c);
      }
    });
    this.setData({ manualCodes: exist.join(',') });
    if (added.length) {
      ui.toast(`已追加 ${added.length} 只股票`);
    } else {
      ui.toast('选中的股票已在列表中');
    }
  },

  /* ==================================================== 当前股票（编辑） */

  onToggleCurrent(e) {
    const i = Number(e.currentTarget.dataset.i);
    const row = this.data.currentStocks[i];
    if (!row) return;
    const rows = this.data.currentStocks.slice();
    rows[i] = Object.assign({}, row, { checked: !row.checked });
    this.setData({
      currentStocks: rows,
      checkedCount: rows.filter((r) => r.checked).length
    });
  },

  /* ==================================================== 保存 */

  parseCodes(raw) {
    return String(raw || '')
      .replace(/[\uFF0C\u3001;；\s]+/g, ',')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  },

  onSave() {
    if (this.data.saving) return;
    const name = String(this.data.name || '').trim();
    if (!name) {
      ui.toast('股票池名称不能为空');
      return;
    }

    const codes = this.parseCodes(this.data.manualCodes);
    if (this.data.mode === 'create' && !codes.length) {
      ui.toast('请至少手动追加一只股票或设置筛选条件');
      return;
    }

    if (this.data.mode === 'create') {
      this.setData({ saving: true });
      api.pool
        .create(name, String(this.data.description || '').trim())
        .then(() => (codes.length ? api.pool.addStocks(name, codes) : null))
        .then(() => {
          store.clearCaches();
          this.setData({ saving: false });
          ui.toast(`股票池「${name}」创建成功`);
          setTimeout(() => wx.navigateBack(), 600);
        })
        .catch((err) => {
          this.setData({ saving: false });
          ui.error((err && err.message) || '创建股票池失败');
        });
      return;
    }

    // 编辑：移除勾选的 + 追加手动填写的
    // 注意：被剔除的股票已从 currentStocks 里摘掉，因此必须用 pendingRemove 记账，
    // 不能再用 currentStocks.filter(checked) 反推（那时已经没有勾选态了）
    const pendingRemove = (this.data.pendingRemove || []).slice();
    const remaining = this.data.currentStocks.map((r) => r.code);
    const toRemove = pendingRemove.filter((c) => remaining.indexOf(c) < 0);
    const toAdd = codes.filter((c) => remaining.indexOf(c) < 0 && toRemove.indexOf(c) < 0);

    if (!toRemove.length && !toAdd.length) {
      ui.toast('没有需要保存的改动');
      return;
    }

    this.setData({ saving: true });
    let chain = Promise.resolve();
    if (toRemove.length) chain = chain.then(() => api.pool.removeStocks(this.data.poolName, toRemove));
    if (toAdd.length) chain = chain.then(() => api.pool.addStocks(this.data.poolName, toAdd));

    chain
      .then(() => {
        store.clearCaches();
        this.setData({ saving: false });
        ui.toast('保存成功');
        setTimeout(() => wx.navigateBack(), 600);
      })
      .catch((err) => {
        this.setData({ saving: false });
        ui.error((err && err.message) || '保存失败');
      });
  },

  /** 移除勾选的当前股票（先摘出界面并记账，点「保存修改」时真正提交） */
  onRemoveChecked() {
    if (!this.data.checkedCount) {
      ui.toast('请先勾选要移除的股票');
      return;
    }
    const removed = this.data.currentStocks.filter((r) => r.checked).map((r) => r.code);
    const keep = this.data.currentStocks.filter((r) => !r.checked);
    const pending = (this.data.pendingRemove || []).slice();
    removed.forEach((c) => {
      if (pending.indexOf(c) < 0) pending.push(c);
    });
    this.setData({
      currentStocks: keep,
      checkedCount: 0,
      pendingRemove: pending
    });
    ui.toast('已剔除，点击「保存修改」后生效');
  },

  onDeletePool() {
    const name = this.data.poolName;
    ui.confirm({
      title: '删除股票池',
      content: `删除「${name}」后不可恢复，且引用该池的策略将失去股票来源。确定删除吗？`,
      confirmText: '确认删除',
      danger: true
    }).then((ok) => {
      if (!ok) return;
      this.setData({ removing: true });
      api.pool
        .remove(name)
        .then(() => {
          store.clearCaches();
          this.setData({ removing: false });
          ui.toast('已删除股票池');
          setTimeout(() => wx.navigateBack(), 600);
        })
        .catch((err) => {
          this.setData({ removing: false });
          ui.error((err && err.message) || '删除失败');
        });
    });
  },

  retry() {
    this.load();
  }
});

/**
 * 创建 / 编辑策略
 *
 * 对齐 Web 端 pages/strategies.py 的两个对话框（《页面操作文档》5.2）：
 *
 *   创建：勾选策略模板 → 自定义名称 → 股票池 → 初始资金（默认 1,000,000）
 *        → 最大持仓数（默认 4）→ 参数 → 保存
 *   编辑：模板**只读不可更换**，可改名称 / 股票池 / 初始资金 / 最大持仓数 / 参数
 *
 * 参数结构（易踩坑，已在 doc/接口契约.md 记录）：
 *   模板 default_params = { match: { key: { desc, value } }, buy: {...}, sell: {...} }  ← 三层
 *   提交 strategy_params = { match: { key: value }, buy: {...}, sell: {...} }           ← 两层
 *   展示/编辑以 default_params 为准（拿到中文 desc 与默认值），用户已保存值优先。
 *
 * 保存校验与提示文案严格对齐 Web（含 409「该策略已关联…」的重复创建提示）。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const fmt = require('../../utils/format');
const { PARAM_GROUP_LABELS } = require('../../utils/constants');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    mode: 'create',
    usId: '',

    loading: true,
    loadError: '',

    templates: [],
    selectedSid: '',
    selectedTpl: null,
    lockedTpl: null, // 编辑模式下只读展示的模板卡

    name: '',
    pools: [],
    poolNames: [],
    poolIndex: -1,
    poolId: '',
    poolName: '',

    initialAmount: '1000000',
    maxStockCount: '4',

    paramGroups: [],
    hasParams: false,

    saving: false
  },

  onLoad(options) {
    theme.applyTo(this);
    if (!auth.guard()) return;
    const mode = options && options.mode === 'edit' ? 'edit' : 'create';
    this.setData({ mode, usId: String((options && options.id) || '') });
    this.load();
  },

  onShow() {
    theme.applyTo(this);
  },

  /* ==================================================== 加载 */

  load() {
    this.setData({ loading: true, loadError: '' });
    return Promise.all([
      api.strategy.listTemplates(true).catch(() => []),
      api.pool.ensureLoaded(true).catch(() => []),
      this.data.mode === 'edit' ? api.userStrategy.ensureLoaded(true).catch(() => []) : []
    ])
      .then(([templates, pools, userStrategies]) => {
        const tplList = (templates || []).map((t) => ({
          id: String(t._id || t.id || ''),
          name: String(t.name || '未命名模板'),
          type: String(t.strategy_type || '-'),
          description: String(t.description || '') || '暂无描述'
        }));
        const poolList = (pools || []).map((p) => ({
          id: String(p._id || p.id || ''),
          name: String(p.name || '')
        }));

        this.setData({
          templates: tplList,
          pools: poolList,
          poolNames: poolList.map((p) => p.name),
          loading: false
        });

        if (this.data.mode === 'edit') {
          return this.prefill(userStrategies || [], tplList, poolList);
        }
        this.setData({ hasParams: false, paramGroups: [] });
        return null;
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '加载失败，请检查网络后重试' });
      });
  },

  prefill(list, tplList, poolList) {
    const us = (list || []).filter(
      (x) => String(x._id || x.id || '') === this.data.usId
    )[0];
    if (!us) {
      this.setData({ loadError: '策略不存在或已被删除' });
      return null;
    }
    const sid = String(us.strategy_id || '');
    const tpl = tplList.filter((t) => t.id === sid)[0] || null;
    const poolId = String(us.pool_id || '');
    const poolIndex = poolList.findIndex((p) => p.id === poolId);

    this.setData({
      selectedSid: sid,
      lockedTpl: tpl || {
        id: sid,
        name: String(us.name || sid || '未命名策略'),
        type: String(us.strategy_type || '-'),
        description: String(us.description || '') || '暂无描述'
      },
      name: String(us.name || ''),
      poolIndex,
      poolId,
      poolName: poolIndex >= 0 ? poolList[poolIndex].name : '',
      initialAmount: String(fmt.sf(us.initial_amount) || 0),
      maxStockCount: String(Number(us.max_stock_count || 0) || 4)
    });

    return this.applyTemplate(sid, us.strategy_params || {});
  },

  /* ==================================================== 模板 */

  onPickTemplate(e) {
    if (this.data.mode === 'edit') {
      ui.toast('编辑时不可更换策略模板');
      return;
    }
    const sid = String(e.currentTarget.dataset.id || '');
    if (!sid) return;
    this.applyTemplate(sid, {});
  },

  /** 选中模板 → 展开该模板的参数（编辑模式则用用户已保存值覆盖） */
  applyTemplate(sid, saved) {
    const tpl = this.data.templates.filter((t) => t.id === sid)[0] || null;
    this.setData({ selectedSid: sid, selectedTpl: tpl });
    if (!tpl) {
      this.setData({ paramGroups: [], hasParams: false });
      return Promise.resolve();
    }
    // 模板详情才有 default_params（列表接口不含），因此这里单独取一次
    return api.strategy
      .getById(sid, true)
      .catch(() => ({}))
      .then((detail) => {
        if (this.data.selectedSid !== sid) return;
        this.setData({
          paramGroups: this.buildParamGroups(detail && detail.default_params, saved),
          hasParams: !!(detail && detail.default_params)
        });
      });
  },

  /**
   * default_params（三层）→ 可编辑的扁平结构
   * { match: { min_price: {desc:'股价下限', value: 3} } } → items[{ rawKey, desc, value:'3', isNumber:true }]
   */
  buildParamGroups(defaultParams, saved) {
    const def = defaultParams || {};
    const groups = [];
    Object.keys(def).forEach((gk) => {
      const groupParams = def[gk];
      if (!groupParams || typeof groupParams !== 'object' || Array.isArray(groupParams)) return;
      const savedGroup = (saved && saved[gk]) || {};
      const items = [];
      Object.keys(groupParams).forEach((k) => {
        const meta = groupParams[k];
        const nested = meta && typeof meta === 'object' && !Array.isArray(meta);
        const fallback = nested ? meta.value : meta;
        const userVal = savedGroup[k];
        const hasUser = userVal !== undefined && userVal !== null && userVal !== '';
        const val = hasUser ? userVal : fallback;
        items.push({
          rawKey: k,
          desc: (nested && meta.desc) || k,
          value: val === null || val === undefined ? '' : String(val),
          isNumber: typeof val === 'number' || isNumericString(fallback)
        });
      });
      if (items.length) {
        groups.push({ key: gk, title: PARAM_GROUP_LABELS[gk] || gk, items });
      }
    });
    return groups;
  },

  onParamInput(e) {
    const gi = Number(e.currentTarget.dataset.gi);
    const ki = Number(e.currentTarget.dataset.ki);
    const v = e.detail.value;
    const groups = this.data.paramGroups;
    if (!groups[gi] || !groups[gi].items[ki]) return;
    this.setData({ [`paramGroups[${gi}].items[${ki}].value`]: v });
  },

  /** 参数扁平结构 → 提交用的两层对象（数字还原为 number） */
  collectParams() {
    const out = {};
    (this.data.paramGroups || []).forEach((g) => {
      const group = {};
      (g.items || []).forEach((it) => {
        const raw = String(it.value === undefined || it.value === null ? '' : it.value).trim();
        if (raw === '') {
          group[it.rawKey] = it.isNumber ? 0 : '';
        } else if (it.isNumber) {
          const n = parseFloat(raw);
          group[it.rawKey] = Number.isFinite(n) ? n : 0;
        } else {
          group[it.rawKey] = raw;
        }
      });
      out[g.key] = group;
    });
    return out;
  },

  /* ==================================================== 表单 */

  onField(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key) return;
    this.setData({ [key]: e.detail.value });
  },

  onPoolChange(e) {
    const i = Number(e.detail.value);
    const pool = this.data.pools[i];
    this.setData({
      poolIndex: i,
      poolId: pool ? pool.id : '',
      poolName: pool ? pool.name : ''
    });
  },

  /* ==================================================== 保存 */

  onSave() {
    if (this.data.saving) return;

    if (this.data.mode === 'create') {
      if (!this.data.selectedSid) {
        ui.toast('请先勾选一个策略模板');
        return;
      }
      if (!String(this.data.name || '').trim()) {
        ui.toast('请输入策略自定义名称');
        return;
      }
    } else if (!String(this.data.name || '').trim()) {
      ui.toast('请输入策略自定义名称');
      return;
    }

    if (!this.data.poolId) {
      ui.toast('请选择股票池');
      return;
    }

    const amount = parseFloat(this.data.initialAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      ui.toast('请输入初始模拟资金');
      return;
    }
    const maxCount = parseInt(this.data.maxStockCount, 10);
    if (!Number.isFinite(maxCount) || maxCount <= 0) {
      ui.toast('请输入最大持仓数');
      return;
    }

    const name = String(this.data.name).trim();
    const params = this.collectParams();
    this.setData({ saving: true });

    const done = (ok, msg) => {
      this.setData({ saving: false });
      if (ok) {
        ui.toast(msg);
        setTimeout(() => {
          const pages = getCurrentPages();
          if (pages.length > 1) wx.navigateBack();
          else wx.switchTab({ url: '/pages/strategies/strategies' });
        }, 600);
      }
    };

    if (this.data.mode === 'create') {
      api.userStrategy
        .create({
          strategy_id: this.data.selectedSid,
          pool_id: this.data.poolId,
          status: 'stopped',
          initial_amount: amount,
          max_stock_count: maxCount,
          strategy_params: params,
          name
        })
        .then(() => done(true, '创建成功'))
        .catch((err) => {
          this.setData({ saving: false });
          ui.error(err && err.statusCode === 409
            ? '该策略已关联，请在列表中编辑，请勿重复创建'
            : (err && err.message) || '创建策略失败');
        });
      return;
    }

    api.userStrategy
      .update(this.data.usId, {
        name,
        pool_id: this.data.poolId,
        initial_amount: amount,
        max_stock_count: maxCount,
        strategy_params: params
      })
      .then(() => done(true, '保存成功'))
      .catch((err) => {
        this.setData({ saving: false });
        ui.error(err && err.statusCode === 409
          ? '已存在同名策略，请更换名称'
          : (err && err.message) || '保存失败');
      });
  },

  retry() {
    this.load();
  }
});

function isNumericString(v) {
  if (typeof v === 'number') return true;
  if (typeof v !== 'string') return false;
  return v.trim() !== '' && Number.isFinite(parseFloat(v));
}

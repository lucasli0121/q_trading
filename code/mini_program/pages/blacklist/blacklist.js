/**
 * 黑名单股票管理
 *
 * 对齐 Web 端 pages/settings_blacklist.py（《页面操作文档》5.8）：
 *   - 蓝色提示横幅：黑名单逻辑生效于所有策略
 *   - 表格：股票代码 / 股票名称 / 拉黑时间 / 拉黑原因 / 用户 / 操作
 *   - 添加：多行输入，多个代码用逗号分隔（中英文逗号均识别）
 *   - 移除：点「移除名单」即时生效
 *
 * 两处与 Web 的**有意差异**（属于改进，已在 doc/代码功能说明.md 记录）：
 *   1. Web 的搜索框尚未绑定过滤逻辑（输入不筛选），小程序端做成了真实过滤；
 *   2. Web 的「用户」列在接口未返回用户信息时回退展示当前登录账号，
 *      小程序沿用同一回退链（user_name → username → user_id → 当前账号）。
 */

const api = require('../../utils/api');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const store = require('../../utils/store');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    loading: true,
    loadError: '',

    keyword: '',
    rows: [],
    filtered: [],

    // 添加对话框
    showAdd: false,
    addCodes: '',
    addReason: '',
    addError: '',
    adding: false
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
  },

  onShow() {
    theme.applyTo(this);
    if (!store.isLoggedIn()) return;
    this.load();
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 加载 */

  load() {
    this.setData({ loading: true, loadError: '' });
    const me = String((store.getUser() || {}).account || '') || '--';

    return api.blacklist
      .list(true)
      .then((list) => {
        const items = list || [];
        return api
          .fetchStockNameMap(items.map((it) => String(it.code || '')))
          .then((nameMap) => {
            const rows = items.map((it) => {
              const code = String(it.code || '');
              const t = String(it.add_time || '');
              return {
                code,
                name: nameMap[code] || nameMap[code.split('.')[0]] || '--',
                addTime: t ? t.slice(0, 16) : '--',
                reason: String(it.reason || '') || '--',
                user:
                  String(it.user_name || it.username || it.user_id || '') || me,
                hasReason: !!String(it.reason || '')
              };
            });
            this.setData({ rows, loading: false });
            this.applyFilter();
          });
      })
      .catch(() => {
        this.setData({ loading: false, loadError: '黑名单加载失败，请稍后重试' });
      });
  },

  applyFilter() {
    const kw = String(this.data.keyword || '').trim().toLowerCase();
    const filtered = this.data.rows.filter((r) => {
      if (!kw) return true;
      return (
        r.code.toLowerCase().indexOf(kw) >= 0 || r.name.toLowerCase().indexOf(kw) >= 0
      );
    });
    this.setData({ filtered });
  },

  onKeyword(e) {
    this.setData({ keyword: e.detail.value });
    this.applyFilter();
  },

  clearKeyword() {
    this.setData({ keyword: '' });
    this.applyFilter();
  },

  /* ==================================================== 添加 */

  openAdd() {
    this.setData({ showAdd: true, addCodes: '', addReason: '', addError: '' });
  },

  closeAdd() {
    this.setData({ showAdd: false, addError: '' });
  },

  onAddField(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key) return;
    this.setData({ [key]: e.detail.value, addError: '' });
  },

  /** 中英文逗号、空白、换行、分号统一按分隔符处理 */
  parseCodes(raw) {
    return String(raw || '')
      .replace(/[\uFF0C\u3001;；\s]+/g, ',')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  },

  submitAdd() {
    const raw = String(this.data.addCodes || '').trim();
    if (!raw) {
      this.setData({ addError: '请输入股票代码' });
      return;
    }
    const codes = this.parseCodes(raw);
    if (!codes.length) {
      this.setData({ addError: '未识别到有效的股票代码' });
      return;
    }
    this.setData({ adding: true, addError: '' });
    api.blacklist
      .add(codes, String(this.data.addReason || '').trim())
      .then(() => {
        this.setData({ adding: false, showAdd: false });
        ui.success('已加入黑名单');
        this.load();
      })
      .catch((err) => {
        this.setData({ adding: false });
        const msg = (err && err.message) || '添加失败';
        this.setData({ addError: msg });
      });
  },

  /* ==================================================== 移除 */

  onRemove(e) {
    const code = String(e.currentTarget.dataset.code || '');
    if (!code) return;
    ui.confirm({
      title: '移除黑名单',
      content: `确定将 ${code} 移出黑名单吗？移除后该股票可再次被策略选中。`,
      confirmText: '移除',
      danger: true
    }).then((ok) => {
      if (!ok) return;
      api.blacklist
        .remove([code])
        .then(() => {
          ui.success('已移除');
          this.load();
        })
        .catch((err) => ui.error((err && err.message) || '移除失败'));
    });
  },

  retry() {
    this.load();
  }
});

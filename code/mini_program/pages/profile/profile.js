/**
 * Tab4 · 我的
 *
 * Web 端没有独立的「我的」页（侧边栏直接列出所有入口），小程序把它聚合为一页：
 *   - 账号卡片：账号 / 角色 / 手机号（打码）
 *   - 数据概览：策略总数 / 运行中 / 本月新增（复用 store 的缓存统计）
 *   - 深色模式快速开关（即时生效并落库，与「个人喜好」页同一实现）
 *   - 功能入口：策略监控 / 策略选股 / 股票池 / 个人喜好 / 黑名单 / 关于
 *   - 退出登录
 */

const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const api = require('../../utils/api');
const fmt = require('../../utils/format');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    account: '',
    roleText: '',
    phoneText: '',
    initials: '',

    dark: false,

    statTotal: 0,
    statRunning: 0,
    statMonthNew: 0,

    version: '1.0.0'
  },

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
    this.refreshUser();
  },

  onShow() {
    theme.applyTo(this);
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 });
    }
    if (!store.isLoggedIn()) return;
    this.refreshUser();
    this.refreshStats();
    this.loadPrefs();
  },

  onPullDownRefresh() {
    Promise.resolve()
      .then(() => this.refreshUser())
      .then(() => this.refreshStats(true))
      .then(() => wx.stopPullDownRefresh());
  },

  /* ==================================================== 数据 */

  /** 主题落库需要带上其余偏好字段（后端是「全量替换」语义），因此先取一份缓存 */
  loadPrefs() {
    return api.user
      .getPreference()
      .then((prefs) => {
        this._prefs = prefs || {};
        // 用户从未保存过偏好时后端返回默认值，不能当作用户选择（resolveFromPrefs 规则）
        const mode = theme.resolveFromPrefs(prefs);
        if (mode && mode !== theme.current()) {
          theme.setMode(mode);
          theme.applyTo(this);
          this.setData({ dark: mode === 'dark' });
        }
      })
      .catch(() => null);
  },

  refreshUser() {
    const s = store.getUser() || {};
    const account = String(s.account || s.username || '');
    const phone = String(s.phone || '');
    this.setData({
      account: account || '未登录',
      roleText: Number(s.role) === 0 ? '管理员' : '普通用户',
      phoneText: phone ? fmt.maskPhone(phone) : '未绑定',
      initials: account ? account.slice(0, 1).toUpperCase() : 'Q',
      dark: theme.current() === 'dark'
    });
  },

  refreshStats(force) {
    return api.userStrategy
      .ensureLoaded(force)
      .then(() => {
        this.setData({
          statTotal: store.countStrategies(),
          statRunning: store.countRunning(),
          statMonthNew: store.countMonthNew()
        });
      })
      .catch(() => null);
  },

  /* ==================================================== 主题 */

  onToggleDark(e) {
    const on = !!e.detail.value;
    theme.setMode(on ? 'dark' : 'light');
    this.setData({ dark: on });
    theme.applyTo(this);
    // 与「个人喜好」页同源：主题即时生效 + 立即落库（其余偏好才走保存按钮）
    const prefs = this._prefs || {};
    api.user
      .updatePreference({
        theme_mode: on ? 'dark' : 'light',
        enable_wx_push: !!prefs.enable_wx_push,
        wx_push_url: prefs.wx_push_url || '',
        enable_phone_text: !!prefs.enable_phone_text,
        phone: prefs.phone || ''
      })
      .then(() => ui.toast(on ? '已切换到深色主题' : '已切换到浅色主题'))
      .catch(() => ui.toast(on ? '已切换到深色主题（未保存到服务器）' : '已切换到浅色主题（未保存到服务器）'));
  },

  /* ==================================================== 导航 */

  goMonitor() {
    ui.navigate('/pages/monitor/monitor');
  },

  goSelect() {
    ui.navigate('/pages/strategy-select/strategy-select');
  },

  goPools() {
    ui.navigate('/pages/quotes/quotes');
  },

  goPreferences() {
    ui.navigate('/pages/preferences/preferences');
  },

  goBlacklist() {
    ui.navigate('/pages/blacklist/blacklist');
  },

  goSearch() {
    ui.navigate('/pages/search/search?scope=quote');
  },

  onAbout() {
    ui.confirm({
      title: 'QTrade 量化交易',
      content:
        '微信小程序端 · v1.0.0\n\n' +
        '与 Web 端共用同一套后端接口，功能包括：股票池管理、策略创建与监控、' +
        '策略选股、实时行情与个股分析、个人偏好与黑名单。\n\n' +
        '行情与信号仅供研究参考，不构成投资建议。',
      confirmText: '知道了',
      cancelText: '关闭'
    });
  },

  onLogout() {
    auth.logout();
  },

  onDeleteAccount() {
    ui.confirm({
      title: '注销账号',
      content: '注销后账号数据不可恢复，且需要重新注册。确定继续吗？',
      confirmText: '继续',
      danger: true
    }).then((ok) => {
      if (!ok) return;
      wx.showModal({
        title: '输入密码确认',
        editable: true,
        placeholderText: '请输入当前账号密码',
        confirmColor: '#DC2626',
        success: (res) => {
          if (!res.confirm) return;
          const pwd = String(res.content || '').trim();
          if (!pwd) {
            ui.error('请输入密码');
            return;
          }
          api.user
            .deleteAccount(pwd)
            .then(() => {
              store.clearSession();
              ui.toast('账号已注销');
              setTimeout(() => wx.reLaunch({ url: '/pages/login/login' }), 600);
            })
            .catch((err) => ui.error((err && err.message) || '注销失败'));
        }
      });
    });
  }
});

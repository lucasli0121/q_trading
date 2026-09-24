/**
 * 个人喜好设置
 *
 * 对齐 Web 端 pages/settings_preferences.py（《页面操作文档》5.7）：
 *   - 三张卡片：界面显示 / 企业微信推送 / 手机短信推送
 *   - 「保存更改」提交**全部 5 个字段**（后端 PUT 是全量替换语义，
 *     因此每次提交都要把没改的字段一起带上，否则会被清空）；
 *   - 深色模式是**即时生效**型：拨动开关立刻写 theme_mode 并落库，
 *     不需要再点「保存更改」（与 Web 一致）；
 *   - 关闭「启用推送」时 Webhook 输入框隐藏，关闭「启用短信推送」时手机号隐藏。
 *
 * 本页在 Web 基础上补了一条前端校验：开启推送却未填地址 / 手机号时不允许保存，
 * 避免把空值写进后端导致策略触发时静默失败。
 */

const api = require('../../utils/api');
const store = require('../../utils/store');
const theme = require('../../utils/theme');
const ui = require('../../utils/ui');
const auth = require('../../utils/auth');
const { PREF_FIELDS } = require('../../utils/constants');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    navbarHeight: 64,
    safeBottom: 0,

    loading: true,
    saving: false,

    dark: false,
    enableWxPush: false,
    wxPushUrl: '',
    enablePhoneText: false,
    phone: '',

    // 表单错误
    wxPushError: '',
    phoneError: ''
  },

  /** 服务端最近一次返回的原始偏好（用于判断「用户是否真的选过主题」） */
  _prefs: {},

  onLoad() {
    theme.applyTo(this);
    if (!auth.guard()) return;
    this.load();
  },

  onShow() {
    theme.applyTo(this);
  },

  /* ==================================================== 加载 */

  load() {
    this.setData({ loading: true });
    return api.user
      .getPreference()
      .then((prefs) => {
        const p = prefs || {};
        this._prefs = p;
        // 从未保存过偏好的用户返回的是模型默认值，不能当作用户选择
        const mode = theme.resolveFromPrefs(p) || theme.current();
        if (mode !== theme.current()) theme.setMode(mode);
        this.setData({
          dark: mode === 'dark',
          enableWxPush: !!p.enable_wx_push,
          wxPushUrl: String(p.wx_push_url || ''),
          enablePhoneText: !!p.enable_phone_text,
          phone: String(p.phone || ''),
          loading: false
        });
        theme.applyTo(this);
      })
      .catch(() => {
        this.setData({ loading: false });
        ui.error('偏好加载失败');
      });
  },

  /* ==================================================== 交互 */

  onToggleDark(e) {
    const on = !!e.detail.value;
    theme.setMode(on ? 'dark' : 'light');
    this.setData({ dark: on });
    theme.applyTo(this);
    // 主题即时落库（带上其余字段，避免全量替换把别的偏好清掉）
    api.user
      .updatePreference(this.payload({ theme_mode: on ? 'dark' : 'light' }))
      .then(() => ui.toast(on ? '已切换到深色主题' : '已切换到浅色主题'))
      .catch(() => ui.toast('主题已切换，但未保存到服务器'));
  },

  onWxPushToggle(e) {
    this.setData({ enableWxPush: !!e.detail.value, wxPushError: '' });
  },

  onPhoneToggle(e) {
    this.setData({ enablePhoneText: !!e.detail.value, phoneError: '' });
  },

  onField(e) {
    const key = String(e.currentTarget.dataset.key || '');
    if (!key) return;
    this.setData({ [key]: e.detail.value });
  },

  /** 组装提交体：以服务端字段为底，再用当前界面值覆盖 */
  payload(override) {
    const body = {};
    const base = this._prefs || {};
    PREF_FIELDS.forEach((k) => {
      // 服务端没返回过的键不传，避免写入 undefined
      if (base[k] !== undefined) body[k] = base[k];
    });
    body.theme_mode = theme.current();
    body.enable_wx_push = !!this.data.enableWxPush;
    body.wx_push_url = String(this.data.wxPushUrl || '').trim();
    body.enable_phone_text = !!this.data.enablePhoneText;
    body.phone = String(this.data.phone || '').trim();
    return Object.assign(body, override || {});
  },

  onSave() {
    const wxPushUrl = String(this.data.wxPushUrl || '').trim();
    const phone = String(this.data.phone || '').trim();

    let wxPushError = '';
    let phoneError = '';
    if (this.data.enableWxPush && !wxPushUrl) {
      wxPushError = '请输入企业微信 Webhook 地址';
    } else if (this.data.enableWxPush && wxPushUrl.indexOf('http') !== 0) {
      wxPushError = 'Webhook 地址需以 http(s):// 开头';
    }
    if (this.data.enablePhoneText && !phone) {
      phoneError = '请输入接收短信的手机号';
    } else if (this.data.enablePhoneText && phone.length < 7) {
      phoneError = '手机号格式不正确';
    }
    if (wxPushError || phoneError) {
      this.setData({ wxPushError, phoneError });
      return;
    }

    this.setData({ saving: true, wxPushError: '', phoneError: '' });
    api.user
      .updatePreference(this.payload())
      .then(() => {
        this._prefs = this.payload();
        this.setData({ saving: false });
        ui.success('设置已保存');
      })
      .catch((err) => {
        this.setData({ saving: false });
        ui.error((err && err.message) || '保存失败，请稍后重试');
      });
  },

  goBlacklist() {
    ui.navigate('/pages/blacklist/blacklist');
  }
});

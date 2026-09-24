/**
 * 登录 / 注册 / 微信一键登录
 *
 * 对齐 Web 端 pages/login_page.py：
 *   - 登录：账号（用户名 / 邮箱 / 手机号任一）+ 密码 → POST /api/user/login
 *   - 注册：用户名 / 邮箱 / 手机号 / 密码 / 确认密码 → POST /api/user/register，
 *           成功后**自动登录**；自动登录失败则切回登录表单
 *   - 「忘记密码？」仅提示「请联系管理员重置密码」，无接口调用
 *   - 「记住我」仅作界面展示，不影响会话（与 Web 一致）
 * 小程序额外增加（设计稿要求）：
 *   - 协议勾选：未勾选时登录/注册按钮禁用
 *   - 微信一键登录：wx.login → code → POST /api/user/wx_login（后端待新增，
 *     未就绪时给出明确提示并引导用账号密码登录）
 *
 * 校验规则刻意与 Web 保持一致：只做长度与非空判断，不加正则。
 * （Web 提示文案写着「4-16 位字母或数字」，但代码里没有正则，这里不擅自加严。）
 */

const api = require('../../utils/api');
const ui = require('../../utils/ui');
const theme = require('../../utils/theme');
const auth = require('../../utils/auth');
const store = require('../../utils/store');

Page({
  data: {
    themeClass: 'theme-light',
    statusBarHeight: 20,
    safeBottom: 0,

    tab: 'login', // login | register
    agreed: false,
    showPwd: false,
    showPwd2: false,
    loading: false,

    // 登录表单
    account: '',
    password: '',

    // 注册表单
    regAccount: '',
    regEmail: '',
    regPhone: '',
    regPassword: '',
    regPassword2: '',

    // 逐字段错误（首次失焦触发）
    errors: {},

    // 微信绑定态
    bindTicket: ''
  },

  onLoad() {
    theme.applyTo(this);
    // 已登录直接进主流程，避免重复登录
    if (store.isLoggedIn()) {
      wx.switchTab({ url: '/pages/home/home' });
    }
  },

  onShow() {
    theme.applyTo(this);
  },

  /* ------------------------------------------------------ 交互 */

  onSwitchTab(e) {
    const tab = String(e.currentTarget.dataset.tab);
    if (tab === this.data.tab) return;
    this.setData({ errors: {}, bindTicket: '', tab });
  },

  onAgree() {
    this.setData({ agreed: !this.data.agreed });
  },

  togglePwd() {
    this.setData({ showPwd: !this.data.showPwd });
  },

  togglePwd2() {
    this.setData({ showPwd2: !this.data.showPwd2 });
  },

  /** 字段变更（q-field 的 change 事件） */
  onChange(e) {
    const field = String(e.currentTarget.dataset.field);
    const patch = {};
    patch[field] = e.detail.value;
    // 输入即清除该字段错误
    const errors = Object.assign({}, this.data.errors);
    delete errors[field];
    patch.errors = errors;
    this.setData(patch);
  },

  /** 首次失焦触发校验 */
  onBlur(e) {
    const field = String(e.currentTarget.dataset.field);
    const value = e.detail.value || '';
    const check = this.validateField(field, value);
    const errors = Object.assign({}, this.data.errors);
    if (check) errors[field] = check;
    else delete errors[field];
    this.setData({ errors });
  },

  validateField(field, value) {
    const v = String(value || '').trim();
    switch (field) {
      case 'account':
        return v ? '' : '请输入用户名 / 邮箱 / 手机号';
      case 'password':
        return v ? '' : '请输入密码';
      case 'regAccount':
        if (!v) return '用户名不能为空';
        if (v.length < 4 || v.length > 16) return '用户名应为4-16位字母或数字';
        return '';
      case 'regEmail':
        return v ? '' : '请输入邮箱';
      case 'regPhone':
        return v ? '' : '请输入手机号';
      case 'regPassword':
        if (!v) return '请输入密码';
        if (v.length < 8) return '密码至少8位，含字母和数字';
        return '';
      case 'regPassword2':
        if (!v) return '请再次输入密码';
        if (v !== this.data.regPassword) return '两次输入的密码不一致';
        return '';
      default:
        return '';
    }
  },

  onForgot() {
    ui.toast('请联系管理员重置密码', 2000);
  },

  /* ------------------------------------------------------ 登录 */

  onLogin() {
    if (this.data.loading) return;
    const account = String(this.data.account || '').trim();
    const password = String(this.data.password || '');

    const errors = {};
    if (!account) errors.account = '请输入用户名 / 邮箱 / 手机号';
    if (!password) errors.password = '请输入密码';
    if (Object.keys(errors).length) {
      this.setData({ errors });
      return;
    }
    if (!this.data.agreed) {
      ui.toast('请先阅读并勾选用户协议');
      return;
    }

    this.setData({ loading: true });
    api.user
      .login(account, password)
      .then((result) => {
        auth.persistLogin(result, account);
        ui.toast('登录成功');
        this.enterApp();
      })
      .catch((err) => {
        // 401 在 request 层已经未走「会话过期」分支（匿名请求），这里直接提示
        ui.error((err && err.message) || '登录失败');
      })
      .then(() => this.setData({ loading: false }));
  },

  /* ------------------------------------------------------ 注册 */

  onRegister() {
    if (this.data.loading) return;
    const fields = ['regAccount', 'regEmail', 'regPhone', 'regPassword', 'regPassword2'];
    const errors = {};
    fields.forEach((f) => {
      const msg = this.validateField(f, this.data[f]);
      if (msg) errors[f] = msg;
    });
    if (Object.keys(errors).length) {
      this.setData({ errors });
      return;
    }
    if (!this.data.agreed) {
      ui.toast('请先阅读并勾选用户协议');
      return;
    }

    const account = String(this.data.regAccount).trim();
    const password = String(this.data.regPassword);

    this.setData({ loading: true });
    api.user
      .register({
        account,
        password,
        phone: String(this.data.regPhone || '').trim(),
        email: String(this.data.regEmail || '').trim()
      })
      .then(() => {
        // 注册成功后自动登录（对齐 Web 行为）
        return api.user
          .login(account, password)
          .then((result) => {
            auth.persistLogin(result, account);
            ui.toast('注册成功');
            this.enterApp();
          })
          .catch(() => {
            ui.toast('注册成功，请手动登录');
            this.setData({ tab: 'login', account, password: '' });
          });
      })
      .catch((err) => {
        ui.error((err && err.message) || '注册失败');
      })
      .then(() => this.setData({ loading: false }));
  },

  /* ------------------------------------------------------ 微信一键登录 */

  onWxLogin() {
    if (this.data.loading) return;
    if (!this.data.agreed) {
      ui.toast('请先阅读并勾选用户协议');
      return;
    }

    this.setData({ loading: true });
    wx.login({
      success: (res) => {
        if (!res.code) {
          this.setData({ loading: false });
          ui.error('微信登录失败，请重试');
          return;
        }
        api.user
          .wxLogin(res.code)
          .then((result) => {
            const data = result || {};
            if (data.need_bind || data.bind_ticket) {
              // 未绑定平台账号：切换到绑定表单
              this.setData({
                bindTicket: String(data.bind_ticket || ''),
                errors: {},
                loading: false
              });
              ui.toast('首次使用请绑定已有账号');
              return;
            }
            auth.persistLogin(data, data.account || '');
            ui.toast('登录成功');
            this.enterApp();
          })
          .catch((err) => {
            const status = err && err.statusCode;
            if (status === 404 || status === 405) {
              ui.toast('微信登录接口尚未就绪，请使用账号密码登录', 2400);
            } else {
              ui.error((err && err.message) || '微信登录失败');
            }
          })
          .then(() => this.setData({ loading: false }));
      },
      fail: () => {
        this.setData({ loading: false });
        ui.error('未获取到微信授权，请重试');
      }
    });
  },

  /** 提交微信绑定 */
  onWxBind() {
    const account = String(this.data.account || '').trim();
    const password = String(this.data.password || '');
    const errors = {};
    if (!account) errors.account = '请输入要绑定的账号';
    if (!password) errors.password = '请输入密码';
    if (Object.keys(errors).length) {
      this.setData({ errors });
      return;
    }
    this.setData({ loading: true });
    api.user
      .wxBind({ bind_ticket: this.data.bindTicket, account, password })
      .then((result) => {
        auth.persistLogin(result, account);
        ui.toast('绑定成功');
        this.enterApp();
      })
      .catch((err) => {
        ui.error((err && err.message) || '绑定失败');
      })
      .then(() => this.setData({ loading: false }));
  },

  onCancelBind() {
    this.setData({ bindTicket: '' });
  },

  /** 登录成功后的统一入口：优先回跳来源页，否则进首页 */
  enterApp() {
    const target = auth.takeRedirect();
    setTimeout(() => {
      if (target && target !== '/pages/login/login') {
        const path = target.split('?')[0];
        const isTab = ['/pages/home/home', '/pages/quotes/quotes', '/pages/strategies/strategies', '/pages/profile/profile'].indexOf(path) >= 0;
        if (isTab) wx.switchTab({ url: path });
        else wx.redirectTo({ url: target });
      } else {
        wx.switchTab({ url: '/pages/home/home' });
      }
    }, 400);
  },

  onProtocol() {
    ui.confirm({
      title: '用户协议',
      content:
        '本工具仅用于量化策略的模拟跟踪与数据查看，所有行情与收益数据不构成任何投资建议。请勿将其用于真实交易决策。',
      confirmText: '我知道了',
      cancelText: '关闭'
    });
  }
});

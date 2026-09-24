/**
 * 登录态与登出流程 —— 对齐 Web 端行为：
 *   - 未登录访问任意业务页 → 引导到登录页（并记住来源，登录后回跳）；
 *   - 退出登录调 /api/user/logout 清 Redis token，并清本地会话与所有缓存；
 *   - 退出登录只影响当前会话，不影响其他用户数据。
 */

const store = require('./store');
const api = require('./api');
const ui = require('./ui');

/** 登录态守卫：未登录则跳登录页并返回 false */
function guard() {
  if (store.isLoggedIn()) return true;
  wx.reLaunch({ url: '/pages/login/login' });
  return false;
}

/**
 * 跳转登录页并记录来源（登录成功后回跳）
 * @param {string} redirect 回跳路径（含 query 的完整 url）
 */
function toLogin(redirect) {
  const app = getApp();
  if (app) app.globalData.loginRedirect = redirect || '';
  wx.reLaunch({ url: '/pages/login/login' });
}

/** 登录成功后取出并清空回跳路径 */
function takeRedirect() {
  const app = getApp();
  const target = (app && app.globalData.loginRedirect) || '';
  if (app) app.globalData.loginRedirect = '';
  return target;
}

/** 退出登录（含二次确认） */
function logout() {
  return ui
    .confirm({ title: '退出登录', content: '确定要退出当前账号吗？', confirmText: '退出' })
    .then((ok) => {
      if (!ok) return false;
      return api.user
        .logout()
        .catch(() => null)
        .then(() => {
          store.clearSession();
          ui.toast('已退出登录');
          setTimeout(() => {
            wx.reLaunch({ url: '/pages/login/login' });
          }, 400);
          return true;
        });
    });
}

/** 写入登录结果到会话（对齐 login_page.py 写入的会话键） */
function persistLogin(result, account) {
  const data = result || {};
  store.setSession({
    token: String(data.token || ''),
    account: String(data.account || account || ''),
    username: String(data.account || account || ''),
    userId: String(data.user_id || data.id || ''),
    role: data.role === 0 ? 0 : 1,
    phone: String(data.phone || ''),
    email: String(data.email || '')
  });
  return store.getSession();
}

module.exports = { guard, toLogin, takeRedirect, logout, persistLogin };

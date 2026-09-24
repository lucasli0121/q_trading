/**
 * 主题管理（浅色 / 深色）—— 对齐 q_trading_cli/colors/theme.py 的行为：
 *
 *   1. 取值是小写 "light" / "dark"；
 *   2. 判定「用户是否真的选过主题」不能只看 theme_mode，必须看偏好响应里的
 *      id / update_time 是否同时为空 —— 后端在用户从未保存过偏好时会返回模型默认值
 *      "light"，那不是用户的选择（resolve_mode_from_prefs 的规则）；
 *   3. 切换主题**即时生效并立即落库**（不需要点「保存更改」），
 *      其余偏好项才走保存按钮；
 *   4. 主题归属是会话级的（同一个账号在不同设备互不影响）。
 *
 * 页面接入方式：在 onShow 里调 theme.applyTo(this)，页面 data 会拿到 themeClass。
 */

const store = require('./store');
const api = require('./api');
const { chrome } = require('./system');

const listeners = [];

/** 当前模式 */
function current() {
  return store.getThemeMode();
}

/** 根节点 class */
function themeClass(mode) {
  return (mode || current()) === 'dark' ? 'theme-dark' : 'theme-light';
}

/** 从后端偏好里解析模式；返回 null 表示「用户没选过」 */
function resolveFromPrefs(prefs) {
  if (!prefs || typeof prefs !== 'object') return null;
  // id 与 update_time 同时为空 → 从未保存过偏好
  if (!prefs.id && !prefs.update_time) return null;
  const mode = String(prefs.theme_mode || '').trim().toLowerCase();
  if (mode === 'dark' || mode === 'light') return mode;
  return null;
}

/** 冷启动时补一次：本地没有显式选择过（首次进入）则尝试从后端偏好恢复 */
function bootstrap() {
  return api.user
    .getPreference()
    .then((prefs) => {
      const mode = resolveFromPrefs(prefs);
      if (mode) store.setThemeMode(mode);
      return prefs;
    })
    .catch(() => null);
}

/** 立即切换（本地生效 + 通知订阅者），返回新模式 */
function setMode(mode) {
  const m = store.setThemeMode(mode);
  listeners.forEach((fn) => {
    try {
      fn(m);
    } catch (e) {
      /* ignore */
    }
  });
  return m;
}

function toggle() {
  return setMode(current() === 'dark' ? 'light' : 'dark');
}

/**
 * 切换主题并落库（对齐 settings_preferences.py::on_toggle_dark）
 * 失败时只提示「未能保存到服务器」，本地仍然生效。
 */
function toggleAndPersist(currentPrefs) {
  const mode = toggle();
  const label = mode === 'dark' ? '深色' : '浅色';
  const base = currentPrefs || {};
  const payload = {
    theme_mode: mode,
    enable_wx_push: !!base.enable_wx_push,
    wx_push_url: base.wx_push_url || '',
    enable_phone_text: !!base.enable_phone_text,
    phone: base.phone || ''
  };
  return api.user
    .updatePreference(payload)
    .then(() => {
      wx.showToast({ title: `已切换到${label}主题`, icon: 'none' });
      return mode;
    })
    .catch(() => {
      wx.showToast({ title: `已切换到${label}主题（未能保存到服务器）`, icon: 'none' });
      return mode;
    });
}

/** 把主题与窗口信息写进页面 data */
function applyTo(page) {
  if (!page || !page.setData) return;
  page.setData({
    themeClass: themeClass(),
    statusBarHeight: chrome().statusBarHeight,
    navbarHeight: chrome().navbarHeight,
    safeBottom: chrome().safeBottom
  });
}

function subscribe(fn) {
  listeners.push(fn);
  return () => {
    const i = listeners.indexOf(fn);
    if (i >= 0) listeners.splice(i, 1);
  };
}

module.exports = {
  current,
  themeClass,
  resolveFromPrefs,
  bootstrap,
  setMode,
  toggle,
  toggleAndPersist,
  applyTo,
  subscribe
};

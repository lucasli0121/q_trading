/**
 * 全局会话与缓存（轻量 store）
 *
 * 设计取舍：
 *   - 会话（token / 用户信息 / 主题）落 Storage，冷启动即可恢复；
 *   - 「用户策略 / 策略模板 / 股票池」这三份是全页面高频读的数据，
 *     放在内存里并按 60 秒节流刷新，语义对齐 q_trading_cli 的
 *     UserStrategyManager（RELOAD_MIN_INTERVAL_SECONDS = 60）与 strategy_cache。
 */

const SESSION_KEY = 'qt_session_v1';
const MODE_KEY = 'qt_theme_mode';

const state = {
  session: null,
  // 缓存
  userStrategies: { list: [], loadedAt: 0 },
  strategyTemplates: { map: {}, loadedAt: 0 },
  pools: { list: [], loadedAt: 0 },
  // 交易信号（内存滚动窗口，最新在前）
  signals: []
};

const CACHE_TTL = 60000;

/* ------------------------------------------------------------ 会话 */

function getSession() {
  if (state.session) return state.session;
  try {
    state.session = wx.getStorageSync(SESSION_KEY) || null;
  } catch (e) {
    state.session = null;
  }
  return state.session;
}

function setSession(session) {
  state.session = session || null;
  try {
    if (session) wx.setStorageSync(SESSION_KEY, session);
    else wx.removeStorageSync(SESSION_KEY);
  } catch (e) {
    /* 存储失败不影响内存会话 */
  }
}

function getToken() {
  const s = getSession();
  return (s && s.token) || '';
}

function isLoggedIn() {
  return !!getToken();
}

function getUser() {
  return getSession() || {};
}

function clearSession() {
  setSession(null);
  clearCaches();
}

/* ------------------------------------------------------------ 主题 */

function getThemeMode() {
  try {
    return wx.getStorageSync(MODE_KEY) || 'light';
  } catch (e) {
    return 'light';
  }
}

function setThemeMode(mode) {
  const m = mode === 'dark' ? 'dark' : 'light';
  try {
    wx.setStorageSync(MODE_KEY, m);
  } catch (e) {
    /* ignore */
  }
  return m;
}

/* ------------------------------------------------------------ 缓存 */

function isFresh(bucket) {
  return bucket && Date.now() - bucket.loadedAt < CACHE_TTL;
}

function setUserStrategies(list) {
  state.userStrategies = { list: list || [], loadedAt: Date.now() };
}

function getUserStrategies() {
  return state.userStrategies.list || [];
}

function setStrategyTemplates(list) {
  const map = {};
  (list || []).forEach((t) => {
    const key = String((t && (t._id || t.id)) || '');
    if (key) map[key] = t;
  });
  state.strategyTemplates = { map, loadedAt: Date.now() };
}

function getStrategyTemplate(id) {
  return state.strategyTemplates.map[String(id || '')] || null;
}

function setPools(list) {
  state.pools = { list: list || [], loadedAt: Date.now() };
}

function getPools() {
  return state.pools.list || [];
}

function clearCaches() {
  state.userStrategies = { list: [], loadedAt: 0 };
  state.strategyTemplates = { map: {}, loadedAt: 0 };
  state.pools = { list: [], loadedAt: 0 };
  state.signals = [];
}

/* ------------------------------------------------------------ 信号窗口 */

/** 往信号窗口里合并一批信号（按 id 去重，无 id 时用「时间+代码+方向+用户策略」组合键），最新在前 */
function mergeSignals(list) {
  // ⚠️ 组合键里必须用 user_strategy_id（用户策略 ID），不能用 strategy_id（策略模板 ID）：
  //    同一模板被多个用户策略引用时，后者会让不同策略的同名信号被误判为重复而去重掉。
  const keyOf = (s) =>
    String(
      (s && (s.id || s._id)) ||
        `${s && s.create_time}|${s && s.stock_code}|${s && s.action}|${s && s.user_strategy_id}`
    );
  const seen = {};
  const merged = [];
  const push = (s) => {
    if (!s) return;
    const k = keyOf(s);
    if (seen[k]) return;
    seen[k] = 1;
    merged.push(s);
  };
  (list || []).forEach(push);
  state.signals.forEach(push);
  merged.sort((a, b) => String(b.create_time || '').localeCompare(String(a.create_time || '')));
  state.signals = merged.slice(0, 1000);
  return state.signals;
}

function getSignals() {
  return state.signals;
}

/* ------------------------------------------------------------ 用户策略派生统计 */

/** 策略总数 —— 对齐 get_strategy_count（缓存长度） */
function countStrategies() {
  return getUserStrategies().length;
}

/** 运行中数量 —— 严格等于 running */
function countRunning() {
  return getUserStrategies().filter((s) => s.status === 'running').length;
}

/** 本月新增 —— create_time 字符串前 7 位等于本地 %Y-%m */
function countMonthNew() {
  const prefix = require('./format').monthPrefix();
  return getUserStrategies().filter((s) =>
    String(s.create_time || '').startsWith(prefix)
  ).length;
}

module.exports = {
  getSession,
  setSession,
  getToken,
  isLoggedIn,
  getUser,
  clearSession,
  getThemeMode,
  setThemeMode,
  isFresh,
  setUserStrategies,
  getUserStrategies,
  setStrategyTemplates,
  getStrategyTemplate,
  setPools,
  getPools,
  clearCaches,
  mergeSignals,
  getSignals,
  countStrategies,
  countRunning,
  countMonthNew,
  CACHE_TTL
};

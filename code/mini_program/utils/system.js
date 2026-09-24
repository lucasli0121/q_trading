/**
 * 窗口与安全区信息 —— 只在启动时取一次，缓存复用。
 *
 * 为什么不用 CSS 的 env(safe-area-inset-top)：
 *   Android 微信返回 0，iOS 返回刘海高度，直接用会导致顶部导航在两端高度不一致。
 *   这里统一用 wx.getWindowInfo().statusBarHeight 作为状态栏高度，
 *   底部安全区用 screenHeight - safeArea.bottom 计算。
 */

let cached = null;

function getSystem() {
  if (cached) return cached;

  let info = {};
  try {
    // 新版基础库
    info = (wx.getWindowInfo && wx.getWindowInfo()) || {};
  } catch (e) {
    info = {};
  }
  if (!info.statusBarHeight) {
    try {
      info = wx.getSystemInfoSync() || {};
    } catch (e) {
      info = {};
    }
  }

  const statusBarHeight = info.statusBarHeight || 20;
  let safeBottom = 0;
  if (info.safeArea && info.screenHeight) {
    safeBottom = Math.max(0, info.screenHeight - info.safeArea.bottom);
  }

  cached = {
    statusBarHeight,
    safeBottom,
    windowWidth: info.windowWidth || 375,
    windowHeight: info.windowHeight || 667,
    pixelRatio: info.pixelRatio || 2,
    platform: info.platform || ''
  };
  return cached;
}

/** 顶部导航整体高度（状态栏 + 56pt 导航条，56pt = 112rpx ≈ 56px×像素比换算） */
function navbarHeightPx() {
  return getSystem().statusBarHeight + 44;
}

/** 注入到页面的 chrome 数据（供 wxss 内联变量使用） */
function chrome() {
  const s = getSystem();
  return {
    statusBarHeight: s.statusBarHeight,
    safeBottom: s.safeBottom,
    navbarHeight: navbarHeightPx()
  };
}

module.exports = { getSystem, navbarHeightPx, chrome };

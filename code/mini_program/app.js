/**
 * QTrade 量化小程序 · 应用入口
 *
 * 启动流程：
 *   1. 采集窗口 / 安全区信息（供自定义导航栏与 TabBar 使用）
 *   2. 恢复本地会话（token / 用户信息 / 主题）
 *   3. 已登录且本地没有显式选过主题时，尝试从后端偏好恢复一次主题
 */

const store = require('./utils/store');
const theme = require('./utils/theme');
const { getSystem } = require('./utils/system');

App({
  globalData: {
    // 登录后回跳路径
    loginRedirect: '',
    system: null
  },

  onLaunch() {
    this.globalData.system = getSystem();

    // 恢复会话（store 内部已从 Storage 读取，这里只是预热）
    const session = store.getSession();
    if (session && session.token) {
      // 已登录：补一次主题偏好（失败静默，保持本地主题）
      theme.bootstrap();
    }
  },

  onShow() {
    // 从后台回到前台时不做强制刷新，由各页面 onShow 自行处理
  },

  onError(err) {
    // 统一收敛未捕获异常，避免开发者工具频繁弹出错误栈
    console.error('[QTrade] 未捕获异常:', err);
  },

  onUnhandledRejection(res) {
    console.warn('[QTrade] 未处理的 Promise 拒绝:', res && res.reason);
  }
});

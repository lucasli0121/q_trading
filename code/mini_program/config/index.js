/**
 * 全局配置 —— 所有环境相关参数集中在此文件，切环境只改这里。
 *
 * ⚠️ 上线前必读：
 *   微信小程序真机 / 线上环境**只允许 HTTPS**，且域名必须在小程序后台
 *   「开发管理 → 开发设置 → 服务器域名」里配置（request 合法域名 / socket 合法域名）。
 *   当前 BASE_URL 是 http 的测试地址，仅能在「微信开发者工具 → 详情 → 本地设置 →
 *   不校验合法域名...」勾选后联调，真机与体验版一律不通。
 *   上线步骤见 doc/上线步骤.md。
 */

// 后端 FastAPI 服务地址（q_trading_server，默认监听 8000）
const BASE_URL = 'http://example.com:8000';

// 交易信号 WebSocket 网关（后端需实现，见 doc/接口契约.md「小程序新增接口」）
// 为空字符串时不启用 WS，自动降级为 HTTP 轮询。
const WS_URL = 'ws://example.com:8000/ws/trade_signal';

module.exports = {
  BASE_URL,
  WS_URL,

  // 微信小程序 appId：上线前替换为真实 appId（同时改 project.config.json）
  WX_APPID: '',

  // 单次请求超时（毫秒）
  REQUEST_TIMEOUT: 20000,

  // 分页
  PAGE_SIZE: 10,

  // 各类轮询间隔（毫秒）—— 对齐 Web 端《页面操作文档》第 6 节「数据刷新周期一览」
  POLL: {
    monitor: 3000,      // 策略监控：信号日志（Web 为 1s，小程序弱网下调至 3s）
    homeSignal: 10000,  // 仪表盘：今日信号数 + 通知角标（Web 为 3s / 10s）
    homeHeavy: 60000,   // 仪表盘：累计收益图 + 近期更新策略表（Web 为 60s）
    quotes: 15000,      // 行情列表自动刷新
    userStrategy: 60000 // 用户策略缓存节流（Web 为 60s）
  },

  // 交易信号保留窗口（小时）—— 对齐 Web 端 SignalManager.SIGNAL_RETENTION_HOURS
  SIGNAL_RETENTION_HOURS: 12,

  // 个股分析取数天数
  ANALYSIS_DAYS: 15,

  // 分页上限（后端多数接口默认 limit=100，需显式传大值避免被截断）
  MAX_LIMIT: 5000
};

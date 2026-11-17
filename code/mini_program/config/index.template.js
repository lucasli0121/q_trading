/**
 * 全局配置模板 —— 本文件入库，但**不要在这里写真实地址**。
 *
 * 构建时由 tools/gen-config.js 把 __BASE_URL__ / __WS_URL__ / __WX_APPID__
 * 替换为实际值，生成 config/index.js（该产物已被 .gitignore 排除，不入库）。
 *
 * 取值优先级：环境变量 > config/.env.local > 脚本缺省值（http://127.0.0.1:8000）。
 * 非环境类常量（分页、轮询间隔等）直接维护在本模板里。
 *
 * ⚠️ 上线前必读：
 *   微信小程序真机 / 线上环境**只允许 HTTPS/WSS**，且域名必须在小程序后台
 *   「开发管理 → 开发设置 → 服务器域名」里配置（request 合法域名 / socket 合法域名）。
 *   生成时若 MP_ENV=production，脚本会强制校验地址是否为 https/wss。
 *   上线步骤见 doc/上线步骤.md。
 */

// 后端 FastAPI 服务地址（q_trading_server，默认监听 8000）
const BASE_URL = '__BASE_URL__';

// 交易信号 WebSocket 网关（后端需实现，见 doc/接口契约.md「小程序新增接口」）
// 为空字符串时不启用 WS，自动降级为 HTTP 轮询。
const WS_URL = '__WS_URL__';

module.exports = {
  BASE_URL,
  WS_URL,

  // 微信小程序 appId：上线前替换为真实 appId（同时改 project.config.json）
  WX_APPID: '__WX_APPID__',

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

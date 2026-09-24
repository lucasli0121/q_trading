/**
 * 业务常量与枚举 —— 取值严格对齐 q_trading_cli / q_trading_server 的真实字符串，
 * 不做任何「好看化」改写，避免与后端契约不一致。
 */

/** 用户策略状态（UserStrategy.status）—— 英文 key，中文映射见 statusText */
const STRATEGY_STATUS = {
  RUNNING: 'running',
  STOPPED: 'stopped',
  PAUSED: 'paused',
  ERROR: 'error'
};

const STRATEGY_STATUS_OPTIONS = [
  { value: '全部状态', label: '全部状态' },
  { value: '运行中', label: '运行中' },
  { value: '已停止', label: '已停止' },
  { value: '已暂停', label: '已暂停' },
  { value: '异常', label: '异常' }
];

/** 状态 → (显示文本, 语义色 key) —— 对齐 pages/strategies.py::_STATUS_STYLES */
const STATUS_STYLE = {
  running: { text: '运行中', tone: 'down', bg: 'status-success' },
  stopped: { text: '已停止', tone: 'second', bg: 'neutral' },
  paused: { text: '已暂停', tone: 'warn', bg: 'warn' },
  error: { text: '异常', tone: 'up', bg: 'up' }
};

function statusStyle(status) {
  return STATUS_STYLE[status] || { text: '未知', tone: 'second', bg: 'neutral' };
}

/**
 * 股票池类型。
 * ⚠️ 后端 PoolInfo 目前**没有 type 字段**（web_api/models.py:90-102），
 * 创建接口也不接受 type，因此真机上恒为「手动维护」。
 * 这里保留完整枚举，等后端补字段后无需改页面逻辑。
 */
const POOL_TYPES = ['自动同步', '条件筛选', '系统自动', '手动维护'];

const POOL_TYPE_META = {
  自动同步: { icon: '⟳', update: '每日更新' },
  条件筛选: { icon: '⚙', update: '盘后更新' },
  系统自动: { icon: '★', update: '实时同步' },
  手动维护: { icon: '✎', update: '手动更新' }
};

function poolTypeMeta(type) {
  return POOL_TYPE_META[type] || POOL_TYPE_META['手动维护'];
}

/** 交易方向 —— 后端只有这两个中文值（models.py:432） */
const ACTION = {
  BUY: '买入',
  SELL: '卖出'
};

/** 信号筛选标签 —— 对齐 pages/strategy_monitor.py:68-72 */
const SIGNAL_FILTERS = ['全部', '买入', '卖出'];

/** 行情列表筛选 chip —— 对齐设计稿「搜索·筛选」页 */
const QUOTE_FILTERS = ['全部', '涨幅榜', '跌幅榜', '成交额'];

/** 个股详情 K 线周期 */
const KLINE_PERIODS = [
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: 'month', label: '月K' }
];

/** 个股分析图表模式 —— 对齐 pages/stock_quotes.py（日K / 分时） */
const CHART_MODES = [
  { key: 'day', label: '日K' },
  { key: 'minute', label: '分时' }
];

/** 仪表盘累计收益周期 —— 对齐 pages/main_page.py::_PERIOD_DAYS */
const PROFIT_PERIODS = [
  { key: '7', label: '7天', days: 7 },
  { key: '30', label: '30天', days: 30 },
  { key: '365', label: '1年', days: 365 }
];

/** 策略模板默认参数分组标题 —— 对齐 pages/strategies.py::_PARAM_GROUP_LABELS */
const PARAM_GROUP_LABELS = {
  match: '选股参数',
  buy: '买入参数',
  sell: '卖出参数'
};

/** 个人喜好可提交字段白名单 —— 对齐 settings_preferences.py::_PREF_FIELDS */
const PREF_FIELDS = ['theme_mode', 'enable_wx_push', 'wx_push_url', 'enable_phone_text', 'phone'];

/** 主题模式取值（全小写） */
const THEME = {
  LIGHT: 'light',
  DARK: 'dark'
};

/** 订单动作仅这两种（OrderInfo.action） */
const ORDER_ACTION = { BUY: '买入', SELL: '卖出' };

/** 底部 Tab 定义（顺序与 app.json tabBar.list 一致） */
const TABS = [
  { path: '/pages/home/home', text: '首页', icon: '⌂' },
  { path: '/pages/quotes/quotes', text: '行情', icon: '≣' },
  { path: '/pages/strategies/strategies', text: '策略', icon: '◈' },
  { path: '/pages/profile/profile', text: '我的', icon: '☺' }
];

module.exports = {
  STRATEGY_STATUS,
  STRATEGY_STATUS_OPTIONS,
  STATUS_STYLE,
  statusStyle,
  POOL_TYPES,
  POOL_TYPE_META,
  poolTypeMeta,
  ACTION,
  SIGNAL_FILTERS,
  QUOTE_FILTERS,
  KLINE_PERIODS,
  CHART_MODES,
  PROFIT_PERIODS,
  PARAM_GROUP_LABELS,
  PREF_FIELDS,
  THEME,
  ORDER_ACTION,
  TABS
};

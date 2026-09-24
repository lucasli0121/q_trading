/**
 * 数值 / 文本格式化 —— 严格对齐 ui/设计交付说明.md 第 7 节「数值与文本格式规范」。
 *
 * 两条极易踩坑的缩放规则（来自 q_trading_cli 源码实证）：
 *   1. 行情类字段（change_percent / change_amt / turnover / amp）后端返回的是**小数比率**
 *      （0.0076972 表示 0.77%），展示前必须 ×100。
 *   2. 财务类字段 net_profit_growth_rate **已经是百分数**，不能再乘。
 * 因此本文件把两者拆成不同函数名（fmtPctRatio / fmtPctRaw），调用点必须显式选择。
 */

/** 安全转 number */
function sf(v) {
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

/** 价格：保留 2 位小数，**不加千分位**（1682.50） */
function price(v) {
  return sf(v).toFixed(2);
}

/** 价格（含千分位，用于成交额等金额场景） */
function priceGroup(v) {
  return sf(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 金额 / 数量：千分位整数（1,000,000） */
function money(v) {
  return Math.round(sf(v)).toLocaleString('en-US');
}

/** 带符号数值：+23.80 / -1.08 */
function signed(v, digits = 2) {
  const n = sf(v);
  const s = n.toFixed(digits);
  return n >= 0 ? `+${s}` : s;
}

/**
 * 涨跌幅 —— 入参为后端的小数比率，内部 ×100 后输出「符号 + 2 位小数 + %」
 * @param {number} ratio 0.0231 → "+2.31%"
 */
function fmtPctRatio(ratio, digits = 2) {
  return fmtPctRaw(sf(ratio) * 100, digits);
}

/**
 * 百分数直出 —— 入参已是百分数（如 net_profit_growth_rate = 12.5）
 * @param {number} pct 12.5 → "+12.50%"
 */
function fmtPctRaw(pct, digits = 2) {
  const n = sf(pct);
  const s = n.toFixed(digits);
  return n >= 0 ? `+${s}%` : `${s}%`;
}

/** 百分数直出（不带符号，用于换手率等无方向含义的指标） */
function pctPlain(pct, digits = 2) {
  return `${sf(pct).toFixed(digits)}%`;
}

/** 涨跌额 —— 入参为后端返回的绝对变动值，输出「符号 + 2 位小数」 */
function fmtChange(v, digits = 2) {
  return signed(v, digits);
}

/** 大市值：亿为单位，整数四舍五入（21145亿） */
function fmtCapYi(v) {
  const n = sf(v);
  if (!n) return '--';
  return `${Math.round(n)}亿`;
}

/** 动态市盈率：负值显示「亏损」，缺失显示「--」—— 对齐 pages/stock_quotes.py:1066 */
function fmtPe(v) {
  const n = sf(v);
  if (!v && v !== 0) return '--';
  if (n < 0) return '亏损';
  return n.toFixed(1);
}

/** 收益率（已是百分数，带符号，1 位小数）—— 仪表盘「收益率 (30d)」列 */
function fmtRatePct(v, digits = 1) {
  if (v === null || v === undefined || v === '') return '-';
  return `${signed(v, digits)}%`;
}

/** 判色：>0 涨（红），<0 跌（绿），=0 中性 —— A 股约定 */
function toneOf(v) {
  const n = sf(v);
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'flat';
}

/** 涨跌对应的文字色 className */
function toneClass(v) {
  const t = toneOf(v);
  if (t === 'up') return 'c-up';
  if (t === 'down') return 'c-down';
  return 'c-second';
}

/** 时间截断：YYYY-MM-DD HH:MM:SS → YYYY-MM-DD HH:MM */
function shortTime(v, len = 16) {
  const s = String(v || '');
  return s.length >= len ? s.slice(0, len) : s;
}

/** 只取日期 YYYY-MM-DD */
function dateOnly(v) {
  const s = String(v || '');
  return s.length >= 10 ? s.slice(0, 10) : s;
}

/** 从 record 中提取日期（兼容 date / trade_date / create_time / time） */
function recordDate(item) {
  for (const k of ['date', 'trade_date', 'create_time', 'time']) {
    const v = String((item && item[k]) || '');
    if (v) return v.slice(0, 10);
  }
  return '';
}

/** 今日日期 YYYY-MM-DD（本地时区） */
function today() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 本月前缀 YYYY-MM（本地时区）—— 对齐 user_strategy_manager.get_month_new_count */
function monthPrefix() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}`;
}

/** N 天前的 YYYY-MM-DD */
function daysAgo(n) {
  const d = new Date(Date.now() - n * 86400000);
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 手机号中间 4 位打码 */
function maskPhone(phone) {
  const s = String(phone || '').replace(/[\s-]/g, '');
  if (!s) return '';
  if (s.length < 7) return s;
  return `${s.slice(0, 3)}****${s.slice(-4)}`;
}

/** 判断是否为买入方向（兼容 买入/B/BUY/buy） */
function isBuyAction(action) {
  const s = String(action || '');
  return s.indexOf('买') >= 0 || s.toLowerCase().indexOf('buy') >= 0;
}

/** 判断是否买入（严格等于「买入」）—— 用于统计口径 */
function isStrictBuy(action) {
  return String(action || '') === '买入';
}

/** 股票代码规范化：去掉交易所后缀（600519.SH → 600519）用于展示 */
function bareCode(code) {
  return String(code || '').split('.')[0];
}

/** 股票代码用于比较：统一大写去空白 */
function normCode(code) {
  return String(code || '').trim().toUpperCase();
}

/** 金额（元）展示：≥1 万按千分位，否则 2 位小数 */
function fmtAmount(v) {
  const n = sf(v);
  if (Math.abs(n) >= 10000) return money(n);
  return n.toFixed(2);
}

module.exports = {
  sf,
  price,
  priceGroup,
  money,
  signed,
  fmtPctRatio,
  fmtPctRaw,
  pctPlain,
  fmtChange,
  fmtCapYi,
  fmtPe,
  fmtRatePct,
  toneOf,
  toneClass,
  shortTime,
  dateOnly,
  recordDate,
  today,
  monthPrefix,
  daysAgo,
  maskPhone,
  isBuyAction,
  isStrictBuy,
  bareCode,
  normCode,
  fmtAmount
};

/**
 * 图表辅助 —— canvas 无法解析 CSS 变量，必须把真实色值传进绘制层。
 * 色值映射对齐 q_trading_cli/colors/theme.py::get_chart_colors()。
 */

const UP_LIGHT = '#DC2626';
const UP_DARK = '#F87171';
const DOWN_LIGHT = '#059669';
const DOWN_DARK = '#34D399';

/**
 * 取当前主题下的图表配色
 * @param {string} mode 'light' | 'dark'
 */
function chartColors(mode) {
  const dark = mode === 'dark';
  return {
    up: dark ? UP_DARK : UP_LIGHT,
    down: dark ? DOWN_DARK : DOWN_LIGHT,
    axis: dark ? '#9CA3AF' : '#6B7280',
    grid: dark ? '#2D3748' : '#EEF2F6',
    axisLine: dark ? '#334155' : '#E5E7EB',
    line: dark ? '#3B82F6' : '#2563EB',
    volume: dark ? '#4B5563' : '#9CA3AF',
    areaTop: dark ? 'rgba(59,130,246,0.30)' : 'rgba(37,99,235,0.28)',
    areaBottom: dark ? 'rgba(59,130,246,0)' : 'rgba(37,99,235,0)',
    text: dark ? '#E5E7EB' : '#111827',
    sub: dark ? '#6B7280' : '#9CA3AF'
  };
}

/** 取数值区间（带 6% 上下留白） */
function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  (values || []).forEach((v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return;
    if (n < min) min = n;
    if (n > max) max = n;
  });
  if (min === Infinity) return { min: 0, max: 1 };
  if (min === max) {
    const pad = Math.abs(min) * 0.02 || 1;
    return { min: min - pad, max: max + pad };
  }
  const pad = (max - min) * 0.06;
  return { min: min - pad, max: max + pad };
}

/** 线性映射：值 → y 像素 */
function scaleY(value, min, max, top, height) {
  if (max === min) return top + height / 2;
  const ratio = (Number(value) - min) / (max - min);
  return top + height * (1 - ratio);
}

/** 生成 N 条水平网格线的 y 坐标（含首尾） */
function gridLines(top, height, count) {
  const out = [];
  const n = count || 4;
  for (let i = 0; i <= n; i += 1) {
    out.push(top + (height * i) / n);
  }
  return out;
}

/** 把 series 重新采样成固定列数，便于在手机上画出可辨识的蜡烛 */
function sliceTail(list, max) {
  if (!list || list.length <= max) return list || [];
  return list.slice(list.length - max);
}

module.exports = { chartColors, extent, scaleY, gridLines, sliceTail };

/**
 * 策略状态徽标 —— 对齐 q_trading_cli/pages/strategies.py::_STATUS_STYLES
 *   running 绿 + 呼吸圆点 / stopped 灰 / paused 黄 / error 红
 */
const { statusStyle } = require('../../utils/constants');

Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    status: { type: String, value: 'stopped' },
    /** false 时不显示呼吸圆点（表格紧凑场景） */
    dot: { type: Boolean, value: true }
  },

  data: {
    text: '未知',
    tone: 'second'
  },

  observers: {
    status(v) {
      const s = statusStyle(v);
      this.setData({ text: s.text, tone: s.tone });
    }
  }
});

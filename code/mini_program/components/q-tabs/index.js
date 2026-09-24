/**
 * 横向标签栏 —— 用于股票池切换、策略标签、信号筛选、周期切换。
 * 两种外观：
 *   chip      胶囊 chip（筛选 chip h28、选中主色底）
 *   underline 下划线（股票池 Tab，选中主色文字 + 2px 底线）
 */
Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    /** [{key, label}] */
    list: { type: Array, value: [] },
    active: { type: String, value: '' },
    /** chip | underline */
    variant: { type: String, value: 'chip' },
    /** 是否横向滚动，false 时等分铺满 */
    scroll: { type: Boolean, value: true }
  },

  methods: {
    onTap(e) {
      const key = String(e.currentTarget.dataset.key);
      if (key === this.properties.active) return;
      this.triggerEvent('change', { key });
    }
  }
});

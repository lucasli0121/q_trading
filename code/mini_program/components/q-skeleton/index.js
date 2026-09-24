/**
 * 骨架屏 —— 形状与真实内容一致，保持布局不跳动。
 * 由页面在首屏请求期间自行控制显示时机（设计规范：≥300ms 才显示，
 * 因此页面里用 setTimeout 300ms 后再 setData 显示）。
 */
Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    /** list | quote | stat | card */
    type: { type: String, value: 'list' },
    rows: { type: Number, value: 6 }
  },

  data: {
    items: []
  },

  observers: {
    rows(v) {
      const n = Math.max(1, Math.min(20, Number(v) || 6));
      this.setData({ items: new Array(n).fill(0).map((_, i) => i) });
    }
  },

  lifetimes: {
    attached() {
      const n = Math.max(1, Math.min(20, Number(this.properties.rows) || 6));
      this.setData({ items: new Array(n).fill(0).map((_, i) => i) });
    }
  }
});

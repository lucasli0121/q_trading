/**
 * 空态 / 加载中 / 加载失败 / 无网络 占位
 * 覆盖 ui/设计交付说明.md 第 5 节「状态矩阵」中的全部占位类状态。
 */
Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    /** empty | loading | error | offline | forbidden */
    type: { type: String, value: 'empty' },
    title: { type: String, value: '' },
    desc: { type: String, value: '' },
    /** 主操作文案，为空则不显示按钮 */
    action: { type: String, value: '' },
    /** 图标用字形高度 */
    compact: { type: Boolean, value: false }
  },

  data: {
    defaults: {
      empty: { title: '暂无数据', desc: '' },
      loading: { title: '加载中...', desc: '' },
      error: { title: '加载失败', desc: '请稍后重试' },
      offline: { title: '网络不可用', desc: '请检查网络后重试' },
      forbidden: { title: '没有访问权限', desc: '请使用其他账号或返回首页' }
    }
  },

  methods: {
    onAction() {
      this.triggerEvent('action');
    }
  }
});

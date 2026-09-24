/**
 * 顶部导航栏 —— h56pt（112rpx），标题居中，返回 / 右侧操作热区 44×44pt。
 *
 * 状态栏高度由外部传入 statusBarHeight（px），不使用 CSS env()，
 * 避免 Android / iOS 表现不一致（见 utils/system.js 注释）。
 */
Component({
  options: {
    styleIsolation: 'apply-shared',
    multipleSlots: true
  },

  properties: {
    title: { type: String, value: '' },
    /** 是否显示返回箭头 */
    back: { type: Boolean, value: true },
    /** 状态栏高度（px） */
    statusBarHeight: { type: Number, value: 20 },
    /** 右侧操作文案，如「创建策略」 */
    action: { type: String, value: '' },
    /** 右侧是否显示 ＋ */
    actionAdd: { type: Boolean, value: true },
    /** 右侧是否为纯图标按钮（如刷新） */
    actionIcon: { type: String, value: '' },
    /** 是否占位（占位后页面内容自然下移） */
    placeholder: { type: Boolean, value: true }
  },

  methods: {
    onBack() {
      const pages = getCurrentPages();
      if (pages.length > 1) {
        wx.navigateBack();
      } else {
        wx.switchTab({ url: '/pages/home/home' });
      }
      this.triggerEvent('back');
    },

    onAction() {
      this.triggerEvent('action');
    },

    onActionIcon() {
      this.triggerEvent('actionicon');
    }
  }
});

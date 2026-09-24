Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  data: {
    selected: 0,
    tabs: [
      { path: '/pages/home/home', text: '首页', icon: 'home' },
      { path: '/pages/quotes/quotes', text: '行情', icon: 'quotes' },
      { path: '/pages/strategies/strategies', text: '策略', icon: 'strategy' },
      { path: '/pages/profile/profile', text: '我的', icon: 'profile' }
    ],
    // 内联主题变量
    themeStyle: ''
  },

  lifetimes: {
    attached() {
      this.syncTheme();
    }
  },

  pageLifetimes: {
    show() {
      this.syncTheme();
    }
  },

  methods: {
    /**
     * 同步主题。
     *
     * 为什么用内联变量而不是依赖 app.wxss 的 .root 变量：
     * custom-tab-bar 是页面级组件，**不在**页面 WXML 里那个 .root 视图的子树中，
     * 因此 .root.theme-dark 上定义的自定义属性无法继承到它。
     * 这里显式取一次 Token 值写进内联样式，切换主题即时生效。
     */
    syncTheme() {
      const store = require('../utils/store');
      const dark = store.getThemeMode() === 'dark';
      const token = dark
        ? {
            bg: '#111827',
            border: '#334155',
            color: '#4B5563',
            active: '#3B82F6'
          }
        : {
            bg: '#FFFFFF',
            border: '#E5E7EB',
            color: '#9CA3AF',
            active: '#2563EB'
          };
      this.setData({
        themeStyle: `--tb-bg:${token.bg};--tb-border:${token.border};--tb-color:${token.color};--tb-active:${token.active};`
      });
    },

    onTap(e) {
      const index = Number(e.currentTarget.dataset.index);
      const tab = this.data.tabs[index];
      if (!tab || index === this.data.selected) return;
      wx.switchTab({ url: tab.path });
    }
  }
});

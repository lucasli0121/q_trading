/**
 * 行情行 —— 行情列表 / 搜索结果 / 策略选股共用。
 *
 * 说明：之所以让**页面**算好 vm（视图模型）再传进来，而不是组件内部格式化，
 * 是因为小程序 WXML 不能直接调用 JS 函数；把格式化集中在页面里也能保证
 * 三处列表的数值口径完全一致（涨跌幅 ×100、利润增长率不再乘等）。
 *
 * vm = {
 *   code, name,                 // 代码 / 名称
 *   price, priceClass,          // 最新价（2 位小数，涨红跌绿）
 *   pct, pctClass,              // 涨跌幅（符号 + 2 位小数 + %）
 *   amt, amtClass,              // 涨跌额（符号 + 2 位小数）
 *   turnover, pe, growth,       // 换手率 / 动态市盈率 / 利润增长率
 *   score                       // 选股评分（策略选股页）
 * }
 */
Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    vm: { type: Object, value: null },
    /** 是否展示「选股评分」列 */
    showScore: { type: Boolean, value: false },
    /** 是否展示行尾操作按钮 */
    showAction: { type: Boolean, value: false },
    actionText: { type: String, value: '分析' }
  },

  methods: {
    onTap() {
      this.triggerEvent('rowtap', { vm: this.properties.vm });
    },

    onAction(e) {
      // 阻止冒泡到整行点击
      this.triggerEvent('action', { vm: this.properties.vm });
    }
  }
});

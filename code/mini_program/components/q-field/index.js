/**
 * 表单字段 —— 五种输入框状态：默认 / 聚焦（主色描边）/ 已填 / 错误 / 禁用
 * 校验失败时描边转为涨色（红），错误文案 11pt 置于输入框正下方。
 * 交互规则对齐 ui/设计交付说明.md 第 6 节：逐字段校验、首次失焦触发。
 */
Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    label: { type: String, value: '' },
    required: { type: Boolean, value: false },
    value: { type: String, value: '' },
    placeholder: { type: String, value: '' },
    /** text | number | digit | idcard | password | textarea */
    mode: { type: String, value: 'text' },
    error: { type: String, value: '' },
    disabled: { type: Boolean, value: false },
    maxlength: { type: Number, value: 140 },
    /** 是否显示字数（textarea 场景） */
    showCount: { type: Boolean, value: false }
  },

  data: {
    focused: false
  },

  methods: {
    onInput(e) {
      this.triggerEvent('change', { value: e.detail.value });
    },

    onFocus() {
      this.setData({ focused: true });
      this.triggerEvent('focus');
    },

    /** 首次失焦时由页面做校验（触发 error 回填） */
    onBlur(e) {
      this.setData({ focused: false });
      this.triggerEvent('blur', { value: e.detail.value });
    }
  }
});

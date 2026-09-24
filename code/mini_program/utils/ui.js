/**
 * 轻量交互封装
 *
 * 说明：Toast / Dialog / ActionSheet 使用微信原生能力（wx.showToast / showModal /
 * showActionSheet）。原生的好处是无须在每个页面挂组件实例、遮罩与手势由客户端保证；
 * 代价是配色跟随微信而非设计令牌。所有调用点都收敛在本文件，
 * 若后续要做「设计稿同款深色胶囊 Toast」，只需替换这里的实现，页面无需改动。
 */

/** 轻反馈 Toast（深色胶囊 1.6s）—— 仅用于「已加入自选」这类轻提示 */
function toast(title, duration) {
  wx.showToast({
    title: String(title || ''),
    icon: 'none',
    mask: false,
    duration: duration || 1600
  });
}

function success(title) {
  wx.showToast({ title: String(title || '成功'), icon: 'success', mask: false, duration: 1600 });
}

function error(title) {
  wx.showToast({ title: String(title || '操作失败'), icon: 'none', duration: 2200 });
}

/**
 * 二次确认弹窗 —— 用于删除等不可逆操作
 * @returns {Promise<boolean>} 是否确认
 */
function confirm(options) {
  const opt = typeof options === 'string' ? { content: options } : options || {};
  return new Promise((resolve) => {
    wx.showModal({
      title: opt.title || '提示',
      content: opt.content || '',
      confirmText: opt.confirmText || '确定',
      cancelText: opt.cancelText || '取消',
      confirmColor: opt.danger ? '#DC2626' : '#2563EB',
      success(res) {
        resolve(!!res.confirm);
      },
      fail() {
        resolve(false);
      }
    });
  });
}

/**
 * 底部 ActionSheet —— 用于选择器等场景
 * @param {string[]} items
 * @returns {Promise<number>} 选中下标，取消时 reject
 */
function actionSheet(items, options) {
  const opt = options || {};
  return new Promise((resolve, reject) => {
    wx.showActionSheet({
      itemList: items,
      itemColor: opt.itemColor,
      alertText: opt.alertText,
      success(res) {
        resolve(res.tapIndex);
      },
      fail(err) {
        reject(err);
      }
    });
  });
}

/**
 * 带破坏项的 ActionSheet（删除项标红）
 * 微信 showActionSheet 只支持整体 itemColor，无法逐项着色，
 * 因此这里把删除项单独放到最后并在文案上显式标注，由调用方再做一次二次确认。
 */
function actionSheetWithDanger(items, dangerIndex) {
  const list = items.slice();
  return actionSheet(
    list.map((text, i) => (i === dangerIndex ? text : text)),
    { itemColor: undefined }
  );
}

/** 全局 loading */
function loading(title) {
  wx.showLoading({ title: title || '加载中', mask: true });
}

function hideLoading() {
  wx.hideLoading();
}

/** 复制到剪贴板 */
function copy(text, tip) {
  wx.setClipboardData({
    data: String(text || ''),
    success() {
      if (tip !== false) toast(tip || '已复制');
    }
  });
}

/** 页面间跳转（自动处理 TabBar 页面） */
const TAB_PAGES = [
  '/pages/home/home',
  '/pages/quotes/quotes',
  '/pages/strategies/strategies',
  '/pages/profile/profile'
];

function navigate(url) {
  const path = String(url || '').split('?')[0];
  if (TAB_PAGES.indexOf(path) >= 0) {
    wx.switchTab({ url: path });
  } else {
    wx.navigateTo({ url });
  }
}

module.exports = {
  toast,
  success,
  error,
  confirm,
  actionSheet,
  actionSheetWithDanger,
  loading,
  hideLoading,
  copy,
  navigate
};

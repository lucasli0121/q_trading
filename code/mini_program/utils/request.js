/**
 * 统一请求封装 —— 对齐 q_trading_cli/api/client.py 的行为：
 *   1. 自动拼接 BASE_URL；
 *   2. 自动携带 Authorization: Bearer <token>；
 *   3. 统一解包后端 ApiResponse（code / message / data），code !== 0 视为业务错误；
 *   4. 401 统一处理：清会话 + 跳登录页（带防抖，避免并发请求重复跳转）；
 *   5. 409 / 422 / 400 / 500 的错误信息尽量透出后端 detail，便于定位。
 */

const config = require('../config/index');
const store = require('./store');

let loadingCount = 0;
let redirecting = false;

function showLoading(title) {
  if (loadingCount === 0) {
    wx.showLoading({ title: title || '加载中', mask: true });
  }
  loadingCount += 1;
}

function hideLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) {
    wx.hideLoading();
  }
}

/** 业务错误：HTTP 通了但 code !== 0 */
class BizError extends Error {
  constructor(code, message) {
    super(message || '请求失败');
    this.name = 'BizError';
    this.code = code;
    this.isBiz = true;
  }
}

/** HTTP 错误 */
class HttpError extends Error {
  constructor(statusCode, message, detail) {
    super(message || `请求失败(${statusCode})`);
    this.name = 'HttpError';
    this.statusCode = statusCode;
    this.detail = detail;
    this.isHttp = true;
  }
}

function buildUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${config.BASE_URL}${path}`;
}

/** 401 → 清会话并跳登录页（只跳一次） */
function handleUnauthorized() {
  store.clearSession();
  if (redirecting) return;
  redirecting = true;
  setTimeout(() => {
    redirecting = false;
  }, 1500);
  wx.showToast({ title: '登录已失效，请重新登录', icon: 'none' });
  setTimeout(() => {
    wx.reLaunch({ url: '/pages/login/login' });
  }, 600);
}

/** 从非标准响应体里尽力提取错误文案 */
function extractMessage(body) {
  if (!body) return '';
  if (typeof body === 'string') return body;
  if (body.detail) {
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      const first = body.detail[0];
      return (first && (first.msg || first.message)) || '参数错误';
    }
  }
  return body.message || '';
}

/**
 * 发起请求
 * @param {object} opt
 * @param {string} opt.url        接口路径，如 /api/pool/list
 * @param {string} [opt.method]   GET/POST/PUT/PATCH/DELETE
 * @param {object} [opt.data]     GET 时作为 query，其余作为 JSON body
 * @param {boolean} [opt.auth]    是否携带 token，默认 true
 * @param {string} [opt.loading]  传字符串则展示全局 loading（如「正在加载行情数据...」）
 * @param {boolean} [opt.silent]  true 时不弹错误 toast（轮询场景）
 * @returns {Promise<any>} 解包后的 data
 */
function request(opt) {
  const method = (opt.method || 'GET').toUpperCase();
  const auth = opt.auth !== false;
  const token = store.getToken();

  const header = { 'Content-Type': 'application/json' };
  if (auth && token) header.Authorization = `Bearer ${token}`;

  if (opt.loading) showLoading(opt.loading);

  return new Promise((resolve, reject) => {
    wx.request({
      url: buildUrl(opt.url),
      method,
      data: opt.data || {},
      header,
      timeout: config.REQUEST_TIMEOUT,
      success(res) {
        const status = res.statusCode;
        const body = res.data;

        if (status === 401) {
          // 携带过 token 才算「会话过期」；登录/注册的 401 交由调用方提示文案
          if (auth && token) {
            handleUnauthorized();
          }
          const msg = extractMessage(body) || '账号或密码错误';
          reject(new HttpError(401, msg));
          return;
        }

        if (status >= 200 && status < 300) {
          if (body && typeof body === 'object' && 'code' in body) {
            const code = Number(body.code);
            if (code === 0) {
              resolve(body.data);
            } else {
              reject(new BizError(code, body.message || '业务处理失败'));
            }
            return;
          }
          // 非包体响应（极少见）直接返回
          resolve(body);
          return;
        }

        const msg =
          extractMessage(body) ||
          (status === 403
            ? '权限不足'
            : status === 404
            ? '接口不存在或数据已被删除'
            : status === 409
            ? '数据冲突'
            : status === 422
            ? '参数错误'
            : `请求失败(${status})`);
        reject(new HttpError(status, msg));
      },
      fail(err) {
        const msg =
          err && err.errMsg && err.errMsg.indexOf('timeout') >= 0
            ? '请求超时，请检查网络'
            : '网络异常，请检查网络连接';
        reject(new HttpError(0, msg));
      },
      complete() {
        if (opt.loading) hideLoading();
      }
    });
  });
}

/** 统一错误提示（silent 时静默） */
function toastError(err, silent) {
  if (silent) return;
  const msg = (err && err.message) || '操作失败';
  wx.showToast({ title: msg, icon: 'none', duration: 2200 });
}

/**
 * 便捷方法：带自动错误提示的请求
 * opt.silent = true 时不提示（用于轮询）
 */
function req(opt) {
  return request(opt).catch((err) => {
    toastError(err, opt.silent);
    throw err;
  });
}

module.exports = {
  request,
  req,
  toastError,
  BizError,
  HttpError,
  showLoading,
  hideLoading
};

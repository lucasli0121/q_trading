/**
 * 交易信号通道 —— WebSocket 优先，HTTP 轮询兜底
 *
 * 背景：Web 端（NiceGUI）是「后端进程订阅 MQTT → 内存 SignalManager → 前端每秒读内存」。
 * 小程序无法直连 MQTT（wx.connectSocket 只支持 WS/WSS，且 MQTT broker 的鉴权方式
 * 与小程序域名白名单不匹配），因此需要后端提供一个把 MQTT 信号转发的 WS 网关。
 *
 * 本模块的行为：
 *   1. 若 config.WS_URL 非空，先尝试建立 WS 连接（token 以 query 传递）；
 *   2. WS 连不上 / 连续失败超过阈值 → 自动降级为 HTTP 轮询 /api/trade_signal/list，
 *      轮询间隔取所有订阅者里最小的那个（监控页 3s、仪表盘 10s）；
 *   3. WS 恢复后自动切回，无需页面干预。
 *
 * 后端 WS 网关契约见 doc/接口契约.md（「小程序新增接口」一节）。
 */

const config = require('../config/index');
const api = require('./api');
const store = require('./store');

/* -------------------------------------------------- 状态 */
let socket = null;
let mode = 'idle'; // idle | ws | poll
let connecting = false;
let wsFailures = 0;
let heartbeatTimer = null;
let pollTimer = null;
let reconnectTimer = null;
let pollInterval = 0;
let stopped = true;

const signalListeners = [];
const statusListeners = [];

function emitStatus() {
  const info = { mode, connected: mode === 'ws' };
  statusListeners.forEach((fn) => {
    try {
      fn(info);
    } catch (e) {
      /* ignore */
    }
  });
}

function emitSignals(list) {
  if (!list || !list.length) return;
  const merged = store.mergeSignals(list);
  signalListeners.forEach((fn) => {
    try {
      fn(list, merged);
    } catch (e) {
      /* ignore */
    }
  });
}

/* -------------------------------------------------- WebSocket */

function buildWsUrl() {
  if (!config.WS_URL) return '';
  const token = store.getToken();
  if (!token) return '';
  const sep = config.WS_URL.indexOf('?') >= 0 ? '&' : '?';
  return `${config.WS_URL}${sep}token=${encodeURIComponent(token)}`;
}

function normalizeFrame(raw) {
  if (!raw) return [];
  let payload = raw;
  if (typeof raw === 'string') {
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      return [];
    }
  }
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === 'object') {
    const body = payload.data !== undefined ? payload.data : payload;
    if (Array.isArray(body)) return body;
    if (body && typeof body === 'object') return [body];
  }
  return [];
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (socket && mode === 'ws') {
      try {
        socket.send({ type: 'ping', ts: Date.now() });
      } catch (e) {
        /* ignore */
      }
    }
  }, 25000);
}

function closeSocket() {
  stopHeartbeat();
  if (socket) {
    try {
      socket.close({ code: 1000 });
    } catch (e) {
      /* ignore */
    }
    socket = null;
  }
}

function scheduleReconnect() {
  if (stopped) return;
  if (reconnectTimer) return;
  const delay = Math.min(30000, 2000 * Math.pow(2, Math.min(wsFailures, 4)));
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (!stopped) connectWs();
  }, delay);
}

function connectWs() {
  const url = buildWsUrl();
  if (!url || stopped || connecting || mode === 'ws') {
    if (!url) startPolling();
    return;
  }
  connecting = true;

  const task = wx.connectSocket({ url, fail: () => onWsDown() });

  task.onOpen(() => {
    connecting = false;
    wsFailures = 0;
    mode = 'ws';
    stopPolling();
    startHeartbeat();
    emitStatus();
  });

  task.onMessage((res) => {
    emitSignals(normalizeFrame(res && res.data));
  });

  task.onError(() => onWsDown());

  task.onClose(() => onWsDown());

  socket = task;
}

function onWsDown() {
  connecting = false;
  const wasWs = mode === 'ws';
  if (socket) {
    socket = null;
  }
  stopHeartbeat();
  if (mode === 'ws') mode = 'idle';
  if (wasWs) wsFailures = 0;
  else wsFailures += 1;
  emitStatus();

  if (stopped) return;

  // 连续失败 2 次以上即启用轮询兜底，同时继续后台重连
  if (wsFailures >= 2) {
    startPolling();
  }
  scheduleReconnect();
}

/* -------------------------------------------------- HTTP 轮询兜底 */

function startPolling() {
  if (stopped) return;
  if (!pollInterval) return;
  if (mode === 'poll') return;
  mode = 'poll';
  emitStatus();
  stopPolling(true);
  const fetchOnce = () => {
    api.signal
      .recent(true)
      .then((list) => {
        emitSignals(list);
      })
      .catch(() => {
        /* 轮询失败保持安静，下个周期重试 */
      });
  };
  fetchOnce();
  pollTimer = setInterval(fetchOnce, pollInterval);
}

function stopPolling(keepMode) {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (!keepMode && mode === 'poll') {
    mode = 'idle';
    emitStatus();
  }
}

/** 重新计算轮询间隔（取订阅者里的最小值） */
function recomputeInterval(intervals) {
  const list = intervals.filter((n) => n > 0);
  const next = list.length ? Math.min.apply(null, list) : 0;
  if (next === pollInterval) return;
  pollInterval = next;
  if (mode === 'poll') {
    const wasPolling = !!pollTimer;
    stopPolling(true);
    if (wasPolling) startPolling();
  }
}

/* -------------------------------------------------- 对外接口 */

// 记录每个订阅者要求的轮询间隔，便于动态取最小值
const subscriptions = [];

/**
 * 启动信号通道
 * @param {object} opt
 * @param {number} opt.pollInterval 该订阅者希望的轮询间隔（毫秒）
 * @param {function} opt.onSignals  收到新信号时回调 (delta, all)
 * @param {function} [opt.onStatus] 连接状态变化回调
 * @returns {function} 取消订阅
 */
function start(opt) {
  const sub = {
    pollInterval: (opt && opt.pollInterval) || config.POLL.monitor,
    onSignals: opt && opt.onSignals,
    onStatus: opt && opt.onStatus
  };
  subscriptions.push(sub);
  if (sub.onSignals) signalListeners.push(sub.onSignals);
  if (sub.onStatus) statusListeners.push(sub.onStatus);

  recomputeInterval(subscriptions.map((s) => s.pollInterval));

  stopped = false;
  if (!config.WS_URL) {
    startPolling();
  } else {
    connectWs();
  }
  if (sub.onStatus) sub.onStatus({ mode, connected: mode === 'ws' });

  return function stop() {
    const i = subscriptions.indexOf(sub);
    if (i >= 0) subscriptions.splice(i, 1);
    if (sub.onSignals) {
      const j = signalListeners.indexOf(sub.onSignals);
      if (j >= 0) signalListeners.splice(j, 1);
    }
    if (sub.onStatus) {
      const k = statusListeners.indexOf(sub.onStatus);
      if (k >= 0) statusListeners.splice(k, 1);
    }
    recomputeInterval(subscriptions.map((s) => s.pollInterval));
    if (!subscriptions.length) shutdown();
  };
}

/** 彻底关闭（无人订阅时） */
function shutdown() {
  stopped = true;
  closeSocket();
  stopPolling();
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  mode = 'idle';
  wsFailures = 0;
  emitStatus();
}

function status() {
  return { mode, connected: mode === 'ws' };
}

module.exports = { start, shutdown, status, normalizeFrame };

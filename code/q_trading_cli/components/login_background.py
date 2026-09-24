#!/usr/bin/env python3
"""
Author: liguoqiang
Date: 2026-09-16
Description: 登录页背景 —— 量化终端风格（蓝图网格 + 品牌辉光 + K 线粒子动画）。

设计意图：用户登录时，系统已经在为他跑数据。
背景分四层：网格 / 辉光 / 品牌水印 / canvas 蜡烛动画，全部压在登录卡片之下
（卡片 z-index=10，背景 z-index 4~6），蜡烛从卡片背后飘过。

动画编排
--------
常态：蜡烛作为粒子随机出现，三种方式混合（``MODES``）
  spawn —— 随机位置淡入浮现，实体从中线生长，停留后淡出
  fall  —— 自上方自由落体（v = v₀ + g·t），坠落中淡出
  blink —— 定点忽明忽暗地呼吸，随后消散

趋势事件（"偶尔"发生，默认首次 7s、之后间隔 12~18s）
  ① 3 条均线自左向右扫入（``SWEEP_DUR``，1.4s）
  ② 屏上蜡烛被吸附到均线上、横向均匀排开、收敛为图表尺寸，
     形成自左下向右上的上涨趋势（``HOLD_DUR``，2s）
  ③ 蜡烛向四周散开并淡出，均线同时向右退场（``EXIT_DUR``，1.1s）
  ④ 回到常态

**不含任何真实或模拟行情数值**：跑马灯与行情面板已移除；涨红跌绿只是沿用
A 股配色习惯，不表示任何行情。左下角的浮层只列系统能力名称，不带数字。

深浅主题
--------
CSS 一律走设计令牌（``var(--qs-*)`` / ``rgba(var(--qs-*-rgb), a)``），
随 ``body.qs-dark`` 自动换套，因此不需要写两份样式。

canvas 不解析 CSS 变量，脚本用 ``tokenColor()`` 在运行时从令牌读**真实色值**
（等价于 Python 侧 ``get_color()``），并在 ``body`` 的 class 变化时重读——
所以切主题时画面会跟着变。读不到时回退到渲染时由 Python 注入的色值。
"""
from __future__ import annotations

import json
import logging

from nicegui import ui

from colors.theme import theme_manager

logger = logging.getLogger(__name__)

# 均线用的三个令牌：深浅两套下三者互不相同，避免"两条线同色"（primary 与 link
# 在浅色下同为 #2563eb，所以这里用 info 而不是 link）
_MA_TOKENS: tuple[str, str, str] = ("primary", "info", "purple")


# ============================================================================
#  公开接口
# ============================================================================


def install_login_background() -> None:
    """安装登录页背景（样式 + DOM + 动画脚本）。

    必须在 ``apply_session_theme()`` 之后调用：脚本的兜底色值按当前会话主题取
    （canvas 取不到 CSS 变量时用它们）。

    通过 ``ui.add_body_html`` 注入，内容会随页面 HTML 一起下发，因此背景
    在首帧就位，不会出现"先空一帧再补上"。
    """
    palette = {
        "up": theme_manager.get_color("positive"),
        "down": theme_manager.get_color("negative"),
        "ma": [theme_manager.get_color(name) for name in _MA_TOKENS],
    }
    ui.add_css(_CSS)
    ui.add_body_html(
        _DOM + "<script>" + _SCRIPT.replace("__PALETTE__", json.dumps(palette)) + "</script>"
    )


# ============================================================================
#  样式 —— 颜色全部引用设计令牌，深浅两套自动生效
# ============================================================================

_CSS = """
/* ===== 登录页背景：网格 / 辉光 / 水印 / K 线画布 =====
   层级刻意低于登录卡片（z-index:10），让蜡烛从卡片背后飘过。
   用正值 z-index 而不是 0：Quasar 的 .q-page / .q-page-container 若是定位元素，
   z-index:0 的浮层会被它们自身的背景盖住。 */
.qtbg-grid,
.qtbg-glow {
    position: fixed;
    inset: 0;
    z-index: 4;
    pointer-events: none;
}
.qtbg-grid {
    background-image:
        linear-gradient(var(--qs-bg-grid) 1px, transparent 1px),
        linear-gradient(90deg, var(--qs-bg-grid) 1px, transparent 1px);
    background-size: 44px 44px;
    -webkit-mask-image: radial-gradient(ellipse 90% 80% at 50% 45%, black 30%, transparent 100%);
    mask-image: radial-gradient(ellipse 90% 80% at 50% 45%, black 30%, transparent 100%);
}
.qtbg-glow {
    background:
        radial-gradient(600px 320px at 18% 8%, var(--qs-bg-glow), transparent 70%),
        radial-gradient(520px 300px at 85% 12%, var(--qs-bg-glow), transparent 70%),
        radial-gradient(700px 380px at 50% 115%, var(--qs-bg-glow), transparent 75%);
}
.qtbg-watermark {
    position: fixed;
    z-index: 4;
    pointer-events: none;
    user-select: none;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -62%);
    font-size: clamp(120px, 22vw, 300px);
    font-weight: 800;
    letter-spacing: 0.08em;
    line-height: 1;
    white-space: nowrap;
    color: var(--qs-bg-grid);
}
#qtbg-scene {
    position: fixed;
    inset: 0;
    z-index: 5;
    pointer-events: none;
}

/* ===== 左下浮层：只列系统能力名称，不含任何数字或业绩 ===== */
.qtbg-panel {
    position: fixed;
    z-index: 6;
    pointer-events: none;
    left: max(24px, 4vw);
    bottom: 12vh;
    padding: 16px 18px;
    border-radius: 14px;
    background: rgba(var(--qs-surface-rgb), 0.55);
    border: 1px solid rgba(var(--qs-border-rgb), 0.65);
    backdrop-filter: blur(8px);
    box-shadow: 0 12px 32px var(--qs-shadow);
    animation: qtbg-float 7s ease-in-out infinite;
}
@keyframes qtbg-float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-10px); }
}
.qtbg-panel h3 {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--qs-link);
}
.qtbg-panel h3::before {
    content: "";
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--qs-primary);
    box-shadow: 0 0 8px var(--qs-primary);
    animation: qtbg-pulse 2s ease-in-out infinite;
}
@keyframes qtbg-pulse {
    50% { opacity: 0.35; }
}
.qtbg-panel .qtbg-feat {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 8px 0;
    font-size: 12.5px;
    color: var(--qs-muted);
}
.qtbg-panel .qtbg-feat::before {
    content: "";
    flex: 0 0 auto;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--qs-primary);
    box-shadow: 0 0 6px var(--qs-primary);
    opacity: 0.85;
}
@media (max-width: 1100px) {
    .qtbg-panel { display: none; }
}
@media (prefers-reduced-motion: reduce) {
    .qtbg-panel,
    .qtbg-panel h3::before { animation: none !important; }
}
"""

# ============================================================================
#  背景 DOM —— 顺序即层级：网格 → 辉光 → 水印 → 画布 → 浮层
# ============================================================================

_DOM = """
<div class="qtbg-grid"></div>
<div class="qtbg-glow"></div>
<div class="qtbg-watermark">QTRADE</div>
<canvas id="qtbg-scene"></canvas>
<div class="qtbg-panel">
    <h3>QTrade 量化策略平台</h3>
    <div class="qtbg-feat">股票池管理</div>
    <div class="qtbg-feat">实时行情</div>
    <div class="qtbg-feat">策略监控</div>
    <div class="qtbg-feat">量化选股</div>
</div>
"""

# ============================================================================
#  动画脚本
#  __PALETTE__ 占位符由 install_login_background() 按当前主题注入真实色值。
#  注意：本脚本内**不要出现写死的色值**（含 rgba 字面量）——一律从令牌读，
#  否则会破坏"界面颜色只有 theme.py 一个来源"的约定。
# ============================================================================

_SCRIPT = """
(function () {
  "use strict";
  if (window.__qtbgRunning) return;          // 幂等：同一页面只启动一次
  window.__qtbgRunning = true;

  const cv = document.getElementById("qtbg-scene");
  if (!cv) return;
  const cx = cv.getContext("2d");

  /* ------------------------------------------------------------
     取色：canvas 不解析 CSS 变量，运行时从令牌读真实色值。
     等价于 Python 侧的 get_color()，但能随主题切换自动更新。
     ------------------------------------------------------------ */
  const FALLBACK = __PALETTE__;
  let P = { up: FALLBACK.up, down: FALLBACK.down, ma: FALLBACK.ma.slice() };

  function tokenColor(name, fallback) {
    try {
      const v = getComputedStyle(document.body).getPropertyValue("--qs-" + name).trim();
      return v || fallback;
    } catch (err) {
      return fallback;
    }
  }
  function readPalette() {
    P = {
      up: tokenColor("positive", FALLBACK.up),
      down: tokenColor("negative", FALLBACK.down),
      ma: [
        tokenColor("primary", FALLBACK.ma[0]),
        tokenColor("info", FALLBACK.ma[1]),
        tokenColor("purple", FALLBACK.ma[2]),
      ],
    };
  }
  readPalette();
  try {
    new MutationObserver(readPalette).observe(document.body, {
      attributes: true,
      attributeFilter: ["class"],
    });
  } catch (err) {
    // 无 MutationObserver：只是切主题时画面不跟随，首屏配色仍然正确
  }

  /* ------------------------------------------------------------
     参数（想调密度 / 频率 / 时长改这里）
     ------------------------------------------------------------ */
  const MODES = ["spawn", "fall", "blink"];   // 常态出现方式；只留一个即固定单一方式
  const MAX_CANDLES = 22;                     // 同屏蜡烛上限
  const SPAWN_EVERY = 700;                    // 常态生成间隔（ms）

  const SWEEP_DUR = 1.4;        // 均线自左扫入时长（s）
  const HOLD_DUR = 2.0;         // 上涨趋势保持时长（s）
  const EXIT_DUR = 1.1;         // 均线向右退场时长（s）
  const TREND_FIRST = 7;        // 首次趋势出现前的等待（s）
  const TREND_GAP = [12, 18];   // 之后两次趋势的间隔区间（s）
  const TREND_MIN = 12;         // 趋势参与蜡烛的最少根数（3 条均线 x 4）

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let W = 1;
  let H = 1;
  const rand = (a, b) => a + Math.random() * (b - a);
  const clamp01 = (v) => Math.max(0, Math.min(1, v));
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  /* ------------------------------------------------------------
     蜡烛粒子
     ------------------------------------------------------------ */
  function makeCandle() {
    const mode = MODES[Math.floor(Math.random() * MODES.length)];
    return {
      mode: mode,
      up: Math.random() < 0.5,                     // 涨/跌只决定颜色，无行情含义
      x: rand(0.05, 0.95) * W,
      y: mode === "fall" ? -rand(60, 220) : rand(0.06, 0.86) * H,
      vy: rand(90, 220),                           // fall：初速（px/s）
      g: rand(120, 320),                           // fall：重力加速度（px/s^2）
      bw: rand(7, 15),                             // 实体宽
      bh: rand(18, 64),                            // 实体高
      wk: rand(0.5, 1.1),                          // 影线相对实体的延伸比例
      age: 0,
      life: mode === "fall" ? rand(2.6, 4.2) : rand(5, 9),
      freq: rand(0.8, 1.8),                        // blink：呼吸频率
      phase: rand(0, Math.PI * 2),
      trend: false,                                // 是否被吸附到均线上
      scatter: false,                              // 是否处于散开后的漂移状态
      tx: 0, ty: 0, tbh: 0, tbw: 0,                // 趋势目标位置与尺寸
      dvx: 0, dvy: 0,                              // 散开后的漂移速度
    };
  }

  const candles = [];

  /* ------------------------------------------------------------
     均线：自左下向右上的上升趋势曲线
       y = baseY - rise*(x/W) + amp*sin(2*pi*freq*x/W + phase)
     baseY 依次下移、rise 依次略减 -> 三条线呈多头排列
     颜色在绘制时按 P.ma[ci] 取，因此换主题即时生效。
     ------------------------------------------------------------ */
  let maLines = [];
  function makeLines() {
    const centerY = H * 0.46;
    return [0, 1, 2].map((i) => ({
      ci: i,
      baseY: centerY + i * Math.min(H * 0.055, 34),
      rise: H * (0.20 - i * 0.02),
      amp: H * (0.018 + i * 0.004),
      freq: 1.15 + i * 0.35,
      phaseOff: rand(0, Math.PI * 2),
    }));
  }
  function maColor(line) {
    return P.ma[line.ci] || P.ma[0];
  }
  function lineY(line, x) {
    const t = x / W;
    return line.baseY - line.rise * t + line.amp * Math.sin(t * Math.PI * 2 * line.freq + line.phaseOff);
  }

  /* ------------------------------------------------------------
     趋势事件状态机：idle -> sweep -> hold -> disperse -> idle
     ------------------------------------------------------------ */
  let phase = "idle";
  let phaseT = 0;
  let nextTrendIn = TREND_FIRST;   // idle 阶段倒计时
  let maDraw = 0;                  // 扫入进度 0..1
  let maShift = 0;                 // 退场位移（px，向右）

  function assignTrendTargets() {
    if (!maLines.length) return;
    const n = candles.length;
    const step = n > 1 ? 0.84 / (n - 1) : 0;
    candles.forEach((c, j) => {
      const tx = W * (0.08 + step * j);          // 横向均匀排开
      c.tx = tx;
      c.ty = lineY(maLines[j % maLines.length], tx);   // 中心落在均线上
    });
  }

  function startTrend() {
    phase = "sweep";
    phaseT = 0;
    maDraw = 0;
    maShift = 0;
    maLines = makeLines();
    while (candles.length < TREND_MIN) candles.push(makeCandle());
    candles.forEach((c) => {
      c.trend = true;
      c.scatter = false;
      c.up = Math.random() < 0.72;               // 上涨趋势以红为主
      c.tbh = rand(10, 26);                      // 收敛为"图表蜡烛"尺寸
      c.tbw = rand(6, 11);
      c.age = 0.6;                               // 已淡入完成，避免趋势中再闪一次
      c.life = Infinity;                         // 趋势期间不老化
      c.dvx = 0;
      c.dvy = 0;
    });
    assignTrendTargets();
  }

  function disperseCandles() {
    for (const c of candles) {
      if (!c.trend) continue;
      c.trend = false;
      c.scatter = true;                          // 转为自由漂移
      c.life = c.age + rand(1.6, 3.0);           // 漂移中淡出
      c.dvx = rand(-170, 170);
      c.dvy = rand(-140, 140);
    }
  }

  function update(dt) {
    phaseT += dt;
    if (phase === "idle") {
      nextTrendIn -= dt;
      if (nextTrendIn <= 0) startTrend();
    } else if (phase === "sweep") {
      maDraw = clamp01(phaseT / SWEEP_DUR);
      if (maDraw >= 1) { phase = "hold"; phaseT = 0; }
    } else if (phase === "hold") {
      if (phaseT >= HOLD_DUR) { phase = "disperse"; phaseT = 0; disperseCandles(); }
    } else if (phase === "disperse") {
      maShift = easeInOut(clamp01(phaseT / EXIT_DUR)) * (W + 80);
      if (phaseT >= EXIT_DUR + 0.8) {
        phase = "idle";
        phaseT = 0;
        maDraw = 0;
        maShift = 0;
        nextTrendIn = rand(TREND_GAP[0], TREND_GAP[1]);
      }
    }
  }

  /* 均线整体透明度：扫入时渐显，退场过半后渐隐 */
  function lineAlpha() {
    if (phase === "sweep") return clamp01(maDraw * 1.2);
    if (phase === "hold") return 1;
    if (phase === "disperse") return clamp01(1 - (phaseT / EXIT_DUR - 0.5) / 0.5);
    return 0;
  }

  function drawLines() {
    const a = lineAlpha();
    if (a <= 0.01) return;
    const limit = phase === "sweep" ? W * maDraw : W;   // 扫入时只画到进度处
    for (const line of maLines) {
      const color = maColor(line);
      cx.save();
      cx.globalAlpha = a * 0.6;
      cx.strokeStyle = color;
      cx.lineWidth = 1.6;
      cx.shadowColor = color;
      cx.shadowBlur = 6;                                // 轻微辉光，像技术指标叠加层
      cx.beginPath();
      for (let x = 0; x <= limit; x += 6) {
        const px = x + maShift;
        const py = lineY(line, x);
        if (x === 0) cx.moveTo(px, py); else cx.lineTo(px, py);
      }
      cx.stroke();
      cx.restore();
    }
  }

  function drawCandle(c, alpha) {
    if (alpha <= 0.01) return;
    const color = c.up ? P.up : P.down;
    const grow = (c.mode === "spawn" && !c.trend && !c.scatter)
      ? clamp01(c.age / 0.7)                            // spawn：实体从中心向两端生长
      : 1;
    const bh = Math.max(c.bh * grow, 1.5);
    const yTop = c.y - bh / 2;
    const wickHalf = (c.bh * c.wk / 2) * grow;

    cx.globalAlpha = alpha * 0.85;
    cx.strokeStyle = color;
    cx.lineWidth = 1.2;
    cx.beginPath();
    cx.moveTo(c.x, yTop - wickHalf);
    cx.lineTo(c.x, yTop + bh + wickHalf);
    cx.stroke();

    cx.globalAlpha = alpha;
    cx.fillStyle = color;
    cx.fillRect(c.x - c.bw / 2, yTop, c.bw, bh);
    cx.globalAlpha = 1;
  }

  /* 亮度包络：淡入 -> 保持 -> 淡出 */
  function envelope(c) {
    const fadeIn = Math.min(c.age / 0.9, 1);
    const fadeOut = Math.min((c.life - c.age) / 1.2, 1);
    return Math.max(0, Math.min(fadeIn, fadeOut));
  }

  function moveCandle(c, dt) {
    if (c.trend) {                                      // 被均线吸附（缓入）
      const k = Math.min(dt * 3.0, 1);
      c.x += (c.tx - c.x) * k;
      c.y += (c.ty - c.y) * k;
      c.bh += (c.tbh - c.bh) * k;
      c.bw += (c.tbw - c.bw) * k;
      return;
    }
    if (c.scatter) {                                    // 散开后自由漂移并衰减
      c.x += c.dvx * dt;
      c.y += c.dvy * dt;
      const decay = Math.exp(-1.5 * dt);
      c.dvx *= decay;
      c.dvy *= decay;
      return;
    }
    if (c.mode === "fall") {
      c.vy += c.g * dt;                                 // v = v0 + g*t
      c.y += c.vy * dt;
    }
  }

  /* ------------------------------------------------------------
     主循环
     ------------------------------------------------------------ */
  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;

    cx.clearRect(0, 0, W, H);
    update(dt);
    drawLines();                                        // 均线在下层

    for (let i = candles.length - 1; i >= 0; i--) {
      const c = candles[i];
      c.age += dt;
      moveCandle(c, dt);
      if (!c.trend && c.age >= c.life) { candles.splice(i, 1); continue; }

      let alpha;
      if (c.trend) {
        alpha = 0.92;                                   // 趋势段稳定可见
      } else {
        alpha = envelope(c);
        if (c.mode === "blink" && !c.scatter) {          // 忽明忽暗（散开后不再呼吸）
          alpha *= 0.45 + 0.55 * (0.5 + 0.5 * Math.sin(c.phase + c.age * c.freq * 2 * Math.PI));
        }
      }
      drawCandle(c, alpha);
    }
    requestAnimationFrame(frame);
  }

  function spawn() {
    if (phase !== "idle") return;                       // 趋势期间不再随机生成
    if (candles.length < MAX_CANDLES) candles.push(makeCandle());
  }

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    W = innerWidth;
    H = innerHeight;
    cv.width = Math.floor(W * dpr);
    cv.height = Math.floor(H * dpr);
    cv.style.width = W + "px";
    cv.style.height = H + "px";
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    maLines = makeLines();
    if (phase === "hold" || phase === "sweep") assignTrendTargets();   // 尺寸变了要重算目标
  }

  resize();
  addEventListener("resize", resize);

  if (reduceMotion) {
    // 减少动态：画一组静态蜡烛后不再推进（无 rAF、无定时器）
    for (let i = 0; i < 12; i++) {
      const c = makeCandle();
      c.mode = "spawn";
      c.age = 2;
      c.life = 9;
      drawCandle(c, 0.35);
    }
  } else {
    for (let i = 0; i < 10; i++) candles.push(makeCandle());   // 开场预铺，避免空场
    requestAnimationFrame(frame);
    setInterval(spawn, SPAWN_EVERY);
  }

  /* 供离线自检读取内部状态：本机无浏览器时用 Node 桩跑帧验证（见
     ~/.workbuddy/skills/browserless-canvas-page-check）。浏览器里也可在
     控制台调用 __qtbgState() 排查。 */
  window.__qtbgState = function () {
    return {
      phase: phase,
      phaseT: phaseT,
      candles: candles.length,
      maDraw: maDraw,
      maShift: maShift,
      palette: P,
      // 趋势段蜡烛的屏幕坐标 [x, y]（y 向下为正，故"向右上"= 斜率为负）
      trend: candles.filter((c) => c.trend).map((c) => [Math.round(c.x), Math.round(c.y)]),
    };
  };
})();
"""

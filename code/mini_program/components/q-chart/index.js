/**
 * 行情图表（canvas 2d 自绘，零第三方依赖）
 *
 * 支持的三种图形，对应 Web 端 ECharts 的三处用法：
 *   kline  —— 日K 蜡烛图 + 成交量副图 + 买卖信号圆圈标注（日/周/月K 共用）
 *   minute —— 分时折线图 + 昨收虚线 + 买卖信号圆圈标注
 *   profit —— 累计收益面积折线（仪表盘）
 *
 * 为什么不用 ECharts/uCharts：设计交付说明第 9 节虽建议引入图表库，
 * 但小程序引入 npm 包需要构建 npm、且体积较大；蜡烛图/折线图的绘制逻辑很短，
 * 自绘可以避免依赖、也便于严格按「涨红跌绿」和设计令牌取色。
 */

const chartUtil = require('../../utils/chart');
const fmt = require('../../utils/format');

Component({
  options: {
    styleIsolation: 'apply-shared'
  },

  properties: {
    /** kline | minute | profit */
    kind: { type: String, value: 'kline' },
    /** 主题：light | dark */
    theme: { type: String, value: 'light' },
    /** 高度（rpx） */
    height: { type: Number, value: 560 },
    /** kline: [{date, open, close, low, high, volume, amount}] */
    candles: { type: Array, value: [] },
    /** profit: {labels: [], values: []} */
    lineData: { type: Object, value: null },
    /** 买卖信号：[{date/create_time, trade_price, action}] */
    signals: { type: Array, value: [] },
    /** 空态文案 */
    emptyText: { type: String, value: '' }
  },

  data: {
    canvasId: 'qchart',
    ready: false
  },

  observers: {
    'kind, theme, candles, lineData, signals': function () {
      this.scheduleDraw();
    }
  },

  lifetimes: {
    attached() {
      this._seq = 0;
      this.scheduleDraw();
    },
    detached() {
      if (this._timer) clearTimeout(this._timer);
    }
  },

  methods: {
    scheduleDraw() {
      if (this._timer) clearTimeout(this._timer);
      this._timer = setTimeout(() => this.draw(), 30);
    },

    /** 取 canvas 节点并按 dpr 设置物理像素 */
    initCanvas() {
      return new Promise((resolve) => {
        const query = this.createSelectorQuery();
        query
          .select('#qchart')
          .fields({ node: true, size: true })
          .exec((res) => {
            const info = res && res[0];
            if (!info || !info.node) {
              resolve(null);
              return;
            }
            const canvas = info.node;
            const dpr = (wx.getWindowInfo && wx.getWindowInfo().pixelRatio) || 2;
            const w = info.width;
            const h = info.height;
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
            const ctx = canvas.getContext('2d');
            ctx.scale(dpr, dpr);
            resolve({ ctx, w, h, canvas });
          });
      });
    },

    draw() {
      const colors = chartUtil.chartColors(this.properties.theme);
      const cans = this.properties.candles || [];
      const sigs = this.properties.signals || [];
      const lineData = this.properties.lineData;

      const isEmpty =
        this.properties.kind === 'profit'
          ? !lineData || !lineData.labels || !lineData.labels.length
          : this.properties.kind === 'minute'
          ? !cans.length
          : !cans.length;

      if (isEmpty) {
        this.setData({ ready: false });
        return;
      }

      this.initCanvas().then((info) => {
        if (!info) return;
        const { ctx, w, h } = info;
        ctx.clearRect(0, 0, w, h);

        if (this.properties.kind === 'kline') {
          this.drawKline(ctx, w, h, cans, sigs, colors);
        } else if (this.properties.kind === 'minute') {
          this.drawMinute(ctx, w, h, cans, sigs, colors);
        } else {
          this.drawProfit(ctx, w, h, lineData, colors);
        }
        this.setData({ ready: true });
      });
    },

    /* ---------------------------------------------------------- 日/周/月 K */
    drawKline(ctx, w, h, raw, signals, colors) {
      // 剔除无效行并按日期升序
      const list = (raw || [])
        .filter((it) => it && (it.close !== undefined || it.price !== undefined))
        .map((it) => ({
          date: fmt.recordDate(it),
          open: fmt.sf(it.open),
          close: fmt.sf(it.close !== undefined ? it.close : it.price),
          low: fmt.sf(it.low),
          high: fmt.sf(it.high),
          volume: fmt.sf(it.volume)
        }))
        .filter((it) => it.close)
        .sort((a, b) => String(a.date).localeCompare(String(b.date)));

      const data = chartUtil.sliceTail(list, 90);
      if (!data.length) return;

      const padL = 8;
      const padR = 8;
      const padT = 10;
      const gap = 12;
      const priceH = Math.round(h * 0.66);
      const volH = h - padT - priceH - gap - 16;
      const plotW = w - padL - padR;
      const step = plotW / data.length;
      const bodyW = Math.max(2, Math.min(step * 0.62, 12));

      const prices = [];
      data.forEach((it) => {
        prices.push(it.low, it.high, it.open, it.close);
      });
      const ext = chartUtil.extent(prices);

      // 网格 + 价格刻度
      ctx.strokeStyle = colors.grid;
      ctx.lineWidth = 1;
      ctx.font = '10px sans-serif';
      ctx.fillStyle = colors.axis;
      ctx.textAlign = 'left';
      chartUtil.gridLines(padT, priceH, 3).forEach((y, i, arr) => {
        ctx.beginPath();
        ctx.setLineDash([3, 3]);
        ctx.moveTo(padL, y);
        ctx.lineTo(w - padR, y);
        ctx.stroke();
        const val = ext.max - ((ext.max - ext.min) * i) / (arr.length - 1);
        ctx.fillText(val.toFixed(2), padL + 2, y - 3);
      });
      ctx.setLineDash([]);

      // 蜡烛
      data.forEach((it, i) => {
        const cx = padL + step * i + step / 2;
        const up = it.close >= it.open;
        const color = up ? colors.up : colors.down;
        const yHigh = chartUtil.scaleY(it.high, ext.min, ext.max, padT, priceH);
        const yLow = chartUtil.scaleY(it.low, ext.min, ext.max, padT, priceH);
        const yOpen = chartUtil.scaleY(it.open, ext.min, ext.max, padT, priceH);
        const yClose = chartUtil.scaleY(it.close, ext.min, ext.max, padT, priceH);

        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx, yHigh);
        ctx.lineTo(cx, yLow);
        ctx.stroke();

        const top = Math.min(yOpen, yClose);
        const bh = Math.max(1.5, Math.abs(yClose - yOpen));
        ctx.fillRect(cx - bodyW / 2, top, bodyW, bh);
      });

      // 成交量副图
      const volTop = padT + priceH + gap;
      const vmax = chartUtil.extent(data.map((it) => it.volume)).max || 1;
      data.forEach((it, i) => {
        const cx = padL + step * i + step / 2;
        const up = it.close >= it.open;
        const vh = Math.max(1, (it.volume / vmax) * volH);
        ctx.fillStyle = up ? colors.up : colors.down;
        ctx.globalAlpha = 0.55;
        ctx.fillRect(cx - bodyW / 2, volTop + volH - vh, bodyW, vh);
        ctx.globalAlpha = 1;
      });

      // 日期轴（首个 / 中间 / 最后一个）
      ctx.fillStyle = colors.axis;
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';
      [0, Math.floor(data.length / 2), data.length - 1].forEach((i) => {
        const d = data[i] && data[i].date;
        if (!d) return;
        const cx = padL + step * i + step / 2;
        const label = d.slice(5);
        const x = Math.max(24, Math.min(w - 24, cx));
        ctx.fillText(label, x, h - 3);
      });

      // 买卖信号圆圈
      const indexOfDate = {};
      data.forEach((it, i) => {
        indexOfDate[it.date] = i;
      });
      (signals || []).forEach((sg) => {
        const d = fmt.recordDate(sg);
        const i = indexOfDate[d];
        if (i === undefined) return;
        const price = fmt.sf(sg.trade_price);
        if (!price) return;
        const cx = padL + step * i + step / 2;
        const cy = chartUtil.scaleY(price, ext.min, ext.max, padT, priceH);
        this.drawSignalBadge(ctx, cx, cy, sg.action, colors);
      });
    },

    /* ---------------------------------------------------------- 分时 */
    drawMinute(ctx, w, h, raw, signals, colors) {
      const list = (raw || [])
        .map((it) => {
          let t = '';
          ['minute_time', 'create_time', 'time'].forEach((k) => {
            const v = String(it[k] || '');
            if (!t && v.length >= 16) t = v.slice(11, 16);
          });
          return {
            time: t,
            price: fmt.sf(it.price !== undefined ? it.price : it.close),
            preclose: fmt.sf(it.preclose)
          };
        })
        .filter((it) => it.time && it.price)
        .sort((a, b) => String(a.time).localeCompare(String(b.time)));

      if (!list.length) return;

      const padL = 8;
      const padR = 8;
      const padT = 10;
      const padB = 18;
      const plotW = w - padL - padR;
      const plotH = h - padT - padB;

      const preclose = list[0].preclose || 0;
      const prices = list.map((it) => it.price);
      if (preclose) prices.push(preclose);
      const ext = chartUtil.extent(prices);

      // 网格
      ctx.strokeStyle = colors.grid;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      chartUtil.gridLines(padT, plotH, 3).forEach((y) => {
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(w - padR, y);
        ctx.stroke();
      });
      ctx.setLineDash([]);

      // 价格刻度
      ctx.fillStyle = colors.axis;
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      chartUtil.gridLines(padT, plotH, 3).forEach((y, i, arr) => {
        const val = ext.max - ((ext.max - ext.min) * i) / (arr.length - 1);
        ctx.fillText(val.toFixed(2), padL + 2, y - 3);
      });

      const step = plotW / Math.max(1, list.length - 1);
      const xy = (i) => [padL + step * i, chartUtil.scaleY(list[i].price, ext.min, ext.max, padT, plotH)];

      // 面积填充
      const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
      grad.addColorStop(0, colors.areaTop);
      grad.addColorStop(1, colors.areaBottom);
      ctx.beginPath();
      ctx.moveTo(padL, padT + plotH);
      list.forEach((it, i) => {
        const p = xy(i);
        ctx.lineTo(p[0], p[1]);
      });
      ctx.lineTo(padL + step * (list.length - 1), padT + plotH);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // 价格线
      ctx.beginPath();
      list.forEach((it, i) => {
        const p = xy(i);
        if (i === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      });
      ctx.strokeStyle = colors.line;
      ctx.lineWidth = 1.6;
      ctx.stroke();

      // 昨收参考线
      if (preclose) {
        const y = chartUtil.scaleY(preclose, ext.min, ext.max, padT, plotH);
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(padL, y);
        ctx.lineTo(w - padR, y);
        ctx.strokeStyle = colors.axis;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // 时间轴
      ctx.fillStyle = colors.axis;
      ctx.textAlign = 'center';
      [0, Math.floor(list.length / 2), list.length - 1].forEach((i) => {
        const it = list[i];
        if (!it) return;
        const x = Math.max(18, Math.min(w - 18, padL + step * i));
        ctx.fillText(it.time, x, h - 3);
      });

      // 信号圆圈：按 HH:MM 就近吸附
      const times = list.map((it) => it.time);
      (signals || []).forEach((sg) => {
        const ct = String(sg.create_time || '');
        if (ct.length < 16) return;
        const hhmm = ct.slice(11, 16);
        const idx = this.nearestIndex(hhmm, times);
        if (idx < 0) return;
        const p = xy(idx);
        this.drawSignalBadge(ctx, p[0], p[1], sg.action, colors);
      });
    },

    /* ---------------------------------------------------------- 累计收益 */
    drawProfit(ctx, w, h, lineData, colors) {
      const labels = (lineData && lineData.labels) || [];
      const values = (lineData && lineData.values) || [];
      if (!labels.length) return;

      const padL = 8;
      const padR = 10;
      const padT = 12;
      const padB = 20;
      const plotW = w - padL - padR;
      const plotH = h - padT - padB;
      const ext = chartUtil.extent(values.concat([0]));

      // 网格 + y 轴
      ctx.strokeStyle = colors.grid;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      const lines = chartUtil.gridLines(padT, plotH, 3);
      lines.forEach((y) => {
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(w - padR, y);
        ctx.stroke();
      });
      ctx.setLineDash([]);

      ctx.fillStyle = colors.axis;
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      lines.forEach((y, i, arr) => {
        const val = ext.max - ((ext.max - ext.min) * i) / (arr.length - 1);
        ctx.fillText(`${val.toFixed(1)}%`, padL + 2, y - 3);
      });

      const step = plotW / Math.max(1, labels.length - 1);
      const xy = (i) => [padL + step * i, chartUtil.scaleY(values[i], ext.min, ext.max, padT, plotH)];

      // 面积
      const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
      grad.addColorStop(0, colors.areaTop);
      grad.addColorStop(1, colors.areaBottom);
      ctx.beginPath();
      ctx.moveTo(padL, padT + plotH);
      labels.forEach((_, i) => {
        const p = xy(i);
        ctx.lineTo(p[0], p[1]);
      });
      ctx.lineTo(padL + step * (labels.length - 1), padT + plotH);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // 折线
      ctx.beginPath();
      labels.forEach((_, i) => {
        const p = xy(i);
        if (i === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      });
      ctx.strokeStyle = colors.line;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.stroke();

      // x 轴标签
      ctx.fillStyle = colors.axis;
      ctx.textAlign = 'center';
      [0, Math.floor(labels.length / 2), labels.length - 1].forEach((i) => {
        const label = labels[i];
        if (!label) return;
        const x = Math.max(18, Math.min(w - 18, padL + step * i));
        ctx.fillText(label, x, h - 4);
      });
    },

    /* ---------------------------------------------------------- 公共 */
    /** 买卖信号圆圈徽标：买入红圈、卖出绿圈，圈内 8px 文字 */
    drawSignalBadge(ctx, cx, cy, action, colors) {
      const isBuy = fmt.isBuyAction(action);
      const color = isBuy ? colors.up : colors.down;
      const r = 9;
      const y = Math.max(10, Math.min(cy, 1e9));
      ctx.beginPath();
      ctx.setLineDash([]);
      ctx.arc(cx, y, r, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.stroke();
      ctx.fillStyle = color;
      ctx.font = 'bold 8px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(isBuy ? '买' : '卖', cx, y + 0.5);
      ctx.textBaseline = 'alphabetic';
    },

    /** 在已排序的 HH:MM 数组里找最接近的下标 */
    nearestIndex(target, list) {
      if (!list || !list.length) return -1;
      const toMin = (s) => {
        const p = String(s).split(':');
        if (p.length !== 2) return -1;
        return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
      };
      const t = toMin(target);
      if (t < 0) return -1;
      let best = 0;
      let bestDiff = Infinity;
      list.forEach((s, i) => {
        const d = Math.abs(toMin(s) - t);
        if (d < bestDiff) {
          bestDiff = d;
          best = i;
        }
      });
      return best;
    }
  }
});

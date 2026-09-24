# ADX 趋势因子:数据不足时会把上涨判成下跌

`factor/adx_trend_factor.py` 里的一个边界缺陷。

ADX 用于判断趋势强度:±DI 判断方向,ADX 判断强度,两者需要配对使用。ADX 的公式取的是 `|+DI − (-DI)|` 的绝对值,只反映多空力量差距,不区分方向,因此单独看 ADX 没有意义。

核心代码如下:

```python
adx      = talib.ADX(highs, lows, closes, timeperiod=period)
plus_di  = talib.PLUS_DI(highs, lows, closes, timeperiod=period)
minus_di = talib.MINUS_DI(highs, lows, closes, timeperiod=period)

current_adx, current_plus, current_minus = adx[-1], plus_di[-1], minus_di[-1]

direction = 'up' if current_plus > current_minus else 'down'

if current_adx > threshold:
    strength = 'strong'
elif current_adx > 20:
    strength = 'moderate'
else:
    strength = 'weak/sideways'
```

打分规则是方向系数乘以强度系数,再归一到 ±10:

| 方向 | 强度 | 得分 |
| --- | --- | --- |
| up | strong | 10.0 |
| up | moderate | 7.5 |
| up | weak/sideways | 2.5 |
| down | strong | −10.0 |
| down | moderate | −7.5 |
| down | weak/sideways | −2.5 |

需要说明的是,该因子的返回值不会为 0。方向只有 up/down 两种,强度系数最低为 0.5,所以横盘震荡时因子必然给出 ±2.5 中的一个。如果下游用 `sum(score)` 与阈值比较,这部分会成为固定的偏移量。

## 数据不足时方向判定错误

talib 的 ADX 需要 `2 × period` 根 K 线才能产生第一个有效值。实测各周期所需 K 线数:period 7 → 14 根、10 → 20、**14 → 28**、15 → 30、20 → 40、25 → 50。

因子类的默认参数是 `days=15`,即输入 K 线少于 30 根时 ADX 为 `NaN`。

代码中没有长度校验,`NaN` 会直接进入后续比较:

- `NaN > 25` 与 `NaN > 20` 均为 False,强度落到 `else` 分支,取值为 `weak/sideways`;
- `NaN > NaN` 为 False,方向落到 `else` 分支,取值为 `down`。

这两个 `else` 分支分别是「下跌」和「震荡」的取值,数据不足这一状态因此被判定为「下跌 + 震荡」。

用真实因子类实测(参数 `days=15`,输入单调上涨序列):

| 输入 K 线数 | adx | 判定方向 | 判定强度 | 得分 |
| --- | --- | --- | --- | --- |
| 5 | NaN | down ✗ | weak/sideways | −2.50 |
| 15 | NaN | down ✗ | weak/sideways | −2.50 |
| 20 | NaN | up ✓ | weak/sideways | 2.50 |
| 30 | 100.0 | up ✓ | strong | 10.00 |

输入 5 根和 15 根 K 线时,因子把连续上涨的序列判定为 `down`,得分 −2.5。

容易触发该问题的是次新股(上市不足 30 个交易日)、长期停牌后复牌的股票,以及取数只得到部分数据的场景。

修复方式是在 `calculate()` 入口增加长度检查:

```python
def calculate(self, stock_array):
    # ADX 需要 2×period 根K线,不足时 talib 返回 NaN
    if len(stock_array) < 2 * self.days:
        return {"trend": "", "level": "", "adx": 0.0, "+DI": 0.0, "-DI": 0.0}
    return self.adx_trend(stock_array, period=self.days)

def score(self, stock_array):
    result = self.calculate(stock_array)
    if not result.get("trend"):   # 数据不足 → 返回 0,不走 else 分支
        return 0.0
    ...
```

处理原则是:数据不足与看空是两种不同状态,不应共用同一个返回值。这一点与取数链路的处理一致:取数失败、无数据、不满足条件需要分别标识,不能合并处理。

## 另外两个问题

**`threshold` 参数只对 strong 判定生效。** 判断 `strong` 时使用了 `threshold`,判断 `moderate` 时使用的 `20` 是硬编码值。调整 `threshold` 不会改变「震荡 / 非震荡」这条分界线。

**基类与子类的方法签名不一致。** 基类为 `BaseFactor.score(stocks, total_score=10.0)`,子类为 `score(stock_array)`,缺少 `total_score` 参数。当前所有调用点都只传一个参数,不会报错;但如果按基类签名传参,会触发 `TypeError`。

## 小结

这类问题不会抛出异常,也不会写入错误日志。数据不足时 `NaN` 穿过比较运算、落入 `else` 分支,输出的是一组格式合法但结论相反的因子值。

> **免责声明**:本文为技术实现分享,涉及因子逻辑与代码均为工程实践讨论,**不构成任何投资建议**。文中数据来自合成序列实测,不代表真实市场表现。股市有风险,投资需谨慎。

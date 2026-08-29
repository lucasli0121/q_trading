## strategy目录说明
- 实现不同的策略
- 策略保存，实现的所有策略都需要有名称，描述，类型（哪种策略），并通过api保存到数据库
- 策略请求接口前缀 url=[host:post]/api/strategy/
## 功能说明
### 策略
- BaseStrategy，提供所有策略的基类
- BaseStrategy实现before_trading函数，每次早上9:30开市之前执行，把定时设置到配置文件默认为9:00
- 强势反弹策略: 单日涨幅 > 3%，最近3日反弹 > 8%，收盘价 > MA5
- 成交量放大策略：当日成交量突然连续放大，股价连续走高，最近3日内涨幅 > 5%
- 连扳策略：连续两日涨停，所处板块连续3天走高

### 关键方法
**before_trading**
- 每天开盘前运行一次，默认为9:00， 用于策略运行时初始化工作

**check_minute_buy**
每分钟检测一次，分钟范围内检查是否买入
- 参数stock_data，类型list[dict[str, Any]], 是一个多股票列表，列表内包含每分钟的股票股价等，是StockRealTimeDao对象的dict形式

**check_minute_sell**
每分钟检测一次，分钟范围内检查是否卖出
- 参数code: str，股票代码
- 参数cost_price: float, 目前持仓成本价
- 参数stock_data: dict[str, Any], 当前分钟内此股票的行情

**check_tick_buy**
实时检查是否买入
- 参数stock_data，类型list[dict[str, Any]], 是一个多股票列表，列表内包含股票实时股价，是StockRealTimeDao对象的dict形式

**check_tick_sell**
实时检测是否卖出
- 参数code: str，股票代码
- 参数cost_price: float, 目前持仓成本价
- 参数stock_data: dict[str, Any], 此股票实时行情
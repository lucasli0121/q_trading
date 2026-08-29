"""
Author: liguoqiang
Date: 2026-06-22 13:30:00
LastEditors: liguoqiang
LastEditTime: 2026-06-22 13:30:00
Description: Pydantic 请求/响应模型 - 定义所有 API 的入参和出参结构
"""

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ==================== 通用响应 ====================

class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应格式"""
    code: int = Field(default=0, description="状态码，0 表示成功")
    message: str = Field(default="ok", description="提示信息")
    data: Optional[T] = Field(default=None, description="响应数据")


# ==================== 用户 ====================

class RegisterRequest(BaseModel):
    """用户注册请求"""
    account: str = Field(..., description="用户账号")
    password: str = Field(..., description="密码")
    role: int = Field(default=1, description="角色: 0=管理员, 1=普通用户")
    phone: str = Field(default="", description="手机号")
    email: str = Field(default="", description="邮箱")


class LoginRequest(BaseModel):
    """用户登录请求"""
    account: str = Field(..., description="用户账号")
    password: str = Field(..., description="密码")


class SetOnlineRequest(BaseModel):
    """设置用户在线状态请求"""
    is_online: bool = Field(..., description="是否在线: true=在线, false=离线")


class UserInfo(BaseModel):
    """用户信息"""
    id: str = ""
    account: str = ""
    role: int = Field(default=1, description="角色: 0=管理员, 1=普通用户")
    phone: str = ""
    email: str = ""


class UserPreferenceRequest(BaseModel):
    """用户偏好设置请求"""
    theme_mode: str = Field(default="light", description="界面模式: dark / light")
    enable_wx_push: bool = Field(default=False, description="是否企业微信消息推送")
    wx_push_url: str = Field(default="", description="企业微信推送链接")
    enable_phone_text: bool = Field(default=False, description="是否手机短信推送")
    phone: str = Field(default="", description="推送手机号")


class UserPreferenceResponse(BaseModel):
    """用户偏好设置响应"""
    id: str = Field(default="", description="记录 ID")
    user_id: str = Field(default="", description="用户 ID")
    theme_mode: str = Field(default="light", description="界面模式: dark / light")
    enable_wx_push: bool = Field(default=False, description="是否企业微信消息推送")
    wx_push_url: str = Field(default="", description="企业微信推送链接")
    enable_phone_text: bool = Field(default=False, description="是否手机短信推送")
    phone: str = Field(default="", description="推送手机号")
    update_time: str = Field(default="", description="更新时间")


# ==================== 股票池 ====================

class PoolCreateRequest(BaseModel):
    """创建股票池请求"""
    name: str = Field(..., description="股票池名称")
    description: str = Field(default="", description="描述")


class PoolStockModifyRequest(BaseModel):
    """股票池添加/剔除股票请求"""
    codes: list[str] = Field(..., description="股票代码列表")


class PoolInfo(BaseModel):
    """股票池信息"""
    id: str = ""
    name: str = ""
    description: str = ""
    create_time: str = ""
    user_id: str = ""


class PoolStockInfo(BaseModel):
    """池内股票信息"""
    code: str = ""
    add_time: str = ""


# ==================== 策略 ====================

class StrategyCreateRequest(BaseModel):
    """创建全局策略请求（仅管理员）"""
    name: str = Field(..., description="策略名称")
    strategy_type: str = Field(..., description="策略类型: 选股策略/盯盘策略/复盘策略")
    description: str = Field(default="", description="描述")
    class_path: str = Field(default="", description="策略类路径")
    class_name: str = Field(default="", description="策略类名")
    default_params: dict[str, Any] = Field(default_factory=dict, description="默认参数")


class StrategyUpdateRequest(BaseModel):
    """更新全局策略请求（仅管理员）"""
    name: Optional[str] = Field(default=None, description="策略名称")
    strategy_type: Optional[str] = Field(default=None, description="策略类型: 选股策略/盯盘策略/复盘策略")
    description: Optional[str] = Field(default=None, description="描述")
    class_path: Optional[str] = Field(default=None, description="策略类路径")
    class_name: Optional[str] = Field(default=None, description="策略类名")
    default_params: Optional[dict[str, Any]] = Field(default=None, description="默认参数")


# ==================== 因子 ====================

class FactorCreateRequest(BaseModel):
    """创建因子请求（仅管理员）"""
    name: str = Field(..., description="因子名称")
    description: str = Field(default="", description="描述")
    class_path: str = Field(default="", description="因子类路径")
    class_name: str = Field(default="", description="因子类名")
    default_params: dict[str, Any] = Field(default_factory=dict, description="默认参数")


# ==================== 用户因子 ====================

class UserFactorCreateRequest(BaseModel):
    """创建用户因子关联请求"""
    factor_id: str = Field(..., description="全局因子 ID（FactorDao 的 ID）")
    factor_params: dict[str, Any] = Field(default_factory=dict, description="因子运行参数（用户自定义）")


class UserFactorUpdateRequest(BaseModel):
    """更新用户因子关联请求"""
    factor_params: Optional[dict[str, Any]] = Field(default=None, description="因子运行参数（用户自定义）")


class UserFactorItem(BaseModel):
    """用户因子关联响应"""
    id: str = Field(default="", description="记录 ID")
    factor_id: str = Field(default="", description="全局因子 ID")
    user_id: str = Field(default="", description="用户 ID")
    factor_params: dict[str, Any] = Field(default_factory=dict, description="因子运行参数")
    create_time: str = Field(default="", description="创建时间")


class UserStrategyCreateRequest(BaseModel):
    """创建用户策略关联请求"""
    strategy_id: str = Field(..., description="全局策略 ID（StrategyDao 的 ID）")
    pool_id: str = Field(default="", description="关联股票池 ID")
    status: str = Field(default="stopped", description="策略状态: running(运行中) / stopped(已停止) / paused(已暂停)")
    initial_amount: float = Field(default=0.0, description="初始金额")
    total_profit: float = Field(default=0.0, description="总收益金额")
    max_stock_count: int = Field(default=0, description="最大持仓数量")
    strategy_params: dict[str, Any] = Field(default_factory=dict, description="策略运行参数")


class UserStrategyUpdateRequest(BaseModel):
    """更新用户策略关联请求"""
    pool_id: Optional[str] = Field(default=None, description="关联股票池 ID")
    status: Optional[str] = Field(default=None, description="策略状态: running(运行中) / stopped(已停止) / paused(已暂停)")
    initial_amount: Optional[float] = Field(default=None, description="初始金额")
    total_profit: Optional[float] = Field(default=None, description="总收益金额")
    max_stock_count: Optional[int] = Field(default=None, description="最大持仓数量")
    strategy_params: Optional[dict[str, Any]] = Field(default=None, description="策略运行参数")


class UserStrategyItem(BaseModel):
    """用户策略关联响应"""
    id: str = Field(default="", description="记录 ID")
    strategy_id: str = Field(default="", description="全局策略 ID")
    status: str = Field(default="", description="策略状态")
    user_id: str = Field(default="", description="用户 ID")
    pool_id: str = Field(default="", description="关联股票池 ID")
    initial_amount: float = Field(default=0.0, description="初始金额")
    total_profit: float = Field(default=0.0, description="总收益金额")
    max_stock_count: int = Field(default=0, description="最大持仓数量")
    strategy_params: dict[str, Any] = Field(default_factory=dict, description="策略运行参数")
    create_time: str = Field(default="", description="创建时间")


class BacktestSaveRequest(BaseModel):
    """保存回测结果请求"""
    result_data: dict[str, Any] = Field(..., description="回测结果数据（JSON 对象）")


class RunLogSaveRequest(BaseModel):
    """保存运行记录请求"""
    log_content: str = Field(..., description="日志内容")
    level: str = Field(default="INFO", description="日志级别")


class PositionItemModel(BaseModel):
    """单只持仓股票信息"""
    code: str = Field(default="", description="股票代码")
    name: str = Field(default="", description="股票名称")
    quantity: int = Field(default=0, description="持仓数量（股）")
    cost_price: float = Field(default=0.0, description="成本价")
    current_price: float = Field(default=0.0, description="当前价")
    profit_rate: float = Field(default=0.0, description="个股收益率")
    profit_amount: float = Field(default=0.0, description="个股收益金额")
    buy_time: str = Field(default="", description="买入时间")


class StrategyExecutionSaveRequest(BaseModel):
    """保存/更新策略执行结果请求"""
    current_return_rate: float = Field(default=0.0, description="目前收益率（如 0.15 = 15%）")
    current_profit: float = Field(default=0.0, description="目前收益金额")
    annualized_return_rate: float = Field(default=0.0, description="年化收益率")
    benchmark_return_rate: float = Field(default=0.0, description="基准收益率（如沪深300同期收益）")
    positions: list[PositionItemModel] = Field(default_factory=list, description="持仓情况")
    initial_amount: float = Field(default=0.0, description="初始金额")
    remaining_cash: float = Field(default=0.0, description="剩余资金")
    start_date: str = Field(default="", description="开始日期 YYYY-MM-DD")
    execution_days: int = Field(default=0, description="执行天数")


class StrategyExecutionItem(BaseModel):
    """策略执行结果响应"""
    id: str = Field(default="", description="记录 ID")
    user_strategy_id: str = Field(default="", description="用户策略 ID")
    current_return_rate: float = Field(default=0.0, description="目前收益率")
    current_profit: float = Field(default=0.0, description="目前收益金额")
    annualized_return_rate: float = Field(default=0.0, description="年化收益率")
    benchmark_return_rate: float = Field(default=0.0, description="基准收益率")
    positions: list[PositionItemModel] = Field(default_factory=list, description="持仓情况")
    initial_amount: float = Field(default=0.0, description="初始金额")
    remaining_cash: float = Field(default=0.0, description="剩余资金")
    start_date: str = Field(default="", description="开始日期")
    execution_days: int = Field(default=0, description="执行天数")
    update_time: str = Field(default="", description="最后更新时间")


# ==================== 订单 ====================

class OrderCreateRequest(BaseModel):
    """创建订单请求"""
    user_strategy_id: str = Field(..., description="用户策略 ID")
    stock_code: str = Field(..., description="股票代码")
    entrust_quantity: int = Field(..., ge=0, description="委托数量")
    trade_price: float = Field(..., ge=0, description="交易价格")
    trade_quantity: int = Field(default=0, ge=0, description="交易数量")
    position_price: float = Field(default=0.0, ge=0, description="持仓价")
    profit_rate: float = Field(default=0.0, description="收益率")
    profit_amount: float = Field(default=0.0, description="收益额")
    commission_fee: float = Field(default=0.0, ge=0, description="手续费")
    status: str = Field(default="委托", description="订单状态")
    action: str = Field(default="买入", description="买卖方向：买入/卖出")
    create_time: str = Field(default="", description="订单时间")


class OrderUpdateStatusRequest(BaseModel):
    """更新订单状态请求"""
    status: str = Field(..., description="订单状态")


class OrderUpdateRequest(BaseModel):
    """更新订单请求（所有字段可选，仅更新传入的字段）"""
    stock_code: str | None = Field(default=None, description="股票代码")
    entrust_quantity: int | None = Field(default=None, ge=0, description="委托数量")
    trade_price: float | None = Field(default=None, ge=0, description="交易价格")
    trade_quantity: int | None = Field(default=None, ge=0, description="交易数量")
    position_price: float | None = Field(default=None, ge=0, description="持仓价")
    profit_rate: float | None = Field(default=None, description="收益率")
    profit_amount: float | None = Field(default=None, description="收益额")
    commission_fee: float | None = Field(default=None, ge=0, description="手续费")
    status: str | None = Field(default=None, description="订单状态")
    action: str | None = Field(default=None, description="买卖方向：买入/卖出")
    create_time: str | None = Field(default=None, description="订单时间")


class OrderInfo(BaseModel):
    """订单信息响应"""
    id: str = Field(default="", description="订单 ID")
    user_strategy_id: str = Field(default="", description="用户策略 ID")
    stock_code: str = Field(default="", description="股票代码")
    entrust_quantity: int = Field(default=0, description="委托数量")
    trade_price: float = Field(default=0.0, description="交易价格")
    trade_quantity: int = Field(default=0, description="交易数量")
    position_price: float = Field(default=0.0, description="持仓价")
    profit_rate: float = Field(default=0.0, description="收益率")
    profit_amount: float = Field(default=0.0, description="收益额")
    commission_fee: float = Field(default=0.0, description="手续费")
    status: str = Field(default="委托", description="订单状态")
    action: str = Field(default="买入", description="买卖方向：买入/卖出")
    create_time: str = Field(default="", description="订单时间")


# ==================== 行情 ====================

class KlineRequest(BaseModel):
    """K线查询请求（Query 参数）"""
    code: str = Field(..., description="股票代码")


# ==================== 财务 ====================

class ProfitRequest(BaseModel):
    """利润查询请求"""
    code: str = Field(..., description="股票代码")
    report_date: str = Field(default="", description="报告期，如 2025-12-31")


# ==================== 股票信息 ====================

class StockInfoItem(BaseModel):
    """股票基本信息响应"""
    code: str = ""
    name: str = ""
    full_name: str = ""
    board: str = ""
    industry: str = ""
    concept: str = ""
    list_date: str = ""


# ==================== 股票筛选 ====================

class StockScreenerItem(BaseModel):
    """股票筛选结果条目"""
    code: str = Field(default="", description="股票代码")
    name: str = Field(default="", description="股票名称")
    ttm_pe: float = Field(default=0.0, description="TTM 市盈率")
    total_market_cap: float = Field(default=0.0, description="总市值")
    profit_margin: float = Field(default=0.0, description="利润率（净利润/营业总收入）")
    report_date: str = Field(default="", description="财报报告期")


# ==================== 热门行业 ====================

class HotIndustryItem(BaseModel):
    """热门行业条目"""
    name: str = Field(default="", description="行业名称")


class HotIndustryCreateRequest(BaseModel):
    """添加热门行业请求"""
    name: str = Field(..., description="行业名称")


# ==================== 策略选股 ====================


class StrategySelectStockCreateRequest(BaseModel):
    """添加策略选股记录请求"""
    strategy_id: str = Field(..., description="策略 ID")
    codes: list[str] = Field(..., description="股票代码列表")


class StrategySelectStockItem(BaseModel):
    """策略选股记录响应"""
    id: str = Field(default="", description="记录 ID")
    strategy_id: str = Field(default="", description="策略 ID")
    code: str = Field(default="", description="股票代码")
    create_time: str = Field(default="", description="创建时间")


class StrategySelectStockWithRtItem(BaseModel):
    """策略选股记录 + 最新实时行情组合响应"""
    id: str = Field(default="", description="记录 ID")
    strategy_id: str = Field(default="", description="策略 ID")
    code: str = Field(default="", description="股票代码")
    create_time: str = Field(default="", description="策略选股创建时间")
    # 实时行情字段
    name: str = Field(default="", description="股票名称")
    price: float = Field(default=0.0, description="最新价")
    change_percent: float = Field(default=0.0, description="涨跌幅")
    change_amount: float = Field(default=0.0, description="涨跌额")
    volume: int = Field(default=0, description="成交量")
    amount: float = Field(default=0.0, description="成交额")
    amp: float = Field(default=0.0, description="振幅")
    high: float = Field(default=0.0, description="最高价")
    low: float = Field(default=0.0, description="最低价")
    open: float = Field(default=0.0, description="今开价")
    preclose: float = Field(default=0.0, description="昨收价")
    qrr: float = Field(default=0.0, description="量比")
    turnover: float = Field(default=0.0, description="换手率")
    rt_update_time: str = Field(default="", description="实时行情更新时间")


# ==================== 交易信号 ====================


class TradeSignalCreateRequest(BaseModel):
    """添加交易信号请求"""
    strategy_id: str = Field(..., description="策略 ID")
    stock_code: str = Field(..., description="股票代码")
    trade_price: float = Field(default=0.0, description="交易价格")
    profit_rate: float = Field(default=0.0, description="收益率")
    profit_amount: float = Field(default=0.0, description="收益金额")
    action: str = Field(default="", description="买卖方向: 买入/卖出")
    reason: str = Field(default="", description="买卖信号原因")


class TradeSignalItem(BaseModel):
    """交易信号记录响应"""
    id: str = Field(default="", description="记录 ID")
    strategy_id: str = Field(default="", description="策略 ID")
    stock_code: str = Field(default="", description="股票代码")
    trade_price: float = Field(default=0.0, description="交易价格")
    profit_rate: float = Field(default=0.0, description="收益率")
    profit_amount: float = Field(default=0.0, description="收益金额")
    action: str = Field(default="", description="买卖方向: 买入/卖出")
    reason: str = Field(default="", description="买卖信号原因")
    create_time: str = Field(default="", description="创建时间")


# ==================== 系统消息 ====================


class SystemMessageCreateRequest(BaseModel):
    """创建系统消息请求"""
    title: str = Field(..., description="消息标题")
    message: str = Field(..., description="推送的消息内容")
    user_ids: list[str] = Field(default_factory=list, description="推送目标用户 ID 列表，为空表示广播给所有用户")


class SystemMessageItem(BaseModel):
    """系统消息响应"""
    id: str = Field(default="", description="记录 ID")
    title: str = Field(default="", description="消息标题")
    message: str = Field(default="", description="消息内容")
    create_id: str = Field(default="", description="创建者用户 ID")
    user_ids: list[str] = Field(default_factory=list, description="推送目标用户 ID 列表，为空表示广播")
    create_time: str = Field(default="", description="创建时间")

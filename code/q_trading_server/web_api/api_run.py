'''
Author: liguoqiang
Date: 2024-07-18 10:36:48
LastEditors: liguoqiang
LastEditTime: 2024-08-23 21:06:58
Description: 启动FastApi服务
'''
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from fastapi.openapi.docs import get_swagger_ui_html
from web_api.routers import (
    blacklist,
    data_agent_pool_stocks,
    data_agent_industry_stocks,
    data_agent,
    factor,
    finance,
    market,
    order,
    stock_info,
    stock_pool,
    stock_screener,
    strategy,
    strategy_select_stocks,
    system_message,
    trade_signal,
    user,
    user_factor,
    user_preference,
    user_strategy,
    workflow_service,
    workflow_service_user_strategy
)

app = FastAPI(
    title="Q Trading Server API",
    version="1.0.0",
    docs_url=None,  # 禁用默认 /docs，使用下面自定义的（替换 jsdelivr CDN）
)

# CORS 中间件 —— 允许浏览器跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自定义 /docs 路由，使用国内可访问的 CDN
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Swagger UI",
        swagger_js_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.9.1/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.9.1/swagger-ui.css",
    )

# 注册路由
app.include_router(user.router)
app.include_router(stock_pool.router)
app.include_router(strategy.router)
app.include_router(factor.router)
app.include_router(user_factor.router)
app.include_router(market.router)
app.include_router(finance.router)
app.include_router(stock_info.router)
app.include_router(blacklist.router)
app.include_router(stock_screener.router)
app.include_router(order.router)
app.include_router(user_strategy.router)
app.include_router(strategy_select_stocks.router)
app.include_router(user_preference.router)
app.include_router(trade_signal.router)
app.include_router(data_agent.router)
app.include_router(data_agent_pool_stocks.router)
app.include_router(data_agent_industry_stocks.router)
app.include_router(system_message.router)
app.include_router(workflow_service.router)
app.include_router(workflow_service_user_strategy.router)

class BizException(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


@app.exception_handler(BizException)
async def biz_exception_handler(request: Request, exc: BizException):
    return JSONResponse(
        status_code=400,
        content={
            "code": exc.code,
            "message": exc.message
        }
    )
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "message": "参数错误",
            "errors": exc.errors()
        }
    )
class ExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"message": "统一异常处理"}
            )

app.add_middleware(ExceptionMiddleware)

'''
function: 
description: 
return {*}
'''
def api_run(host, port):
    # Run the FastAPI app directly. Disable reload in container/runtime
    # to avoid multiprocessing spawn/import issues when using uvicorn's
    # autoreload in a packaged/entrypoint script.
    uvicorn.run(
        app,
        host=host,
        port=int(port),
        reload=False
    )
import os
import sys
import datetime
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any, AsyncGenerator
from fastapi import FastAPI, HTTPException, Query, Path, Request, Depends
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field
import pytz
from dotenv import load_dotenv
from supabase import create_client, Client
from sse_starlette.sse import EventSourceResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport

# 配置日志
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 初始化Supabase客户端
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("请在.env文件中设置SUPABASE_URL和SUPABASE_KEY")

# 直接初始化Supabase客户端，不使用额外选项
supabase: Client = create_client(supabase_url, supabase_key)

# 初始化FastAPI应用
app = FastAPI(
    title="股票分时数据查询API",
    description="提供股票分时数据查询服务，支持查询最新X条数据和时间区间数据，并支持MCP工具",
    version="1.0.0"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，生产环境中应该限制为特定域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Type", 
        "Cache-Control", 
        "Content-Disposition", 
        "X-Accel-Buffering",
        "Connection"
    ],
)

# 表名映射
TABLE_MAPPING = {
    "15min": "bars_15min",
    "30min": "bars_30min",
    "60min": "bars_60min"
}

# 数据模型
class StockBar(BaseModel):
    time: datetime.datetime
    stock_code: str
    open: float
    close: float
    high: float
    low: float
    change: Optional[float] = None
    change_percent: Optional[float] = None

class StockBarResponse(BaseModel):
    data: List[StockBar]
    count: int
    time_level: str
    stock_code: str

# 辅助函数
def format_stock_data(rows: List[Dict[str, Any]], calculate_change: bool = True) -> List[Dict[str, Any]]:
    """
    格式化股票数据，计算涨跌幅
    """
    formatted_data = []
    
    # 按时间排序
    sorted_rows = sorted(rows, key=lambda x: x['time'])
    
    for i, row in enumerate(sorted_rows):
        data_item = {
            "time": row["time"],
            "stock_code": row["stock_code"],
            "open": row["open"],
            "close": row["close"],
            "high": row["high"],
            "low": row["low"]
        }
        
        # 计算涨跌幅
        if calculate_change and i > 0:
            prev_close = sorted_rows[i-1]["close"]
            data_item["change"] = round(row["close"] - prev_close, 2)
            data_item["change_percent"] = round((row["close"] - prev_close) / prev_close * 100, 2)
        
        formatted_data.append(data_item)
    
    return formatted_data

# API端点
@app.get("/")
async def root():
    return {"message": "股票分时数据查询API服务正在运行"}

@app.get("/api/latest_bars/{time_level}/{stock_code}", response_model=StockBarResponse, summary="查询最新X条数据")
async def get_latest_bars(
    time_level: str = Path(..., description="时间级别，可选值为15min, 30min, 60min"),
    stock_code: str = Path(..., description="股票代码，例如sz002353"),
    end_time: Optional[datetime.datetime] = Query(None, description="结束时间，格式为YYYY-MM-DDTHH:MM:SS"),
    limit: int = Query(10, description="返回的记录数量，默认为10")
):
    """
    查询特定股票在给定结束时间前推X条分时数据
    
    - **time_level**: 时间级别，可选值为15min, 30min, 60min
    - **stock_code**: 股票代码，例如sz002353
    - **end_time**: 结束时间（可选），格式为YYYY-MM-DDTHH:MM:SS
    - **limit**: 返回的记录数量（可选），默认为10
    """
    # 验证时间级别
    if time_level not in TABLE_MAPPING:
        raise HTTPException(status_code=400, detail=f"无效的时间级别: {time_level}，可选值为: {', '.join(TABLE_MAPPING.keys())}")
    
    table_name = TABLE_MAPPING[time_level]
    query = supabase.table(table_name).select("*").eq("stock_code", stock_code)
    
    # 如果指定了结束时间，则添加时间过滤条件
    if end_time:
        query = query.lte("time", end_time.isoformat())
    
    # 按时间倒序排列并限制结果数量
    query = query.order("time", desc=True).limit(limit)
    
    response = query.execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail=f"未找到股票 {stock_code} 在 {time_level} 级别的数据")
    
    # 按时间正序排列，以便计算涨跌幅
    data = sorted(response.data, key=lambda x: x['time'])
    
    # 格式化数据
    formatted_data = format_stock_data(data)
    
    return {
        "data": formatted_data,
        "count": len(formatted_data),
        "time_level": time_level,
        "stock_code": stock_code
    }

@app.get("/api/bars_range/{time_level}/{stock_code}", response_model=StockBarResponse, summary="查询时间区间数据")
async def get_bars_range(
    time_level: str = Path(..., description="时间级别，可选值为15min, 30min, 60min"),
    stock_code: str = Path(..., description="股票代码，例如sz002353"),
    start_time: datetime.datetime = Query(..., description="开始时间，格式为YYYY-MM-DDTHH:MM:SS"),
    end_time: datetime.datetime = Query(..., description="结束时间，格式为YYYY-MM-DDTHH:MM:SS")
):
    """
    查询特定股票在给定时间区间内的分时数据
    
    - **time_level**: 时间级别，可选值为15min, 30min, 60min
    - **stock_code**: 股票代码，例如sz002353
    - **start_time**: 开始时间，格式为YYYY-MM-DDTHH:MM:SS
    - **end_time**: 结束时间，格式为YYYY-MM-DDTHH:MM:SS
    """
    # 验证时间级别
    if time_level not in TABLE_MAPPING:
        raise HTTPException(status_code=400, detail=f"无效的时间级别: {time_level}，可选值为: {', '.join(TABLE_MAPPING.keys())}")
    
    # 验证时间范围
    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
    
    table_name = TABLE_MAPPING[time_level]
    
    # 构建查询
    query = supabase.table(table_name)\
        .select("*")\
        .eq("stock_code", stock_code)\
        .gte("time", start_time.isoformat())\
        .lte("time", end_time.isoformat())\
        .order("time")
    
    response = query.execute()
    
    if not response.data:
        raise HTTPException(
            status_code=404, 
            detail=f"未找到股票 {stock_code} 在 {time_level} 级别从 {start_time} 到 {end_time} 的数据"
        )
    
    # 格式化数据
    formatted_data = format_stock_data(response.data)
    
    return {
        "data": formatted_data,
        "count": len(formatted_data),
        "time_level": time_level,
        "stock_code": stock_code
    }

# 初始化MCP实例
mcp = FastMCP("股票分时数据查询工具")

# 定义MCP工具
@mcp.tool(
    name="stock_data_mcp_get_latest_bars",
    description="查询特定股票在给定结束时间前推X条分时数据，支持15分钟、30分钟和60分钟K线"
)
def stock_data_mcp_get_latest_bars(
    time_level: str,
    stock_code: str,
    end_time: Optional[str] = None,
    limit: int = 10
) -> str:
    """查询特定股票在给定结束时间前推X条分时数据
    
    参数:
        time_level: 时间级别，可选值为15min, 30min, 60min
        stock_code: 股票代码，例如sz002353
        end_time: 结束时间（可选），格式为YYYY-MM-DDTHH:MM:SS
        limit: 返回的记录数量（可选），默认为10
    """
    # 这个函数将通过SSE事件生成器调用，不需要在这里实现具体逻辑
    # 实际逻辑在stock_data_event_generator中处理
    pass

@mcp.tool(
    name="stock_data_mcp_get_bars_range",
    description="查询特定股票在给定时间区间内的分时数据，支持15分钟、30分钟和60分钟K线"
)
def stock_data_mcp_get_bars_range(
    time_level: str,
    stock_code: str,
    start_time: str,
    end_time: str
) -> str:
    """查询特定股票在给定时间区间内的分时数据
    
    参数:
        time_level: 时间级别，可选值为15min, 30min, 60min
        stock_code: 股票代码，例如sz002353
        start_time: 开始时间，格式为YYYY-MM-DDTHH:MM:SS
        end_time: 结束时间，格式为YYYY-MM-DDTHH:MM:SS
    """
    # 这个函数将通过SSE事件生成器调用，不需要在这里实现具体逻辑
    # 实际逻辑在stock_data_event_generator中处理
    pass

# 实现MCP工具的实际处理逻辑
@mcp.tool("stock_data_mcp_get_latest_bars")
async def impl_stock_data_mcp_get_latest_bars(request: Request, params: Dict[str, Any]) -> AsyncGenerator[str, None]:
    request_id = id(request)
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"SSE连接开始 [ID:{request_id}] 来自 {client_host} - 工具: stock_data_mcp_get_latest_bars, 参数: {params}")
    
    try:
        # 发送初始连接确认消息
        logger.info(f"SSE [ID:{request_id}] 发送连接确认消息")
        yield json.dumps({
            "status": "connected", 
            "message": "SSE连接已建立", 
            "request_id": str(request_id)
        })
        
        # 处理日期时间参数
        if "end_time" in params and params["end_time"]:
            try:
                params["end_time"] = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
                logger.info(f"SSE [ID:{request_id}] 解析end_time: {params['end_time']}")
            except ValueError as ve:
                error_msg = f"无效的日期时间格式: {params['end_time']}，请使用ISO格式 YYYY-MM-DDTHH:MM:SS"
                logger.error(f"SSE [ID:{request_id}] 日期解析错误: {error_msg} - {str(ve)}")
                yield json.dumps({"error": error_msg, "status": "error"})
                return
        
        # 发送处理状态
        yield json.dumps({"status": "processing", "message": f"正在查询股票 {params['stock_code']} 的 {params['time_level']} 级别数据"})
        
        try:
            result = await get_latest_bars(
                time_level=params["time_level"],
                stock_code=params["stock_code"],
                end_time=params.get("end_time"),
                limit=params.get("limit", 10)
            )
            logger.info(f"SSE [ID:{request_id}] 成功获取最新数据: {params['stock_code']}, 记录数: {len(result['data'])}")
            
            # 转换结果为可序列化的字典
            result_dict = {
                "data": [dict(item) for item in result["data"]],
                "count": result["count"],
                "time_level": result["time_level"],
                "stock_code": result["stock_code"]
            }
            
            # 发送数据处理状态
            yield json.dumps({"status": "data_ready", "message": f"数据已准备就绪，共 {result['count']} 条记录"})
            
            # 发送结果
            logger.info(f"SSE [ID:{request_id}] 发送数据结果，记录数: {result['count']}")
            yield json.dumps(result_dict)
            
            # 发送完成消息
            logger.info(f"SSE [ID:{request_id}] 数据传输完成")
            yield json.dumps({"status": "completed", "message": "数据传输完成"})
            
        except Exception as func_err:
            error_msg = f"获取最新数据失败: {str(func_err)}"
            logger.error(f"SSE [ID:{request_id}] 函数调用错误: {error_msg}", exc_info=True)
            yield json.dumps({"error": error_msg, "status": "error"})
            return
            
    except Exception as e:
        error_msg = f"SSE生成器错误: {str(e)}"
        logger.error(f"SSE [ID:{request_id}] 未处理的异常: {error_msg}", exc_info=True)
        yield json.dumps({
            "error": error_msg, 
            "status": "error",
            "request_id": str(request_id)
        })

@mcp.tool_impl("stock_data_mcp_get_bars_range")
async def impl_stock_data_mcp_get_bars_range(request: Request, params: Dict[str, Any]) -> AsyncGenerator[str, None]:
    request_id = id(request)
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"SSE连接开始 [ID:{request_id}] 来自 {client_host} - 工具: stock_data_mcp_get_bars_range, 参数: {params}")
    
    try:
        # 发送初始连接确认消息
        logger.info(f"SSE [ID:{request_id}] 发送连接确认消息")
        yield json.dumps({
            "status": "connected", 
            "message": "SSE连接已建立", 
            "request_id": str(request_id)
        })
        
        # 处理日期时间参数
        try:
            start_time = datetime.datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
            end_time = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
            logger.info(f"SSE [ID:{request_id}] 解析时间区间: {start_time} 至 {end_time}")
        except ValueError as ve:
            error_msg = f"无效的日期时间格式: {str(ve)}，请使用ISO格式 YYYY-MM-DDTHH:MM:SS"
            logger.error(f"SSE [ID:{request_id}] 日期解析错误: {error_msg}")
            yield json.dumps({"error": error_msg, "status": "error"})
            return
        
        # 发送处理状态
        yield json.dumps({"status": "processing", "message": f"正在查询股票 {params['stock_code']} 在 {start_time} 至 {end_time} 期间的 {params['time_level']} 级别数据"})
        
        try:
            result = await get_bars_range(
                time_level=params["time_level"],
                stock_code=params["stock_code"],
                start_time=start_time,
                end_time=end_time
            )
            logger.info(f"SSE [ID:{request_id}] 成功获取时间区间数据: {params['stock_code']}, 记录数: {len(result['data'])}")
            
            # 转换结果为可序列化的字典
            result_dict = {
                "data": [dict(item) for item in result["data"]],
                "count": result["count"],
                "time_level": result["time_level"],
                "stock_code": result["stock_code"]
            }
            
            # 发送数据处理状态
            yield json.dumps({"status": "data_ready", "message": f"数据已准备就绪，共 {result['count']} 条记录"})
            
            # 发送结果
            logger.info(f"SSE [ID:{request_id}] 发送数据结果，记录数: {result['count']}")
            yield json.dumps(result_dict)
            
            # 发送完成消息
            logger.info(f"SSE [ID:{request_id}] 数据传输完成")
            yield json.dumps({"status": "completed", "message": "数据传输完成"})
            
        except Exception as func_err:
            error_msg = f"获取时间区间数据失败: {str(func_err)}"
            logger.error(f"SSE [ID:{request_id}] 函数调用错误: {error_msg}", exc_info=True)
            yield json.dumps({"error": error_msg, "status": "error"})
            return
            
    except Exception as e:
        error_msg = f"SSE生成器错误: {str(e)}"
        logger.error(f"SSE [ID:{request_id}] 未处理的异常: {error_msg}", exc_info=True)
        yield json.dumps({
            "error": error_msg, 
            "status": "error",
            "request_id": str(request_id)
        })

# MCP SSE 集成
MCP_BASE_PATH = "/sse"  # MCP 服务的基础路径

# 创建SSE服务器传输
sse_transport = SseServerTransport(mcp)

# 注册SSE路由
@app.get("/sse")
async def sse_get(request: Request):
    logger.info(f"SSE GET 请求: {request.url} 来自 {request.client.host if request.client else 'unknown'}")
    return await sse_transport.handle_get(request)

@app.post("/sse")
async def sse_post(request: Request):
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"SSE POST 请求: {request.url} 来自 {client_host}")
    
    try:
        # 使用SseServerTransport处理POST请求
        response = await sse_transport.handle_post(request)
        logger.info(f"SSE 响应已创建，开始流式传输数据")
        return response
    except Exception as e:
        error_msg = f"服务器处理SSE请求时出错: {str(e)}"
        logger.error(f"SSE处理错误: {error_msg}", exc_info=True)
        return JSONResponse({"error": error_msg}, status_code=500)

# 中间件处理SSE连接中断
class OperationCanceledMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            if isinstance(e, (asyncio.CancelledError, ConnectionResetError)) or "cancell" in str(e).lower():
                logger.info(f"客户端连接已关闭: {request.client.host if request.client else 'unknown'}, 路径: {request.url.path}, 错误: {str(e)}")
                # 返回一个响应，避免未处理的异常
                return JSONResponse(
                    status_code=499,  # 客户端关闭请求
                    content={"detail": "客户端已关闭连接", "status": "cancelled"}
                )
            else:
                # 重新抛出其他异常
                raise

# 添加中间件
app.add_middleware(OperationCanceledMiddleware)

# 错误处理
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # 获取请求信息用于日志记录
    client_host = request.client.host if request.client else "unknown"
    path = request.url.path
    method = request.method
    
    # 根据异常类型设置不同的状态码和错误消息
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        error_msg = str(exc.detail)
        log_level = logging.WARNING
    elif isinstance(exc, asyncio.CancelledError):
        status_code = 499  # 客户端关闭请求
        error_msg = "客户端关闭了请求"
        log_level = logging.INFO
    else:
        status_code = 500
        error_msg = f"服务器内部错误: {str(exc)}"
        log_level = logging.ERROR
    
    # 记录日志
    log_msg = f"{method} {path} 来自 {client_host} 失败: {error_msg}"
    if log_level == logging.ERROR:
        logger.error(log_msg, exc_info=True)
    elif log_level == logging.WARNING:
        logger.warning(log_msg)
    else:
        logger.info(log_msg)
    
    # 返回JSON响应
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error_msg,
            "status": "error",
            "path": str(path),
            "method": method
        },
    )

if __name__ == "__main__":
    import uvicorn
    import os
    
    # 从环境变量获取端口号，默认为8080（Cloud Run标准端口）
    port = int(os.environ.get("PORT", 8080))
    
    # 根据环境变量决定是否启用热重载
    env = os.environ.get("ENV", "development")
    reload = env == "development"
    
    # 日志级别根据环境设置
    log_level = "info" if env == "production" else "debug"
    
    # 启动服务器
    logger.info(f"启动服务器: 环境={env}, 端口={port}, 热重载={reload}, 日志级别={log_level}")
    uvicorn.run(
        "stock_data_api:app", 
        host="0.0.0.0", 
        port=port, 
        reload=reload,
        log_level=log_level,
        access_log=True,
        timeout_keep_alive=65  # 增加保持连接超时时间，有助于SSE长连接
    )
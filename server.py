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
logger.info(".env 文件已尝试加载")

# 初始化Supabase客户端
logger.info("正在检查 Supabase 环境变量...")
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    logger.critical("错误: SUPABASE_URL 和/或 SUPABASE_KEY 环境变量未设置。应用将退出。")
    sys.exit(1)
else:
    logger.info("Supabase 环境变量已找到。")

try:
    logger.info("正在初始化 Supabase 客户端...")
    supabase: Client = create_client(supabase_url, supabase_key)
    logger.info("Supabase 客户端初始化成功。")
except Exception as e:
    logger.critical(f"初始化 Supabase 客户端时出错: {e}", exc_info=True)
    sys.exit(1)

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
    "60min": "bars_60min",
    "daily": "bars_daily",
    "weekly": "bars_weekly"
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
    time_level: str = Path(..., description="时间级别，可选值为60min, daily, weekly"),
    stock_code: str = Path(..., description="股票代码，例如sz002353"),
    end_time: Optional[datetime.datetime] = Query(None, description="结束时间，格式为YYYY-MM-DDTHH:MM:SS"),
    limit: int = Query(10, description="返回的记录数量，默认为10")
):
    """
    查询特定股票在给定结束时间前推X条分时数据
    
    - **time_level**: 时间级别，可选值为60min, daily, weekly
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

# @app.get("/api/bars_range/{time_level}/{stock_code}", response_model=StockBarResponse, summary="查询时间区间数据")
# async def get_bars_range(
#     time_level: str = Path(..., description="时间级别，可选值为15min, 30min, 60min"),
#     stock_code: str = Path(..., description="股票代码，例如sz002353"),
#     start_time: datetime.datetime = Query(..., description="开始时间，格式为YYYY-MM-DDTHH:MM:SS"),
#     end_time: datetime.datetime = Query(..., description="结束时间，格式为YYYY-MM-DDTHH:MM:SS")
# ):
#     """
#     查询特定股票在给定时间区间内的分时数据
#     
#     - **time_level**: 时间级别，可选值为15min, 30min, 60min
#     - **stock_code**: 股票代码，例如sz002353
#     - **start_time**: 开始时间，格式为YYYY-MM-DDTHH:MM:SS
#     - **end_time**: 结束时间，格式为YYYY-MM-DDTHH:MM:SS
#     """
#     # 验证时间级别
#     if time_level not in TABLE_MAPPING:
#         raise HTTPException(status_code=400, detail=f"无效的时间级别: {time_level}，可选值为: {', '.join(TABLE_MAPPING.keys())}")
#     
#     # 验证时间范围
#     if start_time >= end_time:
#         raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
#     
#     table_name = TABLE_MAPPING[time_level]
#     
#     # 构建查询
#     query = supabase.table(table_name)\
#         .select("*")\
#         .eq("stock_code", stock_code)\
#         .gte("time", start_time.isoformat())\
#         .lte("time", end_time.isoformat())\
#         .order("time")
#     
#     response = query.execute()
#     
#     if not response.data:
#         raise HTTPException(
#             status_code=404, 
#             detail=f"未找到股票 {stock_code} 在 {time_level} 级别从 {start_time} 到 {end_time} 的数据"
#         )
#     
#     # 格式化数据
#     formatted_data = format_stock_data(response.data)
#     
#     return {
#         "data": formatted_data,
#         "count": len(formatted_data),
#         "time_level": time_level,
#         "stock_code": stock_code
#     }

# 初始化MCP实例
mcp = FastMCP("股票分时数据查询工具", description="提供股票分时数据查询和 SSE 流式推送的 MCP 服务")

# 挂载MCP到FastAPI应用
# 删除这一行
# mcp.mount_to_app(app, "/mcp")

# 新增（或替换为）
app.mount("/mcp", mcp.sse_app())

# 实现MCP工具的实际处理逻辑
@mcp.tool("stock_data_mcp_get_latest_bars")
async def impl_stock_data_mcp_get_latest_bars(params: Dict[str, Any]) -> Any:
    """
    获取最新的分时数据（MCP 工具）

    参数:
        time_level: 分钟级别，如 "60min"
        stock_code: 股票代码，例如 "sz002353"
        end_time: 结束时间，ISO 格式字符串，可选
        limit: 返回记录数量，默认 10

    返回:
        dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
    """
    try:
        end_time_value = None
        if params.get("end_time"):
            end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))

        result = await get_latest_bars(
            time_level=params["time_level"],
            stock_code=params["stock_code"],
            end_time=end_time_value,
            limit=params.get("limit", 10)
        )
        return result
    except Exception as e:
        logger.error(f"获取最新分时数据失败: {e}", exc_info=True)
        raise

# @mcp.tool("stock_data_mcp_get_bars_range")
# async def impl_stock_data_mcp_get_bars_range(params: Dict[str, Any]) -> Any:
#     """
#     获取指定时间区间的分时数据（MCP 工具）
#
#     参数:
#         time_level: 分钟级别，如 "15min"、"30min"、"60min"
#         stock_code: 股票代码，例如 "sz002353"
#         start_time: 起始时间，ISO 格式字符串
#         end_time: 结束时间，ISO 格式字符串
#
#     返回:
#         dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
#     """
#     try:
#         start_time_value = datetime.datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
#         end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
#
#         result = await get_bars_range(
#             time_level=params["time_level"],
#             stock_code=params["stock_code"],
#             start_time=start_time_value,
#             end_time=end_time_value
#         )
#         return result
#     except Exception as e:
#         logger.error(f"获取时间区间分时数据失败: {e}", exc_info=True)
#         raise


@mcp.tool("stock_data_mcp_get_latest_daily_bars")
async def impl_stock_data_mcp_get_latest_daily_bars(params: Dict[str, Any]) -> Any:
    """
    获取最新日线数据（MCP 工具）

    参数:
        stock_code: 股票代码，例如 "sz002353"
        end_time: 结束时间，ISO 格式字符串，可选
        limit: 返回记录数量，默认 10

    返回:
        dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
    """
    try:
        end_time_value = None
        if params.get("end_time"):
            end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))

        return await get_latest_bars(
            time_level="daily",
            stock_code=params["stock_code"],
            end_time=end_time_value,
            limit=params.get("limit", 10)
        )
    except Exception as e:
        logger.error(f"获取最新日线数据失败: {e}", exc_info=True)
        raise


@mcp.tool("stock_data_mcp_get_latest_weekly_bars")
async def impl_stock_data_mcp_get_latest_weekly_bars(params: Dict[str, Any]) -> Any:
    """
    获取最新周线数据（MCP 工具）

    参数:
        stock_code: 股票代码，例如 "sz002353"
        end_time: 结束时间，ISO 格式字符串，可选
        limit: 返回记录数量，默认 10

    返回:
        dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
    """
    try:
        end_time_value = None
        if params.get("end_time"):
            end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))

        return await get_latest_bars(
            time_level="weekly",
            stock_code=params["stock_code"],
            end_time=end_time_value,
            limit=params.get("limit", 10)
        )
    except Exception as e:
        logger.error(f"获取最新周线数据失败: {e}", exc_info=True)
        raise


# @mcp.tool("stock_data_mcp_get_latest_monthly_bars")
# async def impl_stock_data_mcp_get_latest_monthly_bars(params: Dict[str, Any]) -> Any:
#     """
#     获取最新月线数据（MCP 工具）
#
#     参数:
#         stock_code: 股票代码，例如 "sz002353"
#         end_time: 结束时间，ISO 格式字符串，可选
#         limit: 返回记录数量，默认 10
#
#     返回:
#         dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
#     """
#     try:
#         end_time_value = None
#         if params.get("end_time"):
#             end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
#
#         return await get_latest_bars(
#             time_level="monthly",
#             stock_code=params["stock_code"],
#             end_time=end_time_value,
#             limit=params.get("limit", 10)
#         )
#     except Exception as e:
#         logger.error(f"获取最新月线数据失败: {e}", exc_info=True)
#         raise


# @mcp.tool("stock_data_mcp_get_daily_bars_range")
# async def impl_stock_data_mcp_get_daily_bars_range(params: Dict[str, Any]) -> Any:
#     """
#     获取指定时间区间的日线数据（MCP 工具）
#
#     参数:
#         stock_code: 股票代码，例如 "sz002353"
#         start_time: 起始时间，ISO 格式字符串
#         end_time: 结束时间，ISO 格式字符串
#
#     返回:
#         dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
#     """
#     try:
#         start_time_value = datetime.datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
#         end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
#
#         return await get_bars_range(
#             time_level="daily",
#             stock_code=params["stock_code"],
#             start_time=start_time_value,
#             end_time=end_time_value
#         )
#     except Exception as e:
#         logger.error(f"获取日线区间数据失败: {e}", exc_info=True)
#         raise


# @mcp.tool("stock_data_mcp_get_weekly_bars_range")
# async def impl_stock_data_mcp_get_weekly_bars_range(params: Dict[str, Any]) -> Any:
#     """
#     获取指定时间区间的周线数据（MCP 工具）
#
#     参数:
#         stock_code: 股票代码，例如 "sz002353"
#         start_time: 起始时间，ISO 格式字符串
#         end_time: 结束时间，ISO 格式字符串
#
#     返回:
#         dict: 查询结果，包含数据列表、记录数、时间级别和股票代码
#     """
#     try:
#         start_time_value = datetime.datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
#         end_time_value = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
#
#         return await get_bars_range(
#             time_level="weekly",
#             stock_code=params["stock_code"],
#             start_time=start_time_value,
#             end_time=end_time_value
#         )
#     except Exception as e:
#         logger.error(f"获取
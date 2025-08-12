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
from starlette.applications import Starlette
from starlette.routing import Mount, Route

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

# --- MCP over SSE Integration ---

def create_sse_server(mcp: FastMCP):
    """Create a Starlette app that handles SSE connections and message handling"""
    # The transport needs to know the absolute path for POST messages
    transport = SseServerTransport("/sse/messages/")

    async def handle_sse(request: Request):
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0], streams[1], mcp._mcp_server.create_initialization_options()
            )

    routes = [
        Route("/", endpoint=handle_sse),
        Mount("/messages/", app=transport.handle_post_message),
    ]

    return Starlette(routes=routes)

# Initialize FastMCP
mcp = FastMCP(
    name="Intraday Data MCP Server",
    description="MCP server for querying intraday stock data."
)

# Define MCP tools by wrapping existing API functions
@mcp.tool(
    name="get_latest_bars",
    description="Get the latest N bars for a stock at a specific time level."
)
async def mcp_get_latest_bars(
    time_level: str = Query(..., description="Time level: 60min, daily, weekly"),
    stock_code: str = Query(..., description="Stock code, e.g., sz002353"),
    end_time: Optional[datetime.datetime] = Query(None, description="End time (YYYY-MM-DDTHH:MM:SS)"),
    limit: int = Query(10, description="Number of records to return, default is 10")
) -> Dict[str, Any]:
    """MCP tool to fetch latest stock bars."""
    try:
        response = await get_latest_bars(time_level, stock_code, end_time, limit)
        # The function already returns a dictionary that can be serialized
        return response
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e), "status_code": 500}

# Mount the SSE server onto the FastAPI app
app.mount("/sse", create_sse_server(mcp))

# 运行 FastAPI 应用 (for local development)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
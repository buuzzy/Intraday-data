import os
import sys
import datetime
import logging
from typing import Optional, List, Dict, Any

import tushare as ts
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from mcp.server.sse import SseServerTransport

# --- 1. 日志配置 ---
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. 加载环境变量 ---
load_dotenv()
logger.info(".env 文件已尝试加载")

# --- 3. 初始化 Supabase 客户端 ---
logger.info("正在检查 Supabase 环境变量...")
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Optional[Client] = None

if not supabase_url or not supabase_key:
    logger.critical("错误: SUPABASE_URL 和/或 SUPABASE_KEY 环境变量未设置。应用将退出。")
    sys.exit(1)
else:
    logger.info("Supabase 环境变量已找到。")
    try:
        logger.info("正在初始化 Supabase 客户端...")
        supabase = create_client(supabase_url, supabase_key)
        logger.info("Supabase 客户端初始化成功。")
    except Exception as e:
        logger.critical(f"初始化 Supabase 客户端时出错: {e}", exc_info=True)
        sys.exit(1)

# --- 4. 初始化 Tushare 客户端 ---
logger.info("正在检查 Tushare 环境变量...")
tushare_token = os.environ.get("TUSHARE_TOKEN")

# 【修复 2】移除了错误的 'ts.ProApi' 类型提示
tushare_pro_api = None 

if not tushare_token:
    logger.warning("TUSHARE_TOKEN 环境变量未设置。Tushare 相关工具 (如 search_stocks) 将不可用。")
else:
    logger.info("Tushare 环境变量已找到。")
    try:
        logger.info("正在初始化 Tushare Pro API...")
        tushare_pro_api = ts.pro_api(tushare_token)
        
        # 【修复 1】执行测试查询以定义 test_df，并验证 token
        logger.info("正在验证 Tushare token (执行测试查询)...")
        test_df = tushare_pro_api.stock_basic(limit=1) 
        
        if test_df.empty:
            logger.warning("Tushare token 已设置，但验证失败 (stock_basic 返回为空)。")
            tushare_pro_api = None # 验证失败，设为 None
        else:
            logger.info("Tushare Pro API 初始化并验证成功。")
            
    except Exception as e:
        logger.error(f"初始化 Tushare Pro API 时出错: {e}", exc_info=True)
        tushare_pro_api = None # 出现异常，明确设为 None

# --- 5. 初始化 FastAPI 应用 ---
app = FastAPI(
    title="股票数据查询API (Supabase + Tushare)",
    description="提供股票分时数据查询 (Supabase) 和股票信息搜索 (Tushare) 服务",
    version="1.1.0"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# --- 6. Supabase 相关定义 ---
TABLE_MAPPING = {
    "60min": "bars_60min",
    "daily": "bars_daily",
    "weekly": "bars_weekly"
}

class StockBar(BaseModel):
    time: datetime.datetime
    stock_code: str
    open: float
    high: float
    low: float
    close: float

def parse_end_time(end_time_str: Optional[str]) -> Optional[datetime.datetime]:
    """解析 ISO 格式的时间字符串为 datetime 对象"""
    if not end_time_str:
        return None
    try:
        return datetime.datetime.fromisoformat(end_time_str)
    except ValueError as e:
        logger.error(f"时间格式解析失败: {end_time_str}, error={str(e)}")
        raise ValueError(f"无效的时间格式: {end_time_str}，期望格式为 YYYY-MM-DDTHH:MM:SS")

def format_stock_data(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """格式化股票数据，返回 OHLC 四个价格"""
    sorted_rows = sorted(rows, key=lambda x: x['time'])
    formatted_data = []
    for row in sorted_rows:
        formatted_data.append({
            "time": row["time"],
            "stock_code": row["stock_code"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"]
        })
    return formatted_data

# --- 7. API 端点 (Supabase) ---
@app.get("/")
async def root():
    return {"message": "股票数据查询API服务 (Supabase + Tushare) 正在运行"}

@app.get("/api/latest_bars/{time_level}/{stock_code}", summary="查询最新X条数据 (Supabase)")
async def get_latest_bars(
    time_level: str = Path(..., description="时间级别，可选值为60min, daily, weekly"),
    stock_code: str = Path(..., description="股票代码，例如sz002353"),
    end_time: Optional[datetime.datetime] = Query(None, description="结束时间，格式为YYYY-MM-DDTHH:MM:SS"),
    limit: int = Query(10, description="返回的记录数量，默认为10")
) -> List[StockBar]:
    """
    查询特定股票在给定结束时间前推X条分时数据
    """
    logger.info(f"查询请求: time_level={time_level}, stock_code={stock_code}, end_time={end_time}, limit={limit}")

    if not supabase:
         logger.error("Supabase 客户端未初始化。")
         raise HTTPException(status_code=500, detail="数据库客户端未初始化")

    if time_level not in TABLE_MAPPING:
        logger.warning(f"无效的时间级别: {time_level}")
        raise HTTPException(status_code=400, detail=f"无效的时间级别: {time_level}，可选值为: {', '.join(TABLE_MAPPING.keys())}")

    table_name = TABLE_MAPPING[time_level]
    logger.debug(f"使用表: {table_name}")

    try:
        query = supabase.table(table_name).select("*").eq("stock_code", stock_code)
        if end_time:
            query = query.lte("time", end_time.isoformat())
        query = query.order("time", desc=True).limit(limit)
        response = query.execute()
        logger.debug(f"Supabase 查询成功，返回 {len(response.data) if response.data else 0} 条记录")

    except Exception as e:
        logger.error(f"Supabase 查询失败: table={table_name}, stock_code={stock_code}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"数据库查询失败: {str(e)}")

    if not response.data:
        logger.warning(f"未找到数据: stock_code={stock_code}, time_level={time_level}")
        raise HTTPException(status_code=44, detail=f"未找到股票 {stock_code} 在 {time_level} 级别的数据")

    try:
        formatted_data = format_stock_data(response.data)
        logger.info(f"成功返回 {len(formatted_data)} 条格式化数据")
        return formatted_data
    except Exception as e:
        logger.error(f"数据格式化失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"数据处理失败: {str(e)}")

# --- 8. MCP over SSE Integration ---

mcp = FastMCP(
    name="Stock Data MCP Server (Supabase + Tushare)",
    description="MCP server for querying stock data."
)

@mcp.prompt()
def usage_guide() -> str:
    """Provide a usage guide for the MCP tool."""
    return """欢迎使用股票数据 MCP 服务器!

可用工具:

1. get_latest_bars (来自 Supabase)
   - 功能: 获取特定股票、特定时间级别的最新 N 条 K 线数据。
   - 示例: > get_latest_bars(time_level="daily", stock_code="sz002353", limit=5)

2. search_stocks (来自 Tushare)
   - 功能: 根据关键词（代码、简称或名称）搜索股票信息。
   - 示例: > search_stocks(keyword="茅台")
   - 示例: > search_stocks(keyword="600519")
"""

# --- MCP Tool 1 (Supabase) ---
@mcp.tool(
    name="get_latest_bars",
    description="Get the latest N bars for a stock at a specific time level (from Supabase). Returns list of OHLC data."
)
async def mcp_get_latest_bars(
    time_level: str,
    stock_code: str,
    end_time: Optional[str] = None,
    limit: int = 10
) -> List[StockBar]:
    """MCP tool to fetch latest stock bars.
    Args:
        time_level: Time level (60min, daily, weekly)
        stock_code: Stock code (e.g., sz002353)
        end_time: Optional end time in ISO format (YYYY-MM-DDTHH:MM:SS)
        limit: Number of records to return (default 10)
    """
    try:
        end_time_dt = parse_end_time(end_time)
        return await get_latest_bars(time_level, stock_code, end_time_dt, limit)
    except ValueError as e:
        logger.error(f"MCP tool (get_latest_bars) 参数错误: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException as e:
        logger.error(f"MCP tool (get_latest_bars) error: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"MCP tool (get_latest_bars) unexpected error: {str(e)}", exc_info=True)
        raise

# --- MCP Tool 2 (Tushare) - 从 B 脚本中添加 ---
@mcp.tool(
    name="search_stocks",
    description="根据关键词（代码、简称或名称）搜索股票信息 (from Tushare)。"
)
def search_stocks(keyword: str) -> str:
    """
    Search for stock information by keyword (code, symbol, or name).
    
    :param keyword: The keyword to search for (e.g., "茅台", "600519", "000001.SZ").
    :return: A formatted string of stock information.
    """
    logging.info(f"调用工具: search_stocks，参数: {{'keyword': '{keyword}'}}")
    
    # 使用在启动时初始化的全局 tushare_pro_api 实例
    if not tushare_pro_api:
        logger.warning("Tushare API 未初始化 (Token缺失或无效)。")
        return "错误：Tushare token 未在服务器上配置或初始化失败。请检查服务器 .env 文件中的 TUSHARE_TOKEN。"

    try:
        logging.info(f"Searching for stock with keyword: {keyword}")
        
        if not keyword:
            return "错误：必须提供搜索关键词。"

        df_list = []

        # 1. 尝试按名称搜索 (API 级别模糊匹配)
        try:
            df_name = tushare_pro_api.stock_basic(name=keyword, list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
            if not df_name.empty:
                df_list.append(df_name)
        except Exception as e:
            logging.warning(f"Error searching by name '{keyword}': {e}")

        # 2. 尝试按 ts_code 搜索 (API 级别精确匹配)
        keyword_upper = keyword.upper()
        if ".SZ" in keyword_upper or ".SH" in keyword_upper or ".BJ" in keyword_upper:
            try:
                df_ts_code = tushare_pro_api.stock_basic(ts_code=keyword, list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
                if not df_ts_code.empty:
                    df_list.append(df_ts_code)
            except Exception as e:
                logging.warning(f"Error searching by ts_code '{keyword}': {e}")

        # 3. 备选方案: 获取所有并本地过滤 (用于 '600519' 或部分名称)
        if not df_list or (df_list and len(df_list[0]) < 5): # 修正了逻辑
            try:
                # 注意：在生产环境中，获取所有股票可能非常慢
                df_all = tushare_pro_api.stock_basic(
                    exchange='',
                    list_status='L',
                    fields='ts_code,symbol,name,area,industry,list_date'
                )
                
                df_filtered = df_all[
                    df_all['ts_code'].str.contains(keyword, case=False, na=False) |
                    df_all['name'].str.contains(keyword, case=False, na=False) |
                    df_all['symbol'].str.contains(keyword, case=False, na=False)
                ]
                if not df_filtered.empty:
                    df_list.append(df_filtered)
            except Exception as e:
                 logging.warning(f"Error during fallback search for '{keyword}': {e}")
        
        if not df_list:
            return f"No stock found with keyword: {keyword}"

        # 合并所有结果并去重
        df = pd.concat(df_list).drop_duplicates(subset=['ts_code']).reset_index(drop=True)
        
        if df.empty:
            return f"No stock found with keyword: {keyword}"

        # 以字符串形式返回结果
        return df.to_string(index=False)
    except Exception as e:
        logging.error(f"Error searching stocks for keyword '{keyword}': {e}", exc_info=True)
        return f"An error occurred while searching for stocks: {e}"


# --- 9. 挂载 MCP SSE 服务器 ---
MCP_BASE_PATH = "/sse"
try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path)

    async def handle_mcp_sse_handshake(request: Request) -> None:
        """Handle the MCP SSE handshake."""
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )

    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    app.mount(messages_full_path, sse_transport.handle_post_message)
    logger.info("MCP SSE integration configured successfully.")

except Exception as e:
    logger.critical(f"Failed to set up MCP SSE integration: {e}", exc_info=True)
    sys.exit(1)


# --- 10. 运行 FastAPI 应用 ---
# 你的 Dockerfile 使用 "uvicorn server:app"，所以这个 __main__ 块在 Docker 中不会被执行
if __name__ == "__main__":
    import uvicorn
    # Dockerfile 中指定了 $PORT 环境变量，本地运行时使用 8000
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting server locally on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
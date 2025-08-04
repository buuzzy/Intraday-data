import os
import datetime
import json
from typing import Optional, List, Dict, Any, AsyncGenerator
from fastapi import FastAPI, HTTPException, Query, Path, Request, Depends
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import pytz
from dotenv import load_dotenv
from supabase import create_client, Client
from sse_starlette.sse import EventSourceResponse

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

# MCP工具定义
class MCPTool(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]

class MCPToolResponse(BaseModel):
    type: str = "function"
    function: Dict[str, Any]

# 定义MCP工具
latest_bars_tool = {
    "name": "get_latest_bars",
    "description": "查询特定股票在给定结束时间前推X条分时数据",
    "parameters": {
        "type": "object",
        "properties": {
            "time_level": {
                "type": "string",
                "description": "时间级别，可选值为15min, 30min, 60min",
                "enum": ["15min", "30min", "60min"]
            },
            "stock_code": {
                "type": "string",
                "description": "股票代码，例如sz002353"
            },
            "end_time": {
                "type": "string",
                "description": "结束时间（可选），格式为YYYY-MM-DDTHH:MM:SS",
                "format": "date-time"
            },
            "limit": {
                "type": "integer",
                "description": "返回的记录数量（可选），默认为10",
                "default": 10
            }
        },
        "required": ["time_level", "stock_code"]
    }
}

bars_range_tool = {
    "name": "get_bars_range",
    "description": "查询特定股票在给定时间区间内的分时数据",
    "parameters": {
        "type": "object",
        "properties": {
            "time_level": {
                "type": "string",
                "description": "时间级别，可选值为15min, 30min, 60min",
                "enum": ["15min", "30min", "60min"]
            },
            "stock_code": {
                "type": "string",
                "description": "股票代码，例如sz002353"
            },
            "start_time": {
                "type": "string",
                "description": "开始时间，格式为YYYY-MM-DDTHH:MM:SS",
                "format": "date-time"
            },
            "end_time": {
                "type": "string",
                "description": "结束时间，格式为YYYY-MM-DDTHH:MM:SS",
                "format": "date-time"
            }
        },
        "required": ["time_level", "stock_code", "start_time", "end_time"]
    }
}

# 工具列表
mcp_tools = [latest_bars_tool, bars_range_tool]

# SSE端点
async def stock_data_event_generator(request: Request, tool_name: str, params: Dict[str, Any]) -> AsyncGenerator[str, None]:
    try:
        # 根据工具名称调用相应的函数
        if tool_name == "get_latest_bars":
            # 处理日期时间参数
            if "end_time" in params and params["end_time"]:
                params["end_time"] = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
            
            result = await get_latest_bars(
                time_level=params["time_level"],
                stock_code=params["stock_code"],
                end_time=params.get("end_time"),
                limit=params.get("limit", 10)
            )
        elif tool_name == "get_bars_range":
            # 处理日期时间参数
            start_time = datetime.datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
            end_time = datetime.datetime.fromisoformat(params["end_time"].replace("Z", "+00:00"))
            
            result = await get_bars_range(
                time_level=params["time_level"],
                stock_code=params["stock_code"],
                start_time=start_time,
                end_time=end_time
            )
        else:
            yield json.dumps({"error": f"未知的工具名称: {tool_name}"})
            return
        
        # 转换结果为可序列化的字典
        result_dict = {
            "data": [dict(item) for item in result["data"]],
            "count": result["count"],
            "time_level": result["time_level"],
            "stock_code": result["stock_code"]
        }
        
        # 发送结果
        yield json.dumps(result_dict)
    except Exception as e:
        yield json.dumps({"error": str(e)})

@app.get("/sse")
async def sse(request: Request):
    return JSONResponse({"tools": mcp_tools})

@app.post("/sse")
async def sse_post(request: Request):
    # 解析请求体
    data = await request.json()
    
    # 验证请求格式
    if "type" not in data or data["type"] != "function":
        return JSONResponse({"error": "无效的请求类型"}, status_code=400)
    
    if "function" not in data or "name" not in data["function"] or "parameters" not in data["function"]:
        return JSONResponse({"error": "无效的函数调用格式"}, status_code=400)
    
    tool_name = data["function"]["name"]
    params = data["function"]["parameters"]
    
    # 验证工具名称
    valid_tools = [tool["name"] for tool in mcp_tools]
    if tool_name not in valid_tools:
        return JSONResponse({"error": f"未知的工具名称: {tool_name}"}, status_code=400)
    
    # 返回SSE响应
    return EventSourceResponse(stock_data_event_generator(request, tool_name, params))

# 错误处理
@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {str(exc)}"},
    )

if __name__ == "__main__":
    import uvicorn
    import os
    
    # 从环境变量获取端口号，默认为8080（Cloud Run标准端口）
    port = int(os.environ.get("PORT", 8080))
    
    # 在生产环境中禁用reload
    reload = os.environ.get("ENV", "development") == "development"
    
    uvicorn.run("stock_data_api:app", host="0.0.0.0", port=port, reload=reload)
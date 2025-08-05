import sys # Added for stderr output
import functools # Added for checking partial functions
from pathlib import Path
from typing import Optional, Callable, Any
import tushare as ts
from dotenv import load_dotenv, set_key
import pandas as pd
from datetime import datetime, timedelta
import traceback
from mcp.server.fastmcp import FastMCP
import os

from fastapi import FastAPI, HTTPException, Body # Added HTTPException, Body
import uvicorn # New import

# fastapi.staticfiles is not used, so I won't import it here.
# If FastMCP is not directly mountable as an ASGI app, this approach will need adjustment
# based on FastMCP's specific API for integration.

# Added for the workaround:
from starlette.requests import Request
from mcp.server.sse import SseServerTransport

print("DEBUG: debug_server.py starting...", file=sys.stderr, flush=True)

# --- Start of ENV_FILE and Helper Functions ---
ENV_FILE = Path.home() / ".tushare_mcp" / ".env"
print(f"DEBUG: ENV_FILE path resolved to: {ENV_FILE}", file=sys.stderr, flush=True)

def _get_stock_name(pro_api_instance, ts_code: str) -> str:
    """Helper function to get stock name from ts_code."""
    print(f"DEBUG: _get_stock_name called for ts_code: {ts_code}", file=sys.stderr, flush=True)
    if not pro_api_instance:
        print("DEBUG: _get_stock_name received no pro_api_instance. Cannot fetch name.", file=sys.stderr, flush=True)
        return ts_code
    try:
        df_basic = pro_api_instance.stock_basic(ts_code=ts_code, fields='ts_code,name')
        if not df_basic.empty:
            return df_basic.iloc[0]['name']
    except Exception as e:
        print(f"Warning: Failed to get stock name for {ts_code}: {e}", file=sys.stderr, flush=True)
    return ts_code

def _fetch_latest_report_data(
    api_func: Callable[..., pd.DataFrame],
    result_period_field_name: str, 
    result_period_value: str, 
    is_list_result: bool = False, # New parameter to indicate if multiple rows are expected for the latest announcement
    **api_params: Any
) -> Optional[pd.DataFrame]:
    """
    Internal helper to fetch report data.
    If is_list_result is True, it returns all rows matching the latest announcement date.
    Otherwise, it returns only the single latest announced record.
    """
    func_name = "Unknown API function"
    if isinstance(api_func, functools.partial):
        func_name = api_func.func.__name__
    elif hasattr(api_func, '__name__'):
        func_name = api_func.__name__

    print(f"DEBUG: _fetch_latest_report_data called for {func_name}, period: {result_period_value}, is_list: {is_list_result}", file=sys.stderr, flush=True)
    try:
        df = api_func(**api_params)
        if df.empty:
            print(f"DEBUG: _fetch_latest_report_data: API call {func_name} returned empty DataFrame for {api_params.get('ts_code')}", file=sys.stderr, flush=True)
            return None

        # Ensure 'ann_date' and the specified period field exist for sorting/filtering
        if 'ann_date' not in df.columns:
            print(f"Warning: _fetch_latest_report_data: 'ann_date' not in DataFrame columns for {func_name} on {api_params.get('ts_code')}. Returning raw df (or first row if not list).", file=sys.stderr, flush=True)
            return df if is_list_result else df.head(1)
        
        if result_period_field_name not in df.columns:
            print(f"Warning: _fetch_latest_report_data: Period field '{result_period_field_name}' not in DataFrame columns for {func_name} on {api_params.get('ts_code')}. Filtering by ann_date only.", file=sys.stderr, flush=True)
            # Sort by ann_date to get the latest announcement(s)
            df_sorted_by_ann = df.sort_values(by='ann_date', ascending=False)
            if df_sorted_by_ann.empty:
                return None
            latest_ann_date = df_sorted_by_ann['ann_date'].iloc[0]
            df_latest_ann = df_sorted_by_ann[df_sorted_by_ann['ann_date'] == latest_ann_date]
            return df_latest_ann # Return all rows for the latest announcement date

        # Filter by the specific report period first
        # Convert both to string for robust comparison, in case of type mismatches
        df_filtered_period = df[df[result_period_field_name].astype(str) == str(result_period_value)]

        if df_filtered_period.empty:
            print(f"DEBUG: _fetch_latest_report_data: No data found for period {result_period_value} after filtering by '{result_period_field_name}' for {func_name} on {api_params.get('ts_code')}. Original df had {len(df)} rows.", file=sys.stderr, flush=True)
            # Fallback: if strict period filtering yields nothing, but original df had data, 
            # it might be that ann_date is more reliable or the period was slightly off.
            # For now, let's return None if period match fails, to be strict.
            # Consider alternative fallback if needed, e.g. using latest ann_date from original df.
            return None

        # Sort by ann_date to get the latest announcement(s) for that specific period
        df_sorted_by_ann = df_filtered_period.sort_values(by='ann_date', ascending=False)
        if df_sorted_by_ann.empty: # Should not happen if df_filtered_period was not empty
            return None
        
        latest_ann_date = df_sorted_by_ann['ann_date'].iloc[0]
        df_latest_ann = df_sorted_by_ann[df_sorted_by_ann['ann_date'] == latest_ann_date]
        
        if is_list_result:
            print(f"DEBUG: _fetch_latest_report_data: Returning {len(df_latest_ann)} rows for latest announcement on {latest_ann_date} (list_result=True)", file=sys.stderr, flush=True)
            return df_latest_ann # Return all rows for the latest announcement date for this period
        else:
            # Return only the top-most row (which is the latest announcement for that period)
            print(f"DEBUG: _fetch_latest_report_data: Returning 1 row for latest announcement on {latest_ann_date} (list_result=False)", file=sys.stderr, flush=True)
            return df_latest_ann.head(1)

    except Exception as e:
        print(f"Error in _fetch_latest_report_data calling {func_name} for {api_params.get('ts_code', 'N/A')}, period {result_period_value}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return None
# --- End of MODIFIED _fetch_latest_report_data ---

# --- MCP Instance Creation ---
try:
    mcp = FastMCP("Tushare Tools Enhanced")
    print("DEBUG: FastMCP instance created for Tushare Tools Enhanced.", file=sys.stderr, flush=True)
except Exception as e:
    print(f"DEBUG: ERROR creating FastMCP: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    raise
# --- End of MCP Instance Creation ---

# --- FastAPI App Creation and Basic Endpoint ---
app = FastAPI(
    title="Tushare MCP API",
    description="Remote API for Tushare MCP tools via FastAPI.",
    version="0.0.1"
)

@app.get("/")
async def read_root():
    return {"message": "Hello World - Tushare MCP API is running!"}

# New API endpoint for setting up Tushare token
@app.post("/tools/setup_tushare_token", summary="Setup Tushare API token")
async def api_setup_tushare_token(payload: dict = Body(...)):
    """
    Sets the Tushare API token.
    Expects a JSON payload with a "token" key.
    Example: {"token": "your_actual_token_here"}
    """
    print(f"DEBUG: API /tools/setup_tushare_token called with payload: {{payload}}", file=sys.stderr, flush=True)
    token = payload.get("token")
    if not token or not isinstance(token, str):
        print(f"DEBUG: API /tools/setup_tushare_token - Missing or invalid token in payload.", file=sys.stderr, flush=True)
        raise HTTPException(status_code=400, detail="Missing or invalid 'token' in payload. Expected a JSON object with a 'token' string.")

    try:
        # Call your original tool function
        original_tool_function_output = setup_tushare_token(token=token) # This is your original @mcp.tool() function
        print(f"DEBUG: API /tools/setup_tushare_token - Original tool output: {{original_tool_function_output}}", file=sys.stderr, flush=True)
        return {"status": "success", "message": original_tool_function_output}
    except Exception as e:
        error_message = f"Error setting up token via API: {str(e)}"
        print(f"DEBUG: ERROR in api_setup_tushare_token: {{error_message}}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr) # Keep detailed server-side logs
        raise HTTPException(status_code=500, detail=error_message)

# --- End of FastAPI App Creation ---

# --- Start of Core Token Management Functions (to be kept) ---
def init_env_file():
    """初始化环境变量文件"""
    print("DEBUG: init_env_file called.", file=sys.stderr, flush=True)
    try:
        print(f"DEBUG: Attempting to create directory: {ENV_FILE.parent}", file=sys.stderr, flush=True)
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        print(f"DEBUG: Directory {ENV_FILE.parent} ensured.", file=sys.stderr, flush=True)
        if not ENV_FILE.exists():
            print(f"DEBUG: ENV_FILE {ENV_FILE} does not exist, attempting to touch.", file=sys.stderr, flush=True)
            ENV_FILE.touch()
            print(f"DEBUG: ENV_FILE {ENV_FILE} touched.", file=sys.stderr, flush=True)
        else:
            print(f"DEBUG: ENV_FILE {ENV_FILE} already exists.", file=sys.stderr, flush=True)
        load_dotenv(ENV_FILE)
        print("DEBUG: load_dotenv(ENV_FILE) called.", file=sys.stderr, flush=True)
    except Exception as e_fs:
        print(f"DEBUG: ERROR in init_env_file filesystem operations: {str(e_fs)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)

def get_tushare_token() -> Optional[str]:
    """获取Tushare token"""
    print("DEBUG: get_tushare_token called.", file=sys.stderr, flush=True)
    init_env_file()
    token = os.getenv("TUSHARE_TOKEN")
    print(f"DEBUG: get_tushare_token: os.getenv result: {'TOKEN_FOUND' if token else 'NOT_FOUND'}", file=sys.stderr, flush=True)
    return token

def set_tushare_token(token: str):
    """设置Tushare token"""
    print(f"DEBUG: set_tushare_token called with token: {'********' if token else 'None'}", file=sys.stderr, flush=True)
    init_env_file()
    try:
        set_key(ENV_FILE, "TUSHARE_TOKEN", token)
        print(f"DEBUG: set_key executed for ENV_FILE: {ENV_FILE}", file=sys.stderr, flush=True)
        ts.set_token(token)
        print("DEBUG: ts.set_token(token) executed.", file=sys.stderr, flush=True)
    except Exception as e_set_token:
        print(f"DEBUG: ERROR in set_tushare_token during set_key or ts.set_token: {str(e_set_token)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)

# --- End of Core Token Management Functions ---

# 添加setup_tushare_token函数
@mcp.tool()
def setup_tushare_token(token: str) -> str:
    """
    配置Tushare API Token

    参数:
        token: Tushare API Token
    """
    print(f"DEBUG: Tool setup_tushare_token called with token: {'********' if token else 'None'}", file=sys.stderr, flush=True)
    if not token:
        return "错误：请提供有效的Tushare API Token"
    
    try:
        set_tushare_token(token)
        # 验证token是否有效
        pro = ts.pro_api(token)
        # 尝试一个简单的API调用来验证token
        df = pro.stock_basic(limit=1)
        if df is not None and not df.empty:
            return "Tushare API Token配置成功！"
        else:
            return "警告：Token已设置，但可能无效。请检查Token是否正确。"
    except Exception as e:
        print(f"DEBUG: ERROR in setup_tushare_token: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"设置Token失败：{str(e)}"

@mcp.tool()
def check_token_status() -> str:
    """
    检查Tushare API Token的状态
    
    返回:
        str: Token状态信息
    """
    print(f"DEBUG: Tool check_token_status called", file=sys.stderr, flush=True)
    try:
        token = get_tushare_token()
        if not token:
            return "未配置Tushare API Token。请使用setup_tushare_token工具配置Token。"
        
        # 验证token是否有效
        pro = ts.pro_api(token)
        # 尝试一个简单的API调用来验证token
        df = pro.stock_basic(limit=1)
        if df is not None and not df.empty:
            return f"Tushare API Token状态正常，可以使用。Token: {'*' * 4 + token[-4:] if len(token) > 4 else '****'}"
        else:
            return "警告：Token已配置，但可能无效。请检查Token是否正确。"
    except Exception as e:
        print(f"DEBUG: ERROR in check_token_status: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"检查Token状态失败：{str(e)}"

# Tools and Prompts will be added here one by one from refer/server.py

# 删除了其他mcp工具，仅保留get_start_date_for_n_days、search_stocks和get_daily_prices三个工具

    try:
        pro = ts.pro_api(token_value)
        query_params = {
            'name': index_name,
            'fields': 'ts_code,name,fullname,market,publisher,category,list_date'
        }
        if market:
            query_params['market'] = market
        if publisher:
            query_params['publisher'] = publisher
        if category:
            query_params['category'] = category
        
        # The 'name' parameter in index_basic acts as a keyword search against 'name' and 'fullname'
        # No need for complex df filtering if API handles keyword search well.
        df = pro.index_basic(**query_params)

        if df.empty:
            return f"未找到与 '{index_name}'相关的指数。尝试更通用或精确的关键词，或检查市场/发布商/类别参数。"

        results = [f"--- 指数搜索结果 for '{index_name}' ---"]
        # Limit results to avoid overly long output, e.g., top 20 matches
        # Sort by list_date (desc) and then ts_code to have some order if many results
        df_sorted = df.sort_values(by=['list_date', 'ts_code'], ascending=[False, True]).head(20)

        for _, row in df_sorted.iterrows():
            info_parts = [
                f"TS代码: {row.get('ts_code', 'N/A')}",
                f"简称: {row.get('name', 'N/A')}",
                f"全称: {row.get('fullname', 'N/A')}",
                f"市场: {row.get('market', 'N/A')}",
                f"发布方: {row.get('publisher', 'N/A')}",
                f"类别: {row.get('category', 'N/A')}",
                f"发布日期: {row.get('list_date', 'N/A')}"
            ]
            results.append("\n".join(info_parts))
            results.append("------------------------")
        
        if len(df) > 20:
            results.append(f"注意: 结果超过20条，仅显示前20条。请尝试使用 market, publisher 或 category 参数缩小范围。")

        return "\n".join(results)

    except Exception as e:
        print(f"DEBUG: ERROR in search_index for '{index_name}': {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"搜索指数 '{index_name}' 失败: {str(e)}"


@mcp.tool()
def search_stocks(keyword: str) -> str:
    """
    搜索股票

    参数:
        keyword: 关键词（可以是股票代码的一部分或股票名称的一部分）
    """
    print(f"DEBUG: Tool search_stocks called with keyword: '{keyword}'.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"
    try:
        pro = ts.pro_api(token_value)
        df = pro.stock_basic()
        mask = (df['ts_code'].str.contains(keyword, case=False, na=False)) | \
               (df['name'].str.contains(keyword, case=False, na=False))
        results_df = df[mask]
        if results_df.empty:
            return "未找到符合条件的股票"
        output = []
        for _, row in results_df.iterrows():
            output.append(f"{row['ts_code']} - {row['name']}")
        return "\\n".join(output)
    except Exception as e:
        print(f"DEBUG: ERROR in search_stocks: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"搜索失败：{str(e)}"

@mcp.tool()
def get_daily_prices(ts_code: str, trade_date: str = None, start_date: str = None, end_date: str = None) -> str:
    """
    获取指定股票在特定交易日或一段时期内的开盘价、最高价、最低价和收盘价。

    参数:
        ts_code: 股票代码 (例如: 600126.SH)
        trade_date: 交易日期 (YYYYMMDD格式, 例如: 20250227)。与 start_date/end_date 互斥。
        start_date: 开始日期 (YYYYMMDD格式)。需与 end_date 一同使用。
        end_date: 结束日期 (YYYYMMDD格式)。需与 start_date 一同使用。
    """
    print(f"DEBUG: Tool get_daily_prices called with ts_code: '{ts_code}', trade_date: '{trade_date}', start_date: '{start_date}', end_date: '{end_date}'.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"

    if not ((trade_date and not (start_date or end_date)) or ((start_date and end_date) and not trade_date)):
        return "错误：请提供 trade_date (用于单日查询) 或 start_date 和 end_date (用于区间查询)。"

    try:
        pro = ts.pro_api(token_value)
        api_params = {'ts_code': ts_code, 'fields': 'ts_code,trade_date,open,high,low,close,vol,amount'}
        if trade_date:
            api_params['trade_date'] = trade_date
        if start_date and end_date:
            api_params['start_date'] = start_date
            api_params['end_date'] = end_date

        df = pro.daily(**api_params)

        if df.empty:
            if trade_date:
                return f"未找到 {ts_code} 在 {trade_date} 的日线行情数据。"
            else:
                return f"未找到 {ts_code} 在 {start_date} 到 {end_date} 期间的日线行情数据。"

        df_sorted = df.sort_values(by='trade_date', ascending=False)
        
        results = []
        stock_name = _get_stock_name(pro, ts_code)
        if trade_date:
            results.append(f"--- {stock_name} ({ts_code}) {trade_date} 价格信息 ---")
        else:
            results.append(f"--- {stock_name} ({ts_code}) {start_date} to {end_date} 价格信息 ---")

        for index, row in df_sorted.iterrows():
            date_str = row['trade_date']
            results.append(f"\n日期: {date_str}")
            price_fields = {
                'open': '开盘价', 'high': '最高价', 'low': '最低价',
                'close': '收盘价', 'vol': '成交量', 'amount': '成交额'
            }
            for field, label in price_fields.items():
                if field in row and pd.notna(row[field]):
                    try:
                        numeric_value = pd.to_numeric(row[field])
                        if field == 'vol':
                            unit = '手'
                            results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                        elif field == 'amount':
                            unit = '千元'
                            results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                        else:
                            unit = '元'
                            results.append(f"  {label}: {numeric_value:.2f} {unit}")
                    except (ValueError, TypeError):
                        results.append(f"  {label}: (值非数字: {row[field]})")
                else:
                    results.append(f"  {label}: 未提供")
        return "\n".join(results)
    except Exception as e:
        print(f"DEBUG: ERROR in get_daily_prices: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"获取每日价格数据失败：{str(e)}"

# --- Start of MCP SSE Workaround Integration ---
# Remove previous mounting attempt:
# # Mount the FastMCP SSE application.
# # The sse_app() method returns a Starlette application instance.
# mcp_sse_app = mcp.sse_app()
# app.mount("/sse", mcp_sse_app)
# print("DEBUG: FastMCP SSE app instance mounted at /sse", file=sys.stderr, flush=True)

MCP_BASE_PATH = "/sse" # The path where the MCP service will be available (e.g., https://.../sse)

print(f"DEBUG: Applying MCP SSE workaround for base path: {MCP_BASE_PATH}", file=sys.stderr, flush=True)

try:
    # 1. Initialize SseServerTransport.
    # The `messages_endpoint_path` is the path that the client will be told to POST messages to.
    # This path should be the full path, including our base path.
    # The SseServerTransport will handle POSTs to this path.
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path) # Directly pass the full path string
    print(f"DEBUG: SseServerTransport initialized; client will be told messages are at: {messages_full_path}", file=sys.stderr, flush=True)

    async def handle_mcp_sse_handshake(request: Request) -> None:
        """Handles the initial SSE handshake from the client."""
        print(f"DEBUG: MCP SSE handshake request received for: {request.url}", file=sys.stderr, flush=True)
        # request._send is a protected member, type: ignore is used.
        async with sse_transport.connect_sse(
            request.scope,
            request.receive,
            request._send, # type: ignore 
        ) as (read_stream, write_stream):
            print(f"DEBUG: MCP SSE connection established for {MCP_BASE_PATH}. Starting McpServer.run.", file=sys.stderr, flush=True)
            # mcp is our FastMCP instance. _mcp_server is its underlying McpServer.
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
            print(f"DEBUG: McpServer.run finished for {MCP_BASE_PATH}.", file=sys.stderr, flush=True)

    # 2. Add the route for the SSE handshake.
    # Clients will make a GET request to this endpoint to initiate the SSE connection.
    # e.g., GET https://mcp-api.chatbotbzy.top/sse
    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    print(f"DEBUG: MCP SSE handshake GET route added at: {MCP_BASE_PATH}", file=sys.stderr, flush=True)

    # 3. Mount the ASGI app from sse_transport to handle POSTed messages.
    # This will handle POST requests to https://mcp-api.chatbotbzy.top/sse/messages/
    app.mount(messages_full_path, sse_transport.handle_post_message)
    print(f"DEBUG: MCP SSE messages POST endpoint mounted at: {messages_full_path}", file=sys.stderr, flush=True)

    print(f"DEBUG: MCP SSE workaround for base path {MCP_BASE_PATH} applied successfully.", file=sys.stderr, flush=True)

except Exception as e_workaround:
    print(f"DEBUG: CRITICAL ERROR applying MCP SSE workaround: {str(e_workaround)}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
# --- End of MCP SSE Workaround Integration ---

@mcp.tool()
def get_trade_calendar(exchange: str = '', start_date: str = None, end_date: str = None) -> str:
    """
    获取各大交易所交易日历数据。

    参数:
        exchange: str, 交易所 SSE上交所,SZSE深交所,CFFEX 中金所,SHFE 上期所,CZCE 郑商所,DCE 大商所,INE 上能源 (默认为上交所)
        start_date: str, 开始日期 (格式：YYYYMMDD)
        end_date: str, 结束日期 (格式：YYYYMMDD)
    """
    print(f"DEBUG: Tool get_trade_calendar called with exchange='{exchange}', start_date='{start_date}', end_date='{end_date}'.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"

    try:
        pro = ts.pro_api(token_value)
        query_params = {
            'exchange': exchange,
            'start_date': start_date,
            'end_date': end_date
        }
        # 移除值为None的参数，以使用Tushare API的默认值
        query_params = {k: v for k, v in query_params.items() if v is not None}

        df = pro.trade_cal(**query_params)

        if df.empty:
            return "未找到符合条件的交易日历数据。"

        # 筛选出开盘日
        trading_days = df[df['is_open'] == 1]
        if trading_days.empty:
            return "在指定日期范围内没有找到交易日。"

        # 格式化输出
        results = [f"--- 交易日历查询结果 (交易所: {exchange if exchange else '默认'}) ---"]
        # 限制输出长度，例如最多显示最近的100个交易日
        df_limited = trading_days.head(100)

        day_list = df_limited['cal_date'].tolist()
        results.append("交易日列表:")
        # 每10个日期换一行
        for i in range(0, len(day_list), 10):
            results.append(" ".join(day_list[i:i+10]))

        if len(trading_days) > 100:
            results.append(f"\n注意: 结果超过100条，仅显示前100条。总共有 {len(trading_days)} 个交易日。")

        return "\n".join(results)

    except Exception as e:
        print(f"DEBUG: ERROR in get_trade_calendar: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"获取交易日历失败: {str(e)}"

@mcp.tool()
def get_weekly_prices(ts_code: str = None, trade_date: str = None, start_date: str = None, end_date: str = None) -> str:
    """
    获取A股周线行情数据

    参数:
        ts_code: TS代码（ts_code和trade_date两个参数任选一）
        trade_date: 交易日期（每周最后一个交易日期，YYYYMMDD格式）
        start_date: 开始日期
        end_date: 结束日期
    """
    print(f"DEBUG: Tool get_weekly_prices called with ts_code='{ts_code}', trade_date='{trade_date}', start_date='{start_date}', end_date='{end_date}'.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"
    
    try:
        pro = ts.pro_api(token_value)
        query_params = {}
        
        # 设置查询参数
        if ts_code:
            query_params['ts_code'] = ts_code
        if trade_date:
            query_params['trade_date'] = trade_date
        if start_date:
            query_params['start_date'] = start_date
        if end_date:
            query_params['end_date'] = end_date
            
        # 检查必要参数
        if not ts_code and not trade_date:
            return "错误：必须提供ts_code或trade_date参数之一"
            
        # 调用weekly接口获取周线数据
        df = pro.weekly(**query_params)
        
        if df.empty:
            return "未找到符合条件的周线数据"
            
        # 限制结果数量，避免输出过长
        if len(df) > 20:
            df = df.head(20)
            show_limit_warning = True
        else:
            show_limit_warning = False
            
        # 格式化输出结果
        results = []
        
        # 如果是查询单个股票的多个交易周期
        if ts_code and not trade_date:
            stock_name = _get_stock_name(ts_code, pro)
            results.append(f"--- {ts_code} {stock_name} 周线行情 ---")
            
            for _, row in df.iterrows():
                results.append(f"交易日期: {row.get('trade_date', 'N/A')}")
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上周收盘价'),
                    ('change', '周涨跌额'),
                    ('pct_chg', '周涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
                results.append("------------------------")
        
        # 如果是查询特定交易日期的多个股票
        elif trade_date and not ts_code:
            results.append(f"--- {trade_date} 交易日周线行情 ---")
            
            for _, row in df.iterrows():
                current_ts_code = row.get('ts_code', 'N/A')
                stock_name = _get_stock_name(current_ts_code, pro)
                results.append(f"{current_ts_code} {stock_name}:")
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上周收盘价'),
                    ('change', '周涨跌额'),
                    ('pct_chg', '周涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
                results.append("------------------------")
        
        # 如果是查询特定股票的特定交易日期
        else:
            stock_name = _get_stock_name(ts_code, pro)
            results.append(f"--- {ts_code} {stock_name} {trade_date} 周线行情 ---")
            
            if not df.empty:
                row = df.iloc[0]
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上周收盘价'),
                    ('change', '周涨跌额'),
                    ('pct_chg', '周涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
        
        if show_limit_warning:
            results.append(f"注意: 结果超过20条，仅显示前20条。请缩小日期范围以获取更精确的结果。")
            
        return "\n".join(results)
    except Exception as e:
        print(f"DEBUG: ERROR in get_weekly_prices: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"获取周线行情数据失败：{str(e)}"

@mcp.tool()
def get_monthly_prices(ts_code: str = None, trade_date: str = None, start_date: str = None, end_date: str = None) -> str:
    """
    获取A股月线行情数据

    参数:
        ts_code: TS代码（ts_code和trade_date两个参数任选一）
        trade_date: 交易日期（每月最后一个交易日日期，YYYYMMDD格式）
        start_date: 开始日期
        end_date: 结束日期
    """
    print(f"DEBUG: Tool get_monthly_prices called with ts_code='{ts_code}', trade_date='{trade_date}', start_date='{start_date}', end_date='{end_date}'.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"
    
    try:
        pro = ts.pro_api(token_value)
        query_params = {}
        
        # 设置查询参数
        if ts_code:
            query_params['ts_code'] = ts_code
        if trade_date:
            query_params['trade_date'] = trade_date
        if start_date:
            query_params['start_date'] = start_date
        if end_date:
            query_params['end_date'] = end_date
            
        # 检查必要参数
        if not ts_code and not trade_date:
            return "错误：必须提供ts_code或trade_date参数之一"
            
        # 调用monthly接口获取月线数据
        df = pro.monthly(**query_params)
        
        if df.empty:
            return "未找到符合条件的月线数据"
            
        # 限制结果数量，避免输出过长
        if len(df) > 20:
            df = df.head(20)
            show_limit_warning = True
        else:
            show_limit_warning = False
            
        # 格式化输出结果
        results = []
        
        # 如果是查询单个股票的多个交易月份
        if ts_code and not trade_date:
            stock_name = _get_stock_name(ts_code, pro)
            results.append(f"--- {ts_code} {stock_name} 月线行情 ---")
            
            for _, row in df.iterrows():
                results.append(f"交易日期: {row.get('trade_date', 'N/A')}")
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上月收盘价'),
                    ('change', '月涨跌额'),
                    ('pct_chg', '月涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
                results.append("------------------------")
        
        # 如果是查询特定交易日期的多个股票
        elif trade_date and not ts_code:
            results.append(f"--- {trade_date} 交易日月线行情 ---")
            
            for _, row in df.iterrows():
                current_ts_code = row.get('ts_code', 'N/A')
                stock_name = _get_stock_name(current_ts_code, pro)
                results.append(f"{current_ts_code} {stock_name}:")
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上月收盘价'),
                    ('change', '月涨跌额'),
                    ('pct_chg', '月涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
                results.append("------------------------")
        
        # 如果是查询特定股票的特定交易日期
        else:
            stock_name = _get_stock_name(ts_code, pro)
            results.append(f"--- {ts_code} {stock_name} {trade_date} 月线行情 ---")
            
            if not df.empty:
                row = df.iloc[0]
                
                # 定义要显示的字段及其标签
                fields_to_display = [
                    ('open', '开盘价'),
                    ('high', '最高价'),
                    ('low', '最低价'),
                    ('close', '收盘价'),
                    ('pre_close', '上月收盘价'),
                    ('change', '月涨跌额'),
                    ('pct_chg', '月涨跌幅(%)'),
                    ('vol', '成交量'),
                    ('amount', '成交额')
                ]
                
                for field, label in fields_to_display:
                    if field in row and pd.notna(row[field]):
                        try:
                            numeric_value = pd.to_numeric(row[field])
                            if field == 'vol':
                                unit = '手'
                                results.append(f"  {label}: {numeric_value:,.0f} {unit}")
                            elif field == 'amount':
                                unit = '千元'
                                results.append(f"  {label}: {numeric_value:,.2f} {unit}")
                            elif field == 'pct_chg':
                                results.append(f"  {label}: {numeric_value:.2f}%")
                            else:
                                unit = '元'
                                results.append(f"  {label}: {numeric_value:.2f} {unit}")
                        except (ValueError, TypeError):
                            results.append(f"  {label}: (值非数字: {row[field]})")
                    else:
                        results.append(f"  {label}: 未提供")
        
        if show_limit_warning:
            results.append(f"注意: 结果超过20条，仅显示前20条。请缩小日期范围以获取更精确的结果。")
            
        return "\n".join(results)
    except Exception as e:
        print(f"DEBUG: ERROR in get_monthly_prices: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"获取月线行情数据失败：{str(e)}"

@mcp.tool()
def get_start_date_for_n_days(end_date: str, days_ago: int = 80) -> str:
    """
    根据结束日期和天数，获取Tushare交易日历上的起始日期。

    参数:
        end_date: str, 结束日期 (格式：YYYYMMDD)
        days_ago: int, 需要回溯的交易日天数 (默认为80)
    """
    print(f"DEBUG: Tool get_start_date_for_n_days called with end_date='{end_date}', days_ago={days_ago}.", file=sys.stderr, flush=True)
    token_value = get_tushare_token()
    if not token_value:
        return "错误：Tushare token 未配置或无法获取。请使用 setup_tushare_token 配置。"

    try:
        pro = ts.pro_api(token_value)
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        # 为了获取足够的日历数据，我们估算一个较早的开始日期
        # 通常交易日与日历日的比例约为 252/365 ≈ 0.7。为保险起见，我们用更大的乘数。
        estimated_days = int(days_ago / 0.5) # 넉넉하게 160일
        start_dt_estimated = end_dt - timedelta(days=estimated_days)
        start_date_estimated_str = start_dt_estimated.strftime('%Y%m%d')

        df = pro.trade_cal(start_date=start_date_estimated_str, end_date=end_date, is_open='1')

        if df.empty or len(df) < days_ago:
            return f"错误：无法获取足够的交易日数据。在 {start_date_estimated_str} 和 {end_date} 之间只找到了 {len(df)} 个交易日，需要 {days_ago} 个。"

        # 日期已经是升序排列的，我们取倒数第N个即可
        trading_days = df['cal_date'].sort_values(ascending=False).tolist()
        
        if len(trading_days) < days_ago:
             return f"错误：再次确认，交易日数据不足。在 {start_date_estimated_str} 和 {end_date} 之间只找到了 {len(trading_days)} 个交易日，需要 {days_ago} 个。"

        # 获取第N个交易日
        start_date_actual = trading_days[days_ago - 1]

        return f"查询成功。对于结束日期 {end_date}，往前 {days_ago} 个交易日的开始日期是: {start_date_actual}"

    except Exception as e:
        print(f"DEBUG: ERROR in get_start_date_for_n_days: {str(e)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return f"计算起始日期失败: {str(e)}"

if __name__ == "__main__":
    print("DEBUG: debug_server.py entering main section for FastAPI...", file=sys.stderr, flush=True)
    try:
        # mcp.run() # Commented out original MCP run
        print("DEBUG: Attempting to start uvicorn server...", file=sys.stderr, flush=True)
        # 使用环境变量PORT，以便与Cloud Run兼容
        port = int(os.environ.get("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
        print("DEBUG: uvicorn.run() completed (should not happen if server runs indefinitely).", file=sys.stderr, flush=True)
    except Exception as e_run:
        print(f"DEBUG: ERROR during uvicorn.run(): {e_run}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        raise
    except BaseException as be_run: # Catching BaseException like KeyboardInterrupt
        print(f"DEBUG: BASE EXCEPTION during uvicorn.run() (e.g., KeyboardInterrupt): {be_run}", file=sys.stderr, flush=True)
        # traceback.print_exc(file=sys.stderr) # Optional: might be too verbose for Ctrl+C
        # raise # Re-raise if you want the process to exit with an error code from the BaseException
    finally:
        print("DEBUG: debug_server.py finished.", file=sys.stderr, flush=True)
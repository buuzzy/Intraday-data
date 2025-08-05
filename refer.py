import sys
from typing import Optional, Any, Callable
import tushare as ts
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv, set_key
from mcp.server.fastmcp import FastMCP
import os
import traceback
import uvicorn
from fastapi import FastAPI, Query
from starlette.requests import Request
from mcp.server.sse import SseServerTransport
import logging

# --- Setup basic logging ---
# Configure logger. MCP/FastAPI might have its own logging, so this is a basic setup.
# You might want to align this with how your other MCP services handle logging.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # Added: logger instance
# --- End of logging setup ---

# --- Start of ENV_FILE and Helper Functions (Copied from server.py or made shared) ---
# It's better to have a shared utility module for these if you have multiple server files.
# For now, we'll include them directly for simplicity.

ENV_FILE_DIR_NAME = ".tushare_mcp"
ENV_FILE_NAME = ".env"

# Try to get home directory, fallback to current dir for restricted environments
try:
    APP_DATA_DIR = Path.home() / ENV_FILE_DIR_NAME
except RuntimeError: # pragma: no cover
    print(f"Warning: Could not determine home directory. Using current directory for .env file: {Path.cwd() / ENV_FILE_DIR_NAME}", file=sys.stderr, flush=True)
    APP_DATA_DIR = Path.cwd() / ENV_FILE_DIR_NAME

ENV_FILE = APP_DATA_DIR / ENV_FILE_NAME
print(f"DEBUG: hotlist.py: ENV_FILE path resolved to: {ENV_FILE}", file=sys.stderr, flush=True)

def init_env_file():
    """初始化环境变量文件"""
    print("DEBUG: hotlist.py: init_env_file called.", file=sys.stderr, flush=True)
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not ENV_FILE.exists():
            ENV_FILE.touch()
        load_dotenv(ENV_FILE, override=True) # override=True ensures env vars from .env take precedence
        print(f"DEBUG: hotlist.py: load_dotenv(ENV_FILE) called. ENV_FILE exists: {ENV_FILE.exists()}", file=sys.stderr, flush=True)
    except Exception as e_fs:
        print(f"DEBUG: hotlist.py: ERROR in init_env_file filesystem operations: {str(e_fs)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)

def get_tushare_token() -> Optional[str]:
    """获取Tushare token"""
    print("DEBUG: hotlist.py: get_tushare_token called.", file=sys.stderr, flush=True)
    init_env_file() # Ensure .env is loaded
    token = os.getenv("TUSHARE_TOKEN")
    print(f"DEBUG: hotlist.py: get_tushare_token: os.getenv result: {'TOKEN_FOUND' if token else 'NOT_FOUND'}", file=sys.stderr, flush=True)
    return token

def set_tushare_token_in_env(token: str):
    """设置Tushare token到.env文件并加载到当前环境"""
    print(f"DEBUG: hotlist.py: set_tushare_token_in_env called with token: {'********' if token else 'None'}", file=sys.stderr, flush=True)
    init_env_file() # Ensure directory exists
    try:
        set_key(ENV_FILE, "TUSHARE_TOKEN", token, quote_mode='never')
        os.environ["TUSHARE_TOKEN"] = token # Also set in current process's env
        print(f"DEBUG: hotlist.py: Token set in {ENV_FILE} and os.environ.", file=sys.stderr, flush=True)
    except Exception as e_set_token:
        print(f"DEBUG: hotlist.py: ERROR in set_tushare_token_in_env: {str(e_set_token)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)

# --- End of Core Token Management Functions ---

# --- Initialize Tushare Pro API Instance (Module Level) ---
PRO_API_INSTANCE = None
PRINTED_TOKEN_ERROR = False # To avoid printing the token error repeatedly for every tool call

INIT_TOKEN = get_tushare_token()
if not INIT_TOKEN:
    print("ERROR: hotlist.py: Tushare token not found. PRO_API_INSTANCE will not be initialized. Please configure TUSHARE_TOKEN in .tushare_mcp/.env", file=sys.stderr, flush=True)
    PRINTED_TOKEN_ERROR = True # Mark that the error has been printed
else:
    try:
        PRO_API_INSTANCE = ts.pro_api(INIT_TOKEN)
        if PRO_API_INSTANCE is None:
            print("ERROR: hotlist.py: ts.pro_api(INIT_TOKEN) returned None. Token might be invalid or Tushare service issue.", file=sys.stderr, flush=True)
            PRINTED_TOKEN_ERROR = True # Mark that the error has been printed
        else:
            print("INFO: hotlist.py: Tushare Pro API (PRO_API_INSTANCE) initialized successfully at module level.", file=sys.stderr, flush=True)
    except Exception as e_pro_api_init:
        print(f"ERROR: hotlist.py: Failed to initialize Tushare Pro API (PRO_API_INSTANCE) at module level: {str(e_pro_api_init)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        PRO_API_INSTANCE = None # Ensure it's None on failure
        PRINTED_TOKEN_ERROR = True # Mark that an error occurred during init
# --- End of Tushare Pro API Instance Initialization ---

# --- MCP Instance Creation ---
try:
    mcp = FastMCP("Tushare Hotlist Tools")
    print("DEBUG: hotlist.py: FastMCP instance created for Tushare Hotlist Tools.", file=sys.stderr, flush=True)
except Exception as e:
    print(f"DEBUG: hotlist.py: ERROR creating FastMCP: {e}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    raise
# --- End of MCP Instance Creation ---

# --- FastAPI App Creation (similar to server.py) ---
app = FastAPI(
    title="Tushare Hotlist MCP API",
    description="Remote API for Tushare Hotlist MCP tools via FastAPI.",
    version="0.0.1" # Or a suitable version
)

@app.get("/")
async def read_root():
    return {"message": "Hello World - Tushare Hotlist MCP API is running!"}
# --- End of FastAPI App Creation ---

# --- Tushare KPL Concept Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_kpl_concept_list",
    description="获取开盘啦概念题材列表。可以根据交易日期、题材代码或题材名称进行筛选。"
)
def get_kpl_concept_list(
    trade_date: str = "",  # Changed from Optional[str] = None
    ts_code: str = "",     # Changed from Optional[str] = None
    name: str = ""         # Changed from Optional[str] = None
) -> str:
    """
    获取开盘啦概念题材列表。

    参数:
        trade_date: 交易日期 (YYYYMMDD格式, 例如: 20241014)
        ts_code: 题材代码 (xxxxxx.KP格式, 例如: 000111.KP)
        name: 题材名称 (例如: 化债概念)
    """
    # Log received parameters for debugging
    print(f"DEBUG: hotlist.py: get_kpl_concept_list called with trade_date='{trade_date}', ts_code='{ts_code}', name='{name}'", file=sys.stderr, flush=True)

    global PRO_API_INSTANCE, PRINTED_TOKEN_ERROR
    if not PRO_API_INSTANCE:
        # Avoid re-printing the detailed error if it was already printed during module load
        if not PRINTED_TOKEN_ERROR:
            print("ERROR: hotlist.py: Tushare Pro API (PRO_API_INSTANCE) is not available in get_kpl_concept_list.", file=sys.stderr, flush=True)
        return "错误: Tushare Pro API 未成功初始化。请检查服务日志和Tushare token配置。"
    
    try:
        api_params = {}
        if trade_date: # Only add if not an empty string
            api_params['trade_date'] = trade_date
        if ts_code:    # Only add if not an empty string
            api_params['ts_code'] = ts_code
        if name:       # Only add if not an empty string
            api_params['name'] = name
        
        # The Tushare doc for kpl_concept says all parameters are optional.
        # If api_params is empty, Tushare might return latest or all concepts, or an error.
        # It's generally good to inform the user if no specific filters are applied,
        # or handle it based on expected API behavior.
        if not api_params: 
            print("DEBUG: hotlist.py: No specific parameters provided to get_kpl_concept_list. Tushare API will use its default behavior.", file=sys.stderr, flush=True)
            # Depending on Tushare's behavior for kpl_concept with no params, 
            # you might want to return a message or fetch default data.
            # For now, let Tushare handle it, or return a message if it errors.
            # return "提示: 未提供具体查询参数，将尝试获取默认的开盘啦题材数据。" # Example message

        print(f"DEBUG: hotlist.py: Calling PRO_API_INSTANCE.kpl_concept with params: {api_params}", file=sys.stderr, flush=True)
        df = PRO_API_INSTANCE.kpl_concept(**api_params)

        if df.empty:
            query_desc = f"查询参数: trade_date='{trade_date}', ts_code='{ts_code}', name='{name}'" if api_params else "无特定查询参数"
            return f"未找到符合条件的开盘啦题材数据。{query_desc}"

        results = [f"--- 开盘啦题材库查询结果 ---"]
        if api_params:
            param_strings = [f"{k}='{v}'" for k, v in api_params.items()]
            results[0] += f" (查询: {', '.join(param_strings)})"
        else:
            results[0] += " (默认/最新)"

        # Limit output for brevity, e.g., top 20 results
        df_limited = df.head(20)

        for _, row in df_limited.iterrows():
            info_parts = [
                f"交易日期: {row.get('trade_date', 'N/A')}",
                f"题材代码: {row.get('ts_code', 'N/A')}",
                f"题材名称: {row.get('name', 'N/A')}",
                f"涨停数量: {row.get('z_t_num') if pd.notna(row.get('z_t_num')) else 'N/A'}", # z_t_num can be None
                f"排名上升位数: {row.get('up_num', 'N/A')}"
            ]
            results.append("\n".join(info_parts))
            results.append("------------------------")
        
        if len(df) > len(df_limited):
            results.append(f"注意: 结果超过 {len(df_limited)} 条，仅显示前 {len(df_limited)} 条。共有 {len(df)} 条数据。")

        return "\n".join(results)

    except Exception as e:
        error_msg_detail = f"trade_date='{trade_date}', ts_code='{ts_code}', name='{name}'"
        error_msg = f"获取开盘啦题材库数据失败: {str(e)}. 查询参数: {error_msg_detail}"
        print(f"DEBUG: hotlist.py: ERROR in get_kpl_concept_list: {error_msg}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return f"获取开盘啦题材库数据失败：Tushare积分不足或无权限访问此接口。({str(e)})"
        return error_msg

# --- End of Tushare KPL Concept Tool Definition ---

# --- Tushare KPL Concept Constituents Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_kpl_concept_constituents",
    description="获取开盘啦概念题材的成分股。可以根据交易日期、题材代码或股票代码进行筛选。"
)
def get_kpl_concept_constituents(
    trade_date: str = "",
    ts_code: str = "",
    con_code: str = "" 
) -> str:
    """
    获取开盘啦题材成分股列表。

    参数:
        trade_date: 交易日期 (YYYYMMDD格式, 例如: 20241014)
        ts_code: 题材代码 (xxxxxx.KP格式, 例如: 000111.KP)
        con_code: 成分股代码 (股票代码, 例如: 600657.SH)
    """
    print(f"DEBUG: hotlist.py: get_kpl_concept_constituents called with trade_date='{trade_date}', ts_code='{ts_code}', con_code='{con_code}'", file=sys.stderr, flush=True)

    global PRO_API_INSTANCE, PRINTED_TOKEN_ERROR
    if not PRO_API_INSTANCE:
        if not PRINTED_TOKEN_ERROR:
            print("ERROR: hotlist.py: Tushare Pro API (PRO_API_INSTANCE) is not available in get_kpl_concept_constituents.", file=sys.stderr, flush=True)
        return "错误: Tushare Pro API 未成功初始化。请检查服务日志和Tushare token配置。"
    
    try:
        api_params = {}
        if trade_date:
            api_params['trade_date'] = trade_date
        if ts_code:
            api_params['ts_code'] = ts_code
        if con_code:
            api_params['con_code'] = con_code
        
        # According to Tushare docs for kpl_concept_cons, all params are optional.
        # If api_params is empty, Tushare might return data for the latest date or all available data.
        if not api_params:
            print("DEBUG: hotlist.py: No specific parameters provided to get_kpl_concept_constituents. Tushare API will use its default behavior (likely latest date).", file=sys.stderr, flush=True)
            # return "提示: 未提供具体查询参数，将尝试获取默认的题材成分数据 (可能为最新日期)。" # Optional message

        print(f"DEBUG: hotlist.py: Calling PRO_API_INSTANCE.kpl_concept_cons with params: {api_params}", file=sys.stderr, flush=True)
        df = PRO_API_INSTANCE.kpl_concept_cons(**api_params)

        if df.empty:
            query_desc = []
            if trade_date: query_desc.append(f"trade_date='{trade_date}'")
            if ts_code: query_desc.append(f"ts_code='{ts_code}'")
            if con_code: query_desc.append(f"con_code='{con_code}'")
            query_str = ", ".join(query_desc) if query_desc else "无特定查询参数"
            return f"未找到符合条件的开盘啦题材成分股数据。查询参数: {query_str}"

        results = [f"--- 开盘啦题材成分股查询结果 ---"]
        if api_params:
            param_strings = [f"{k}='{v}'" for k, v in api_params.items()]
            results[0] += f" (查询: {', '.join(param_strings)})"
        else:
            results[0] += " (默认/最新日期)"

        # Limit output for brevity, e.g., top 20 results (Tushare default limit is 3000 for this API)
        df_limited = df.head(30) # Increased limit slightly for more context

        for _, row in df_limited.iterrows():
            info_parts = [
                f"题材代码: {row.get('ts_code', 'N/A')} ({row.get('name', 'N/A')})",
                f"成分股: {row.get('con_code', 'N/A')} ({row.get('con_name', 'N/A')})",
                f"交易日期: {row.get('trade_date', 'N/A')}",
                f"描述: {row.get('desc', 'N/A')}",
                f"人气值: {row.get('hot_num') if pd.notna(row.get('hot_num')) else 'N/A'}" # hot_num can be None
            ]
            results.append("\n".join(info_parts))
            results.append("------------------------")
        
        if len(df) > len(df_limited):
            results.append(f"注意: 结果超过 {len(df_limited)} 条，仅显示前 {len(df_limited)} 条。共有 {len(df)} 条数据。")

        return "\n".join(results)

    except Exception as e:
        error_msg_detail_parts = []
        if trade_date: error_msg_detail_parts.append(f"trade_date='{trade_date}'")
        if ts_code: error_msg_detail_parts.append(f"ts_code='{ts_code}'")
        if con_code: error_msg_detail_parts.append(f"con_code='{con_code}'")
        error_msg_detail = ", ".join(error_msg_detail_parts) if error_msg_detail_parts else "无特定参数"
        
        error_msg = f"获取开盘啦题材成分股数据失败: {str(e)}. 查询参数: {error_msg_detail}"
        print(f"DEBUG: hotlist.py: ERROR in get_kpl_concept_constituents: {error_msg}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return f"获取开盘啦题材成分股数据失败：Tushare积分不足或无权限访问此接口 (kpl_concept_cons需要5000积分)。({str(e)})"
        return error_msg
# --- End of Tushare KPL Concept Constituents Tool Definition ---

# --- Tushare KPL List (Ranking Data) Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_kpl_list_data",
    description="获取开盘啦涨停、跌停、炸板、自然涨停、竞价等榜单数据。可按股票代码、交易日期、榜单类型、日期范围筛选。"
)
def get_kpl_list_data(
    trade_date: str = "",
    ts_code: str = "",
    tag: str = "", # 例如: "涨停", "跌停", "炸板", "自然涨停", "竞价"
    start_date: str = "",
    end_date: str = ""
) -> str:
    """
    获取开盘啦榜单数据。

    参数:
        trade_date: 交易日期 (YYYYMMDD格式, 例如: 20241014)
        ts_code: 股票代码 (例如: 000762.SZ)
        tag: 榜单类型 (例如: "涨停", "跌停", "炸板", "自然涨停", "竞价")
        start_date: 开始日期 (YYYYMMDD格式)
        end_date: 结束日期 (YYYYMMDD格式)
    """
    print(f"DEBUG: hotlist.py: get_kpl_list_data called with trade_date='{trade_date}', ts_code='{ts_code}', tag='{tag}', start_date='{start_date}', end_date='{end_date}'", file=sys.stderr, flush=True)

    global PRO_API_INSTANCE, PRINTED_TOKEN_ERROR
    if not PRO_API_INSTANCE:
        if not PRINTED_TOKEN_ERROR:
            print("ERROR: hotlist.py: Tushare Pro API (PRO_API_INSTANCE) is not available in get_kpl_list_data.", file=sys.stderr, flush=True)
        return "错误: Tushare Pro API 未成功初始化。请检查服务日志和Tushare token配置。"

    try:
        api_params = {}
        if trade_date:
            api_params['trade_date'] = trade_date
        if ts_code:
            api_params['ts_code'] = ts_code
        if tag:
            api_params['tag'] = tag
        if start_date:
            api_params['start_date'] = start_date
        if end_date:
            api_params['end_date'] = end_date
        
        if not api_params:
            print("DEBUG: hotlist.py: No specific parameters provided to get_kpl_list_data. Tushare API will use its default behavior.", file=sys.stderr, flush=True)

        fields_to_get = 'ts_code,name,trade_date,tag,theme,status,lu_time,ld_time,open_time,last_time,net_change,bid_amount,pct_chg,limit_order,amount,turnover_rate'
        api_params['fields'] = fields_to_get

        print(f"DEBUG: hotlist.py: Calling PRO_API_INSTANCE.kpl_list with params: {api_params}", file=sys.stderr, flush=True)
        df = PRO_API_INSTANCE.kpl_list(**api_params)

        if df.empty:
            query_desc_parts = []
            if trade_date: query_desc_parts.append(f"trade_date='{trade_date}'")
            if ts_code: query_desc_parts.append(f"ts_code='{ts_code}'")
            if tag: query_desc_parts.append(f"tag='{tag}'")
            if start_date: query_desc_parts.append(f"start_date='{start_date}'")
            if end_date: query_desc_parts.append(f"end_date='{end_date}'")
            query_str = ", ".join(query_desc_parts) if query_desc_parts else "无特定查询参数"
            return f"未找到符合条件的开盘啦榜单数据。查询参数: {query_str}"

        results = [f"--- 开盘啦榜单数据查询结果 ---"]
        # Create a description of actual parameters used for the query, excluding 'fields'
        param_strings = [f"{k}='{v}'" for k, v in api_params.items() if k != 'fields' and v]
        if param_strings:
             results[0] += f" (查询: {', '.join(param_strings)})"
        else:
             results[0] += " (默认查询)"


        df_limited = df.head(30) # Limit output

        for _, row in df_limited.iterrows():
            info_parts = [
                f"股票: {row.get('ts_code', 'N/A')} ({row.get('name', 'N/A')})",
                f"交易日期: {row.get('trade_date', 'N/A')}",
                f"标签: {row.get('tag', 'N/A')}",
                f"板块: {row.get('theme', 'N/A')}", # theme can be None or empty
                f"状态: {row.get('status', 'N/A')}",
                f"涨停时间: {row.get('lu_time', 'N/A')}",
                f"跌停时间: {row.get('ld_time', 'N/A')}",
                f"开板时间: {row.get('open_time', 'N/A')}",
                f"最后涨停: {row.get('last_time', 'N/A')}",
                #f"涨跌幅: {row.get('pct_chg', 'N/A')}%" # Old line
            ]
            # New logic for pct_chg
            pct_chg_val = row.get('pct_chg')
            if pd.isna(pct_chg_val):
                info_parts.append("涨跌幅: N/A")
            else:
                info_parts.append(f"涨跌幅: {pct_chg_val}%")

            if pd.notna(row.get('net_change')) and row.get('net_change') != '': # Ensure net_change is meaningful
                info_parts.append(f"主力净额: {row.get('net_change')}")
            if pd.notna(row.get('amount')) and row.get('amount') != '': # Ensure amount is meaningful
                info_parts.append(f"成交额: {row.get('amount')}")
            if pd.notna(row.get('turnover_rate')) and row.get('turnover_rate') != '':
                 info_parts.append(f"换手率: {row.get('turnover_rate')}%")


            results.append("\\n".join(info_parts))
            results.append("------------------------")
        
        if len(df) > len(df_limited):
            results.append(f"注意: 结果超过 {len(df_limited)} 条，仅显示前 {len(df_limited)} 条。共有 {len(df)} 条数据。")

        return "\\n".join(results)

    except Exception as e:
        error_msg_detail_parts = []
        if trade_date: error_msg_detail_parts.append(f"trade_date='{trade_date}'")
        if ts_code: error_msg_detail_parts.append(f"ts_code='{ts_code}'")
        if tag: error_msg_detail_parts.append(f"tag='{tag}'")
        if start_date: error_msg_detail_parts.append(f"start_date='{start_date}'")
        if end_date: error_msg_detail_parts.append(f"end_date='{end_date}'")
        error_msg_detail = ", ".join(error_msg_detail_parts) if error_msg_detail_parts else "无特定参数"
        
        error_msg = f"获取开盘啦榜单数据失败: {str(e)}. 查询参数: {error_msg_detail}"
        print(f"DEBUG: hotlist.py: ERROR in get_kpl_list_data: {error_msg}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return f"获取开盘啦榜单数据失败：Tushare积分不足或无权限访问此接口 (kpl_list 需要至少5000积分)。({str(e)})"
        return error_msg
# --- End of Tushare KPL List (Ranking Data) Tool Definition ---

# --- Tushare Daily Limit List (U/D/Z Stats) Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_daily_limit_list",
    description="获取每日股票涨停(U)、跌停(D)和炸板(Z)的详细统计数据，包括行业、市值、连板天数、封单金额、开板次数等。"
)
def get_daily_limit_list(
    trade_date: str = "",
    ts_code: str = "",
    limit_type: str = "", # U:涨停, D:跌停, Z:炸板
    exchange: str = "",   # SH, SZ, BJ
    start_date: str = "",
    end_date: str = ""
) -> str:
    """
    获取每日股票涨停、跌停和炸板的详细统计数据。

    参数:
        trade_date: 交易日期 (YYYYMMDD格式, 例如: 20241014)
        ts_code: 股票代码 (例如: 000001.SZ)
        limit_type: 涨跌停类型 ('U'->涨停, 'D'->跌停, 'Z'->炸板)
        exchange: 交易所代码 ('SH'->上海, 'SZ'->深圳, 'BJ'->北京)
        start_date: 开始日期 (YYYYMMDD格式)
        end_date: 结束日期 (YYYYMMDD格式)
    """
    print(f"DEBUG: hotlist.py: get_daily_limit_list called with trade_date='{trade_date}', ts_code='{ts_code}', limit_type='{limit_type}', exchange='{exchange}', start_date='{start_date}', end_date='{end_date}'", file=sys.stderr, flush=True)

    global PRO_API_INSTANCE, PRINTED_TOKEN_ERROR
    if not PRO_API_INSTANCE:
        if not PRINTED_TOKEN_ERROR:
            print("ERROR: hotlist.py: Tushare Pro API (PRO_API_INSTANCE) is not available in get_daily_limit_list.", file=sys.stderr, flush=True)
        return "错误: Tushare Pro API 未成功初始化。请检查服务日志和Tushare token配置。"

    try:
        api_params = {}
        if trade_date:
            api_params['trade_date'] = trade_date
        if ts_code:
            api_params['ts_code'] = ts_code
        if limit_type:
            api_params['limit_type'] = limit_type.upper() # Ensure U, D, Z
        if exchange:
            api_params['exchange'] = exchange.upper() # Ensure SH, SZ, BJ
        if start_date:
            api_params['start_date'] = start_date
        if end_date:
            api_params['end_date'] = end_date
        
        if not api_params.get('trade_date') and not (api_params.get('start_date') and api_params.get('end_date')) :
            print("DEBUG: hotlist.py: get_daily_limit_list called without specific date(s). This might fetch a lot of data if other filters are also broad.", file=sys.stderr, flush=True)
        
        fields_to_get = 'trade_date,ts_code,name,industry,limit,close,pct_chg,limit_times,up_stat,open_times,first_time,last_time,fd_amount,limit_amount,turnover_ratio,amount,float_mv,total_mv'
        api_params['fields'] = fields_to_get

        print(f"DEBUG: hotlist.py: Calling PRO_API_INSTANCE.limit_list_d with params: {api_params}", file=sys.stderr, flush=True)
        df = PRO_API_INSTANCE.limit_list_d(**api_params)

        if df.empty:
            query_desc_parts = []
            if trade_date: query_desc_parts.append(f"trade_date='{trade_date}'")
            if ts_code: query_desc_parts.append(f"ts_code='{ts_code}'")
            if limit_type: query_desc_parts.append(f"limit_type='{api_params.get('limit_type', '')}'")
            if exchange: query_desc_parts.append(f"exchange='{api_params.get('exchange', '')}'")
            if start_date: query_desc_parts.append(f"start_date='{start_date}'")
            if end_date: query_desc_parts.append(f"end_date='{end_date}'")
            query_str = ", ".join(query_desc_parts) if query_desc_parts else "无特定查询参数"
            return f"未找到符合条件的每日涨跌停/炸板数据。查询参数: {query_str}"

        results = [f"--- 每日涨跌停/炸板数据 ({api_params.get('limit_type', '综合')}) ---"]
        param_strings = [f"{k}='{v}'" for k, v_val in api_params.items() if k != 'fields' for v in (v_val if isinstance(v_val, list) else [v_val]) if v] # handle if params could be lists and ensure v is not empty
        if param_strings:
             results[0] += f" (查询: {', '.join(param_strings)})"
        else:
             results[0] += " (默认查询)"
        
        df_limited = df.head(30)

        for _, row in df_limited.iterrows():
            info_parts = [
                f"股票: {row.get('ts_code', 'N/A')} ({row.get('name', 'N/A')})",
                f"日期: {row.get('trade_date', 'N/A')}",
                f"行业: {row.get('industry', 'N/A')}",
                f"类型: {row.get('limit', 'N/A')} ({'涨停' if row.get('limit') == 'U' else '跌停' if row.get('limit') == 'D' else '炸板' if row.get('limit') == 'Z' else '未知'})",
                f"收盘价: {row.get('close', 'N/A')}"
            ]
            
            pct_chg_val = row.get('pct_chg')
            if pd.isna(pct_chg_val):
                info_parts.append("涨跌幅: N/A")
            else:
                info_parts.append(f"涨跌幅: {pct_chg_val}%")

            info_parts.extend([
                f"连板天数: {row.get('limit_times', 'N/A')}",
                f"涨停统计: {row.get('up_stat', 'N/A')}",
                f"开板/炸板次数: {row.get('open_times', 'N/A')}",
            ])

            current_limit_type_from_row = row.get('limit') # Use 'limit' field from data row
            
            # first_time is not applicable for D (跌停) according to docs
            if current_limit_type_from_row == 'U' or current_limit_type_from_row == 'Z':
                 info_parts.append(f"首次封板: {row.get('first_time', 'N/A')}")
            
            info_parts.append(f"最后封板: {row.get('last_time', 'N/A')}")

            # fd_amount for U/Z, limit_amount for D
            if current_limit_type_from_row == 'U' or current_limit_type_from_row == 'Z':
                info_parts.append(f"封单金额(涨停): {row.get('fd_amount', 'N/A')}")
            elif current_limit_type_from_row == 'D':
                info_parts.append(f"板上成交额(跌停): {row.get('limit_amount', 'N/A')}")
            
            info_parts.extend([
                f"成交额: {row.get('amount', 'N/A')}",
                f"换手率: {row.get('turnover_ratio', 'N/A')}%",
                f"流通市值: {row.get('float_mv', 'N/A')}",
                f"总市值: {row.get('total_mv', 'N/A')}"
            ])
            
            results.append("\\n".join(info_parts))
            results.append("------------------------")

        if len(df) > len(df_limited):
            results.append(f"注意: 结果超过 {len(df_limited)} 条，仅显示前 {len(df_limited)} 条。共有 {len(df)} 条数据。")

        return "\\n".join(results)

    except Exception as e:
        error_msg_detail_parts = []
        if trade_date: error_msg_detail_parts.append(f"trade_date='{trade_date}'")
        if ts_code: error_msg_detail_parts.append(f"ts_code='{ts_code}'")
        if limit_type: error_msg_detail_parts.append(f"limit_type='{limit_type.upper()}'") # Use submitted param
        if exchange: error_msg_detail_parts.append(f"exchange='{exchange.upper()}'") # Use submitted param
        if start_date: error_msg_detail_parts.append(f"start_date='{start_date}'")
        if end_date: error_msg_detail_parts.append(f"end_date='{end_date}'")
        error_msg_detail = ", ".join(error_msg_detail_parts) if error_msg_detail_parts else "无特定参数"
        
        error_msg = f"获取每日涨跌停/炸板数据失败: {str(e)}. 查询参数: {error_msg_detail}"
        print(f"DEBUG: hotlist.py: ERROR in get_daily_limit_list: {error_msg}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return f"获取每日涨跌停/炸板数据失败：Tushare积分不足或无权限访问此接口 (limit_list_d)。({str(e)})"
        return error_msg
# --- End of Tushare Daily Limit List (U/D/Z Stats) Tool Definition ---

# --- Start of MCP SSE Workaround Integration (copied and adapted from server.py) ---
MCP_BASE_PATH = "/sse" # The path where the MCP service will be available

print(f"DEBUG: hotlist.py: Applying MCP SSE workaround for base path: {MCP_BASE_PATH}", file=sys.stderr, flush=True)

try:
    messages_full_path = f"{MCP_BASE_PATH}/messages/"
    sse_transport = SseServerTransport(messages_full_path)
    print(f"DEBUG: hotlist.py: SseServerTransport initialized; client will be told messages are at: {messages_full_path}", file=sys.stderr, flush=True)

    async def handle_mcp_sse_handshake(request: Request) -> None:
        print(f"DEBUG: hotlist.py: MCP SSE handshake request received for: {request.url}", file=sys.stderr, flush=True)
        async with sse_transport.connect_sse(
            request.scope,
            request.receive,
            request._send, # type: ignore 
        ) as (read_stream, write_stream):
            print(f"DEBUG: hotlist.py: MCP SSE connection established for {MCP_BASE_PATH}. Starting McpServer.run.", file=sys.stderr, flush=True)
            # Ensure 'mcp' here refers to the FastMCP instance created in hotlist.py
            await mcp._mcp_server.run(
                read_stream,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
            print(f"DEBUG: hotlist.py: McpServer.run finished for {MCP_BASE_PATH}.", file=sys.stderr, flush=True)

    app.add_route(MCP_BASE_PATH, handle_mcp_sse_handshake, methods=["GET"])
    print(f"DEBUG: hotlist.py: MCP SSE handshake GET route added at: {MCP_BASE_PATH}", file=sys.stderr, flush=True)

    app.mount(messages_full_path, sse_transport.handle_post_message)
    print(f"DEBUG: hotlist.py: MCP SSE messages POST endpoint mounted at: {messages_full_path}", file=sys.stderr, flush=True)

    print(f"DEBUG: hotlist.py: MCP SSE workaround for base path {MCP_BASE_PATH} applied successfully.", file=sys.stderr, flush=True)

except Exception as e_workaround:
    print(f"DEBUG: hotlist.py: CRITICAL ERROR applying MCP SSE workaround: {str(e_workaround)}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
# --- End of MCP SSE Workaround Integration ---

# Placeholder for main execution block if this file is run directly
if __name__ == "__main__":
    print("DEBUG: hotlist.py entering main section to start Uvicorn server...", file=sys.stderr, flush=True)
    try:
        print("DEBUG: hotlist.py: Starting Uvicorn server for Hotlist MCP (FastAPI app) on port 8001...", file=sys.stderr, flush=True)
        # Run the FastAPI app instance 'app', not 'mcp' directly
        uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
        print("DEBUG: hotlist.py: Uvicorn server for Hotlist MCP stopped.", file=sys.stderr, flush=True)
    except Exception as e_server:
        print(f"ERROR: hotlist.py: Failed to start or run Uvicorn server: {str(e_server)}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if isinstance(e_server, ImportError) and "uvicorn" in str(e_server).lower():
             print("Hint: Ensure 'uvicorn' is installed. You can install it with: pip install uvicorn[standard]", file=sys.stderr, flush=True)
    print("DEBUG: hotlist.py finished main section execution.", file=sys.stderr, flush=True)

# --- 同花顺板块指数列表 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_ths_index_list",
    description="获取同花顺板块指数列表，包括概念指数、行业指数、地域指数等。可根据指数代码、市场类型、指数类型筛选。"
)
@app.get("/tools/mcp_hotlist_mcp_get_ths_index_list", summary="获取同花顺板块指数列表", deprecated=False)
async def get_ths_index_list(
    ts_code: str = Query(default="", description="指数代码,例如：885835.TI"),
    exchange: str = Query(default="", description="市场类型 A-a股 HK-港股 US-美股, 例如：A"),
    type: str = Query(default="", description="指数类型 N-概念指数 I-行业指数 R-地域指数 S-同花顺特色指数 ST-同花顺风格指数 TH-同花顺主题指数 BB-同花顺宽基指数, 例如：N")
):
    """
    获取同花顺板块指数列表，包括概念指数、行业指数、地域指数等。
    数据来源: [Tushare ths_index](https://tushare.pro/document/2?doc_id=259)
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized.")
        return {"error": "Tushare Pro API uninitialized. Check token."}
    try:
        log_params = f"ts_code='{ts_code}', exchange='{exchange}', type='{type}'"
        logger.info(f"Calling Tushare API ths_index with params: {log_params}")
        
        api_params = {}
        if ts_code:
            api_params["ts_code"] = ts_code
        if exchange:
            api_params["exchange"] = exchange
        if type:
            api_params["type"] = type
            
        df = PRO_API_INSTANCE.ths_index(**api_params)
        
        if df is None or df.empty:
            logger.info(f"No data returned from Tushare ths_index for params: {log_params}")
            return {"results": []} # Return empty list for no data, consistent with other endpoints
            
        results = df.to_dict(orient="records")
        logger.info(f"Successfully retrieved {len(results)} records from ths_index.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare ths_index with params {log_params}: {e}", exc_info=True)
        return {"error": f"Error calling Tushare ths_index: {str(e)}"}

# --- 同花顺概念板块成分 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_ths_members",
    description="获取同花顺概念板块的成分股列表。可根据板块指数代码和股票代码筛选。"
)
@app.get("/tools/mcp_hotlist_mcp_get_ths_members", summary="获取同花顺概念板块成分股列表", deprecated=False)
async def get_ths_members(
    ts_code: str = Query(default="", description="板块指数代码, 例如：885800.TI"),
    con_code: str = Query(default="", description="股票代码, 例如：000001.SZ")
):
    """
    获取同花顺概念板块的成分股列表。
    数据来源: [Tushare ths_member](https://tushare.pro/document/2?doc_id=261)
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized.")
        return {"error": "Tushare Pro API uninitialized. Check token."}
    
    if not ts_code:
        # The API is for getting members of a *specific concept index*.
        # If ts_code is not provided, it's not clear which concept's members to fetch.
        # While the Tushare API might accept only con_code (to find which concepts a stock belongs to),
        # the intent of *this MCP tool endpoint* is to list members for a given concept.
        logger.warning("ths_member called without ts_code. ts_code is required to list members of a specific concept.")
        return {"error": "ts_code (板块指数代码) is required to get concept members."}

    log_params = f"ts_code='{ts_code}', con_code='{con_code}'"
    try:
        logger.info(f"Calling Tushare API ths_member with params: {log_params}")
        
        api_params = {}
        api_params["ts_code"] = ts_code # ts_code is now effectively mandatory from check above
        if con_code:
            api_params["con_code"] = con_code
            
        df = PRO_API_INSTANCE.ths_member(**api_params)

        if df is None or df.empty:
            logger.info(f"No data returned from Tushare ths_member for params: {log_params}")
            return {"results": []}
            
        results = df.to_dict(orient="records")
        logger.info(f"Successfully retrieved {len(results)} records from ths_member.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare ths_member with params {log_params}: {e}", exc_info=True)
        return {"error": f"Error calling Tushare ths_member: {str(e)}"}

# --- 同花顺板块指数行情 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_ths_daily_data",
    description="获取同花顺板块指数的每日行情数据。可根据指数代码、交易日期或日期范围筛选。"
)
@app.get("/tools/mcp_hotlist_mcp_get_ths_daily_data", summary="获取同花顺板块指数每日行情数据", deprecated=False)
async def get_ths_daily_data(
    ts_code: str = Query(default="", description="指数代码, 例如：865001.TI"),
    trade_date: str = Query(default="", description="交易日期 (YYYYMMDD格式), 例如：20230101"),
    start_date: str = Query(default="", description="开始日期 (YYYYMMDD格式), 例如：20230101"),
    end_date: str = Query(default="", description="结束日期 (YYYYMMDD格式), 例如：20230131")
):
    """
    获取同花顺板块指数的每日行情数据。
    数据来源: [Tushare ths_daily](https://tushare.pro/document/2?doc_id=260)
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized.")
        return {"error": "Tushare Pro API uninitialized. Check token."}

    log_params = f"ts_code='{ts_code}', trade_date='{trade_date}', start_date='{start_date}', end_date='{end_date}'"
        
    api_params = {}
    if ts_code:
        api_params["ts_code"] = ts_code
    if trade_date:
        api_params["trade_date"] = trade_date
    if start_date:
        api_params["start_date"] = start_date
    if end_date:
        api_params["end_date"] = end_date

    if not ts_code and not trade_date and not (start_date and end_date):
        logger.warning(f"ths_daily called without sufficient filters: {log_params}")
        return {"error": "Please provide at least a ts_code, or a specific trade_date, or a start_date and end_date for ths_daily_data."}
    
    try:
        logger.info(f"Calling Tushare API ths_daily with params: {log_params}")
        df = PRO_API_INSTANCE.ths_daily(**api_params)

        if df is None or df.empty:
            logger.info(f"No data returned from Tushare ths_daily for params: {log_params}")
            return {"results": []}
            
        results = df.to_dict(orient="records")
        logger.info(f"Successfully retrieved {len(results)} records from ths_daily. Note: Max 3000 rows per call from Tushare.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare ths_daily with params {log_params}: {e}", exc_info=True)
        return {"error": f"Error calling Tushare ths_daily: {str(e)}"}

# --- 东方财富概念板块 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_dc_index",
    description="获取东方财富每个交易日的概念板块数据。可按代码、名称、日期或日期范围筛选。"
)
@app.get("/tools/hotlist_mcp_get_dc_index", summary="获取东方财富概念板块数据", deprecated=False)
async def get_dc_index(
    ts_code: str = Query(default="", description="指数代码（支持多个代码同时输入，用逗号分隔） 例如: BK1184.DC"),
    name: str = Query(default="", description="板块名称（例如：人形机器人）"),
    trade_date: str = Query(default="", description="交易日期（YYYYMMDD格式） 例如: 20250103"),
    start_date: str = Query(default="", description="开始日期 (YYYYMMDD格式) 例如: 20250101"),
    end_date: str = Query(default="", description="结束日期 (YYYYMMDD格式) 例如: 20250131")
):
    """
    获取东方财富每个交易日的概念板块数据。
    数据来源: [Tushare dc_index](https://tushare.pro/document/2?doc_id=362)
    权限: 用户积累5000积分可调取。
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized for get_dc_index.")
        return {"error": "Tushare Pro API uninitialized. Check token."}

    log_params = f"ts_code='{ts_code}', name='{name}', trade_date='{trade_date}', start_date='{start_date}', end_date='{end_date}'"
    
    api_params = {}
    if ts_code:
        api_params["ts_code"] = ts_code
    if name:
        api_params["name"] = name
    if trade_date:
        api_params["trade_date"] = trade_date
    if start_date:
        api_params["start_date"] = start_date
    if end_date:
        api_params["end_date"] = end_date

    # 至少需要一个有效筛选条件
    if not any(api_params.values()): # Simpler check: if api_params is empty
        logger.warning(f"get_dc_index called without any filters: {log_params}")
        return {"error": "Please provide at least one filter for dc_index (e.g., ts_code, name, trade_date, or start_date/end_date)."}
    
    try:
        logger.info(f"Calling Tushare API dc_index with params: {api_params}") # Log actual params sent
        df = PRO_API_INSTANCE.dc_index(**api_params)

        if df is None or df.empty:
            logger.info(f"No data returned from Tushare dc_index for params: {log_params}")
            return {"results": []}
            
        results = df.to_dict(orient="records")
        logger.info(f"Successfully retrieved {len(results)} records from dc_index for params: {log_params}.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare dc_index with params {log_params}: {e}", exc_info=True)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return {"error": f"获取东方财富概念板块数据失败：Tushare积分不足或无权限访问此接口 (dc_index 需要5000积分)。({str(e)})"}
        return {"error": f"Error calling Tushare dc_index: {str(e)}"}

# --- 东方财富板块成分 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_dc_members",
    description="获取东方财富某概念板块的每日成分股数据。须提供板块指数代码。"
)
@app.get("/tools/hotlist_mcp_get_dc_members", summary="获取东方财富板块成分股列表", deprecated=False)
async def get_dc_members(
    ts_code: str = Query(..., description="板块指数代码, 例如：BK1184.DC (此参数为必需参数)"),
    trade_date: str = Query(default="", description="交易日期（YYYYMMDD格式）, 例如：20250102 (提供日期以获取特定日成分)"),
    con_code: str = Query(default="", description="成分股票代码, 例如：002117.SZ (可选，结合ts_code和trade_date筛选特定成分股)")
):
    """
    获取东方财富板块每日成分数据。
    数据来源: [Tushare dc_member](https://tushare.pro/document/2?doc_id=363)
    权限: 用户积累5000积分可调取。
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized for get_dc_members.")
        return {"error": "Tushare Pro API uninitialized. Check token."}
    
    # ts_code is mandatory via Query(...)

    log_params = f"ts_code='{ts_code}', trade_date='{trade_date}', con_code='{con_code}'"
    
    api_params = {"ts_code": ts_code} 
    if trade_date:
        api_params["trade_date"] = trade_date
    if con_code:
        api_params["con_code"] = con_code
            
    try:
        logger.info(f"Calling Tushare API dc_member with params: {api_params}") # Log actual params sent
        df = PRO_API_INSTANCE.dc_member(**api_params)

        if df is None or df.empty:
            logger.info(f"No data returned from Tushare dc_member for params: {log_params}")
            return {"results": []}
            
        results = df.to_dict(orient="records")
        logger.info(f"Successfully retrieved {len(results)} records from dc_member for params: {log_params}.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare dc_member with params {log_params}: {e}", exc_info=True)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return {"error": f"获取东方财富板块成分数据失败：Tushare积分不足或无权限访问此接口 (dc_member 需要5000积分)。({str(e)})"}
        return {"error": f"Error calling Tushare dc_member: {str(e)}"}

# --- End of 东方财富板块成分 Tool Definition ---

# --- 同花顺热榜 Tool Definition ---
@mcp.tool(
    name="hotlist_mcp_get_ths_hot_list",
    description="获取同花顺App热榜数据，包括热股、概念板块、ETF等。可按日期、代码、榜单类型筛选。"
)
@app.get("/tools/hotlist_mcp_get_ths_hot_list", summary="获取同花顺App热榜数据", deprecated=False)
async def get_ths_hot_list(
    trade_date: str = Query(default="", description="交易日期 (YYYYMMDD格式)，例如：20240315"),
    ts_code: str = Query(default="", description="TS代码，例如：300750.SZ"),
    market: str = Query(default="", description="热榜类型 (例如：热股, ETF, 概念板块, 行业板块)"),
    is_new: str = Query(default="Y", description="是否最新 (Y/N, 默认Y)。N则为盘中和盘后阶段采集。")
):
    """
    获取同花顺App热榜数据。
    数据来源: [Tushare ths_hot](https://tushare.pro/document/2?doc_id=320)
    权限: 用户积累5000积分可调取。
    """
    if not PRO_API_INSTANCE:
        logger.error("Tushare Pro API uninitialized for get_ths_hot_list.")
        return {"error": "Tushare Pro API uninitialized. Check token."}

    log_params = f"trade_date='{trade_date}', ts_code='{ts_code}', market='{market}', is_new='{is_new}'"
    
    api_params = {}
    if trade_date:
        api_params["trade_date"] = trade_date
    if ts_code:
        api_params["ts_code"] = ts_code
    if market:
        api_params["market"] = market
    if is_new: # is_new has a default, so it will always be present unless explicitly empty string
        api_params["is_new"] = is_new

    if not market and not ts_code: # Require at least market or ts_code to make a meaningful query
        logger.warning(f"get_ths_hot_list called without 'market' or 'ts_code': {log_params}")
        return {"error": "Please provide at least a 'market' (热榜类型) or a 'ts_code' for ths_hot."}
    
    # Define specific fields to fetch; adjust as needed
    fields_to_get = "trade_date,data_type,ts_code,ts_name,rank,pct_change,current_price,concept,rank_reason,hot,rank_time"
    api_params["fields"] = fields_to_get

    try:
        logger.info(f"Calling Tushare API ths_hot with params: {api_params}")
        df = PRO_API_INSTANCE.ths_hot(**api_params)

        if df is None or df.empty:
            logger.info(f"No data returned from Tushare ths_hot for params: {log_params}")
            return {"results": []}
            
        # Replace NaN with None (or suitable string like 'N/A') for JSON compatibility
        df_filled = df.fillna(value=pd.NA).astype(str).where(pd.notna(df), None) # Convert NA to None for JSON
        results = df_filled.to_dict(orient="records")
        
        logger.info(f"Successfully retrieved {len(results)} records from ths_hot for params: {log_params}.")
        return {"results": results}
    except Exception as e:
        logger.error(f"Error calling Tushare ths_hot with params {log_params}: {e}", exc_info=True)
        if "积分" in str(e) or "credits" in str(e).lower() or "权限" in str(e):
             return {"error": f"获取同花顺热榜数据失败：Tushare积分不足或无权限访问此接口 (ths_hot 需要5000积分)。({str(e)})"}
        return {"error": f"Error calling Tushare ths_hot: {str(e)}"}

# --- End of 同花顺热榜 Tool Definition ---

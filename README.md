# 股票分时数据查询服务

这是一个通过HTTP调用查询股票分时数据的服务，支持MCP（多内容提供商）工具和SSE（Server-Sent Events）。可以部署在本地或Google Cloud Run上运行。

## 功能

本服务提供两个主要工具：

1. **查询最新X条数据**：查询特定股票在给定结束时间前推X条分时数据
2. **查询时间区间数据**：查询特定股票在给定时间区间内的分时数据

## 安装

```bash
# 创建并激活虚拟环境
python3.12 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

## 配置

在项目根目录创建`.env`文件，包含以下内容：

```
SUPABASE_URL=你的Supabase项目URL
SUPABASE_KEY=你的Supabase项目API密钥
```

## 运行

### 本地运行

```bash
uvicorn stock_data_api:app --reload
```

服务将在 http://localhost:8000 上运行

### 部署到GitHub和Google Cloud Run

1. 将项目上传到GitHub仓库：

```bash
# 初始化Git仓库
git init

# 添加所有文件
git add .

# 提交更改
git commit -m "初始提交"

# 添加远程仓库
git remote add origin https://github.com/你的用户名/你的仓库名.git

# 推送到GitHub
git push -u origin main
```

2. 通过Google Cloud Run部署：

   - 方法1：使用Google Cloud Console直接从GitHub仓库部署
   - 方法2：使用GitHub Actions自动部署
   - 方法3：使用Cloud Build自动部署

详细部署步骤请参考 [DEPLOY.md](DEPLOY.md) 文件。

## API文档

启动服务后，访问 http://localhost:8000/docs 查看完整的API文档

## API端点

### 1. 查询最新X条数据

```
GET /api/latest_bars/{time_level}/{stock_code}?end_time={end_time}&limit={limit}
```

参数：
- `time_level`: 时间级别，可选值为 `15min`, `30min`, `60min`
- `stock_code`: 股票代码，例如 `sz002353`
- `end_time`: 结束时间（可选），格式为 `YYYY-MM-DDTHH:MM:SS`
- `limit`: 返回的记录数量（可选），默认为10

### 2. 查询时间区间数据

```
GET /api/bars_range/{time_level}/{stock_code}?start_time={start_time}&end_time={end_time}
```

参数：
- `time_level`: 时间级别，可选值为 `15min`, `30min`, `60min`
- `stock_code`: 股票代码，例如 `sz002353`
- `start_time`: 开始时间，格式为 `YYYY-MM-DDTHH:MM:SS`
- `end_time`: 结束时间，格式为 `YYYY-MM-DDTHH:MM:SS`

## MCP工具和SSE端点

本服务支持通过SSE（Server-Sent Events）方式调用MCP工具，可以集成到支持MCP的AI助手中。

### MCP工具列表

```
GET /sse
```

返回可用的MCP工具列表，包括工具名称、描述和参数定义。

### 调用MCP工具

```
POST /sse
```

请求体格式：
```json
{
  "type": "function",
  "function": {
    "name": "工具名称",
    "parameters": {
      // 工具参数
    }
  }
}
```

支持的工具：

1. **get_latest_bars**：查询特定股票在给定结束时间前推X条分时数据
   - 参数：
     - `time_level`: 时间级别，可选值为 `15min`, `30min`, `60min`
     - `stock_code`: 股票代码，例如 `sz002353`
     - `end_time`: 结束时间（可选），格式为 `YYYY-MM-DDTHH:MM:SS`
     - `limit`: 返回的记录数量（可选），默认为10

2. **get_bars_range**：查询特定股票在给定时间区间内的分时数据
   - 参数：
     - `time_level`: 时间级别，可选值为 `15min`, `30min`, `60min`
     - `stock_code`: 股票代码，例如 `sz002353`
     - `start_time`: 开始时间，格式为 `YYYY-MM-DDTHH:MM:SS`
     - `end_time`: 结束时间，格式为 `YYYY-MM-DDTHH:MM:SS`

响应格式为SSE事件流，每个事件包含查询结果的JSON数据。

### 示例

使用curl调用get_latest_bars工具：

```bash
curl -X POST http://127.0.0.1:8000/sse \
-H "Content-Type: application/json" \
-d '{"type":"function","function":{"name":"get_latest_bars","parameters":{"time_level":"15min","stock_code":"sz002353","limit":5}}}'
```

使用curl调用get_bars_range工具：

```bash
curl -X POST http://127.0.0.1:8000/sse \
-H "Content-Type: application/json" \
-d '{"type":"function","function":{"name":"get_bars_range","parameters":{"time_level":"15min","stock_code":"sz002353","start_time":"2025-08-01T06:00:00","end_time":"2025-08-01T07:00:00"}}}'
```
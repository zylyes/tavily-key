# MCP 工具集

<cite>来源：[mcp_server.py](file://app/mcp_server.py)</cite>

## 目录

- [概述](#概述)
- [工具总览](#工具总览)
- [tavily_search（网络搜索）](#tavily_search网络搜索)
- [tavily_extract（内容提取）](#tavily_extract内容提取)
- [tavily_crawl（网站爬取）](#tavily_crawl网站爬取)
- [tavily_map（站点地图）](#tavily_map站点地图)
- [tavily_research（深度研究）](#tavily_research深度研究)
- [tavily_pool_status（密钥池状态）](#tavily_pool_status密钥池状态)
- [MCP 协议调用方式](#mcp-协议调用方式)
- [公共行为与错误处理](#公共行为与错误处理)

## 概述

本服务是名为 **Tavily Web Search** 的 MCP Server，基于 `FastMCP` 框架构建，对外暴露 6 个工具，供 AI Agent 通过 MCP 协议发现和调用。所有工具均通过 Tavily Python SDK（`TavilyClient`）与 Tavily API 通信，API Key 由 KeyPool 统一托管，默认采用 round-robin 轮询策略选取。

```python
mcp = FastMCP(
    "Tavily Web Search",
    instructions="Use this server to search the web, extract content from URLs, "
                 "crawl websites, map site structures, and conduct AI-powered research.",
)
```

工具通过 `@mcp.tool()` 装饰器注册，函数名即 MCP 工具名。所有工具统一返回 `json.dumps(..., ensure_ascii=False, indent=2)` 序列化后的 JSON 字符串；调用失败时返回包含 `error` 与 `key_used` 字段的 JSON，便于在服务端追踪具体 Key 的问题。

```mermaid
graph LR
    Agent[AI Agent / MCP Client] -->|tools/call JSON-RPC| Server[Tavily Web Search<br/>FastMCP Server]
    Server --> S[tavily_search]
    Server --> E[tavily_extract]
    Server --> C[tavily_crawl]
    Server --> M[tavily_map]
    Server --> R[tavily_research]
    Server --> P[tavily_pool_status]
    S --> Pool[KeyPool<br/>round-robin 取 Key]
    E --> Pool
    C --> Pool
    M --> Pool
    R --> Pool
    Pool --> TC[TavilyClient]
    TC --> API[Tavily API]
```

## 工具总览

| MCP 工具名 | 功能 | 底层 TavilyClient 方法 | 耗时特征 |
|---|---|---|---|
| `tavily_search` | 网络搜索，返回带相关性评分的结构化结果 | `client.search()` | 秒级 |
| `tavily_extract` | 从 URL 提取纯净正文（支持 JS 渲染页面） | `client.extract()` | 秒级，每 URL 可设超时 |
| `tavily_crawl` | 从起始 URL 爬取多页面内容 | `client.crawl()` | 数十秒 |
| `tavily_map` | 快速发现站点 URL 结构，比爬取更快 | `client.map()` | 秒级 |
| `tavily_research` | AI 深度研究，生成带引用的综合报告 | `client.research()` | 30–120 秒 |
| `tavily_pool_status` | 查询 API Key 池状态与用量统计 | 无（直接读取 KeyPool） | 毫秒级 |

## tavily_search（网络搜索）

核心搜索工具，返回 LLM 优化的搜索结果（标题、正文片段、相关性评分、URL），支持 `general`、`news`、`finance` 三种主题以及时间范围过滤。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `query` | str | 必填 | 搜索查询语句 |
| `search_depth` | str | `"basic"` | `basic`（1 积分）或 `advanced`（2 积分），advanced 返回更相关的来源 |
| `topic` | str | `"general"` | `general`、`news` 或 `finance` |
| `time_range` | str | `""` | `day`/`week`/`month`/`year` 或简写 `d`/`w`/`m`/`y` |
| `start_date` | str | `""` | `YYYY-MM-DD` 格式，返回该日期之后的结果 |
| `end_date` | str | `""` | `YYYY-MM-DD` 格式，返回该日期之前的结果 |
| `max_results` | int | `5` | 返回结果数量，范围 0–20 |
| `chunks_per_source` | int | `3` | 每个来源的最大内容块数（1–3），仅 `advanced` 深度生效 |
| `include_images` | bool | `False` | 结果中包含图片 |
| `include_image_descriptions` | bool | `False` | 附带图片描述 |
| `include_answer` | bool | `False` | 生成 LLM 综合回答（basic/advanced 均可） |
| `include_raw_content` | bool | `False` | 返回清洗后的 HTML/Markdown 原文 |
| `include_domains` | list[str] | `None` | 限定搜索的域名列表（最多 300 个） |
| `exclude_domains` | list[str] | `None` | 排除的域名列表（最多 150 个） |
| `country` | str | `""` | 优先返回该国家的结果（仅 `topic=general` 时生效） |
| `exact_match` | bool | `False` | 仅返回包含精确短语的结果 |
| `include_favicon` | bool | `False` | 返回站点 favicon URL |
| `include_usage` | bool | `True` | 响应中包含积分用量（默认开启，便于追踪） |

### 底层实现

调用 `TavilyClient.search(**kwargs)`，并按需附加 `time_range`、`start_date`、`end_date`、`include_domains`、`exclude_domains`、`country` 等可选参数。响应若包含 `usage` 字段则读取实际消耗积分，缺失或为 0 时按 `_est_credits(search_depth)` 估算（advanced=2，basic=1）：

```python
resp = client.search(**kwargs)
credits = 0
if isinstance(resp, dict) and "usage" in resp:
    usage = resp["usage"]
    if isinstance(usage, dict):
        credits = usage.get("credits", 0) or _est_credits(search_depth)
```

### 返回示例

```json
{
  "query": "MCP 协议是什么",
  "results": [
    {
      "title": "Model Context Protocol — 官方文档",
      "url": "https://modelcontextprotocol.io/",
      "content": "MCP 是一种开放协议，用于连接 AI 模型与外部数据源……",
      "score": 0.982,
      "published_date": "2025-01-15"
    }
  ],
  "answer": "MCP（Model Context Protocol）是……",
  "usage": {
    "credits": 1
  }
}
```

> 提示：当 `include_answer=True` 时，响应中会附加 `answer` 字段；当 `include_images=True` 时，会附加 `images` 数组。

### 适用场景

- 需要实时、带来源引用的通用网络检索
- 新闻、财经主题的时效性查询（配合 `time_range`）
- 限定或排除特定域名的合规检索（`include_domains` / `exclude_domains`）

## tavily_extract（内容提取）

从 1–20 个 URL 中提取干净正文，支持 JavaScript 渲染页面。`format` 可选 `markdown` 或 `text`。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `urls` | list[str] | 必填 | 要提取的 URL 列表（最多 20 个） |
| `extract_depth` | str | `"basic"` | `basic`（每 5 个 URL 消耗 1 积分）或 `advanced`（每 5 个 URL 消耗 2 积分） |
| `format` | str | `"markdown"` | `markdown` 或 `text` |
| `include_images` | bool | `False` | 提取页面内图片 |
| `include_favicon` | bool | `False` | 返回站点 favicon URL |
| `include_usage` | bool | `True` | 响应中包含积分用量 |
| `query` | str | `""` | 用户意图描述，用于对内容块重排序 |
| `chunks_per_source` | int | `3` | 每个来源的内容块数（1–5），**需配合 `query` 使用** |
| `timeout` | float | `30.0` | 每个 URL 的最大提取秒数（1–60） |

### 底层实现

调用 `TavilyClient.extract(**kwargs)`。`query` 仅在非空时写入参数，同时才会携带 `chunks_per_source`；`timeout` 为非空时传入：

```python
kwargs = {
    "urls": urls,
    "extract_depth": extract_depth,
    "format": format,
    "include_images": include_images,
    "include_favicon": include_favicon,
    "include_usage": include_usage,
}
if query:
    kwargs["query"] = query
    kwargs["chunks_per_source"] = chunks_per_source
if timeout:
    kwargs["timeout"] = timeout
```

### 返回示例

```json
{
  "results": [
    {
      "url": "https://example.com/article",
      "raw_content": "# 标题\n\n正文内容……",
      "images": []
    }
  ],
  "failed_results": [],
  "usage": {
    "credits": 1
  }
}
```

### 适用场景

- 对搜索结果中的页面做二次内容抽取
- 抓取动态渲染（JS）页面的正文
- 为 RAG 流水线提供干净的 Markdown 文本

## tavily_crawl（网站爬取）

从起始 URL 出发，沿链接逐层爬取多个页面并提取内容。与 `tavily_map` 相比，crawl 会实际抓取页面正文，而不仅是发现 URL。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `url` | str | 必填 | 爬取的起始 URL |
| `max_depth` | int | `2` | 跟随链接的层级深度 |
| `limit` | int | `10` | 最大爬取页面数 |
| `instructions` | str | `""` | 自然语言指令，用于语义聚焦 |
| `chunks_per_source` | int | `3` | 每个来源的内容块数（1–5），**需配合 `instructions` 使用** |
| `include_images` | bool | `False` | 提取页面内图片 |
| `include_favicon` | bool | `False` | 返回 favicon URL |
| `include_usage` | bool | `True` | 响应中包含积分用量 |
| `select_paths` | list[str] | `None` | 正则表达式，仅爬取匹配的路径 |
| `exclude_paths` | list[str] | `None` | 正则表达式，排除匹配的路径 |
| `timeout` | float | `60.0` | 整个爬取过程的超时秒数（10–60） |

### 底层实现

调用 `TavilyClient.crawl(**kwargs)`。`instructions` 非空时才会附带 `chunks_per_source`；`select_paths` / `exclude_paths` 用于正则过滤，例如只爬取 `/docs` 下的文档页：

```python
resp = client.crawl(
    url="https://example.com",
    max_depth=2,
    limit=10,
    select_paths=["^/docs/.*"],
    exclude_paths=["^/login"],
)
```

### 返回示例

```json
{
  "url": "https://example.com",
  "pages_crawled": 8,
  "results": [
    {
      "url": "https://example.com/",
      "raw_content": "……"
    }
  ],
  "usage": {
    "credits": 40
  }
}
```

### 适用场景

- 整站文档、博客的内容采集
- 站点规模不大但需要逐页正文的场景
- 用 `select_paths`/`exclude_paths` 精确控制采集范围

## tavily_map（站点地图）

快速发现并列出站点内的 URL 结构，比爬取更轻量、更快。适合先探测站点布局，再决定对哪些页面执行 `tavily_extract`。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `url` | str | 必填 | 起始 URL |
| `max_depth` | int | `2` | 探索的链接深度 |
| `limit` | int | `100` | 最多发现的 URL 数量 |
| `instructions` | str | `""` | 自然语言指令，过滤符合条件的页面 |
| `select_paths` | list[str] | `None` | 正则表达式，包含匹配的路径 |
| `exclude_paths` | list[str] | `None` | 正则表达式，排除匹配的路径 |
| `include_usage` | bool | `True` | 响应中包含积分用量 |
| `timeout` | float | `60.0` | 超时秒数（10–60） |

### 底层实现

调用 `TavilyClient.map(**kwargs)`，仅发现 URL，不抓取正文：

```python
resp = client.map(
    url="https://example.com",
    max_depth=2,
    limit=100,
)
```

### 返回示例

```json
{
  "url": "https://example.com",
  "urls": [
    "https://example.com/",
    "https://example.com/docs",
    "https://example.com/blog"
  ],
  "usage": {
    "credits": 1
  }
}
```

### 适用场景

- 爬虫任务前的站点结构探测
- 快速生成站点 URL 清单
- 与 `tavily_extract` 组合：先 map 后 extract，避免无差别爬取

## tavily_research（深度研究）

AI 驱动的深度研究工具：自动收集来源、交叉分析，并生成带引用的综合报告。耗时约 30–120 秒，适合复杂问题。

> **注意**：`tavily-python` 0.7.x 起，`research()` 改为**异步任务式 API**——`research(input=...)` 提交任务并返回 `request_id`，服务端内部轮询 `get_research(request_id)` 直到任务完成，最终返回带引用的报告。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `input` | str | 必填 | 研究问题或主题 |
| `model` | str | `"auto"` | `auto`、`mini` 或 `pro`（更全面的分析） |
| `citation_format` | str | `"numbered"` | 引用格式：`numbered`、`mla`、`apa` 或 `chicago` |
| `include_domains` | list[str] | `None` | 来源域名的软性偏好（最多 20 个） |
| `exclude_domains` | list[str] | `None` | 硬性屏蔽的域名（最多 20 个） |
| `wait` | bool | `True` | `True`：等待报告完成并返回最终报告；`False`：提交后立即返回 `request_id`，配合 `tavily_research_status` 查询 |
| `timeout` | float | `300.0` | 等待报告的最大秒数 |
| `poll_interval` | float | `2.0` | 状态轮询间隔（秒） |

> **任务状态查询**：`wait=false` 提交后可用 `tavily_research_status(request_id)` 查询任务状态与结果。任务与提交时的 key 绑定（Tavily 任务按 key 隔离），状态查询优先使用同一 key，该 key 失效/耗尽时才回退轮询；key 映射持久化于 `data/research_keys.json`，MCP 服务重启后仍可查询。

### 底层实现

先调用 `TavilyClient.research(input=..., ...)` 提交任务拿到 `request_id`，再循环 `get_research(request_id)` 轮询直到 `status` 为 `completed`（或失败/超时）：

```python
resp = client.research(
    input="2025 年 AI Agent 发展趋势",
    model="pro",
    citation_format="numbered",
    include_domains=["arxiv.org", "openai.com"],
)
request_id = resp["request_id"]
# 服务端内部：轮询直到 status == "completed"
result = client.get_research(request_id)
```

### 返回示例

```json
{
  "request_id": "…",
  "status": "completed",
  "content": "……（引用 12 个来源的深度分析报告）",
  "sources": [
    {
      "title": "……",
      "url": "https://……"
    }
  ]
}
```

### 适用场景

- 需要多源交叉验证的复杂调研问题
- 生成带引用的研究报告或综述
- Agent 无法在单次搜索中完成的综合分析

> 注意：该工具耗时较长（30–120 秒），Agent 调用时应设置足够的超时，避免中断。

## tavily_pool_status（密钥池状态）

无参数工具，直接读取 KeyPool 统计信息，返回活跃 Key 数量、各 Key 的用量、延迟与近期活动。适用于运维排查与健康检查。

```python
@mcp.tool()
def tavily_pool_status() -> str:
    """Get API key pool status — active keys, usage stats, recent activity."""
    stats = pool.get_stats()
    return json.dumps(stats, ensure_ascii=False, indent=2)
```

返回内容由 `KeyPool.get_stats()` 提供，包括但不限于：Key 掩码、请求计数、成功率、累计积分、平均延迟等。

## MCP 协议调用方式

### 工具发现

AI Agent 可通过 `tools/list` 发现全部工具及其 JSON Schema 参数定义：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

### 工具调用

通过 `tools/call` 发起调用，`name` 为工具名，`arguments` 为参数字典：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "tavily_search",
    "arguments": {
      "query": "Tavily MCP Server",
      "search_depth": "advanced",
      "max_results": 5,
      "include_answer": true
    }
  }
}
```

### Python 客户端示例

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server = StdioServerParameters(
        command="python",
        args=["mcp_server.py"],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 列出所有工具
            tools = await session.list_tools()
            for tool in tools.tools:
                print(tool.name, tool.description)

            # 调用搜索工具
            result = await session.call_tool(
                "tavily_search",
                {"query": "MCP protocol", "max_results": 3},
            )
            print(result.content[0].text)

asyncio.run(main())
```

### 一次调用的完整时序

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant Server as FastMCP Server
    participant Pool as KeyPool
    participant Client as TavilyClient

    Agent->>Server: tools/call → tavily_search(query=...)
    Server->>Pool: next_key()（round-robin）
    Pool-->>Server: (raw_key, masked_key)
    Server->>Client: search(**kwargs)
    Client-->>Server: JSON 响应（含 usage.credits）
    Server->>Pool: record_request(masked, "search", latency, success, credits)
    Server-->>Agent: JSON 字符串（indent=2）
```

## 公共行为与错误处理

所有工具定义于 [mcp_server.py](file://app/mcp_server.py)，共享以下机制：

### Key 获取

每个工具调用开始时通过 `_get_client()` 从 KeyPool 取 Key，策略由模块级常量 `KEY_STRATEGY` 控制：

```python
KEY_STRATEGY = "round-robin"  # or "least-used"

def _get_client() -> tuple[TavilyClient, str]:
    if KEY_STRATEGY == "least-used":
        result = pool.next_key_least_used()
    else:
        result = pool.next_key()
    if result is None:
        raise RuntimeError("No active API keys in pool. Add keys via CLI or dashboard.")
    raw, masked = result
    return TavilyClient(raw), masked
```

- `round-robin`：轮询选取下一个未被限流的 Key
- `least-used`：选取累计使用次数最少的 Key
- KeyPool 中没有任何可用 Key 时抛出 `RuntimeError`，该异常发生在工具内部的 `try/except` 之外，会直接冒泡到 MCP 调用层

### 用量记录

每个工具调用后通过 `_record()` 写入一条调用记录，供统计与健康检查使用：

```python
pool.record_request(masked, endpoint, latency, success, credits, error_msg)
```

记录的端点名分别为 `search`、`extract`、`crawl`、`map`、`research`；延迟以毫秒计。积分优先取响应中的 `usage.credits`，搜索工具在缺失时按 `_est_credits(search_depth)` 估算。

### 错误返回格式

每个工具体内部使用 `try/except` 包裹 SDK 调用，捕获异常后返回统一结构，调用方可通过 `error` 字段判断失败原因、通过 `key_used` 定位具体 Key：

```json
{
  "error": "Tavily API returned 429 ...",
  "key_used": "tvly-****abcd"
}
```

### 与相邻主题的关系

- **Key 轮询与健康检查**：`_get_client()` 与 `_record()` 的行为由 KeyPool 实现，限流熔断、健康判定均在 KeyPool 内部完成
- **用量追踪与日志**：每次调用的积分、延迟、成功与否经 `_record()` 落库，`tavily_pool_status` 工具可直接读取这些统计数据
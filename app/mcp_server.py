#!/usr/bin/env python3
"""
Tavily MCP Server — Web search, extract, crawl, map, research via MCP protocol.
API keys managed by KeyPool with configurable load-balancing strategy.
"""
from __future__ import annotations

import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from tavily import TavilyClient

from key_pool import KeyPool, _mask
from logging_setup import get_logger
from settings import get_settings

_log = get_logger("mcp_server")

mcp = FastMCP("Tavily Web Search", instructions="Use this server to search the web, extract content from URLs, crawl websites, map site structures, and conduct AI-powered research.")

pool = KeyPool()


def _key_strategy() -> str:
    """负载均衡策略（config.json key_strategy）：round-robin | least-used。"""
    cfg = get_settings()
    s = (cfg.get("key_strategy") or "round-robin").strip().lower()
    return s if s in ("round-robin", "least-used") else "round-robin"


def _get_client() -> tuple[TavilyClient, str]:
    if _key_strategy() == "least-used":
        result = pool.next_key_least_used()
    else:
        result = pool.next_key()
    if result is None:
        raise RuntimeError("No active API keys in pool. Add keys via CLI or dashboard.")
    raw, masked = result
    return TavilyClient(raw), masked


def _record(masked: str, endpoint: str, start: float, success: bool,
            credits: int = 0, error_msg: str = ""):
    latency = (time.time() - start) * 1000
    pool.record_request(masked, endpoint, latency, success, credits, error_msg)


# ═══════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def tavily_search(
    query: str,
    search_depth: str = "basic",
    topic: str = "general",
    time_range: str = "",
    start_date: str = "",
    end_date: str = "",
    max_results: int = 5,
    chunks_per_source: int = 3,
    include_images: bool = False,
    include_image_descriptions: bool = False,
    include_answer: bool = False,
    include_raw_content: bool = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    country: str = "",
    exact_match: bool = False,
    include_favicon: bool = False,
    include_usage: bool = True,
) -> str:
    """Search the web. Returns LLM-optimized results with content, scores, and URLs.

    Args:
        query: Search query string.
        search_depth: `basic` (1 credit), `advanced` (2 credits), `fast` (low latency), or `ultra-fast` (lowest latency).
        topic: `general`, `news`, or `finance`.
        time_range: `day`, `week`, `month`, `year` or `d`, `w`, `m`, `y`.
        start_date: YYYY-MM-DD format. Returns results after this date.
        end_date: YYYY-MM-DD format. Returns results before this date.
        max_results: Number of results, 0-20. Default 5.
        chunks_per_source: Max content chunks per source (1-3). Only with advanced depth.
        include_images: Include images in results.
        include_image_descriptions: Include image descriptions with images.
        include_answer: Include LLM-generated answer. `basic` or `advanced`.
        include_raw_content: Include cleaned HTML/markdown content.
        include_domains: List of domains to restrict search to (max 300).
        exclude_domains: List of domains to exclude (max 150).
        country: Prioritize results from this country (only with topic=general).
        exact_match: Only return results with exact quoted phrases.
        include_favicon: Include favicon URLs.
        include_usage: Include credit usage in response (default True for tracking).
    """
    t0 = time.time()
    client, masked = _get_client()
    kwargs: dict[str, Any] = {
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "max_results": max_results,
        "chunks_per_source": chunks_per_source,
        "include_images": include_images,
        "include_image_descriptions": include_image_descriptions,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "include_favicon": include_favicon,
        "exact_match": exact_match,
        "include_usage": include_usage,
    }
    # 官方行为（与官方 tavily-mcp 一致）：start_date/end_date 与 time_range
    # 互斥，同时设置时 API 会报错；指定了具体日期区间则忽略 time_range。
    if (start_date or end_date) and time_range:
        time_range = ""
    if time_range:
        kwargs["time_range"] = time_range
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    if country:
        kwargs["country"] = country

    try:
        resp = client.search(**kwargs)
        credits = 0
        if isinstance(resp, dict) and "usage" in resp:
            usage = resp["usage"]
            if isinstance(usage, dict):
                credits = usage.get("credits", 0) or _est_credits(search_depth)
        _record(masked, "search", t0, True, credits)
        return json.dumps(resp, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.warning("search 失败 masked=%s: %s", masked, str(e)[:300])
        _record(masked, "search", t0, False, 0, str(e))
        return json.dumps({"error": str(e), "key_used": masked})


@mcp.tool()
def tavily_extract(
    urls: list[str],
    extract_depth: str = "basic",
    format: str = "markdown",
    include_images: bool = False,
    include_favicon: bool = False,
    include_usage: bool = True,
    query: str = "",
    chunks_per_source: int = 3,
    timeout: float = 30.0,
) -> str:
    """Extract clean content from one or more URLs. Handles JavaScript-rendered pages.

    Args:
        urls: List of URLs to extract (max 20).
        extract_depth: `basic` (1 credit per 5 URLs) or `advanced` (2 credits per 5 URLs).
        format: `markdown` or `text`.
        include_images: Include extracted images.
        include_favicon: Include favicon URLs.
        include_usage: Include credit usage (default True).
        query: User intent for reranking content chunks.
        chunks_per_source: Max chunks per source, 1-5. Requires query.
        timeout: Max seconds per URL, 1-60.
    """
    t0 = time.time()
    client, masked = _get_client()
    kwargs: dict[str, Any] = {
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

    try:
        resp = client.extract(**kwargs)
        credits = 0
        if isinstance(resp, dict) and "usage" in resp:
            credits = resp["usage"].get("credits", 0) if isinstance(resp["usage"], dict) else 0
        _record(masked, "extract", t0, True, credits)
        return json.dumps(resp, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.warning("extract 失败 masked=%s: %s", masked, str(e)[:300])
        _record(masked, "extract", t0, False, 0, str(e))
        return json.dumps({"error": str(e), "key_used": masked})


@mcp.tool()
def tavily_crawl(
    url: str,
    max_depth: int = 2,
    max_breadth: int = 20,
    limit: int = 10,
    instructions: str = "",
    chunks_per_source: int = 3,
    include_images: bool = False,
    include_favicon: bool = False,
    include_usage: bool = True,
    select_paths: list[str] | None = None,
    select_domains: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    allow_external: bool = True,
    extract_depth: str = "basic",
    format: str = "markdown",
    timeout: float = 150.0,
) -> str:
    """Crawl a website and extract content from multiple pages.

    Starts from a base URL and follows links, extracting content from discovered pages.

    Args:
        url: Starting URL for the crawl.
        max_depth: How many levels of links to follow.
        max_breadth: Max links to follow per level of the tree (per page).
        limit: Maximum number of pages to crawl.
        instructions: Natural language instructions for semantic focus.
            When provided, the cost becomes 2 credits per 10 pages (vs 1 credit).
        chunks_per_source: Max chunks per source, 1-5. Requires instructions.
        include_images: Include extracted images.
        include_favicon: Include favicon URLs.
        include_usage: Include credit usage.
        select_paths: Regex patterns for paths to include.
        select_domains: Regex patterns to restrict to specific domains/subdomains.
        exclude_paths: Regex patterns for paths to exclude.
        exclude_domains: Regex patterns for domains to exclude.
        allow_external: Whether to include external links in the final response.
        extract_depth: `basic` or `advanced` (tables/embedded content, higher success).
        format: `markdown` or `text`.
        timeout: Max seconds for the crawl, 10-150.
    """
    t0 = time.time()
    client, masked = _get_client()
    kwargs: dict[str, Any] = {
        "url": url,
        "max_depth": max_depth,
        "limit": limit,
        "include_images": include_images,
        "include_favicon": include_favicon,
        "include_usage": include_usage,
        "allow_external": allow_external,
        "extract_depth": extract_depth,
        "format": format,
    }
    if instructions:
        kwargs["instructions"] = instructions
        kwargs["chunks_per_source"] = chunks_per_source
    if max_breadth:
        kwargs["max_breadth"] = max_breadth
    if select_paths:
        kwargs["select_paths"] = select_paths
    if select_domains:
        kwargs["select_domains"] = select_domains
    if exclude_paths:
        kwargs["exclude_paths"] = exclude_paths
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    if timeout:
        kwargs["timeout"] = timeout

    try:
        resp = client.crawl(**kwargs)
        credits = 0
        if isinstance(resp, dict) and "usage" in resp:
            credits = resp["usage"].get("credits", 0) if isinstance(resp["usage"], dict) else 0
        _record(masked, "crawl", t0, True, credits)
        return json.dumps(resp, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.warning("crawl 失败 masked=%s: %s", masked, str(e)[:300])
        _record(masked, "crawl", t0, False, 0, str(e))
        return json.dumps({"error": str(e), "key_used": masked})


@mcp.tool()
def tavily_map(
    url: str,
    max_depth: int = 2,
    max_breadth: int = 20,
    limit: int = 100,
    instructions: str = "",
    select_paths: list[str] | None = None,
    select_domains: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    allow_external: bool = True,
    include_usage: bool = True,
    timeout: float = 150.0,
) -> str:
    """Discover and list URLs on a website. Faster than crawling.

    Maps a site's structure to find pages before extracting.

    Args:
        url: Starting URL to map.
        max_depth: Link depth to explore.
        max_breadth: Max links to follow per level of the tree (per page).
        limit: Maximum number of URLs to discover.
        instructions: Natural language instructions to filter pages.
            When provided, the cost becomes 2 credits per 10 pages (vs 1 credit).
        select_paths: Regex patterns for paths to include.
        select_domains: Regex patterns to restrict to specific domains/subdomains.
        exclude_paths: Regex patterns for paths to exclude.
        exclude_domains: Regex patterns for domains to exclude.
        allow_external: Whether to include external links in the final response.
        include_usage: Include credit usage.
        timeout: Max seconds, 10-150.
    """
    t0 = time.time()
    client, masked = _get_client()
    kwargs: dict[str, Any] = {
        "url": url,
        "max_depth": max_depth,
        "limit": limit,
        "include_usage": include_usage,
        "allow_external": allow_external,
    }
    if instructions:
        kwargs["instructions"] = instructions
    if max_breadth:
        kwargs["max_breadth"] = max_breadth
    if select_paths:
        kwargs["select_paths"] = select_paths
    if select_domains:
        kwargs["select_domains"] = select_domains
    if exclude_paths:
        kwargs["exclude_paths"] = exclude_paths
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    if timeout:
        kwargs["timeout"] = timeout

    try:
        resp = client.map(**kwargs)
        credits = 0
        if isinstance(resp, dict) and "usage" in resp:
            credits = resp["usage"].get("credits", 0) if isinstance(resp["usage"], dict) else 0
        _record(masked, "map", t0, True, credits)
        return json.dumps(resp, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.warning("map 失败 masked=%s: %s", masked, str(e)[:300])
        _record(masked, "map", t0, False, 0, str(e))
        return json.dumps({"error": str(e), "key_used": masked})


@mcp.tool()
def tavily_research(
    input: str,
    model: str = "auto",
    citation_format: str = "numbered",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    timeout: float = 300.0,
    poll_interval: float = 2.0,
) -> str:
    """AI-powered deep research producing a cited report. Takes 30-120 seconds.

    Submits a research task and polls until it completes, returning the cited
    synthesis report with sources.

    Args:
        input: Research question or topic.
        model: `auto`, `mini`, or `pro` — `pro` for more comprehensive analysis.
        citation_format: `numbered`, `mla`, `apa`, or `chicago`.
        include_domains: Soft preference for source domains (max 20).
        exclude_domains: Hard blocklist of domains to exclude (max 20).
        timeout: Max seconds to wait for the report (default 300).
        poll_interval: Seconds between status polls.
    """
    t0 = time.time()
    client, masked = _get_client()
    kwargs: dict[str, Any] = {}
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains

    try:
        # tavily-python >= 0.7 的 research 为异步任务式 API：
        # research(input=...) 提交任务返回 request_id，需用 get_research() 轮询结果。
        resp = client.research(
            input=input,
            model=model,
            citation_format=citation_format,
            **kwargs,
        )
        request_id = resp.get("request_id") if isinstance(resp, dict) else None
        if not request_id:
            raise RuntimeError(f"Unexpected research response: {resp}")

        deadline = time.time() + timeout
        last = resp
        while time.time() < deadline:
            time.sleep(poll_interval)
            last = client.get_research(request_id)
            status = last.get("status", "") if isinstance(last, dict) else ""
            if status in ("completed", "failed", "error", "cancelled"):
                break

        credits = 0
        if isinstance(last, dict) and "usage" in last:
            credits = last["usage"].get("credits", 0) if isinstance(last["usage"], dict) else 0
        _record(masked, "research", t0, True, credits)
        return json.dumps(last, ensure_ascii=False, indent=2)
    except Exception as e:
        err_str = str(e)
        # 官方要求流式（HTTP 400 research_stream_required）：自动回退 stream=true 组装报告
        if "research_stream_required" in err_str or ("stream" in err_str.lower() and "required" in err_str.lower()):
            _log.info("research 要求流式，自动回退 stream=true: %s", err_str[:200])
            try:
                last = _research_stream(client, input, model, citation_format, timeout, kwargs)
                _record(masked, "research", t0, True, 0)
                return json.dumps(last, ensure_ascii=False, indent=2)
            except Exception as e2:  # noqa: BLE001
                _log.warning("research 流式回退失败 masked=%s: %s", masked, str(e2)[:300])
                _record(masked, "research", t0, False, 0, str(e2))
                return json.dumps({"error": str(e2), "key_used": masked})
        _log.warning("research 失败 masked=%s: %s", masked, err_str[:300])
        _record(masked, "research", t0, False, 0, err_str)
        return json.dumps({"error": err_str, "key_used": masked})


@mcp.tool()
def tavily_pool_status() -> str:
    """Get API key pool status — active keys, usage stats, recent activity."""
    stats = pool.get_stats()
    return json.dumps(stats, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _est_credits(depth: str) -> int:
    return 2 if depth == "advanced" else 1


def _research_stream(client: TavilyClient, input: str, model: str, citation_format: str,
                     timeout: float, kwargs: dict) -> dict:
    """research(stream=True) 的 SSE 流式组装：解析 choices[0].delta.content 拼接报告。"""
    gen = client.research(
        input=input, model=model, citation_format=citation_format,
        stream=True, timeout=timeout, **kwargs,
    )
    # 真实 SDK 返回 bytes 生成器（逐块 yield）；若直接返回 bytes/str，
    # 迭代会退化为逐字节(int)/逐字符(str)，需包成单元素列表防御。
    if isinstance(gen, (bytes, str)):
        gen = [gen]
    parts: list[str] = []
    for chunk in gen:
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8", errors="replace")
        elif isinstance(chunk, str):
            text = chunk
        else:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
            delta = (obj.get("choices") or [{}])[0].get("delta") or {}
            c = delta.get("content")
            if isinstance(c, str):
                parts.append(c)
    return {"status": "completed", "content": "".join(parts)}


def _wrap_bearer_auth(inner_app: Any, token: str):
    """给 MCP 网络服务包一层 Bearer token 鉴权中间件。

    config.mcp_token 非空时，所有请求必须携带 `Authorization: Bearer <token>`
    头，否则返回 401。用 starlette 外层包裹 sse_app()/streamable_http_app()
    返回的 app（公开 API，不依赖 mcp 内部 OAuth 配置）。token 为空时不包装。
    """
    if not token:
        return inner_app
    import secrets
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount

    class _AuthMiddleware(BaseHTTPMiddleware):
        def __init__(self, app):
            super().__init__(app)
            self._expected = token

        async def dispatch(self, request, call_next):
            auth = request.headers.get("Authorization") or ""
            if not auth.lower().startswith("bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"error": "invalid_token", "description": "Authentication required"},
                )
            provided = auth[7:].strip()
            if not secrets.compare_digest(provided, self._expected):
                return JSONResponse(
                    status_code=401,
                    content={"error": "invalid_token", "description": "Invalid token"},
                )
            return await call_next(request)

    return Starlette(
        routes=[Mount("/", app=inner_app)],
        middleware=[Middleware(_AuthMiddleware)],
    )


def main():
    """按 config.json 启动 MCP 服务。

    - mcp_transport=stdio           : 标准输入输出模式，供 AI 客户端直接拉起本进程
    - mcp_transport=sse             : SSE 网络服务（默认），局域网内可通过 URL 访问
    - mcp_transport=streamable-http : Streamable HTTP 网络服务
    - mcp_token 非空时，网络模式要求请求携带 `Authorization: Bearer <token>`
    """
    from settings import get_settings
    from mcp.server.transport_security import TransportSecuritySettings

    cfg = get_settings()
    transport = (cfg.get("mcp_transport") or "sse").strip().lower()
    if transport == "stdio":
        # stdio 模式由 AI 客户端直接拉起本地进程，不参与网络鉴权
        mcp.run(transport="stdio")
        return
    # 网络模式：应用监听地址与端口（0.0.0.0 = 局域网可用）
    mcp.settings.host = cfg.get("mcp_host", "0.0.0.0")
    mcp.settings.port = int(cfg.get("mcp_port", 8001))
    # 新版 mcp 包在 FastMCP 创建时（默认 host=127.0.0.1）会自动启用
    # DNS rebinding 防护，allowed_hosts 仅含 localhost；此时即使把 host
    # 改为 0.0.0.0，局域网 IP 访问 /sse 仍会被拒（HTTP 421 Invalid Host
    # header）。这里显式关闭该防护，确保局域网内设备可直接连接。
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    # 可选鉴权：mcp_token 非空时要求 Bearer token（防止局域网内任意设备
    # 消费 Key 池额度）；通过公开的 sse_app()/streamable_http_app() 取 app，
    # 外层包中间件后用 uvicorn 启动（与 mcp.run 内部行为一致）。
    import uvicorn

    app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
    token = (cfg.get("mcp_token") or "").strip()
    if token:
        app = _wrap_bearer_auth(app, token)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port, log_level="info")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Tavily MCP Server — Web search, extract, crawl, map, research via MCP protocol.
API keys managed by KeyPool with configurable load-balancing strategy.
"""
from __future__ import annotations

import anyio
import inspect
import json
import re
import threading
import time
import uuid
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from tavily import BadRequestError, TavilyClient

from key_pool import KeyPool, _mask, _classify_error
from logging_setup import get_logger
from paths import runtime_dir
from settings import get_settings, get_settings_fresh

_log = get_logger("mcp_server")

mcp = FastMCP("Tavily Web Search", instructions="Use this server to search the web, extract content from URLs, crawl websites, map site structures, and conduct AI-powered research.")

pool = KeyPool()

# request_id → 提交 research 时使用的 masked key。Tavily research 任务按 key
# 隔离，tavily_research_status 若用轮询到的其他 key 查询会返回 404，因此
# 记录映射，status 查询时优先使用同一 key。
# 映射持久化到 data/research_keys.json：服务器重启后仍可查询未完成任务。
_research_keys: dict[str, str] = {}
_research_keys_lock = threading.Lock()
_RESEARCH_KEYS_PATH = runtime_dir() / "research_keys.json"


def _load_research_keys() -> None:
    """从磁盘加载 request_id→masked 映射（服务器重启后异步任务仍可查询）。

    utf-8-sig：兼容带 BOM 的 UTF-8（某些编辑器/工具会写 BOM，若按纯 utf-8
    解析 json 会抛异常导致整份映射静默回退为空，任务看板/status 将找不到 key）。
    """
    global _research_keys
    try:
        if _RESEARCH_KEYS_PATH.exists():
            data = json.loads(_RESEARCH_KEYS_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                _research_keys = {str(k): str(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        _research_keys = {}


def _save_research_key(request_id: str, masked: str) -> None:
    """持久化 request_id→masked 映射（上限 1000 条防无界增长）。"""
    global _research_keys
    with _research_keys_lock:
        _research_keys[request_id] = masked
        if len(_research_keys) > 1000:
            dropped = len(_research_keys) - 1000
            _research_keys = dict(list(_research_keys.items())[-1000:])
            _log.warning(
                "research_keys 映射超上限，裁剪最旧 %d 条（现 %d 条），"
                "超龄 research 任务 status 将回退轮询", dropped, len(_research_keys)
            )
        try:
            _RESEARCH_KEYS_PATH.write_text(
                json.dumps(_research_keys, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


_load_research_keys()


def _key_strategy() -> str:
    """负载均衡策略（config.json key_strategy）：round-robin | least-used。"""
    cfg = get_settings()
    s = (cfg.get("key_strategy") or "round-robin").strip().lower()
    return s if s in ("round-robin", "least-used") else "round-robin"


# tavily-python 的 get_research() 使用 requests.Session.get() 且未传 timeout，
# requests 默认无限等待。若该请求挂起且在 MCP 事件循环内执行，会永久阻塞
# 整个服务器（所有 MCP 调用 180s 超时）。给 session 注入默认超时兜底：显式
# 传 timeout 的调用不受影响（显式参数优先于 partial 的默认值）。
_REQUEST_TIMEOUT = 60.0
# 429 retry-after 低于此值则同 key 等待重试（避免白白消耗别的 key 限流预算）
_RETRY_AFTER_SAME_KEY_MAX = 5.0

# MCP 进程级会话 ID：进程启动时自动生成一次，经 SDK 原生 session_id 参数转发
# X-Session-Id 头（对齐官方 tavily-mcp 的 per-process session 行为）。同一进程内
# 所有 key/请求共用同一会话 ID，便于 Tavily 侧按会话聚合分析；不设配置项。
_SESSION_ID = uuid.uuid4().hex


def _apply_default_timeout(client: TavilyClient) -> None:
    """给 TavilyClient 的 requests.Session 注入默认超时，防止请求无限挂起。"""
    import functools

    session = getattr(client, "session", None)
    if session is not None and hasattr(session, "request"):
        session.request = functools.partial(session.request, timeout=_REQUEST_TIMEOUT)


def _patch_error_headers(client: TavilyClient) -> None:
    """让 SDK 429 异常携带 retry-after 头。

    tavily-python 的 _handle_error_response 只把 detail 字符串放进异常，
    不带 response/headers，无法读取官方 429 的 retry-after。这里 wrap 该方法，
    把 response 的 retry-after 附着到异常上。幂等：重复调用不重复包装。
    """
    orig = getattr(client, "_handle_error_response", None)
    if orig is None or getattr(orig, "_tavily_retry_after_patched", False):
        return

    def _patched(response):
        try:
            orig(response)
        except Exception as e:  # noqa: BLE001
            headers = getattr(response, "headers", None)
            ra = headers.get("retry-after") if headers is not None else None
            if ra is not None and not hasattr(e, "retry_after"):
                try:
                    e.retry_after = float(ra)
                except (TypeError, ValueError):
                    e.retry_after = None
            raise

    _patched._tavily_retry_after_patched = True
    client._handle_error_response = _patched


def _retry_after(e: Exception) -> float | None:
    """从异常读取 retry-after 头（由 _patch_error_headers 附着），非法值返回 None。"""
    ra = getattr(e, "retry_after", None)
    if ra is None:
        return None
    try:
        return float(ra)
    except (TypeError, ValueError):
        return None


def _client_for(raw_key: str) -> TavilyClient:
    """构造 TavilyClient：注入默认超时、429 retry-after 提取、会话归属头。

    mcp_human_id / mcp_project_id 配置（可选）通过 SDK 原生参数转发
    X-Human-Id / X-Project-ID 头；session_id 为进程启动时自动生成的会话 ID
    （对齐官方 tavily-mcp 的 per-process session），转发 X-Session-Id 头，
    便于 Tavily 侧按会话聚合分析。
    """
    cfg = get_settings_fresh()
    client = TavilyClient(
        raw_key,
        session_id=_SESSION_ID,
        human_id=(cfg.get("mcp_human_id") or None),
        project_id=(cfg.get("mcp_project_id") or None),
    )
    _apply_default_timeout(client)
    _patch_error_headers(client)
    return client


def _get_client(endpoint: str = "search") -> tuple[TavilyClient, str]:
    """取一个可用 key（未耗尽、未超限流，按 endpoint 独立限流桶）；全部受限时内部短暂等待。

    endpoint：'search'/'extract'/'crawl'/'map'/'research'。research「创建任务」走
    独立的 20 RPM 桶（官方按 key 独立）；research 状态轮询/看板查询走默认桶。
    """
    result = pool.next_available_key(endpoint)
    if result is None:
        raise RuntimeError("No active API keys in pool. Add keys via CLI or dashboard.")
    raw, masked = result
    return _client_for(raw), masked


def _record(masked: str, endpoint: str, start: float, success: bool,
            credits: int = 0, error_msg: str = "", request_id: str = "",
            usage_source: str = "", is_client_error: bool = False,
            source: str = "mcp", project_id: str | None = None):
    if project_id is None:
        # MCP 请求默认归属 mcp_project_id 配置值（代理等非 MCP 来源显式传空）
        project_id = (get_settings_fresh().get("mcp_project_id") or "")
    latency = (time.time() - start) * 1000
    pool.record_request(masked, endpoint, latency, success, credits, error_msg, request_id, usage_source,
                        1 if is_client_error else 0, source, project_id)


def _is_client_error(e: Exception) -> bool:
    """是否为客户端请求错误（HTTP 400/参数校验失败），与 Key/服务器健康无关。

    依据异常类型/响应状态码判定（比消息字符串更可靠）：SDK 对 HTTP 400 抛
    BadRequestError；401/403/429/432/433/5xx/超时/网络错误均视为服务器侧，计入错误率。
    """
    if isinstance(e, BadRequestError):
        return True
    resp = getattr(e, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 400:
        return True
    s = str(e).lower()
    return "400 bad request" in s or "http 400" in s or "bad request" in s


def _usage_credits(resp: Any) -> tuple[int, bool]:
    """从响应中提取消耗积分，返回 (credits, has_usage)。

    has_usage=False 表示响应不含 usage 字段——如 Research API（POST /research 与
    GET /research/{id} 官方响应均无 usage），这类消耗只能以官方 /usage 的
    research_usage 为准，本地不能当作「消耗 0」。
    """
    if isinstance(resp, dict) and isinstance(resp.get("usage"), dict):
        try:
            return int(resp["usage"].get("credits") or 0), True
        except (TypeError, ValueError):
            return 0, True
    return 0, False


def _usage_request_id(resp: Any) -> str:
    """从响应中提取 request_id（用于问题定位）。"""
    if isinstance(resp, dict):
        rid = resp.get("request_id") or ""
        return str(rid)
    return ""


def _norm_flag(v: Any) -> Any:
    """归一化 bool|str 参数：字符串 'true'/'false'（任意大小写）转为 bool。

    Tavily API 对 include_answer 等参数严格校验（只接受 True/False/'basic'/
    'advanced'），MCP 客户端常传字符串 'true'/'false' 导致 400；这里统一归一化。
    'basic'/'advanced'/'markdown'/'text' 等字符串原样保留。
    """
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        # 枚举字符串统一转小写：客户端可能传 'Basic'/'Advanced'/'Markdown'
        # 等大小写变体，原样透传给 API 会触发 400 校验错误。
        if s in ("basic", "advanced", "markdown", "text"):
            return s
    return v


_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def _norm_country(country: str) -> str:
    """校验 country 参数：必须是两位 ISO 3166-1 alpha-2 国家代码。

    返回规范化后的小写代码；非法值抛 ValueError 带友好中文提示，避免把
    模糊的 API 400 错误抛给客户端。
    """
    c = (country or "").strip()
    if not c:
        return ""
    if not _COUNTRY_RE.match(c):
        raise ValueError(
            f"country 参数无效: '{country}'。需为两位 ISO 3166-1 国家代码"
            "（如 'us'/'cn'/'jp'），且仅 topic=general 时有效。"
        )
    return c.lower()


def _tool_kwargs(func, kwargs: dict) -> dict:
    """应用 mcp_default_parameters：未显式传入的参数（值=声明默认）由默认参数覆盖。

    用热刷新读配置：面板修改默认参数后无需重启 MCP 即生效。
    """
    defaults = get_settings_fresh().get("mcp_default_parameters") or {}
    if not isinstance(defaults, dict) or not defaults:
        return kwargs
    sig_defaults = {
        k: v.default for k, v in inspect.signature(func).parameters.items()
        if v.default is not inspect.Parameter.empty
    }
    out = dict(defaults)
    for k, v in kwargs.items():
        if k in sig_defaults and v == sig_defaults[k]:
            continue  # 未显式传入：保留 defaults 的值
        out[k] = v
    return out


def _run_with_retry(endpoint: str, fn, on_success: Callable[[str, Any], None] | None = None,
                    source: str = "mcp", project_id: str | None = None) -> str:
    """执行工具调用并序列化返回。

    - 记录成功/失败（含 request_id）。
    - quota(额度耗尽)/auth(认证失效) 类错误自动切换其他 key 重试（最多 2 次）。
    - 429 带 retry-after 头：< 5s 同 key 等待后重试（不换 key，避免白白消耗别的
      key 限流预算）；>= 5s 切换其他 key 重试。
    - on_success：成功后回调 (masked, resp)（代理提交 research 时用于固定
      request_id→key 映射——research 任务按 key 隔离，状态查询须用同一 key）。
    - source：请求来源标记（mcp 默认 / proxy），写入 request_log 便于筛选。
    - project_id：项目归属；None 时读 mcp_project_id 配置（代理显式传空）。
    """
    if project_id is None:
        project_id = (get_settings_fresh().get("mcp_project_id") or "")
    last_err = ""
    for attempt in range(3):
        try:
            client, masked = _get_client(endpoint)
        except RuntimeError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        t0 = time.time()
        same_key_waits = 0
        while True:
            try:
                resp = fn(client)
                credits, has_usage = _usage_credits(resp)
                _record(masked, endpoint, t0, True, credits,
                        request_id=_usage_request_id(resp),
                        usage_source="response" if has_usage else "unknown",
                        source=source, project_id=project_id)
                if on_success is not None:
                    try:
                        on_success(masked, resp)
                    except Exception:  # noqa: BLE001
                        pass
                return json.dumps(resp, ensure_ascii=False, indent=2)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                _log.warning("%s 失败 masked=%s: %s", endpoint, masked, err[:300])
                _record(masked, endpoint, t0, False, 0, err, usage_source="none",
                        is_client_error=_is_client_error(e), source=source, project_id=project_id)
                cat = _classify_error(err)
                last_err = err
                ra = _retry_after(e)
                if cat == "rate" and ra is not None:
                    if ra < _RETRY_AFTER_SAME_KEY_MAX and same_key_waits < 2:
                        # 短等待：同 key 重试，不消耗别的 key 限流预算
                        time.sleep(ra)
                        same_key_waits += 1
                        continue
                    if attempt < 2:
                        break  # 长等待：切换其他 key 重试
                if cat in ("quota", "auth") and attempt < 2:
                    break  # 换 key 重试
                return json.dumps({"error": err, "key_used": masked}, ensure_ascii=False)
    return json.dumps({"error": last_err, "key_used": "?"}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
async def tavily_search(
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
    include_answer: bool | str = False,
    include_raw_content: bool | str = False,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    country: str = "",
    exact_match: bool = False,
    include_favicon: bool = False,
    auto_parameters: bool = False,
    safe_search: bool = False,
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
        chunks_per_source: Max content chunks per source (1-3). Only with advanced/basic/fast depth.
        include_images: Include images in results.
        include_image_descriptions: Include image descriptions with images.
        include_answer: Include LLM-generated answer. `basic`/`true` for quick, `advanced` for detailed.
        include_raw_content: Include cleaned content. `markdown`/`true` for markdown, `text` for plain text.
        include_domains: List of domains to restrict search to (max 300).
        exclude_domains: List of domains to exclude (max 150).
        country: Prioritize results from this country (only with topic=general).
        exact_match: Only return results with exact quoted phrases.
        include_favicon: Include favicon URLs.
        auto_parameters: Let Tavily auto-tune parameters based on query intent.
        safe_search: Enable safe search to filter explicit content.
        include_usage: Include credit usage in response (default True for tracking).
    """
    kwargs: dict[str, Any] = {
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "max_results": max_results,
        "chunks_per_source": chunks_per_source,
        "include_images": include_images,
        "include_image_descriptions": include_image_descriptions,
        "include_answer": _norm_flag(include_answer),
        "include_raw_content": _norm_flag(include_raw_content),
        "include_favicon": include_favicon,
        "exact_match": exact_match,
        "auto_parameters": auto_parameters,
        "safe_search": safe_search,
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
    kwargs = _tool_kwargs(tavily_search, kwargs)
    # country 前置校验：非法值（非两位 ISO 代码）直接返回友好错误，避免每次
    # 发请求到 API 才被 400 拒绝，也避免把模糊英文错误抛给客户端。
    if kwargs.get("country"):
        try:
            kwargs["country"] = _norm_country(kwargs["country"])
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _do(client: TavilyClient):
        return client.search(**kwargs)

    # 同步工具在 MCP 事件循环内执行会阻塞整个服务器：async 工具 + to_thread
    # 让耗时调用（Tavily API、重试）在线程池中运行，避免阻塞其他请求。
    return await anyio.to_thread.run_sync(_run_with_retry, "search", _do)


@mcp.tool()
async def tavily_extract(
    urls: list[str],
    extract_depth: str = "basic",
    format: str = "markdown",
    include_images: bool = False,
    include_favicon: bool = False,
    include_usage: bool = True,
    query: str = "",
    chunks_per_source: int = 3,
    timeout: float | None = None,
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
        timeout: Max seconds per URL, 1-60. Default 30s, matching the official API.
    """
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
    kwargs = _tool_kwargs(tavily_extract, kwargs)
    # 官方 extract 默认超时 30s（SDK timeout=30，不分 extract_depth）。
    # basic 若按 10s 常被慢页面触发「Request timed out after 10.0 seconds」
    # 超时失败，故统一按官方默认 30s（用户显式传参 / mcp_default_parameters 注入优先）
    if "timeout" not in kwargs:
        kwargs["timeout"] = 30.0

    def _do(client: TavilyClient):
        return client.extract(**kwargs)

    return await anyio.to_thread.run_sync(_run_with_retry, "extract", _do)


@mcp.tool()
async def tavily_crawl(
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
    timeout: float = 60.0,
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
        timeout: Max seconds for the crawl, 10-60 (keep under the MCP client's request timeout).
    """
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
    kwargs = _tool_kwargs(tavily_crawl, kwargs)

    def _do(client: TavilyClient):
        return client.crawl(**kwargs)

    return await anyio.to_thread.run_sync(_run_with_retry, "crawl", _do)


@mcp.tool()
async def tavily_map(
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
    timeout: float = 60.0,
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
        timeout: Max seconds, 10-60 (keep under the MCP client's request timeout).
    """
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
    kwargs = _tool_kwargs(tavily_map, kwargs)

    def _do(client: TavilyClient):
        return client.map(**kwargs)

    return await anyio.to_thread.run_sync(_run_with_retry, "map", _do)


_RESEARCH_OUTPUT_LENGTHS = ("short", "standard", "long")


def _research_impl(input: str, model: str, citation_format: str,
                   include_domains: list[str] | None, exclude_domains: list[str] | None,
                   wait: bool, timeout: float | None, poll_interval: float,
                   output_length: str = "standard",
                   output_schema: dict | None = None,
                   max_sources: int | None = None,
                   max_subsources: int | None = None) -> str:
    """tavily_research 的同步实现，在线程池中执行，避免阻塞 MCP 事件循环。"""
    t0 = time.time()
    kwargs: dict[str, Any] = {}
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains

    # ── 参数校验（客户端侧提前拦截，避免 400 才暴露给用户）──────
    ol = (output_length or "standard").strip().lower()
    if ol not in _RESEARCH_OUTPUT_LENGTHS:
        return json.dumps({"error": f"output_length 无效: '{output_length}'。需为 short/standard/long。"},
                          ensure_ascii=False)
    if output_schema is not None and not isinstance(output_schema, dict):
        return json.dumps({"error": "output_schema 必须是 JSON Schema 对象（dict）。"}, ensure_ascii=False)
    if max_sources is not None:
        try:
            if not (3 <= int(max_sources) <= 10):
                return json.dumps({"error": "max_sources 范围 3-10。"}, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"error": "max_sources 必须是整数（范围 3-10）。"}, ensure_ascii=False)
    if max_subsources is not None:
        try:
            if not (1 <= int(max_subsources) <= 8):
                return json.dumps({"error": "max_subsources 范围 1-8。"}, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"error": "max_subsources 必须是整数（范围 1-8）。"}, ensure_ascii=False)

    # 透传 research 高级参数。output_schema 单独显式传：SDK research() 有同名
    # 参数，不能放 kwargs 里（避免 'got multiple values for keyword argument'）。
    kwargs["output_length"] = ol
    if max_sources is not None:
        kwargs["max_sources"] = max_sources
    if max_subsources is not None:
        kwargs["max_subsources"] = max_subsources

    # 总时长默认按 model 对齐官方：mini/auto 300s、pro 900s。
    # 下限保护：research 官方生成报告通常需 1-5 分钟，客户端常显式传过小的
    # timeout（如 Cherry Studio 等默认 120s），会过早掐断流式/轮询导致频繁
    # 「超时失败」→ 显式 timeout 低于模型默认值时自动提升，并在返回 JSON 中
    # 注明（timeout_raised），客户端可感知实际生效值。
    default_timeout = _default_research_timeout(model)
    timeout_raised: float | None = None
    if timeout:
        timeout = max(float(timeout), 1.0)
        if timeout < default_timeout:
            timeout_raised = timeout
            timeout = default_timeout
    else:
        timeout = default_timeout

    def _dump(payload: dict) -> str:
        """序列化 research 结果：若发生 timeout 下限提升则附加说明字段。"""
        if timeout_raised is not None:
            payload = dict(payload)
            payload["timeout_raised"] = {
                "requested": round(timeout_raised, 1),
                "applied": default_timeout,
                "note": (f"客户端传入 timeout={timeout_raised:.0f}s 低于模型默认 "
                         f"{default_timeout:.0f}s，已自动提升避免 research 生成中被过早掐断"),
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # 取可用 key（池空时返回友好错误，避免异常冒泡导致会话不稳定）。
    # research「创建任务」走独立 20 RPM 限流桶（官方按 key 独立），提交与流式
    # 都用同一桶；状态轮询/看板查询走默认桶（官方仅限创建任务）。
    try:
        client, masked = _get_client("research")
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── wait=True：优先 SDK 原生 stream=True（官方 API 已强制流式）──
    # 直接消费生成器组装报告，避免「先 400 research_stream_required 再回退」。
    if wait:
        try:
            last = _research_stream(client, input, model, citation_format, timeout, kwargs, output_schema)
        except Exception as e:  # noqa: BLE001
            # 提交阶段失败（连接/头部超时/认证等）：回退「提交+轮询」
            _log.info("research 流式提交失败，回退提交+轮询 masked=%s: %s", masked, str(e)[:200])
        else:
            status = last.get("status")
            if status in ("timeout", "error"):
                # 流已开始但中断：返回部分结果，不回退（服务端任务可能仍在运行，
                # 回退重新提交会造成重复任务、双倍消耗）
                _record(masked, "research", t0, False, 0,
                        last.get("error") or status, usage_source="unknown")
                return _dump(last)
            _record(masked, "research", t0, True, _usage_credits(last)[0], usage_source="unknown")
            return _dump(last)

    # ── 提交任务（quota/auth 错误自动切换其他 key 重试）──────────
    request_id = None
    last_err = ""
    for attempt in range(3):
        try:
            # tavily-python >= 0.7 的 research 为异步任务式 API：
            # research(input=...) 提交任务返回 request_id，需用 get_research() 轮询结果。
            resp = client.research(
                input=input, model=model, citation_format=citation_format,
                output_schema=output_schema, **kwargs,
            )
            request_id = resp.get("request_id") if isinstance(resp, dict) else None
            if not request_id:
                raise RuntimeError(f"Unexpected research response: {resp}")
            _save_research_key(request_id, masked)
            break
        except Exception as e:  # noqa: BLE001
            err = str(e)
            cat = _classify_error(err)
            ra = _retry_after(e)
            if cat == "rate" and ra is not None and ra < _RETRY_AFTER_SAME_KEY_MAX:
                # 短限流等待：同 key 稍等后重试，避免白白消耗别的 key 限流预算
                _log.info("research 提交限流(retry-after=%.0fs)，同 key 等待重试: %s", ra, err[:200])
                time.sleep(ra)
                continue
            if cat in ("quota", "auth") and attempt < 2:
                _log.info("research 提交失败(%s)，切换 key 重试: %s", cat, err[:200])
                try:
                    client, masked = _get_client("research")
                except RuntimeError as e2:
                    return json.dumps({"error": str(e2)}, ensure_ascii=False)
                continue
            last_err = err
            # 官方要求流式（HTTP 400 research_stream_required）：自动回退 stream=true 组装报告
            if "research_stream_required" in err or ("stream" in err.lower() and "required" in err.lower()):
                _log.info("research 要求流式，自动回退 stream=true: %s", err[:200])
                try:
                    last = _research_stream(client, input, model, citation_format, timeout, kwargs, output_schema)
                except Exception as e2:  # noqa: BLE001
                    _log.warning("research 流式回退失败 masked=%s: %s", masked, str(e2)[:300])
                    _record(masked, "research", t0, False, 0, str(e2), usage_source="none",
                            is_client_error=_is_client_error(e2))
                    return json.dumps({"error": str(e2), "key_used": masked}, ensure_ascii=False)
                if last.get("status") in ("timeout", "error"):
                    _record(masked, "research", t0, False, 0,
                            last.get("error") or last.get("status"), usage_source="none")
                else:
                    _record(masked, "research", t0, True, _usage_credits(last)[0], usage_source="unknown")
                return _dump(last)
            _log.warning("research 提交失败 masked=%s: %s", masked, err[:300])
            _record(masked, "research", t0, False, 0, err, usage_source="none",
                    is_client_error=_is_client_error(e))
            return json.dumps({"error": err, "key_used": masked}, ensure_ascii=False)
    if request_id is None:
        return json.dumps({"error": last_err, "key_used": masked}, ensure_ascii=False)

    # ── wait=False：提交即返回，供客户端用 tavily_research_status 轮询 ──
    if not wait:
        _record(masked, "research", t0, True, 0, request_id=request_id, usage_source="unknown")
        return _dump({"request_id": request_id, "status": "submitted", "key_used": masked})

    # ── wait=True：轮询直到完成 ─────────────────────────────────
    try:
        deadline = time.time() + timeout
        last = resp
        while time.time() < deadline:
            time.sleep(poll_interval)
            last = client.get_research(request_id)
            status = last.get("status", "") if isinstance(last, dict) else ""
            if status in ("completed", "failed", "error", "cancelled"):
                break
        _record(masked, "research", t0, True, _usage_credits(last)[0], request_id=request_id, usage_source="unknown")
        return _dump(last)
    except Exception as e:  # noqa: BLE001
        _log.warning("research 轮询失败 masked=%s: %s", masked, str(e)[:300])
        _record(masked, "research", t0, False, 0, str(e), request_id=request_id, usage_source="none",
                is_client_error=_is_client_error(e))
        return json.dumps({"error": str(e), "request_id": request_id, "key_used": masked},
                          ensure_ascii=False)


@mcp.tool()
async def tavily_research(
    input: str,
    model: str = "auto",
    citation_format: str = "numbered",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    wait: bool = True,
    timeout: float | None = None,
    poll_interval: float = 2.0,
    output_length: str = "standard",
    output_schema: dict | None = None,
    max_sources: int | None = None,
    max_subsources: int | None = None,
) -> str:
    """AI-powered deep research producing a cited report. Takes 30-120 seconds.

    wait=True（默认）: 提交任务并轮询直到完成，返回带引用的合成报告。
    wait=False: 提交后立即返回 request_id，配合 tavily_research_status 轮询，
        避免长时间阻塞当前调用。

    Args:
        input: Research question or topic.
        model: `auto`, `mini`, or `pro` — `pro` for more comprehensive analysis.
        citation_format: `numbered`, `mla`, `apa`, or `chicago`.
        include_domains: Soft preference for source domains (max 20).
        exclude_domains: Hard blocklist of domains to exclude (max 20).
        wait: If True, poll until the task completes; if False, return request_id immediately.
        timeout: Max seconds to wait for the report. Default follows model (mini/auto 300,
            pro 900). Values below the model default are raised to it automatically
            (research generation typically takes 1-5 min, so client-side 120s defaults
            would cut it off prematurely); the effective value is reported back in the
            `timeout_raised` field of the response.
        poll_interval: Seconds between status polls.
        output_length: Report length — `short`, `standard`, or `long`.
        output_schema: JSON Schema dict for structured output — research returns a
            structured object instead of markdown (ideal for agent consumption).
        max_sources: Max sources to use (3-10).
        max_subsources: Max sub-sources per source (1-8).
    """
    return await anyio.to_thread.run_sync(
        _research_impl, input, model, citation_format,
        include_domains, exclude_domains, wait, timeout, poll_interval,
        output_length, output_schema, max_sources, max_subsources,
    )


@mcp.tool()
async def tavily_research_status(request_id: str) -> str:
    """Check the status/result of a previously submitted research task.

    Args:
        request_id: The request ID returned by tavily_research (wait=false).
    """

    def _impl() -> str:
        t0 = time.time()
        masked = ""
        try:
            # research 任务按 key 隔离：优先用提交时的同一 key 查询，避免 404
            pinned = _research_keys.get(request_id) or ""
            resp, masked = _query_research_status(request_id, pinned)
            _record(masked, "research-status", t0, True, _usage_credits(resp)[0],
                    request_id=request_id, usage_source="unknown")
            return json.dumps(resp, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            _log.warning("research-status 失败 masked=%s: %s", masked, str(e)[:300])
            _record(masked, "research-status", t0, False, 0, str(e), request_id=request_id, usage_source="none",
                    is_client_error=_is_client_error(e))
            return json.dumps({"error": str(e), "request_id": request_id}, ensure_ascii=False)

    return await anyio.to_thread.run_sync(_impl)


# ── Research 任务看板 ─────────────────────────────────────────
# request_id → (查询时间, 状态摘要 dict, 持久化 TTL 或 None) 的 TTL 缓存。
# 前两项为内存缓存：避免每次刷新都对每个任务调 get_research 烧官方限流预算。
# 第三项：落盘加载的终态任务携带持久化 TTL（重启后无需重新调官方 API）。
_research_task_cache: dict[str, tuple[float, dict, float | None]] = {}
_RESEARCH_TASK_TTL = 30.0        # 非终态任务缓存（秒）
_RESEARCH_TASK_DONE_TTL = 300.0  # 终态任务缓存（秒）
_RESEARCH_TASK_QUERY_CAP = 15   # 单次刷新最多实际查询多少个任务
_RESEARCH_TASK_QUERY_TIMEOUT = 10.0  # 单次看板状态查询超时（秒）：看板只需状态，避免慢请求拖到全局 60s
# 终态任务摘要落盘（data/research_tasks_cache.json）：跨重启恢复，避免重启后
# 重新调官方 API 拉取已完成任务状态。仅终态任务落盘（进行中任务会过期，不固化）；
# 7 天有效期（月度重置前任务状态基本可信，超过则重新查询确认）。
_RESEARCH_TASK_PERSIST_TTL = 7 * 86400.0
_RESEARCH_TASKS_CACHE_PATH = runtime_dir() / "research_tasks_cache.json"
_TASKS_CACHE_SAVE_INTERVAL = 5.0   # 落盘节流（秒）：避免每次刷新都写盘
_last_tasks_cache_save = 0.0

_TERMINAL_STATUSES = ("completed", "failed", "error", "cancelled")
# 看板只返回 content 摘要（研究报告全文可达数百 KB，全量传输会导致页面长时间加载）
_RESEARCH_TASK_CONTENT_PREVIEW = 200


def _research_content_preview(resp: dict, n: int = _RESEARCH_TASK_CONTENT_PREVIEW) -> dict:
    """把任务响应中的 content 截断为摘要（str 截断 / 对象序列化截断）。"""
    out = dict(resp)
    c = out.get("content")
    if isinstance(c, str):
        out["content"] = c[:n]
    elif isinstance(c, (dict, list)):
        out["content"] = json.dumps(c, ensure_ascii=False)[:n]
    return out


def _load_research_tasks_cache() -> None:
    """加载落盘的终态任务摘要到内存缓存（跨重启，避免重新调官方 API）。

    只加载终态且未超过 _RESEARCH_TASK_PERSIST_TTL 的任务；进行中任务不落盘、
    不加载（其状态会过期）。文件损坏/格式异常时安全回退为空缓存。
    """
    global _research_task_cache
    try:
        if not _RESEARCH_TASKS_CACHE_PATH.exists():
            return
        data = json.loads(_RESEARCH_TASKS_CACHE_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return
        now = time.time()
        loaded = 0
        for rid, item in data.items():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status not in _TERMINAL_STATUSES:
                continue
            ts = float(item.get("ts") or 0)
            if now - ts >= _RESEARCH_TASK_PERSIST_TTL:
                continue
            preview: dict = {"status": status, "content": item.get("content")}
            if item.get("error"):
                preview["error"] = item["error"]
            _research_task_cache[str(rid)] = (ts, preview, _RESEARCH_TASK_PERSIST_TTL)
            loaded += 1
        if loaded:
            _log.info("research 看板缓存落盘加载 %d 个终态任务", loaded)
    except Exception:  # noqa: BLE001
        _log.warning("research 看板缓存落盘加载失败，回退空缓存")
        _research_task_cache = {}


def _save_research_tasks_cache() -> None:
    """把内存中的终态任务摘要原子落盘（先写临时文件再 rename）。

    仅持久化终态且未过期任务（进行中任务状态会变化，不固化）。
    """
    now = time.time()
    persist: dict[str, dict] = {}
    with _research_keys_lock:
        for rid, (ts, preview, _pttl) in _research_task_cache.items():
            status = preview.get("status", "")
            if status not in _TERMINAL_STATUSES:
                continue
            if now - ts >= _RESEARCH_TASK_PERSIST_TTL:
                continue
            item: dict = {"status": status, "content": preview.get("content"), "ts": ts}
            if preview.get("error"):
                item["error"] = preview["error"]
            persist[str(rid)] = item
    if not persist:
        return
    try:
        _RESEARCH_TASKS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _RESEARCH_TASKS_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(persist, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_RESEARCH_TASKS_CACHE_PATH)
    except Exception as e:  # noqa: BLE001
        _log.warning("research 看板缓存落盘失败: %s", str(e)[:200])


def _maybe_save_research_tasks_cache() -> None:
    """节流版落盘：距上次写盘小于 _TASKS_CACHE_SAVE_INTERVAL 则跳过。"""
    global _last_tasks_cache_save
    now = time.time()
    if now - _last_tasks_cache_save < _TASKS_CACHE_SAVE_INTERVAL:
        return
    _last_tasks_cache_save = now
    _save_research_tasks_cache()


# 模块加载时恢复落盘的终态任务（须在常量定义之后调用）
_load_research_tasks_cache()


def _query_research_status(request_id: str, pinned_masked: str = "",
                           timeout: float | None = None) -> tuple[dict, str]:
    """查询 research 任务状态。任务按 key 隔离：优先用提交时的 key，避免 404。

    返回 (响应 dict, 实际使用的 masked key)。池空等场景抛异常由调用方处理。
    timeout：单次查询超时（秒）。SDK 的 get_research 不支持 timeout 参数，这里
    通过临时覆盖 session 默认超时实现；为 None 时沿用 _REQUEST_TIMEOUT。
    """
    client = None
    masked = ""
    if pinned_masked:
        k = pool.get_key(pinned_masked)
        if k is not None and k.is_active and not k.is_exhausted:
            client = _client_for(k.key)
            masked = pinned_masked
    if client is None:
        client, masked = _get_client()
    if timeout is None:
        return client.get_research(request_id), masked
    return _get_research_with_timeout(client, request_id, timeout), masked


def _get_research_with_timeout(client: TavilyClient, request_id: str, timeout: float) -> dict:
    """带显式超时调用 get_research：临时替换 session.request 的默认超时并恢复。

    get_research 不支持 timeout 参数（内部走 session.get），此处临时覆盖
    session.request 的默认超时，保证看板查询不会无限期阻塞。
    """
    import functools

    session = getattr(client, "session", None)
    orig = getattr(session, "request", None) if session is not None else None
    if session is not None and orig is not None:
        try:
            session.request = functools.partial(orig, timeout=max(float(timeout), 1.0))
            return client.get_research(request_id)
        finally:
            session.request = orig
    return client.get_research(request_id)


def list_research_tasks(limit: int = 50) -> list[dict]:
    """列出最近提交的 research 任务及状态（供面板「Research 任务」看板）。

    成本控制：查询结果按 TTL 缓存（非终态 30s / 终态 300s），单次刷新最多查
    _RESEARCH_TASK_QUERY_CAP 个任务，避免逐个调 get_research 烧官方限流预算。
    性能：需要实际查询的任务用线程池并发执行（每个限时
    _RESEARCH_TASK_QUERY_TIMEOUT 秒），避免串行逐个阻塞导致看板长时间加载。
    持久化：查询到的终态任务摘要落盘（_save_research_tasks_cache），重启后
    由 _load_research_tasks_cache 直接恢复展示，避免重新调官方 API。
    """
    with _research_keys_lock:
        items = list(_research_keys.items())
    items = items[-max(1, int(limit)):]
    now = time.time()
    tasks: list[dict] = []
    pending: list[tuple[str, str]] = []
    for request_id, masked in reversed(items):
        cached = _research_task_cache.get(request_id)
        if cached is not None:
            ts = cached[0]
            preview = cached[1]
            persist_ttl = cached[2] if len(cached) > 2 else None
            status = preview.get("status", "")
            ttl = _RESEARCH_TASK_DONE_TTL if status in _TERMINAL_STATUSES else _RESEARCH_TASK_TTL
            if persist_ttl is not None:
                ttl = persist_ttl  # 落盘恢复的终态任务：在持久化有效期内始终命中
            if now - ts < ttl:
                tasks.append({**preview, "request_id": request_id, "masked": masked, "cached": True})
                continue
        if len(pending) >= _RESEARCH_TASK_QUERY_CAP:
            tasks.append({"request_id": request_id, "masked": masked, "status": "unknown", "cached": True})
            continue
        pending.append((request_id, masked))
    if not pending:
        return tasks

    from concurrent.futures import ThreadPoolExecutor

    def _query_one(item: tuple[str, str]) -> tuple[dict, bool]:
        rid, rmasked = item
        try:
            resp, used = _query_research_status(rid, rmasked, timeout=_RESEARCH_TASK_QUERY_TIMEOUT)
            preview = _research_content_preview(resp)
            status = resp.get("status", "")
            _research_task_cache[rid] = (time.time(), preview, None)
            return ({
                "request_id": rid,
                "masked": rmasked,
                "status": status,
                "content": preview.get("content"),
                "key_used": used,
                "cached": False,
            }, status in _TERMINAL_STATUSES)
        except Exception as e:  # noqa: BLE001
            _log.warning("research 看板查询失败 %s: %s", rid, str(e)[:200])
            # 查询失败不是任务真实终态：不落盘，重启后重新查询
            return ({"request_id": rid, "masked": rmasked, "status": "error",
                     "error": str(e)[:200], "cached": False}, False)

    with ThreadPoolExecutor(max_workers=min(_RESEARCH_TASK_QUERY_CAP, len(pending))) as ex:
        results = list(ex.map(_query_one, pending))
    need_save = False
    for task, is_terminal in results:
        tasks.append(task)
        if is_terminal:
            need_save = True
    if need_save:
        _maybe_save_research_tasks_cache()
    return tasks


@mcp.tool()
async def tavily_pool_status() -> str:
    """Get API key pool status — active keys, usage stats, anomalies, aggregate capacity."""

    def _impl() -> str:
        try:
            stats = pool.get_stats()
            stats["aggregate"] = pool.get_aggregate()
            stats["anomalies"] = pool.detect_anomalies()
            return json.dumps(stats, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            _log.warning("pool_status 失败: %s", str(e)[:300])
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return await anyio.to_thread.run_sync(_impl)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


_STREAM_HEADER_TIMEOUT = 30.0   # 头部/连接超时（requests timeout=(connect, read) 的 connect 段）
_STREAM_IDLE_TIMEOUT = 300.0    # 单 chunk 读超时（read 段）：容忍官方报告生成阶段静默期


def _default_research_timeout(model: str) -> float:
    """按 model 对齐官方默认总时长：mini/auto 300s、pro 900s。"""
    return 900.0 if (model or "").strip().lower() == "pro" else 300.0


def _research_stream(client: TavilyClient, input: str, model: str, citation_format: str,
                     timeout: float, kwargs: dict, output_schema: dict | None = None) -> dict:
    """research(stream=True) 的 SSE 流式组装：解析 choices[0].delta.content 拼接报告。

    三段式超时（对齐官方 tavily-mcp）：
    - 头部超时：30s 内未建立连接视为失败（requests timeout 的 connect 段）；
    - idle 容忍：单 chunk 读超时放宽到 300s（read 段），容忍官方报告生成阶段的静默期；
    - 整体 deadline：超过总时长（timeout，默认 mini/auto 300 / pro 900）停止并返回部分内容。

    流已开始后的中断（整体超时/读异常）返回 {"status":"timeout"|"error", ...} 部分结果，
    不回退到「提交+轮询」（服务端任务可能仍在运行，回退会重复提交双倍消耗）；
    只有提交阶段（client.research 发送请求/读响应头）异常才向上抛，由调用方决定回退。

    output_schema 结构化输出：delta.content 可能直接是对象（dict/list），收集后作为
    content 返回；若 API 以 JSON 分片字符串下发则尝试整体解析。
    """
    deadline = time.time() + max(float(timeout), 1.0)
    idle = min(max(float(timeout), 1.0), _STREAM_IDLE_TIMEOUT)
    gen = client.research(
        input=input, model=model, citation_format=citation_format,
        stream=True, timeout=(_STREAM_HEADER_TIMEOUT, idle),
        output_schema=output_schema, **kwargs,
    )
    # 真实 SDK 返回 bytes 生成器（逐块 yield）；若直接返回 bytes/str，
    # 迭代会退化为逐字节(int)/逐字符(str)，需包成单元素列表防御。
    if isinstance(gen, (bytes, str)):
        gen = [gen]
    parts: list[str] = []
    structured: list[Any] = []
    timed_out = False
    try:
        for chunk in gen:
            if time.time() > deadline:
                timed_out = True
                break
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
                elif isinstance(c, (dict, list)):
                    structured.append(c)  # output_schema 结构化输出：直接收集对象
    except Exception as e:  # noqa: BLE001
        # 流读取阶段失败：返回部分内容，不回退（避免重复提交双倍消耗）
        _log.warning("research 流读取中断: %s", str(e)[:200])
        return {"status": "error", "content": "".join(parts), "error": str(e)}
    finally:
        close = getattr(gen, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    if timed_out:
        content = _structured_content(parts, structured, output_schema)
        return {"status": "timeout", "content": content,
                "error": (f"research stream exceeded {timeout:.0f}s deadline —— "
                          "Tavily 官方 research 生成报告通常需 1-5 分钟（mini 5min / pro 15min），"
                          "超过总时长 deadline 会被中断并只返回部分内容。建议调大 timeout "
                          "（默认 mini/auto 300s / pro 900s），或改用 wait=false + "
                          "tavily_research_status 异步查询，避免长时间阻塞当前调用。")}
    content = _structured_content(parts, structured, output_schema)
    return {"status": "completed", "content": content}


def _structured_content(parts: list[str], structured: list[Any], output_schema: dict | None) -> Any:
    """组装流式 content：结构化输出优先返回对象；字符串片段整体尝试 JSON 解析。"""
    if output_schema is None:
        return "".join(parts)
    if structured:
        return structured[-1]
    joined = "".join(parts)
    try:
        return json.loads(joined)
    except Exception:  # noqa: BLE001
        return joined


def _wrap_bearer_auth(inner_app: Any, token: str | None = None):
    """给 MCP 网络服务包一层 Bearer token 鉴权（纯 ASGI 中间件）。

    config.mcp_token 非空时，所有请求必须携带 `Authorization: Bearer <token>`
    头，否则返回 401。

    必须用纯 ASGI 包装（不新建 Starlette 应用、不干预 lifespan scope）：
    mcp 的 streamable_http_app() 在 Starlette lifespan 里调用 session manager
    的 run() 初始化 task_group；若用外层 Starlette + Mount 包裹，内层 app 的
    lifespan 不会被触发（Mount 不代理子 app 的 lifespan），streamable-http 下
    每个请求都会 500 "Task group is not initialized. Make sure to use run()."
    sse 模式每次连接自行 run()、不依赖 lifespan，故此前未暴露该问题。

    token=None（main 的调用方式）：每次请求按 TTL 热读最新配置——面板设置/
    修改/清空 mcp_token 后无需重启 MCP 即生效，token 为空时放行。
    显式传 token（测试/兼容）：固定用该值鉴权，不读配置。
    """
    import secrets

    if token is None:
        def _expected() -> str:
            return (get_settings_fresh().get("mcp_token") or "").strip()
    else:
        def _expected() -> str:
            return token

    class _AuthASGI:
        """纯 ASGI 包装：非 http scope（lifespan 等）原样透传，http 校验 Bearer。"""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            # lifespan / websocket 等 scope 直接透传，保证内层 app 的 lifespan
            # （streamable-http 的 task_group 初始化）正常触发
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            expected = _expected()
            if not expected:
                await self.app(scope, receive, send)
                return
            auth = ""
            for k, v in scope.get("headers", []):
                if k.lower() == b"authorization":
                    auth = v.decode("latin-1", "replace")
                    break
            provided = ""
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
            if not secrets.compare_digest(provided, expected):
                body = json.dumps(
                    {"error": "invalid_token", "description": "Authentication required"},
                    ensure_ascii=False,
                ).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("latin-1")),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            await self.app(scope, receive, send)

    return _AuthASGI(inner_app)


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
    # 始终包一层鉴权中间件：token 热刷新（mcp_token 空则放行），面板设置/修改
    # 令牌后无需重启 MCP 即生效；transport/host/port 变更仍需重启（无法热绑定）。
    app = _wrap_bearer_auth(app)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port, log_level="info")


if __name__ == "__main__":
    main()

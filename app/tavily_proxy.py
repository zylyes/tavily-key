#!/usr/bin/env python3
"""
Tavily 兼容搜索代理 — 把 Key 池暴露为 Tavily 官方 REST API 形态。

供 Cherry Studio 等 AI 客户端通过「自定义 API 地址」直接对接：客户端把本服务的
地址填入「API 地址」、proxy_token 填入「API 密钥」，客户端发来的 /search、
/extract、/crawl、/map 请求由本代理转发到 Tavily 官方 API，内部走 Key
池轮询/限流/异常切换（复用 mcp_server._run_with_retry），额度与日志自动落账。

- 鉴权：Authorization: Bearer <proxy_token>（或 body api_key 字段）；proxy_token
  为空时不鉴权（开放，面板会提示风险）。
- 独立子进程角色：python dashboard.py --proxy（打包后 Tavily.exe --proxy），
  由 proxy_manager 管理启停，监听 proxy_host:proxy_port（默认 8002）。
- 响应/错误与 Tavily 官方一致：成功原样透传官方 JSON；错误为
  {"detail": {"error": "..."}}，状态码按错误类别映射（auth→401、quota→432、
  rate→429、池空→503、其他→500）。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import anyio
import mcp_server
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from key_pool import KeyPool, _classify_error
from logging_setup import get_logger
from mcp_server import _client_for, _get_client, _norm_flag, _record, _run_with_retry, _usage_credits
from settings import get_settings

_log = get_logger("tavily_proxy")

pool = KeyPool()

# ── 配置热刷新 ────────────────────────────────────────────────
# settings.get_settings() 是进程内缓存：代理作为独立子进程，启动后不会感知
# config.json 的变更（如面板后来设置/生成的 proxy_token、限流参数等），导致
# 密钥配置不生效。鉴权前按 TTL 刷新缓存，让配置修改在数秒内生效、无需重启。
_REFRESH_TTL = 1.0  # 秒：TTL 内复用缓存，避免每个请求都读盘


def _fresh_settings() -> dict:
    """返回最新配置（TTL 内复用；config.json 变化时 reload 刷新进程内缓存）。"""
    from settings import get_settings_fresh

    return get_settings_fresh(ttl=_REFRESH_TTL)

proxy_app = FastAPI(
    title="Tavily Compatible Search Proxy",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# 桌面客户端通常无跨域问题，但允许 CORS 可兼容基于浏览器的调用方
proxy_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 需要鉴权的路径（与 Tavily 官方端点一致）
_AUTH_PATHS = {"/search", "/extract", "/crawl", "/map", "/research"}

# 各类端点允许转发的字段白名单（其余字段忽略，避免把非法参数透传给官方 400）
_SEARCH_FIELDS = (
    "query", "search_depth", "topic", "time_range", "start_date", "end_date",
    "max_results", "chunks_per_source", "include_images", "include_image_descriptions",
    "include_answer", "include_raw_content", "include_domains", "exclude_domains",
    "country", "exact_match", "include_favicon", "auto_parameters", "safe_search",
)
_EXTRACT_FIELDS = (
    "urls", "query", "chunks_per_source", "extract_depth",
    "include_images", "include_favicon", "format", "timeout",
)
_CRAWL_FIELDS = ("url", "max_depth", "max_pages", "include_images", "include_raw_content")
_MAP_FIELDS = ("url", "search_depth", "max_depth", "max_urls", "include_subdomains")
# Research 提交参数白名单（代理只做「提交 + 轮询」，不做 SSE 流式透传）。
# input 必填参数单独处理（显式传参），不进白名单避免重复。
# files：官方 base64 附件（≤5 文件、80k 词，.txt/.md/.json），REST JSON body
# 传 {"files": [{"name", "data", "type": "base64"}]} 合理，代理透传；MCP 侧
# 传文件体验差，tavily_research 工具不加该参数。
_RESEARCH_FIELDS = (
    "model", "citation_format", "include_domains", "exclude_domains",
    "output_length", "output_schema", "max_sources", "max_subsources",
    "files",
)


def _error(status: int, msg: str) -> JSONResponse:
    """Tavily 风格错误响应：{"detail": {"error": "..."}}。"""
    return JSONResponse(status_code=status, content={"detail": {"error": msg}})


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
    return default


# ── 鉴权 ───────────────────────────────────────────────────────
@proxy_app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """校验代理密钥：Authorization: Bearer <proxy_token> 或 body api_key 字段。

    proxy_token 为空时不鉴权（开放）。用 _fresh_settings() 读取最新配置：
    面板设置/生成密钥后无需重启代理，TTL 内自动感知。request.body() 在
    Starlette 中有缓存，中间件读取后 endpoint 仍可正常 await request.json()。
    """
    token = (_fresh_settings().get("proxy_token") or "").strip()
    if not token or request.url.path not in _AUTH_PATHS:
        return await call_next(request)

    import secrets

    auth = request.headers.get("Authorization") or ""
    provided = ""
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    else:
        # 部分客户端把密钥放在 JSON body 的 api_key 字段（官方也支持）
        try:
            body = await request.json()
            if isinstance(body, dict):
                provided = str(body.get("api_key") or "").strip()
        except Exception:  # noqa: BLE001
            provided = ""
    if provided and secrets.compare_digest(provided, token):
        return await call_next(request)
    return _error(401, "Unauthorized: missing or invalid API key.")


# ── 核心转发 ───────────────────────────────────────────────────
def _call(endpoint: str, fn: Callable,
          on_success: Callable[[str, Any], None] | None = None) -> JSONResponse:
    """执行池转发（含 Key 轮询/限流/异常切换）并映射为 Tavily 风格响应。

    _run_with_retry 成功返回官方响应 JSON 串；失败返回 {"error": ...} JSON 串，
    这里按错误类别映射 HTTP 状态码（客户端把非 2xx 视为失败）。
    on_success 透传给 _run_with_retry（research 提交后固定 request_id→key）；
    source 固定为 proxy，请求日志可区分 MCP / 代理来源。
    """
    try:
        # project_id 显式空：代理请求不归属 MCP 项目的 mcp_project_id 配置
        raw = _run_with_retry(endpoint, fn, on_success, source="proxy", project_id="")
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        _log.warning("proxy %s 转发异常: %s", endpoint, str(e)[:200])
        return _error(500, "Internal Server Error")
    if isinstance(data, dict) and data.get("error"):
        msg = str(data.get("error"))
        if "No active API keys" in msg:
            return _error(503, msg)
        status = {"auth": 401, "quota": 432, "rate": 429, "bad_request": 400}.get(_classify_error(msg), 500)
        resp = _error(status, msg)
        if status == 429:
            # 透传 Retry-After：让 REST 客户端（Cherry Studio 等）正确退避，
            # 避免限流时反复撞墙。值来自 _run_with_retry 附着在错误 JSON 的
            # retry_after 字段（源自官方 429 头），缺省 1 秒兜底。
            ra = data.get("retry_after")
            resp.headers["Retry-After"] = str(ra if ra is not None else 1)
        return resp
    return JSONResponse(data)


async def _read_body(request: Request) -> dict | None:
    """读取 JSON body；非法 JSON 或非对象返回 None。"""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _pick(body: dict, fields: tuple) -> dict:
    """按白名单挑选字段；int/bool/枚举字符串做类型归一化。"""
    out: dict = {}
    for k in fields:
        if k not in body or body[k] is None:
            continue
        v = body[k]
        if k in ("max_results", "chunks_per_source", "max_depth", "max_pages",
                 "max_urls", "timeout", "max_sources", "max_subsources"):
            out[k] = _as_int(v, 3 if k == "chunks_per_source" else 5)
        elif k in ("include_images", "include_image_descriptions", "include_favicon",
                   "exact_match", "auto_parameters", "safe_search",
                   "include_subdomains"):
            out[k] = _as_bool(v)
        elif k in ("include_answer", "include_raw_content"):
            out[k] = _norm_flag(v)
        elif k in ("include_domains", "exclude_domains", "urls"):
            if isinstance(v, list):
                out[k] = [str(x) for x in v]
            else:
                out[k] = v
        else:
            out[k] = v
    return out


# ═══════════════════════════════════════════════════════════════
# Tavily 兼容端点
# ═══════════════════════════════════════════════════════════════

@proxy_app.post("/search")
async def proxy_search(request: Request):
    """Tavily Search：转发官方 /search，强制 include_usage=True 保证本地积分落账。"""
    body = await _read_body(request)
    if body is None:
        return _error(400, "Request body must be a valid JSON object.")
    query = body.get("query")
    if query is None or not str(query).strip():
        return _error(400, "Missing required parameter: query.")
    kwargs = _pick(body, _SEARCH_FIELDS)
    kwargs["include_usage"] = True

    def _do(client):
        return client.search(**kwargs)

    return await anyio.to_thread.run_sync(_call, "search", _do)


@proxy_app.post("/extract")
async def proxy_extract(request: Request):
    """Tavily Extract：转发官方 /extract（urls 必填）。"""
    body = await _read_body(request)
    if body is None:
        return _error(400, "Request body must be a valid JSON object.")
    urls = body.get("urls")
    if not urls:
        return _error(400, "Missing required parameter: urls.")
    kwargs = _pick(body, _EXTRACT_FIELDS)
    kwargs["include_usage"] = True

    def _do(client):
        return client.extract(**kwargs)

    return await anyio.to_thread.run_sync(_call, "extract", _do)


@proxy_app.post("/crawl")
async def proxy_crawl(request: Request):
    """Tavily Crawl：转发官方 /crawl（url 必填）。"""
    body = await _read_body(request)
    if body is None:
        return _error(400, "Request body must be a valid JSON object.")
    url = body.get("url")
    if url is None or not str(url).strip():
        return _error(400, "Missing required parameter: url.")
    kwargs = _pick(body, _CRAWL_FIELDS)
    kwargs["include_usage"] = True

    def _do(client):
        return client.crawl(**kwargs)

    return await anyio.to_thread.run_sync(_call, "crawl", _do)


@proxy_app.post("/map")
async def proxy_map(request: Request):
    """Tavily Map：转发官方 /map（url 必填）。"""
    body = await _read_body(request)
    if body is None:
        return _error(400, "Request body must be a valid JSON object.")
    url = body.get("url")
    if url is None or not str(url).strip():
        return _error(400, "Missing required parameter: url.")
    kwargs = _pick(body, _MAP_FIELDS)
    kwargs["include_usage"] = True

    def _do(client):
        return client.map(**kwargs)

    return await anyio.to_thread.run_sync(_call, "map", _do)


@proxy_app.post("/research")
async def proxy_research(request: Request):
    """Tavily Research：提交任务，返回 request_id（客户端用 GET /research/{id} 轮询）。

    与官方 POST /research 一致返回异步任务（request_id），不做 SSE 流式透传。
    支持官方 files 附件参数（base64，≤5 文件），透传给 API。
    提交成功后把 request_id→masked 映射写入 research_keys.json（跨进程共享），
    供 GET /research/{id} 用同一 key 查询（任务按 key 隔离，用其他 key 查 404）。
    """
    body = await _read_body(request)
    if body is None:
        return _error(400, "Request body must be a valid JSON object.")
    inp = body.get("input")
    if inp is None or not str(inp).strip():
        return _error(400, "Missing required parameter: input.")
    kwargs = _pick(body, _RESEARCH_FIELDS)

    def _do(client):
        return client.research(input=inp, **kwargs)

    def _pin(masked, resp):
        rid = resp.get("request_id") if isinstance(resp, dict) else None
        if rid:
            mcp_server._save_research_key(str(rid), masked)

    return await anyio.to_thread.run_sync(_call, "research", _do, _pin)


_research_keys_refresh_ts = 0.0
_RESEARCH_KEYS_REFRESH_TTL = 2.0  # 秒：跨进程共享 research_keys.json 的读取节流


def _refresh_research_keys() -> None:
    """从磁盘重新加载 research 任务→key 映射（TTL 节流）。

    代理与 MCP 是独立进程，提交侧（本进程或其他进程）写入 research_keys.json，
    状态查询前刷新内存映射；TTL 内复用避免每次请求都读盘。
    """
    global _research_keys_refresh_ts
    now = time.time()
    if now - _research_keys_refresh_ts >= _RESEARCH_KEYS_REFRESH_TTL:
        mcp_server._load_research_keys()
        _research_keys_refresh_ts = now


@proxy_app.get("/research/{request_id}")
async def proxy_research_status(request_id: str):
    """Tavily Research 状态：查询指定任务。

    任务按 key 隔离：优先用提交时的同一 key（research_keys.json 映射，跨进程
    共享），找不到/已失效才回退轮询取 key。
    """
    _refresh_research_keys()
    pinned = mcp_server._research_keys.get(request_id) or ""
    masked = ""
    t0 = time.time()
    try:
        client = None
        if pinned:
            k = pool.get_key(pinned)
            if k is not None and k.is_active and not k.is_exhausted:
                client = _client_for(k.key)
                masked = pinned
        if client is None:
            client, masked = _get_client()
        resp = client.get_research(request_id)
        if not isinstance(resp, dict):
            resp = {"status": resp}
        _record(masked, "research-status", t0, True, _usage_credits(resp)[0],
                request_id=request_id, usage_source="unknown", source="proxy", project_id="")
        return JSONResponse(resp)
    except Exception as e:  # noqa: BLE001
        _log.warning("proxy /research/%s 失败 masked=%s: %s", request_id, masked, str(e)[:200])
        _record(masked, "research-status", t0, False, 0, str(e), request_id=request_id,
                usage_source="none", source="proxy", project_id="")
        return _error(500, str(e)[:300])


def main() -> None:
    """按 config.json 启动搜索代理服务（proxy_host:proxy_port，默认 8002）。"""
    import uvicorn

    cfg = get_settings()
    host = cfg.get("proxy_host", "0.0.0.0")
    port = int(cfg.get("proxy_port", 8002))
    uvicorn.run(proxy_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

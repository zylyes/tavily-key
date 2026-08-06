"""Tavily 兼容搜索代理（app/tavily_proxy.py）单元测试。

用 TestClient 走完整 ASGI 链路，monkeypatch 掉真实网络转发
（tavily_proxy._run_with_retry 与 httpx.get），聚焦鉴权、参数解析、
错误映射与各端点行为。
"""
import json

import pytest
from fastapi.testclient import TestClient

import tavily_proxy
from tavily_proxy import proxy_app

TOKEN = "proxy-secret-123"


class _FakeClient:
    """假 TavilyClient：记录最近一次调用参数并返回官方风格响应。"""

    def __init__(self):
        self.last: dict = {}

    def search(self, **kwargs):
        self.last = dict(kwargs)
        return {
            "query": kwargs.get("query"),
            "results": [{"title": "t", "content": "c", "url": "u"}],
            "response_time": 0.12,
            "request_id": "rid-1",
            "usage": {"credits": 1},
        }

    def extract(self, **kwargs):
        self.last = dict(kwargs)
        return {"results": [{"url": "https://example.com", "raw_content": "x"}],
                "response_time": 0.1, "request_id": "rid-e", "usage": {"credits": 1}}

    def crawl(self, **kwargs):
        self.last = dict(kwargs)
        return {"results": [{"url": "https://example.com"}],
                "response_time": 0.1, "request_id": "rid-c", "usage": {"credits": 1}}

    def map(self, **kwargs):
        self.last = dict(kwargs)
        return {"results": [{"url": "https://example.com"}],
                "response_time": 0.1, "request_id": "rid-m", "usage": {"credits": 1}}


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def proxy_env(monkeypatch):
    """默认环境：空 token（不鉴权）+ 假转发（_run_with_retry 直接调假 client）。"""
    holder = {"client": _FakeClient()}
    monkeypatch.setattr(
        tavily_proxy, "_run_with_retry",
        lambda ep, fn: json.dumps(fn(holder["client"]), ensure_ascii=False),
    )
    monkeypatch.setattr(tavily_proxy, "_fresh_settings", lambda: {"proxy_token": ""})
    return holder


def _client() -> TestClient:
    return TestClient(proxy_app)


def _set_token(monkeypatch, token: str):
    monkeypatch.setattr(tavily_proxy, "_fresh_settings", lambda: {"proxy_token": token})


# ── 鉴权 ───────────────────────────────────────────────────────
def test_auth_required_when_token_set(proxy_env, monkeypatch):
    _set_token(monkeypatch, TOKEN)
    c = _client()
    # 无 Authorization → 401
    assert c.post("/search", json={"query": "hello"}).status_code == 401
    # 错误 token → 401
    r = c.post("/search", json={"query": "hello"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    # 正确 Bearer → 200
    r = c.post("/search", json={"query": "hello"}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    # 错误响应为 Tavily 风格 {"detail": {"error": ...}}
    assert "detail" in c.post("/search", json={"query": "x"}).json()


def test_auth_via_body_api_key(proxy_env, monkeypatch):
    _set_token(monkeypatch, TOKEN)
    r = _client().post("/search", json={"query": "hello", "api_key": TOKEN})
    assert r.status_code == 200


def test_no_auth_when_token_empty(proxy_env):
    assert _client().post("/search", json={"query": "hello"}).status_code == 200


def test_token_change_picked_up_without_restart(monkeypatch):
    """回归：面板设置密钥后代理无需重启，鉴权即时生效（settings 缓存热刷新）。"""
    monkeypatch.setattr(
        tavily_proxy, "_run_with_retry",
        lambda ep, fn: json.dumps({"results": []}, ensure_ascii=False),
    )
    state = {"token": ""}
    monkeypatch.setattr(tavily_proxy, "_fresh_settings", lambda: {"proxy_token": state["token"]})
    c = _client()
    # 未设置密钥：开放
    assert c.post("/search", json={"query": "hello"}).status_code == 200
    # 面板设置密钥后（模拟 config.json 变更被 _fresh_settings 感知）
    state["token"] = TOKEN
    # 无密钥 / 错密钥 → 401
    assert c.post("/search", json={"query": "hello"}).status_code == 401
    assert c.post("/search", json={"query": "hello"},
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    # 正确密钥 → 200
    r = c.post("/search", json={"query": "hello"},
               headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


# ── /search ────────────────────────────────────────────────────
def test_search_get_405(proxy_env):
    assert _client().get("/search").status_code == 405


def test_search_missing_query_400(proxy_env):
    r = _client().post("/search", json={})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_search_ok_and_include_usage_forced(proxy_env):
    r = _client().post("/search", json={"query": "test", "max_results": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["title"] == "t"
    assert proxy_env["client"].last["query"] == "test"
    assert proxy_env["client"].last["max_results"] == 3
    # 强制 include_usage=True：保证本地积分落账
    assert proxy_env["client"].last["include_usage"] is True


def test_search_type_normalization(proxy_env):
    _client().post("/search", json={
        "query": "q", "max_results": "10", "chunks_per_source": "2",
        "include_answer": "true", "include_raw_content": "markdown",
        "include_images": "true",
    })
    last = proxy_env["client"].last
    assert last["max_results"] == 10
    assert last["chunks_per_source"] == 2
    assert last["include_answer"] is True
    assert last["include_raw_content"] == "markdown"
    assert last["include_images"] is True


# ── 错误映射 ───────────────────────────────────────────────────
def _set_retry_error(monkeypatch, payload: dict):
    monkeypatch.setattr(
        tavily_proxy, "_run_with_retry",
        lambda ep, fn: json.dumps(payload, ensure_ascii=False),
    )


def test_error_pool_empty_503(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "No active API keys in pool. Add keys via CLI or dashboard."})
    r = _client().post("/search", json={"query": "x"})
    assert r.status_code == 503
    assert "error" in r.json()["detail"]


def test_error_auth_401(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "Unauthorized: invalid api key"})
    assert _client().post("/search", json={"query": "x"}).status_code == 401


def test_error_quota_432(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "This request exceeds your plan's set usage limit. Please upgrade your plan."})
    assert _client().post("/search", json={"query": "x"}).status_code == 432


def test_error_rate_429(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "Your request has been blocked due to excessive requests."})
    assert _client().post("/search", json={"query": "x"}).status_code == 429


def test_error_other_500(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "boom"})
    assert _client().post("/search", json={"query": "x"}).status_code == 500


def test_error_bad_request_400(proxy_env, monkeypatch):
    _set_retry_error(monkeypatch, {"error": "400 Bad Request: invalid search_depth"})
    assert _client().post("/search", json={"query": "x"}).status_code == 400


# ── /extract /crawl /map ───────────────────────────────────────
def test_extract_missing_urls_400(proxy_env):
    assert _client().post("/extract", json={}).status_code == 400


def test_extract_ok(proxy_env):
    r = _client().post("/extract", json={"urls": ["https://example.com"], "extract_depth": "basic"})
    assert r.status_code == 200
    assert proxy_env["client"].last["include_usage"] is True
    assert proxy_env["client"].last["urls"] == ["https://example.com"]


def test_crawl_missing_url_400(proxy_env):
    assert _client().post("/crawl", json={}).status_code == 400


def test_crawl_ok(proxy_env):
    r = _client().post("/crawl", json={"url": "https://example.com", "max_depth": 2})
    assert r.status_code == 200
    assert proxy_env["client"].last["max_depth"] == 2


def test_map_missing_url_400(proxy_env):
    assert _client().post("/map", json={}).status_code == 400


def test_map_ok(proxy_env):
    r = _client().post("/map", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert proxy_env["client"].last["include_usage"] is True


# ── /usage ─────────────────────────────────────────────────────
def _active_key():
    from key_pool import ApiKey

    return ApiKey(
        key="tvly-raw", masked="tvly-****abcd", is_active=True, is_exhausted=False,
        request_count=0, error_count=0, credits_used=0, credits_limit=0,
        last_used_at=0, added_at=0, last_error="",
    )


def test_usage_no_keys_503(proxy_env, monkeypatch):
    monkeypatch.setattr(tavily_proxy.pool, "list_keys", lambda: [])
    assert _client().get("/usage").status_code == 503


def test_usage_ok(proxy_env, monkeypatch):
    monkeypatch.setattr(tavily_proxy.pool, "list_keys", lambda: [_active_key()])
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **kw: _FakeResp(200, {"key": {"usage": 5, "limit": 1000}, "account": {}}),
    )
    r = _client().get("/usage")
    assert r.status_code == 200
    assert r.json()["key"]["usage"] == 5


def test_usage_http_error_500(proxy_env, monkeypatch):
    monkeypatch.setattr(tavily_proxy.pool, "list_keys", lambda: [_active_key()])
    monkeypatch.setattr("httpx.get", lambda *a, **kw: _FakeResp(500, {}))
    assert _client().get("/usage").status_code == 500

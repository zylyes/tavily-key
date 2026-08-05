"""MCP 服务器测试：Bearer 鉴权中间件、工具调用（mock client）、research 流式回退。"""
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import mcp_server


def _inner_app():
    async def hello(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/", hello)])


def test_bearer_auth_requires_token():
    app = mcp_server._wrap_bearer_auth(_inner_app(), "sekret")
    client = TestClient(app)
    assert client.get("/").status_code == 401
    r = client.get("/", headers={"Authorization": "Bearer sekret"})
    assert r.status_code == 200
    r = client.get("/", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_bearer_auth_disabled_when_no_token():
    app = mcp_server._wrap_bearer_auth(_inner_app(), "")
    client = TestClient(app)
    assert client.get("/").status_code == 200


def test_key_strategy_default_round_robin(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_settings", lambda: {})
    assert mcp_server._key_strategy() == "round-robin"


def test_key_strategy_reads_config(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_settings", lambda: {"key_strategy": "least-used"})
    assert mcp_server._key_strategy() == "least-used"


class _FakeSearchClient:
    def search(self, **kwargs):
        return {"query": kwargs["query"], "results": [{"title": "t", "url": "u", "content": "c"}]}


def test_tavily_search_tool(monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_FakeSearchClient(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(mcp_server.tavily_search("hello"))
    assert out["results"][0]["title"] == "t"


class _FakeResearchClient:
    """research 默认抛 stream-required，stream=True 时返回 SSE 帧。"""

    def research(self, **kwargs):
        if kwargs.get("stream"):
            return (
                b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"World"}}]}\n\n'
                b'data: [DONE]\n\n'
            )
        raise Exception("research_stream_required (HTTP 400): please use stream=true")

    def get_research(self, request_id):
        return {"status": "completed", "content": "unused"}


def test_tavily_research_stream_fallback(monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_FakeResearchClient(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(mcp_server.tavily_research("q", model="mini", timeout=10))
    assert out["status"] == "completed"
    assert out["content"] == "Hello World"


def test_research_stream_helper_parses_sse():
    client = _FakeResearchClient()
    result = mcp_server._research_stream(
        client, "q", "mini", "numbered", 10.0, {}
    )
    assert result["content"] == "Hello World"

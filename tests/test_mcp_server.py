"""MCP 服务器测试：Bearer 鉴权中间件、工具调用（mock client）、research 流式回退。"""
import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import mcp_server
from key_pool import ApiKey


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
    out = json.loads(asyncio.run(mcp_server.tavily_search("hello")))
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
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", model="mini", timeout=10)))
    assert out["status"] == "completed"
    assert out["content"] == "Hello World"


def test_research_stream_helper_parses_sse():
    client = _FakeResearchClient()
    result = mcp_server._research_stream(
        client, "q", "mini", "numbered", 10.0, {}
    )
    assert result["content"] == "Hello World"


# ── 新增：新参数 / 重试 / 异步 research ───────────────────────
def test_tavily_search_auto_parameters(monkeypatch):
    captured = {}

    class _C:
        def search(self, **kw):
            captured.update(kw)
            return {"query": kw["query"], "results": []}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    asyncio.run(mcp_server.tavily_search("q", auto_parameters=True))
    assert captured.get("auto_parameters") is True


def test_run_with_retry_switches_key_on_quota(monkeypatch):
    state = {"calls": 0}

    class _Bad:
        def search(self, **kw):
            raise Exception("432 plan limit exceeded")

    class _Good:
        def search(self, **kw):
            return {"results": [{"title": "t", "url": "u", "content": "c"}]}

    def fake_get_client():
        state["calls"] += 1
        if state["calls"] == 1:
            return _Bad(), "tvly-bad***"
        return _Good(), "tvly-good***"

    monkeypatch.setattr(mcp_server, "_get_client", fake_get_client)
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(mcp_server._run_with_retry("search", lambda c: c.search(query="q")))
    assert state["calls"] == 2
    assert out["results"][0]["title"] == "t"


def test_tavily_research_wait_false_returns_request_id(monkeypatch):
    class _C:
        def research(self, **kw):
            return {"request_id": "rid-1"}

        def get_research(self, request_id):
            return {"status": "completed", "content": "x"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", wait=False)))
    assert out["request_id"] == "rid-1"
    assert out["status"] == "submitted"


def test_tavily_research_status_tool(monkeypatch):
    class _C:
        def get_research(self, request_id):
            return {"status": "completed", "content": "done"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research_status("rid-1")))
    assert out["status"] == "completed"


def test_norm_flag_string_true():
    assert mcp_server._norm_flag("true") is True
    assert mcp_server._norm_flag("False") is False
    assert mcp_server._norm_flag("TRUE") is True
    assert mcp_server._norm_flag("basic") == "basic"
    assert mcp_server._norm_flag("advanced") == "advanced"
    assert mcp_server._norm_flag("markdown") == "markdown"
    assert mcp_server._norm_flag(True) is True
    assert mcp_server._norm_flag(False) is False


def test_norm_flag_enum_case_insensitive():
    """枚举字符串（basic/advanced/markdown/text）应忽略大小写统一转小写，避免 API 400。"""
    assert mcp_server._norm_flag("Basic") == "basic"
    assert mcp_server._norm_flag("ADVANCED") == "advanced"
    assert mcp_server._norm_flag("Markdown") == "markdown"
    assert mcp_server._norm_flag("Text") == "text"


def test_tavily_search_normalizes_include_answer(monkeypatch):
    captured = {}

    class _C:
        def search(self, **kw):
            captured.update(kw)
            return {"query": kw["query"], "results": []}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    asyncio.run(mcp_server.tavily_search("q", include_answer="true", include_raw_content="false"))
    assert captured.get("include_answer") is True
    assert captured.get("include_raw_content") is False
    asyncio.run(mcp_server.tavily_search("q", include_answer="basic", include_raw_content="markdown"))
    assert captured.get("include_answer") == "basic"
    assert captured.get("include_raw_content") == "markdown"


def test_tavily_research_status_uses_same_key(monkeypatch):
    """status 查询必须复用提交 research 时的同一 key（任务按 key 隔离）。"""
    used = {}

    class _C:
        def __init__(self, key):
            self._key = key

        def research(self, **kw):
            return {"request_id": "rid-same"}

        def get_research(self, request_id):
            used["key"] = self._key
            return {"status": "completed", "content": "done"}

    fake_key = ApiKey(
        key="tvly-pinned-raw", masked="tvly-pinned***", is_active=True, is_exhausted=False,
        request_count=0, error_count=0, credits_used=0, credits_limit=0,
        last_used_at=0.0, added_at=0.0, last_error="",
    )
    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C("tvly-fallback"), "tvly-fallback***"))
    monkeypatch.setattr(mcp_server.pool, "get_key", lambda masked: fake_key)
    monkeypatch.setattr(mcp_server, "TavilyClient", _C)  # 构造真实 client 时返回 fake
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)

    mcp_server._research_keys.clear()
    asyncio.run(mcp_server.tavily_research("q", wait=False))
    out = json.loads(asyncio.run(mcp_server.tavily_research_status("rid-same")))
    assert out["status"] == "completed"
    assert used["key"] == "tvly-pinned-raw"  # 用了提交时的 key，而非轮询 key
    mcp_server._research_keys.clear()


def test_research_key_persists_across_reload(monkeypatch, tmp_path):
    """request_id→masked 映射落盘，模拟重启后 _load_research_keys 可恢复。"""
    fake_path = tmp_path / "research_keys.json"
    monkeypatch.setattr(mcp_server, "_RESEARCH_KEYS_PATH", fake_path)

    mcp_server._save_research_key("rid-persist", "tvly-persist***")
    assert fake_path.exists()

    # 模拟重启：清空内存后重新从磁盘加载
    mcp_server._research_keys.clear()
    mcp_server._load_research_keys()
    assert mcp_server._research_keys.get("rid-persist") == "tvly-persist***"

    mcp_server._research_keys.clear()


def test_apply_default_timeout_injects_session_timeout():
    """get_research 走 requests.Session.get() 且无显式 timeout，必须注入默认超时兜底。"""
    import requests

    calls = {}

    class _ProbeSession(requests.Session):
        def request(self, *args, **kwargs):
            calls["timeout"] = kwargs.get("timeout")
            return super().request(*args, **kwargs)

    client = mcp_server.TavilyClient(api_key="tvly-probe", session=_ProbeSession())
    mcp_server._apply_default_timeout(client)
    assert calls == {}

    # session.get()（get_research 路径）应带上默认超时
    with pytest.raises(Exception):
        client.get_research("rid-1")  # 连接必然失败，但会经过 request()
    assert calls.get("timeout") == mcp_server._REQUEST_TIMEOUT


def test_apply_default_timeout_keeps_explicit_timeout():
    """显式传 timeout 的调用（search 等）应覆盖默认值，不受注入影响。"""
    import requests

    calls = {}

    class _ProbeSession(requests.Session):
        def request(self, *args, **kwargs):
            calls["timeout"] = kwargs.get("timeout")
            raise requests.ConnectionError("probe")

    client = mcp_server.TavilyClient(api_key="tvly-probe", session=_ProbeSession())
    mcp_server._apply_default_timeout(client)

    # search 显式传 timeout=5，应覆盖注入的默认值
    with pytest.raises(Exception):
        client.search("q", max_results=1, timeout=5)
    assert calls.get("timeout") == 5


def test_norm_country_valid_cases():
    """两位 ISO 代码/完整国家名/别名均应归一化为官方完整国家名；空值原样返回。"""
    assert mcp_server._norm_country("") == ""
    assert mcp_server._norm_country("us") == "united states"
    assert mcp_server._norm_country("US") == "united states"
    assert mcp_server._norm_country("  CN  ") == "china"
    assert mcp_server._norm_country("jp") == "japan"
    assert mcp_server._norm_country("United States") == "united states"
    assert mcp_server._norm_country("china") == "china"
    assert mcp_server._norm_country("usa") == "united states"  # 别名
    assert mcp_server._norm_country("uk") == "united kingdom"


def test_norm_country_invalid_raises():
    """非合法国家（中文/数字/乱码/未知两位码/未知名称）应抛 ValueError。"""
    for bad in ("中国", "u1", "usx", "u", "US-", "xx", "atlantis"):
        with pytest.raises(ValueError):
            mcp_server._norm_country(bad)


def test_tavily_search_validates_country(monkeypatch):
    """非法 country 直接返回友好错误、不触达 API；合法值归一化为完整国家名透传。"""
    captured = {}

    class _C:
        def search(self, **kw):
            captured.update(kw)
            return {"query": kw["query"], "results": []}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)

    # 非法 country：返回错误 JSON，且 client.search 不被调用
    out = json.loads(asyncio.run(mcp_server.tavily_search("q", country="中国")))
    assert "error" in out
    assert "country" in out["error"].lower()
    assert captured == {}  # 未触达 client.search

    # 合法 country（两位 ISO 码）：归一化为官方完整国家名后透传
    asyncio.run(mcp_server.tavily_search("q", country="US"))
    assert captured.get("country") == "united states"

    # 合法 country（完整国家名，大小写变体）：归一化后透传
    captured.clear()
    asyncio.run(mcp_server.tavily_search("q", country="China"))
    assert captured.get("country") == "china"


def test_run_with_retry_switches_country_format(monkeypatch):
    """Invalid country 错误应自动切换格式（完整名 ↔ 两位码）后换 key 重试。"""
    keys = []
    kwargs = {"query": "q", "country": "united states"}

    def fake_get_client():
        keys.append(f"tvly-key{len(keys)}")
        return (object(), keys[-1])

    monkeypatch.setattr(mcp_server, "_get_client", fake_get_client)
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)

    def flaky(client):
        # 第一次（完整名）报 Invalid country；切换后（两位码）成功
        if kwargs["country"] == "united states":
            raise ValueError("Invalid country. Must be a valid country.")
        return {"query": kwargs["country"], "results": []}

    out = json.loads(mcp_server._run_with_retry("search", flaky, kwargs))
    assert out["results"] == []
    assert kwargs["country"] == "us"  # 已切换为两位 ISO 码
    assert len(keys) == 2  # 换 key 重试了一次

    # 反向：两位码报错 → 自动切换为完整名
    kwargs = {"query": "q", "country": "us"}

    def flaky2(client):
        if kwargs["country"] == "us":
            raise ValueError("Invalid country. Must be a valid country.")
        return {"query": kwargs["country"], "results": []}

    out2 = json.loads(mcp_server._run_with_retry("search", flaky2, kwargs))
    assert out2["results"] == []
    assert kwargs["country"] == "united states"

    # 非 country 错误：不切换格式
    kwargs = {"query": "q", "country": "us"}

    def flaky3(client):
        raise ValueError("Internal Server Error")

    out3 = json.loads(mcp_server._run_with_retry("search", flaky3, kwargs))
    assert "error" in out3
    assert kwargs["country"] == "us"  # 未切换

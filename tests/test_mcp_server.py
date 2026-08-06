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


@pytest.fixture(autouse=True)
def _isolate_research_keys(tmp_path, monkeypatch):
    """隔离 research 任务映射：所有测试写入独立文件，避免污染真实 data/。"""
    monkeypatch.setattr(mcp_server, "_RESEARCH_KEYS_PATH", tmp_path / "research_keys.json")
    mcp_server._research_keys.clear()
    yield
    mcp_server._research_keys.clear()
    mcp_server._research_task_cache.clear()


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
        def __init__(self, key, **kwargs):
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
    import tempfile
    from pathlib import Path
    monkeypatch.setattr(mcp_server, "_RESEARCH_KEYS_PATH", Path(tempfile.mkdtemp()) / "research_keys.json")

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
    """两位 ISO 代码应保留并转小写；空值原样返回。"""
    assert mcp_server._norm_country("") == ""
    assert mcp_server._norm_country("us") == "us"
    assert mcp_server._norm_country("US") == "us"
    assert mcp_server._norm_country("  CN  ") == "cn"
    assert mcp_server._norm_country("jp") == "jp"


def test_norm_country_invalid_raises():
    """非两位字母代码（全名/中文/数字/三位）应抛 ValueError。"""
    for bad in ("China", "中国", "USA", "u1", "usx", "u", "US-"):
        with pytest.raises(ValueError):
            mcp_server._norm_country(bad)


def test_tavily_search_validates_country(monkeypatch):
    """非法 country 直接返回友好错误、不触达 API；合法值转小写透传。"""
    captured = {}

    class _C:
        def search(self, **kw):
            captured.update(kw)
            return {"query": kw["query"], "results": []}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)

    # 非法 country：返回错误 JSON，且 client.search 不被调用
    out = json.loads(asyncio.run(mcp_server.tavily_search("q", country="China")))
    assert "error" in out
    assert "country" in out["error"].lower()
    assert captured == {}  # 未触达 client.search

    # 合法 country：大写转小写后透传
    asyncio.run(mcp_server.tavily_search("q", country="US"))
    assert captured.get("country") == "us"


# ── P0-1：Research 流式三段式超时 ──────────────────────────────
def test_default_research_timeout_by_model():
    """默认总时长按 model：mini/auto 300s、pro 900s。"""
    assert mcp_server._default_research_timeout("mini") == 300.0
    assert mcp_server._default_research_timeout("auto") == 300.0
    assert mcp_server._default_research_timeout("pro") == 900.0
    assert mcp_server._default_research_timeout("PRO") == 900.0


def test_research_stream_deadline_returns_timeout(monkeypatch):
    """整体 deadline：滴灌流超过总时长应返回 status=timeout 与部分内容，并显式关闭连接。"""
    class _Slow:
        def __init__(self):
            self.closed = False

        def research(self, **kwargs):
            return self

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"World"}}]}\n\n'

        def close(self):
            self.closed = True

    fake = {"now": 1000.0}

    def _t():
        fake["now"] += 1.0
        return fake["now"]

    monkeypatch.setattr(mcp_server.time, "time", _t)
    slow = _Slow()
    result = mcp_server._research_stream(slow, "q", "mini", "numbered", 1.0, {})
    assert result["status"] == "timeout"
    assert result["content"] == "Hello "  # 只拼到超时前的部分内容
    assert "deadline" in result["error"]
    assert slow.closed is True  # 超时后显式关闭连接


def test_research_stream_read_error_returns_partial():
    """流读取阶段异常：返回 status=error 与部分内容，不向上抛（避免回退重复提交）。"""
    class _Broken:
        def research(self, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"Part"}}]}\n\n'
            raise RuntimeError("connection reset by peer")

    result = mcp_server._research_stream(_Broken(), "q", "mini", "numbered", 10.0, {})
    assert result["status"] == "error"
    assert result["content"] == "Part"
    assert "connection reset" in result["error"]


def test_research_impl_stream_timeout_no_fallback(monkeypatch):
    """流已开始后整体超时：返回 timeout 结果，不回退「提交+轮询」（避免重复提交双倍消耗）。"""
    state = {"polled": False}

    class _Slow:
        def research(self, **kwargs):
            if kwargs.get("stream"):
                return iter([
                    b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"World"}}]}\n\n',
                ])
            return {"request_id": "rid-fallback"}

        def get_research(self, request_id):
            state["polled"] = True
            return {"status": "completed", "content": "fallback-report"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_Slow(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    fake = {"now": 1000.0}

    def _t():
        fake["now"] += 1.0
        return fake["now"]

    monkeypatch.setattr(mcp_server.time, "time", _t)
    out = json.loads(mcp_server._research_impl("q", "mini", "numbered", None, None, True, 1.0, 2.0))
    assert out["status"] == "timeout"
    assert out["content"] == "Hello "
    assert state["polled"] is False  # 未回退提交+轮询


# ── P0-2：尊重 429 retry-after ─────────────────────────────────
def test_patch_error_headers_attaches_retry_after():
    """wrap 后 429 异常携带 retry-after（SDK 原生异常不带 headers，无法直接读取）。"""
    from tavily.errors import UsageLimitExceededError

    class _Resp:
        status_code = 429
        headers = {"retry-after": "3"}

        def raise_for_status(self):
            raise RuntimeError("boom")

    class _Client:
        def _handle_error_response(self, response):
            raise UsageLimitExceededError("rate limited")

    c = _Client()
    mcp_server._patch_error_headers(c)
    with pytest.raises(Exception) as ei:
        c._handle_error_response(_Resp())
    assert getattr(ei.value, "retry_after", None) == 3.0


def test_patch_error_headers_idempotent():
    """重复 wrap 不重复包装（幂等）。"""
    class _Client:
        def _handle_error_response(self, response):
            raise Exception("boom")

    c = _Client()
    mcp_server._patch_error_headers(c)
    first = c._handle_error_response
    mcp_server._patch_error_headers(c)
    assert c._handle_error_response is first


def test_run_with_retry_short_retry_after_retries_same_key(monkeypatch):
    """429 带短 retry-after(<5s)：同 key 等待重试，不切换其他 key。"""
    state = {"calls": 0, "sleeps": []}

    class _C:
        def search(self, **kw):
            state["calls"] += 1
            if state["calls"] == 1:
                e = Exception("429 Too Many Requests")
                e.retry_after = 2.0
                raise e
            return {"results": [{"title": "t", "url": "u", "content": "c"}]}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server.time, "sleep", lambda s: state["sleeps"].append(s))
    out = json.loads(mcp_server._run_with_retry("search", lambda c: c.search(query="q")))
    assert state["calls"] == 2
    assert state["sleeps"] == [2.0]
    assert out["results"][0]["title"] == "t"


def test_run_with_retry_long_retry_after_switches_key(monkeypatch):
    """429 带长 retry-after(>=5s)：切换其他 key 重试。"""
    state = {"calls": 0}

    class _Bad:
        def search(self, **kw):
            e = Exception("429 Too Many Requests")
            e.retry_after = 10.0
            raise e

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


# ── P0-5：mcp_human_id 接线 ────────────────────────────────────
def test_client_for_forwards_human_id(monkeypatch):
    """mcp_human_id 配置应通过 SDK 原生参数接线（此前配置存在但从未生效）。"""
    captured = {}

    class _C:
        def __init__(self, key, **kwargs):
            captured["key"] = key
            captured["human_id"] = kwargs.get("human_id")

    monkeypatch.setattr(mcp_server, "TavilyClient", _C)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: {"mcp_human_id": "human-123"})
    monkeypatch.setattr(mcp_server, "_apply_default_timeout", lambda c: None)
    monkeypatch.setattr(mcp_server, "_patch_error_headers", lambda c: None)
    mcp_server._client_for("tvly-raw")
    assert captured["key"] == "tvly-raw"
    assert captured["human_id"] == "human-123"


def test_client_for_human_id_empty_passes_none(monkeypatch):
    """mcp_human_id 为空时不传 human_id（避免发空 X-Human-Id 头）。"""
    captured = {}

    class _C:
        def __init__(self, key, **kwargs):
            captured["human_id"] = kwargs.get("human_id")

    monkeypatch.setattr(mcp_server, "TavilyClient", _C)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: {"mcp_human_id": ""})
    monkeypatch.setattr(mcp_server, "_apply_default_timeout", lambda c: None)
    monkeypatch.setattr(mcp_server, "_patch_error_headers", lambda c: None)
    mcp_server._client_for("tvly-raw")
    assert captured["human_id"] is None


# ── P1：Research 新能力（output_length / output_schema / max_sources）──
def test_tavily_research_output_length_passthrough(monkeypatch):
    """output_length 透传到 client.research（枚举转小写）。"""
    captured = {}

    class _C:
        def research(self, **kwargs):
            captured.update(kwargs)
            if kwargs.get("stream"):
                return b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            return {"request_id": "rid"}

        def get_research(self, rid):
            return {"status": "completed", "content": "x"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", output_length="LONG", timeout=5)))
    assert out["status"] == "completed"
    assert captured.get("output_length") == "long"


def test_tavily_research_invalid_output_length(monkeypatch):
    """非法 output_length 直接返回友好错误，不触达 API。"""
    captured = {}

    class _C:
        def research(self, **kwargs):
            captured["called"] = True
            return {"request_id": "rid"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", output_length="huge", timeout=5)))
    assert "error" in out
    assert "output_length" in out["error"]
    assert captured == {}


def test_tavily_research_max_sources_validation(monkeypatch):
    """max_sources 越界返回友好错误；合法值透传。"""
    monkeypatch.setattr(mcp_server, "_get_client", lambda: (object(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", max_sources=20, timeout=5)))
    assert "error" in out
    assert "max_sources" in out["error"]

    captured = {}

    class _C:
        def research(self, **kwargs):
            captured.update(kwargs)
            if kwargs.get("stream"):
                return b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            return {"request_id": "rid"}

        def get_research(self, rid):
            return {"status": "completed", "content": "x"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", max_sources=5, max_subsources=3, timeout=5)))
    assert out["status"] == "completed"
    assert captured.get("max_sources") == 5
    assert captured.get("max_subsources") == 3


def test_tavily_research_output_schema_stream_structured(monkeypatch):
    """output_schema 结构化：delta.content 为对象时直接作为 content 返回。"""
    class _C:
        def research(self, **kwargs):
            if kwargs.get("stream"):
                return iter([
                    b'data: {"choices":[{"delta":{"content":{"summary":"done","n":3}}}]}\n\n',
                ])
            return {"request_id": "rid"}

        def get_research(self, rid):
            return {"status": "completed", "content": "x"}

    monkeypatch.setattr(mcp_server, "_get_client", lambda: (_C(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research(
        "q", output_schema={"properties": {"summary": {"type": "string"}}}, timeout=5)))
    assert out["status"] == "completed"
    assert out["content"] == {"summary": "done", "n": 3}


def test_tavily_research_output_schema_invalid(monkeypatch):
    """output_schema 非 dict 返回友好错误。"""
    monkeypatch.setattr(mcp_server, "_get_client", lambda: (object(), "tvly-***"))
    monkeypatch.setattr(mcp_server, "_record", lambda *a, **k: None)
    out = json.loads(asyncio.run(mcp_server.tavily_research("q", output_schema="not-a-dict", timeout=5)))
    assert "error" in out
    assert "output_schema" in out["error"]


def test_client_for_forwards_project_id(monkeypatch):
    """mcp_project_id 配置通过 SDK 原生参数接线（X-Project-ID 头）。"""
    captured = {}

    class _C:
        def __init__(self, key, **kwargs):
            captured["key"] = key
            captured["project_id"] = kwargs.get("project_id")
            captured["human_id"] = kwargs.get("human_id")

    monkeypatch.setattr(mcp_server, "TavilyClient", _C)
    monkeypatch.setattr(mcp_server, "get_settings",
                        lambda: {"mcp_project_id": "proj-1", "mcp_human_id": "human-1"})
    monkeypatch.setattr(mcp_server, "_apply_default_timeout", lambda c: None)
    monkeypatch.setattr(mcp_server, "_patch_error_headers", lambda c: None)
    mcp_server._client_for("tvly-raw")
    assert captured["key"] == "tvly-raw"
    assert captured["project_id"] == "proj-1"
    assert captured["human_id"] == "human-1"


# ── P2-3：Research 任务看板（缓存与查询上限） ────────────────
def test_list_research_tasks_cache_and_cap(monkeypatch):
    """看板：查询带 TTL 缓存（终态不重复查询）与查询上限（超限标 unknown 不触达 API）。"""
    state = {"queries": 0}

    def fake_query(request_id, pinned=""):
        state["queries"] += 1
        return {"status": "completed", "content": "done"}, pinned

    mcp_server._research_keys.clear()
    for i in range(30):
        mcp_server._research_keys[f"rid-{i}"] = f"tvly-{i}***"
    mcp_server._research_task_cache.clear()
    monkeypatch.setattr(mcp_server, "_query_research_status", fake_query)

    # 小范围：全部实际查询
    tasks = mcp_server.list_research_tasks(limit=5)
    assert state["queries"] == 5
    assert all(t["status"] == "completed" for t in tasks)

    # 二次调用：全部命中终态缓存，不再查询
    mcp_server.list_research_tasks(limit=5)
    assert state["queries"] == 5

    # 大范围：超过查询上限的标记 unknown，不触达 API
    state["queries"] = 0
    mcp_server._research_task_cache.clear()
    tasks = mcp_server.list_research_tasks(limit=30)
    assert 0 < state["queries"] <= mcp_server._RESEARCH_TASK_QUERY_CAP
    assert any(t["status"] == "unknown" for t in tasks)

    mcp_server._research_keys.clear()
    mcp_server._research_task_cache.clear()


def test_load_research_keys_bom_compatible(tmp_path, monkeypatch):
    """带 BOM 的 UTF-8 文件也能正确加载（某些编辑器/工具写 BOM，纯 utf-8 解析会抛异常回退空）。"""
    fake_path = tmp_path / "research_keys_bom.json"
    fake_path.write_bytes(b'\xef\xbb\xbf{"rid-bom": "tvly-bom***"}')  # UTF-8 with BOM
    monkeypatch.setattr(mcp_server, "_RESEARCH_KEYS_PATH", fake_path)
    mcp_server._research_keys.clear()
    mcp_server._load_research_keys()
    assert mcp_server._research_keys.get("rid-bom") == "tvly-bom***"
    mcp_server._research_keys.clear()

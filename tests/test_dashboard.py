"""Dashboard API 测试：访问鉴权中间件（对比 constant-time）与设置保存校验。"""
import pytest
from fastapi.testclient import TestClient

import dashboard


@pytest.fixture()
def client():
    # 关闭自动启动 MCP（mock get_settings 不含 mcp_auto_start）
    return TestClient(dashboard.app)


def test_auth_disabled_when_token_empty(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.get("/api/stats")
    assert r.status_code == 200


def test_auth_401_without_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    r = client.get("/api/stats")
    assert r.status_code == 401


def test_auth_401_wrong_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    r = client.get("/api/stats", headers={"X-Auth-Token": "wrong"})
    assert r.status_code == 401


def test_auth_200_with_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    r = client.get("/api/stats", headers={"X-Auth-Token": "sekret"})
    assert r.status_code == 200


def test_auth_200_with_query_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    r = client.get("/api/stats?token=sekret")
    assert r.status_code == 200


def test_settings_validation_rejects_bad_port(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/settings", json={"port": 99999})
    assert r.status_code == 400
    assert "error" in r.json()


def test_settings_accepts_valid_patch(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    # 隔离真实 config.json，避免测试写入污染用户配置
    import settings as settings_mod
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_mod, "_cache", None)
    # mcp_token 合法值应被接受并写入（validate_patch 校验）
    r = client.post("/api/settings", json={"mcp_token": "pool-token"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── API 端点 TTL 缓存 ─────────────────────────────────────────
def _fresh_pool(tmp_path, monkeypatch):
    """把 dashboard.pool 替换为隔离的 KeyPool 实例。"""
    import key_pool as kp_mod
    kp_mod.KeyPool._instance = None
    p = kp_mod.KeyPool(str(tmp_path / "db.sqlite"))
    monkeypatch.setattr(dashboard, "pool", p)
    return p


def test_api_logs_ttl_cache(client, monkeypatch, tmp_path):
    """/api/logs 短 TTL 缓存：同参数命中，不同参数分键，写操作后失效。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    p = _fresh_pool(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_query_logs(*a, **k):
        calls["n"] += 1
        return [], 0

    monkeypatch.setattr(p, "query_logs", fake_query_logs)
    assert client.get("/api/logs").status_code == 200
    assert client.get("/api/logs").status_code == 200
    assert calls["n"] == 1                       # 第二次命中缓存
    client.get("/api/logs?status=success")
    assert calls["n"] == 2                       # 不同筛选 → 不同缓存键
    client.get("/api/logs")
    client.post("/api/keys/add", json={"keys": []})
    client.get("/api/logs")
    assert calls["n"] == 3                       # add 后失效重查


def test_api_mcp_status_ttl_cache_and_invalidate(client, monkeypatch):
    """/api/mcp/status 缓存；start/stop 后失效。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    calls = {"n": 0}

    def fake_status():
        calls["n"] += 1
        return {"running": False, "pid": None, "port": 8001}

    monkeypatch.setattr(dashboard.mcp_manager, "status", fake_status)
    monkeypatch.setattr(dashboard.mcp_manager, "start", lambda: {})
    monkeypatch.setattr(dashboard.mcp_manager, "stop", lambda: {})
    client.get("/api/mcp/status")
    client.get("/api/mcp/status")
    assert calls["n"] == 1                       # 命中缓存
    # start 端点内部会立即取一次最新状态（invalidate + status）
    client.post("/api/mcp/start")
    assert calls["n"] == 2
    client.get("/api/mcp/status")
    assert calls["n"] == 3                       # start 后失效重查
    client.get("/api/mcp/status")
    client.post("/api/mcp/stop")
    assert calls["n"] == 4                       # stop 端点内部 status
    client.get("/api/mcp/status")
    assert calls["n"] == 5                       # stop 后失效重查


def test_api_proxy_status_ttl_cache(client, monkeypatch):
    """/api/proxy/status 短 TTL 缓存。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    calls = {"n": 0}

    def fake_status():
        calls["n"] += 1
        return {"running": False, "port": 8002}

    monkeypatch.setattr(dashboard.proxy_manager, "status", fake_status)
    client.get("/api/proxy/status")
    client.get("/api/proxy/status")
    assert calls["n"] == 1                       # 命中缓存
    dashboard._api_cache.clear()
    client.get("/api/proxy/status")
    assert calls["n"] == 2                       # 失效后重查


def test_anomalies_endpoint(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(dashboard, "pool", dashboard.KeyPool())
    r = client.get("/api/keys/anomalies")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["anomalies"], list)


def test_aggregate_endpoint(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(dashboard, "pool", dashboard.KeyPool())
    r = client.get("/api/usage/aggregate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    for key in ("total_keys", "active_keys", "remaining", "total_limit", "total_used"):
        assert key in body["aggregate"]


# ── P2：用量趋势 / 日志筛选导出 / Research 任务看板 ──────────
def test_usage_trend_endpoint(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    class _Fake:
        def get_usage_trend(self, days):
            return {"days": days, "points": [{"date": "2026-08-05", "requests": 3, "success": 2, "failed": 1, "credits": 5, "endpoints": {"search": 3}}]}

    monkeypatch.setattr(dashboard, "pool", _Fake())
    r = client.get("/api/usage/trend?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["trend"]["days"] == 7
    assert body["trend"]["points"][0]["requests"] == 3


def test_logs_endpoint(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    class _Fake:
        def query_logs(self, **kw):
            return [{"created_at": 1, "key_masked": "tvly-***", "endpoint": "search", "success": 1,
                     "credits_consumed": 1, "latency_ms": 10, "request_id": "", "usage_source": "response",
                     "source": "mcp", "error_msg": ""}], 1

    monkeypatch.setattr(dashboard, "pool", _Fake())
    r = client.get("/api/logs?endpoint=search&status=success")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["logs"][0]["endpoint"] == "search"


def test_logs_source_filter(client, monkeypatch):
    """/api/logs 的 source 参数应透传到 query_logs。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    captured: dict = {}

    class _Fake:
        def query_logs(self, **kw):
            captured.update(kw)
            return [], 0

    monkeypatch.setattr(dashboard, "pool", _Fake())
    client.get("/api/logs?source=proxy")
    assert captured.get("source") == "proxy"
    client.get("/api/logs")
    assert captured.get("source") == ""


def test_logs_export_csv(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    class _Fake:
        def query_logs(self, **kw):
            return [{"created_at": 1754400000, "key_masked": "tvly-***", "endpoint": "search", "success": 1,
                     "credits_consumed": 1, "latency_ms": 10.5, "request_id": "rid", "usage_source": "response",
                     "source": "mcp", "error_msg": ""}], 1

    monkeypatch.setattr(dashboard, "pool", _Fake())
    r = client.get("/api/logs/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "endpoint" in r.text
    assert "search" in r.text


def test_research_tasks_endpoint(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    def fake_list(limit=50):
        return [{"request_id": "r1", "masked": "tvly-***", "status": "completed", "content": "ok"}]

    import mcp_server
    monkeypatch.setattr(mcp_server, "list_research_tasks", fake_list)
    r = client.get("/api/research/tasks")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tasks"][0]["status"] == "completed"


# ── 数据备份与恢复 ────────────────────────────────────────────
def test_backup_endpoint(client, monkeypatch, tmp_path):
    """/api/backup 返回备份 zip 下载。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    import backup

    fake_zip = tmp_path / "tavily-backup-test.zip"
    fake_zip.write_bytes(b"PK\x03\x04 fake")
    monkeypatch.setattr(backup, "backup_to", lambda target=None: fake_zip)
    r = client.post("/api/backup")
    assert r.status_code == 200
    assert "application/zip" in r.headers["content-type"]
    assert b"fake" in r.content


def test_backup_endpoint_error(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    import backup

    def _boom(target=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup, "backup_to", _boom)
    r = client.post("/api/backup")
    assert r.status_code == 500
    assert r.json()["ok"] is False


def test_restore_endpoint(client, monkeypatch):
    """/api/restore：停子进程 → 释放 DB 连接 → 恢复 → 刷新配置。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    import backup
    import settings as settings_mod

    calls: list[str] = []
    monkeypatch.setattr(backup, "restore_from", lambda zp: (calls.append("restore"), 4)[1])
    monkeypatch.setattr(dashboard.mcp_manager, "stop", lambda: calls.append("stop-mcp"))
    monkeypatch.setattr(dashboard.proxy_manager, "stop", lambda: calls.append("stop-proxy"))
    monkeypatch.setattr(dashboard.pool, "close_all_connections", lambda: calls.append("close-db"))
    monkeypatch.setattr(settings_mod, "reload", lambda: calls.append("reload"))
    r = client.post("/api/restore", content=b"PK\x03\x04 fake zip")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restored"] == 4
    assert calls == ["stop-mcp", "stop-proxy", "close-db", "restore", "reload"]


def test_restore_endpoint_rejects_empty(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/restore", content=b"")
    assert r.status_code == 400
    assert r.json()["ok"] is False

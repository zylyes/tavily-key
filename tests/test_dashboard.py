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

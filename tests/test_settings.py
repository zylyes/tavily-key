"""settings.validate_patch 配置校验单元测试。"""
import pytest

from settings import mcp_urls, validate_patch


def test_valid_port_normalized():
    assert validate_patch({"port": "8080"})["port"] == 8080
    assert validate_patch({"mcp_port": 9000})["mcp_port"] == 9000


def test_port_out_of_range_rejected():
    with pytest.raises(ValueError):
        validate_patch({"port": 70000})
    with pytest.raises(ValueError):
        validate_patch({"mcp_port": -1})


def test_port_non_integer_rejected():
    with pytest.raises(ValueError):
        validate_patch({"port": "abc"})


def test_unknown_fields_ignored():
    assert validate_patch({"nonsense": 1}) == {}


def test_mode_enum():
    assert validate_patch({"mode": "server"})["mode"] == "server"
    with pytest.raises(ValueError):
        validate_patch({"mode": "cloud"})


def test_transport_enum():
    for t in ("stdio", "sse", "streamable-http"):
        assert validate_patch({"mcp_transport": t})["mcp_transport"] == t
    with pytest.raises(ValueError):
        validate_patch({"mcp_transport": "ws"})


def test_tokens_stripped():
    assert validate_patch({"auth_token": "  abc  "})["auth_token"] == "abc"
    assert validate_patch({"mcp_token": " t "})["mcp_token"] == "t"


def test_booleans_coerced():
    assert validate_patch({"autostart": 1})["autostart"] is True
    assert validate_patch({"start_to_tray": 0})["start_to_tray"] is False
    assert validate_patch({"mcp_auto_start": "yes"})["mcp_auto_start"] is True


def test_key_strategy_passthrough():
    assert validate_patch({"key_strategy": "least-used"})["key_strategy"] == "least-used"


def test_mcp_project_id_accepted():
    assert validate_patch({"mcp_project_id": " proj-9 "})["mcp_project_id"] == "proj-9"
    assert validate_patch({"mcp_human_id": "human-1"})["mcp_human_id"] == "human-1"


def test_mcp_urls_stdio_empty():
    assert mcp_urls({"mcp_transport": "stdio"}) == {}


def test_mcp_urls_lan(monkeypatch):
    monkeypatch.setattr("settings.lan_ip", lambda: "192.168.1.5")
    monkeypatch.setattr("settings.lan_hostname", lambda: "DESKTOP-ABC123")
    cfg = {"mcp_transport": "sse", "mcp_host": "0.0.0.0", "mcp_port": 8001}
    u = mcp_urls(cfg)
    assert u["ip"] == "http://192.168.1.5:8001/sse"
    assert u["hostname"] == "http://DESKTOP-ABC123:8001/sse"
    assert u["hostname_local"] == "http://DESKTOP-ABC123.local:8001/sse"
    assert u["local"] == "http://127.0.0.1:8001/sse"


def test_mcp_urls_streamable_http(monkeypatch):
    monkeypatch.setattr("settings.lan_ip", lambda: "10.0.0.8")
    monkeypatch.setattr("settings.lan_hostname", lambda: "PC")
    cfg = {"mcp_transport": "streamable-http", "mcp_host": "0.0.0.0", "mcp_port": 9001}
    u = mcp_urls(cfg)
    assert u["ip"].endswith(":9001/mcp")
    assert u["hostname_local"] == "http://PC.local:9001/mcp"
    assert u["local"] == "http://127.0.0.1:9001/mcp"


def test_mcp_urls_localhost_only():
    cfg = {"mcp_transport": "sse", "mcp_host": "127.0.0.1", "mcp_port": 8001}
    u = mcp_urls(cfg)
    assert set(u) == {"local"}
    assert u["local"] == "http://127.0.0.1:8001/sse"


def test_mcp_urls_custom_host():
    cfg = {"mcp_transport": "sse", "mcp_host": "192.168.1.10", "mcp_port": 8001}
    u = mcp_urls(cfg)
    assert u["ip"] == "http://192.168.1.10:8001/sse"
    assert "hostname_local" not in u

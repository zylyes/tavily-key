"""settings.validate_patch 配置校验单元测试。"""
import pytest
from settings import mcp_urls, proxy_url, proxy_urls, validate_patch


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


def test_get_settings_fresh_reloads_on_file_change(tmp_path, monkeypatch):
    """config.json 被外部（其他进程）修改后 get_settings_fresh 感知并 reload。"""
    import json

    import settings as settings_mod

    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_mod, "_cache", None)
    settings_mod._fresh_ts = 0.0
    settings_mod._fresh_fp = None
    settings_mod.get_settings_fresh(ttl=0.01)   # 初始化指纹 + 缓存
    assert settings_mod.get_settings()["auth_token"] == ""

    # 模拟子进程外其他进程改写 config.json（绕过进程内缓存）
    (tmp_path / "config.json").write_text(
        json.dumps({"auth_token": "external-token", "port": 8123}), encoding="utf-8"
    )
    settings_mod._fresh_ts = 0.0                 # 过期 TTL 窗口
    settings_mod._fresh_fp = None
    settings_mod.get_settings_fresh(ttl=0.01)
    assert settings_mod.get_settings()["auth_token"] == "external-token"
    assert settings_mod.get_settings()["port"] == 8123


def test_get_settings_fresh_ttl_reuses_cache(tmp_path, monkeypatch):
    """TTL 内不重新 stat：长 TTL 下重复调用复用进程缓存。"""
    import settings as settings_mod

    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_mod, "_cache", None)
    settings_mod._fresh_ts = 0.0
    settings_mod._fresh_fp = None
    settings_mod.get_settings_fresh(ttl=60.0)
    settings_mod.get_settings_fresh(ttl=60.0)   # TTL 内复用，不触发 reload
    assert settings_mod.get_settings()["auth_token"] == ""


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


# ── 搜索代理字段 ───────────────────────────────────────────────
def test_proxy_port_normalized():
    assert validate_patch({"proxy_port": 8002})["proxy_port"] == 8002
    assert validate_patch({"proxy_port": "9000"})["proxy_port"] == 9000
    with pytest.raises(ValueError):
        validate_patch({"proxy_port": 70000})


def test_proxy_token_stripped():
    assert validate_patch({"proxy_token": "  abc  "})["proxy_token"] == "abc"


def test_proxy_host_accepted():
    assert validate_patch({"proxy_host": "127.0.0.1"})["proxy_host"] == "127.0.0.1"


def test_proxy_auto_start_boolean():
    assert validate_patch({"proxy_auto_start": "yes"})["proxy_auto_start"] is True
    assert validate_patch({"proxy_auto_start": 0})["proxy_auto_start"] is False


def test_proxy_urls_shapes():
    # 仅本机监听：只返回 local
    urls = proxy_urls({"proxy_host": "127.0.0.1", "proxy_port": 8002})
    assert urls == {"local": "http://127.0.0.1:8002"}
    # 局域网监听：ip 是 http 地址且端口正确（lan_ip 在无网络时回退 127.0.0.1）
    urls = proxy_urls({"proxy_host": "0.0.0.0", "proxy_port": 8002})
    assert urls["local"] == "http://127.0.0.1:8002"
    assert urls["ip"].startswith("http://") and urls["ip"].endswith(":8002")
    # 单地址推导
    assert proxy_url({"proxy_host": "127.0.0.1", "proxy_port": 8002}) == "http://127.0.0.1:8002"


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


def test_log_rotation_fields():
    """日志轮转字段：log_max_bytes（大整数范围）/ log_backup_count 校验。"""
    assert validate_patch({"log_max_bytes": 10 * 1024 * 1024})["log_max_bytes"] == 10 * 1024 * 1024
    assert validate_patch({"log_backup_count": 5})["log_backup_count"] == 5
    assert validate_patch({"log_backup_count": 0})["log_backup_count"] == 0
    with pytest.raises(ValueError):
        validate_patch({"log_max_bytes": 600 * 1024 * 1024})  # 超 512MB 上限
    with pytest.raises(ValueError):
        validate_patch({"log_max_bytes": "abc"})
    with pytest.raises(ValueError):
        validate_patch({"log_backup_count": -1})


def test_auto_backup_fields():
    """定时自动备份字段：开关/间隔/保留份数校验。"""
    assert validate_patch({"auto_backup_enabled": 1})["auto_backup_enabled"] is True
    assert validate_patch({"auto_backup_enabled": "yes"})["auto_backup_enabled"] is True
    assert validate_patch({"auto_backup_interval_days": "3"})["auto_backup_interval_days"] == 3
    assert validate_patch({"auto_backup_keep": 10})["auto_backup_keep"] == 10
    with pytest.raises(ValueError):
        validate_patch({"auto_backup_interval_days": -1})
    with pytest.raises(ValueError):
        validate_patch({"auto_backup_keep": "abc"})


def test_update_check_fields():
    """GitHub 更新检查字段：开关/间隔小时/单位枚举校验。"""
    assert validate_patch({"update_check_enabled": 1})["update_check_enabled"] is True
    assert validate_patch({"update_check_enabled": 0})["update_check_enabled"] is False
    assert validate_patch({"update_check_interval_hours": "24"})["update_check_interval_hours"] == 24
    assert validate_patch({"update_check_interval_hours": 0})["update_check_interval_hours"] == 0
    for u in ("hour", "day", "week", "month"):
        assert validate_patch({"update_check_interval_unit": u})["update_check_interval_unit"] == u
    with pytest.raises(ValueError):
        validate_patch({"update_check_interval_unit": "minute"})
    with pytest.raises(ValueError):
        validate_patch({"update_check_interval_hours": -1})
    with pytest.raises(ValueError):
        validate_patch({"update_check_interval_hours": "abc"})
    # 自动更新已移除：auto_update_enabled 不再被接受
    assert validate_patch({"auto_update_enabled": True}) == {}

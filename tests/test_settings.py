"""settings.validate_patch 配置校验单元测试。"""
import pytest

from settings import validate_patch


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

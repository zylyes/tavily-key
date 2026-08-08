"""/api/update/* 路由层测试：入参透传、状态机拒绝、变更端点本机来源防护。

覆盖 check / status / announcement / notice-pending（只读）与
download / pause / resume / cancel / apply（变更，无 token 时仅本机回环）。
"""
import dashboard
import pytest
import settings as settings_mod
import updater
from fastapi.testclient import TestClient
from starlette.requests import Request as SRequest


@pytest.fixture()
def client():
    # 不进 with：不触发 lifespan，避免启动后台线程
    return TestClient(dashboard.app)


@pytest.fixture(autouse=True)
def _reset_updater():
    """每个测试前重置 updater 全局状态（下载状态/缓存/通知标记）。"""
    updater._cached_result = None
    updater._cached_ts = 0.0
    updater._cached_is_error = False
    updater._notified_version = None
    updater._dl_thread = None
    updater._dl_epoch = 0
    updater._dl.update(state="idle", received=0, total=0, error="", version="",
                       path="", body="")
    updater._pause_event.clear()
    updater._cancel_event.clear()
    updater._pending_open_notice = ""
    yield


def _release(latest="1.0.0"):
    return {
        "latest_version": latest, "tag_name": f"v{latest}",
        "release_url": f"https://github.com/zylyes/tavily-key/releases/tag/v{latest}",
        "published_at": "", "body": "更新说明",
        "asset_name": f"Tavily-v{latest}-win64.zip",
        "asset_url": f"https://github.com/zylyes/tavily-key/releases/download/v{latest}/a.zip",
        "asset_size": 12345,
    }


# ── 只读端点：check / status / announcement / notice-pending ──
def test_update_check_returns_structure(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(updater, "get_settings",
                        lambda: {"update_repo": "zylyes/tavily-key",
                                 "update_check_interval_hours": 24})
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("9.9.9"))
    r = client.get("/api/update/check")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["update"]["update_available"] is True
    assert body["update"]["latest_version"] == "9.9.9"


def test_update_check_force_passthrough(client, monkeypatch):
    """force=1 强制刷新（不走缓存）→ 触发一次网络请求。"""
    calls: list = []
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(updater, "get_settings",
                        lambda: {"update_repo": "zylyes/tavily-key",
                                 "update_check_interval_hours": 24})
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda repo: calls.append(repo) or _release("1.0.0"))
    r = client.get("/api/update/check?force=1")
    assert r.status_code == 200
    assert len(calls) == 1


def test_update_status_idle(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.get("/api/update/status")
    assert r.status_code == 200
    assert r.json()["status"]["state"] == "idle"


def test_update_announcement_once(client, monkeypatch, tmp_path):
    """更新公告一次性读取：第一次返回，第二次为 null。"""
    import json as _json

    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    (tmp_path / "last-update.json").write_text(
        _json.dumps({"version": "0.9.4", "body": "x", "applied_at": 1.0}), encoding="utf-8")
    r1 = client.get("/api/update/announcement")
    assert r1.status_code == 200
    assert r1.json()["announcement"]["version"] == "0.9.4"
    r2 = client.get("/api/update/announcement")
    assert r2.json()["announcement"] is None


def test_update_notice_pending_consume(client, monkeypatch):
    """系统通知点击标记：非空一次性返回，随后为空。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    updater.mark_open_notice("0.13.4")
    r1 = client.get("/api/update/notice-pending")
    assert r1.json()["version"] == "0.13.4"
    r2 = client.get("/api/update/notice-pending")
    assert r2.json()["version"] == ""


# ── 变更端点：无 token 时仅本机回环来源 ─────────────────────
def test_update_download_rejected_remote_without_token(client, monkeypatch):
    """未设置 auth_token 时，非回环来源（TestClient 为 testclient）被拒 403。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/update/download")
    assert r.status_code == 403
    assert "local only" in r.json()["error"]


def test_update_apply_rejected_remote_without_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/update/apply")
    assert r.status_code == 403


def test_update_pause_rejected_remote_without_token(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/update/pause")
    assert r.status_code == 403


def test_update_local_only_skipped_when_token_set(client, monkeypatch):
    """已设置 auth_token 时跳过来源校验（按 token 鉴权）；带正确 token 可达业务逻辑。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": "sekret"})
    # 非打包版 → start_download 返回「仅打包版」错误，证明通过了来源校验
    r = client.post("/api/update/download", headers={"X-Auth-Token": "sekret"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "打包版" in r.json()["error"]


def test_local_only_loopback_allowed_remote_rejected(monkeypatch):
    """_local_only 单元级：回环放行、远程拒绝、已设 token 时跳过。"""
    from routes.update import _local_only

    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})

    async def recv():
        return {"type": "http.request", "body": b"", "more_body": False}

    def mk(host: str) -> SRequest:
        scope = {
            "type": "http", "method": "POST", "path": "/api/update/download",
            "headers": [(b"host", b"x")], "client": (host, 54321),
            "scheme": "http", "server": ("x", 8000), "query_string": b"",
            "root_path": "", "http_version": "1.1", "asgi": {"version": "3.0"},
        }
        return SRequest(scope, recv)

    assert _local_only(mk("127.0.0.1")) is None
    assert _local_only(mk("::1")) is None
    assert _local_only(mk("192.168.1.5")) is not None
    assert _local_only(mk("testclient")) is not None
    # 已设置 token 时跳过来源校验
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": "s"})
    assert _local_only(mk("192.168.1.5")) is None


# ── 变更端点：状态机拒绝（本机来源、业务逻辑路径）────────────
def test_update_pause_rejected_when_idle(client, monkeypatch):
    """本机来源下，idle 状态暂停被业务层拒绝（非 403）。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})
    # TestClient 是 testclient（非回环）→ 直接测业务函数避免来源拦截
    ok, err = updater.pause_download()
    assert ok is False and "下载" in err


def test_update_apply_rejected_not_done(client, monkeypatch):
    """本机来源下，未就绪时 apply 被业务层拒绝（非 403）。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(settings_mod, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)  # 绕过打包版前置
    r = updater.apply_update()
    assert r["ok"] is False
    assert "未就绪" in r["error"]

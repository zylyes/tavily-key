"""app/updater.py GitHub 更新检查单元测试。"""
import pytest
import updater


@pytest.fixture(autouse=True)
def reset_updater_state():
    """每个测试前重置 updater 模块级缓存、已通知版本与下载状态（全局状态隔离）。"""
    updater._cached_result = None
    updater._cached_ts = 0.0
    updater._notified_version = None
    updater._dl.update(state="idle", received=0, total=0, error="", version="",
                       path="", body="")
    yield


def _cfg(**kw):
    cfg = {
        "update_repo": "zylyes/tavily-key",
        "update_check_interval_hours": 24,
        "notify_webhook": "",
    }
    cfg.update(kw)
    return cfg


def _release(latest="1.0.0", body="更新说明", asset=True):
    r = {
        "latest_version": latest,
        "tag_name": f"v{latest}",
        "release_url": f"https://github.com/zylyes/tavily-key/releases/tag/v{latest}",
        "published_at": "2026-08-07T00:00:00Z",
        "body": body,
    }
    if asset:
        r.update({
            "asset_name": f"Tavily-v{latest}-win64.zip",
            "asset_url": (f"https://github.com/zylyes/tavily-key/releases/download/"
                          f"v{latest}/Tavily-v{latest}-win64.zip"),
            "asset_size": 12345,
        })
    return r


# ── 版本解析与比较 ──────────────────────────────────────────
def test_version_tuple():
    assert updater._version_tuple("v0.9.1") == (0, 9, 1, "")
    assert updater._version_tuple("0.9.2") == (0, 9, 2, "")
    assert updater._version_tuple("0.10.0") == (0, 10, 0, "")
    assert updater._version_tuple("0.9") == (0, 9, 0, "")
    assert updater._version_tuple("0.9.1-beta.2") == (0, 9, 1, "beta.2")
    assert updater._version_tuple("") == (0, 0, 0, "")


def test_normalize_repo():
    assert updater._normalize_repo("zylyes/tavily-key") == "zylyes/tavily-key"
    assert updater._normalize_repo(" https://github.com/zylyes/tavily-key ") == "zylyes/tavily-key"
    assert updater._normalize_repo("github.com/zylyes/tavily-key") == "zylyes/tavily-key"
    assert updater._normalize_repo("zylyes/tavily-key.git") == "zylyes/tavily-key"
    assert updater._normalize_repo("zylyes/tavily-key/") == "zylyes/tavily-key"
    assert updater._normalize_repo("") == ""
    assert updater._normalize_repo("only-owner") == ""


def test_check_interval_hours_invalid_falls_back(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: {"update_check_interval_hours": "abc"})
    assert updater._check_interval_hours() == 24


def test_is_newer():
    assert updater._is_newer("0.9.2", "0.9.1")
    assert updater._is_newer("0.10.0", "0.9.9")
    assert updater._is_newer("v0.9.2", "0.9.1")  # v 前缀
    assert not updater._is_newer("0.9.1", "0.9.2")
    assert not updater._is_newer("0.9.1", "0.9.1")
    # pre-release 低于正式版
    assert updater._is_newer("0.9.2", "0.9.2-beta.1")
    assert not updater._is_newer("0.9.2-beta.2", "0.9.2")
    assert updater._is_newer("0.9.2-beta.3", "0.9.2-beta.2")


# ── check_update ────────────────────────────────────────────
def test_check_update_success(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.11.0"))
    r = updater.check_update(force=True)
    assert r["ok"] is True
    assert r["disabled"] is False
    assert r["update_available"] is True
    assert r["current_version"] == updater.__version__
    assert r["latest_version"] == "0.11.0"
    assert r["release_url"].startswith("https://github.com")
    assert r["body"] == "更新说明"
    assert r["error"] == ""


def test_check_update_uptodate(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release(updater.__version__))
    r = updater.check_update(force=True)
    assert r["ok"] is True
    assert r["update_available"] is False


def test_check_update_disabled_when_repo_empty(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg(update_repo=""))
    r = updater.check_update(force=True)
    assert r["ok"] is False
    assert r["disabled"] is True
    assert not r["update_available"]


def test_check_update_network_error(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def boom(repo):
        raise TimeoutError("timed out")

    monkeypatch.setattr(updater, "_fetch_latest", boom)
    r = updater.check_update(force=True)
    assert r["ok"] is False
    assert r["disabled"] is False
    assert "更新失败" in r["error"]


def test_check_update_cache_and_force(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fake(repo):
        calls.append(repo)
        return _release("9.9.9")

    monkeypatch.setattr(updater, "_fetch_latest", fake)
    updater.check_update(force=True)
    updater.check_update(force=False)   # 命中缓存，不再请求网络
    updater.check_update(force=True)    # 强制刷新，再次请求
    assert len(calls) == 2


def test_check_update_interval_zero_always_refetches(monkeypatch):
    """interval=0（关闭自动检查）时结果不缓存，任何检查都重新请求网络。"""
    calls: list[str] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg(update_check_interval_hours=0))

    def fake(repo):
        calls.append(repo)
        return _release("1.0.0")

    monkeypatch.setattr(updater, "_fetch_latest", fake)
    updater.check_update(force=True)
    updater.check_update(force=False)   # interval=0 不命中缓存 → 再次请求
    assert len(calls) == 2


# ── handle_auto_update（发现新版本仅通知，按版本去重）────────
class _FakeTray:
    def __init__(self):
        self.notifications: list[tuple[str, str]] = []

    def notify(self, title, message):
        self.notifications.append((title, message))


def test_handle_auto_update_notifies(monkeypatch):
    """发现新版本仅通知（托盘 + webhook 附带公告摘要），不触发下载。"""
    tray = _FakeTray()
    sent: list[tuple] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.11.0", body="新增功能A；修复B"))
    monkeypatch.setattr("notify.send_webhook",
                        lambda url, payload: sent.append((url, payload)) or True)
    calls: list[tuple] = []
    monkeypatch.setattr(updater, "start_download",
                        lambda: calls.append(()) or (True, ""))

    r = updater.handle_auto_update(tray=tray, webhook="https://example.com/hook")
    assert r["update_available"] is True
    # 托盘已通知、webhook 带更新公告摘要、不触发下载
    assert len(tray.notifications) == 1
    assert "有新版本" in tray.notifications[0][0]
    assert len(sent) == 1
    assert sent[0][1]["event"] == "update_available"
    assert "新增功能A" in sent[0][1]["summary"]
    assert calls == []


def test_handle_auto_update_dedupes_by_version(monkeypatch):
    """同一版本只通知一次（去重），第二次调用不再通知。"""
    tray = _FakeTray()
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.11.0"))

    updater.handle_auto_update(tray=tray, webhook="")
    updater.handle_auto_update(tray=tray, webhook="")
    assert len(tray.notifications) == 1
    # 已是本地版本 → 不通知
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release(updater.__version__))
    updater.handle_auto_update(tray=tray, webhook="")
    assert len(tray.notifications) == 1


def test_handle_auto_update_webhook(monkeypatch):
    """webhook 通知含更新公告摘要与版本信息。"""
    sent: list[tuple] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.11.0"))

    def fake_webhook(url, payload):
        sent.append((url, payload))
        return True

    monkeypatch.setattr("notify.send_webhook", fake_webhook)
    updater.handle_auto_update(tray=None, webhook="https://example.com/hook")
    assert len(sent) == 1
    assert sent[0][0] == "https://example.com/hook"
    assert sent[0][1]["event"] == "update_available"
    assert sent[0][1]["latest_version"] == "0.11.0"


# ── 自动更新：资产解析 / 下载 / 应用 ─────────────────────────
def test_asset_info_picks_win64_zip():
    data = {"assets": [
        {"name": "Tavily-v1.0.0-win64.zip", "browser_download_url": "https://x/z.zip", "size": 12345},
        {"name": "Tavily-v1.0.0-linux.tar.gz", "browser_download_url": "https://x/z.tgz", "size": 999},
        {"name": "source.zip", "browser_download_url": "https://x/s.zip", "size": 1},
    ]}
    a = updater._asset_info(data)
    assert a == {"asset_name": "Tavily-v1.0.0-win64.zip",
                 "asset_url": "https://x/z.zip", "asset_size": 12345}


def test_asset_info_none():
    assert updater._asset_info({"assets": []}) == {"asset_name": "", "asset_url": "", "asset_size": 0}
    assert updater._asset_info({}) == {"asset_name": "", "asset_url": "", "asset_size": 0}


def test_check_update_includes_asset_and_can_auto(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.11.0"))
    r = updater.check_update(force=True)
    assert r["asset_name"] == "Tavily-v0.11.0-win64.zip"
    assert r["asset_url"].startswith("https://")
    assert r["asset_size"] == 12345
    assert r["can_auto_update"] is False  # 测试环境非打包版
    assert r["version_type"] in ("stable", "beta")


def test_version_type():
    assert updater._version_type() in ("stable", "beta")
    assert updater._version_type() == ("beta" if "-" in updater.__version__ else "stable")


def test_can_auto_update_false_in_dev():
    assert updater.can_auto_update() is False


def test_download_status_initial_idle():
    assert updater.get_download_status()["state"] == "idle"


def test_start_download_rejected_not_frozen(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: False)
    ok, err = updater.start_download()
    assert ok is False
    assert "打包版" in err


def test_download_update_success(monkeypatch, tmp_path):
    import zipfile
    from pathlib import Path

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
            zf.writestr("Tavily/_internal/foo.py", b"foo")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "done"
    assert st["version"] == "1.0.0"
    assert (Path(st["path"]) / "Tavily.exe").is_file()
    assert (Path(st["path"]) / "_internal").is_dir()


def test_download_update_no_asset(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda repo: _release("1.0.0", asset=False))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "打包产物" in st["error"]


def test_download_update_size_mismatch(monkeypatch, tmp_path):
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"exe")
        return 100  # 与 asset_size=12345 不符

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    assert updater.get_download_status()["state"] == "error"


def test_apply_update_rejected_not_frozen(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: False)
    r = updater.apply_update()
    assert r["ok"] is False
    assert "打包版" in r["error"]


def test_apply_update_requires_done(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    r = updater.apply_update()
    assert r["ok"] is False
    assert "未就绪" in r["error"]


# ── 更新公告（一次性读取）────────────────────────────────────
def test_read_announcement_none(monkeypatch, tmp_path):
    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    assert updater.read_announcement() is None


def test_read_announcement_returns_and_removes(monkeypatch, tmp_path):
    import json as _json

    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    (tmp_path / "last-update.json").write_text(
        _json.dumps({"version": "0.9.4", "body": "本次更新说明", "applied_at": 123.0}),
        encoding="utf-8")
    ann = updater.read_announcement()
    assert ann == {"version": "0.9.4", "body": "本次更新说明", "applied_at": 123.0}
    # 一次性：读取后文件被删除
    assert not (tmp_path / "last-update.json").exists()
    assert updater.read_announcement() is None

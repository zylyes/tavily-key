"""app/updater.py GitHub 更新检查单元测试。"""
import pytest
import updater


@pytest.fixture(autouse=True)
def reset_updater_state():
    """每个测试前重置 updater 模块级缓存、已通知版本与下载状态（全局状态隔离）。"""
    updater._cached_result = None
    updater._cached_ts = 0.0
    updater._cached_is_error = False
    updater._cached_repo = ""
    updater._notified_version = None
    updater._dl_thread = None
    updater._dl_epoch = 0
    updater._dl.update(state="idle", received=0, total=0, error="", version="",
                       path="", body="")
    updater._pause_event.clear()
    updater._cancel_event.clear()
    updater._pending_open_notice = ""
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


def test_is_newer_pre_release_numeric():
    """pre-release 后缀数字段按数值比较（beta.10 > beta.9），标签按优先级。"""
    # 数值位数：此前字符串比较会把 beta.10 判为小于 beta.9（更新通知静默丢失）
    assert updater._is_newer("0.9.2-beta.10", "0.9.2-beta.9")
    assert updater._is_newer("0.9.2-beta.9", "0.9.2-beta.8")
    assert not updater._is_newer("0.9.2-beta.9", "0.9.2-beta.10")
    # 标签优先级：alpha < beta < rc（跨标签数值不参与）
    assert updater._is_newer("0.9.2-rc.1", "0.9.2-beta.9")
    assert updater._is_newer("0.9.2-beta.1", "0.9.2-alpha.9")
    assert not updater._is_newer("0.9.2-alpha.9", "0.9.2-beta.1")
    # 正式版 > 任何 pre-release
    assert updater._is_newer("0.9.2", "0.9.2-rc.99")
    assert not updater._is_newer("0.9.2-rc.99", "0.9.2")
    # 无数字后缀（beta）视为 beta.0
    assert updater._is_newer("0.9.2-beta.1", "0.9.2-beta")
    assert not updater._is_newer("0.9.2-beta", "0.9.2-beta.1")


# ── check_update ────────────────────────────────────────────
def test_check_update_success(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))
    r = updater.check_update(force=True)
    assert r["ok"] is True
    assert r["disabled"] is False
    assert r["update_available"] is True
    assert r["current_version"] == updater.__version__
    assert r["latest_version"] == "0.13.5"
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
    """发现新版本仅通知（窗口未打开时托盘 + webhook 附带公告摘要），不触发下载。"""
    tray = _FakeTray()
    sent: list[tuple] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5", body="新增功能A；修复B"))
    monkeypatch.setattr("notify.send_webhook",
                        lambda url, payload: sent.append((url, payload)) or True)
    calls: list[tuple] = []
    monkeypatch.setattr(updater, "start_download",
                        lambda: calls.append(()) or (True, ""))

    r = updater.handle_auto_update(tray=tray, webhook="https://example.com/hook",
                                   window_open=False)
    assert r["update_available"] is True
    # 托盘已通知、webhook 带更新公告摘要、不触发下载
    assert len(tray.notifications) == 1
    assert "有新版本" in tray.notifications[0][0]
    assert len(sent) == 1
    assert sent[0][1]["event"] == "update_available"
    assert "新增功能A" in sent[0][1]["summary"]
    assert calls == []
    # 窗口未打开时标记待展示公告（系统通知点击后前端消费）
    assert updater.consume_open_notice() == "0.13.5"


def test_handle_auto_update_dedupes_by_version(monkeypatch):
    """同一版本只通知一次（去重），第二次调用不再通知。"""
    tray = _FakeTray()
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))

    updater.handle_auto_update(tray=tray, webhook="", window_open=False)
    updater.handle_auto_update(tray=tray, webhook="", window_open=False)
    assert len(tray.notifications) == 1
    # 已是本地版本 → 不通知
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release(updater.__version__))
    updater.handle_auto_update(tray=tray, webhook="", window_open=False)
    assert len(tray.notifications) == 1


def test_handle_auto_update_window_open_skips_tray(monkeypatch):
    """主窗口打开时自动检查到新版本：不弹系统托盘气泡（前端轮询显示右下角通知），webhook 照常。"""
    tray = _FakeTray()
    sent: list[tuple] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))
    monkeypatch.setattr("notify.send_webhook",
                        lambda url, payload: sent.append((url, payload)) or True)

    r = updater.handle_auto_update(tray=tray, webhook="https://example.com/hook",
                                   window_open=True)
    assert r["update_available"] is True
    assert tray.notifications == []          # 窗口打开 → 不弹系统通知
    assert len(sent) == 1                     # webhook 不受影响
    assert updater.consume_open_notice() == ""   # 不标记待展示公告


def test_handle_auto_update_webhook(monkeypatch):
    """webhook 通知含更新公告摘要与版本信息。"""
    sent: list[tuple] = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))

    def fake_webhook(url, payload):
        sent.append((url, payload))
        return True

    monkeypatch.setattr("notify.send_webhook", fake_webhook)
    updater.handle_auto_update(tray=None, webhook="https://example.com/hook")
    assert len(sent) == 1
    assert sent[0][0] == "https://example.com/hook"
    assert sent[0][1]["event"] == "update_available"
    assert sent[0][1]["latest_version"] == "0.13.5"


def test_mark_consume_open_notice():
    """系统通知点击标记：mark 后可被 consume 一次性读取并清除。"""
    updater.mark_open_notice("0.13.5")
    assert updater.consume_open_notice() == "0.13.5"
    assert updater.consume_open_notice() == ""   # 已清除
    updater.mark_open_notice("")
    assert updater.consume_open_notice() == ""   # 空版本不残留


# ── 自动更新：资产解析 / 下载 / 应用 ─────────────────────────
def test_asset_info_picks_win64_zip():
    data = {"assets": [
        {"name": "Tavily-v1.0.0-win64.zip", "browser_download_url": "https://x/z.zip", "size": 12345},
        {"name": "Tavily-v1.0.0-linux.tar.gz", "browser_download_url": "https://x/z.tgz", "size": 999},
        {"name": "source.zip", "browser_download_url": "https://x/s.zip", "size": 1},
    ]}
    a = updater._asset_info(data)
    assert a["asset_name"] == "Tavily-v1.0.0-win64.zip"
    assert a["asset_url"] == "https://x/z.zip"
    assert a["asset_size"] == 12345
    assert a["digest"] == ""   # 数据未提供 digest


def test_asset_info_picks_digest():
    data = {"assets": [
        {"name": "Tavily-v1.0.0-win64.zip", "browser_download_url": "https://x/z.zip",
         "size": 12345, "digest": "sha256:abcd"},
    ]}
    assert updater._asset_info(data)["digest"] == "sha256:abcd"


def test_asset_info_none():
    assert updater._asset_info({"assets": []}) == {"asset_name": "", "asset_url": "", "asset_size": 0, "digest": ""}
    assert updater._asset_info({}) == {"asset_name": "", "asset_url": "", "asset_size": 0, "digest": ""}


def test_check_update_includes_asset_and_can_auto(monkeypatch):
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))
    r = updater.check_update(force=True)
    assert r["asset_name"] == "Tavily-v0.13.5-win64.zip"
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


# ── 暂停 / 继续 / 取消 ──────────────────────────────────────
def test_pause_resume_rejected_when_idle():
    ok, err = updater.pause_download()
    assert ok is False and "下载" in err
    ok, err = updater.resume_download()
    assert ok is False and "下载" in err
    ok, err = updater.cancel_download()
    assert ok is False and "下载" in err


def test_pause_resume_flow(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    updater._dl.update(state="downloading", received=10, total=100)
    ok, _ = updater.pause_download()
    assert ok is True
    assert updater._pause_event.is_set()
    assert updater.get_download_status()["state"] == "paused"
    # 已暂停时重复暂停应失败
    ok, _ = updater.pause_download()
    assert ok is False
    ok, _ = updater.resume_download()
    assert ok is True
    assert not updater._pause_event.is_set()
    assert updater.get_download_status()["state"] == "downloading"
    # 已继续时重复继续应失败
    ok, _ = updater.resume_download()
    assert ok is False


def test_cancel_download_flow(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    updater._dl.update(state="paused", received=10, total=100)
    ok, _ = updater.cancel_download()
    assert ok is True
    assert updater._cancel_event.is_set()
    assert not updater._pause_event.is_set()
    assert updater.get_download_status()["state"] == "cancelled"
    # 已取消后再次取消应失败（state 已变为 cancelled）
    ok, _ = updater.cancel_download()
    assert ok is False


def test_download_file_cancel_raises(monkeypatch, tmp_path):
    """取消时 _download_file 应抛出 _DownloadCancelled 并中断写入。"""

    class FakeResp:
        def __init__(self):
            self.headers = {"Content-Length": "100"}
            self._calls = 0
            self._closed = False

        def read(self, size):
            self._calls += 1
            if self._calls == 1:
                return b"x" * size
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._closed = True
            return False

    def fake_urlopen(req, timeout):
        return FakeResp()

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    # 首次 read 后设置取消：下一次循环检测到取消 → 抛异常
    def fake_set_dl(**kw):
        if kw.get("received"):
            updater._cancel_event.set()

    monkeypatch.setattr(updater, "_set_dl", fake_set_dl)
    with pytest.raises(updater._DownloadCancelled):
        updater._download_file("https://example.com/x.zip", tmp_path / "dest.zip")


def test_download_update_cancelled_cleans_up(monkeypatch, tmp_path):
    """取消下载后：临时目录被清理，状态回到 idle。"""
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
        updater._cancel_event.set()   # 下载中途取消
        raise updater._DownloadCancelled()

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "idle"
    # 临时目录已被清理
    assert not list(tmp_path.glob("tavily-update-*"))


def test_start_download_rejects_when_paused(monkeypatch):
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    updater._dl.update(state="paused", received=10, total=100)
    ok, err = updater.start_download()
    assert ok is False
    assert "下载进行中" in err


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


def test_fix_zip_name_restores_gbk_chinese():
    """GBK 文件名被按 CP437 解码的乱码可逆映射还原。"""
    assert updater._fix_zip_name("CLI╩╣╙├") == "CLI使用"
    assert updater._fix_zip_name("Web┐╪╓╞╠¿") == "Web控制台"
    assert updater._fix_zip_name("║╦╨─╣ª─▄/Key┬╓╤»╙δ╜í┐╡╝∞▓Θ.md") == "核心功能/Key轮询与健康检查.md"
    # 正常名/非乱码名原样返回
    assert updater._fix_zip_name("Tavily/Tavily.exe") == "Tavily/Tavily.exe"
    assert updater._fix_zip_name("README.md") == "README.md"
    assert updater._fix_zip_name("") == ""


def test_download_update_fixes_mojibake_zip_names(monkeypatch, tmp_path):
    """解压 release zip 时，乱码中文文件名被修复为正确中文名。"""
    import zipfile
    from pathlib import Path

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
            # 模拟 release zip 内文件名已是乱码（GBK 字节按 CP437 解码的结果）
            zf.writestr("Tavily/_internal/docs/wiki/CLI╩╣╙├/CLI╩╣╙├.md",
                        "# CLI 使用".encode())
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "done"
    base = Path(st["path"])
    assert (base / "Tavily.exe").is_file()
    fixed_md = base / "_internal" / "docs" / "wiki" / "CLI使用" / "CLI使用.md"
    assert fixed_md.is_file(), "乱码文件名应被修复为正确中文名"
    assert fixed_md.read_text(encoding="utf-8") == "# CLI 使用"


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


# ── 安全：解压路径穿越 / 资产文件名 / 资产 URL / 顶层目录白名单 ──

def test_download_update_zip_slip_rejected(monkeypatch, tmp_path):
    """Zip Slip：条目含 .. 时拒绝解压，且不写出临时目录之外、临时目录被清理。"""
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
            zf.writestr("../../evil.txt", b"pwned")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "路径" in st["error"]
    assert not (tmp_path / "evil.txt").exists()          # 未写出临时目录外
    assert not list(tmp_path.glob("tavily-update-*"))    # 临时目录已清理


def test_download_update_zip_absolute_path_rejected(monkeypatch, tmp_path):
    """Zip Slip：绝对路径（盘符）条目拒绝解压。"""
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("C:/evil.txt", b"pwned")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "路径" in st["error"]


def test_download_update_asset_name_traversal_rejected(monkeypatch, tmp_path):
    """资产文件名含路径分隔符（..\）时拒绝，不逃逸临时目录。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fetch(repo):
        r = _release("1.0.0")
        r["asset_name"] = "Tavily-..\\..\\evil-win64.zip"
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "资产文件名非法" in st["error"]
    assert not (tmp_path / "evil-win64.zip").exists()


def test_download_update_asset_url_http_rejected(monkeypatch, tmp_path):
    """资产 URL 非 https 时拒绝下载。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fetch(repo):
        r = _release("1.0.0")
        r["asset_url"] = ("http://github.com/zylyes/tavily-key/releases/download/"
                          "v1.0.0/Tavily-v1.0.0-win64.zip")
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "仅支持 https" in st["error"]


def test_download_update_asset_url_non_github_rejected(monkeypatch, tmp_path):
    """资产 URL 主机非 GitHub 时拒绝下载（SSRF 封堵）。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fetch(repo):
        r = _release("1.0.0")
        r["asset_url"] = "https://evil.example.com/x.zip"
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "非 GitHub" in st["error"]

def test_download_update_asset_url_github_subdomain_rejected(monkeypatch, tmp_path):
    """资产 URL 为 *.github.com 子域（非资产 CDN）时拒绝——白名单精确匹配而非宽松后缀。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fetch(repo):
        r = _release("1.0.0")
        r["asset_url"] = "https://api.github.com/repos/a/b/releases/download/v1.0.0/a.zip"
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "非 GitHub 域名" in st["error"]


def test_download_update_asset_url_evil_suffix_rejected(monkeypatch, tmp_path):
    """资产 URL 以 evilgithubusercontent.com 结尾时拒绝——带点后缀防宽松匹配。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def fetch(repo):
        r = _release("1.0.0")
        r["asset_url"] = "https://evilgithubusercontent.com/a.zip"
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "非 GitHub 域名" in st["error"]


def test_safe_zip_target_rejects_dot_segments():
    """条目 '.' / 'a/.'（点段）拒绝——避免解压目标落到目录本身。"""
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp())
    for bad in [".", "a/.", "./a", ".\\a", "a/b/."]:
        try:
            updater._safe_zip_target(root, bad)
            raise AssertionError(f"应拒绝 {bad!r}")
        except RuntimeError:
            pass

def test_download_update_top_dir_metachar_rejected(monkeypatch, tmp_path):
    """顶层目录名含 cmd 元字符时拒绝（防 apply_update.bat 注入）。"""
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily & Co/Tavily.exe", b"fake-exe")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "顶层目录名" in st["error"]


def test_download_update_picks_dir_with_exe(monkeypatch, tmp_path):
    """多顶层目录时优先选择含 Tavily.exe 的目录。"""
    import zipfile
    from pathlib import Path

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("1.0.0"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("docs/README.md", b"readme")
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "done"
    assert Path(st["path"]).name == "Tavily"


def test_normalize_repo_rejects_illegal():
    """非法 owner/repo（含空格等异常字符）视为未配置。"""
    assert updater._normalize_repo("a b/c d") == ""
    assert updater._normalize_repo("owner%20/repo") == ""
    assert updater._normalize_repo("zylyes/tavily-key") == "zylyes/tavily-key"


# ── 可靠性：错误清理 / 失败短缓存 / 取消竞态 / 去重时机 ──

def test_download_update_size_mismatch_cleans_tmp(monkeypatch, tmp_path):
    """下载失败（非取消）也清理临时目录。"""
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
    assert not list(tmp_path.glob("tavily-update-*")), "失败后临时目录应被清理"


def test_check_update_failure_short_ttl(monkeypatch):
    """失败结果用短 TTL 缓存：600s 内不重试，超过后自动重试成功。"""
    calls = {"n": 0}
    now = [100.0]
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater.time, "monotonic", lambda: now[0])

    def fetch(repo):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("boom")
        return _release("1.0.0")

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    r1 = updater.check_update(force=True)
    assert r1["ok"] is False
    r2 = updater.check_update(force=False)     # 600s 内命中失败短缓存
    assert r2["ok"] is False
    assert calls["n"] == 1
    now[0] += 700                              # 超过失败短缓存 → 自动重试
    r3 = updater.check_update(force=False)
    assert r3["ok"] is True
    assert calls["n"] == 2


def test_start_download_rejects_when_previous_thread_alive(monkeypatch):
    """取消后立即重启：旧线程仍存活时拒绝，避免新旧线程互相覆盖状态。"""
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)

    class _FakeThread:
        def is_alive(self):
            return True

    updater._dl_thread = _FakeThread()
    updater._dl.update(state="idle", received=0, total=0, error="", version="",
                       path="", body="")
    ok, err = updater.start_download()
    assert ok is False
    assert "清理" in err
    assert updater.get_download_status()["state"] == "idle"


def test_download_update_old_epoch_does_not_overwrite(monkeypatch):
    """旧代际任务（被取消后新任务已启动）不覆盖新任务状态。"""
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())

    def boom(repo):
        raise TimeoutError("boom")

    monkeypatch.setattr(updater, "_fetch_latest", boom)
    updater._dl_epoch = 5
    updater._dl.update(state="starting", received=0, total=0, error="", version="",
                       path="", body="")
    updater.download_update(gen=4)
    assert updater.get_download_status()["state"] == "starting", "旧任务不应把状态覆盖为 error"


def test_handle_auto_update_dedupe_only_after_success(monkeypatch):
    """去重标记必须在推送成功之后：全渠道失败不标记，下一轮仍重试。"""
    attempts = {"tray": 0}
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))

    class _FailingTray:
        def notify(self, title, message):
            attempts["tray"] += 1
            raise RuntimeError("tray broken")

    def boom(url, payload):
        raise RuntimeError("webhook down")

    monkeypatch.setattr("notify.send_webhook", boom)
    updater.handle_auto_update(tray=_FailingTray(), webhook="https://hook.invalid/",
                               window_open=False)
    updater.handle_auto_update(tray=_FailingTray(), webhook="https://hook.invalid/",
                               window_open=False)
    assert attempts["tray"] == 2                 # 未去重：每次都尝试推送
    assert updater._notified_version is None

    # webhook 成功 → 标记去重；此后不再推送
    monkeypatch.setattr("notify.send_webhook", lambda url, payload: True)
    updater.handle_auto_update(tray=_FailingTray(), webhook="https://hook.invalid/",
                               window_open=False)
    assert updater._notified_version == "0.13.5"
    updater.handle_auto_update(tray=_FailingTray(), webhook="https://hook.invalid/",
                               window_open=False)
    assert attempts["tray"] == 3                 # 标记后不再推送


# ── P2：缓存按仓库失效 / done 态清理 / SHA-256 / notify_tray / 取消前置 / 清理与回滚 ──
def test_check_update_cache_keyed_by_repo(monkeypatch):
    """切换 update_repo 后旧缓存失效，重新请求网络。"""
    calls: list[str] = []
    current = ["a/b"]
    monkeypatch.setattr(updater, "get_settings",
                        lambda: {"update_repo": current[0], "update_check_interval_hours": 24})
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda repo: calls.append(repo) or _release("1.0.0"))
    updater.check_update(force=False)
    updater.check_update(force=False)      # 命中缓存
    assert len(calls) == 1
    current[0] = "c/d"
    updater.check_update(force=False)      # repo 变化 → 重新请求
    assert len(calls) == 2


def test_start_download_cleans_previous_tmp(monkeypatch, tmp_path):
    """done 态重复下载：先清理上次临时目录再启动新任务。"""
    old_tmp = tmp_path / "tavily-update-old"
    old_tmp.mkdir()
    (old_tmp / "x").write_bytes(b"x")
    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    monkeypatch.setattr(updater, "download_update", lambda gen=None: None)
    updater._dl.update(state="done", received=100, total=100, error="", version="1.0.0",
                       path=str(old_tmp / "extracted" / "Tavily"), body="", tmp=str(old_tmp))
    ok, err = updater.start_download()
    assert ok is True
    assert not old_tmp.exists()                      # 旧临时目录已清理
    assert updater._dl["tmp"] == ""                  # 内部 tmp 已重置（API 不再暴露该字段）


def test_download_update_cancel_before_check(monkeypatch):
    """starting 阶段取消：在网络请求前即退出，无需等待。"""
    called: list = []
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda repo: called.append(repo) or _release("1.0.0"))
    updater._cancel_event.set()
    updater.download_update()
    assert called == []
    assert updater.get_download_status()["state"] == "idle"


def test_download_update_digest_mismatch(monkeypatch, tmp_path):
    """SHA-256 digest 不匹配时拒绝（内容可能被篡改）。"""
    import zipfile

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fetch(repo):
        r = _release("1.0.0")
        r["digest"] = "sha256:" + "0" * 64
        return r

    monkeypatch.setattr(updater, "_fetch_latest", fetch)

    def fake_download(url, dest):
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("Tavily/Tavily.exe", b"fake-exe")
        return 12345

    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    st = updater.get_download_status()
    assert st["state"] == "error"
    assert "SHA-256" in st["error"]


def test_download_update_digest_match(monkeypatch, tmp_path):
    """digest 正确时通过完整性校验并完成解压。"""
    import hashlib
    import io
    import zipfile

    # 固定内容 + 固定时间戳生成 zip 字节，保证摘要稳定
    zinfo = zipfile.ZipInfo("Tavily/Tavily.exe", date_time=(2026, 1, 1, 0, 0, 0))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zinfo, b"fake-exe")
    payload = buf.getvalue()

    monkeypatch.setattr(updater, "get_settings", lambda: _cfg())
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))

    def fetch(repo):
        r = _release("1.0.0")
        r["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        return r

    def fake_download(url, dest):
        dest.write_bytes(payload)
        return 12345

    monkeypatch.setattr(updater, "_fetch_latest", fetch)
    monkeypatch.setattr(updater, "_download_file", fake_download)
    updater.download_update()
    assert updater.get_download_status()["state"] == "done"


def test_handle_auto_update_respects_notify_tray(monkeypatch):
    """配置 notify_tray=False 时，窗口未打开也不弹托盘气泡（webhook 不受影响）。"""
    tray = _FakeTray()
    sent: list = []
    monkeypatch.setattr(updater, "get_settings", lambda: _cfg(notify_tray=False))
    monkeypatch.setattr(updater, "_fetch_latest", lambda repo: _release("0.13.5"))
    monkeypatch.setattr("notify.send_webhook", lambda url, payload: sent.append(url) or True)
    updater.handle_auto_update(tray=tray, webhook="https://example.com/hook", window_open=False)
    assert tray.notifications == []
    assert len(sent) == 1
    assert updater.consume_open_notice() == ""


def test_bat_escape():
    """bat 内 % 双写为 %% 避免变量展开。"""
    assert updater._bat_escape("C:/Program Files/%x%") == "C:/Program Files/%%x%%"
    assert updater._bat_escape("no-percent") == "no-percent"
    assert updater._bat_escape("") == ""


def test_cleanup_after_update_keeps_backup_when_pending(monkeypatch, tmp_path):
    """存在 update-pending.json（上次更新未确认）→ 保留 backup-old、删除标记。"""
    base = tmp_path / "base"
    (base / "backup-old").mkdir(parents=True)
    (base / "backup-old" / "Tavily.exe").write_bytes(b"old")
    (tmp_path / "update-pending.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("paths.base_dir", lambda: base)
    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    updater.cleanup_after_update()
    assert (base / "backup-old").exists()
    assert not (tmp_path / "update-pending.json").exists()


def test_cleanup_after_update_removes_backup_without_pending(monkeypatch, tmp_path):
    """无 update-pending.json（正常启动）→ 清理 backup-old。"""
    base = tmp_path / "base"
    (base / "backup-old").mkdir(parents=True)
    monkeypatch.setattr("paths.base_dir", lambda: base)
    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    updater.cleanup_after_update()
    assert not (base / "backup-old").exists()


def test_apply_update_success_writes_files(monkeypatch, tmp_path):
    """apply 成功：写入 update-pending.json（回滚标记）与 last-update.json（公告），并启动 bat。"""
    import json as _json

    monkeypatch.setattr(updater, "can_auto_update", lambda: True)
    monkeypatch.setattr("paths.runtime_dir", lambda: tmp_path)
    monkeypatch.setattr("mcp_manager.stop", lambda: None)
    monkeypatch.setattr("proxy_manager.stop", lambda: None)
    new_dir = tmp_path / "new"
    new_dir.mkdir(parents=True)
    updater._dl.update(state="done", received=100, total=100, error="",
                       version="1.0.0", path=str(new_dir), body="说明")
    spawned: list = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: spawned.append(args))
    r = updater.apply_update()
    assert r["ok"] is True
    assert (tmp_path / "update-pending.json").exists()
    assert (tmp_path / "last-update.json").exists()
    ann = _json.loads((tmp_path / "last-update.json").read_text(encoding="utf-8"))
    assert ann["version"] == "1.0.0"
    assert spawned and spawned[0][:2] == ["cmd", "/c"]

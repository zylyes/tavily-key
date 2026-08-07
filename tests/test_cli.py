"""CLI 子命令测试（cli.py 此前 0% 覆盖）：add/list/usage/audit/backup/update-check 等。"""
import argparse

import cli
import pytest


class _FakeKey:
    """最小 ApiKey 替身（cmd_usage / cmd_list 展示用）。"""
    def __init__(self, masked, credits_limit=0, credits_used=0, plan="", is_exhausted=False):
        self.masked = masked
        self.is_active = True
        self.request_count = 0
        self.credits_used = credits_used
        self.credits_limit = credits_limit
        self.last_used_at = 0.0
        self.last_error = ""
        self.plan = plan
        self.is_exhausted = is_exhausted
        self.usage_pct = (credits_used / credits_limit * 100) if credits_limit > 0 else 0.0


@pytest.fixture()
def fp(monkeypatch):
    """替换 cli.pool 为可控假对象（避免连真实数据库/网络）。"""
    holder: dict = {}

    class _Fake:
        def add_keys_batch(self, keys):
            holder["added"] = list(keys)
            return len(keys)

        def remove_key(self, key):
            holder["removed"] = key

        def list_keys(self):
            return holder.get("keys", [])

        def deactivate_key(self, masked, reason):
            holder["deact"] = (masked, reason)

        def activate_key(self, masked):
            holder["act"] = masked

        def get_stats(self):
            return {"total_keys": 0, "active_keys": 0}

        def get_recent_logs(self, limit):
            return []

        def check_health_all(self):
            return []

        def sync_usage(self):
            return []

        def get_aggregate(self):
            return {"active_keys": 0, "total_keys": 0, "total_used": 0,
                    "total_limit": 0, "remaining": 0}

        def detect_anomalies(self):
            return []

        def get_usage_trend(self, days, source="", project=""):
            return {"points": [{"requests": 0, "success": 0, "failed": 0,
                                "endpoints": {}}]}

        def project_stats(self, days):
            return {}

    fake = _Fake()
    monkeypatch.setattr(cli, "pool", fake)
    return fake, holder


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# ── add ─────────────────────────────────────────────────────────
def test_cmd_add(fp, capsys):
    fp[1]["added"] = None
    cli.cmd_add(_args(keys=["tvly-aaa"], from_file=None))
    assert fp[1]["added"] == ["tvly-aaa"]
    out = capsys.readouterr().out
    assert "Added 1 key(s)" in out


def test_cmd_add_from_file(fp, capsys, tmp_path):
    f = tmp_path / "keys.txt"
    f.write_text("tvly-aaa111222333\n\n tvly-bbb222333444 \n", encoding="utf-8")
    cli.cmd_add(_args(keys=[], from_file=str(f)))
    assert fp[1]["added"] == ["tvly-aaa111222333", "tvly-bbb222333444"]


def test_cmd_add_no_keys(fp, capsys):
    with pytest.raises(SystemExit):
        cli.cmd_add(_args(keys=[], from_file=None))


# ── remove / deactivate / activate / list ───────────────────────
def test_cmd_remove(fp):
    cli.cmd_remove(_args(key="tvly-aaa"))
    assert fp[1]["removed"] == "tvly-aaa"


def test_cmd_deactivate(fp):
    cli.cmd_deactivate(_args(masked="tvly-***", reason="manual"))
    assert fp[1]["deact"] == ("tvly-***", "manual")


def test_cmd_activate(fp):
    cli.cmd_activate(_args(masked="tvly-***"))
    assert fp[1]["act"] == "tvly-***"


def test_cmd_list_empty(fp, capsys):
    fp[1]["keys"] = []
    cli.cmd_list(_args(active=False))
    assert "No keys in pool" in capsys.readouterr().out


def test_cmd_list_shows_keys(fp, capsys):
    fp[1]["keys"] = [_FakeKey("tvly-aaa1****3333", credits_limit=0, credits_used=0)]
    cli.cmd_list(_args(active=False))
    out = capsys.readouterr().out
    assert "tvly-aaa1****3333" in out
    assert "Total: 1 keys" in out


# ── stats / recent / health ─────────────────────────────────────
def test_cmd_stats_json(fp, capsys):
    cli.cmd_stats(_args())
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["total_keys"] == 0


def test_cmd_recent_empty(fp, capsys):
    cli.cmd_recent(_args(limit=20))
    assert "No recent requests" in capsys.readouterr().out


def test_cmd_health(fp, capsys):
    cli.cmd_health(_args())
    assert "No active keys to check" in capsys.readouterr().out


# ── usage ───────────────────────────────────────────────────────
def test_cmd_usage_no_sync(fp, capsys):
    fp[1]["keys"] = [_FakeKey("tvly-aaa1****3333", credits_limit=1000, credits_used=100)]
    cli.cmd_usage(_args(sync=False, json=False))
    out = capsys.readouterr().out
    assert "tvly-aaa1****3333" in out


def test_cmd_usage_sync(fp, capsys, monkeypatch):
    monkeypatch.setattr(cli, "pool", type("P", (), {
        "sync_usage": lambda self=None, masked=None: [{"masked": "tvly-***", "ok": True, "usage": 0, "limit": 1000,
                                "plan": "free", "recovered": False,
                                "search_usage": 0, "extract_usage": 0, "crawl_usage": 0,
                                "map_usage": 0, "research_usage": 0}],
    })())
    cli.cmd_usage(_args(sync=True, json=False))
    assert "Synced 1/1 key(s)" in capsys.readouterr().out


# ── audit ───────────────────────────────────────────────────────
def test_cmd_audit_clean(fp, capsys, monkeypatch):
    monkeypatch.setattr(cli.pool, "list_keys", lambda: [])
    cli.cmd_audit(_args())
    assert "No anomalies detected" in capsys.readouterr().out


# ── proxy ───────────────────────────────────────────────────────
def test_cmd_proxy(fp, capsys, monkeypatch):
    monkeypatch.setattr("proxy_manager.status",
                        lambda: {"running": False, "host": "0.0.0.0", "port": 8002, "pid": None})
    monkeypatch.setattr("settings.proxy_urls", lambda cfg: {"local": "http://127.0.0.1:8002"})
    monkeypatch.setattr("settings.get_settings", lambda: {"proxy_token": "", "proxy_auto_start": False})
    cli.cmd_proxy(_args())
    out = capsys.readouterr().out
    assert "已停止" in out
    assert "未设置" in out


# ── backup / restore / update-check ─────────────────────────────
def test_cmd_backup(fp, capsys, tmp_path, monkeypatch):
    dest = tmp_path / "b.zip"
    monkeypatch.setattr("backup.backup_to", lambda path=None: dest)
    cli.cmd_backup(_args(path=None))
    assert f"备份完成: {dest}" in capsys.readouterr().out


def test_cmd_restore_ok(fp, capsys, tmp_path, monkeypatch):
    z = tmp_path / "b.zip"
    z.write_bytes(b"x")
    monkeypatch.setattr("backup.restore_from", lambda zp: 5)
    cli.cmd_restore(_args(zip=str(z)))
    assert "已恢复 5 个文件" in capsys.readouterr().out


def test_cmd_restore_error(fp, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("backup.restore_from",
                        lambda zp: (_ for _ in ()).throw(Exception("bad zip")))
    cli.cmd_restore(_args(zip=str(tmp_path / "x.zip")))
    assert "恢复失败" in capsys.readouterr().out


def test_cmd_update_ok(fp, capsys, monkeypatch):
    monkeypatch.setattr("updater.check_update",
                        lambda force=False: {"current_version": "0.10.0", "ok": True,
                                             "latest_version": "0.10.0",
                                             "update_available": False,
                                             "release_url": "", "body": ""})
    cli.cmd_update(_args(force=False, notes=False))
    out = capsys.readouterr().out
    assert "当前版本 : 0.10.0" in out
    assert "已是最新版本" in out

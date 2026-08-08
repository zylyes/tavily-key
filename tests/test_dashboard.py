"""Dashboard API 测试：访问鉴权中间件（对比 constant-time）与设置保存校验。"""
import sys

import dashboard
import pytest
from fastapi.testclient import TestClient


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


# ── 新前端入口（web/dist）─────────────────────────────────────
def test_index_serves_web_dist(client, monkeypatch, tmp_path):
    """GET / 始终返回新前端 web/dist/index.html 的内容（Vue 构建产物），无旧模板回退。"""
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>new-vue-app</html>", encoding="utf-8")
    monkeypatch.setattr(dashboard, "_WEB_DIST", dist)
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == "<html>new-vue-app</html>"
    # 旧前端单页模板已移除，不存在回退路径
    assert not hasattr(dashboard, "DASHBOARD_HTML")


def test_web_dist_missing_raises(tmp_path, monkeypatch):
    """web/dist 缺失（index.html 不存在）时 _web_dist() 明确抛错，不静默回退旧前端。"""
    monkeypatch.setattr(dashboard, "__file__", str(tmp_path / "app" / "dashboard.py"))
    (tmp_path / "web" / "dist").mkdir(parents=True)  # dist 目录存在但无 index.html
    with pytest.raises(FileNotFoundError, match="index.html"):
        dashboard._web_dist()


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


def test_api_mcp_status_masks_token(client, monkeypatch):
    """/api/mcp/status 只回脱敏 mcp_token，不回明文（完整令牌由 /api/settings 提供）。"""
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "", "mcp_token": "supersecret123456"})
    dashboard._api_cache.clear()
    r = client.get("/api/mcp/status")
    body = r.json()
    assert body["token"] == "supe****3456"
    assert "supersecret123456" not in body["token"]
    assert body["token_set"] is True


def test_api_mcp_token_generate(client, monkeypatch, tmp_path):
    """POST /api/mcp/token/generate 生成随机访问令牌并写入设置。"""
    import settings as settings_mod
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_mod, "_cache", None)
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "", "mcp_token": ""})
    r = client.post("/api/mcp/token/generate")
    body = r.json()
    assert body["ok"] is True
    assert body["token"] and len(body["token"]) >= 24
    assert settings_mod.get_settings()["mcp_token"] == body["token"]


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


def test_proxy_status_masks_token(client, monkeypatch):
    """/api/proxy/status 只回脱敏 token，不回明文（完整密钥由 /api/settings 提供）。"""
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "", "proxy_token": "supersecret123456"})
    dashboard._api_cache.clear()
    r = client.get("/api/proxy/status")
    body = r.json()
    assert body["token"] == "supe****3456"
    assert "supersecret123456" not in body["token"]
    assert body["token_set"] is True


def test_proxy_status_token_set_flag(client, monkeypatch):
    """未设置代理密钥时 token 为空串、token_set=False。"""
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "", "proxy_token": ""})
    dashboard._api_cache.clear()
    r = client.get("/api/proxy/status")
    body = r.json()
    assert body["token"] == ""
    assert body["token_set"] is False


def test_restore_calls_reset_runtime_state(client, monkeypatch, tmp_path):
    """恢复备份后重置进程内运行时状态（限流桶/用量缓存清空），DB 可正常重连。"""
    import backup as backup_mod
    import key_pool as kp_mod
    import settings as settings_mod

    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(backup_mod, "runtime_dir", lambda: tmp_path)
    # 先用临时 DB 的 KeyPool 制造内存态（DB 为合法 sqlite）
    kp_mod.KeyPool._instance = None
    p = kp_mod.KeyPool(str(tmp_path / "tavily_keys.db"))
    p.add_key("tvly-aaa111222333")
    p.next_available_key("search")
    assert p._buckets
    monkeypatch.setattr(dashboard, "pool", p)
    # 再备份：此时 db 是真实 sqlite，恢复后仍可重连
    (tmp_path / "config.json").write_text('{"auth_token": ""}', encoding="utf-8")
    (tmp_path / ".tavily-secret.key").write_bytes(b"secret")
    dest = backup_mod.backup_to(tmp_path / "bk.zip")

    class _FakeStop:
        def stop(self):
            return {"ok": True}

    monkeypatch.setattr(dashboard, "mcp_manager", _FakeStop())
    monkeypatch.setattr(dashboard, "proxy_manager", _FakeStop())
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(settings_mod, "_cache", None)

    r = client.post("/api/restore", content=dest.read_bytes())
    assert r.status_code == 200
    assert r.json()["restored"] >= 3
    assert not p._buckets        # 恢复后限流桶已重置
    assert not p._usage_cache
    assert p.list_keys()         # 恢复后 DB 可正常重连（不抛异常）
    kp_mod.KeyPool._instance = None  # 清理单例，避免污染后续测试


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
        def get_usage_trend(self, days, source="", project=""):
            return {"days": days, "points": [{"date": "2026-08-05", "requests": 3, "success": 2, "failed": 1, "credits": 5, "endpoints": {"search": 3}}]}

    monkeypatch.setattr(dashboard, "pool", _Fake())
    r = client.get("/api/usage/trend?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["trend"]["days"] == 7
    assert body["trend"]["points"][0]["requests"] == 3
    # source 筛选透传
    r2 = client.get("/api/usage/trend?days=7&source=proxy")
    assert r2.status_code == 200


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


def test_logs_project_filter(client, monkeypatch):
    """/api/logs 的 project 参数应透传到 query_logs（按项目筛选）。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    dashboard._api_cache.clear()  # 隔离模块级 logs 缓存，避免前序测试污染
    captured: dict = {}

    class _Fake:
        def query_logs(self, **kw):
            captured.update(kw)
            return [], 0

    monkeypatch.setattr(dashboard, "pool", _Fake())
    client.get("/api/logs?project=proj-a")
    assert captured.get("project_id") == "proj-a"
    client.get("/api/logs")
    assert captured.get("project_id") == ""


def test_logs_export_csv(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    class _Fake:
        def query_logs(self, **kw):
            return [{"created_at": 1754400000, "key_masked": "tvly-***", "endpoint": "search", "success": 1,
                     "credits_consumed": 1, "latency_ms": 10.5, "request_id": "rid", "usage_source": "response",
                     "source": "mcp", "project_id": "proj-a", "error_msg": ""}], 1

    monkeypatch.setattr(dashboard, "pool", _Fake())


def test_logs_clear_endpoint(client, monkeypatch):
    """/api/logs/clear 按筛选清理并返回删除条数（透传筛选条件）。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    captured: dict = {}

    class _Fake:
        def clear_logs(self, **kw):
            captured.update(kw)
            return 7

    monkeypatch.setattr(dashboard, "pool", _Fake())
    dashboard._api_cache.clear()
    r = client.post("/api/logs/clear", json={"endpoint": "search", "days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["deleted"] == 7
    assert captured.get("endpoint") == "search"
    assert captured.get("before", 0) > 0  # days=7 → before 为 7 天前时间戳


def test_usage_sync_interval(monkeypatch):
    """自动同步间隔：按配置小时换算；0=关闭；下限 300s 防过频。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"usage_auto_sync_hours": 6})
    assert dashboard._usage_sync_interval() == 6 * 3600
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"usage_auto_sync_hours": 0})
    assert dashboard._usage_sync_interval() == 0.0
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"usage_auto_sync_hours": 1})
    assert dashboard._usage_sync_interval() == 3600.0
    # 缺失配置（旧 config.json）回退默认 6 小时
    monkeypatch.setattr(dashboard, "get_settings", lambda: {})
    assert dashboard._usage_sync_interval() == 6 * 3600


def test_research_retry_endpoint(client, monkeypatch):
    """/api/research/retry 透传 request_id 并返回重试结果。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    captured: dict = {}

    def fake_retry(rid):
        captured["rid"] = rid
        return {"ok": True, "request_id": "new-1", "task": {}}

    monkeypatch.setattr("mcp_server.retry_research_task", fake_retry)
    r = client.post("/api/research/retry", json={"request_id": "old-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["request_id"] == "new-1"
    assert captured["rid"] == "old-1"


def test_research_retry_missing_id(client, monkeypatch):
    """缺 request_id 时返回业务错误（HTTP 200 + ok=False）。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.post("/api/research/retry", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "request_id" in r.json().get("error", "")


def test_docs_endpoints(client, monkeypatch):
    """文档目录树与内容读取端点；路径穿越返回 404。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    r = client.get("/api/docs/tree")
    assert r.status_code == 200
    tree = r.json()["tree"]
    assert isinstance(tree, list)
    # 取第一篇文档验证内容
    if tree:
        first = tree[0]["docs"][0]["path"]
        r2 = client.get(f"/api/docs?path={first}")
        assert r2.status_code == 200
        doc = r2.json()["doc"]
        assert doc["path"] == first and doc["content"]
    # 路径穿越 → 404
    r3 = client.get("/api/docs?path=../../README.md")
    assert r3.status_code == 404


def test_audit_export_zip(client, monkeypatch):
    """审计导出 zip：含全量请求日志 CSV + Key 池状态 + 汇总（无密钥明文）。"""
    import io
    import zipfile

    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})

    class _Fake:
        def query_logs(self, **kw):
            return [{"created_at": 1754400000, "key_masked": "tvly-***", "endpoint": "search", "success": 1,
                     "credits_consumed": 1, "latency_ms": 10.5, "request_id": "rid", "usage_source": "response",
                     "source": "mcp", "project_id": "proj-a", "is_client_error": 0, "error_msg": ""}], 1

        def get_stats(self):
            return {"keys": [], "total_keys": 0, "active_keys": 0, "total_requests": 0,
                    "total_errors": 0, "total_credits": 0, "recent_24h": {}}

        def get_aggregate(self):
            return {"total_keys": 0, "active_keys": 0, "exhausted_count": 0, "total_limit": 0,
                    "total_used": 0, "remaining": 0, "usage_pct": 0}

        def detect_anomalies(self):
            return []

        def list_projects(self):
            return ["proj-a"]

    monkeypatch.setattr(dashboard, "pool", _Fake())
    r = client.get("/api/audit/export.zip")
    assert r.status_code == 200
    assert "application/zip" in r.headers["content-type"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert {"request_log.csv", "pool_status.json", "summary.json"} <= names
        assert b"search" in zf.read("request_log.csv")
        assert b"proj-a" in zf.read("summary.json")
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


# ── _WindowApi.save_backup_as（桌面版「另存为」备份）────────────────
class _FakeWindow:
    """伪造 pywebview Window：记录 create_file_dialog 参数并返回预设结果。"""

    def __init__(self):
        self.dialog_calls: list[dict] = []
        self.pick_result: object | None = None
        self.dialog_error: Exception | None = None

    def create_file_dialog(self, dialog_type, directory="", save_filename="", file_types=()):
        self.dialog_calls.append({
            "dialog_type": dialog_type,
            "directory": directory,
            "save_filename": save_filename,
            "file_types": file_types,
        })
        if self.dialog_error is not None:
            raise self.dialog_error
        return self.pick_result


def _save_backup_api(monkeypatch, win):
    """装配 save_backup_as 测试环境：假窗口 + 假 backup_to，返回 (api, 调用记录)。"""
    import backup as backup_mod

    monkeypatch.setattr(dashboard, "_window", lambda: win)
    calls = []

    def _fake_backup_to(target=None):
        calls.append(str(target))
        return target or "fake.zip"

    monkeypatch.setattr(backup_mod, "backup_to", _fake_backup_to)
    return dashboard._WindowApi(), calls


def test_save_backup_as_default_dir_is_base_dir(monkeypatch):
    """对话框默认目录为程序根目录 base_dir()，类型为保存对话框，默认文件名带 .zip。"""
    import webview
    from paths import base_dir

    win = _FakeWindow()
    win.pick_result = "C:/tmp/backup.zip"
    api, _ = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as()
    assert r["ok"] is True
    assert r["path"] == "C:/tmp/backup.zip"
    call = win.dialog_calls[0]
    assert call["dialog_type"] == webview.SAVE_DIALOG
    assert call["directory"] == str(base_dir())
    assert call["file_types"] == ("ZIP files (*.zip)",)
    assert call["save_filename"].endswith(".zip")


def test_save_backup_as_passes_selected_path_to_backup_to(monkeypatch):
    """用户选定路径（含 .zip）原样传给 backup_to，返回该路径。"""
    win = _FakeWindow()
    win.pick_result = "D:/用户/我的备份/backup.zip"
    api, calls = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as("custom-name.zip")
    assert r["ok"] is True
    assert calls == ["D:/用户/我的备份/backup.zip"]
    assert r["path"] == "D:/用户/我的备份/backup.zip"
    assert win.dialog_calls[0]["save_filename"] == "custom-name.zip"


def test_save_backup_as_appends_zip_extension(monkeypatch):
    """选定路径无扩展名时自动补 .zip 再传给 backup_to。"""
    win = _FakeWindow()
    win.pick_result = "D:/backup-folder/mybackup"
    api, calls = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as()
    assert r["ok"] is True
    assert calls == ["D:/backup-folder/mybackup.zip"]


def test_save_backup_as_cancel_no_backup(monkeypatch):
    """用户取消对话框：返回 cancelled，不调用 backup_to、不产生备份。"""
    win = _FakeWindow()
    win.pick_result = None  # pywebview SAVE_DIALOG 取消返回 None
    api, calls = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as()
    assert r == {"ok": False, "cancelled": True, "error": "已取消保存"}
    assert calls == []


def test_save_backup_as_cancel_empty_list_no_backup(monkeypatch):
    """个别平台取消返回空序列：同样视为取消，不写备份。"""
    win = _FakeWindow()
    win.pick_result = []
    api, calls = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as()
    assert r["ok"] is False and r["cancelled"] is True
    assert calls == []


def test_save_backup_as_dialog_error_returns_error(monkeypatch):
    """对话框异常：返回 ok:False + 错误文案，不写备份。"""
    win = _FakeWindow()
    win.pick_result = None
    win.dialog_error = RuntimeError("dialog boom")
    api, calls = _save_backup_api(monkeypatch, win)
    r = api.save_backup_as()
    assert r["ok"] is False
    assert r["error"] == "dialog boom"
    assert calls == []


def test_save_backup_as_backup_error_returns_error(monkeypatch):
    """文件写入异常：返回 ok:False + 错误文案（不泄露备份内容）。"""
    import backup as backup_mod

    win = _FakeWindow()
    win.pick_result = "C:/tmp/backup.zip"
    api, _ = _save_backup_api(monkeypatch, win)

    def _boom(target=None):
        raise OSError("磁盘空间不足")

    monkeypatch.setattr(backup_mod, "backup_to", _boom)
    r = api.save_backup_as()
    assert r["ok"] is False
    assert r["error"] == "磁盘空间不足"


def test_save_backup_as_no_window_returns_error(monkeypatch):
    """无 WebView 窗口（--server/--mcp 模式）：返回清晰错误，不写备份。"""
    import backup as backup_mod

    monkeypatch.setattr(dashboard, "_window", lambda: None)
    calls = []
    monkeypatch.setattr(backup_mod, "backup_to", lambda target=None: calls.append(target) or target)
    api = dashboard._WindowApi()
    r = api.save_backup_as()
    assert r["ok"] is False
    assert "桌面版" in r["error"]
    assert calls == []


# ── 打包版单实例激活（/api/activate + _activate_window + _try_activate_existing）──
def _fake_socket(connect_ok: bool, timeouts: list | None = None):
    """mock socket.socket：connect_ok=True 连接成功，否则抛 OSError；timeouts 记录 settimeout 值。"""

    class _FakeSock:
        def __init__(self, *a, **k):
            self.addr = None

        def settimeout(self, t):
            if timeouts is not None:
                timeouts.append(t)

        def connect(self, addr):
            self.addr = addr
            if not connect_ok:
                raise OSError("refused")

        def close(self):
            pass

    return _FakeSock


def test_api_activate_returns_ok(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(dashboard, "_activate_window", lambda: True)
    r = client.post("/api/activate")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "activated": True}


def test_api_activate_no_window(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": ""})
    monkeypatch.setattr(dashboard, "_activate_window", lambda: False)
    r = client.post("/api/activate")
    assert r.json() == {"ok": True, "activated": False}


def test_api_activate_requires_token(client, monkeypatch):
    """开启面板鉴权时 /api/activate 同样需要有效令牌。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "sekret"})
    r = client.post("/api/activate")
    assert r.status_code == 401
    r2 = client.post("/api/activate", headers={"X-Auth-Token": "sekret"})
    assert r2.status_code == 200


def test_activate_window_with_hwnd(monkeypatch):
    """有窗口句柄：经 GUI 线程执行，恢复显示 + 模拟 Alt 绕过前台锁定 + 前置聚焦。"""
    monkeypatch.setattr(dashboard, "_HWND", 12345)
    monkeypatch.setattr(dashboard, "_invoke_gui", lambda f: f())

    class _FakeUser32:
        def __init__(self):
            self.calls = []

        def ShowWindow(self, hwnd, cmd):
            self.calls.append(("show", hwnd, cmd))
            return 1

        def keybd_event(self, *a):
            self.calls.append(("keybd", a))
            return None

        def SetForegroundWindow(self, hwnd):
            self.calls.append(("fg", hwnd))
            return 1

        def BringWindowToTop(self, hwnd):
            self.calls.append(("top", hwnd))
            return 1

    fake = _FakeUser32()
    monkeypatch.setattr(dashboard.ctypes, "windll", type("W", (), {"user32": fake})())
    assert dashboard._activate_window() is True
    assert fake.calls == [
        ("show", 12345, 9),          # SW_RESTORE（恢复最小化）
        ("show", 12345, 5),          # SW_SHOW（显示隐藏窗口，如「关闭到托盘」）
        ("keybd", (0x12, 0, 0, 0)),  # VK_MENU down（绕过前台锁定）
        ("keybd", (0x12, 0, 2, 0)),  # VK_MENU up
        ("fg", 12345),               # SetForegroundWindow
        ("top", 12345),              # BringWindowToTop
    ]


def test_activate_window_no_hwnd(monkeypatch):
    """无窗口（server/mcp/proxy 模式 _HWND=0）：返回 False，不触发任何窗口操作。"""
    monkeypatch.setattr(dashboard, "_HWND", 0)
    monkeypatch.setattr(
        dashboard, "_invoke_gui", lambda f: (_ for _ in ()).throw(AssertionError("不应调用"))
    )
    assert dashboard._activate_window() is False


def test_tray_show_uses_force_foreground(monkeypatch):
    """托盘恢复复用 _force_foreground（与单实例激活同一聚焦逻辑）。"""
    monkeypatch.setattr(dashboard, "_HWND", 777)
    calls = []
    monkeypatch.setattr(dashboard, "_force_foreground", lambda h: calls.append(h) or True)
    dashboard._tray_show()
    assert calls == [777]


def test_try_activate_existing_not_frozen(monkeypatch):
    """非打包版（开发环境）：恒不触发单实例检测，也不探测端口。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    assert dashboard._try_activate_existing() is False
    assert dashboard._STARTUP_PORT_BUSY is None


def test_try_activate_existing_port_free(monkeypatch):
    """端口未被占用：无已有实例，返回 False 正常启动，并记录探测结果。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=False))
    assert dashboard._try_activate_existing() is False
    assert dashboard._STARTUP_PORT_BUSY is False


def test_try_activate_existing_activates(monkeypatch):
    """已有实例且窗口已激活：返回 True（调用方将退出本进程）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=True))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "activated": true}'

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _Resp())
    assert dashboard._try_activate_existing() is True


def test_try_activate_existing_not_tavily(monkeypatch):
    """端口被其他程序占用（响应非 Tavily 激活成功）：返回 False，按原逻辑继续。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=True))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": false}'

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _Resp())
    assert dashboard._try_activate_existing() is False


def test_try_activate_existing_error(monkeypatch):
    """请求异常（连接失败等）：不激活，按原逻辑继续。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=True))
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("refused"))
    )
    assert dashboard._try_activate_existing() is False


def test_try_activate_existing_sends_token(monkeypatch):
    """已开启面板鉴权：激活请求必须携带 X-Auth-Token 且指向正确端口。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": "sekret"})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=True))
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "activated": true}'

    def _fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        # add_header 会把键 capitalize()（X-Auth-Token → X-auth-token），
        # 大小写不敏感遍历以贴合实际发送行为
        seen["token"] = next(
            (v for k, v in dict(req.headers).items() if k.lower() == "x-auth-token"),
            None,
        )
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert dashboard._try_activate_existing() is True
    assert seen["url"] == "http://127.0.0.1:8000/api/activate"
    assert seen["token"] == "sekret"


def test_try_activate_existing_probe_short_timeout(monkeypatch):
    """端口探测用 0.25s 短超时：is_port_open 的 1s 超时在端口关闭时会让启动白等 1s。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"port": 8000, "auth_token": ""})
    monkeypatch.setattr(dashboard, "_STARTUP_PORT_BUSY", None)
    timeouts = []
    monkeypatch.setattr("socket.socket", _fake_socket(connect_ok=False, timeouts=timeouts))
    assert dashboard._try_activate_existing() is False
    assert timeouts == [0.25]
    assert dashboard._STARTUP_PORT_BUSY is False


# ── Host 白名单（未设 auth_token 时的 DNS-rebinding / 外部域名防护）──
def test_host_check_rejects_foreign_dotted_host(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "", "domain": ""})
    r = client.get("/api/update/status", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_host_check_allows_localhost(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "", "domain": ""})
    r = client.get("/api/update/status", headers={"Host": "127.0.0.1"})
    assert r.status_code == 200


def test_host_check_allows_bare_hostname(client, monkeypatch):
    """裸主机名（无点，如 TestClient 默认 testserver / NetBIOS 名）不拦截。"""
    monkeypatch.setattr(dashboard, "get_settings", lambda: {"auth_token": "", "domain": ""})
    r = client.get("/api/update/status", headers={"Host": "testserver"})
    assert r.status_code == 200


def test_host_check_allows_configured_domain(client, monkeypatch):
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "", "domain": "https://tavily.example.com"})
    r = client.get("/api/update/status", headers={"Host": "tavily.example.com"})
    assert r.status_code == 200


def test_host_check_skipped_when_token_set(client, monkeypatch):
    """已配置 auth_token 时以令牌鉴权为准，不做 Host 校验（避免破坏自定义部署）。"""
    monkeypatch.setattr(dashboard, "get_settings",
                        lambda: {"auth_token": "sekret", "domain": ""})
    r = client.get("/api/update/status", headers={"Host": "evil.example.com",
                                                  "X-Auth-Token": "sekret"})
    assert r.status_code == 200

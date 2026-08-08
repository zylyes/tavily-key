"""API 路由：Key 池统计 / Key CRUD / 健康检查 / 异常识别 / 官方用量同步。

共享状态（pool / _api_cache）运行时从 dashboard 读取，测试 monkeypatch 生效。
"""
from __future__ import annotations

from fastapi import APIRouter, Body

router = APIRouter()


@router.get("/api/stats")
def api_stats():
    from dashboard import pool

    stats = pool.get_stats()
    stats["logs"] = pool.get_recent_logs(50)
    stats["aggregate"] = pool.get_aggregate()
    stats["anomalies"] = pool.detect_anomalies()
    return stats


@router.post("/api/keys/add")
def api_keys_add(payload: dict = Body(...)):
    from dashboard import _api_cache, pool

    keys = payload.get("keys", [])
    added = pool.add_keys_batch(keys)
    _api_cache.invalidate("logs:")
    return {"ok": True, "added": added}


@router.post("/api/keys/remove")
def api_keys_remove(payload: dict = Body(...)):
    from dashboard import _api_cache, pool

    pool.remove_key(payload["masked"])
    _api_cache.invalidate("logs:")
    return {"ok": True}


@router.post("/api/keys/deactivate")
def api_keys_deactivate(payload: dict = Body(...)):
    from dashboard import _api_cache, pool

    pool.deactivate_key(payload["masked"], payload.get("reason", "manual"))
    _api_cache.invalidate("logs:")
    return {"ok": True}


@router.post("/api/keys/activate")
def api_keys_activate(payload: dict = Body(...)):
    from dashboard import _api_cache, pool

    pool.activate_key(payload["masked"])
    _api_cache.invalidate("logs:")
    return {"ok": True}


@router.post("/api/health")
def api_health():
    from dashboard import pool

    results = pool.check_health_all()
    return {"results": results}


@router.post("/api/health/one")
def api_health_one(payload: dict = Body(...)):
    """健康检查单个 Key（供面板逐个进度展示）。"""
    from dashboard import pool

    masked = (payload.get("masked") or "").strip()
    results = pool.check_health(masked)
    if results:
        return {"ok": True, "result": results[0]}
    k = pool.get_key(masked)
    if k is None:
        return {"ok": True, "result": {"masked": masked, "alive": False, "error": "key not found"}}
    return {"ok": True, "result": {"masked": masked, "alive": False, "skipped": True, "error": "inactive key skipped"}}


@router.get("/api/keys/anomalies")
def api_keys_anomalies():
    """结合本地调用记录与官方用量，识别异常 Key（泄露/耗尽/高错误率/静默/慢）。"""
    from dashboard import pool

    return {"ok": True, "anomalies": pool.detect_anomalies()}


@router.post("/api/keys/usage-sync")
def api_keys_usage_sync():
    """从 Tavily 官方 /usage 同步所有 active key 的 billing cycle 真实用量。"""
    from dashboard import pool

    results = pool.sync_usage()
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "synced": ok_count, "failed": len(results) - ok_count, "results": results}


@router.post("/api/keys/usage-sync/one")
def api_keys_usage_sync_one(payload: dict = Body(...)):
    """更新单个 Key 的官方用量（供面板逐个进度展示）。"""
    from dashboard import pool

    masked = (payload.get("masked") or "").strip()
    k = pool.get_key(masked)
    if k is None:
        return {"ok": True, "result": {"masked": masked, "ok": False, "error": "key not found"}}
    if not k.is_active:
        return {"ok": True, "result": {"masked": masked, "ok": False, "skipped": True, "error": "inactive key skipped"}}
    result = pool.sync_usage_one(masked)
    if result is None:
        result = {"masked": masked, "ok": False, "error": "sync failed"}
    return {"ok": True, "result": result}

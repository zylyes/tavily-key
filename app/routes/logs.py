"""API 路由：请求日志（查询/清理/导出）与请求审计导出。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body
from fastapi.responses import Response

router = APIRouter()


@router.get("/api/logs")
def api_logs(endpoint: str = "", key: str = "", status: str = "", days: int = 0,
             source: str = "", project: str = "", limit: int = 200, offset: int = 0):
    """筛选请求日志（endpoint/key/状态/来源/项目/时间范围），分页返回（短 TTL 缓存）。"""
    from dashboard import _api_cache, _logs_ttl, pool

    # 字符串缓存键：与 _api_cache.invalidate("logs:") 的前缀失效匹配
    cache_key = f"logs:{endpoint}|{key}|{status}|{source}|{project}|{days}|{limit}|{offset}"
    hit = _api_cache.get(cache_key)
    if hit is not None:
        return hit
    since = (time.time() - max(int(days), 0) * 86400) if days else 0.0
    rows, total = pool.query_logs(
        endpoint=endpoint.strip(), key_masked=key.strip(), status=status.strip(),
        source=source.strip(), project_id=project.strip(), since=since,
        limit=max(1, min(int(limit), 1000)), offset=max(0, int(offset)),
    )
    resp = {"ok": True, "logs": rows, "total": total, "limit": int(limit), "offset": max(0, int(offset))}
    _api_cache.set(cache_key, resp, _logs_ttl())
    return resp


@router.post("/api/logs/clear")
def api_logs_clear(payload: dict = Body(...)):
    """按条件清理请求日志（面板「清理日志」按钮；空条件 = 清空全部）。

    筛选条件与 /api/logs 一致（endpoint/key/status/source/project/days），
    返回删除条数；清理后失效日志/趋势 API 缓存，避免残留旧数据。
    """
    from dashboard import _api_cache, pool

    days = max(int(payload.get("days") or 0), 0)
    before = (time.time() - days * 86400) if days else 0.0
    n = pool.clear_logs(
        endpoint=(payload.get("endpoint") or "").strip(),
        key_masked=(payload.get("key") or "").strip(),
        status=(payload.get("status") or "").strip(),
        source=(payload.get("source") or "").strip(),
        project_id=(payload.get("project") or "").strip(),
        before=before,
    )
    _api_cache.invalidate("logs:")
    return {"ok": True, "deleted": n}


@router.get("/api/logs/export.csv")
def api_logs_export(endpoint: str = "", key: str = "", status: str = "", days: int = 0,
                    source: str = "", project: str = ""):
    """按当前筛选导出请求日志为 CSV。"""
    import csv
    import io

    from dashboard import pool

    since = (time.time() - max(int(days), 0) * 86400) if days else 0.0
    rows, _ = pool.query_logs(
        endpoint=endpoint.strip(), key_masked=key.strip(), status=status.strip(),
        source=source.strip(), project_id=project.strip(), since=since, limit=100000,
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "key_masked", "endpoint", "success", "credits", "latency_ms", "request_id", "usage_source", "source", "project_id", "error"])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"])),
            r["key_masked"], r["endpoint"], r["success"], r["credits_consumed"],
            round(r["latency_ms"], 1), r["request_id"], r["usage_source"], r["source"], r["project_id"], r["error_msg"],
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=request_log.csv"},
    )


@router.get("/api/audit/export.zip")
def api_audit_export():
    """导出请求审计包（zip）：全量请求日志 CSV + Key 池状态 + 汇总 JSON。

    供离线审计/对账使用：含 request_id / usage_source / 来源 / 项目归属 / 异常
    标记等全部请求元数据，不含 .tavily-secret.key 与任何密钥明文。
    """
    import csv
    import io
    import json
    import zipfile

    from dashboard import pool

    rows, total = pool.query_logs(limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "time", "key_masked", "endpoint", "success", "credits", "latency_ms",
        "request_id", "usage_source", "source", "project_id", "is_client_error", "error",
    ])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"])),
            r["key_masked"], r["endpoint"], r["success"], r["credits_consumed"],
            round(r["latency_ms"], 1), r["request_id"], r["usage_source"],
            r["source"], r["project_id"], r["is_client_error"], r["error_msg"],
        ])
    # Key 池状态与异常（不含密钥明文，仅 masked）
    stats = pool.get_stats()
    stats["aggregate"] = pool.get_aggregate()
    stats["anomalies"] = pool.detect_anomalies()
    # 汇总
    ok = sum(1 for r in rows if r["success"])
    by_source: dict[str, int] = {}
    by_endpoint: dict[str, int] = {}
    for r in rows:
        s = r["source"] or "unknown"
        by_source[s] = by_source.get(s, 0) + 1
        by_endpoint[r["endpoint"]] = by_endpoint.get(r["endpoint"], 0) + 1
    summary = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "log_entries": total,
        "requests": total,
        "success": ok,
        "failed": total - ok,
        "credits": sum(r["credits_consumed"] for r in rows if r["success"]),
        "by_source": by_source,
        "by_endpoint": by_endpoint,
        "projects": pool.list_projects(),
    }
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("request_log.csv", buf.getvalue())
        zf.writestr("pool_status.json", json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        zf.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return Response(
        content=z.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=tavily-audit.zip"},
    )

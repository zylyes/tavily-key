"""API 路由：用量聚合 / 趋势 / 项目列表。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/usage/aggregate")
def api_usage_aggregate():
    """全池聚合容量：剩余总积分、已用总积分、可用 key 数。"""
    from dashboard import pool

    return {"ok": True, "aggregate": pool.get_aggregate()}


@router.get("/api/usage/trend")
def api_usage_trend(days: int = 7, source: str = "", project: str = ""):
    """按天聚合用量趋势（本地时区），days 1-90；source/project 非空时按来源/项目筛选。"""
    from dashboard import pool

    days = max(1, min(int(days), 90))
    return {"ok": True, "trend": pool.get_usage_trend(days, source.strip(), project.strip())}


@router.get("/api/projects")
def api_projects():
    """请求日志中出现过的项目 ID 列表（供面板项目筛选下拉）。"""
    from dashboard import pool

    return {"ok": True, "projects": pool.list_projects()}

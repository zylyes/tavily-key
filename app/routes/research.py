"""API 路由：Research 任务看板 / 重试 / 面板内置 wiki 文档。"""
from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/research/tasks")
def api_research_tasks(limit: int = 50):
    """Research 任务看板：最近提交的异步任务与状态（带 TTL 缓存与查询上限）。"""
    from mcp_server import list_research_tasks
    return {"ok": True, "tasks": list_research_tasks(limit=max(1, min(int(limit), 200)))}


@router.post("/api/research/retry")
def api_research_retry(payload: dict = Body(...)):
    """用原任务参数重试失败的 Research 任务，返回新提交的 request_id。

    参数来自提交时保存的 research_keys 映射（input/model/高级参数）；
    返回 {ok, request_id, task} 或 {ok: False, error}（HTTP 200，业务错误在 body）。
    """
    from mcp_server import retry_research_task

    request_id = (payload.get("request_id") or "").strip()
    if not request_id:
        return {"ok": False, "error": "request_id 不能为空"}
    return retry_research_task(request_id)


@router.get("/api/docs/tree")
def api_docs_tree():
    """wiki 文档目录树：分类 → 文档列表（面板「文档」视图侧栏）。"""
    from wiki_docs import docs_tree

    return {"ok": True, "tree": docs_tree()}


@router.get("/api/docs")
def api_docs_get(path: str = ""):
    """读取指定 wiki 文档（markdown 原文 + 标题），路径限制在 docs/wiki 内。"""
    from wiki_docs import get_doc

    doc = get_doc((path or "").strip())
    if doc is None:
        return JSONResponse({"ok": False, "error": "文档不存在"}, status_code=404)
    return {"ok": True, "doc": doc}

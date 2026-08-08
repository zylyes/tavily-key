"""API 路由：GitHub 更新检查 / 自动更新（下载、状态、应用、公告）。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


# 无 auth_token 时，变更类端点（download/pause/resume/cancel/apply）仅允许
# 本机回环来源：Host 白名单只挡 DNS-rebinding/外部域名，不挡局域网设备用
# 本机 IP 直连——这些端点能触发下载/结束进程，破坏面大，须限制来源。
def _local_only(request: Request) -> JSONResponse | None:
    """未设置 auth_token 时拒绝非本机来源；返回 None 表示放行。"""
    from settings import get_settings

    if (get_settings().get("auth_token") or "").strip():
        return None  # 已设置 token：按 token 鉴权（auth_middleware）
    client = request.client
    host = (client.host if client else "") or ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return None
    return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden: local only"})


@router.get("/api/update/check")
def api_update_check(force: int = Query(0, ge=0, le=1)):
    """检查 GitHub 最新 release 并返回对比结果（force=1 强制刷新网络）。

    结果带进程内 TTL 缓存（间隔 = update_check_interval_hours），前端
    「检查更新」按钮可传 force=1 拿到即时结果。force 仅接受 0/1（其他值 422）。
    """
    from updater import check_update

    return {"ok": True, "update": check_update(force=bool(force))}


@router.post("/api/update/download")
def api_update_download(request: Request):
    """后台下载最新 release 打包产物（zip），进度经 /api/update/status 轮询。"""
    blocked = _local_only(request)
    if blocked is not None:
        return blocked
    from updater import start_download

    ok, err = start_download()
    return {"ok": ok, "error": err}


@router.get("/api/update/status")
def api_update_status():
    """下载进度：idle/starting/downloading/paused/done/error/cancelled + 已接收/总字节数。"""
    from updater import get_download_status

    return {"ok": True, "status": get_download_status()}


@router.post("/api/update/pause")
def api_update_pause(request: Request):
    """暂停正在进行的下载（保持连接，可继续）。"""
    blocked = _local_only(request)
    if blocked is not None:
        return blocked
    from updater import pause_download

    ok, err = pause_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/resume")
def api_update_resume(request: Request):
    """继续已暂停的下载。"""
    blocked = _local_only(request)
    if blocked is not None:
        return blocked
    from updater import resume_download

    ok, err = resume_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/cancel")
def api_update_cancel(request: Request):
    """取消下载：终止后台线程并清理临时文件。"""
    blocked = _local_only(request)
    if blocked is not None:
        return blocked
    from updater import cancel_download

    ok, err = cancel_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/apply")
def api_update_apply(request: Request):
    """应用已下载的更新：生成重启脚本 → 结束本进程 → 部署新版并启动。

    调用成功后当前进程将被结束，前端提示「正在重启应用」。
    """
    blocked = _local_only(request)
    if blocked is not None:
        return blocked
    from updater import apply_update

    return apply_update()


@router.get("/api/update/announcement")
def api_update_announcement():
    """读取本次更新公告（一次性：读取后清除），供新版本启动后展示更新说明。

    返回 {ok, announcement: {version, body, applied_at} | null}。
    """
    from updater import read_announcement

    return {"ok": True, "announcement": read_announcement()}


@router.get("/api/update/notice-pending")
def api_update_notice_pending():
    """读取系统通知（托盘气泡）点击后待展示公告的标记（一次性读取后清除）。

    主窗口未打开时自动/手动检查到新版本 → 系统通知 → 用户点击后打开主窗口，
    前端轮询本端点，非空 version 表示应弹出更新公告弹窗。
    """
    from updater import consume_open_notice

    return {"ok": True, "version": consume_open_notice()}

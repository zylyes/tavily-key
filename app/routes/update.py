"""API 路由：GitHub 更新检查 / 自动更新（下载、状态、应用、公告）。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/update/check")
def api_update_check(force: int = 0):
    """检查 GitHub 最新 release 并返回对比结果（force=1 强制刷新网络）。

    结果带进程内 TTL 缓存（间隔 = update_check_interval_hours），前端
    「检查更新」按钮可传 force=1 拿到即时结果。
    """
    from updater import check_update

    return {"ok": True, "update": check_update(force=bool(force))}


@router.post("/api/update/download")
def api_update_download():
    """后台下载最新 release 打包产物（zip），进度经 /api/update/status 轮询。"""
    from updater import start_download

    ok, err = start_download()
    return {"ok": ok, "error": err}


@router.get("/api/update/status")
def api_update_status():
    """下载进度：idle/starting/downloading/paused/done/error/cancelled + 已接收/总字节数。"""
    from updater import get_download_status

    return {"ok": True, "status": get_download_status()}


@router.post("/api/update/pause")
def api_update_pause():
    """暂停正在进行的下载（保持连接，可继续）。"""
    from updater import pause_download

    ok, err = pause_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/resume")
def api_update_resume():
    """继续已暂停的下载。"""
    from updater import resume_download

    ok, err = resume_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/cancel")
def api_update_cancel():
    """取消下载：终止后台线程并清理临时文件。"""
    from updater import cancel_download

    ok, err = cancel_download()
    return {"ok": ok, "error": err}


@router.post("/api/update/apply")
def api_update_apply():
    """应用已下载的更新：生成重启脚本 → 结束本进程 → 部署新版并启动。

    调用成功后当前进程将被结束，前端提示「正在重启应用」。
    """
    from updater import apply_update

    return apply_update()


@router.get("/api/update/announcement")
def api_update_announcement():
    """读取本次更新公告（一次性：读取后清除），供新版本启动后展示更新说明。

    返回 {ok, announcement: {version, body, applied_at} | null}。
    """
    from updater import read_announcement

    return {"ok": True, "announcement": read_announcement()}

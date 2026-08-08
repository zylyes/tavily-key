"""API 路由：部署设置 / 开机自启 / 数据备份与恢复。"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


@router.get("/api/settings")
def api_settings_get():
    import autostart
    from dashboard import get_settings
    from settings import mcp_url, public_url

    s = get_settings()
    s["autostart"] = autostart.is_enabled()  # 以注册表实际状态为准
    return {"ok": True, "settings": s, "public_url": public_url(s), "mcp_url": mcp_url(s)}


@router.post("/api/settings")
def api_settings_set(payload: dict = Body(...)):
    import autostart
    from dashboard import _api_cache
    from settings import mcp_url, public_url, validate_patch
    from settings import save as save_settings

    try:
        allowed = validate_patch(payload)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    if "autostart" in allowed:
        autostart.set_enabled(allowed["autostart"])  # 同步写入注册表
    s = save_settings(allowed)
    # 配置可能影响服务地址/端口/令牌：清空全部 API 缓存
    _api_cache.clear()
    return {"ok": True, "settings": s, "public_url": public_url(s), "mcp_url": mcp_url(s)}


@router.get("/api/autostart")
def api_autostart_get():
    import autostart

    return {"ok": True, "enabled": autostart.is_enabled(), "command": autostart.command()}


@router.post("/api/autostart")
def api_autostart_set(payload: dict = Body(...)):
    import autostart

    enabled = bool(payload.get("enabled"))
    autostart.set_enabled(enabled)
    return {"ok": True, "enabled": autostart.is_enabled()}


@router.post("/api/backup")
def api_backup():
    """打包 data/ 关键文件为 zip 下载（配置/Key/密钥/缓存）。"""
    from backup import backup_to

    try:
        dest = backup_to()
        return FileResponse(str(dest), media_type="application/zip", filename=dest.name)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/restore")
async def api_restore(request: Request):
    """从上传的备份 zip 恢复 data/。

    先停 MCP / 搜索代理子进程并释放本进程 DB 连接（Windows 文件占用会阻止
    rename），随后解压；完成后刷新配置缓存，子进程由用户在面板重新启动。
    """
    import tempfile

    from backup import restore_from
    from dashboard import _api_cache, mcp_manager, pool, proxy_manager
    from settings import reload as reload_settings

    try:
        data = await request.body()
        if not data:
            return JSONResponse({"ok": False, "error": "请选择备份文件"}, status_code=400)
        tmp = Path(tempfile.gettempdir()) / f"tavily-restore-{int(time.time())}.zip"
        tmp.write_bytes(data)
        mcp_manager.stop()
        proxy_manager.stop()
        pool.close_all_connections()
        n = restore_from(tmp)
        reload_settings()
        # 恢复后重置进程内运行时状态：限流桶/用量缓存/重计算缓存（数据库可能
        # 换了 key 集合与用量），并广播失效信号让子进程同步清缓存
        pool.reset_runtime_state()
        _api_cache.clear()
        return {"ok": True, "restored": n}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

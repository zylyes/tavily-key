"""API 路由：MCP / 搜索代理服务管理与打包版单实例激活。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/mcp/status")
def api_mcp_status():
    """MCP 服务运行状态 + 访问令牌（脱敏，短 TTL 缓存，吸收 5s 轮询与重复调用）。

    完整 mcp_token 仅由设置接口 /api/settings 提供（设置页输入框回显用），
    状态接口只回脱敏值，避免任何能访问面板的页面把访问令牌明文带出。
    """
    from dashboard import _api_cache, _mask_token, _service_ttl, get_settings, mcp_manager

    hit = _api_cache.get("mcp:status")
    if hit is not None:
        return hit
    resp = {
        "ok": True,
        **mcp_manager.status(),
        "token": _mask_token((get_settings().get("mcp_token") or "").strip()),
        "token_set": bool((get_settings().get("mcp_token") or "").strip()),
    }
    _api_cache.set("mcp:status", resp, _service_ttl())
    return resp


@router.post("/api/mcp/start")
def api_mcp_start():
    from dashboard import _api_cache, mcp_manager

    result = mcp_manager.start()
    _api_cache.invalidate("mcp:")
    result["status"] = mcp_manager.status()
    return result


@router.post("/api/mcp/stop")
def api_mcp_stop():
    from dashboard import _api_cache, mcp_manager

    result = mcp_manager.stop()
    _api_cache.invalidate("mcp:")
    result["status"] = mcp_manager.status()
    return result


@router.post("/api/mcp/token/generate")
def api_mcp_token_generate():
    """生成随机 MCP 访问令牌并保存（面板「生成新密钥」按钮调用）。"""
    import secrets

    from dashboard import _api_cache
    from settings import save as save_settings

    token = secrets.token_urlsafe(24)
    save_settings({"mcp_token": token})
    _api_cache.invalidate("mcp:")
    return {"ok": True, "token": token}


@router.get("/api/proxy/status")
def api_proxy_status():
    """搜索代理运行状态 + 可用地址 + 代理密钥（脱敏，短 TTL 缓存，供面板展示）。

    完整 proxy_token 仅由设置接口 /api/settings 提供（设置页输入框回显用），
    状态接口只回脱敏值，避免任何能访问面板的页面把代理密钥明文带出。
    """
    from dashboard import _api_cache, _mask_token, _service_ttl, get_settings, proxy_manager

    hit = _api_cache.get("proxy:status")
    if hit is not None:
        return hit
    resp = {
        "ok": True,
        **proxy_manager.status(),
        "token": _mask_token((get_settings().get("proxy_token") or "").strip()),
        "token_set": bool((get_settings().get("proxy_token") or "").strip()),
    }
    _api_cache.set("proxy:status", resp, _service_ttl())
    return resp


@router.post("/api/proxy/start")
def api_proxy_start():
    from dashboard import _api_cache, proxy_manager

    result = proxy_manager.start()
    _api_cache.invalidate("proxy:")
    result["status"] = proxy_manager.status()
    return result


@router.post("/api/proxy/stop")
def api_proxy_stop():
    from dashboard import _api_cache, proxy_manager

    result = proxy_manager.stop()
    _api_cache.invalidate("proxy:")
    result["status"] = proxy_manager.status()
    return result


@router.post("/api/proxy/token/generate")
def api_proxy_token_generate():
    """生成随机代理密钥并保存（面板「生成随机密钥」按钮调用）。"""
    import secrets

    from settings import save as save_settings

    token = secrets.token_urlsafe(24)
    save_settings({"proxy_token": token})
    return {"ok": True, "token": token}


@router.post("/api/activate")
def api_activate():
    """打包版单实例激活：已有实例在运行时，新实例调用本端点恢复并前置主窗口。

    返回 activated=True 表示窗口已激活；False 表示当前实例无窗口（如 --server 模式）。
    仅供新实例检测使用（见 _try_activate_existing），正常前端页面不调用。
    """
    from dashboard import _activate_window

    return {"ok": True, "activated": _activate_window()}

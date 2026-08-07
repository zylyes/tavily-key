#!/usr/bin/env python3
"""
FastAPI web dashboard — visualize API key pool usage, add/remove keys.

支持两种部署模式（config.json 中配置）：
  - server: Linux 服务器，绑定域名对外提供服务
  - local : Windows 本地提供服务
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Body, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import autostart
import mcp_manager
import proxy_manager
from cache import TTLCache
from key_pool import KeyPool
from settings import (
    get_settings, save as save_settings, public_url, mcp_url, validate_patch, cache_ttls,
)
from tray import TrayIcon


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：启动时按配置自动拉起 MCP 服务，关闭时清理。"""
    # 启动：开启「随软件启动 MCP 服务」且为网络模式时自动启动。
    # mcp_manager.start() 内部有 1.5s 子进程存活探测，若在 lifespan 里同步
    # 调用会阻塞 uvicorn 就绪，导致面板首次加载 fetch 失败；放后台线程执行。
    if get_settings().get("mcp_auto_start") and mcp_manager.mcp_is_network(get_settings()):
        def _safe_start_mcp():
            try:
                mcp_manager.start()
            except Exception:
                pass

        threading.Thread(target=_safe_start_mcp, daemon=True).start()
    # 启动：开启「随软件启动搜索代理」时自动启动（后台线程，避免阻塞 uvicorn 就绪）
    if get_settings().get("proxy_auto_start"):
        def _safe_start_proxy():
            try:
                proxy_manager.start()
            except Exception:
                pass

        threading.Thread(target=_safe_start_proxy, daemon=True).start()
    # 异常通知后台循环（Webhook / 托盘气泡，去重节流见 notify.check_and_notify）
    notify_stop = threading.Event()
    notify_thread = threading.Thread(
        target=_anomaly_notify_loop, args=(notify_stop,), daemon=True, name="tavily-notify"
    )
    notify_thread.start()
    yield
    notify_stop.set()
    # 关闭：停止由本软件管理的 MCP 服务与搜索代理
    try:
        mcp_manager.stop()
    except Exception:
        pass
    try:
        proxy_manager.stop()
    except Exception:
        pass


# ── 异常通知后台循环（Webhook / 托盘气泡）─────────────────────
# run_app 创建托盘后写入，供通知线程读取（server/无托盘模式为 None）
_TRAY = {"icon": None}


def _anomaly_notify_loop(stop_event: threading.Event) -> None:
    """周期检测异常并通知（首次约 30s 后检查，此后按 notify_interval_minutes）。"""
    from notify import check_and_notify

    interval = max(int(get_settings().get("notify_interval_minutes", 5)), 1) * 60
    first = True
    while not stop_event.wait(30 if first else interval):
        first = False
        try:
            check_and_notify(pool, tray=_TRAY.get("icon"))
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="Tavily Key Pool Dashboard", lifespan=lifespan)
pool = KeyPool()

# API 端点短 TTL 缓存（TTL 见 settings.cache_ttls）：吸收前端高频轮询与重复调用。
# 写操作端点（keys / health / usage-sync / mcp / proxy / settings）会显式失效。
_api_cache = TTLCache(default_ttl=1.0, maxsize=512)


def _service_ttl() -> float:
    return float(cache_ttls().get("service_status", 1.0))


def _logs_ttl() -> float:
    return float(cache_ttls().get("logs", 1.0))


def _mask_token(tok: str) -> str:
    """脱敏展示令牌：仅显示首尾各 4 位，避免状态接口明文回传密钥。"""
    tok = (tok or "").strip()
    if not tok:
        return ""
    if len(tok) <= 8:
        return "****"
    return f"{tok[:4]}****{tok[-4:]}"


def _system_light_theme() -> bool:
    """判断 Windows 系统主题是否为浅色（注册表 AppsUseLightTheme=1）。

    用于 WebView 窗口初始背景色（background_color）跟随主题：浅色系统用
    浅色底、深色系统用深色底，避免启动黑屏。非 Windows / 读取失败回退深色。
    """
    if os.name != "nt":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(value)
        finally:
            winreg.CloseKey(key)
    except Exception:  # noqa: BLE001
        return False


def _window_bg_color() -> str:
    """WebView 窗口初始背景色：优先跟随应用内选择的颜色模式
    （config.json theme_mode: system/light/dark）；light 浅色、dark 深色，
    system 或未设置时跟随系统主题，避免开屏背景与所选主题不一致。"""
    mode = get_settings().get("theme_mode")
    if mode == "light":
        return "#eef1f8"
    if mode == "dark":
        return "#070b14"
    return "#eef1f8" if _system_light_theme() else "#070b14"


def _web_dist() -> Path:
    """新前端（web/，Vite 构建产物）目录：含 index.html，打包后位于
    _MEIPASS/web/dist（见 Tavily.spec datas）；开发时为项目根下的
    web/dist（本文件在 app/ 下，项目根是其父目录的父目录）。

    index.html 缺失时直接抛错：新前端是唯一前端，不静默回退旧版。
    """
    if getattr(sys, "frozen", False):
        dist = Path(sys._MEIPASS) / "web" / "dist"
    else:
        dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"[dashboard] 新前端构建产物缺失: {index} 不存在；"
            "请先构建前端（cd web && npm ci && npm run build，"
            "scripts/build_win.bat 会自动执行），否则服务无法启动。"
        )
    return dist


# dist 在 import 期解析一次（静态资源挂载点必须在 import 期注册）；
# dist 缺失时模块导入直接失败（不静默回退旧前端）。
_WEB_DIST = _web_dist()
if (_WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_WEB_DIST / "assets"), name="web-assets")

# ── 访问鉴权 ───────────────────────────────────────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """设置了 auth_token 时，所有 /api/* 请求都需要携带有效令牌。"""
    import secrets
    token = (get_settings().get("auth_token") or "").strip()
    if token and request.url.path.startswith("/api/"):
        provided = request.headers.get("X-Auth-Token") or ""
        if not provided:
            provided = request.query_params.get("token") or ""
        if not secrets.compare_digest(provided, token):
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index():
    # 每次现读 index.html（文件很小，前端重新构建后免重启即生效）；
    # 显式 no-cache：WebView2 默认缓存本地响应，否则启动时可能加载旧页面
    # 导致启动动画缺失（表现为加载期黑屏/直接跳应用界面）。
    body = (_WEB_DIST / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        body,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/favicon.ico")
def favicon():
    """浏览器标签页图标（与应用图标统一）。"""
    p = _window_icon_path()
    if p.exists():
        return Response(content=p.read_bytes(), media_type="image/x-icon")
    return Response(status_code=404)


_LOGO_CACHE: bytes | None = None
_LOGO_READY = False


def _logo_png() -> bytes | None:
    """把应用图标(.ico)转为 PNG bytes 供页面展示；PIL 不可用时回退原始 ico。"""
    global _LOGO_CACHE, _LOGO_READY
    if not _LOGO_READY:
        _LOGO_READY = True
        p = _window_icon_path()
        if p.exists():
            try:
                import io
                from PIL import Image

                buf = io.BytesIO()
                Image.open(p).convert("RGBA").save(buf, format="PNG")
                _LOGO_CACHE = buf.getvalue()
            except Exception:
                _LOGO_CACHE = None
    if _LOGO_CACHE:
        return _LOGO_CACHE
    p = _window_icon_path()
    if p.exists():
        try:
            return p.read_bytes()  # 回退：直接返回 ico 原始字节
        except Exception:
            return None
    return None


@app.get("/logo.png")
def logo():
    """顶部栏 / 登录层 Logo（与应用图标统一，由 .ico 实时转换）。"""
    data = _logo_png()
    if data is None:
        return Response(status_code=404)
    ctype = "image/png" if data[:4] == b"\x89PNG" else "image/x-icon"
    return Response(content=data, media_type=ctype)


@app.get("/api/stats")
def api_stats():
    stats = pool.get_stats()
    stats["logs"] = pool.get_recent_logs(50)
    stats["aggregate"] = pool.get_aggregate()
    stats["anomalies"] = pool.detect_anomalies()
    return stats


@app.post("/api/keys/add")
def api_keys_add(payload: dict = Body(...)):
    keys = payload.get("keys", [])
    added = pool.add_keys_batch(keys)
    _api_cache.invalidate("logs:")
    return {"ok": True, "added": added}


@app.post("/api/keys/remove")
def api_keys_remove(payload: dict = Body(...)):
    pool.remove_key(payload["masked"])
    _api_cache.invalidate("logs:")
    return {"ok": True}


@app.post("/api/keys/deactivate")
def api_keys_deactivate(payload: dict = Body(...)):
    pool.deactivate_key(payload["masked"], payload.get("reason", "manual"))
    _api_cache.invalidate("logs:")
    return {"ok": True}


@app.post("/api/keys/activate")
def api_keys_activate(payload: dict = Body(...)):
    pool.activate_key(payload["masked"])
    _api_cache.invalidate("logs:")
    return {"ok": True}


@app.post("/api/health")
def api_health():
    results = pool.check_health_all()
    return {"results": results}


@app.post("/api/health/one")
def api_health_one(payload: dict = Body(...)):
    """健康检查单个 Key（供面板逐个进度展示）。"""
    masked = (payload.get("masked") or "").strip()
    results = pool.check_health(masked)
    if results:
        return {"ok": True, "result": results[0]}
    k = pool.get_key(masked)
    if k is None:
        return {"ok": True, "result": {"masked": masked, "alive": False, "error": "key not found"}}
    return {"ok": True, "result": {"masked": masked, "alive": False, "skipped": True, "error": "inactive key skipped"}}


@app.get("/api/keys/anomalies")
def api_keys_anomalies():
    """结合本地调用记录与官方用量，识别异常 Key（泄露/耗尽/高错误率/静默/慢）。"""
    return {"ok": True, "anomalies": pool.detect_anomalies()}


@app.get("/api/usage/aggregate")
def api_usage_aggregate():
    """全池聚合容量：剩余总积分、已用总积分、可用 key 数。"""
    return {"ok": True, "aggregate": pool.get_aggregate()}


@app.get("/api/usage/trend")
def api_usage_trend(days: int = 7, source: str = ""):
    """按天聚合用量趋势（本地时区），days 1-90；source 非空时只统计该来源。"""
    days = max(1, min(int(days), 90))
    return {"ok": True, "trend": pool.get_usage_trend(days, source.strip())}


@app.get("/api/research/tasks")
def api_research_tasks(limit: int = 50):
    """Research 任务看板：最近提交的异步任务与状态（带 TTL 缓存与查询上限）。"""
    from mcp_server import list_research_tasks
    return {"ok": True, "tasks": list_research_tasks(limit=max(1, min(int(limit), 200)))}


@app.get("/api/logs")
def api_logs(endpoint: str = "", key: str = "", status: str = "", days: int = 0,
             source: str = "", limit: int = 200, offset: int = 0):
    """筛选请求日志（endpoint/key/状态/来源/时间范围），分页返回（短 TTL 缓存）。"""
    # 字符串缓存键：与 _api_cache.invalidate("logs:") 的前缀失效匹配
    cache_key = f"logs:{endpoint}|{key}|{status}|{source}|{days}|{limit}|{offset}"
    hit = _api_cache.get(cache_key)
    if hit is not None:
        return hit
    since = (time.time() - max(int(days), 0) * 86400) if days else 0.0
    rows, total = pool.query_logs(
        endpoint=endpoint.strip(), key_masked=key.strip(), status=status.strip(),
        source=source.strip(), since=since, limit=max(1, min(int(limit), 1000)),
        offset=max(0, int(offset)),
    )
    resp = {"ok": True, "logs": rows, "total": total, "limit": int(limit), "offset": max(0, int(offset))}
    _api_cache.set(cache_key, resp, _logs_ttl())
    return resp


@app.get("/api/logs/export.csv")
def api_logs_export(endpoint: str = "", key: str = "", status: str = "", days: int = 0,
                    source: str = ""):
    """按当前筛选导出请求日志为 CSV。"""
    import csv
    import io

    since = (time.time() - max(int(days), 0) * 86400) if days else 0.0
    rows, _ = pool.query_logs(
        endpoint=endpoint.strip(), key_masked=key.strip(), status=status.strip(),
        source=source.strip(), since=since, limit=100000,
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "key_masked", "endpoint", "success", "credits", "latency_ms", "request_id", "usage_source", "source", "error"])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"])),
            r["key_masked"], r["endpoint"], r["success"], r["credits_consumed"],
            round(r["latency_ms"], 1), r["request_id"], r["usage_source"], r["source"], r["error_msg"],
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=request_log.csv"},
    )


@app.post("/api/keys/usage-sync")
def api_keys_usage_sync():
    """从 Tavily 官方 /usage 同步所有 active key 的 billing cycle 真实用量。"""
    results = pool.sync_usage()
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "synced": ok_count, "failed": len(results) - ok_count, "results": results}


@app.post("/api/keys/usage-sync/one")
def api_keys_usage_sync_one(payload: dict = Body(...)):
    """更新单个 Key 的官方用量（供面板逐个进度展示）。"""
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


# ── 设置 ───────────────────────────────────────────────────────
@app.get("/api/settings")
def api_settings_get():
    s = get_settings()
    s["autostart"] = autostart.is_enabled()  # 以注册表实际状态为准
    return {"ok": True, "settings": s, "public_url": public_url(s), "mcp_url": mcp_url(s)}


@app.post("/api/settings")
def api_settings_set(payload: dict = Body(...)):
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


# ── 开机自启（Windows 注册表 Run 键）─────────────────────────
@app.get("/api/autostart")
def api_autostart_get():
    return {"ok": True, "enabled": autostart.is_enabled(), "command": autostart.command()}


@app.post("/api/autostart")
def api_autostart_set(payload: dict = Body(...)):
    enabled = bool(payload.get("enabled"))
    autostart.set_enabled(enabled)
    return {"ok": True, "enabled": autostart.is_enabled()}


# ── MCP 服务管理（面板开关 / 状态 / 地址）───────────────────────
@app.get("/api/mcp/status")
def api_mcp_status():
    """MCP 服务运行状态 + 访问令牌（脱敏，短 TTL 缓存，吸收 5s 轮询与重复调用）。

    完整 mcp_token 仅由设置接口 /api/settings 提供（设置页输入框回显用），
    状态接口只回脱敏值，避免任何能访问面板的页面把访问令牌明文带出。
    """
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


@app.post("/api/mcp/start")
def api_mcp_start():
    result = mcp_manager.start()
    _api_cache.invalidate("mcp:")
    result["status"] = mcp_manager.status()
    return result


@app.post("/api/mcp/stop")
def api_mcp_stop():
    result = mcp_manager.stop()
    _api_cache.invalidate("mcp:")
    result["status"] = mcp_manager.status()
    return result


@app.post("/api/mcp/token/generate")
def api_mcp_token_generate():
    """生成随机 MCP 访问令牌并保存（面板「生成新密钥」按钮调用）。"""
    import secrets

    token = secrets.token_urlsafe(24)
    save_settings({"mcp_token": token})
    _api_cache.invalidate("mcp:")
    return {"ok": True, "token": token}


# ── 搜索代理服务管理（面板开关 / 状态 / 地址）───────────────────
@app.get("/api/proxy/status")
def api_proxy_status():
    """搜索代理运行状态 + 可用地址 + 代理密钥（脱敏，短 TTL 缓存，供面板展示）。

    完整 proxy_token 仅由设置接口 /api/settings 提供（设置页输入框回显用），
    状态接口只回脱敏值，避免任何能访问面板的页面把代理密钥明文带出。
    """
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


@app.post("/api/proxy/start")
def api_proxy_start():
    result = proxy_manager.start()
    _api_cache.invalidate("proxy:")
    result["status"] = proxy_manager.status()
    return result


@app.post("/api/proxy/stop")
def api_proxy_stop():
    result = proxy_manager.stop()
    _api_cache.invalidate("proxy:")
    result["status"] = proxy_manager.status()
    return result


@app.post("/api/proxy/token/generate")
def api_proxy_token_generate():
    """生成随机代理密钥并保存（面板「生成随机密钥」按钮调用）。"""
    import secrets

    token = secrets.token_urlsafe(24)
    save_settings({"proxy_token": token})
    return {"ok": True, "token": token}


@app.post("/api/activate")
def api_activate():
    """打包版单实例激活：已有实例在运行时，新实例调用本端点恢复并前置主窗口。

    返回 activated=True 表示窗口已激活；False 表示当前实例无窗口（如 --server 模式）。
    仅供新实例检测使用（见 _try_activate_existing），正常前端页面不调用。
    """
    return {"ok": True, "activated": _activate_window()}


@app.post("/api/backup")
def api_backup():
    """打包 data/ 关键文件为 zip 下载（配置/Key/密钥/缓存）。"""
    from backup import backup_to

    try:
        dest = backup_to()
        return FileResponse(str(dest), media_type="application/zip", filename=dest.name)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/restore")
async def api_restore(request: Request):
    """从上传的备份 zip 恢复 data/。

    先停 MCP / 搜索代理子进程并释放本进程 DB 连接（Windows 文件占用会阻止
    rename），随后解压；完成后刷新配置缓存，子进程由用户在面板重新启动。
    """
    import tempfile

    from backup import restore_from
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


def _run_server_thread(host: str, port: int):
    """在后台线程启动 uvicorn 服务（供网页套壳窗口使用）。"""
    import threading
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="tavily-server")
    thread.start()
    return server, thread


def _wait_ready(server, timeout: float = 10.0) -> bool:
    """等待服务就绪；端口已被占用（他人已启动）时也视为可用。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if getattr(server, "started", False):
            return True
        if getattr(server, "should_exit", False):
            return False
        time.sleep(0.05)
    return bool(getattr(server, "started", False))


# ── 无边框窗口后端（Windows）────────────────────────────────────
# EdgeChromium(WebView2) 后端未实现 easy_drag，拖动/缩放需自实现：
# ReleaseCapture + SendMessageW(WM_NCLBUTTONDOWN, HT 命中代码)。
#
# ⚠️ 关键：pywebview 6.x 的 js_api 方法在【后台线程】执行，从后台线程发送
# WM_NCLBUTTONDOWN 不会进入移动/缩放模态循环（消息立即返回、窗口不动）。
# 必须把 SendMessageW 调度到 GUI（WinForms UI）线程执行（_invoke_gui）。
WM_NCLBUTTONDOWN = 0xA1
HTCAPTION = 2
_HT_HITS = {
    "left": 10, "right": 11, "top": 12,
    "top-left": 13, "top-right": 14,
    "bottom": 15, "bottom-left": 16, "bottom-right": 17,
}

_HWND = 0  # 真实窗口句柄缓存（GUI 线程捕获，供后台线程使用）


def _window():
    """取当前 pywebview 窗口对象（懒加载 webview，避免 --mcp/--server 模式加载）。"""
    try:
        import webview
        if webview.windows:
            return webview.windows[0]
    except Exception:
        pass
    return None


def _handle_of(native) -> int:
    """IntPtr → int（pythonnet 的 int(IntPtr) 会抛 TypeError，须用 ToInt32）。"""
    try:
        return int(native.Handle.ToInt32())
    except Exception:
        try:
            return int(native.Handle.ToInt64())
        except Exception:
            return 0


def _invoke_gui(func):
    """在 GUI（WinForms UI）线程执行 func 并返回结果。

    pywebview 6.x 的 js_api 方法在后台线程执行；由 WM_NCLBUTTONDOWN 触发的
    移动/缩放模态循环必须在窗口所属线程上运行，后台线程发送会立即返回。
    """
    try:
        import System
        w = _window()
        if w is not None and getattr(w, "native", None) is not None:
            box = {}

            def _run():
                box["v"] = func()

            w.native.Invoke(System.Action(_run))
            return box.get("v")
    except Exception:
        pass
    return func()


class _WindowApi:
    """暴露给前端（js_api）的窗口控制接口（无边框窗口）。"""

    @staticmethod
    def _hwnd() -> int:
        # 优先用 GUI 线程捕获的真实句柄；pywebview Window 无 hwnd 属性，
        # 从 native（WinForms Form）取 Handle；最后回退前台窗口。
        if _HWND:
            return _HWND
        try:
            w = _window()
            if w is not None:
                native = getattr(w, "native", None)
                if native is not None:
                    return _handle_of(native)
        except Exception:
            pass
        return int(ctypes.windll.user32.GetForegroundWindow())

    def _hide_to_tray(self):
        """隐藏主窗口到系统托盘（应用后台继续运行）。"""
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE

    def close(self):
        # 开启「关闭时最小化到托盘」：隐藏窗口而不退出
        if get_settings().get("close_to_tray"):
            self._hide_to_tray()
            return
        ctypes.windll.user32.PostMessageW(self._hwnd(), 0x0010, 0, 0)  # WM_CLOSE

    def minimize(self):
        # 开启「最小化时隐藏到托盘」：隐藏窗口
        if get_settings().get("minimize_to_tray"):
            self._hide_to_tray()
            return
        ctypes.windll.user32.ShowWindow(self._hwnd(), 6)  # SW_MINIMIZE

    def toggle_maximize(self):
        hwnd = self._hwnd()
        if ctypes.windll.user32.IsZoomed(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE

    def start_drag(self):
        hwnd = self._hwnd()

        def _do():
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)

        _invoke_gui(_do)

    def resize(self, direction: str):
        hit = _HT_HITS.get(direction)
        if hit is None:
            return
        hwnd = self._hwnd()

        def _do():
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, hit, 0)

        _invoke_gui(_do)

    def save_backup_as(self, filename: str = ""):
        """「另存为」备份：系统对话框（默认目录 = 程序根目录），备份直接写入所选位置。

        仅桌面 WebView 可用；无窗口（--server/--mcp 模式）时返回清晰错误。
        用户取消返回 {ok: False, cancelled: True, error: '已取消保存'}，不产生备份；
        对话框/写入异常返回 {ok: False, error: str(e)}（仅文件名/路径，不泄露备份内容）。
        """
        import webview

        from backup import backup_to
        from paths import base_dir

        w = _window()
        if w is None:
            return {"ok": False, "error": "仅桌面版支持选择备份保存位置，请运行桌面版应用"}
        if not filename:
            filename = time.strftime("tavily-backup-%Y%m%d-%H%M%S.zip")

        def _pick():
            return w.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=str(base_dir()),
                save_filename=filename,
                file_types=("ZIP files (*.zip)",),
            )

        try:
            result = _invoke_gui(_pick)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        # 用户取消：SAVE_DIALOG 返回 None（个别平台返回空序列）
        if isinstance(result, (list, tuple)):
            if not result:
                return {"ok": False, "cancelled": True, "error": "已取消保存"}
            selected = str(result[0])
        elif result:
            selected = str(result)
        else:
            return {"ok": False, "cancelled": True, "error": "已取消保存"}
        if not selected.lower().endswith(".zip"):
            selected += ".zip"
        try:
            dest = backup_to(selected)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": str(dest)}


def _work_area() -> tuple[int, int, int, int]:
    """主屏工作区（排除任务栏）: (left, top, width, height)。

    任意线程均可调用；失败时回退常见 1080p 桌面尺寸。
    """
    try:
        work = ctypes.wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)
        w = work.right - work.left
        h = work.bottom - work.top
        if w > 0 and h > 0:
            return work.left, work.top, w, h
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _centered_pos(width: int, height: int) -> tuple[int, int]:
    """计算窗口在主屏工作区居中时的 (x, y) 坐标。"""
    left, top, ww, wh = _work_area()
    return left + max(0, (ww - width) // 2), top + max(0, (wh - height) // 2)


def _center_window(*_args) -> None:
    """将窗口居中于主屏工作区（首次显示时调用，任意线程均可）。"""
    try:
        user32 = ctypes.windll.user32
        # 优先用 GUI 线程捕获的真实句柄；未就绪时从 pywebview window 的
        # native（WinForms Form）取 Handle；最后才回退前台窗口。
        hwnd = _HWND
        if not hwnd:
            try:
                w = _window()
                if w is not None:
                    hwnd = _handle_of(getattr(w, "native", None))
            except Exception:
                pass
        if not hwnd:
            hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return
        x, y = _centered_pos(width, height)
        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)
    except Exception:
        pass


def _window_icon_path() -> Path:
    """窗口图标路径：打包后从 _MEIPASS 读取，开发时为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "tavily.ico"
    return Path(__file__).resolve().parent.parent / "assets" / "tavily.ico"


def _apply_window_icon(edge_self) -> None:
    """给窗口设置应用图标（pywebview 在 Windows 不支持 icon 参数，需自行设置）。"""
    try:
        ico = _window_icon_path()
        if not ico.exists():
            return
        user32 = ctypes.windll.user32
        hwnd = _handle_of(edge_self.form)
        if not hwnd:
            return
        h_big = user32.LoadImageW(None, str(ico), 1, 32, 32, 0x10)     # IMAGE_ICON, LR_LOADFROMFILE
        h_small = user32.LoadImageW(None, str(ico), 1, 16, 16, 0x10)
        if h_big:
            user32.SendMessageW(hwnd, 0x0080, 1, h_big)    # WM_SETICON, ICON_BIG
        if h_small:
            user32.SendMessageW(hwnd, 0x0080, 0, h_small)  # WM_SETICON, ICON_SMALL
    except Exception:
        pass


def _force_foreground(hwnd: int) -> bool:
    """把指定窗口恢复到前台并聚焦（单实例激活 / 托盘恢复共用）。

    组合处理三个 Windows 细节：
      - SW_RESTORE 只能恢复最小化窗口；被 SW_HIDE（关闭到托盘）的窗口需 SW_SHOW 才显示；
      - SetForegroundWindow 受前台锁定（SPI_GETFOREGROUNDLOCKTIMEOUT，默认约 200s）限制，
        后台进程直接调用会被忽略；模拟一次 Alt 键让系统认为本进程收到输入事件即可绕过；
      - BringWindowToTop 兜底把窗口提到 Z 序最前。
    """
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE（恢复最小化）
        user32.ShowWindow(hwnd, 5)   # SW_SHOW（显示被隐藏的窗口，如「关闭到托盘」）
        # 模拟 Alt 键：绕过 Windows 前台锁定，使 SetForegroundWindow 真正生效
        user32.keybd_event(0x12, 0, 0, 0)   # VK_MENU down
        user32.keybd_event(0x12, 0, 2, 0)   # VK_MENU up
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return True
    except Exception:
        return False


def _tray_show() -> None:
    """托盘回调：恢复并前置主窗口。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = _HWND or int(user32.GetForegroundWindow())
        if hwnd:
            _force_foreground(hwnd)
    except Exception:
        pass


def _tray_exit() -> None:
    """托盘回调：退出菜单 → 关闭主窗口（走正常退出流程）。"""
    try:
        if _HWND:
            ctypes.windll.user32.PostMessageW(_HWND, 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


def _activate_window() -> bool:
    """恢复并前置主窗口（打包版单实例激活用）。

    仅桌面窗口模式会捕获 _HWND；server / mcp / proxy 模式恒为 0，返回 False。
    经 _invoke_gui 调度到 GUI（WinForms）线程执行 _force_foreground，确保窗口
    真正显示并聚焦到前台（后台线程 SetForegroundWindow 可能被系统忽略）。
    """
    hwnd = _HWND
    if not hwnd:
        return False
    return bool(_invoke_gui(lambda: _force_foreground(hwnd)))


def _harden_webview() -> None:
    """WebView2 加固：禁用扩展、消除权限/密码/自动填充弹窗，并应用窗口图标。"""
    try:
        import webview.platforms.edgechromium as edge
    except Exception:
        return

    _orig_init = edge.EdgeChrome.__init__
    _orig_ready = edge.EdgeChrome.on_webview_ready

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        try:
            extra = " --disable-extensions --disable-component-extensions-with-background-pages"
            props = self.webview.CreationProperties
            if extra not in (props.AdditionalBrowserArguments or ""):
                props.AdditionalBrowserArguments = (props.AdditionalBrowserArguments or "") + extra
        except Exception:
            pass

    def _on_permission(sender, e):
        try:
            from Microsoft.Web.WebView2.Core import (
                CoreWebView2PermissionKind,
                CoreWebView2PermissionState,
            )
            allowed = e.get_PermissionKind() == CoreWebView2PermissionKind.ClipboardRead
            e.set_State(
                CoreWebView2PermissionState.Allow if allowed
                else CoreWebView2PermissionState.Deny
            )
            e.set_Handled(True)
        except Exception:
            pass

    def _patched_ready(self, sender, args):
        global _HWND
        _orig_ready(self, sender, args)
        try:
            core = getattr(sender, "CoreWebView2", None)
            if core is None:
                return
            settings = core.Settings
            try:
                settings.AreBrowserExtensionsEnabled = False
            except Exception:
                pass
            try:
                settings.IsPasswordAutosaveEnabled = False
                settings.IsGeneralAutofillEnabled = False
            except Exception:
                pass
            # 禁用缩放（Ctrl+滚轮）并强制 100% 缩放，避免 DPI/缩放导致内容显示不全
            try:
                settings.IsZoomControlEnabled = False
            except Exception:
                pass
            try:
                core.ZoomFactor = 1.0
            except Exception:
                pass
            # 事件订阅需持有实例方法引用，防止委托被 GC 回收
            self._on_permission = _on_permission
            core.PermissionRequested += self._on_permission
        except Exception:
            pass
        # 确保 WebView2 控件始终填满窗体客户区（防止内容被窗口边缘截断）
        try:
            import System.Windows.Forms as WinForms  # noqa: N812
            self.webview.Dock = WinForms.DockStyle.Fill
            self.webview.BringToFront()
            if self.form is not None:
                self.webview.Size = self.form.ClientSize
        except Exception:
            pass
        # 在 GUI 线程捕获真实窗口句柄（供后台线程的 _WindowApi 使用）
        try:
            _HWND = _handle_of(self.form)
        except Exception:
            pass
        _apply_window_icon(self)

    edge.EdgeChrome.__init__ = _patched_init
    edge.EdgeChrome.on_webview_ready = _patched_ready


def _keep_alive() -> None:
    """浏览器回退模式下保持服务进程常驻。"""
    import time

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _cleanup_stale_mei() -> None:
    """清理 %TEMP% 下残留的 PyInstaller onefile 临时目录（_MEI*）。

    打包版退出时可能残留 _MEI 目录（见 _quiet_exit），下次启动时统一清理：
      - 仅处理修改时间早于 30 分钟前的目录，避开刚解压的其他实例；
      - 运行中实例的 _MEI 因 DLL 被占用删除必然失败，删除失败即跳过，
        天然不会误删正在使用的目录。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import shutil
        import tempfile
        import time

        base = Path(tempfile.gettempdir())
        cutoff = time.time() - 30 * 60
        try:
            mine = str(Path(sys._MEIPASS).resolve())
        except Exception:  # noqa: BLE001
            mine = None
        for p in base.glob("_MEI*"):
            if not p.is_dir():
                continue
            try:
                if mine is not None and str(p.resolve()) == mine:
                    continue  # 当前进程自己的临时目录
            except OSError:
                continue
            try:
                if p.stat().st_mtime > cutoff:
                    continue  # 可能刚解压（其他实例正在启动）
            except OSError:
                continue
            shutil.rmtree(p, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _quiet_exit() -> None:
    """打包版退出兜底：onedir 正常返回；onefile 静默终止避免弹窗。

    - onedir：无 %TEMP%\\_MEI* 临时目录，正常返回走 Python 清理即可；
    - onefile：进程退出时 bootloader 会尝试删除 %TEMP%\\_MEIxxxxx，若退出
      瞬间仍有 DLL 句柄被占用（pywebview/pythonnet 的 CLR、WebView2、
      后台线程等）删除失败，会弹出 "Failed to remove temporary directory"
      对话框。此时用 os._exit 跳过该检查，残留目录由下次启动时的
      _cleanup_stale_mei() 统一清理。
    """
    if not getattr(sys, "frozen", False):
        return
    # onefile 解压目录名形如 _MEI12345；onedir 为 _internal，无需跳过
    try:
        is_onefile = Path(sys._MEIPASS).name.startswith("_MEI")
    except Exception:  # noqa: BLE001
        is_onefile = False
    if is_onefile:
        import os

        os._exit(0)


def run_server(host: str, port: int) -> None:
    """纯服务模式：前台运行 Web 服务（供 --server / 无界面部署）。"""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


# 打包版单实例检测的端口探测结果（None=未探测）：run_app 复用，避免二次阻塞探测。
# 启动路径用 0.25s 短超时——is_port_open 的 1s 超时在端口关闭时会拖慢每次启动。
_STARTUP_PORT_BUSY: bool | None = None


def _port_open_fast(port: int) -> bool:
    """快速探测本机端口是否被监听（0.25s 短超时，仅用于启动路径）。

    被 Tavily 占用的回环连接瞬时成功；端口关闭时本机网络栈 connect 会阻塞到
    超时（而非立即拒绝），is_port_open 的 1s 超时会让每次正常启动白白等 1s。
    """
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.25)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()
    except Exception:
        return False


def _try_activate_existing() -> bool:
    """打包版单实例检测：本机已有 Tavily 面板在运行时，激活其窗口并返回 True。

    仅打包版（sys.frozen）启用，避免影响开发环境多开 / --server / --mcp / --proxy。
    流程：面板端口被占用 → POST /api/activate 确认是 Tavily 面板并触发窗口激活 →
    成功则本进程应退出（由调用方处理），不再创建第二个窗口/服务实例。
    """
    global _STARTUP_PORT_BUSY
    if not getattr(sys, "frozen", False):
        return False
    try:
        import json as _json
        import urllib.request

        cfg = get_settings()
        port = int(cfg.get("port", 8000))
        # 快速探测端口（0.25s 短超时）；结果记录供 run_app 复用，避免二次阻塞探测
        busy = _port_open_fast(port)
        _STARTUP_PORT_BUSY = busy
        if not busy:
            return False  # 端口未占用：无已有实例，正常启动
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/activate",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        token = (cfg.get("auth_token") or "").strip()
        if token:
            req.add_header("X-Auth-Token", token)
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = _json.loads(r.read().decode("utf-8"))
        return bool(payload.get("ok")) and bool(payload.get("activated"))
    except Exception:
        # 端口被其他程序占用 / 网络异常：不激活，按原逻辑继续
        return False


def run_app() -> None:
    """网页套壳模式（默认）：原生 WebView2 窗口内嵌控制台面板。

    双击 Tavily.exe 即打开应用窗口，不再跳转系统浏览器；
    关闭窗口自动停止服务并退出。WebView2 不可用时自动回退浏览器。
    """
    import time
    import webbrowser

    # 打包版单实例：已有实例在运行则激活其窗口并静默退出，避免重复启动
    if _try_activate_existing():
        _quiet_exit()
        return

    # 清理上次退出残留的 PyInstaller 临时目录（见 _quiet_exit）
    _cleanup_stale_mei()

    cfg = get_settings()
    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 8000))
    url = f"http://127.0.0.1:{port}/"

    # 端口已被占用（例如已有实例在运行）：直接复用，不再重复启动服务。
    # 打包版已由 _try_activate_existing 探测过（结果在 _STARTUP_PORT_BUSY），
    # 直接复用避免第二次 1s 阻塞探测；开发版未探测则按原逻辑探测。
    port_busy = (
        _STARTUP_PORT_BUSY
        if _STARTUP_PORT_BUSY is not None
        else mcp_manager.is_port_open("127.0.0.1", port)
    )

    server = None
    thread = None
    tray_icon = None
    ready = False
    if not port_busy:
        server, thread = _run_server_thread(host, port)
        ready = _wait_ready(server)

    try:
        import webview  # noqa: PLC0415
        have_webview = True
    except Exception:
        have_webview = False

    if have_webview and (ready or port_busy):
        try:
            _harden_webview()
            # 系统托盘（与窗口/网页图标统一为应用图标）；
            # 「关闭/最小化/启动时进托盘」设置由 _WindowApi 与 shown 事件消费
            tray_icon = TrayIcon(_window_icon_path(), on_show=_tray_show, on_exit=_tray_exit)
            tray_icon.start()
            _TRAY["icon"] = tray_icon
            # 创建时即传入居中坐标（pywebview 支持 x/y，避免依赖 shown 事件时序）
            _win_w, _win_h = 1280, 840
            _win_x, _win_y = _centered_pos(_win_w, _win_h)
            window = webview.create_window(
                "Tavily Key Pool",
                url,
                width=_win_w,
                height=_win_h,
                x=_win_x,
                y=_win_y,
                min_size=(1024, 640),
                frameless=True,
                easy_drag=False,  # EdgeChromium 未实现 easy_drag，由 _WindowApi 自实现
                background_color=_window_bg_color(),  # 跟随系统主题，避免加载期黑屏
                js_api=_WindowApi(),
            )
            # 首次显示时把窗口居中于主屏工作区
            if window is not None:
                try:
                    def _on_shown(*_args):
                        _center_window(*_args)
                        # 开启「启动时进托盘」：窗口显示后立即隐藏
                        if get_settings().get("start_to_tray") and _HWND:
                            try:
                                ctypes.windll.user32.ShowWindow(_HWND, 0)  # SW_HIDE
                            except Exception:
                                pass

                    window.events.shown += _on_shown
                except Exception:
                    pass
            # private_mode=False + storage_path：让 WebView2 使用持久化用户数据目录，
            # 否则 localStorage（如主题模式选择）在退出后丢失。
            from paths import runtime_dir
            webview.start(
                private_mode=False,
                storage_path=str(runtime_dir() / "webview"),
            )  # 阻塞至窗口关闭
        except Exception as e:  # WebView2 缺失或启动失败 → 回退浏览器
            print(f"[tavily] WebView 不可用（{e}），改用系统浏览器打开面板。")
            webbrowser.open(url)
            _keep_alive()
    else:
        # 无 pywebview 或端口被占用：浏览器 + 常驻服务
        print(f"[tavily] 控制台面板: {url}")
        if ready or port_busy:
            webbrowser.open(url)
        else:
            print("[tavily] 服务启动失败，请检查 config.json 端口是否可用。")
        _keep_alive()

    # 窗口已关闭：清理托盘图标并停止本进程启动的服务
    if tray_icon is not None:
        tray_icon.stop()
    if server is not None:
        server.should_exit = True
    if thread is not None:
        thread.join(timeout=5)
    # 兜底：确保 MCP 子进程已终止（lifespan shutdown 可能因超时未完成）
    try:
        mcp_manager.stop()
    except Exception:  # noqa: BLE001
        pass
    # 打包版：静默退出，避免 bootloader 删除 _MEI 临时目录失败时弹窗
    _quiet_exit()


if __name__ == "__main__":
    import os

    # windowed 打包（console=False）下 stdout/stderr 可能为 None，先兜底
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    # ── MCP 角色：Tavily.exe --mcp（或 python dashboard.py --mcp）─────────
    # 由 mcp_manager 作为子进程拉起，仅运行 MCP 服务本体。
    # （软件启动时是否自动启动 MCP 由 lifespan 处理）
    if "--mcp" in sys.argv:
        import mcp_server
        mcp_server.main()
        sys.exit(0)

    # ── 搜索代理角色：Tavily.exe --proxy（或 python dashboard.py --proxy）──
    # 由 proxy_manager 作为子进程拉起，仅运行 Tavily 兼容搜索代理服务。
    if "--proxy" in sys.argv:
        import tavily_proxy
        tavily_proxy.main()
        sys.exit(0)

    # ── 纯服务模式：Tavily.exe --server（或 python dashboard.py --server）──
    if "--server" in sys.argv:
        cfg = get_settings()
        run_server(cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8000)))
        sys.exit(0)

    # ── 默认：网页套壳应用模式 ───────────────────────────────────────────
    run_app()

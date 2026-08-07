"""
MCP 服务子进程管理 — 由 Dashboard 主程序启动/停止 MCP 服务。

网络模式（sse / streamable-http）下，MCP 服务作为独立子进程运行，
监听局域网地址；stdio 模式由 AI 客户端直接拉起，不由面板管理。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from paths import runtime_dir
from settings import get_settings, mcp_is_network, mcp_url, mcp_urls

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
# 本程序实际启动过的 MCP 服务进程 PID 集合（以端口监听为准）。
# stop() 只允许清理这些 PID，避免误杀占用同一端口的第三方进程。
_owned_pids: set[int] = set()

# 期望运行状态：面板手动启动（start）/随软件自启置 True、手动停止（stop）置 False。
# 看门狗据此判断「应运行但已退出」→ 自动重启；手动停止后不会反复拉起。
_want = False
# 会话内看门狗自动重启次数（进程内计数，软件重启清零；前端状态卡展示）
_auto_restarts = 0


def want_running() -> bool:
    """是否期望 MCP 服务保持运行（面板手动启动 / 随软件自启置 True）。"""
    with _lock:
        return _want


def auto_restarts() -> int:
    """本次会话内看门狗自动重启次数。"""
    with _lock:
        return _auto_restarts


def _bump_auto_restarts() -> None:
    global _auto_restarts
    with _lock:
        _auto_restarts += 1


LOG_PATH = runtime_dir() / "mcp_server.log"


def _python_exe() -> str:
    """MCP 依赖 mcp 包：打包后用 exe 自身；开发模式优先使用 .venv 解释器。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    venv = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


def _entry_args() -> list[str]:
    """构造启动 MCP 子进程的命令行。"""
    if getattr(sys, "frozen", False):
        # 打包后单 exe：Tavily.exe --mcp 进入 MCP 角色
        return [sys.executable, "--mcp"]
    # 开发模式：python dashboard.py --mcp（与 exe 共用同一角色入口）
    return [_python_exe(), str(Path(__file__).resolve().parent / "dashboard.py"), "--mcp"]


def is_port_open(host: str, port: int) -> bool:
    """检查端口是否已被监听。"""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


# 前端每 5 秒轮询 /api/mcp/status，netstat 是重量级子进程调用；
# 缓存端口 PID 查询结果（TTL 3s），避免每次都 spawn netstat。
_pid_cache: dict = {"ts": 0.0, "port": None, "pid": None}
_PID_CACHE_TTL = 3.0


def _find_pid_on_port(port: int, force: bool = False) -> int | None:
    """通过 netstat 找到监听指定端口的真实进程 PID。

    Windows 上 .venv 的 python.exe 是 launcher，Popen 返回的 PID 是
    launcher 的（会很快退出），因此以端口为准定位真实服务进程。
    """
    now = time.time()
    if not force and now - _pid_cache["ts"] < _PID_CACHE_TTL and _pid_cache.get("port") == port:
        return _pid_cache["pid"]
    try:
        # Windows 下面板（Tavily.exe --noconsole）无控制台窗口：spawn 外部
        # console 程序（netstat）必须带 CREATE_NO_WINDOW，否则前端每 5 秒
        # 轮询 /api/mcp/status 都会弹出一个命令行窗口又马上关闭。
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout
        pid = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() in ("TCP", "TCP6"):
                if parts[1].endswith(f":{port}") and "LISTENING" in line.upper():
                    pid = int(parts[-1])
                    break
        _pid_cache.update({"ts": now, "port": port, "pid": pid})
        return pid
    except Exception:  # noqa: BLE001
        return None


def status() -> dict:
    """返回 MCP 服务运行状态（网络模式以端口检测为准）。"""
    global _proc
    with _lock:
        proc = _proc
    cfg = get_settings()
    network = mcp_is_network(cfg)
    port = int(cfg.get("mcp_port", 8001))
    running = False
    pid = None
    if network:
        # 以端口监听为准（Popen 的 pid 可能是 venv launcher，不可靠）
        running = is_port_open("127.0.0.1", port)
        if running:
            pid = _find_pid_on_port(port)
            if pid is not None and proc is not None:
                # 端口上的服务进程由本程序拉起（Popen 引用仍在）→ 记录归属，
                # 供 stop() 只清理自己的进程。
                with _lock:
                    _owned_pids.add(pid)
        else:
            # 启动瞬间 launcher 仍在，Popen 未退出也算启动中
            if proc is not None and proc.poll() is None:
                running = True
                pid = proc.pid
    if not running:
        _proc = None  # 清理已退出进程引用
    return {
        "running": running,
        "pid": pid,
        "transport": cfg.get("mcp_transport", "sse"),
        "host": cfg.get("mcp_host", "0.0.0.0"),
        "port": port,
        "url": mcp_url(cfg),
        "urls": mcp_urls(cfg),
        "network": network,
        "auto_start": bool(cfg.get("mcp_auto_start", False)),
        "auto_restarts": _auto_restarts,
    }


def start() -> dict:
    """启动 MCP 服务子进程（仅网络模式）。"""
    global _proc, _want
    with _lock:
        _want = True
        cfg = get_settings()
        if not mcp_is_network(cfg):
            return {
                "ok": False,
                "error": "当前为 stdio 模式，由 AI 客户端直接拉起进程，无法由面板管理。"
                         "请在 MCP 设置中将传输方式改为 sse 或 streamable-http。",
            }
        port = int(cfg.get("mcp_port", 8001))
        if is_port_open("127.0.0.1", port):
            return {"ok": False, "error": f"端口 {port} 已被占用，可能已有 MCP 服务在运行"}
        # 清理可能残留的 launcher 进程引用
        if _proc is not None and _proc.poll() is None:
            try:
                _proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        env = dict(os.environ)
        env["TAVILY_ROLE"] = "mcp"
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            logf = open(LOG_PATH, "ab")
            # Windows 下父进程为无控制台应用（Tavily.exe --noconsole / pywebview 面板），
            # 若不指定 CREATE_NO_WINDOW，每次拉起 MCP 子进程都会弹出新的命令行窗口。
            # 加上该标志后子进程完全在后台运行，不再弹窗。
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(
                _entry_args(),
                cwd=str(Path(__file__).resolve().parent),
                env=env,
                stdout=logf,
                stderr=logf,
                creationflags=creationflags,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"启动失败: {e}"}
        # 启动后短暂检查：若子进程立即崩溃（端口被占、依赖缺失等），
        # 尽早把真实错误返回给面板，避免面板误报“已启动”导致反复拉起。
        try:
            proc.wait(timeout=1.5)
            # 子进程已退出：读取日志尾部辅助定位原因
            tail = ""
            try:
                with open(LOG_PATH, "rb") as f:
                    f.seek(max(0, f.seek(0, 2) - 2000))
                    tail = f.read().decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001
                pass
            _proc = None
            reason = tail.splitlines()[-1] if tail else "子进程已退出（无日志输出）"
            return {"ok": False, "error": f"MCP 服务启动后立即退出: {reason}"}
        except subprocess.TimeoutExpired:
            # 1.5s 内未退出，视为仍在启动/正常运行
            _proc = proc
            return {"ok": True, "pid": proc.pid}


def stop() -> dict:
    """停止 MCP 服务子进程（杀掉监听端口的真实进程）。"""
    global _proc, _want
    with _lock:
        _want = False
        proc = _proc
        _proc = None
    cfg = get_settings()
    if not mcp_is_network(cfg):
        return {"ok": True}
    port = int(cfg.get("mcp_port", 8001))
    # 1) 若 Popen 进程还在（启动瞬间的 launcher），先结束它
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
    # 2) 兜底：杀掉监听端口的真实服务进程（仅限本程序启动的进程）
    pid = _find_pid_on_port(port, force=True)
    if pid is not None:
        if pid in _owned_pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            # 端口被本程序之外的进程占用：不越权清理，向面板说明
            if is_port_open("127.0.0.1", port):
                return {"ok": False, "error": f"端口 {port} 被外部进程(pid={pid})占用，未执行清理，请手动处理"}
            return {"ok": True}
    if is_port_open("127.0.0.1", port):
        return {"ok": False, "error": "已发送停止指令，但端口仍被占用（可能是外部进程）"}
    return {"ok": True}

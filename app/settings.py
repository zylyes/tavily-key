"""
App settings — config.json persistence.

提供部署模式(server/local)、绑定域名、监听地址、端口与访问鉴权令牌的
读取/保存能力。配置文件位于 data/config.json（见 paths.runtime_dir），
首次访问时自动生成。
"""
from __future__ import annotations

import json
import socket
import threading
import time

from paths import runtime_dir

CONFIG_PATH = runtime_dir() / "config.json"

# mode: "server" = Linux 服务器通过域名对外提供服务
#       "local"  = Windows 本地/局域网提供服务
# host: 127.0.0.1 = 仅本机可访问；0.0.0.0 = 局域网内所有设备可访问
DEFAULTS: dict = {
    "mode": "local",          # 部署模式: server | local
    "domain": "",             # 绑定域名（仅界面展示用途）
    "host": "0.0.0.0",        # 监听地址: 127.0.0.1(仅本机) / 0.0.0.0(局域网)
    "port": 8000,             # 监听端口
    "auth_token": "",         # 访问鉴权令牌（留空则不鉴权）
    # ── 通用（Windows 桌面应用）─────────────────────────────
    "autostart": False,         # 开机自启（注册表 Run 键）
    "start_to_tray": False,     # 启动后直接隐藏到系统托盘
    "close_to_tray": False,     # 点击关闭时最小化到系统托盘（不退出）
    "minimize_to_tray": False,  # 点击最小化时隐藏到系统托盘
    # ── MCP 服务（供 AI Agent 调用）──────────────────────────
    "mcp_auto_start": False,  # 软件启动时是否自动启动 MCP 服务
    "mcp_transport": "sse",   # 传输方式: stdio | sse | streamable-http
    "mcp_host": "0.0.0.0",    # MCP 监听地址（0.0.0.0 = 局域网可用）
    "mcp_port": 8001,         # MCP 监听端口
    "mcp_token": "",          # MCP 服务访问令牌（非空时要求 Authorization: Bearer <token>，留空不鉴权）
    "key_strategy": "round-robin",  # Key 池负载均衡策略: round-robin | least-used
    # ── 限流与用量同步 ────────────────────────────────────────
    "rate_limit_rpm": 90,        # 每 key 令牌桶速率兜底（endpoint_rpm 未覆盖的端点用）
    "rate_limit_max_wait": 1.0,  # 全部 key 受限时最多等待秒数
    "usage_cache_ttl": 60,       # /usage 同步结果缓存 TTL（秒）
    # 按 endpoint 分组的每 key 限流（RPM）。官方限流按 key 独立（池内 key 均来自
    # 不同账号）：research「创建任务」独立 20 RPM、crawl 独立 100 RPM、默认 dev
    # 100 RPM。默认值按官方上限留 10% 余量；未列出的 endpoint 回退 rate_limit_rpm。
    "endpoint_rpm": {"search": 90, "extract": 90, "crawl": 90, "map": 90, "research": 18},
    # ── 缓存 TTL（秒，0 = 关闭对应缓存）─────────────────────
    "cache_ttls": {
        "anomalies": 5,       # 异常识别结果缓存（近24h聚合，秒级变化无意义）
        "trend": 30,          # 用量趋势（按天聚合）缓存
        "logs": 1,            # 请求日志查询缓存
        "service_status": 1,  # MCP / 搜索代理服务状态缓存
    },
    # ── 异常识别阈值 ──────────────────────────────────────────
    "anomaly_thresholds": {},    # error_rate / leak_diff_credits / stale_days / slow_ratio
    "log_retention_days": 90,    # request_log 保留天数（0 = 不清理，防无界增长）
    # ── 异常通知 ──────────────────────────────────────────────
    "notify_webhook": "",          # 异常 Webhook URL（钉钉/企业微信/Server酱等，留空不推送）
    "notify_tray": True,           # 异常时是否显示 Windows 托盘气泡
    "notify_interval_minutes": 5,  # 异常检测后台周期（分钟）
    # ── MCP 默认参数与会话归属 ────────────────────────────────
    "mcp_default_parameters": {},  # 对 search 类请求注入的默认参数（对齐官方 DEFAULT_PARAMETERS）
    "mcp_human_id": "",          # 可选：转发 X-Human-Id 头，便于 Tavily 侧会话分析
    "mcp_project_id": "",        # 可选：转发 X-Project-ID 头，按项目归类用量
    # ── 网络搜索代理（Tavily 兼容 REST 服务，供 Cherry Studio 等客户端对接）──
    "proxy_auto_start": False,     # 软件启动时是否自动启动搜索代理服务
    "proxy_host": "0.0.0.0",       # 代理监听地址（0.0.0.0 = 局域网可用）
    "proxy_port": 8002,            # 代理监听端口
    "proxy_token": "",             # 代理 API 密钥（客户端 Bearer 鉴权；留空则不鉴权=开放）
}

MODE_DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 8000},
    "local": {"host": "0.0.0.0", "port": 8000},
}

_lock = threading.Lock()
_cache: dict | None = None


def load() -> dict:
    """从 config.json 读取设置，缺失项使用默认值；首次运行自动生成默认配置。"""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            # utf-8-sig：兼容带 BOM 的 UTF-8（某些编辑器/工具会写 BOM，
            # 若按纯 utf-8 解析 json 会抛异常导致整份配置静默回退默认值）
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
        except Exception:
            pass
    else:
        # 首次运行：写入默认配置，便于用户直接查看/编辑
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    return cfg


def get_settings() -> dict:
    """返回缓存的设置（进程内）。"""
    global _cache
    if _cache is None:
        _cache = load()
    return dict(_cache)


def reload() -> dict:
    """重新从磁盘加载设置。"""
    global _cache
    _cache = load()
    return dict(_cache)


# 面向长驻子进程（MCP / 搜索代理）的热刷新状态：TTL 内 stat 一次 config.json，
# 仅当文件 mtime/size 指纹变化时才 reload（避免每个请求都读盘）。
_fresh_lock = threading.Lock()
_fresh_ts = 0.0
_fresh_fp: tuple | None = None


def get_settings_fresh(ttl: float = 1.0) -> dict:
    """返回较新的设置：TTL 内复用进程缓存；超时后 stat config.json，文件
    变化才 reload（比无条件 reload 更省，适合每个请求调用的热刷新路径）。

    settings.get_settings() 是进程内缓存，长驻子进程（MCP / 搜索代理）启动后
    不会感知 config.json 的外部修改（如面板设置/生成密钥）。本函数用文件
    mtime+size 作指纹，只有文件确实变化才重读磁盘，TTL 兜底避免频繁 stat。
    """
    global _fresh_ts, _fresh_fp
    with _fresh_lock:
        now = time.monotonic()
        if now - _fresh_ts >= ttl:
            _fresh_ts = now
            try:
                st = CONFIG_PATH.stat() if CONFIG_PATH.exists() else None
                fp = (st.st_mtime, st.st_size) if st is not None else None
            except OSError:
                fp = None
            if fp != _fresh_fp:
                _fresh_fp = fp
                reload()
    return get_settings()


def save(patch: dict) -> dict:
    """合并保存设置项，返回更新后的完整设置。"""
    global _cache
    with _lock:
        cfg = load()
        for k, v in patch.items():
            if k in DEFAULTS:
                cfg[k] = v
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _cache = cfg
    return dict(cfg)


_INT_FIELDS = ("port", "mcp_port", "rate_limit_rpm", "usage_cache_ttl", "log_retention_days", "notify_interval_minutes", "proxy_port")
_BOOL_FIELDS = (
    "autostart", "start_to_tray", "close_to_tray", "minimize_to_tray",
    "mcp_auto_start", "notify_tray", "proxy_auto_start",
)
_STR_FIELDS = (
    "mode", "domain", "host", "auth_token",
    "mcp_transport", "mcp_host", "mcp_token", "mcp_human_id", "mcp_project_id",
    "notify_webhook", "proxy_host", "proxy_token",
)
# 以 JSON 对象存储的字段：接受 dict 或 JSON 字符串
_DICT_FIELDS = ("anomaly_thresholds", "mcp_default_parameters", "cache_ttls", "endpoint_rpm")
_FLOAT_FIELDS = ("rate_limit_max_wait",)


def validate_patch(patch: dict) -> dict:
    """规范化并校验设置补丁（白名单：只处理 DEFAULTS 中的字段）。

    非法值抛 ValueError；返回规范化后的字段字典，供 save() 使用。
    """
    out: dict = {}
    for k, v in patch.items():
        if k not in DEFAULTS:
            continue
        if k in _INT_FIELDS:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be an integer")
            if not (0 <= n <= 65535):
                raise ValueError(f"{k} must be in range 0-65535")
            out[k] = n
        elif k in _FLOAT_FIELDS:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
        elif k in _BOOL_FIELDS:
            out[k] = bool(v)
        elif k in _STR_FIELDS:
            out[k] = str(v or "").strip()
        elif k in _DICT_FIELDS:
            if isinstance(v, dict):
                out[k] = v
            else:
                import json
                try:
                    parsed = json.loads(str(v))
                except Exception as exc:
                    raise ValueError(f"{k} must be a JSON object") from exc
                if not isinstance(parsed, dict):
                    raise ValueError(f"{k} must be a JSON object")
                out[k] = parsed
        else:
            out[k] = v
    # 枚举/交叉校验
    if "mode" in out and out["mode"] not in ("server", "local"):
        raise ValueError("mode must be server or local")
    if "mcp_transport" in out and out["mcp_transport"] not in ("stdio", "sse", "streamable-http"):
        raise ValueError("mcp_transport must be stdio, sse or streamable-http")
    return out


def lan_ip() -> str:
    """获取本机局域网 IP；失败时回退 127.0.0.1。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        # connect 不会实际发包，仅用于让系统选择路由出口
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def lan_hostname() -> str:
    """获取本机主机名（用于 .local / NetBIOS 局域网地址，切换网络不变）。"""
    try:
        hn = socket.gethostname().strip()
        if hn and hn.lower() not in ("localhost", "localhost.localdomain"):
            return hn
    except Exception:
        pass
    return ""


def mcp_urls(cfg: dict | None = None) -> dict:
    """返回 MCP 服务可用的访问地址集合（仅网络模式）。

    返回 dict（stdio 返回空 dict）：
      - ip             : 局域网 IP 地址（随网络变化）
      - hostname       : 裸主机名（Windows 局域网 NetBIOS 解析）
      - hostname_local : 主机名 .local（mDNS，macOS/Linux 可解析）
      - local          : 127.0.0.1（仅本机，不依赖网络）
    """
    cfg = cfg or get_settings()
    transport = (cfg.get("mcp_transport") or "sse").strip().lower()
    if transport == "stdio":
        return {}
    port = int(cfg.get("mcp_port", 8001))
    path = "/mcp" if transport == "streamable-http" else "/sse"
    host = (cfg.get("mcp_host") or "0.0.0.0").strip()
    urls: dict = {"local": f"http://127.0.0.1:{port}{path}"}
    if host in ("0.0.0.0", "::"):
        urls["ip"] = f"http://{lan_ip()}:{port}{path}"
        hn = lan_hostname()
        if hn:
            urls["hostname"] = f"http://{hn}:{port}{path}"
            urls["hostname_local"] = f"http://{hn}.local:{port}{path}"
    elif host not in ("127.0.0.1", "localhost"):
        urls["ip"] = f"http://{host}:{port}{path}"
    return urls


def _display_host(host: str) -> str:
    """把 0.0.0.0 / :: 转换为可供他人访问的地址（域名优先交给调用方处理）。"""
    if host in ("0.0.0.0", "::"):
        return lan_ip()
    return host or "127.0.0.1"


def public_url(cfg: dict | None = None) -> str:
    """根据配置推导对外访问地址（域名优先，否则显示局域网可访问地址）。"""
    cfg = cfg or get_settings()
    domain = (cfg.get("domain") or "").strip()
    if domain:
        return f"http://{domain}"
    host = _display_host(cfg.get("host", "127.0.0.1"))
    return f"http://{host}:{cfg.get('port', 8000)}"


def mcp_url(cfg: dict | None = None) -> str:
    """根据配置推导 MCP 服务对外地址（sse/streamable-http 网络模式下可用）。"""
    cfg = cfg or get_settings()
    transport = (cfg.get("mcp_transport") or "sse").strip().lower()
    if transport == "stdio":
        return ""
    host = _display_host(cfg.get("mcp_host", "0.0.0.0"))
    port = int(cfg.get("mcp_port", 8001))
    path = "/mcp" if transport == "streamable-http" else "/sse"
    return f"http://{host}:{port}{path}"


def mcp_is_network(cfg: dict | None = None) -> bool:
    """MCP 是否为网络服务（可被面板启停、局域网访问）；stdio 为客户端直连模式。"""
    cfg = cfg or get_settings()
    return (cfg.get("mcp_transport") or "sse").strip().lower() != "stdio"


# 各缓存 TTL 默认值（秒）；配置 cache_ttls 可覆盖，0 = 关闭对应缓存
_CACHE_TTL_DEFAULTS = {
    "anomalies": 5.0,       # 异常识别结果缓存
    "trend": 30.0,          # 用量趋势（按天聚合）缓存
    "logs": 1.0,            # 请求日志查询缓存
    "service_status": 1.0,  # MCP / 搜索代理服务状态缓存
}


def cache_ttls() -> dict:
    """读取缓存 TTL 配置（秒，合并默认值，非法值回退默认）。"""
    cfg = get_settings()
    out = dict(_CACHE_TTL_DEFAULTS)
    for k, v in (cfg.get("cache_ttls") or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        out[k] = fv if fv >= 0 else 0.0
    return out


def proxy_urls(cfg: dict | None = None) -> dict:
    """返回搜索代理（Tavily 兼容 REST）可用的访问地址集合。

    返回 dict（始终可用，搜索代理恒为网络服务）：
      - ip             : 局域网 IP 地址（随网络变化）
      - hostname       : 裸主机名（Windows 局域网 NetBIOS 解析）
      - hostname_local : 主机名 .local（mDNS，macOS/Linux 可解析）
      - local          : 127.0.0.1（仅本机，不依赖网络）
    客户端把该地址填入「API 地址」即直接作为 Tavily base URL 使用（无路径后缀）。
    """
    cfg = cfg or get_settings()
    port = int(cfg.get("proxy_port", 8002))
    host = (cfg.get("proxy_host") or "0.0.0.0").strip()
    urls: dict = {"local": f"http://127.0.0.1:{port}"}
    if host in ("0.0.0.0", "::"):
        urls["ip"] = f"http://{lan_ip()}:{port}"
        hn = lan_hostname()
        if hn:
            urls["hostname"] = f"http://{hn}:{port}"
            urls["hostname_local"] = f"http://{hn}.local:{port}"
    elif host not in ("127.0.0.1", "localhost"):
        urls["ip"] = f"http://{host}:{port}"
    return urls


def proxy_url(cfg: dict | None = None) -> str:
    """根据配置推导搜索代理对外地址（默认返回局域网可访问地址）。"""
    cfg = cfg or get_settings()
    host = _display_host(cfg.get("proxy_host", "0.0.0.0"))
    return f"http://{host}:{int(cfg.get('proxy_port', 8002))}"

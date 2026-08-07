"""GitHub 更新检查：查询最新 release、对比版本、通知与手动更新。

提供 `check_update()` 给面板（/api/update/check）、CLI（update-check）与
后台自动检查线程共用：
- 调用 GitHub API `GET /repos/{repo}/releases/latest`（urllib，短超时，
  无第三方依赖，与 notify.send_webhook 同风格）；
- 语义化版本比较（支持 v 前缀与 pre-release 后缀），并标注当前版本类型
  （beta/stable）；
- 结果带 TTL 缓存（间隔 = 配置 update_check_interval_hours，默认 24h），
  避免频繁打 GitHub API（未认证限流 60 次/小时）；`force=True` 强制刷新；
- 任何网络/解析失败都优雅降级为 `ok: False` + error 字段，绝不抛异常
  影响服务主流程。

发现新版本一律【仅通知】（`handle_auto_update`）：托盘/Webhook 通知附带
更新公告摘要（release notes 前 120 字），由用户在面板手动点击「立即更新」
下载、确认后重启应用（`start_download` / `get_download_status` / `apply_update`，
仅打包版 Tavily.exe）。`apply_update` 生成 `data/apply_update.bat` 重启脚本：
结束当前实例 → 备份旧版（backup-old）→ 复制新版 Tavily.exe 与 _internal/ →
启动新版本；**data/ 运行数据（config/密钥/Key 池/日志）不替换**，新版本启动时
（dashboard）清理 backup-old 与临时更新目录，并一次性展示本次更新公告。

仓库可在配置 `update_repo` 中覆盖（默认 zylyes/tavily-key）；留空表示
禁用更新检查（check_update 直接返回 disabled 状态）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from logging_setup import get_logger
from settings import get_settings
from version import __version__

_log = get_logger("updater")

_API_URL = "https://api.github.com/repos/{repo}/releases/latest"
# GitHub API 要求 User-Agent；短超时避免阻塞调用方
_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 120.0   # 下载大 zip 的读写超时（秒）
_USER_AGENT = f"tavily-key-pool/{__version__} (update-check)"

_lock = threading.Lock()
_cached_ts = 0.0      # time.monotonic 上次真实检查时间
_cached_result: dict | None = None
# 后台自动检查已通知过的版本（按版本去重，避免每次检查都重复推送）
_notified_version: str | None = None

# ── 自动下载状态（面板轮询）──────────────────────────────────
# state: idle | starting | downloading | done | error
_dl_lock = threading.Lock()
_dl: dict = {"state": "idle", "received": 0, "total": 0, "error": "", "version": "", "path": "",
             "body": ""}


def _version_tuple(v: str) -> tuple:
    """把版本字符串解析为可比较元组 (major, minor, patch, pre)。

    支持 'v0.9.1' / '0.9.1' / '0.9.1-beta.2' / '0.9' 等常见形态；
    无法解析时回退 (0, 0, 0, '')，保证比较不抛异常。
    """
    s = (v or "").strip().lstrip("vV")
    if not s:
        return (0, 0, 0, "")
    # 拆出 pre-release 后缀（首个非数字段），如 '1.2.3-beta.1'
    pre = ""
    rest = s
    for i, ch in enumerate(s):
        if ch.isdigit() or ch == ".":
            continue
        pre = s[i:].lower().lstrip("-")
        rest = s[:i]
        break
    parts = rest.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], pre)


def _is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新（语义化比较，pre-release 低于正式版）。"""
    lt, ct = _version_tuple(latest), _version_tuple(current)
    if lt[:3] != ct[:3]:
        return lt[:3] > ct[:3]
    # 数字部分相同：正式版（无 pre）> 有 pre 后缀
    if bool(lt[3]) != bool(ct[3]):
        return not lt[3]
    return lt[3] > ct[3]


def _version_type() -> str:
    """当前版本类型：含 pre-release 后缀（-beta/-rc/-alpha 等）为 beta，否则 stable。"""
    return "beta" if _version_tuple(__version__)[3] else "stable"


def _normalize_repo(repo: str) -> str:
    """把用户输入的仓库归一化为 owner/repo。

    兼容 'zylyes/tavily-key'、'github.com/zylyes/tavily-key'、
    'https://github.com/zylyes/tavily-key(.git)' 等形式；非法输入返回 ''。
    """
    s = (repo or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip("/").removesuffix(".git").strip("/")
    parts = s.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _check_interval_hours() -> int:
    """读取配置 update_check_interval_hours（非法值回退 24）。"""
    try:
        return max(int(get_settings().get("update_check_interval_hours", 24) or 0), 0)
    except (TypeError, ValueError):
        return 24


def _asset_info(data: dict) -> dict:
    """从 release JSON 中挑出 Windows 打包产物（Tavily-*-win64.zip）。"""
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if name.startswith("tavily-") and name.endswith("-win64.zip"):
            return {
                "asset_name": a.get("name") or "",
                "asset_url": a.get("browser_download_url") or "",
                "asset_size": int(a.get("size") or 0),
            }
    return {"asset_name": "", "asset_url": "", "asset_size": 0}


def _fetch_latest(repo: str) -> dict:
    """请求 GitHub API 获取最新 release 关键字段。

    返回 {tag_name, html_url, published_at, body, asset_*}；网络/解析失败
    抛异常（调用方负责捕获转成 error 字段）。
    """
    req = urllib.request.Request(
        _API_URL.format(repo=repo.strip("/")),
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    # tag 形如 v0.9.2 → 展示时去掉 v 前缀更友好（比较仍用原始 tag 亦可）
    latest = tag.lstrip("vV") or ""
    return {
        "latest_version": latest,
        "tag_name": tag,
        "release_url": data.get("html_url") or f"https://github.com/{repo}/releases/latest",
        "published_at": data.get("published_at") or "",
        "body": (data.get("body") or "").strip(),
        **_asset_info(data),
    }


def can_auto_update() -> bool:
    """是否支持自动更新（仅打包版；源码运行无自更新意义）。"""
    return bool(getattr(sys, "frozen", False))


def check_update(force: bool = False) -> dict:
    """检查 GitHub 最新 release 并返回结果（进程内 TTL 缓存）。

    force=True 强制重新请求网络；否则在缓存有效期内直接返回上次结果。
    返回字段：
      ok                  bool   请求是否成功（False 表示网络/解析失败或禁用）
      disabled            bool   配置 update_repo 留空 = 关闭更新检查
      current_version     str    本地版本（app/version.py）
      latest_version      str    最新 release 版本（无 v 前缀）
      update_available    bool   是否发现可用更新
      release_url         str    最新 release 页面地址
      published_at        str    release 发布时间（ISO8601，可能为空）
      body                str    更新说明（release notes，可能为空）
      checked_at          float  unix 秒，最近一次成功检查时间
      error               str    失败原因（仅 ok=False 时有意义）
    """
    repo = _normalize_repo(get_settings().get("update_repo") or "")
    if not repo:
        return {
            "ok": False, "disabled": True, "current_version": __version__,
            "version_type": _version_type(),
            "latest_version": "", "update_available": False,
            "release_url": "", "published_at": "", "body": "",
            "checked_at": 0.0, "error": "update_repo 未配置，更新检查已禁用",
            "can_auto_update": can_auto_update(),
            "asset_name": "", "asset_url": "", "asset_size": 0,
        }

    global _cached_ts, _cached_result
    interval_h = _check_interval_hours()
    with _lock:
        now = time.monotonic()
        if not force and _cached_result is not None:
            if interval_h > 0 and now - _cached_ts < interval_h * 3600:
                return dict(_cached_result)

    try:
        info = _fetch_latest(repo)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        _log.warning("更新检查失败: %s", err)
        result = {
            "ok": False, "disabled": False, "current_version": __version__,
            "version_type": _version_type(),
            "latest_version": "", "update_available": False,
            "release_url": "", "published_at": "", "body": "",
            "checked_at": time.time(), "error": f"获取更新失败：{err}",
            "can_auto_update": can_auto_update(),
            "asset_name": "", "asset_url": "", "asset_size": 0,
        }
        with _lock:
            _cached_result = result
            _cached_ts = time.monotonic()
        return dict(result)

    result = {
        "ok": True, "disabled": False, "current_version": __version__,
        "version_type": _version_type(),
        **info,
        "update_available": _is_newer(info["latest_version"], __version__),
        "can_auto_update": can_auto_update(),
        "checked_at": time.time(),
        "error": "",
    }
    with _lock:
        _cached_result = result
        _cached_ts = time.monotonic()
    _log.info(
        "更新检查完成：当前 %s，最新 %s（%s）",
        __version__, info["latest_version"] or "?",
        "有新版本" if result["update_available"] else "已是最新",
    )
    return dict(result)


def _notify_all(tray, webhook: str, title: str, message: str, payload: dict) -> bool:
    """按配置推送单个通知（托盘 + webhook），返回是否至少推送了一个渠道。"""
    sent = False
    if tray is not None:
        try:
            tray.notify(title, message)
            sent = True
        except Exception:  # noqa: BLE001
            pass
    if webhook:
        from notify import send_webhook
        try:
            payload.setdefault("title", title)
            payload.setdefault("message", message)
            sent = send_webhook(webhook, payload) or sent
        except Exception:  # noqa: BLE001
            pass
    return sent


def handle_auto_update(tray=None, webhook: str = "") -> dict:
    """后台自动检查主入口：发现新版本时通知用户（附带更新公告摘要）。

    自动更新已移除：新版本一律仅通知，由用户在面板「设置 → 关于与更新」
    查看更新公告并手动点击「立即更新」。webhook 附带 release notes 摘要
    （前 120 字）。按版本去重（同一版本只通知一次）。返回 check_update 结果。
    """
    global _notified_version
    result = check_update()
    if not result.get("ok") or not result.get("update_available"):
        return result
    latest = result.get("latest_version") or ""
    with _lock:
        if latest == _notified_version:
            return result
        _notified_version = latest
    body = result.get("body") or ""
    summary = body[:120] + ("…" if len(body) > 120 else "")
    title = f"Tavily Key Pool 有新版本 {latest}"
    message = (f"当前版本 {__version__}，发现新版本 {latest}。"
               f"可在面板「设置 → 关于与更新」查看更新公告并手动更新。")
    _notify_all(tray, webhook, title, message, {
        "event": "update_available",
        "current_version": __version__, "latest_version": latest,
        "release_url": result.get("release_url", ""),
        "summary": summary,
    })
    _log.info("发现新版本 %s，已通知用户（含更新公告摘要）", latest)
    return result


# ── 自动更新：下载 / 校验 / 解压 / 替换重启（仅打包版）────────

def _set_dl(**kw) -> None:
    """更新下载状态（线程安全）。"""
    with _dl_lock:
        _dl.update(**kw)


def get_download_status() -> dict:
    """返回当前下载状态（供面板轮询）。"""
    with _dl_lock:
        return dict(_dl)


def start_download() -> tuple[bool, str]:
    """启动后台下载线程，返回 (ok, error)。仅打包版可用。

    面板手动触发：下载完成后由用户在面板点「重启应用」应用更新。
    """
    if not can_auto_update():
        return False, "仅打包版（Tavily.exe）支持自动更新"
    with _dl_lock:
        if _dl["state"] in ("downloading", "starting"):
            return False, "已有下载进行中，请稍候"
    _set_dl(state="starting", received=0, total=0, error="", version="", path="",
            body="")
    threading.Thread(target=download_update, daemon=True, name="tavily-update-dl").start()
    return True, ""


def _download_file(url: str, dest: Path) -> int:
    """分块下载 url 到 dest，返回字节数；进度写入模块状态。"""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        received = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                _set_dl(received=received, total=total)
        return received


def download_update() -> None:
    """后台线程主体：下载最新打包 zip → 校验 → 解压到临时目录。

    结果写入模块状态 _dl（done 时 path 指向含 Tavily.exe 的新版目录）。
    任何异常写入 error 状态，不抛给调用线程。
    """
    try:
        info = check_update(force=True)
        if not info.get("ok"):
            raise RuntimeError(info.get("error") or "更新检查失败")
        if not info.get("asset_url"):
            raise RuntimeError("最新 release 未附带 Windows 打包产物（Tavily-*-win64.zip）")
        version = info.get("latest_version") or ""
        body = info.get("body") or ""

        tmp = Path(tempfile.gettempdir()) / f"tavily-update-{int(time.time())}"
        tmp.mkdir(parents=True, exist_ok=True)
        zip_path = tmp / (info.get("asset_name") or "tavily-update.zip")
        _set_dl(state="downloading", received=0, total=int(info.get("asset_size") or 0),
                error="", version=version, body=body)
        _log.info("开始下载更新包 %s（%s 字节）", zip_path.name, info.get("asset_size"))
        n = _download_file(info["asset_url"], zip_path)
        # 大小校验（GitHub 提供 size）
        expected = int(info.get("asset_size") or 0)
        if expected and n != expected:
            raise RuntimeError(f"下载文件大小不符：{n} ≠ {expected}")
        # zip 完整性校验
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f"压缩包校验失败：{bad}")
            extracted = tmp / "extracted"
            zf.extractall(extracted)
        # 定位顶层目录（zip 内可能再套一层 Tavily/）
        top = extracted
        for child in extracted.iterdir():
            if child.is_dir():
                top = child
                break
        if not (top / "Tavily.exe").is_file():
            raise RuntimeError("压缩包内未找到 Tavily.exe，无法自动更新")
        _set_dl(state="done", received=n, total=n, path=str(top), version=version, body=body)
        _log.info("更新包下载并解压完成：%s", top)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        _log.error("自动更新下载失败：%s", err)
        _set_dl(state="error", error=err)


def _write_update_script(new_dir: str) -> Path:
    """生成 data/apply_update.bat：结束实例 → 备份旧版 → 部署新版 → 启动。

    脚本输出写 data/update_apply.log 便于排查。返回脚本路径。
    """
    from paths import base_dir, runtime_dir

    base = base_dir()
    log_path = runtime_dir() / "update_apply.log"
    bat = runtime_dir() / "apply_update.bat"
    # 路径统一转义：cmd 中 &()^ 等需转义，简单场景用引号包裹即可
    content = f"""@echo off
chcp 65001 >nul
set "BASE={base}"
set "NEW={new_dir}"
set "LOG={log_path}"
echo [%date% %time%] ==== apply update start ==== >> "%LOG%"
rem 等待当前实例退出（面板/子进程均为 Tavily.exe）
taskkill /IM Tavily.exe /F >> "%LOG%" 2>&1
timeout /t 2 /nobreak >nul
rem 备份当前版本（保留 data/ 运行数据不动）
set "BAK=%BASE%\\backup-old"
if exist "%BAK%" rmdir /s /q "%BAK%" >> "%LOG%" 2>&1
mkdir "%BAK%" >> "%LOG%" 2>&1
if exist "%BASE%\\Tavily.exe" move "%BASE%\\Tavily.exe" "%BAK%\\Tavily.exe" >> "%LOG%" 2>&1
if exist "%BASE%\\_internal" move "%BASE%\\_internal" "%BAK%\\_internal" >> "%LOG%" 2>&1
rem 部署新版本
if exist "%NEW%\\Tavily.exe" (
  copy /y "%NEW%\\Tavily.exe" "%BASE%\\Tavily.exe" >> "%LOG%" 2>&1
) else (
  echo [%date% %time%] ERROR: new Tavily.exe not found >> "%LOG%"
)
if exist "%NEW%\\_internal" xcopy /e /i /y "%NEW%\\_internal" "%BASE%\\_internal" >> "%LOG%" 2>&1
rem 启动新版本
if exist "%BASE%\\Tavily.exe" (
  start "" "%BASE%\\Tavily.exe"
  echo [%date% %time%] new version launched >> "%LOG%"
) else (
  echo [%date% %time%] ERROR: launch failed, restoring backup >> "%LOG%"
  if exist "%BAK%\\Tavily.exe" copy /y "%BAK%\\Tavily.exe" "%BASE%\\Tavily.exe" >> "%LOG%" 2>&1
  if exist "%BAK%\\_internal" xcopy /e /i /y "%BAK%\\_internal" "%BASE%\\_internal" >> "%LOG%" 2>&1
  start "" "%BASE%\\Tavily.exe"
)
echo [%date% %time%] ==== apply update done ==== >> "%LOG%"
"""
    bat.write_text(content, encoding="utf-8")
    return bat


def apply_update() -> dict:
    """打包版：生成并启动重启脚本，替换为已下载的新版本。

    返回 {ok, error}。调用后当前进程将被脚本结束（taskkill），
    新版本随后自动启动；data/ 运行数据保留。
    """
    if not can_auto_update():
        return {"ok": False, "error": "仅打包版（Tavily.exe）支持自动更新"}
    with _dl_lock:
        st = dict(_dl)
    if st["state"] != "done":
        return {"ok": False, "error": "更新包未就绪，请先完成下载"}
    new_dir = st.get("path") or ""
    if not new_dir or not Path(new_dir).is_dir():
        return {"ok": False, "error": "更新包目录无效"}
    try:
        from paths import runtime_dir

        # 先尝试优雅停止子进程（MCP/代理），脚本再 taskkill 兜底
        try:
            import mcp_manager
            import proxy_manager
            mcp_manager.stop()
            proxy_manager.stop()
        except Exception:  # noqa: BLE001
            pass
        bat = _write_update_script(new_dir)
        # 记录待应用版本（供下次启动检测残留）
        try:
            (runtime_dir() / "update-pending.json").write_text(
                json.dumps({"version": st.get("version", ""), "new_dir": new_dir},
                           ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        # 写更新公告：新版本启动后展示本次更新说明（read_announcement 一次性读取）
        try:
            (runtime_dir() / "last-update.json").write_text(
                json.dumps({
                    "version": st.get("version", ""),
                    "body": st.get("body", ""),
                    "applied_at": time.time(),
                }, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=creationflags)
        _log.info("已启动自动更新脚本：%s", bat)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        _log.error("自动更新启动失败：%s", str(e)[:300])
        return {"ok": False, "error": f"启动更新失败：{str(e)[:200]}"}


def read_announcement() -> dict | None:
    """读取本次更新公告（一次性：读取后删除文件）。

    返回 {version, body, applied_at}；无公告返回 None。供面板
    GET /api/update/announcement 在新版本启动后展示更新说明。
    """
    try:
        from paths import runtime_dir

        p = runtime_dir() / "last-update.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        p.unlink(missing_ok=True)
        return {
            "version": str(data.get("version", "")),
            "body": str(data.get("body", "")),
            "applied_at": float(data.get("applied_at", 0) or 0),
        }
    except Exception as e:  # noqa: BLE001
        _log.warning("读取更新公告失败：%s", str(e)[:200])
        return None


def cleanup_after_update() -> None:
    """新版本启动时清理：旧版备份（backup-old）与过期临时更新目录。

    dashboard 启动（lifespan / run_app）调用一次，幂等。
    """
    try:
        from paths import base_dir, runtime_dir

        base = base_dir()
        bak = base / "backup-old"
        if bak.exists():
            import shutil
            shutil.rmtree(bak, ignore_errors=True)
            _log.info("已清理旧版本备份：%s", bak)
        # 清理 data/update-pending.json（已应用）
        try:
            p = runtime_dir() / "update-pending.json"
            if p.exists():
                p.unlink()
        except OSError:
            pass
        # 清理临时下载目录（超过 1 天）
        try:
            tmp_root = Path(tempfile.gettempdir())
            cutoff = time.time() - 86400
            for d in tmp_root.glob("tavily-update-*"):
                try:
                    if d.stat().st_mtime < cutoff:
                        import shutil
                        shutil.rmtree(d, ignore_errors=True)
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        _log.warning("更新残留清理失败：%s", str(e)[:200])

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
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from logging_setup import get_logger
from settings import get_settings
from tray import NIIF_INFO
from version import __version__

_log = get_logger("updater")

_API_URL = "https://api.github.com/repos/{repo}/releases/latest"
# GitHub API 要求 User-Agent；短超时避免阻塞调用方
_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 120.0   # 下载大 zip 的读写超时（秒）
_USER_AGENT = f"tavily-key-pool/{__version__} (update-check)"
# 更新检查失败结果的短缓存（秒）：避免一次瞬时网络故障导致整个间隔内不重试
_FAIL_CACHE_TTL = 600.0
# 资产文件名白名单（仅 basename；路径分隔符/.. 一律拒绝，防路径穿越）
_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
# update_repo 归一化后的 owner/repo 白名单（防止异常字符进入 URL）
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# cmd 元字符：顶层目录名含这些字符时拒绝解压（防 bat 注入，纵深防御）
_CMD_METACHARS = set("&|^<>%!")
# 资产下载地址允许的主机：github.com 精确匹配 + GitHub 资产 CDN（带点后缀，
# 避免 *.github.com 等宽松匹配放行非资产子域）
_ALLOWED_ASSET_HOST_SUFFIXES = (".githubusercontent.com",)

_lock = threading.Lock()
_cached_ts = 0.0      # time.monotonic 上次真实检查时间
_cached_result: dict | None = None
_cached_is_error = False   # 最近一次缓存结果是否为失败（失败用短 TTL，见 _FAIL_CACHE_TTL）
_cached_repo = ""     # 缓存对应的 update_repo（切换仓库后缓存失效）
# 后台自动检查已通知过的版本（按版本去重，避免每次检查都重复推送）
_notified_version: str | None = None

# ── 自动下载状态（面板轮询）──────────────────────────────────
# state: idle | starting | downloading | paused | done | error | cancelled
_dl_lock = threading.Lock()
_dl: dict = {"state": "idle", "received": 0, "total": 0, "error": "", "version": "", "path": "",
             "body": "", "tmp": ""}
# 下载控制：暂停（set=暂停，保持连接不读取）与取消（set=取消）
_pause_event = threading.Event()
_cancel_event = threading.Event()
# 下载线程与代际号：start_download 在旧线程仍存活时拒绝重启；worker 写入
# 最终状态前校验代际，避免被取消的旧线程覆盖新任务的状态。
_dl_thread: threading.Thread | None = None
_dl_epoch = 0


class _DownloadCancelled(Exception):
    """内部信号：下载被用户取消（调用方负责清理并回到 idle）。"""


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


# pre-release 后缀类别优先级（PEP 440 简化）：dev < alpha < beta < rc < 正式版
_PRE_ORDER = {"dev": -1, "alpha": 0, "a": 0, "beta": 1, "b": 1,
              "rc": 2, "pre": 2, "preview": 2}


def _pre_key(pre: str) -> tuple:
    """把 pre-release 后缀（如 'beta.2'）解析为可比较键：(类别序, 数字列表)。

    'beta.10' > 'beta.9'（数字按数值比较而非字符串）；无后缀（正式版）最高。
    未知标签回退 beta 级（与旧行为一致）。
    """
    if not pre:
        return (len(_PRE_ORDER) + 1, [0])   # 正式版 > 任何 pre-release
    m = re.match(r"^([a-zA-Z]+)\.?(.*)$", pre)
    label = (m.group(1) if m else "").lower()
    nums = [int(x) for x in re.findall(r"\d+", m.group(2) if m else "")]
    return (_PRE_ORDER.get(label, 1), nums or [0])


def _is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新（语义化比较，pre-release 低于正式版）。"""
    lt, ct = _version_tuple(latest), _version_tuple(current)
    if lt[:3] != ct[:3]:
        return lt[:3] > ct[:3]
    # 数字部分相同：按 pre 后缀键比较（无后缀 = 正式版，级别最高）
    return _pre_key(lt[3]) > _pre_key(ct[3])


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
        repo = f"{parts[0]}/{parts[1]}"
        # 白名单校验：仅允许字母数字与 ._-，非法输入视为未配置（禁用更新检查）
        if _REPO_RE.fullmatch(repo):
            return repo
    return ""


def _check_interval_hours() -> int:
    """读取配置 update_check_interval_hours（非法值回退 24）。"""
    try:
        return max(int(get_settings().get("update_check_interval_hours", 24) or 0), 0)
    except (TypeError, ValueError):
        return 24


def _asset_info(data: dict) -> dict:
    """从 release JSON 中挑出 Windows 打包产物（Tavily-*-win64.zip）。

    digest 为 GitHub 提供的 SHA-256（形如 'sha256:<hex>'），下载后用于完整性校验。
    """
    for a in data.get("assets") or []:
        name = (a.get("name") or "").lower()
        if name.startswith("tavily-") and name.endswith("-win64.zip"):
            return {
                "asset_name": a.get("name") or "",
                "asset_url": a.get("browser_download_url") or "",
                "asset_size": int(a.get("size") or 0),
                "digest": a.get("digest") or "",
            }
    return {"asset_name": "", "asset_url": "", "asset_size": 0, "digest": ""}


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

    global _cached_ts, _cached_result, _cached_is_error, _cached_repo
    interval_h = _check_interval_hours()
    with _lock:
        now = time.monotonic()
        if not force and _cached_result is not None and _cached_repo == repo:
            if _cached_is_error:
                # 失败结果用短缓存：瞬时网络故障不会导致整个间隔内不再重试
                if now - _cached_ts < _FAIL_CACHE_TTL:
                    return dict(_cached_result)
            elif interval_h > 0 and now - _cached_ts < interval_h * 3600:
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
            _cached_is_error = True
            _cached_repo = repo
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
        _cached_is_error = False
        _cached_repo = repo
    _log.info(
        "更新检查完成：当前 %s，最新 %s（%s）",
        __version__, info["latest_version"] or "?",
        "有新版本" if result["update_available"] else "已是最新",
    )
    return dict(result)


def handle_auto_update(tray=None, webhook: str = "", window_open: bool = True,
                       force: bool = False) -> dict:
    """自动/手动检查主入口：发现新版本时通知用户（附带更新公告摘要）。

    自动更新已移除：新版本一律仅通知，由用户在面板查看更新公告并手动更新。
    通知方式按主窗口是否打开区分：
      - window_open=True（主窗口可见）：不弹系统托盘气泡——前端页面会轮询
        /api/update/check 并显示右下角通知；仅 webhook 推送。
      - window_open=False（主窗口未打开/托盘后台）：托盘气泡（系统通知），
        点击气泡后打开主窗口并显示更新公告（mark_open_notice）。
    force=True 强制刷新网络（托盘「检查更新」手动检查）。按版本去重
    （同一版本只通知一次）。返回 check_update 结果。
    """
    global _notified_version
    result = check_update(force=force)
    if not result.get("ok") or not result.get("update_available"):
        return result
    latest = result.get("latest_version") or ""
    with _lock:
        if latest == _notified_version:
            return result
    body = result.get("body") or ""
    summary = body[:120] + ("…" if len(body) > 120 else "")
    title = f"Tavily Key Pool 有新版本 {latest}"
    message = (f"当前版本 {__version__}，发现新版本 {latest}。点击查看更新公告。")
    # 去重标记必须在“至少一个渠道确实推送成功”之后设置：若所有渠道都推送
    # 失败则不标记，下一轮自动检查会重试，避免用户永远收不到更新提醒。
    sent = False
    # 系统通知（托盘气泡）：仅主窗口未打开且配置 notify_tray 开启时推送
    if not window_open and tray is not None and bool(get_settings().get("notify_tray", True)):
        try:
            tray.notify(title, message, icon=NIIF_INFO)
            mark_open_notice(latest)
            sent = True
        except Exception as e:  # noqa: BLE001
            _log.warning("更新通知（托盘气泡）推送失败：%s", str(e)[:200])
    # webhook 照常推送（不受窗口状态影响）
    if webhook:
        from notify import send_webhook
        try:
            payload = {
                "event": "update_available",
                "current_version": __version__, "latest_version": latest,
                "release_url": result.get("release_url", ""),
                "summary": summary,
                "title": title,
                "message": message,
            }
            if send_webhook(webhook, payload):
                sent = True
        except Exception as e:  # noqa: BLE001
            _log.warning("更新通知（webhook）推送失败：%s", str(e)[:200])
    if sent:
        with _lock:
            _notified_version = latest
        _log.info("发现新版本 %s，已通知用户（含更新公告摘要）", latest)
    else:
        _log.info("发现新版本 %s，但无可用的通知渠道或推送失败（未标记去重，下轮重试）", latest)
    return result


# ── 系统通知点击 → 打开窗口后显示公告 ────────────────────────
# 主窗口未打开时通过托盘气泡（系统通知）提醒；用户点击气泡后打开主窗口，
# 前端轮询 /api/update/notice-pending 消费该标记并展示更新公告弹窗。
_notice_lock = threading.Lock()
_pending_open_notice: str = ""


def mark_open_notice(version: str) -> None:
    """记录系统通知点击后待展示公告的版本（窗口打开后前端消费）。"""
    global _pending_open_notice
    with _notice_lock:
        _pending_open_notice = version or ""


def consume_open_notice() -> str:
    """前端轮询：读取并清除待展示公告标记，返回版本（空=无）。"""
    global _pending_open_notice
    with _notice_lock:
        v = _pending_open_notice
        _pending_open_notice = ""
        return v


# ── 自动更新：下载 / 校验 / 解压 / 替换重启（仅打包版）────────

def _set_dl(**kw) -> None:
    """更新下载状态（线程安全）。"""
    with _dl_lock:
        _dl.update(**kw)


def get_download_status() -> dict:
    """返回当前下载状态（供面板轮询；内部 tmp 路径不外泄）。"""
    with _dl_lock:
        st = dict(_dl)
    st.pop("tmp", None)
    return st


def start_download() -> tuple[bool, str]:
    """启动后台下载线程，返回 (ok, error)。仅打包版可用。

    面板手动触发：下载完成后由用户在面板点「重启应用」应用更新。
    取消后立即重启会被拒绝（旧线程仍在清理），避免新旧线程互相覆盖状态。
    """
    global _dl_thread, _dl_epoch
    if not can_auto_update():
        return False, "仅打包版（Tavily.exe）支持自动更新"
    with _dl_lock:
        if _dl["state"] in ("downloading", "starting", "paused"):
            return False, "已有下载进行中，请稍候"
        if _dl_thread is not None and _dl_thread.is_alive():
            return False, "正在清理上次下载，请稍候"
        old_tmp = _dl.get("tmp") or ""
        _dl_epoch += 1
        gen = _dl_epoch
        # 已在锁内：直接更新（_set_dl 会再次获取同一把非重入锁 → 同线程死锁）
        _dl.update(state="starting", received=0, total=0, error="", version="", path="",
                   body="", tmp="")
    _pause_event.clear()
    _cancel_event.clear()
    if old_tmp:
        # done 态重复下载：先清理上次的临时目录，避免磁盘累积
        try:
            import shutil
            shutil.rmtree(old_tmp, ignore_errors=True)
            _log.info("已清理上次下载的临时目录：%s", old_tmp[:200])
        except Exception:  # noqa: BLE001
            _log.warning("清理上次下载临时目录失败：%s", old_tmp[:200])
    t = threading.Thread(target=download_update, args=(gen,), daemon=True,
                         name="tavily-update-dl")
    _dl_thread = t
    t.start()
    return True, ""


def pause_download() -> tuple[bool, str]:
    """暂停正在进行的下载（保持连接不读取数据，可继续）。"""
    with _dl_lock:
        st = _dl["state"]
    if st != "downloading":
        return False, "当前没有正在进行的下载"
    _pause_event.set()
    _set_dl(state="paused")
    _log.info("下载已暂停")
    return True, ""


def resume_download() -> tuple[bool, str]:
    """继续已暂停的下载。"""
    with _dl_lock:
        st = _dl["state"]
    if st != "paused":
        return False, "当前没有已暂停的下载"
    _pause_event.clear()
    _set_dl(state="downloading")
    _log.info("下载已继续")
    return True, ""


def cancel_download() -> tuple[bool, str]:
    """取消下载：通知后台线程终止并清理临时文件，回到 idle。"""
    with _dl_lock:
        st = _dl["state"]
    if st not in ("downloading", "paused", "starting"):
        return False, "当前没有可取消的下载"
    _cancel_event.set()
    _pause_event.clear()   # 解除暂停，让下载循环立即退出
    _set_dl(state="cancelled", error="")
    _log.info("下载已取消")
    return True, ""


def _download_file(url: str, dest: Path) -> int:
    """分块下载 url 到 dest，返回字节数；进度写入模块状态。

    支持暂停（_pause_event）与取消（_cancel_event）：暂停时保持连接
    不读取数据；取消时抛 _DownloadCancelled 由调用方清理。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        received = 0
        with open(dest, "wb") as f:
            while True:
                # 暂停：等待恢复或取消
                while _pause_event.is_set() and not _cancel_event.is_set():
                    time.sleep(0.2)
                if _cancel_event.is_set():
                    raise _DownloadCancelled()
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                _set_dl(received=received, total=total)
        return received


def _fix_zip_name(name: str) -> str:
    """修复 zip 内被错误按 CP437 解码的中文（GBK）文件名。

    中文 Windows 下用 7-Zip 等工具打包时，文件名按本地代码页（GBK）写入
    且不带 UTF-8 标志位；Python zipfile 对无 UTF-8 标志的文件名按 CP437
    解码 → 中文变成「CLI╩╣╙├」类乱码（GBK 字节被当作 CP437 字符）。
    这里把乱码逆映射还原为正确中文名；非乱码名（CP437 编码失败或无中文
    结果）原样返回。
    """
    try:
        raw = name.encode("cp437")
        fixed = raw.decode("gbk")
        if fixed != name and any("\u4e00" <= c <= "\u9fff" for c in fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


def _safe_zip_target(extracted: Path, name: str) -> Path:
    """校验 zip 条目名并返回安全解压目标（拒绝路径穿越/绝对路径/盘符）。

    Windows 下 `\\` 与 `/` 同为分隔符，统一按 `/` 归一化；条目含 `..`、
    绝对路径、盘符或越出 extracted 目录时抛 RuntimeError（OWASP Zip Slip 防护）。
    """
    norm = (name or "").replace("\\", "/")
    parts = norm.split("/")
    if (not norm or norm.startswith("/") or ".." in parts
            or any(p in ("", ".") for p in parts)
            or re.match(r"^[A-Za-z]:", norm)):
        raise RuntimeError(f"压缩包条目路径非法：{name!r}")
    root = extracted.resolve()
    target = (extracted / norm).resolve()
    if target != root and not target.is_relative_to(root):
        raise RuntimeError(f"压缩包条目越界：{name!r}")
    return target


def _validate_asset_url(url: str) -> None:
    """资产下载地址白名单：仅允许 https 且主机为 github.com 或其资产 CDN。"""
    u = urllib.parse.urlsplit(url or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        raise RuntimeError("资产下载地址非法：仅支持 https")
    # github.com 精确匹配；CDN 用带点后缀（.githubusercontent.com）防宽松匹配
    if not (host == "github.com" or host.endswith(_ALLOWED_ASSET_HOST_SUFFIXES)):
        raise RuntimeError("资产下载地址非法：非 GitHub 域名")


def download_update(gen: int | None = None) -> None:
    """后台线程主体：下载最新打包 zip → 校验 → 解压到临时目录。

    结果写入模块状态 _dl（done 时 path 指向含 Tavily.exe 的新版目录）。
    任何异常写入 error 状态，不抛给调用线程；用户取消时清理临时目录
    并回到 idle。`gen` 为代际号：若调用时模块代际已推进（旧任务被取消后
    新任务已启动），本次结果丢弃且不覆盖 _dl，仅清理自身临时目录。
    """
    global _dl_epoch
    if gen is None:
        gen = _dl_epoch
    tmp: Path | None = None
    try:
        if _cancel_event.is_set():
            raise _DownloadCancelled()   # starting 阶段取消：无需等待网络请求返回
        info = check_update(force=True)
        if not info.get("ok"):
            raise RuntimeError(info.get("error") or "更新检查失败")
        if not info.get("asset_url"):
            raise RuntimeError("最新 release 未附带 Windows 打包产物（Tavily-*-win64.zip）")
        if _cancel_event.is_set():
            raise _DownloadCancelled()
        version = info.get("latest_version") or ""
        body = info.get("body") or ""

        # 代际校验：取消后立即重启时，旧线程在此退出，不再写任何状态
        if gen != _dl_epoch:
            return
        # 资产文件名白名单（仅 basename，防路径穿越）
        asset_name = (info.get("asset_name") or "").strip()
        if not asset_name or Path(asset_name).name != asset_name or not _ASSET_NAME_RE.fullmatch(asset_name):
            raise RuntimeError("最新 release 资产文件名非法")
        _validate_asset_url(info.get("asset_url") or "")

        tmp = Path(tempfile.gettempdir()) / f"tavily-update-{time.time_ns()}"
        tmp.mkdir(parents=True, exist_ok=True)
        zip_path = tmp / asset_name
        _set_dl(state="downloading", received=0, total=int(info.get("asset_size") or 0),
                error="", version=version, body=body, tmp=str(tmp))
        _log.info("开始下载更新包 %s（%s 字节）", zip_path.name, info.get("asset_size"))
        n = _download_file(info["asset_url"], zip_path)
        # 大小校验（GitHub 提供 size）
        expected = int(info.get("asset_size") or 0)
        if expected and n != expected:
            raise RuntimeError(f"下载文件大小不符：{n} ≠ {expected}")
        # SHA-256 完整性校验（GitHub 提供 digest，如 'sha256:<hex>'）
        digest = (info.get("digest") or "").strip()
        if digest:
            import hashlib
            expected_digest = digest.removeprefix("sha256:").lower()
            if expected_digest:
                actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
                if actual != expected_digest:
                    raise RuntimeError("下载文件 SHA-256 校验失败（内容可能被篡改）")
        # zip 完整性校验
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f"压缩包校验失败：{bad}")
            extracted = tmp / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            # 手动逐条解压并修复 GBK 文件名乱码（zipfile.extractall 会把
            # 无 UTF-8 标志的中文名按 CP437 解出乱码文件名，导致内置 wiki
            # 文档目录名/文件名损坏）。每条目先做路径安全校验（防 Zip Slip）。
            for info in zf.infolist():
                fixed = _fix_zip_name(info.filename)
                target = _safe_zip_target(extracted, fixed)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # 定位顶层目录：优先选含 Tavily.exe 的目录；zip 根直接放 Tavily.exe 也可
        top = next((c for c in extracted.iterdir() if c.is_dir() and (c / "Tavily.exe").is_file()),
                   None)
        if top is None:
            top = extracted if (extracted / "Tavily.exe").is_file() else None
        if top is None:
            raise RuntimeError("压缩包内未找到 Tavily.exe，无法自动更新")
        # 顶层目录名白名单：含 cmd 元字符时拒绝（防 apply_update.bat 注入，纵深防御）
        if top != extracted and any(ch in top.name for ch in _CMD_METACHARS):
            raise RuntimeError("压缩包顶层目录名包含非法字符，无法自动更新")
        if gen != _dl_epoch:
            _log.info("下载线程已过期（新任务已启动），丢弃本次结果")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        _set_dl(state="done", received=n, total=n, path=str(top), version=version, body=body)
        _log.info("更新包下载并解压完成：%s", top)
    except _DownloadCancelled:
        _log.info("自动更新下载已取消，清理临时文件")
        if gen == _dl_epoch:
            _set_dl(state="idle", received=0, total=0, error="", version="", path="",
                    body="", tmp="")
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        _log.error("自动更新下载失败：%s", err)
        if gen == _dl_epoch:
            _set_dl(state="error", error=err)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _bat_escape(s: object) -> str:
    """bat 内变量赋值转义：`%` 在 cmd 中触发变量展开，双写为 `%%` 保留字面值。"""
    return str(s).replace("%", "%%")


def _write_update_script(new_dir: str) -> Path:
    """生成 data/apply_update.bat：结束实例 → 备份旧版 → 部署新版 → 启动。

    脚本输出写 data/update_apply.log 便于排查。返回脚本路径。
    """
    from paths import base_dir, runtime_dir

    base = base_dir()
    log_path = runtime_dir() / "update_apply.log"
    bat = runtime_dir() / "apply_update.bat"
    # 路径统一转义：% 双写避免变量展开；cmd 元字符由解压阶段的白名单提前拦截
    content = f"""@echo off
chcp 65001 >nul
set "BASE={_bat_escape(base)}"
set "NEW={_bat_escape(new_dir)}"
set "LOG={_bat_escape(log_path)}"
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
    语义：update-pending.json 是 apply_update 写入的「应用未确认」标记——若存在
    （本次启动源于一次自动更新），【保留 backup-old】作为新版本首个启动周期的
    回滚依据，并删除标记；下次正常启动（无标记）再清理 backup-old。
    """
    try:
        from paths import base_dir, runtime_dir

        base = base_dir()
        bak = base / "backup-old"
        pending = runtime_dir() / "update-pending.json"
        keep_backup = False
        if pending.exists():
            pending.unlink(missing_ok=True)
            keep_backup = True
            _log.info("检测到更新残留标记：本次保留 backup-old 作为回滚依据（下次启动清理）")
        if not keep_backup and bak.exists():
            import shutil
            shutil.rmtree(bak, ignore_errors=True)
            _log.info("已清理旧版本备份：%s", bak)
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

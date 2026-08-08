"""异常通知：Webhook + 托盘气泡，带同 key 同类型去重/节流。

`check_and_notify(pool, tray)` 供 dashboard 后台线程周期调用（周期见配置
`notify_interval_minutes`）：
- 检测新增/变化的异常（exhausted / near_exhausted / suspected_leak /
  high_error_rate / stale / slow）与「池空」全局事件（存在 key 但全部不可用）；
- 同一 (masked, flag) 在去重窗口内只通知一次，避免每 5 分钟刷屏；
- 通知渠道：Webhook（`notify_webhook`，POST JSON）与 Windows 托盘气泡
  （`notify_tray`）；两个渠道都未配置/不可用时直接跳过。
"""
from __future__ import annotations

import json
import threading
import time

from key_pool import KeyPool
from logging_setup import get_logger
from settings import get_settings
from tray import NIIF_ERROR, NIIF_INFO, NIIF_WARNING

_log = get_logger("notify")

# 同一 (masked, flag) 去重窗口（秒）：窗口内不重复通知
_DEDUPE_SECONDS = 3600.0
# 池空全局告警去重窗口（秒）
_POOL_EMPTY_DEDUPE = 1800.0

_notified: dict[tuple[str, str], float] = {}
_lock = threading.Lock()

FLAG_LABELS = {
    "exhausted": "额度耗尽",
    "near_exhausted": "近耗尽",
    "suspected_leak": "疑似泄露",
    "high_error_rate": "高错误率",
    "stale": "静默失效",
    "slow": "延迟异常",
    "pool_empty": "全部 Key 不可用",
}

# 通知类型 → 托盘气泡图标（NIIF_*）；未列出的 flag 回退 NIIF_INFO
FLAG_ICONS = {
    "exhausted": NIIF_ERROR,
    "high_error_rate": NIIF_ERROR,
    "pool_empty": NIIF_ERROR,
    "near_exhausted": NIIF_WARNING,
    "suspected_leak": NIIF_WARNING,
    "stale": NIIF_INFO,
    "slow": NIIF_INFO,
}


def _mark_notified(key: tuple[str, str], window: float) -> bool:
    """若未在窗口内通知过则标记并返回 True（本次应通知）。"""
    now = time.time()
    with _lock:
        last = _notified.get(key, 0.0)
        if now - last < window:
            return False
        _notified[key] = now
        # 防止无界增长：超过 1000 条时清理已过期条目
        if len(_notified) > 1000:
            cutoff = now - 86400
            stale = [k for k, v in _notified.items() if v < cutoff]
            for k in stale:
                _notified.pop(k, None)
        return True


def _forget_notified(key: tuple[str, str]) -> None:
    """推送失败时回滚去重标记，保证下一轮检测会重试（与 updater 语义一致）。"""
    with _lock:
        _notified.pop(key, None)


def send_webhook(url: str, payload: dict) -> bool:
    """POST JSON 到 Webhook URL（urllib，短超时，失败仅记日志）。"""
    if not url:
        return False
    try:
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001
        _log.warning("webhook 推送失败: %s", str(e)[:200])
        return False


def _notify_one(tray, webhook: str, title: str, message: str, payload: dict, icon: int = NIIF_INFO) -> bool:
    """按配置推送单个通知（托盘 + webhook），返回是否至少推送了一个渠道。

    icon: 托盘气泡图标（NIIF_*）；托盘失败时降级到 webhook 并记日志。
    """
    sent = False
    if tray is not None:
        try:
            tray.notify(title, message, icon=icon)
            sent = True
        except Exception as e:  # noqa: BLE001
            _log.warning("托盘通知异常（降级到 webhook）: %s", str(e)[:200])
    if webhook:
        payload.setdefault("title", title)
        payload.setdefault("message", message)
        sent = send_webhook(webhook, payload) or sent
    return sent


def check_and_notify(pool: KeyPool, tray=None) -> list[dict]:
    """检测异常并通知新增项，返回本次实际通知的列表（空列表 = 无新增/无配置）。"""
    cfg = get_settings()
    webhook = (cfg.get("notify_webhook") or "").strip()
    use_tray = bool(cfg.get("notify_tray", True)) and tray is not None
    if not webhook and not use_tray:
        return []

    anomalies = pool.detect_anomalies()
    notified: list[dict] = []

    # 池空告警：存在 key 但全部不可用（active 且未耗尽为空）
    keys = pool.list_keys()
    if keys:
        available = [k for k in keys if k.is_active and not k.is_exhausted]
        if not available:
            key = ("__pool__", "pool_empty")
            if _mark_notified(key, _POOL_EMPTY_DEDUPE):
                msg = "Key 池中所有 Key 均不可用（耗尽/停用），MCP 调用将全部失败！"
                if _notify_one(
                    tray,
                    webhook,
                    "Tavily Key 池耗尽",
                    msg,
                    {"type": "anomaly", "masked": "__pool__", "flag": "pool_empty", "reasons": [msg]},
                    icon=FLAG_ICONS["pool_empty"],
                ):
                    notified.append({"masked": "__pool__", "flags": ["pool_empty"], "reason": msg})
                else:
                    _forget_notified(key)

    for a in anomalies:
        for flag in a["flags"]:
            key = (a["masked"], flag)
            if not _mark_notified(key, _DEDUPE_SECONDS):
                continue
            label = FLAG_LABELS.get(flag, flag)
            reasons = a.get("reasons") or []
            reason = "；".join(reasons) or label
            title = f"Tavily Key 异常：{a['masked']}"
            message = f"{label}：{reason}"
            payload = {
                "type": "anomaly",
                "masked": a["masked"],
                "flag": flag,
                "reasons": reasons,
                "usage_pct": a.get("usage_pct"),
            }
            if _notify_one(tray, webhook, title, message, payload, icon=FLAG_ICONS.get(flag, NIIF_INFO)):
                notified.append({"masked": a["masked"], "flags": [flag], "reason": reason})
            else:
                _forget_notified(key)

    if notified:
        _log.info("异常通知 %d 条", len(notified))
    return notified

"""
Tavily API Key Pool — SQLite-backed key rotation, usage tracking, load balancing.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from security import encrypt_text, is_ciphertext, decrypt_text
from logging_setup import get_logger

_log = get_logger("key_pool")


def _app_dir() -> Path:
    """数据库文件的存放目录：打包后为 exe 所在目录，开发时为项目根目录（app 的上级）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


DB_PATH = _app_dir() / "tavily_keys.db"


@dataclass
class ApiKey:
    key: str
    masked: str
    is_active: bool
    request_count: int
    error_count: int
    credits_used: int
    credits_limit: int
    last_used_at: float
    added_at: float
    last_error: str

    @property
    def usage_pct(self) -> float:
        if self.credits_limit <= 0:
            return 0.0
        return (self.credits_used / self.credits_limit) * 100


class KeyPool:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: str | None = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str | None = None):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._db = db_path or str(DB_PATH)
        self._local = threading.local()
        self._next_index = 0
        self._index_lock = threading.Lock()
        self._init_db()

    # ── DB helpers ──────────────────────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=3000")
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self._db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                masked TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                request_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                credits_used INTEGER DEFAULT 0,
                credits_limit INTEGER DEFAULT 0,
                last_used_at REAL DEFAULT 0,
                added_at REAL NOT NULL,
                last_error TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_masked TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                credits_consumed INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_msg TEXT DEFAULT '',
                latency_ms REAL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        # masked 唯一索引：加密后 key 主键不可靠，作为重复检测的兜底约束。
        # 历史数据若存在重复 masked（理论不会），容忍创建失败。
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_masked ON api_keys(masked)"
            )
        except sqlite3.Error:  # noqa: BLE001
            pass
        conn.commit()
        conn.close()

    # ── Key CRUD ────────────────────────────────────────────────
    def add_key(self, key: str) -> ApiKey:
        key = key.strip()
        masked = _mask(key)
        now = time.time()
        conn = self._get_conn()
        # 注意：key 字段现在存储密文（加密后同一 key 每次密文不同），
        # 主键无法再承担唯一性检测，改以 masked（明文不变）判断重复。
        exists = conn.execute(
            "SELECT 1 FROM api_keys WHERE masked=?", (masked,)
        ).fetchone()
        if exists:
            raise ValueError(f"Key {masked} already exists")
        conn.execute(
            "INSERT INTO api_keys (key, masked, added_at) VALUES (?,?,?)",
            (encrypt_text(key), masked, now),
        )
        conn.commit()
        return self.get_key(masked)

    def add_keys_batch(self, keys: list[str]) -> int:
        added = 0
        conn = self._get_conn()
        now = time.time()
        for k in keys:
            k = k.strip()
            if not k:
                continue
            masked = _mask(k)
            # 同上：加密后 key 主键不可靠，按 masked 判断重复
            exists = conn.execute(
                "SELECT 1 FROM api_keys WHERE masked=?", (masked,)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO api_keys (key, masked, added_at) VALUES (?,?,?)",
                (encrypt_text(k), masked, now),
            )
            added += 1
        conn.commit()
        return added

    def remove_key(self, masked_or_key: str):
        conn = self._get_conn()
        if "****" in masked_or_key:
            conn.execute("DELETE FROM api_keys WHERE masked = ?", (masked_or_key,))
        else:
            conn.execute("DELETE FROM api_keys WHERE key = ?", (masked_or_key,))
        conn.commit()

    def deactivate_key(self, masked: str, reason: str = ""):
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_active=0, last_error=? WHERE masked=?",
            (reason, masked),
        )
        conn.commit()

    def activate_key(self, masked: str):
        conn = self._get_conn()
        conn.execute("UPDATE api_keys SET is_active=1, error_count=0 WHERE masked=?", (masked,))
        conn.commit()

    def _decrypt_or_migrate(self, stored: str, masked: str) -> str:
        """解密存储的 key；若为旧明文（无加密前缀），就地加密迁移后返回明文。"""
        if is_ciphertext(stored):
            return decrypt_text(stored)
        # 旧明文：尝试加密写回（后端不可用时 enc == stored，跳过迁移）
        try:
            enc = encrypt_text(stored)
            if enc != stored:
                conn = self._get_conn()
                conn.execute(
                    "UPDATE api_keys SET key=? WHERE masked=?", (enc, masked)
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            _log.warning("key 迁移加密失败 masked=%s: %s", masked, e)
        return stored

    def _materialize(self, row: sqlite3.Row) -> ApiKey:
        """把数据库行转换为 ApiKey（解密 key 字段）。"""
        return ApiKey(
            key=self._decrypt_or_migrate(row["key"], row["masked"]),
            masked=row["masked"],
            is_active=bool(row["is_active"]),
            request_count=row["request_count"],
            error_count=row["error_count"],
            credits_used=row["credits_used"],
            credits_limit=row["credits_limit"],
            last_used_at=row["last_used_at"],
            added_at=row["added_at"],
            last_error=row["last_error"],
        )

    def list_keys(self) -> list[ApiKey]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM api_keys ORDER BY added_at DESC"
        ).fetchall()
        return [self._materialize(r) for r in rows]

    def get_key(self, masked: str) -> ApiKey | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM api_keys WHERE masked=?", (masked,)
        ).fetchone()
        return self._materialize(row) if row else None

    def get_key_count(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM api_keys WHERE is_active=1").fetchone()
        return row["cnt"]

    # ── Load balancing ─────────────────────────────────────────
    def next_key(self) -> tuple[str, str] | None:
        """Return (raw_key, masked) of next key via round-robin among active keys."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT key, masked FROM api_keys WHERE is_active=1 ORDER BY last_used_at ASC"
        ).fetchall()
        if not rows:
            return None
        with self._index_lock:
            idx = self._next_index % len(rows)
            self._next_index = (idx + 1) % len(rows)
        row = rows[idx]
        return (self._decrypt_or_migrate(row["key"], row["masked"]), row["masked"])

    def next_key_least_used(self) -> tuple[str, str] | None:
        """Return key with fewest requests today (based on request_log)."""
        conn = self._get_conn()
        day_start = time.time() - 86400
        rows = conn.execute(
            """
            SELECT k.key, k.masked,
                   (SELECT COUNT(*) FROM request_log r
                    WHERE r.key_masked = k.masked AND r.created_at > ?) AS today_count
            FROM api_keys k
            WHERE k.is_active = 1
            ORDER BY today_count ASC, k.last_used_at ASC
            """,
            (day_start,),
        ).fetchall()
        if not rows:
            return None
        row = rows[0]
        return (self._decrypt_or_migrate(row["key"], row["masked"]), row["masked"])

    # ── Usage recording ────────────────────────────────────────
    def record_request(self, masked: str, endpoint: str, latency_ms: float, success: bool,
                       credits: int = 0, error_msg: str = ""):
        now = time.time()
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET request_count=request_count+1, last_used_at=? WHERE masked=?",
            (now, masked),
        )
        if success:
            conn.execute(
                "UPDATE api_keys SET credits_used=credits_used+? WHERE masked=?",
                (credits, masked),
            )
        else:
            conn.execute(
                "UPDATE api_keys SET error_count=error_count+1, last_error=? WHERE masked=?",
                (error_msg[:500], masked),
            )
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
            (masked, endpoint, credits, 1 if success else 0, error_msg[:500], latency_ms, now),
        )
        conn.commit()

    # ── Health check ────────────────────────────────────────────
    def check_health(self, key_masked: str | None = None) -> list[dict]:
        """Probe one or all active keys with a lightweight search.

        防误伤策略（历史教训：tavily 库缺失时曾把整个池误判失效清空）：
        - tavily 库不可用：直接返回全局错误，不处理任何 key、不停用
        - 认证错误(401/403/Unauthorized)：立即停用该 key
        - 空响应：立即停用该 key
        - 其他临时错误(超时/网络抖动)：累计 error_count，连续失败 >= 2 次才停用
        并发探测（最多 5 个并行），结果带 error_category 供前端区分。
        """
        # 前置检查：tavily 库必须可用，否则绝不处理任何 key
        try:
            import tavily  # noqa: F401
        except Exception as e:  # noqa: BLE001
            _log.error("tavily 库不可用，跳过健康检查（不处理任何 key）: %s", e)
            return [{"error": f"tavily 库不可用: {e}", "error_category": "fatal"}]

        keys = [self.get_key(key_masked)] if key_masked else self.list_keys()
        keys = [k for k in keys if k and k.is_active]
        if not keys:
            return []

        def _probe(k: ApiKey) -> dict:
            t0 = time.time()
            try:
                client = tavily.TavilyClient(k.key)
                resp = client.search("test", max_results=1, search_depth="basic", timeout=5)
                elapsed = round((time.time() - t0) * 1000)
                ok = isinstance(resp, dict) and len(resp.get("results", [])) > 0
                if ok:
                    return {"masked": k.masked, "alive": True, "latency_ms": elapsed, "error_category": "ok"}
                return {"masked": k.masked, "alive": False, "error": "empty response",
                        "error_category": "empty", "latency_ms": elapsed}
            except Exception as e:  # noqa: BLE001
                err = str(e)
                _log.warning("健康检查失败 masked=%s: %s", k.masked, err[:300])
                return {"masked": k.masked, "alive": False, "error": err,
                        "error_category": _classify_error(err),
                        "latency_ms": round((time.time() - t0) * 1000)}

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(5, len(keys))) as ex:
            for r in ex.map(_probe, keys):
                results.append(r)
                if r["alive"]:
                    continue
                cat = r.get("error_category", "other")
                if cat == "auth":
                    self.deactivate_key(r["masked"], f"health-check auth: {r['error'][:200]}")
                elif cat == "empty":
                    self.deactivate_key(r["masked"], "health-check: empty response")
                else:
                    # 临时错误：累计 error_count，连续失败 >= 2 次才停用
                    conn = self._get_conn()
                    conn.execute(
                        "UPDATE api_keys SET error_count=error_count+1, last_error=? WHERE masked=?",
                        (r["error"][:500], r["masked"]),
                    )
                    conn.commit()
                    k2 = self.get_key(r["masked"])
                    if k2 is not None and k2.error_count >= 2:
                        self.deactivate_key(r["masked"], f"health-check consecutive: {r['error'][:200]}")
        return results

    def check_health_all(self) -> list[dict]:
        return self.check_health()

    # ── Official usage sync ───────────────────────────────────
    def sync_usage(self) -> list[dict]:
        """从 Tavily 官方 /usage 同步每个 active key 的 billing cycle 真实用量。

        tavily-python 0.7.x 未暴露 usage 方法，这里用 httpx 直调
        GET https://api.tavily.com/usage（Authorization: Bearer <key>）。
        用官方数值覆盖本地近似计数（credits_used / credits_limit）。
        """
        import httpx

        results: list[dict] = []
        for k in self.list_keys():
            if not k.is_active:
                continue
            try:
                r = httpx.get(
                    "https://api.tavily.com/usage",
                    headers={"Authorization": f"Bearer {k.key}"},
                    timeout=20,
                )
                if r.status_code != 200:
                    _log.warning("usage 同步 HTTP %s masked=%s", r.status_code, k.masked)
                    results.append({"masked": k.masked, "ok": False, "error": f"HTTP {r.status_code}"})
                    continue
                data = r.json()
                key_usage = data.get("key") or {}
                usage = int(key_usage.get("usage") or 0)
                limit_raw = key_usage.get("limit")
                limit = int(limit_raw) if limit_raw is not None else 0
                conn = self._get_conn()
                conn.execute(
                    "UPDATE api_keys SET credits_used=?, credits_limit=? WHERE masked=?",
                    (usage, limit, k.masked),
                )
                conn.commit()
                account = data.get("account") or {}
                results.append({
                    "masked": k.masked,
                    "ok": True,
                    "usage": usage,
                    "limit": limit_raw,
                    "search_usage": key_usage.get("search_usage", 0),
                    "extract_usage": key_usage.get("extract_usage", 0),
                    "crawl_usage": key_usage.get("crawl_usage", 0),
                    "map_usage": key_usage.get("map_usage", 0),
                    "research_usage": key_usage.get("research_usage", 0),
                    "plan": account.get("current_plan", ""),
                    "plan_usage": account.get("plan_usage", 0),
                    "plan_limit": account.get("plan_limit"),
                })
            except Exception as e:  # noqa: BLE001
                _log.warning("usage 同步失败 masked=%s: %s", k.masked, e)
                results.append({"masked": k.masked, "ok": False, "error": str(e)})
        return results

    # ── Stats ───────────────────────────────────────────────────
    def get_stats(self) -> dict:
        conn = self._get_conn()
        keys = self.list_keys()
        total_requests = sum(k.request_count for k in keys)
        total_errors = sum(k.error_count for k in keys)
        total_credits = sum(k.credits_used for k in keys)
        active_count = sum(1 for k in keys if k.is_active)
        recent = conn.execute(
            "SELECT endpoint, success, COUNT(*) as cnt FROM request_log WHERE created_at > ? GROUP BY endpoint, success",
            (time.time() - 86400,),
        ).fetchall()

        recent_by_endpoint: dict[str, dict] = {}
        for r in recent:
            ep = r["endpoint"]
            if ep not in recent_by_endpoint:
                recent_by_endpoint[ep] = {"success": 0, "failed": 0}
            if r["success"]:
                recent_by_endpoint[ep]["success"] += r["cnt"]
            else:
                recent_by_endpoint[ep]["failed"] += r["cnt"]

        return {
            "keys": [_apikey_to_dict(k) for k in keys],
            "total_keys": len(keys),
            "active_keys": active_count,
            "total_requests": total_requests,
            "total_errors": total_errors,
            "total_credits": total_credits,
            "recent_24h": recent_by_endpoint,
        }

    def get_recent_logs(self, limit: int = 100) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM request_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def _mask(key: str) -> str:
    if key.startswith("tvly-"):
        return key[:12] + "****" + key[-4:]
    return key[:4] + "****" + key[-4:] if len(key) > 8 else "****"


def _classify_error(err: str) -> str:
    """把健康检查异常分类：auth(认证)/other(临时错误)。"""
    e = (err or "").lower()
    if any(s in e for s in (
        "401", "403", "unauthorized", "invalid api key", "forbidden",
        "authentication", "api key"
    )):
        return "auth"
    return "other"


def _apikey_to_dict(k: ApiKey) -> dict:
    return {
        "masked": k.masked,
        "is_active": k.is_active,
        "request_count": k.request_count,
        "error_count": k.error_count,
        "credits_used": k.credits_used,
        "credits_limit": k.credits_limit,
        "usage_pct": round(k.usage_pct, 1),
        "last_used_at": k.last_used_at,
        "added_at": k.added_at,
        "last_error": k.last_error,
    }

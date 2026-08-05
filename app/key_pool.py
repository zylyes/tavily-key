"""
Tavily API Key Pool — SQLite-backed key rotation, usage tracking, load balancing.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from security import encrypt_text, is_ciphertext, decrypt_text
from logging_setup import get_logger
from settings import get_settings
from paths import runtime_dir

_log = get_logger("key_pool")

# 官方开发环境限流 100 RPM，默认按 90% 留余量
DEFAULT_RPM = 90


class _TokenBucket:
    """按 key 的令牌桶：速率取自配置 rate_limit_rpm，threading 安全。"""

    def __init__(self, rate_per_min: int):
        self._rate = max(1, int(rate_per_min)) / 60.0  # tokens/sec
        self._capacity = max(1, int(rate_per_min))
        self._tokens = float(self._capacity)
        self._updated = time.time()
        self._lock = threading.Lock()

    def try_acquire(self, cost: int = 1) -> float:
        """尝试取 cost 个令牌；成功返回 0，否则返回需要等待的秒数。"""
        with self._lock:
            now = time.time()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens >= cost:
                self._tokens -= cost
                return 0.0
            return (cost - self._tokens) / self._rate

    def wait_time(self, cost: int = 1) -> float:
        """不消耗令牌，返回取 cost 个令牌需要等待的秒数（0 表示立即可用）。"""
        with self._lock:
            now = time.time()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens >= cost:
                return 0.0
            return (cost - self._tokens) / self._rate


def _ensure_columns(conn, table: str, cols: dict[str, str]):
    """旧库迁移：为表补充缺失的列（ALTER TABLE ADD COLUMN，容忍失败）。"""
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in cols.items():
        if name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            except sqlite3.Error:  # noqa: BLE001
                pass


DB_PATH = runtime_dir() / "tavily_keys.db"


@dataclass
class ApiKey:
    key: str
    masked: str
    is_active: bool
    is_exhausted: bool
    request_count: int
    error_count: int
    credits_used: int
    credits_limit: int
    last_used_at: float
    added_at: float
    last_error: str
    plan: str = ""
    plan_usage: int = 0
    plan_limit: int = 0
    usage_synced_at: float = 0.0

    @property
    def effective_limit(self) -> int:
        """有效积分上限：优先 key 自身额度；无限额（credits_limit<=0）时回退账户套餐额度。"""
        if self.credits_limit > 0:
            return self.credits_limit
        return self.plan_limit

    @property
    def usage_pct(self) -> float:
        limit = self.effective_limit
        if limit <= 0:
            return 0.0
        return (self.credits_used / limit) * 100


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
        self._buckets: dict[str, _TokenBucket] = {}
        self._bucket_lock = threading.Lock()
        self._usage_cache: dict[str, tuple[float, dict]] = {}
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
                is_exhausted INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                credits_used INTEGER DEFAULT 0,
                credits_limit INTEGER DEFAULT 0,
                last_used_at REAL DEFAULT 0,
                added_at REAL NOT NULL,
                last_error TEXT DEFAULT '',
                plan TEXT DEFAULT '',
                plan_usage INTEGER DEFAULT 0,
                plan_limit INTEGER DEFAULT 0,
                usage_synced_at REAL DEFAULT 0
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
                request_id TEXT DEFAULT '',
                created_at REAL NOT NULL
            )
            """
        )
        # 旧库迁移：补充新增列
        _ensure_columns(conn, "api_keys", {
            "is_exhausted": "INTEGER DEFAULT 0",
            "plan": "TEXT DEFAULT ''",
            "plan_usage": "INTEGER DEFAULT 0",
            "plan_limit": "INTEGER DEFAULT 0",
            "usage_synced_at": "REAL DEFAULT 0",
        })
        _ensure_columns(conn, "request_log", {
            "request_id": "TEXT DEFAULT ''",
        })
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
        conn.execute(
            "UPDATE api_keys SET is_active=1, is_exhausted=0, error_count=0 WHERE masked=?",
            (masked,),
        )
        conn.commit()

    def mark_exhausted(self, masked: str, reason: str = ""):
        """标记 key 额度耗尽（432/433）：移出轮询，但保留可恢复（月度重置后自动恢复）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_exhausted=1, last_error=? WHERE masked=?",
            (reason[:500] or "exhausted", masked),
        )
        conn.commit()

    def mark_recovered(self, masked: str):
        """清除额度耗尽标记（新计费周期 usage 归零时调用）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_exhausted=0, error_count=0 WHERE masked=?",
            (masked,),
        )
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
            is_exhausted=bool(row["is_exhausted"]),
            request_count=row["request_count"],
            error_count=row["error_count"],
            credits_used=row["credits_used"],
            credits_limit=row["credits_limit"],
            last_used_at=row["last_used_at"],
            added_at=row["added_at"],
            last_error=row["last_error"],
            plan=row["plan"] or "",
            plan_usage=row["plan_usage"] or 0,
            plan_limit=row["plan_limit"] or 0,
            usage_synced_at=row["usage_synced_at"] or 0.0,
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
    def _active_rows(self) -> list[sqlite3.Row]:
        conn = self._get_conn()
        return conn.execute(
            "SELECT key, masked FROM api_keys WHERE is_active=1 AND is_exhausted=0 "
            "ORDER BY last_used_at ASC"
        ).fetchall()

    def _bucket(self, masked: str) -> _TokenBucket:
        with self._bucket_lock:
            b = self._buckets.get(masked)
            if b is None:
                rpm = int(get_settings().get("rate_limit_rpm") or DEFAULT_RPM)
                b = _TokenBucket(rpm)
                self._buckets[masked] = b
            return b

    def _consume_bucket(self, masked: str) -> None:
        self._bucket(masked).try_acquire(1)

    def next_key(self, skip_limited: bool = False) -> tuple[str, str] | None:
        """Round-robin 取下一个 active 且未耗尽的 key。

        skip_limited=True 时跳过当前受限（令牌不足）的 key。
        """
        rows = self._active_rows()
        if not rows:
            return None
        with self._index_lock:
            idx = self._next_index % len(rows)
            self._next_index = (idx + 1) % len(rows)
        for off in range(len(rows)):
            row = rows[(idx + off) % len(rows)]
            masked = row["masked"]
            if not skip_limited or self._bucket(masked).wait_time() <= 0:
                self._consume_bucket(masked)
                return (self._decrypt_or_migrate(row["key"], masked), masked)
        return None

    def next_key_least_used(self, skip_limited: bool = False) -> tuple[str, str] | None:
        """取今日请求最少的 active 且未耗尽 key。

        skip_limited=True 时跳过当前受限（令牌不足）的 key。
        """
        conn = self._get_conn()
        day_start = time.time() - 86400
        rows = conn.execute(
            """
            SELECT k.key, k.masked,
                   (SELECT COUNT(*) FROM request_log r
                    WHERE r.key_masked = k.masked AND r.created_at > ?) AS today_count
            FROM api_keys k
            WHERE k.is_active = 1 AND k.is_exhausted = 0
            ORDER BY today_count ASC, k.last_used_at ASC
            """,
            (day_start,),
        ).fetchall()
        if not rows:
            return None
        for row in rows:
            masked = row["masked"]
            if not skip_limited or self._bucket(masked).wait_time() <= 0:
                self._consume_bucket(masked)
                return (self._decrypt_or_migrate(row["key"], masked), masked)
        return None

    def next_available_key(self) -> tuple[str, str] | None:
        """按配置策略取一个当前可用（未超限流）的 key；全部受限时短暂等待最早的可用时机。"""
        strategy = (get_settings().get("key_strategy") or "round-robin").strip().lower()
        pick = self.next_key_least_used if strategy == "least-used" else self.next_key
        r = pick(skip_limited=True)
        if r is not None:
            return r
        # 全部受限：等待最短等待时间的 key（上限 rate_limit_max_wait 秒）
        max_wait = float(get_settings().get("rate_limit_max_wait") or 1.0)
        deadline = time.time() + max_wait
        best: tuple[str, str] | None = None
        best_wait = float("inf")
        for row in self._active_rows():
            masked = row["masked"]
            w = self._bucket(masked).wait_time()
            if w < best_wait:
                best_wait = w
                best = (self._decrypt_or_migrate(row["key"], masked), masked)
        if best is None:
            return None
        if best_wait > 0:
            wait = min(best_wait, max(0.0, deadline - time.time()))
            if wait > 0:
                time.sleep(wait)
        self._consume_bucket(best[1])
        return best

    # ── Usage recording ────────────────────────────────────────
    def record_request(self, masked: str, endpoint: str, latency_ms: float, success: bool,
                       credits: int = 0, error_msg: str = "", request_id: str = ""):
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
            cat = _classify_error(error_msg)
            conn.execute(
                "UPDATE api_keys SET error_count=error_count+1, last_error=? WHERE masked=?",
                (error_msg[:500], masked),
            )
            if cat == "quota":
                # 额度耗尽：移出轮询（下月自动恢复），请求层已自动切换其他 key
                self.mark_exhausted(masked, f"quota: {error_msg[:200]}")
            elif cat == "auth":
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE masked=?", (masked,)
                )
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (masked, endpoint, credits, 1 if success else 0, error_msg[:500], latency_ms, request_id[:200], now),
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
                elif cat == "quota":
                    self.mark_exhausted(r["masked"], f"health-check quota: {r['error'][:200]}")
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
    def sync_usage(self, masked: list[str] | None = None) -> list[dict]:
        """从 Tavily 官方 /usage 同步 billing cycle 真实用量。

        - 每 key 独立限流（10 次/10 分钟），多账户并发安全，8 并发拉取。
        - TTL 缓存（usage_cache_ttl，默认 60s）：同一 key 短时间重复同步直接返回缓存。
        - 记录 plan/plan_usage/plan_limit 与同步时间。
        - 月度重置检测：exhausted key 的 usage 归零/低于 limit → 自动恢复（is_exhausted=0）。
        """
        import httpx

        keys = [self.get_key(m) for m in masked] if masked else self.list_keys()
        keys = [k for k in keys if k and k.is_active]
        if not keys:
            return []

        ttl = float(get_settings().get("usage_cache_ttl") or 60.0)
        now = time.time()

        def _sync(k: ApiKey) -> dict:
            # TTL 缓存命中：直接返回上次结果
            cached = self._usage_cache.get(k.masked)
            if cached and now - cached[0] < ttl:
                return dict(cached[1])
            try:
                r = httpx.get(
                    "https://api.tavily.com/usage",
                    headers={"Authorization": f"Bearer {k.key}"},
                    timeout=20,
                )
                if r.status_code != 200:
                    _log.warning("usage 同步 HTTP %s masked=%s", r.status_code, k.masked)
                    return {"masked": k.masked, "ok": False, "error": f"HTTP {r.status_code}"}
                data = r.json()
                key_usage = data.get("key") or {}
                usage = int(key_usage.get("usage") or 0)
                limit_raw = key_usage.get("limit")
                account = data.get("account") or {}
                plan = account.get("current_plan", "")
                plan_usage = int(account.get("plan_usage") or 0)
                plan_limit_raw = account.get("plan_limit")
                plan_limit = int(plan_limit_raw) if plan_limit_raw is not None else 0
                # 单个 key 无限额（/usage 返回 limit=null）时，回退账户套餐额度作为上限，
                # 保证「用量 % / 剩余积分」有可计算的分母（如免费套餐每月 1000）。
                limit = int(limit_raw) if limit_raw is not None else (plan_limit if plan_limit > 0 else 0)
                conn = self._get_conn()
                conn.execute(
                    "UPDATE api_keys SET credits_used=?, credits_limit=?, plan=?, plan_usage=?, plan_limit=?, usage_synced_at=? WHERE masked=?",
                    (usage, limit, plan, plan_usage, plan_limit, now, k.masked),
                )
                conn.commit()
                # 月度重置检测：exhausted key 的 usage 归零/低于 limit → 自动恢复
                recovered = bool(k.is_exhausted and limit > 0 and usage < limit)
                if recovered:
                    self.mark_recovered(k.masked)
                    _log.info("key 额度已重置，自动恢复 masked=%s", k.masked)
                out = {
                    "masked": k.masked,
                    "ok": True,
                    "usage": usage,
                    "limit": limit_raw,
                    "search_usage": key_usage.get("search_usage", 0),
                    "extract_usage": key_usage.get("extract_usage", 0),
                    "crawl_usage": key_usage.get("crawl_usage", 0),
                    "map_usage": key_usage.get("map_usage", 0),
                    "research_usage": key_usage.get("research_usage", 0),
                    "plan": plan,
                    "plan_usage": plan_usage,
                    "plan_limit": plan_limit,
                    "recovered": recovered,
                }
                self._usage_cache[k.masked] = (now, out)
                return out
            except Exception as e:  # noqa: BLE001
                _log.warning("usage 同步失败 masked=%s: %s", k.masked, e)
                return {"masked": k.masked, "ok": False, "error": str(e)}

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as ex:
            for r in ex.map(_sync, keys):
                results.append(r)
        return results

    def sync_usage_one(self, masked: str) -> dict | None:
        """同步单个 active Key 的官方用量（供面板逐个进度展示）。"""
        results = self.sync_usage(masked=[masked])
        return results[0] if results else None

    # ── 聚合视图与异常识别 ────────────────────────────────────
    def get_aggregate(self) -> dict:
        """全池聚合容量：剩余总积分、已用总积分、可用 key 数等。"""
        keys = self.list_keys()
        total_limit = sum(k.effective_limit for k in keys)
        total_used = sum(k.credits_used for k in keys)
        active = [k for k in keys if k.is_active and not k.is_exhausted]
        exhausted = [k for k in keys if k.is_exhausted]
        return {
            "total_keys": len(keys),
            "active_keys": len(active),
            "exhausted_count": len(exhausted),
            "total_limit": total_limit,
            "total_used": total_used,
            "remaining": max(0, total_limit - total_used),
            "usage_pct": round((total_used / total_limit * 100), 1) if total_limit > 0 else 0.0,
        }

    def _local_credits(self, masked: str) -> float:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(credits_consumed),0) AS c FROM request_log WHERE key_masked=? AND success=1",
            (masked,),
        ).fetchone()
        return float(row["c"] or 0)

    def _recent_error_rate(self, masked: str) -> tuple[float, str]:
        """近 24h 错误率与最主要的错误类别。"""
        conn = self._get_conn()
        since = time.time() - 86400
        rows = conn.execute(
            "SELECT success, COUNT(*) AS cnt FROM request_log WHERE key_masked=? AND created_at > ? GROUP BY success",
            (masked, since),
        ).fetchall()
        total = sum(r["cnt"] for r in rows)
        if total == 0:
            return 0.0, ""
        fails = sum(r["cnt"] for r in rows if not r["success"])
        err_rows = conn.execute(
            "SELECT error_msg FROM request_log WHERE key_masked=? AND created_at > ? AND success=0",
            (masked, since),
        ).fetchall()
        types: dict[str, int] = {}
        for r in err_rows:
            cat = _classify_error(r["error_msg"])
            types[cat] = types.get(cat, 0) + 1
        top = max(types, key=types.get) if types else "other"
        return (fails / total), top

    def _avg_latency(self, masked: str) -> float:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT AVG(latency_ms) AS avg FROM request_log WHERE key_masked=? AND success=1 AND created_at > ?",
            (masked, time.time() - 86400),
        ).fetchone()
        return float(row["avg"] or 0)

    def detect_anomalies(self) -> list[dict]:
        """结合本地调用记录与官方用量，识别异常 key。

        规则（阈值见 config.anomaly_thresholds）：
        - exhausted        : is_exhausted（官方 usage >= limit，已触发 432）
        - near_exhausted   : 官方 usage_pct >= 90%
        - suspected_leak   : 官方 usage 明显大于本地累计 credits（差值 > leak_diff_credits）
        - high_error_rate  : 近 24h 错误率 > error_rate
        - stale            : active 但近 stale_days 天无本地调用
        - slow             : 近 24h 平均延迟 > 池内均值 * slow_ratio
        """
        thresholds = get_settings().get("anomaly_thresholds") or {}
        error_rate_th = float(thresholds.get("error_rate", 0.3))
        leak_diff = float(thresholds.get("leak_diff_credits", 50))
        stale_days = float(thresholds.get("stale_days", 7))
        slow_ratio = float(thresholds.get("slow_ratio", 2.0))
        conn = self._get_conn()
        keys = self.list_keys()
        if not keys:
            return []
        row = conn.execute(
            "SELECT AVG(latency_ms) AS avg FROM request_log WHERE success=1 AND created_at > ?",
            (time.time() - 86400,),
        ).fetchone()
        pool_avg_latency = row["avg"] or 0.0

        anomalies: list[dict] = []
        for k in keys:
            flags: list[str] = []
            reasons: list[str] = []
            if k.is_exhausted:
                flags.append("exhausted")
                reasons.append("官方额度已耗尽（432）")
            elif k.credits_limit > 0 and k.usage_pct >= 90:
                flags.append("near_exhausted")
                reasons.append(f"官方额度剩余 <10%（已用 {k.usage_pct:.1f}%）")
            if k.credits_limit > 0 and (k.credits_used - self._local_credits(k.masked)) >= leak_diff:
                flags.append("suspected_leak")
                reasons.append(f"官方用量比本地记录多 ≥{leak_diff:.0f} 积分，疑似被外部使用")
            rate, err_types = self._recent_error_rate(k.masked)
            if rate > error_rate_th:
                flags.append("high_error_rate")
                reasons.append(f"近24h错误率 {rate*100:.0f}%（{err_types}）")
            if k.is_active and not k.is_exhausted and k.last_used_at > 0:
                if (time.time() - k.last_used_at) > stale_days * 86400:
                    flags.append("stale")
                    reasons.append(f"{stale_days:.0f} 天无本地调用")
            if pool_avg_latency > 0:
                avg = self._avg_latency(k.masked)
                if avg > pool_avg_latency * slow_ratio and avg > 100:
                    flags.append("slow")
                    reasons.append(f"平均延迟 {avg:.0f}ms > 池均值 {pool_avg_latency:.0f}ms")
            if flags:
                anomalies.append({
                    "masked": k.masked,
                    "is_active": k.is_active,
                    "is_exhausted": k.is_exhausted,
                    "flags": flags,
                    "reasons": reasons,
                    "usage_pct": round(k.usage_pct, 1),
                    "credits_used": k.credits_used,
                    "credits_limit": k.credits_limit,
                })
        return anomalies

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
    """把错误分类：auth(认证)/quota(额度耗尽)/rate(限流)/other(临时错误)。"""
    e = (err or "").lower()
    if any(s in e for s in (
        "432", "433", "plan limit", "paygo", "usage limit",
        "exceeds your plan", "plan's set usage",
    )):
        return "quota"
    if any(s in e for s in (
        "401", "403", "unauthorized", "invalid api key", "forbidden",
        "authentication", "api key",
    )):
        return "auth"
    if any(s in e for s in (
        "429", "rate limit", "too many requests", "blocked due to excessive",
    )):
        return "rate"
    return "other"


def _apikey_to_dict(k: ApiKey) -> dict:
    return {
        "masked": k.masked,
        "is_active": k.is_active,
        "is_exhausted": k.is_exhausted,
        "request_count": k.request_count,
        "error_count": k.error_count,
        "credits_used": k.credits_used,
        "credits_limit": k.credits_limit,
        "usage_pct": round(k.usage_pct, 1),
        "last_used_at": k.last_used_at,
        "added_at": k.added_at,
        "last_error": k.last_error,
        "plan": k.plan,
        "plan_usage": k.plan_usage,
        "plan_limit": k.plan_limit,
        "usage_synced_at": k.usage_synced_at,
    }

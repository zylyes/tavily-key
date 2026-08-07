"""
Tavily API Key Pool — SQLite-backed key rotation, usage tracking, load balancing.
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from cache import TTLCache, emit_invalidate, signal_mtime
from logging_setup import get_logger
from paths import runtime_dir
from security import decrypt_text, encrypt_text, is_ciphertext
from settings import cache_ttls, get_settings, get_settings_fresh

_log = get_logger("key_pool")

# 官方开发环境限流 100 RPM，默认按 90% 留余量
DEFAULT_RPM = 90

# request_log 保留策略：默认保留 90 天，每插入 _LOG_PRUNE_EVERY 条触发一次清理，
# 避免长期运行 request_log 无界增长（可由配置 log_retention_days 覆盖，0=不清理）。
LOG_RETENTION_DAYS = 90
_LOG_PRUNE_EVERY = 500


class _TokenBucket:
    """按 key 的令牌桶：速率取自配置 rate_limit_rpm，threading 安全。"""

    def __init__(self, rate_per_min: int):
        self._rpm = max(1, int(rate_per_min))
        self._rate = self._rpm / 60.0  # tokens/sec
        self._capacity = self._rpm
        self._tokens = float(self._capacity)
        self._updated = time.time()
        self._lock = threading.Lock()

    @property
    def rate_per_min(self) -> int:
        """本桶的 RPM 速率（配置变化时用于判断是否需要重建桶）。"""
        return self._rpm

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
    research_usage: int = 0
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
        # 全部分配的 sqlite 连接（线程本地缓存 + 全局登记）：供 close_all_connections
        # 在恢复 data/ 前统一释放文件句柄（Windows 下占用会导致 rename 失败）
        self._conns: set[sqlite3.Connection] = set()
        self._conns_lock = threading.Lock()
        self._next_index = 0
        self._index_lock = threading.Lock()
        # 每 key × 每 endpoint 的令牌桶（各 key 来自不同账号、限流独立）：
        # {masked: {endpoint: _TokenBucket}}，research 创建任务 20 RPM、
        # crawl 100 RPM 等官方按 endpoint 独立的限流在每 key 内分别生效。
        self._buckets: dict[str, dict[str, _TokenBucket]] = {}
        self._bucket_lock = threading.Lock()
        self._usage_cache: dict[str, tuple[float, dict]] = {}
        # 重计算缓存（TTL 见 settings.cache_ttls）：异常识别与按天聚合结果
        # 基于近 24h 数据，秒级变化无意义，短 TTL 缓存避免每次轮询重复计算。
        self._anomalies_cache = TTLCache(default_ttl=5.0, maxsize=16)
        self._trend_cache = TTLCache(default_ttl=30.0, maxsize=32)
        # 跨进程失效信号：本进程最后见过的信号文件 mtime（其他进程写操作后广播）
        self._sig_mtime = 0.0
        self._log_inserts = 0
        self._init_db()

    # ── DB helpers ──────────────────────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            with self._conns_lock:
                if conn not in self._conns:
                    # 该连接已被 close_all_connections 关闭（可能发生在其他
                    # 线程，如恢复备份的 API 工作线程）：丢弃旧引用重建。
                    self._local.conn = None
                    conn = None
        if conn is None:
            # check_same_thread=False：每线程仍各自持有连接（_local.conn 保证
            # 线程内互不共享），但允许 close_all_connections 从其他线程（如
            # 恢复备份的 API 工作线程）安全关闭——默认 check_same_thread=True
            # 时跨线程 close() 抛 ProgrammingError 被吞，Windows 上文件句柄
            # 不释放，恢复备份会报「文件被占用」失败。
            conn = sqlite3.connect(self._db, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            with self._conns_lock:
                self._conns.add(conn)
            self._local.conn = conn
        return self._local.conn

    def reset_runtime_state(self) -> None:
        """重置进程内运行时状态（备份恢复后调用）。

        恢复的数据库可能换了 key 集合/用量，旧的限流桶、/usage 用量缓存与
        重计算缓存不再适用；同时广播跨进程失效信号让 MCP/proxy 子进程同步清缓存。
        """
        with self._bucket_lock:
            self._buckets.clear()
        self._usage_cache.clear()
        self._anomalies_cache.clear()
        self._trend_cache.clear()
        emit_invalidate()
        self._sig_mtime = signal_mtime()

    def close_all_connections(self) -> None:
        """关闭所有 sqlite 连接并清空线程本地缓存（恢复 data/ 前调用释放文件句柄）。

        之后的 _get_conn() 会重新连接当前（可能已恢复的）数据库文件。
        """
        with self._conns_lock:
            conns = list(self._conns)
            self._conns.clear()
        for c in conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        self._local.conn = None

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
                research_usage INTEGER DEFAULT 0,
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
                usage_source TEXT DEFAULT '',
                source TEXT DEFAULT '',
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
            "research_usage": "INTEGER DEFAULT 0",
            "usage_synced_at": "REAL DEFAULT 0",
        })
        _ensure_columns(conn, "request_log", {
            "request_id": "TEXT DEFAULT ''",
            # 积分来源标记：response（接口响应含 usage）/ unknown（接口不返回
            # usage，如 Research，官方 API 无此字段）/ none（失败请求）
            "usage_source": "TEXT DEFAULT ''",
            # 是否为客户端请求错误（HTTP 400/参数校验失败）：高错误率识别时忽略
            "is_client_error": "INTEGER DEFAULT 0",
            # 请求来源：mcp（MCP 工具调用）/ proxy（搜索代理 REST）/ cli（命令行）
            "source": "TEXT DEFAULT ''",
            # 项目归属：MCP 请求落 mcp_project_id 配置值，按项目拆分统计用量
            "project_id": "TEXT DEFAULT ''",
        })
        # masked 唯一索引：加密后 key 主键不可靠，作为重复检测的兜底约束。
        # 历史数据若存在重复 masked（理论不会），容忍创建失败。
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_masked ON api_keys(masked)"
            )
        except sqlite3.Error:  # noqa: BLE001
            pass
        # request_log 查询索引：日志分页（COUNT + ORDER BY created_at DESC）、
        # 用量趋势（GROUP BY date）、异常检测（按 key/endpoint/source/project
        # 聚合）此前全表扫描；90 天保留策略下数据量持续增长，补索引避免长期
        # 运行后日志页/统计页变慢。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_log_created ON request_log(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_log_key_created "
            "ON request_log(key_masked, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_log_endpoint_created "
            "ON request_log(endpoint, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_log_source_created "
            "ON request_log(source, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_log_project_created "
            "ON request_log(project_id, created_at DESC)"
        )
        conn.commit()
        conn.close()

    # ── Key CRUD ────────────────────────────────────────────────
    def _invalidate_caches(self) -> None:
        """写操作后统一失效重计算缓存（异常识别 / 用量趋势）。

        record_request 不在此列：它高频触发，靠 TTL 自然过期，避免每次请求清缓存。
        同时广播跨进程失效信号：其他进程（MCP/proxy 子进程）的 KeyPool 缓存
        检测到后同步失效，保证展示一致性。
        """
        self._anomalies_cache.clear()
        self._trend_cache.clear()
        emit_invalidate()
        # 本进程刚广播，直接记录最新 mtime，避免下次读取时误清一次
        self._sig_mtime = signal_mtime()

    def _check_remote_invalidate(self) -> None:
        """检测其他进程的失效信号：信号比本进程见过的更新则清空自身缓存。

        读取重计算缓存前调用（一次 stat，µs 级开销）。
        """
        m = signal_mtime()
        if m > self._sig_mtime:
            self._sig_mtime = m
            self._anomalies_cache.clear()
            self._trend_cache.clear()

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
        self._invalidate_caches()
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
        if added:
            self._invalidate_caches()
        return added

    def remove_key(self, masked_or_key: str):
        conn = self._get_conn()
        if "****" in masked_or_key:
            conn.execute("DELETE FROM api_keys WHERE masked = ?", (masked_or_key,))
        else:
            conn.execute("DELETE FROM api_keys WHERE key = ?", (masked_or_key,))
        conn.commit()
        self._invalidate_caches()

    def deactivate_key(self, masked: str, reason: str = ""):
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_active=0, last_error=? WHERE masked=?",
            (reason, masked),
        )
        conn.commit()
        self._invalidate_caches()

    def activate_key(self, masked: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_active=1, is_exhausted=0, error_count=0 WHERE masked=?",
            (masked,),
        )
        conn.commit()
        self._invalidate_caches()

    def mark_exhausted(self, masked: str, reason: str = ""):
        """标记 key 额度耗尽（432/433）：移出轮询，但保留可恢复（月度重置后自动恢复）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_exhausted=1, last_error=? WHERE masked=?",
            (reason[:500] or "exhausted", masked),
        )
        conn.commit()
        self._invalidate_caches()

    def mark_recovered(self, masked: str):
        """清除额度耗尽标记（新计费周期 usage 归零时调用）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE api_keys SET is_exhausted=0, error_count=0 WHERE masked=?",
            (masked,),
        )
        conn.commit()
        self._invalidate_caches()

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
            research_usage=row["research_usage"] or 0,
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

    def _endpoint_rpm(self, endpoint: str) -> int:
        """读取每 endpoint 的每 key 限流（config endpoint_rpm）；未配置的 endpoint 回退 rate_limit_rpm。

        官方限流按 key 独立（池内 key 均来自不同账号）：research「创建任务」独立
        20 RPM、crawl 独立 100 RPM、默认 dev 100 RPM。默认 90/18 等按官方上限留
        10% 余量，避免贴近上限被 429。
        """
        # 用热刷新读配置：MCP/代理子进程能感知面板改动的限流参数
        ep = get_settings_fresh().get("endpoint_rpm") or {}
        if isinstance(ep, dict) and endpoint in ep:
            try:
                return int(ep[endpoint])
            except (TypeError, ValueError):
                pass
        return int(get_settings_fresh().get("rate_limit_rpm") or DEFAULT_RPM)

    def _bucket(self, masked: str, endpoint: str) -> _TokenBucket:
        rpm = self._endpoint_rpm(endpoint)
        with self._bucket_lock:
            by_endpoint = self._buckets.get(masked)
            if by_endpoint is None:
                by_endpoint = {}
                self._buckets[masked] = by_endpoint
            b = by_endpoint.get(endpoint)
            # 配置改动了该 endpoint 的限流 → 重建桶让新速率生效（热刷新）
            if b is None or b.rate_per_min != rpm:
                b = _TokenBucket(rpm)
                by_endpoint[endpoint] = b
            return b

    def _consume_bucket(self, masked: str, endpoint: str) -> None:
        self._bucket(masked, endpoint).try_acquire(1)

    def next_key(self, endpoint: str = "search", skip_limited: bool = False) -> tuple[str, str] | None:
        """Round-robin 取下一个 active 且未耗尽的 key（按 endpoint 独立限流桶）。

        endpoint：'search'/'extract'/'crawl'/'map'/'research' 等，对应 endpoint_rpm
        配置；skip_limited=True 时跳过该 endpoint 令牌不足的 key。
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
            if not skip_limited or self._bucket(masked, endpoint).wait_time() <= 0:
                self._consume_bucket(masked, endpoint)
                return (self._decrypt_or_migrate(row["key"], masked), masked)
        return None

    def _remaining_credits(self, row) -> int:
        """单行剩余额度（effective_limit - credits_used，负值钳为 0）。"""
        limit = row["credits_limit"] if (row["credits_limit"] or 0) > 0 else (row["plan_limit"] or 0)
        return max(0, int(limit) - int(row["credits_used"]))

    def next_key_least_used(self, endpoint: str = "search", skip_limited: bool = False) -> tuple[str, str] | None:
        """按剩余额度优先取 key（least-used 语义：用量最少的 key 优先）。

        免费池场景：优先使用剩余额度多的 key，避免个别 key 提前耗尽触发
        432（官方额度耗尽）；剩余额度相同的 key 组内随机打散，防止固定顺序
        导致请求集中命中同一批 key（对标 TavilyProxyManager）。

        skip_limited=True 时跳过该 endpoint 令牌不足的 key。
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT k.key, k.masked, k.credits_used, k.credits_limit, k.plan_limit
            FROM api_keys k
            WHERE k.is_active = 1 AND k.is_exhausted = 0
            ORDER BY
                (COALESCE(NULLIF(k.credits_limit, 0), k.plan_limit) - k.credits_used) DESC,
                k.last_used_at ASC
            """
        ).fetchall()
        if not rows:
            return None
        # 剩余额度最高的 key 集合内随机打散（起点随机 + 顺序扫描）：同权 key
        # 不被固定顺序压制，请求更分散、更难集中触发单 key 限流。
        max_remaining = self._remaining_credits(rows[0])
        start = random.randrange(
            sum(1 for r in rows if self._remaining_credits(r) == max_remaining)
        )
        for off in range(len(rows)):
            row = rows[(start + off) % len(rows)]
            masked = row["masked"]
            if not skip_limited or self._bucket(masked, endpoint).wait_time() <= 0:
                self._consume_bucket(masked, endpoint)
                return (self._decrypt_or_migrate(row["key"], masked), masked)
        return None

    def next_available_key(self, endpoint: str = "search") -> tuple[str, str] | None:
        """按配置策略取一个当前可用（未超限流）的 key；全部受限时短暂等待最早的可用时机。

        endpoint 指定限流桶：search/extract/crawl/map/research 各自独立，互不影响。
        """
        strategy = (get_settings().get("key_strategy") or "round-robin").strip().lower()
        pick = self.next_key_least_used if strategy == "least-used" else self.next_key
        r = pick(endpoint, skip_limited=True)
        if r is not None:
            return r
        # 全部受限：等待最短等待时间的 key（上限 rate_limit_max_wait 秒）
        max_wait = float(get_settings().get("rate_limit_max_wait") or 1.0)
        deadline = time.time() + max_wait
        best: tuple[str, str] | None = None
        best_wait = float("inf")
        for row in self._active_rows():
            masked = row["masked"]
            w = self._bucket(masked, endpoint).wait_time()
            if w < best_wait:
                best_wait = w
                best = (self._decrypt_or_migrate(row["key"], masked), masked)
        if best is None:
            return None
        if best_wait > 0:
            wait = min(best_wait, max(0.0, deadline - time.time()))
            if wait > 0:
                time.sleep(wait)
        self._consume_bucket(best[1], endpoint)
        return best

    # ── Usage recording ────────────────────────────────────────
    def record_request(self, masked: str, endpoint: str, latency_ms: float, success: bool,
                       credits: int = 0, error_msg: str = "", request_id: str = "",
                       usage_source: str = "", is_client_error: int = 0,
                       source: str = "", project_id: str = ""):
        """记录一次请求。usage_source：response/unknown/none（见 request_log 表注释）。

        is_client_error：1 表示该失败由客户端请求错误（HTTP 400/参数校验）导致，
        与 Key/服务器健康无关，高错误率识别时忽略（不改变 error_count 累计）。
        source：请求来源标记（mcp / proxy / cli 等），便于日志筛选与统计。
        project_id：项目归属（MCP 请求落 mcp_project_id 配置值；代理请求为空），
        用于按项目拆分统计用量。
        """
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
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, is_client_error, source, project_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (masked, endpoint, credits, 1 if success else 0, error_msg[:500], latency_ms, request_id[:200], usage_source[:20], 1 if is_client_error else 0, source[:20], (project_id or "")[:50], now),
        )
        conn.commit()
        # 日志保留：周期性清理超期记录，避免 request_log 无界增长
        self._log_inserts += 1
        if self._log_inserts % _LOG_PRUNE_EVERY == 0:
            try:
                self.prune_request_log()
            except Exception:  # noqa: BLE001
                pass

    def prune_request_log(self, retention_days: int | None = None) -> int:
        """清理超期请求日志，返回删除行数。

        retention_days 缺省读配置 log_retention_days（默认 LOG_RETENTION_DAYS=90）；
        传 0 表示不清理。超期判定基于 created_at（unix 秒）。
        """
        days = retention_days
        if days is None:
            try:
                cfg_val = get_settings().get("log_retention_days")
                days = int(cfg_val) if cfg_val is not None else LOG_RETENTION_DAYS
            except (TypeError, ValueError):
                days = LOG_RETENTION_DAYS
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM request_log WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

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
        self._invalidate_caches()
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
                research_usage = int(key_usage.get("research_usage") or 0)
                conn = self._get_conn()
                conn.execute(
                    "UPDATE api_keys SET credits_used=?, credits_limit=?, plan=?, plan_usage=?, plan_limit=?, research_usage=?, usage_synced_at=? WHERE masked=?",
                    (usage, limit, plan, plan_usage, plan_limit, research_usage, now, k.masked),
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
        self._invalidate_caches()
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
        """本地可对账积分：仅统计接口明确返回 usage 的成功请求。

        Research API 响应不包含 usage（官方文档），本地永远记 0，若计入会
        让本地累计系统性偏低、误报 suspected_leak，因此排除 usage_source='unknown'。
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(credits_consumed),0) AS c FROM request_log "
            "WHERE key_masked=? AND success=1 AND usage_source != 'unknown'",
            (masked,),
        ).fetchone()
        return float(row["c"] or 0)

    def _recent_error_rate(self, masked: str) -> tuple[float, str]:
        """近 24h 错误率（仅服务器错误）与最主要的错误类别。

        客户端请求错误（HTTP 400/参数校验失败，is_client_error=1）与 Key/服务器
        健康无关，分子分母均排除，避免客户端参数错误虚增错误率导致误报。
        """
        conn = self._get_conn()
        since = time.time() - 86400
        rows = conn.execute(
            "SELECT success, is_client_error, COUNT(*) AS cnt FROM request_log "
            "WHERE key_masked=? AND created_at > ? GROUP BY success, is_client_error",
            (masked, since),
        ).fetchall()
        total = sum(r["cnt"] for r in rows)
        if total == 0:
            return 0.0, ""
        server_fails = sum(r["cnt"] for r in rows if not r["success"] and not r["is_client_error"])
        client_fails = sum(r["cnt"] for r in rows if not r["success"] and r["is_client_error"])
        server_reqs = total - client_fails  # 排除客户端错误请求后，真正发给服务器的请求数
        if server_reqs <= 0:
            return 0.0, ""
        err_rows = conn.execute(
            "SELECT error_msg FROM request_log WHERE key_masked=? AND created_at > ? "
            "AND success=0 AND is_client_error=0",
            (masked, since),
        ).fetchall()
        types: dict[str, int] = {}
        for r in err_rows:
            cat = _classify_error(r["error_msg"])
            types[cat] = types.get(cat, 0) + 1
        top = max(types, key=types.get) if types else "other"
        return (server_fails / server_reqs), top

    def _avg_latency(self, masked: str) -> float:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT AVG(latency_ms) AS avg FROM request_log WHERE key_masked=? AND success=1 AND created_at > ?",
            (masked, time.time() - 86400),
        ).fetchone()
        return float(row["avg"] or 0)

    def detect_anomalies(self) -> list[dict]:
        """识别异常 key（结果短 TTL 缓存）。

        规则（阈值见 config.anomaly_thresholds）：
        - exhausted        : is_exhausted（官方 usage >= limit，已触发 432）
        - near_exhausted   : 官方 usage_pct >= 90%
        - suspected_leak   : 官方 usage 明显大于本地累计 credits（差值 > leak_diff_credits）
        - high_error_rate  : 近 24h 错误率 > error_rate
        - stale            : active 但近 stale_days 天无本地调用
        - slow             : 近 24h 平均延迟 > 池内均值 * slow_ratio

        TTL：settings.cache_ttls.anomalies（默认 5s，0=关闭）。写操作后自动
        失效；需要强制刷新可传 skip_cache=True。
        """
        self._check_remote_invalidate()
        ttl = float(cache_ttls().get("anomalies", 5.0))
        if ttl > 0:
            hit = self._anomalies_cache.get("anomalies")
            if hit is not None:
                return hit
        result = self._detect_anomalies_impl()
        if ttl > 0:
            self._anomalies_cache.set("anomalies", result, ttl)
        return result

    def _detect_anomalies_impl(self) -> list[dict]:
        """异常识别实现（无缓存），见 detect_anomalies。"""
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
            # suspected_leak：官方总用量扣除 research（其消耗只存在于官方 /usage，
            # 本地 response 日志永远记 0）后再与本地可对账积分比较，否则正常使用
            # research 也会被误判为泄露。
            if k.credits_limit > 0 and (k.credits_used - k.research_usage - self._local_credits(k.masked)) >= leak_diff:
                flags.append("suspected_leak")
                reasons.append(
                    f"官方用量比本地记录多 ≥{leak_diff:.0f} 积分（已扣除官方 research {k.research_usage}），疑似被外部使用"
                )
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

    def get_usage_trend(self, days: int = 7, source: str = "", project: str = "") -> dict:
        """按天聚合 request_log 用量（结果短 TTL 缓存）。

        每日请求数/成功/失败/积分/按 endpoint 拆分。source 非空时只统计该来源
        （mcp/proxy/cli，来自 request_log.source）；project 非空时只统计该
        项目（MCP 请求的 mcp_project_id 归属）。日志是追加写、按天聚合
        结果秒级变化无意义，TTL：settings.cache_ttls.trend（默认 30s，0=关闭）。
        """
        days = max(1, min(int(days), 90))
        source = (source or "").strip()
        project = (project or "").strip()
        self._check_remote_invalidate()
        ttl = float(cache_ttls().get("trend", 30.0))
        key = ("trend", days, source, project)
        if ttl > 0:
            hit = self._trend_cache.get(key)
            if hit is not None:
                return hit
        result = self._get_usage_trend_impl(days, source, project)
        if ttl > 0:
            self._trend_cache.set(key, result, ttl)
        return result

    def _get_usage_trend_impl(self, days: int = 7, source: str = "", project: str = "") -> dict:
        """按天聚合实现（无缓存），见 get_usage_trend。"""
        days = max(1, min(int(days), 90))
        source = (source or "").strip()
        project = (project or "").strip()
        since = time.time() - days * 86400
        conn = self._get_conn()
        sql = """
            SELECT date(created_at, 'unixepoch', 'localtime') AS day,
                   endpoint,
                   COUNT(*) AS cnt,
                   COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END), 0) AS ok,
                   COALESCE(SUM(CASE WHEN success=1 THEN credits_consumed ELSE 0 END), 0) AS credits
            FROM request_log
            WHERE created_at >= ?
        """
        params: list = [since]
        if source:
            sql += " AND source = ?"
            params.append(source)
        if project:
            sql += " AND project_id = ?"
            params.append(project)
        sql += " GROUP BY day, endpoint ORDER BY day"
        rows = conn.execute(sql, params).fetchall()
        day_map: dict[str, dict] = {}
        for r in rows:
            d = day_map.setdefault(
                r["day"], {"requests": 0, "success": 0, "failed": 0, "credits": 0, "endpoints": {}}
            )
            d["requests"] += r["cnt"]
            d["success"] += r["ok"]
            d["failed"] += r["cnt"] - r["ok"]
            d["credits"] += r["credits"]
            d["endpoints"][r["endpoint"]] = d["endpoints"].get(r["endpoint"], 0) + r["cnt"]
        # 补齐无请求的日期（显示 0），保持时间顺序
        out = []
        for i in range(days - 1, -1, -1):
            day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
            d = day_map.get(day, {"requests": 0, "success": 0, "failed": 0, "credits": 0, "endpoints": {}})
            out.append({"date": day, **d})
        return {"days": days, "points": out}

    def list_projects(self) -> list[str]:
        """返回 request_log 中出现过的项目 ID（去重、非空、按名称排序），供面板筛选。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT project_id FROM request_log "
            "WHERE project_id != '' ORDER BY project_id"
        ).fetchall()
        return [r["project_id"] for r in rows]

    def exhaustion_eta(self, days: int = 7) -> list[dict]:
        """按近 N 天本地日均消耗估算每个 active key 的额度耗尽时间。

        基于 request_log 成功请求的 credits_consumed 聚合（research 等接口本地
        记 0、损耗以官方 /usage 为准，故 research 占比高的 key 估算偏保守）。
        返回 [{masked, remaining, daily_avg, eta_days}]：
        - remaining：剩余额度（effective_limit − credits_used，<=0 视为已耗尽）
        - daily_avg：近 N 天日均消耗（无成功请求记录为 0）
        - eta_days：预计耗尽天数（remaining / daily_avg；无消耗或未同步时为 None）
        """
        days = max(1, min(int(days), 90))
        since = time.time() - days * 86400
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT key_masked, SUM(credits_consumed) AS credits FROM request_log "
            "WHERE created_at >= ? AND success = 1 GROUP BY key_masked",
            (since,),
        ).fetchall()
        burn = {r["key_masked"]: float(r["credits"] or 0) / days for r in rows}
        out: list[dict] = []
        for k in self.list_keys():
            if not k.is_active:
                continue
            remaining = max(0, k.effective_limit - k.credits_used)
            daily = burn.get(k.masked, 0.0)
            eta = (remaining / daily) if (daily > 0 and remaining > 0) else None
            out.append({
                "masked": k.masked,
                "remaining": remaining,
                "daily_avg": round(daily, 2),
                "eta_days": round(eta, 1) if eta is not None else None,
            })
        return out

    def query_logs(self, endpoint: str = "", key_masked: str = "", status: str = "",
                   since: float = 0.0, until: float = 0.0, source: str = "",
                   project_id: str = "", limit: int = 200, offset: int = 0) -> tuple[list[dict], int]:
        """按条件筛选请求日志（倒序），返回 (行, 总数)。status: '' | success | failed。"""
        where: list[str] = []
        params: list = []
        if endpoint:
            where.append("endpoint = ?")
            params.append(endpoint)
        if key_masked:
            where.append("key_masked = ?")
            params.append(key_masked)
        if source:
            where.append("source = ?")
            params.append(source)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if status == "success":
            where.append("success = 1")
        elif status == "failed":
            where.append("success = 0")
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if until:
            where.append("created_at <= ?")
            params.append(until)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) AS c FROM request_log{cond}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM request_log{cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()
        return [dict(r) for r in rows], int(total)

    def clear_logs(self, endpoint: str = "", key_masked: str = "", status: str = "",
                   source: str = "", project_id: str = "", before: float = 0.0,
                   after: float = 0.0) -> int:
        """按条件清理请求日志，返回删除条数（保留策略之外的手动清理入口）。

        筛选条件与 query_logs 一致（空条件 = 清空全部）；before/after 为
        unix 时间戳。清理后失效用量趋势缓存（面板统计页数据不再含被删记录）。
        """
        where: list[str] = []
        params: list = []
        if endpoint:
            where.append("endpoint = ?")
            params.append(endpoint)
        if key_masked:
            where.append("key_masked = ?")
            params.append(key_masked)
        if source:
            where.append("source = ?")
            params.append(source)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if status == "success":
            where.append("success = 1")
        elif status == "failed":
            where.append("success = 0")
        if before:
            where.append("created_at <= ?")
            params.append(before)
        if after:
            where.append("created_at >= ?")
            params.append(after)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_conn()
        cur = conn.execute(f"DELETE FROM request_log{cond}", params)
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount

    def project_stats(self, days: int = 1) -> dict[str, int]:
        """近 N 天 request_log 按项目统计请求数（供 CLI audit，避免拉全量日志）。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT project_id, COUNT(*) AS cnt FROM request_log "
            "WHERE project_id != '' AND created_at > ? GROUP BY project_id ORDER BY cnt DESC",
            (time.time() - max(1, int(days)) * 86400,),
        ).fetchall()
        return {r["project_id"]: r["cnt"] for r in rows}


def _mask(key: str) -> str:
    if key.startswith("tvly-"):
        return key[:12] + "****" + key[-4:]
    return key[:4] + "****" + key[-4:] if len(key) > 8 else "****"


def _classify_error(err: str) -> str:
    """把错误分类：auth(认证)/quota(额度耗尽)/rate(限流)/bad_request(客户端参数错误)/other(临时错误)。"""
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
    # 客户端请求错误（HTTP 400/参数校验）：与 Key 健康无关，高错误率识别忽略
    if any(s in e for s in (
        "400", "bad request", "request validation failed",
        "missing required parameter", "invalid parameter",
        "research_stream_required", "unsupported parameter",
    )):
        return "bad_request"
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
        "research_usage": k.research_usage,
        "usage_synced_at": k.usage_synced_at,
    }

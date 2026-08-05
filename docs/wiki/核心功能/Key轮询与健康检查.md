# Key 轮询与健康检查

> <cite>本文档基于 [`key_pool.py`](file://app/key_pool.py) 源码撰写。文中所引用的类、方法与代码片段均出自该文件。</cite>

## 目录

- [概述](#概述)
- [核心数据结构](#核心数据结构)
  - [ApiKey 数据类](#apikey-数据类)
  - [数据库表结构](#数据库表结构)
- [轮询算法](#轮询算法)
  - [最近未用优先的轮询调度](#最近未用优先的轮询调度)
  - [最少使用优先](#最少使用优先)
- [并发控制](#并发控制)
- [健康检查](#健康检查)
  - [检查流程](#检查流程)
  - [自动停用与恢复](#自动停用与恢复)
- [请求记录与失败处理](#请求记录与失败处理)
- [Key 生命周期状态图](#key-生命周期状态图)
- [完整调用示例](#完整调用示例)
- [相关命令入口](#相关命令入口)

## 概述

当系统持有多个 Tavily API Key 时，如果所有请求都固定命中同一个 Key，很容易触发配额上限或速率限制。`KeyPool` 正是为解决这一问题而设计：它以 SQLite 为存储后端，持久化每个 Key 的元数据、使用量、额度消耗与错误状态，并通过**轮询算法**在活跃 Key 之间均匀分发请求。与此同时，`KeyPool` 内置**健康检查**机制，用真实但轻量的探测请求识别失效 Key，并自动将其停用，避免后续请求继续打到坏 Key 上。

`KeyPool` 的核心职责：

| 职责 | 对应方法 |
| --- | --- |
| Key 注册与删除 | `add_key` / `add_keys_batch` / `remove_key` |
| 可用 Key 选择 | `next_key` / `next_key_least_used` |
| 请求结果回写 | `record_request` |
| 健康探测与自动停用 | `check_health` / `check_health_all` |
| 手动恢复 | `activate_key` |
| 统计与日志 | `get_stats` / `get_recent_logs` |

## 核心数据结构

### ApiKey 数据类

`ApiKey` 是单个 Key 在内存中的映射对象，每个字段对应数据库 `api_keys` 表中的一列：

```python
@dataclass
class ApiKey:
    key: str           # 原始 Key（仅内存中持有）
    masked: str        # 脱敏后的展示值，如 tvly-xxxxxxxx****abcd
    is_active: bool    # 是否可用
    request_count: int # 累计请求次数
    error_count: int   # 累计错误次数
    credits_used: int  # 已消耗额度
    credits_limit: int # 额度上限
    last_used_at: float  # 最近使用时间戳
    added_at: float      # 添加时间戳
    last_error: str      # 最近一次错误信息

    @property
    def usage_pct(self) -> float:
        """额度使用百分比，credits_limit <= 0 时返回 0"""
        if self.credits_limit <= 0:
            return 0.0
        return (self.credits_used / self.credits_limit) * 100
```

其中 `masked` 由 `_mask()` 函数生成：`tvly-` 开头的 Key 保留前 12 位与后 4 位，其余 Key 保留前 4 位与后 4 位，中间以 `****` 代替。脱敏值用于日志、界面展示与作为业务层引用的稳定标识。

### 数据库表结构

`KeyPool` 使用 SQLite 存储，默认数据库文件为 `key_pool.py` 同目录下的 `tavily_keys.db`，并开启 WAL 模式以提升并发读写性能。两张核心表：

- `api_keys`：Key 主表，`key` 为唯一主键，`is_active` 以 0/1 表示停用/启用。
- `request_log`：请求日志表，记录每次请求的端点、成功与否、延迟与消耗额度，供统计与审计使用。

```sql
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
);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_masked TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    credits_consumed INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1,
    error_msg TEXT DEFAULT '',
    latency_ms REAL DEFAULT 0,
    created_at REAL NOT NULL
);
```

## 轮询算法

`KeyPool` 提供两种 Key 选择策略。两者都只查询 `is_active=1` 的 Key，保证已停用的 Key 永远不会被选中；当没有活跃 Key 时均返回 `None`，调用方需要自行处理该分支（例如抛出明确的业务异常或进入重试等待）。

### 最近未用优先的轮询调度

`next_key()` 是默认的轮询入口。它先按 `last_used_at ASC` 排序，使最久未使用的 Key 排在最前，再通过内部游标 `_next_index` 做轮询，兼顾"均匀分发"与"冷却优先"：

```python
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
    return (row["key"], row["masked"])
```

```mermaid
flowchart TD
    A[调用 next_key] --> B{查询 active=1 的 Key}
    B -- 无结果 --> C[返回 None 表示无可用 Key]
    B -- 有结果 --> D[按 last_used_at 升序排序]
    D --> E[加锁计算本轮索引 idx]
    E --> F[推进 _next_index 游标]
    F --> G[返回 raw_key 与 masked 元组]
```

### 最少使用优先

`next_key_least_used()` 按 `request_count ASC, last_used_at ASC` 排序，总是返回累计请求次数最少的 Key，适合更严格的"按量分摊"场景：

```python
def next_key_least_used(self) -> tuple[str, str] | None:
    """Return key with fewest requests today."""
    conn = self._get_conn()
    rows = conn.execute(
        "SELECT key, masked FROM api_keys WHERE is_active=1 ORDER BY request_count ASC, last_used_at ASC"
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return (row["key"], row["masked"])
```

## 并发控制

`KeyPool` 是进程级单例（`__new__` 中使用双重检查锁），并为多线程并发调用做了以下设计：

- **线程隔离连接**：通过 `threading.local()` 为每个线程维护独立的 SQLite 连接，避免连接对象跨线程共享；
- **轮询游标锁**：`_index_lock` 保护 `_next_index` 的读取与自增，防止多线程下重复选中同一个 Key；
- **写并发兜底**：每个连接设置 `PRAGMA busy_timeout=3000`，写冲突时等待而不是立刻抛错；
- **WAL 模式**：读写互不阻塞，适合"一写多读"的 Key 分发场景。

```python
self._local = threading.local()
self._next_index = 0
self._index_lock = threading.Lock()

def _get_conn(self) -> sqlite3.Connection:
    if not hasattr(self._local, "conn") or self._local.conn is None:
        self._local.conn = sqlite3.connect(self._db)
        self._local.conn.row_factory = sqlite3.Row
        self._local.conn.execute("PRAGMA journal_mode=WAL")
        self._local.conn.execute("PRAGMA busy_timeout=3000")
    return self._local.conn
```

## 健康检查

### 检查流程

`check_health()` 会对指定 Key（或全部 Key）发起一次真实但轻量的搜索请求来探活。它使用 `TavilyClient.search("test", max_results=1, search_depth="basic", timeout=5)` 构造探测请求，并依据响应中是否存在非空 `results` 判断 Key 是否存活：

```python
def check_health(self, key_masked: str | None = None) -> list[dict]:
    """Probe one or all active keys with a lightweight search. Auto-deactivate dead keys."""
    results = []
    keys = [self.get_key(key_masked)] if key_masked else self.list_keys()
    for k in keys:
        if not k or not k.is_active:
            continue
        try:
            from tavily import TavilyClient
            t0 = time.time()
            client = TavilyClient(k.key)
            resp = client.search("test", max_results=1, search_depth="basic", timeout=5)
            elapsed = (time.time() - t0) * 1000
            ok = "results" in resp and len(resp.get("results", [])) > 0
            if ok:
                results.append({"masked": k.masked, "alive": True, "latency_ms": round(elapsed)})
            else:
                results.append({"masked": k.masked, "alive": False, "error": "empty response"})
                self.deactivate_key(k.masked, "health-check: empty response")
        except Exception as e:
            err = str(e)
            results.append({"masked": k.masked, "alive": False, "error": err})
            self.deactivate_key(k.masked, f"health-check: {err[:200]}")
    return results
```

`check_health_all()` 是 `check_health()` 的无参封装，用于一键探测全部 Key。返回列表中的每一项包含 `masked`、`alive`、`latency_ms` 或 `error` 字段，调用方可以据此生成告警或展示面板。

```mermaid
sequenceDiagram
    participant S as 调用方/定时任务
    participant P as KeyPool
    participant DB as SQLite
    participant T as Tavily API

    S->>P: check_health_all()
    loop 遍历每个活跃 Key
        P->>T: client.search("test", max_results=1)
        alt 请求成功且返回非空 results
            T-->>P: 响应包含 results
            P->>P: 记录 alive=true + 延迟
        else 请求异常或返回空响应
            T-->>P: 异常 / 空 results
            P->>DB: UPDATE api_keys SET is_active=0
            P->>P: 记录 alive=false + 错误信息
        end
    end
    P-->>S: 返回健康检查结果列表
```

### 自动停用与恢复

健康检查发现异常后会自动停用 Key，停用原因写入 `last_error` 字段，便于事后排查：

```python
def deactivate_key(self, masked: str, reason: str = ""):
    conn = self._get_conn()
    conn.execute(
        "UPDATE api_keys SET is_active=0, last_error=? WHERE masked=?",
        (reason, masked),
    )
    conn.commit()
```

被停用的 Key 不会再出现在 `next_key()` / `next_key_least_used()` 的候选集中。手动恢复调用 `activate_key()`，同时将 `error_count` 清零，重新参与轮询：

```python
def activate_key(self, masked: str):
    conn = self._get_conn()
    conn.execute("UPDATE api_keys SET is_active=1, error_count=0 WHERE masked=?", (masked,))
    conn.commit()
```

## 请求记录与失败处理

每次外部请求完成后，调用方应通过 `record_request()` 回写结果。它同时更新 Key 的使用统计，并追加一条请求日志：

```python
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
```

失败处理要点：

- **成功**：`credits_used` 累加实际消耗额度，用于后续的额度占比统计；
- **失败**：`error_count + 1`，并把截断到 500 字符的错误信息写入 `last_error`；
- 无论成功与否，都会写入一条 `request_log` 记录，供 `get_stats()` 按端点聚合最近 24 小时的成功/失败分布。

```mermaid
flowchart TD
    A[record_request] --> B[request_count+1 并更新 last_used_at]
    B --> C{请求成功?}
    C -- 是 --> D[credits_used 累加消耗额度]
    C -- 否 --> E[error_count+1 并记录 last_error]
    D --> F[写入 request_log]
    E --> F
```

需要特别说明：`record_request` 本身**不会**自动停用 Key，它只负责累计错误与记录日志；真正的自动停用逻辑发生在 `check_health` 中。如果希望实现"连续 N 次失败即自动禁用"，可以在业务层基于 `get_key().error_count` 判断后主动调用 `deactivate_key`。

## Key 生命周期状态图

```mermaid
stateDiagram-v2
    [*] --> 可用: add_key / add_keys_batch
    可用 --> 停用: deactivate_key（健康检查失败）
    可用 --> 停用: 业务层依据 error_count 调用 deactivate_key
    停用 --> 可用: activate_key（手动恢复，清零 error_count）
    可用 --> [*]: remove_key
    停用 --> [*]: remove_key
```

## 完整调用示例

下面是一个典型的请求处理流程：轮询取 Key → 调用 Tavily → 回写记录 → 定期健康检查。

```python
from key_pool import KeyPool

pool = KeyPool()

# 1. 初始化：批量注册 Key
pool.add_keys_batch([
    "tvly-xxxxxxxxxxxxxxxxxxxxkey1",
    "tvly-yyyyyyyyyyyyyyyyyyyykey2",
    "tvly-zzzzzzzzzzzzzzzzzzzzkey3",
])

# 2. 每次请求：取 Key、执行搜索、回写结果
selected = pool.next_key()
if selected is None:
    raise RuntimeError("没有可用的活跃 Key")
raw_key, masked = selected
print(f"本次使用 Key: {masked}")

try:
    resp = tavily_client.search("量子计算", max_results=5)
    pool.record_request(masked, "/search", latency_ms=320.5,
                        success=True, credits=1)
except Exception as e:
    pool.record_request(masked, "/search", latency_ms=0,
                        success=False, error_msg=str(e))

# 3. 定时任务：健康检查并自动停用失效 Key
health_results = pool.check_health_all()
for r in health_results:
    if not r["alive"]:
        print(f"Key {r['masked']} 已停用: {r['error']}")
    else:
        print(f"Key {r['masked']} 存活，延迟 {r['latency_ms']}ms")

# 4. 查看整体统计
stats = pool.get_stats()
print(f"活跃 Key: {stats['active_keys']}/{stats['total_keys']}")
print(f"最近 24h 请求分布: {stats['recent_24h']}")
```

## 相关命令入口

本文所述能力在项目其他模块中的使用方式：

- **CLI 命令**：Key 管理命令（添加、删除、激活、停用）与统计健康命令（健康检查、统计概览）直接封装了 `KeyPool` 的上述方法；
- **Web Dashboard**：界面与 API 层通过 `get_stats()` / `check_health()` 展示 Key 状态与健康结果；
- **MCP 工具集**：工具调用层使用 `next_key()` 完成请求分发，并用 `record_request()` 回写每次调用的成败与耗时。

> <cite>更多实现细节请直接阅读源码：[`key_pool.py`](file://app/key_pool.py)</cite>
"""通用 TTL 缓存基础设施单元测试（cache.py）。"""
import time

from cache import TTLCache, emit_invalidate, set_signal_file, signal_mtime, ttl_cached


def test_get_set_roundtrip():
    c = TTLCache(default_ttl=10)
    c.set("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None
    assert c.get("missing", "d") == "d"


def test_ttl_expiry():
    c = TTLCache(default_ttl=0.05)
    c.set("a", 1)
    assert c.get("a") == 1
    time.sleep(0.08)
    assert c.get("a") is None


def test_per_item_ttl_overrides_default():
    c = TTLCache(default_ttl=10)
    c.set("short", 1, ttl=0.05)
    c.set("long", 2, ttl=10)
    time.sleep(0.08)
    assert c.get("short") is None
    assert c.get("long") == 2


def test_delete_and_clear():
    c = TTLCache(default_ttl=10)
    c.set("a", 1)
    c.set("b", 2)
    c.delete("a")
    assert c.get("a") is None and c.get("b") == 2
    c.clear()
    assert c.get("b") is None
    assert len(c) == 0


def test_invalidate_prefix():
    c = TTLCache(default_ttl=10)
    c.set("mcp:status", 1)
    c.set("proxy:status", 2)
    c.set("logs:1", 3)
    assert c.invalidate("mcp:") == 1
    assert c.get("mcp:status") is None
    assert c.get("proxy:status") == 2
    assert c.get("logs:1") == 3
    # 前缀失效不影响其他
    c.set("mcp:status", 9)
    assert c.invalidate() == 3  # 空前缀 = 全清
    assert c.get("mcp:status") is None and c.get("proxy:status") is None


def test_maxsize_evicts_stale_first():
    c = TTLCache(default_ttl=10, maxsize=3)
    for i in range(5):
        c.set(f"k{i}", i)
    assert len(c) <= 3
    # 新写入的键应可读取
    assert c.get("k4") == 4


def test_thread_safety_smoke():
    import threading

    c = TTLCache(default_ttl=10)
    errors = []

    def worker(n):
        try:
            for i in range(100):
                c.set(f"{n}-{i}", i)
                c.get(f"{n}-{i}")
                if i % 10 == 0:
                    c.invalidate(f"{n}-")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_ttl_cached_decorator():
    calls = {"n": 0}

    @ttl_cached(ttl=10)
    def heavy(x, y=1):
        calls["n"] += 1
        return x + y

    assert heavy(1, y=2) == 3
    assert heavy(1, y=2) == 3
    assert calls["n"] == 1          # TTL 内命中
    assert heavy(2, y=2) == 4       # 不同参数 → 不同缓存键
    assert calls["n"] == 2
    heavy.cache.clear()             # 手动失效
    assert heavy(1, y=2) == 3
    assert calls["n"] == 3


def test_ttl_cached_skip_cache():
    calls = {"n": 0}

    @ttl_cached(ttl=10)
    def heavy():
        calls["n"] += 1
        return calls["n"]

    assert heavy() == 1
    assert heavy() == 1            # 命中缓存
    assert heavy(skip_cache=True) == 2  # 强制刷新，且 skip_cache 不泄漏给函数
    assert calls["n"] == 2


# ── 跨进程失效信号（文件 mtime）─────────────────────────────
def test_signal_mtime_absent_returns_zero(tmp_path):
    set_signal_file(tmp_path / "sig")
    try:
        assert signal_mtime() == 0.0        # 文件不存在 → 0
    finally:
        set_signal_file(None)


def test_emit_invalidate_updates_signal(tmp_path):
    sig = tmp_path / "sig"
    set_signal_file(sig)
    try:
        emit_invalidate()
        assert sig.exists()
        m1 = signal_mtime()
        assert m1 > 0
        time.sleep(0.02)                    # 保证 mtime 粒度差异
        emit_invalidate()
        m2 = signal_mtime()
        assert m2 > m1                      # 广播后 mtime 更新
    finally:
        set_signal_file(None)


def test_emit_invalidate_atomic_no_half_file(tmp_path):
    """先写临时文件再 rename：落盘后不应残留 .tmp。"""
    sig = tmp_path / "sig"
    set_signal_file(sig)
    try:
        emit_invalidate()
        assert not list(tmp_path.glob("*.tmp"))
        assert sig.read_text(encoding="utf-8").strip()  # 内容为时间戳
    finally:
        set_signal_file(None)

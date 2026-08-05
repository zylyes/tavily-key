"""KeyPool 单元测试：CRUD、负载均衡、健康检查防误伤、官方用量同步。"""
import builtins
import pytest

import key_pool
from key_pool import KeyPool


@pytest.fixture()
def pool(tmp_path):
    """隔离的 KeyPool 实例（每次重置单例，使用临时 DB）。"""
    KeyPool._instance = None
    p = KeyPool(str(tmp_path / "test.db"))
    yield p
    KeyPool._instance = None


KEY1 = "tvly-aaa111222333"
KEY2 = "tvly-bbb222333444"
KEY3 = "tvly-ccc333444555"
MASK1 = "tvly-aaa1112****2333"
MASK2 = "tvly-bbb2223****3444"


# ── CRUD ───────────────────────────────────────────────────────
def test_add_and_get_roundtrip(pool):
    k = pool.add_key(KEY1)
    assert k.masked == MASK1
    got = pool.get_key(MASK1)
    assert got is not None and got.key == KEY1  # 解密后与原值一致


def test_add_duplicate_raises(pool):
    pool.add_key(KEY1)
    with pytest.raises(ValueError):
        pool.add_key(KEY1)


def test_add_keys_batch_skips_duplicates(pool):
    n = pool.add_keys_batch([KEY1, KEY1, KEY2, ""])
    assert n == 2


def test_remove_by_masked(pool):
    pool.add_key(KEY1)
    pool.remove_key(MASK1)
    assert pool.get_key(MASK1) is None


def test_deactivate_activate(pool):
    pool.add_key(KEY1)
    pool.deactivate_key(MASK1, "test")
    assert not pool.get_key(MASK1).is_active
    pool.activate_key(MASK1)
    assert pool.get_key(MASK1).is_active


def test_stored_key_is_encrypted_or_migrated(pool):
    """写入的 key 不应以明文 `tvly-` 开头（加密后应带前缀；无后端时保持原样则跳过）。"""
    pool.add_key(KEY1)
    import security
    conn = pool._get_conn()
    row = conn.execute("SELECT key FROM api_keys WHERE masked=?", (MASK1,)).fetchone()
    stored = row["key"]
    if security.available():
        assert not stored.startswith("tvly-")
    # 读取仍能得到原明文
    assert pool.get_key(MASK1).key == KEY1


# ── 负载均衡 ───────────────────────────────────────────────────
def test_round_robin_rotates_through_all(pool):
    pool.add_keys_batch([KEY1, KEY2, KEY3])
    seen = {pool.next_key()[1] for _ in range(3)}
    assert len(seen) == 3


def test_next_key_none_when_empty(pool):
    assert pool.next_key() is None
    assert pool.next_key_least_used() is None


def test_least_used_prefers_low_today_count(pool):
    pool.add_keys_batch([KEY1, KEY2])
    # 给 KEY1 记 5 次今日请求
    conn = pool._get_conn()
    now = key_pool.time.time()
    for _ in range(5):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, created_at) VALUES (?,?,?,?,?)",
            (MASK1, "search", 1, 1, now),
        )
    conn.commit()
    _, masked = pool.next_key_least_used()
    assert masked == MASK2


def test_deactivated_keys_excluded(pool):
    pool.add_keys_batch([KEY1, KEY2])
    pool.deactivate_key(MASK1, "test")
    seen = {pool.next_key()[1] for _ in range(3)}
    assert seen == {MASK2}


# ── 健康检查防误伤 ─────────────────────────────────────────────
class _FakeAuthErrorClient:
    def search(self, *a, **k):
        raise Exception("401 Unauthorized: invalid api key")


class _FakeTimeoutClient:
    def search(self, *a, **k):
        raise TimeoutError("request timed out")


class _FakeOkClient:
    def search(self, *a, **k):
        return {"results": [{"title": "t", "url": "u", "content": "c"}]}


def test_health_auth_error_deactivates_immediately(pool, monkeypatch):
    pool.add_key(KEY1)
    monkeypatch.setattr("tavily.TavilyClient", lambda key: _FakeAuthErrorClient())
    results = pool.check_health()
    assert results[0]["error_category"] == "auth"
    assert not pool.get_key(MASK1).is_active


def test_health_temporary_error_needs_two_failures(pool, monkeypatch):
    pool.add_key(KEY1)
    monkeypatch.setattr("tavily.TavilyClient", lambda key: _FakeTimeoutClient())
    # 第一次：临时错误，不停用，error_count=1
    r1 = pool.check_health()
    assert r1[0]["error_category"] == "other"
    assert pool.get_key(MASK1).is_active
    assert pool.get_key(MASK1).error_count == 1
    # 第二次：仍失败，达到阈值停用
    pool.check_health()
    assert not pool.get_key(MASK1).is_active


def test_health_ok_keeps_active(pool, monkeypatch):
    pool.add_key(KEY1)
    monkeypatch.setattr("tavily.TavilyClient", lambda key: _FakeOkClient())
    results = pool.check_health()
    assert results[0]["alive"] is True
    assert pool.get_key(MASK1).is_active


def test_health_tavily_missing_never_deactivates(pool, monkeypatch):
    """tavily 库缺失时不得误停用任何 key（历史踩坑回归测试）。"""
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "tavily":
            raise ImportError("No module named 'tavily'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    pool.add_key(KEY1)
    results = pool.check_health()
    assert results and results[0]["error_category"] == "fatal"
    assert pool.get_key(MASK1).is_active


def test_health_inactive_skipped(pool, monkeypatch):
    pool.add_key(KEY1)
    pool.deactivate_key(MASK1, "manual")
    monkeypatch.setattr("tavily.TavilyClient", lambda key: _FakeOkClient())
    assert pool.check_health() == []  # 无 active key 直接跳过


# ── 官方用量同步 ───────────────────────────────────────────────
class _FakeUsageResp:
    status_code = 200

    def json(self):
        return {
            "key": {"usage": 42, "limit": 1000, "search_usage": 42, "crawl_usage": 0,
                    "extract_usage": 0, "map_usage": 0, "research_usage": 0},
            "account": {"current_plan": "Researcher", "plan_usage": 42, "plan_limit": 1000},
        }


def test_sync_usage_updates_credits(pool, monkeypatch):
    pool.add_key(KEY1)
    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _FakeUsageResp())
    results = pool.sync_usage()
    assert results[0]["ok"] is True
    assert results[0]["usage"] == 42
    k = pool.get_key(MASK1)
    assert k.credits_used == 42
    assert k.credits_limit == 1000


def test_sync_usage_http_error_reported(pool, monkeypatch):
    pool.add_key(KEY1)

    class _ErrResp:
        status_code = 401

        def json(self):
            return {}

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _ErrResp())
    results = pool.sync_usage()
    assert results[0]["ok"] is False
    assert "401" in results[0]["error"]

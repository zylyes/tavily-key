"""KeyPool 单元测试：CRUD、负载均衡、健康检查防误伤、官方用量同步。"""
import builtins
import time

import key_pool
import pytest
from key_pool import KeyPool


@pytest.fixture()
def pool(tmp_path):
    """隔离的 KeyPool 实例（每次重置单例，使用临时 DB）。"""
    KeyPool._instance = None
    p = KeyPool(str(tmp_path / "test.db"))
    yield p
    p.close_all_connections()  # 释放 sqlite 连接，避免 ResourceWarning
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


def test_least_used_prefers_higher_remaining_credit(pool):
    """least-used 优先剩余额度多的 key（免费池避免个别 key 提前耗尽 432）。"""
    pool.add_keys_batch([KEY1, KEY2])
    conn = pool._get_conn()
    conn.execute(
        "UPDATE api_keys SET credits_used=900, credits_limit=1000 WHERE masked=?",
        (MASK1,),
    )
    conn.execute(
        "UPDATE api_keys SET credits_used=100, credits_limit=1000 WHERE masked=?",
        (MASK2,),
    )
    conn.commit()
    # 剩余额度：KEY1=100、KEY2=900 → 应优先 KEY2
    _, masked = pool.next_key_least_used()
    assert masked == MASK2


def test_least_used_same_remaining_is_randomized(pool):
    """剩余额度相同的 key 组内随机打散：多次调用两者都应被选到。"""
    pool.add_keys_batch([KEY1, KEY2])
    conn = pool._get_conn()
    conn.execute(
        "UPDATE api_keys SET credits_used=0, credits_limit=1000 WHERE masked IN (?,?)",
        (MASK1, MASK2),
    )
    conn.commit()
    seen = set()
    for _ in range(100):
        _, masked = pool.next_key_least_used()
        seen.add(masked)
        if len(seen) == 2:
            break
    assert seen == {MASK1, MASK2}


def test_deactivated_keys_excluded(pool):
    pool.add_keys_batch([KEY1, KEY2])
    pool.deactivate_key(MASK1, "test")
    seen = {pool.next_key()[1] for _ in range(3)}
    assert seen == {MASK2}


# ── 日志清理 / 项目统计 ─────────────────────────────────────────
def test_clear_logs_by_condition(pool):
    pool.add_key(KEY1)
    now = key_pool.time.time()
    conn = pool._get_conn()
    for ep in ("search", "search", "extract"):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, created_at) VALUES (?,?,?,?,?)",
            (MASK1, ep, 1, 1, now),
        )
    conn.commit()
    # 按 endpoint 清理：只删 search
    n = pool.clear_logs(endpoint="search")
    assert n == 2
    rows, total = pool.query_logs(limit=100)
    assert total == 1 and rows[0]["endpoint"] == "extract"
    # 清空全部
    n2 = pool.clear_logs()
    assert n2 == 1
    _, total2 = pool.query_logs(limit=100)
    assert total2 == 0


def test_project_stats_aggregates(pool):
    pool.add_key(KEY1)
    now = key_pool.time.time()
    conn = pool._get_conn()
    for pid in ("p-a", "p-a", "p-b"):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, project_id, created_at) VALUES (?,?,?,?,?,?)",
            (MASK1, "search", 1, 1, pid, now),
        )
    conn.commit()
    stats = pool.project_stats(1)
    assert stats == {"p-a": 2, "p-b": 1}


def test_exhaustion_eta(pool):
    """按近 N 天日均消耗估算额度耗尽天数。"""
    pool.add_key(KEY1)
    conn = pool._get_conn()
    now = key_pool.time.time()
    # 近 7 天每天 10 积分 → 日均 10；剩余额度 500 → 预计 50 天
    for i in range(7):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, created_at) VALUES (?,?,?,?,?)",
            (MASK1, "search", 10, 1, now - i * 86400),
        )
    conn.execute(
        "UPDATE api_keys SET credits_used=500, credits_limit=1000 WHERE masked=?",
        (MASK1,),
    )
    conn.commit()
    eta = pool.exhaustion_eta(days=7)
    assert len(eta) == 1
    assert eta[0]["masked"] == MASK1
    assert eta[0]["remaining"] == 500
    assert eta[0]["daily_avg"] == pytest.approx(10.0)
    assert eta[0]["eta_days"] == pytest.approx(50.0)


def test_exhaustion_eta_no_usage(pool):
    """无消耗记录时 eta_days 为 None（无法估算）。"""
    pool.add_key(KEY1)
    conn = pool._get_conn()
    conn.execute(
        "UPDATE api_keys SET credits_used=100, credits_limit=1000 WHERE masked=?",
        (MASK1,),
    )
    conn.commit()
    eta = pool.exhaustion_eta(days=7)
    assert eta and eta[0]["eta_days"] is None
    assert eta[0]["remaining"] == 900


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


def test_sync_usage_unlimited_key_falls_back_to_plan_limit(pool, monkeypatch):
    """key.limit 为 null（无限额）时，回退账户套餐额度作为 credits_limit。"""
    pool.add_key(KEY1)

    class _UnlimitedResp:
        status_code = 200

        def json(self):
            return {
                "key": {"usage": 30, "limit": None, "search_usage": 30, "crawl_usage": 0,
                        "extract_usage": 0, "map_usage": 0, "research_usage": 0},
                "account": {"current_plan": "Bootstrap", "plan_usage": 30, "plan_limit": 1000},
            }

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _UnlimitedResp())
    results = pool.sync_usage()
    assert results[0]["ok"] is True
    k = pool.get_key(MASK1)
    assert k.credits_limit == 1000  # 回退到 plan_limit
    assert k.plan_limit == 1000
    assert round(k.usage_pct, 1) == 3.0  # 30 / 1000


def test_usage_pct_falls_back_to_plan_limit(pool):
    """未同步（credits_limit=0）但已有 plan_limit 时，usage_pct 回退套餐额度计算。"""
    pool.add_key(KEY1)
    conn = pool._get_conn()
    conn.execute(
        "UPDATE api_keys SET credits_used=150, plan='Bootstrap', plan_usage=150, plan_limit=1000 WHERE masked=?",
        (MASK1,),
    )
    conn.commit()
    k = pool.get_key(MASK1)
    assert k.credits_limit == 0
    assert round(k.usage_pct, 1) == 15.0  # 150 / 1000（回退 plan_limit）
    agg = pool.get_aggregate()
    assert agg["total_limit"] == 1000
    assert agg["remaining"] == 850


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


def test_sync_usage_masked_filters_and_only_updates_selected(pool, monkeypatch):
    """指定 masked 列表时只更新选中（且 active）的 Key。"""
    pool.add_keys_batch([KEY1, KEY2])
    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _FakeUsageResp())
    results = pool.sync_usage(masked=[MASK1])
    assert len(results) == 1
    assert results[0]["masked"] == MASK1
    assert results[0]["ok"] is True
    # 只更新了 KEY1，KEY2 保持原样
    assert pool.get_key(MASK1).credits_used == 42
    assert pool.get_key(MASK2).credits_used == 0


def test_sync_usage_masked_inactive_skipped(pool, monkeypatch):
    pool.add_key(KEY1)
    pool.deactivate_key(MASK1, "manual")
    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _FakeUsageResp())
    assert pool.sync_usage(masked=[MASK1]) == []  # inactive 不发起请求
    assert pool.sync_usage_one(MASK1) is None


def test_sync_usage_one_returns_result(pool, monkeypatch):
    pool.add_key(KEY1)
    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _FakeUsageResp())
    r = pool.sync_usage_one(MASK1)
    assert r is not None and r["ok"] is True and r["usage"] == 42


# ── 额度耗尽（is_exhausted）与错误分类 ────────────────────────
def test_classify_error_categories():
    from key_pool import _classify_error
    assert _classify_error("401 Unauthorized: invalid api key") == "auth"
    assert _classify_error("403 Forbidden") == "auth"
    assert _classify_error("432 This request exceeds your plan limit") == "quota"
    assert _classify_error("433 paygo limit exceeded") == "quota"
    assert _classify_error("429 too many requests") == "rate"
    assert _classify_error("request timed out") == "other"


def test_record_quota_marks_exhausted(pool):
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, False, 0, "432: plan limit exceeded")
    assert pool.get_key(MASK1).is_exhausted is True
    # 耗尽 key 移出轮询
    assert pool.next_key() is None


def test_exhausted_key_skipped_in_rotation(pool):
    pool.add_keys_batch([KEY1, KEY2])
    pool.mark_exhausted(MASK1, "quota")
    seen = {pool.next_key()[1] for _ in range(3)}
    assert seen == {MASK2}


def test_mark_recovered_clears_exhausted(pool):
    pool.add_key(KEY1)
    pool.mark_exhausted(MASK1, "quota")
    pool.mark_recovered(MASK1)
    assert pool.get_key(MASK1).is_exhausted is False
    assert pool.next_key() is not None


def test_activate_clears_exhausted(pool):
    pool.add_key(KEY1)
    pool.mark_exhausted(MASK1, "quota")
    pool.activate_key(MASK1)
    assert pool.get_key(MASK1).is_exhausted is False


def test_record_auth_deactivates(pool):
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, False, 0, "401 Unauthorized")
    assert not pool.get_key(MASK1).is_active


def test_record_rate_keeps_active(pool):
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, False, 0, "429 Too Many Requests")
    k = pool.get_key(MASK1)
    assert k.is_active and not k.is_exhausted
    assert k.error_count == 1


def test_record_request_id_logged(pool):
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1, request_id="req-123")
    logs = pool.get_recent_logs(5)
    assert logs[0]["request_id"] == "req-123"


# ── 积分来源标记（usage_source）──────────────────────────────
def test_record_usage_source_logged(pool):
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1, usage_source="response")
    pool.record_request(MASK1, "research", 20, True, 0, request_id="r-1", usage_source="unknown")
    pool.record_request(MASK1, "search", 30, False, 0, "boom", usage_source="none")
    logs = pool.get_recent_logs(10)
    by_ep = {log["endpoint"]: log for log in logs}
    assert by_ep["search"]["usage_source"] == "response"
    assert by_ep["research"]["usage_source"] == "unknown"
    assert by_ep["search"]["success"] == 1
    assert any(log["usage_source"] == "none" for log in logs)


def test_record_source_logged_and_filtered(pool):
    """source 标记写入并按来源筛选（mcp/proxy 区分）。"""
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1, source="proxy")
    pool.record_request(MASK1, "search", 10, True, 1, source="mcp")
    rows, _ = pool.query_logs(source="proxy")
    assert len(rows) == 1
    assert rows[0]["source"] == "proxy"
    rows, _ = pool.query_logs(source="")
    assert len(rows) == 2


def test_local_credits_excludes_unknown(pool):
    """Research（unknown）不参与本地对账：避免官方 usage 与本地差值误报 suspected_leak。"""
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1, usage_source="response")
    pool.record_request(MASK1, "extract", 10, True, 0, usage_source="response")  # 官方批量未达下限=真0
    pool.record_request(MASK1, "research", 10, True, 0, usage_source="unknown")
    assert pool._local_credits(MASK1) == 1.0  # 只统计 response 的成功积分


def test_usage_source_migration_adds_column(pool):
    """旧库迁移：缺少 usage_source 列时自动补充，历史记录 usage_source 为空。"""
    conn = pool._get_conn()
    conn.execute("ALTER TABLE request_log DROP COLUMN usage_source")
    conn.commit()
    pool._init_db()  # 幂等重建：触发 _ensure_columns 补列
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1)
    logs = pool.get_recent_logs(5)
    assert logs[0]["usage_source"] == ""


# ── 令牌桶限流 ───────────────────────────────────────────────
def test_token_bucket_limits_rate():
    from key_pool import _TokenBucket
    b = _TokenBucket(60)  # 60/min = 1/sec，容量 60
    assert all(b.try_acquire() == 0.0 for _ in range(60))
    assert b.try_acquire() > 0.0  # 第 61 次需要等待


def test_next_available_key_skips_limited(monkeypatch, pool):
    pool.add_keys_batch([KEY1, KEY2])
    monkeypatch.setattr(key_pool, "get_settings",
                        lambda: {"endpoint_rpm": {"search": 90}, "rate_limit_rpm": 90})
    for _ in range(90):
        pool._consume_bucket(MASK1, "search")  # 打满 KEY1 的 search 令牌桶
    _, masked = pool.next_available_key("search")
    assert masked == MASK2  # KEY1 受限被跳过


# ── 每 key × 每 endpoint 分组限流 ──────────────────────────
def test_endpoint_buckets_independent(monkeypatch, pool):
    """research 与 search 桶互不影响：打满 research 桶只影响 research 选 key。"""
    pool.add_keys_batch([KEY1, KEY2])
    monkeypatch.setattr(key_pool, "get_settings",
                        lambda: {"endpoint_rpm": {"research": 18}, "rate_limit_rpm": 90})
    for _ in range(18):
        pool._consume_bucket(MASK1, "research")  # 打满 KEY1 的 research 桶
    # research 受限 → 跳过 KEY1 取 KEY2
    _, masked = pool.next_available_key("research")
    assert masked == MASK2
    # search 桶未受影响 → 轮询仍可取 KEY1
    seen = {pool.next_available_key("search")[1] for _ in range(2)}
    assert MASK1 in seen


def test_endpoint_rpm_config_and_fallback(monkeypatch, pool):
    """endpoint_rpm 配置生效；未配置的 endpoint 回退 rate_limit_rpm。"""
    pool.add_key(KEY1)
    monkeypatch.setattr(key_pool, "get_settings",
                        lambda: {"endpoint_rpm": {"research": 18}, "rate_limit_rpm": 90})
    assert pool._bucket(MASK1, "research")._capacity == 18  # 配置生效
    assert pool._bucket(MASK1, "crawl")._capacity == 90     # 未配置 → 回退 90
    # research 桶打满不影响 crawl 桶
    for _ in range(18):
        pool._consume_bucket(MASK1, "research")
    assert pool._bucket(MASK1, "research").wait_time() > 0
    assert pool._bucket(MASK1, "crawl").wait_time() == 0


# ── /usage 缓存与月度恢复 ────────────────────────────────────
def test_sync_usage_cache_hits(monkeypatch, pool):
    pool.add_key(KEY1)
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            calls["n"] += 1
            return {"key": {"usage": 42, "limit": 1000}, "account": {"current_plan": "Researcher"}}

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _Resp())
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"usage_cache_ttl": 60})
    assert pool.sync_usage()[0]["ok"] is True
    assert pool.sync_usage()[0]["ok"] is True
    assert calls["n"] == 1  # 第二次走缓存


def test_sync_usage_recovers_exhausted(monkeypatch, pool):
    pool.add_key(KEY1)
    pool.mark_exhausted(MASK1, "quota")

    class _Resp:
        status_code = 200

        def json(self):
            return {"key": {"usage": 0, "limit": 1000}, "account": {"current_plan": "Researcher"}}

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _Resp())
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"usage_cache_ttl": 60})
    results = pool.sync_usage()
    assert results[0]["recovered"] is True
    assert not pool.get_key(MASK1).is_exhausted


def test_sync_usage_persists_research_usage(monkeypatch, pool):
    """官方 /usage 的 research_usage 落库，供 suspected_leak 对账时扣除。"""
    pool.add_key(KEY1)

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "key": {"usage": 120, "limit": 1000, "research_usage": 100,
                        "search_usage": 15, "extract_usage": 2, "crawl_usage": 1, "map_usage": 2},
                "account": {"current_plan": "Researcher", "plan_limit": 1000},
            }

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _Resp())
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"usage_cache_ttl": 60})
    pool.sync_usage()
    k = pool.get_key(MASK1)
    assert k.credits_used == 120
    assert k.research_usage == 100  # 官方 research 单独落库


def test_suspected_leak_excludes_research(monkeypatch, pool):
    """官方 research 消耗（仅存在于 /usage，本地无对应日志积分）不应误报 suspected_leak。"""
    pool.add_key(KEY1)
    pool.record_request(MASK1, "search", 10, True, 1, usage_source="response")
    pool.record_request(MASK1, "research", 10, True, 0, usage_source="unknown")

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "key": {"usage": 120, "limit": 1000, "research_usage": 100},
                "account": {"current_plan": "Researcher", "plan_limit": 1000},
            }

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _Resp())
    monkeypatch.setattr(key_pool, "get_settings", lambda: {
        "usage_cache_ttl": 60,
        "anomaly_thresholds": {"leak_diff_credits": 50},
    })
    pool.sync_usage()
    # diff = 120 - 100(research) - 1(local) = 19 < 50 → 不误报
    assert not any("suspected_leak" in a["flags"] for a in pool.detect_anomalies())
    # 反向验证：真实泄露（官方用量远超 research+本地）仍能检出
    pool._usage_cache.clear()

    class _Resp2:
        status_code = 200

        def json(self):
            return {
                "key": {"usage": 300, "limit": 1000, "research_usage": 100},
                "account": {"current_plan": "Researcher", "plan_limit": 1000},
            }

    monkeypatch.setattr("httpx.get", lambda url, headers=None, timeout=None: _Resp2())
    pool.sync_usage()
    # diff = 300 - 100 - 1 = 199 >= 50 → 检出
    assert any("suspected_leak" in a["flags"] for a in pool.detect_anomalies())


def test_get_aggregate(pool):
    pool.add_keys_batch([KEY1, KEY2])
    conn = pool._get_conn()
    conn.execute("UPDATE api_keys SET credits_used=400, credits_limit=1000 WHERE masked=?", (MASK1,))
    conn.execute("UPDATE api_keys SET credits_used=100, credits_limit=1000 WHERE masked=?", (MASK2,))
    conn.commit()
    agg = pool.get_aggregate()
    assert agg["total_limit"] == 2000
    assert agg["total_used"] == 500
    assert agg["remaining"] == 1500


# ── 异常识别 ────────────────────────────────────────────────
def test_detect_anomalies_suspected_leak(pool, monkeypatch):
    pool.add_key(KEY1)
    conn = pool._get_conn()
    now = key_pool.time.time()
    for _ in range(10):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, created_at) VALUES (?,?,?,?,?)",
            (MASK1, "search", 1, 1, now),
        )
    conn.execute("UPDATE api_keys SET credits_used=200, credits_limit=1000 WHERE masked=?", (MASK1,))
    conn.commit()
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"anomaly_thresholds": {"leak_diff_credits": 50}})
    anomalies = pool.detect_anomalies()
    assert anomalies and "suspected_leak" in anomalies[0]["flags"]


def test_detect_anomalies_high_error_rate(pool, monkeypatch):
    pool.add_key(KEY1)
    conn = pool._get_conn()
    now = key_pool.time.time()
    for _ in range(7):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, created_at) VALUES (?,?,?,?,?,?)",
            (MASK1, "search", 0, 0, "request timed out", now),
        )
    for _ in range(3):
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, created_at) VALUES (?,?,?,?,?)",
            (MASK1, "search", 1, 1, now),
        )
    conn.commit()
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"anomaly_thresholds": {"error_rate": 0.3}})
    anomalies = pool.detect_anomalies()
    assert anomalies and "high_error_rate" in anomalies[0]["flags"]


def test_detect_anomalies_ignores_client_errors(pool, monkeypatch):
    """客户端请求错误（is_client_error=1，如 HTTP 400）不应触发高错误率。"""
    pool.add_key(KEY1)
    # 7 次客户端错误 + 3 次成功：若计入错误率 70% 会误报
    for _ in range(7):
        pool.record_request(MASK1, "search", 10, False, 0,
                            "400 Bad Request: invalid search_depth", is_client_error=1)
    for _ in range(3):
        pool.record_request(MASK1, "search", 10, True, 1)
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"anomaly_thresholds": {"error_rate": 0.3}})
    anomalies = pool.detect_anomalies()
    assert not any("high_error_rate" in a["flags"] for a in anomalies)


def test_detect_anomalies_high_error_rate_mixed(pool, monkeypatch):
    """混合客户端/服务器错误：高错误率只按服务器错误计算（分子分母均排除客户端）。"""
    pool.add_key(KEY1)
    # 3 次客户端错误 + 4 次服务器错误（timeout）+ 3 次成功
    # 服务器错误率 = 4 / (10 - 3) ≈ 57% > 0.3 → 触发
    for _ in range(3):
        pool.record_request(MASK1, "search", 10, False, 0,
                            "400 Bad Request: invalid parameter", is_client_error=1)
    for _ in range(4):
        pool.record_request(MASK1, "search", 10, False, 0, "request timed out")
    for _ in range(3):
        pool.record_request(MASK1, "search", 10, True, 1)
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"anomaly_thresholds": {"error_rate": 0.3}})
    anomalies = pool.detect_anomalies()
    assert anomalies and "high_error_rate" in anomalies[0]["flags"]


def test_classify_error_bad_request():
    assert key_pool._classify_error("Client error '400 Bad Request' for url 'https://api.tavily.com/search'") == "bad_request"
    assert key_pool._classify_error("Request validation failed: search_depth") == "bad_request"
    assert key_pool._classify_error("boom") == "other"


# ── request_log 保留策略 ──────────────────────────────────────
def test_prune_request_log_removes_old(pool):
    """超期日志被清理，未超期日志保留。"""
    now = time.time()
    pool.record_request(MASK1, "search", 10, True, credits=1, usage_source="response")
    conn = pool._get_conn()
    conn.execute(
        "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("tvly-old***", "search", 1, 1, "", 10, "", "response", now - 100 * 86400),
    )
    conn.commit()
    deleted = pool.prune_request_log(retention_days=90)
    assert deleted >= 1
    rows = conn.execute(
        "SELECT key_masked FROM request_log WHERE created_at > ?", (now - 86400,)
    ).fetchall()
    assert all(r["key_masked"] != "tvly-old***" for r in rows)
    assert any(r["key_masked"] == MASK1 for r in rows)


def test_prune_request_log_config_disabled(pool, monkeypatch):
    """配置 log_retention_days=0 表示不清理。"""
    monkeypatch.setattr(key_pool, "get_settings", lambda: {"log_retention_days": 0})
    conn = pool._get_conn()
    conn.execute(
        "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("tvly-old***", "search", 1, 1, "", 10, "", "response", time.time() - 100 * 86400),
    )
    conn.commit()
    assert pool.prune_request_log() == 0


# ── 用量趋势与日志筛选 ────────────────────────────────────────
def test_get_usage_trend_aggregates(pool):
    """趋势按天聚合：请求数/成功/失败/积分，并补齐无请求的日期。"""
    now = time.time()
    conn = pool._get_conn()
    conn.execute(
        "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (MASK1, "search", 1, 1, "", 10, "", "response", now),
    )
    conn.execute(
        "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (MASK1, "research", 0, 0, "err", 20, "", "none", now),
    )
    conn.execute(
        "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (MASK1, "extract", 2, 1, "", 15, "", "response", now - 86400),
    )
    conn.commit()
    t = pool.get_usage_trend(7)
    assert t["days"] == 7
    assert len(t["points"]) == 7
    last = t["points"][-1]  # 今天
    assert last["requests"] == 2
    assert last["success"] == 1
    assert last["failed"] == 1
    assert last["credits"] == 1
    assert last["endpoints"].get("search") == 1
    assert t["points"][-2]["requests"] == 1  # 昨天
    assert t["points"][-2]["credits"] == 2


def test_query_logs_filters(pool):
    """日志筛选：按接口/状态/时间过滤，返回总数与分页。"""
    now = time.time()
    conn = pool._get_conn()
    for ep, ok, dt in [("search", 1, now), ("search", 0, now), ("research", 1, now - 86400), ("extract", 1, now - 2 * 86400)]:
        conn.execute(
            "INSERT INTO request_log (key_masked, endpoint, credits_consumed, success, error_msg, latency_ms, request_id, usage_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (MASK1, ep, 1, ok, "" if ok else "err", 10, "", "response", dt),
        )
    conn.commit()
    rows, total = pool.query_logs(endpoint="search")
    assert total == 2
    assert len(rows) == 2
    rows, total = pool.query_logs(status="failed")
    assert total == 1
    rows, total = pool.query_logs(since=now - 3600)
    assert total == 2  # 近 1 小时内的两条
    rows, total = pool.query_logs(limit=2)
    assert total == 4
    assert len(rows) == 2
    rows, total = pool.query_logs(offset=2)
    assert len(rows) == 2


# ── 重计算缓存（detect_anomalies / get_usage_trend TTL 缓存） ─
def test_detect_anomalies_ttl_cache(pool, monkeypatch):
    """异常识别结果短 TTL 缓存：TTL 内重复调用不重算，失效后重算。"""
    calls = {"n": 0}

    def fake_impl():
        calls["n"] += 1
        return [{"masked": "k", "flags": []}]

    monkeypatch.setattr(pool, "_detect_anomalies_impl", fake_impl)
    pool.detect_anomalies()
    pool.detect_anomalies()
    assert calls["n"] == 1              # TTL 内命中缓存
    pool._anomalies_cache.clear()       # 失效
    pool.detect_anomalies()
    assert calls["n"] == 2


def test_get_usage_trend_ttl_cache(pool, monkeypatch):
    """用量趋势按天聚合缓存：按 days 分键，失效后重算。"""
    calls = {"n": 0}

    def fake_impl(days, source="", project=""):
        calls["n"] += 1
        return {"days": days, "points": []}

    monkeypatch.setattr(pool, "_get_usage_trend_impl", fake_impl)
    pool.get_usage_trend(7)
    pool.get_usage_trend(7)
    assert calls["n"] == 1              # 同 days 命中缓存
    pool.get_usage_trend(30)            # 不同 days → 不同缓存键
    assert calls["n"] == 2
    pool.get_usage_trend(7, source="proxy")  # 不同 source → 不同缓存键
    assert calls["n"] == 3
    pool.get_usage_trend(7, project="proj-a")  # 不同 project → 不同缓存键
    assert calls["n"] == 4
    pool._invalidate_caches()
    pool.get_usage_trend(7)
    assert calls["n"] == 5


def test_usage_trend_filters_project(pool):
    """用量趋势按项目（request_log.project_id）筛选，且不影响其他项目统计。"""
    pool.record_request(MASK1, "search", 10, True, 1, project_id="proj-a", usage_source="response")
    pool.record_request(MASK1, "search", 10, True, 2, project_id="proj-a", usage_source="response")
    pool.record_request(MASK1, "search", 10, True, 1, project_id="proj-b", usage_source="response")
    pool.record_request(MASK1, "search", 10, True, 1, project_id="", usage_source="response")
    all_trend = pool.get_usage_trend(1)
    a_trend = pool.get_usage_trend(1, project="proj-a")
    b_trend = pool.get_usage_trend(1, project="proj-b")
    all_day = all_trend["points"][-1]
    a_day = a_trend["points"][-1]
    b_day = b_trend["points"][-1]
    assert all_day["requests"] == 4
    assert a_day["requests"] == 2
    assert a_day["credits"] == 3
    assert b_day["requests"] == 1
    pool._invalidate_caches()


def test_reset_runtime_state_clears_inmemory(pool, monkeypatch):
    """备份恢复后：限流桶/用量缓存/重计算缓存清空并广播失效信号。"""
    from cache import signal_mtime

    pool.add_keys_batch([KEY1, KEY2])
    pool.next_available_key("search")
    assert pool._buckets
    pool._usage_cache[MASK1] = (time.time(), {})
    pool._anomalies_cache.set(("a",), {"x": 1}, 5.0)
    pool._trend_cache.set(("t", 7, ""), {"points": []}, 30.0)
    before = signal_mtime()
    pool.reset_runtime_state()
    assert not pool._buckets
    assert not pool._usage_cache
    assert not pool._anomalies_cache
    assert not pool._trend_cache
    assert signal_mtime() >= before


def test_bucket_rebuilt_when_rpm_config_changes(pool, monkeypatch):
    """endpoint_rpm 配置变化后令牌桶按新速率重建（热刷新生效）。"""
    pool.add_key(KEY1)
    monkeypatch.setattr(
        key_pool, "get_settings_fresh",
        lambda: {"endpoint_rpm": {"search": 90}, "rate_limit_rpm": 90},
    )
    assert pool._bucket(MASK1, "search").rate_per_min == 90
    monkeypatch.setattr(
        key_pool, "get_settings_fresh",
        lambda: {"endpoint_rpm": {"search": 30}, "rate_limit_rpm": 90},
    )
    assert pool._bucket(MASK1, "search").rate_per_min == 30


def test_write_ops_invalidate_anomalies_cache(pool, monkeypatch):
    """写操作（add/deactivate/sync）后异常识别缓存自动失效。"""
    calls = {"n": 0}

    def fake_impl():
        calls["n"] += 1
        return []

    monkeypatch.setattr(pool, "_detect_anomalies_impl", fake_impl)
    pool.detect_anomalies()
    assert calls["n"] == 1
    pool.add_key(KEY1)
    pool.detect_anomalies()
    assert calls["n"] == 2              # add 后失效
    pool.detect_anomalies()
    pool.deactivate_key(MASK1, "t")
    pool.detect_anomalies()
    assert calls["n"] == 3              # add、deactivate 各失效一次


def test_record_request_does_not_invalidate_every_call(pool, monkeypatch):
    """record_request 高频触发，不应每次清缓存（靠 TTL 自然过期）。"""
    calls = {"n": 0}

    def fake_impl():
        calls["n"] += 1
        return []

    monkeypatch.setattr(pool, "_detect_anomalies_impl", fake_impl)
    pool.add_key(KEY1)
    pool.detect_anomalies()             # 计算并缓存
    assert calls["n"] == 1
    pool.record_request(MASK1, "search", 10, True, 0, "", "response")
    pool.detect_anomalies()
    assert calls["n"] == 1              # 仍在 TTL 内，未因 record 被清除


# ── 跨进程失效同步（信号文件 mtime） ────────────────────────
def test_remote_invalidate_signal_clears_cache(pool, monkeypatch, tmp_path):
    """其他进程写操作广播信号后，本进程读取缓存前检测到并清空重计算缓存。"""
    import cache as cache_mod

    monkeypatch.setattr(cache_mod, "_signal_file", tmp_path / "sig")
    calls = {"n": 0}

    def fake_impl():
        calls["n"] += 1
        return []

    monkeypatch.setattr(pool, "_detect_anomalies_impl", fake_impl)
    pool.detect_anomalies()
    assert calls["n"] == 1              # 已缓存
    cache_mod.emit_invalidate()         # 模拟其他进程写操作广播
    pool.detect_anomalies()             # 检测到信号 → 清缓存重算
    assert calls["n"] == 2
    cache_mod.emit_invalidate()         # 再次广播
    pool.detect_anomalies()
    assert calls["n"] == 3              # 每次广播都触发重算（信号确实生效）


def test_local_invalidate_does_not_clear_twice(pool, monkeypatch, tmp_path):
    """本进程写操作：_invalidate_caches 清缓存并同步记录信号，避免下次误清。"""
    import cache as cache_mod

    monkeypatch.setattr(cache_mod, "_signal_file", tmp_path / "sig")
    calls = {"n": 0}

    def fake_impl():
        calls["n"] += 1
        return []

    monkeypatch.setattr(pool, "_detect_anomalies_impl", fake_impl)
    pool.add_key(KEY1)                  # 写操作：_invalidate_caches 内部已清缓存 + 记录信号
    pool.detect_anomalies()
    assert calls["n"] == 1
    pool.detect_anomalies()             # 本进程信号已记录 → 不再因自身广播误清
    assert calls["n"] == 1              # 命中缓存
    pool.deactivate_key(MASK1, "t")     # 又一次写操作广播
    pool.detect_anomalies()
    assert calls["n"] == 2

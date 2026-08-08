"""notify 异常通知测试：去重/节流、Webhook、池空告警。"""
import notify


def _reset_dedup():
    notify._notified.clear()


class _FakeTray:
    def __init__(self):
        self.shown = []

    def notify(self, title, message, icon=0):
        self.shown.append((title, message, icon))


def test_check_and_notify_webhook_dedup(monkeypatch):
    """同 key 同 flag 去重：第二次调用不再通知；Webhook 收到 JSON。"""
    _reset_dedup()
    sent = []

    def fake_send(url, payload):
        sent.append(payload)
        return True

    class _FakeKey:
        is_active = True
        is_exhausted = False

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["exhausted"], "reasons": ["官方额度已耗尽"], "usage_pct": 100.0}]

        def list_keys(self):
            return [_FakeKey()]  # 有可用 key，避免池空分支干扰

    monkeypatch.setattr(notify, "get_settings",
                        lambda: {"notify_webhook": "https://hook.example/x", "notify_tray": False})
    monkeypatch.setattr(notify, "send_webhook", fake_send)
    n1 = notify.check_and_notify(_FakePool())
    assert len(n1) == 1
    assert len(sent) == 1
    assert sent[0]["flag"] == "exhausted"
    assert sent[0]["masked"] == "tvly-***"
    # 去重窗口内再次检测 → 不通知
    n2 = notify.check_and_notify(_FakePool())
    assert n2 == []
    assert len(sent) == 1
    _reset_dedup()


def test_notify_tray_balloon(monkeypatch):
    """notify_tray=True 且传入托盘时，异常会显示气泡。"""
    _reset_dedup()
    tray = _FakeTray()

    class _FakeKey:
        is_active = True
        is_exhausted = False

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["suspected_leak"], "reasons": ["疑似被外部使用"]}]

        def list_keys(self):
            return [_FakeKey()]

    monkeypatch.setattr(notify, "get_settings",
                        lambda: {"notify_webhook": "", "notify_tray": True})
    n = notify.check_and_notify(_FakePool(), tray=tray)
    assert len(n) == 1
    assert tray.shown, "应显示托盘气泡"
    assert "suspected_leak" in tray.shown[0][0] or "疑似泄露" in tray.shown[0][1]
    _reset_dedup()


def test_pool_empty_notify(monkeypatch):
    """池空：存在 key 但全部不可用时触发全局告警。"""
    _reset_dedup()
    sent = []

    def fake_send(url, payload):
        sent.append(payload)
        return True

    class _FakeKey:
        def __init__(self, active, exhausted):
            self.is_active = active
            self.is_exhausted = exhausted

    class _FakePool:
        def detect_anomalies(self):
            return []

        def list_keys(self):
            return [_FakeKey(True, True), _FakeKey(False, False)]  # 全部不可用

    monkeypatch.setattr(notify, "get_settings",
                        lambda: {"notify_webhook": "https://hook.example/x", "notify_tray": False})
    monkeypatch.setattr(notify, "send_webhook", fake_send)
    n = notify.check_and_notify(_FakePool())
    assert len(n) == 1
    assert n[0]["flags"] == ["pool_empty"]
    assert sent[0]["flag"] == "pool_empty"
    _reset_dedup()


def test_check_and_notify_no_channels(monkeypatch):
    """两个渠道都不可用时不执行（避免空转）。"""

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["exhausted"], "reasons": []}]

        def list_keys(self):
            return []

    monkeypatch.setattr(notify, "get_settings", lambda: {"notify_webhook": "", "notify_tray": True})
    assert notify.check_and_notify(_FakePool(), tray=None) == []


def test_notify_icons_by_flag(monkeypatch):
    """不同异常 flag 使用对应图标：错误类 NIIF_ERROR、警告类 NIIF_WARNING、信息类 NIIF_INFO。"""
    from tray import NIIF_ERROR, NIIF_INFO, NIIF_WARNING

    class _FakeKey:
        is_active = True
        is_exhausted = False

    cases = [
        ("exhausted", NIIF_ERROR),
        ("high_error_rate", NIIF_ERROR),
        ("near_exhausted", NIIF_WARNING),
        ("suspected_leak", NIIF_WARNING),
        ("stale", NIIF_INFO),
        ("slow", NIIF_INFO),
    ]
    for flag, expected in cases:
        _reset_dedup()
        tray = _FakeTray()

        class _FakePool:
            def detect_anomalies(self):
                return [{"masked": "tvly-***", "flags": [flag], "reasons": [f"{flag} reason"]}]

            def list_keys(self):
                return [_FakeKey()]

        monkeypatch.setattr(notify, "get_settings", lambda: {"notify_webhook": "", "notify_tray": True})
        n = notify.check_and_notify(_FakePool(), tray=tray)
        assert len(n) == 1, f"{flag} 应通知"
        assert tray.shown, f"{flag} 应显示气泡"
        assert tray.shown[0][2] == expected, f"{flag} 图标应为 {expected}，实际 {tray.shown[0][2]}"
    _reset_dedup()


def test_pool_empty_uses_error_icon(monkeypatch):
    """池空全局告警使用 NIIF_ERROR 图标。"""
    from tray import NIIF_ERROR

    _reset_dedup()

    class _FakeKey:
        def __init__(self, active, exhausted):
            self.is_active = active
            self.is_exhausted = exhausted

    class _FakePool:
        def detect_anomalies(self):
            return []

        def list_keys(self):
            return [_FakeKey(True, True), _FakeKey(False, False)]  # 全部不可用

    tray = _FakeTray()
    monkeypatch.setattr(notify, "get_settings", lambda: {"notify_webhook": "", "notify_tray": True})
    n = notify.check_and_notify(_FakePool(), tray=tray)
    assert len(n) == 1
    assert n[0]["flags"] == ["pool_empty"]
    assert tray.shown[0][2] == NIIF_ERROR
    _reset_dedup()


def test_tray_failure_degrades_to_webhook(monkeypatch):
    """托盘 notify 抛异常时降级到 webhook，仍视为已通知。"""
    _reset_dedup()
    sent = []

    def fake_send(url, payload):
        sent.append(payload)
        return True

    class _BrokenTray:
        def notify(self, title, message, icon=0):
            raise RuntimeError("tray broken")

    class _FakeKey:
        is_active = True
        is_exhausted = False

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["exhausted"], "reasons": ["额度耗尽"]}]

        def list_keys(self):
            return [_FakeKey()]

    monkeypatch.setattr(
        notify, "get_settings", lambda: {"notify_webhook": "https://hook.example/x", "notify_tray": True}
    )
    monkeypatch.setattr(notify, "send_webhook", fake_send)
    n = notify.check_and_notify(_FakePool(), tray=_BrokenTray())
    assert len(n) == 1  # webhook 成功 → 视为已通知
    assert len(sent) == 1
    _reset_dedup()


def test_webhook_failure_keeps_tray(monkeypatch):
    """webhook 失败时托盘仍显示，且视为已通知（至少一个渠道成功）。"""
    _reset_dedup()
    tray = _FakeTray()

    class _FakeKey:
        is_active = True
        is_exhausted = False

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["slow"], "reasons": ["响应慢"]}]

        def list_keys(self):
            return [_FakeKey()]

    monkeypatch.setattr(
        notify, "get_settings", lambda: {"notify_webhook": "https://hook.invalid/x", "notify_tray": True}
    )
    monkeypatch.setattr(notify, "send_webhook", lambda url, payload: False)
    n = notify.check_and_notify(_FakePool(), tray=tray)
    assert len(n) == 1
    assert tray.shown
    _reset_dedup()


def test_all_channels_failed_retries_next_round(monkeypatch):
    """全渠道推送失败时回滚去重标记：下一轮检测仍会尝试通知。"""
    _reset_dedup()
    calls = {"tray": 0}

    class _BrokenTray:
        def notify(self, title, message, icon=0):
            calls["tray"] += 1
            raise RuntimeError("tray broken")

    class _FakeKey:
        is_active = True
        is_exhausted = False

    class _FakePool:
        def detect_anomalies(self):
            return [{"masked": "tvly-***", "flags": ["exhausted"], "reasons": ["额度耗尽"]}]

        def list_keys(self):
            return [_FakeKey()]

    monkeypatch.setattr(
        notify, "get_settings", lambda: {"notify_webhook": "https://hook.invalid/x", "notify_tray": True}
    )
    monkeypatch.setattr(notify, "send_webhook", lambda url, payload: False)
    n1 = notify.check_and_notify(_FakePool(), tray=_BrokenTray())
    assert n1 == []  # 全渠道失败 → 不计入实际通知
    n2 = notify.check_and_notify(_FakePool(), tray=_BrokenTray())
    assert n2 == []
    assert calls["tray"] == 2  # 去重已回滚 → 第二轮仍尝试推送
    assert notify._notified == {}  # 回滚生效：无残留标记
    _reset_dedup()

#!/usr/bin/env python3
"""
通用 TTL 缓存基础设施（线程安全）。

提供：
- TTLCache：可手动管理的 TTL 缓存（get/set/delete/invalidate/clear）
- ttl_cached：函数级 TTL 缓存装饰器（支持 skip_cache=True 强制刷新）

统一使用单调时钟（time.monotonic），不受系统时间调整影响。
所有实例化组件（KeyPool、Dashboard）共用本模块，避免各处自行实现
不一致的缓存逻辑。
"""
from __future__ import annotations

import functools
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


class TTLCache:
    """线程安全的 TTL 缓存。

    键为任意可哈希对象；值为 (expire_at, value)。过期项在读取与写入时惰性
    清理；条目数超过 maxsize 时优先淘汰已过期项，仍超限则淘汰最旧项。
    """

    def __init__(self, default_ttl: float = 60.0, maxsize: int = 1024):
        self._default_ttl = float(default_ttl)
        self._maxsize = max(1, int(maxsize))
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        """读取缓存；未命中或已过期返回 default（过期项同时被清理）。"""
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expire_at, value = item
            if expire_at <= now:
                del self._data[key]
                return default
            return value

    def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        """写入缓存；ttl 缺省时使用实例默认 TTL。"""
        ttl = float(ttl) if ttl is not None else self._default_ttl
        expire_at = time.monotonic() + max(0.0, ttl)
        with self._lock:
            self._data[key] = (expire_at, value)
            if len(self._data) <= self._maxsize:
                return
            now = time.monotonic()
            stale = [k for k, (e, _) in self._data.items() if e <= now]
            for k in stale:
                del self._data[k]
            while len(self._data) > self._maxsize:
                self._data.pop(next(iter(self._data)))

    def delete(self, key: Any) -> None:
        """精确删除一个键。"""
        with self._lock:
            self._data.pop(key, None)

    def invalidate(self, prefix: Any = "") -> int:
        """按 key 前缀批量失效（prefix='' 时清空全部）。返回失效条数。"""
        with self._lock:
            if not prefix:
                n = len(self._data)
                self._data.clear()
                return n
            p = str(prefix)
            keys = [k for k in self._data if str(k).startswith(p)]
            for k in keys:
                del self._data[k]
            return len(keys)

    def clear(self) -> None:
        """清空全部缓存。"""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def ttl_cached(ttl: float = 60.0, maxsize: int = 1024,
               key_func: Optional[Callable] = None) -> Callable:
    """函数级 TTL 缓存装饰器。

    - 以 (args, tuple(sorted(kwargs.items()))) 作为缓存键；可用 key_func 自定义。
    - 传入 skip_cache=True 时跳过缓存强制刷新（该参数不会传给被装饰函数）。
    - 适用于结果可复用、无副作用、调用较重的函数；返回值不应为 None
      （None 视为未命中），否则请用 TTLCache 手动管理。
    - 装饰器实例暴露 .cache（TTLCache），可在外侧失效：
        @ttl_cached(ttl=5)
        def heavy(): ...
        heavy.cache.clear()
    """
    def deco(fn: Callable) -> Callable:
        cache = TTLCache(default_ttl=ttl, maxsize=maxsize)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            skip = kwargs.pop("skip_cache", False)
            key = key_func(args, kwargs) if key_func is not None else (
                args,
                tuple(sorted(kwargs.items())),
            )
            if not skip:
                hit = cache.get(key)
                if hit is not None:
                    return hit
            value = fn(*args, **kwargs)
            cache.set(key, value, ttl)
            return value

        wrapper.cache = cache  # type: ignore[attr-defined]
        return wrapper

    return deco


# ── 跨进程失效信号（文件 mtime）────────────────────────────
# 本应用为多进程架构（dashboard / MCP 子进程 / proxy 子进程），各进程的
# KeyPool 缓存相互独立。写操作方（如 dashboard 停用 key）通过原子更新一个
# 共享信号文件的时间戳广播"缓存已失效"；其他进程在读取重计算缓存前检查
# mtime，比本进程见过的更新则清空自身缓存。成本：每次读取一次 stat（µs 级）。
_signal_file: Optional[Path] = None
_signal_lock = threading.Lock()


def set_signal_file(path: Optional[Path]) -> None:
    """设置跨进程失效信号文件路径（缺省为 runtime_dir()/cache_invalidate.sig）。"""
    global _signal_file
    with _signal_lock:
        _signal_file = path


def _sig_path() -> Optional[Path]:
    with _signal_lock:
        p = _signal_file
    if p is not None:
        return p
    try:
        from paths import runtime_dir

        return runtime_dir() / "cache_invalidate.sig"
    except Exception:  # noqa: BLE001
        return None


def emit_invalidate() -> None:
    """广播跨进程失效：原子更新信号文件（先写临时文件再 rename，避免半写）。"""
    p = _sig_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(str(time.time()), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


def signal_mtime() -> float:
    """信号文件 mtime；文件不存在返回 0。"""
    p = _sig_path()
    if p is None:
        return 0.0
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0

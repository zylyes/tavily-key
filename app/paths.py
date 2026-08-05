"""
统一运行时路径 — 所有可写数据集中到 data/ 目录。

文件布局（v0.3.0 起）：
  - 打包后（frozen）：<exe 目录>/data/
  - 开发时         ：<项目根目录>/data/

data/ 内集中存放：config.json、tavily_keys.db（含 -shm/-wal）、
日志（tavily.log / mcp_server.log 等）、.tavily-secret.key。

首次运行时自动把旧版散落在根目录/exe 目录的同名文件迁移到 data/，
保证升级后配置与数据不丢失（幂等、线程安全）。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

# 旧版散落位置里需要迁移的运行文件（相对于 base_dir）
_LEGACY_FILES = (
    "config.json",
    "tavily_keys.db",
    "tavily_keys.db-shm",
    "tavily_keys.db-wal",
    "tavily.log",
    "mcp_server.log",
    "dashboard_server.log",
    "dashboard_server.err.log",
    ".tavily-secret.key",
)

_lock = threading.Lock()
_migrated = False


def base_dir() -> Path:
    """程序根目录：打包后为 exe 所在目录，开发时为项目根目录（app 的上级）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def runtime_dir() -> Path:
    """运行时数据目录（自动创建）：打包后 <exe>/data，开发时 <项目根>/data。

    所有模块应通过本函数获取可写数据目录，不要自行拼路径。
    """
    d = base_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    migrate_legacy()
    return d


def migrate_legacy() -> None:
    """把旧版散落在根目录/exe 目录的运行文件迁移到 data/（幂等、线程安全）。"""
    global _migrated
    if _migrated:
        return
    with _lock:
        if _migrated:
            return
        _migrate(base_dir())
        _migrated = True


def _migrate(src: Path) -> None:
    """把 src 目录下的旧版运行文件迁移到 src/data/（供 migrate_legacy 与测试使用）。

    - 目标不存在：直接移动。
    - 目标已存在：
      - config.json：JSON 键合并（源键优先，避免升级后丢失新配置项），成功后移除源；
        合并失败（如非法 JSON）则两份都保留，绝不丢数据。
      - 其他文件：以 data/ 下已有文件为准并移除源，保证数据统一收敛到 data/。
    单个文件失败不影响其他文件。
    """
    dst = src / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for name in _LEGACY_FILES:
        s = src / name
        if not (s.exists() and s.is_file()):
            continue
        t = dst / name
        try:
            if not t.exists():
                s.replace(t)
                continue
            if name == "config.json":
                if _merge_config_into(s, t):
                    continue
                continue  # 合并失败：两份都保留，不删除源
            # 其他文件目标已存在：以 data/ 为准，移除源
            s.unlink()
        except OSError:
            pass


def _merge_config_into(src: Path, dst: Path) -> bool:
    """把 src 的 JSON 键合并进 dst（src 键优先），成功返回 True 并移除 src。"""
    try:
        import json

        merged = dict(json.loads(dst.read_text(encoding="utf-8-sig")))
        for k, v in json.loads(src.read_text(encoding="utf-8-sig")).items():
            merged[k] = v
        dst.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        src.unlink()
        return True
    except Exception:
        return False

"""
统一日志配置 — 控制台 + 文件双输出。

windowed 打包（Tavily.exe --noconsole）下 stdout/stderr 可能为 None，
print 会静默丢失，因此关键模块统一走本模块的 logger 并写文件
（日志统一写入 data/ 目录，见 paths.runtime_dir）。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from paths import runtime_dir

_root: logging.Logger | None = None

# 日志轮转默认值（可经 config.json 的 log_max_bytes / log_backup_count 覆盖；
# 长驻进程（MCP/代理/面板）日志不再无限膨胀）
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024   # 5MB
_DEFAULT_BACKUP_COUNT = 3


def _rotation_config() -> tuple[int, int]:
    """从 config.json 读取日志轮转参数（进程启动时读取一次；异常回退默认值）。"""
    try:
        from settings import get_settings

        cfg = get_settings()
        max_bytes = int(cfg.get("log_max_bytes") or _DEFAULT_MAX_BYTES)
        backup_count = int(cfg.get("log_backup_count") or _DEFAULT_BACKUP_COUNT)
        if max_bytes <= 0 or backup_count < 0:
            raise ValueError
        return max_bytes, backup_count
    except Exception:  # noqa: BLE001
        return _DEFAULT_MAX_BYTES, _DEFAULT_BACKUP_COUNT


def setup(
    name: str = "tavily",
    filename: str = "tavily.log",
    level: int = logging.INFO,
) -> logging.Logger:
    """初始化根 logger（幂等），返回可复用的根 logger。"""
    global _root
    if _root is not None:
        return _root

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 文件输出（windowed exe 下 print 不可靠，日志以文件为准；RotatingFileHandler
    # 按大小轮转，长驻进程日志不再无限膨胀）
    try:
        path = runtime_dir() / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes, backup_count = _rotation_config()
        fh = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass

    # 控制台输出（stdio/CLI/开发模式可见）
    try:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    except Exception:  # noqa: BLE001
        pass

    _root = logger
    return logger


def get_logger(module: str) -> logging.Logger:
    """获取子 logger（先确保根 logger 已初始化）。"""
    if _root is None:
        setup()
    return logging.getLogger(f"tavily.{module}")

"""
统一日志配置 — 控制台 + 文件双输出。

windowed 打包（Tavily.exe --noconsole）下 stdout/stderr 可能为 None，
print 会静默丢失，因此关键模块统一走本模块的 logger 并写文件
（日志统一写入 data/ 目录，见 paths.runtime_dir）。
"""
from __future__ import annotations

import logging

from paths import runtime_dir

_root: logging.Logger | None = None


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

    # 文件输出（windowed exe 下 print 不可靠，日志以文件为准）
    try:
        path = runtime_dir() / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
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

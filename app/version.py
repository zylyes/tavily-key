"""集中版本号定义。

项目所有版本相关展示（面板「关于与更新」、CLI update-check、GitHub
更新检查对比）统一引用本模块，避免各处硬编码版本号导致不一致。
"""
from __future__ import annotations

__version__ = "0.13.0"

# GitHub 仓库（owner/repo）。release 以 vX.Y.Z 标签发布，CHANGELOG 底部
# 有各版本链接；可在配置 update_repo 中覆盖（见 settings.DEFAULTS）。
DEFAULT_REPO = "zylyes/tavily-key"

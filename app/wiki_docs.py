"""面板内置 wiki 文档服务：扫描 docs/wiki 提供目录树与 markdown 内容。

面板「文档」视图用前端 MdView 组件渲染（离线、零外部依赖、已防 XSS）。
- 文档路径严格限制在 docs/wiki 目录内（resolve 后校验前缀），防路径穿越；
- 文档实时从磁盘读取（编辑 docs/ 后免重启即生效）；
- 目录按子目录分类，标题取自 md 首个 `# ` 一级标题（无则回退文件名）。
"""
from __future__ import annotations

import re
from pathlib import Path

_WIKI_ROOT = Path(__file__).resolve().parent.parent / "docs" / "wiki"

# 目录名 → 面板展示名（未列出的目录原样展示）
_CATEGORY_LABELS = {
    "项目概述": "项目概述",
    "快速开始": "快速开始",
    "核心功能": "核心功能",
    "Web控制台": "Web 控制台",
    "CLI使用": "CLI 使用",
    "架构设计": "架构设计",
    "部署运维": "部署运维",
}


def _title_from_md(text: str, fallback: str) -> str:
    """从 markdown 提取一级标题（首个 `# `），无则回退文件名。"""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return fallback


def _read_safe(path: str) -> str | None:
    """读取 docs/wiki 下的 .md 文档；路径穿越 / 不存在返回 None。"""
    try:
        p = (_WIKI_ROOT / path).resolve()
        if not str(p).startswith(str(_WIKI_ROOT.resolve())):
            return None
        if p.suffix.lower() != ".md" or not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


def docs_tree() -> list[dict]:
    """扫描 docs/wiki 返回目录树：[{category, docs: [{name, path, title}]}]。"""
    if not _WIKI_ROOT.is_dir():
        return []
    tree: list[dict] = []
    for d in sorted(_WIKI_ROOT.iterdir()):
        if not d.is_dir():
            continue
        docs: list[dict] = []
        for f in sorted(d.glob("*.md")):
            rel = f.relative_to(_WIKI_ROOT).as_posix()
            content = _read_safe(rel) or ""
            docs.append({"name": f.stem, "path": rel, "title": _title_from_md(content, f.stem)})
        if docs:
            tree.append({"category": _CATEGORY_LABELS.get(d.name, d.name), "docs": docs})
    return tree


def get_doc(path: str) -> dict | None:
    """返回 {path, title, content}；路径非法 / 文档不存在返回 None。"""
    content = _read_safe(path)
    if content is None:
        return None
    return {"path": path, "title": _title_from_md(content, Path(path).stem), "content": content}

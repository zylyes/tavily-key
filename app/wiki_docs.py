"""面板内置 wiki 文档服务：扫描 docs/wiki 提供目录树与 markdown 内容。

面板「文档」视图用前端 MdView 组件渲染（离线、零外部依赖、已防 XSS）。
- 文档路径严格限制在 docs/wiki 目录内（resolve 后校验前缀），防路径穿越；
- 文档实时从磁盘读取（编辑 docs/ 后免重启即生效）；
- 目录按子目录分类，标题取自 md 首个 `# ` 一级标题（无则回退文件名）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _wiki_root() -> Path:
    """wiki 文档根目录。

    - 开发时：项目根下的 docs/wiki；
    - 打包后：优先用 exe 旁可编辑的 docs/wiki（用户自定义/覆盖、升级不丢），
      不存在则回退到内置在 _MEIPASS 的默认文档（Tavily.spec 已打包 docs/wiki）。
    """
    if getattr(sys, "frozen", False):
        external = Path(sys.executable).resolve().parent / "docs" / "wiki"
        if external.is_dir():
            return external
        return Path(sys._MEIPASS) / "docs" / "wiki"
    return Path(__file__).resolve().parent.parent / "docs" / "wiki"

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


def _fix_mojibake(text: str) -> str:
    """修复 zip 解压产生的乱码文件名（GBK 字节被按 CP437 解码）。

    自动更新解压的 release zip 若文件名用本地代码页（GBK）且无 UTF-8 标志，
    Python zipfile 会按 CP437 解码成「╩╣╙├」类乱码。这里尝试逆映射还原
    为正确中文名（如 'CLI╩╣╙├' → 'CLI使用'）；非乱码名原样返回。
    """
    try:
        raw = text.encode("cp437")
        fixed = raw.decode("gbk")
        if fixed != text and any("\u4e00" <= c <= "\u9fff" for c in fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text


def _title_from_md(text: str, fallback: str) -> str:
    """从 markdown 提取一级标题（首个 `# `），无则回退文件名。"""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return fallback


def _read_safe(path: str) -> str | None:
    """读取 wiki 下的 .md 文档；路径穿越 / 不存在返回 None。"""
    root = _wiki_root()
    try:
        p = (root / path).resolve()
        if not str(p).startswith(str(root.resolve())):
            return None
        if p.suffix.lower() != ".md" or not p.is_file():
            return None
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


def docs_tree() -> list[dict]:
    """扫描 wiki 返回目录树：[{category, docs: [{name, path, title}]}]。"""
    root = _wiki_root()
    if not root.is_dir():
        return []
    tree: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        docs: list[dict] = []
        for f in sorted(d.glob("*.md")):
            rel = f.relative_to(root).as_posix()
            content = _read_safe(rel) or ""
            fixed_stem = _fix_mojibake(f.stem)
            docs.append({"name": fixed_stem, "path": rel,
                         "title": _title_from_md(content, fixed_stem)})
        if docs:
            # 先修复乱码目录名再查展示标签（保证 'CLI╩╣╙├' → 'CLI使用' → 'CLI 使用'）
            real_name = _fix_mojibake(d.name)
            tree.append({"category": _CATEGORY_LABELS.get(real_name, real_name),
                         "docs": docs})
    return tree


def get_doc(path: str) -> dict | None:
    """返回 {path, title, content}；路径非法 / 文档不存在返回 None。"""
    content = _read_safe(path)
    if content is None:
        return None
    return {"path": path, "title": _title_from_md(content, Path(path).stem), "content": content}

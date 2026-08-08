"""wiki_docs 测试：目录树 / 文档读取 / 路径穿越防护。"""
import wiki_docs


def test_docs_tree_returns_categories():
    """返回分类结构，每个分类含文档列表（标题 + 路径）。"""
    tree = wiki_docs.docs_tree()
    assert isinstance(tree, list)
    cats = {c["category"] for c in tree}
    assert "核心功能" in cats
    for c in tree:
        assert c["docs"]
        for d in c["docs"]:
            assert d["path"].endswith(".md")
            assert d["title"]


def test_get_doc_returns_content():
    """读取文档返回 markdown 原文与标题。"""
    path = None
    for c in wiki_docs.docs_tree():
        for d in c["docs"]:
            if d["name"] == "快速开始":
                path = d["path"]
                break
        if path:
            break
    assert path, "docs/wiki 应包含「快速开始/快速开始.md」"
    doc = wiki_docs.get_doc(path)
    assert doc is not None
    assert doc["content"]
    assert doc["title"]


def test_get_doc_rejects_path_traversal():
    """路径穿越（../）被拒绝，返回 None。"""
    assert wiki_docs.get_doc("../../README.md") is None
    assert wiki_docs.get_doc("..%2F..%2FREADME.md") is None


def test_get_doc_rejects_non_md():
    """非 .md 文件被拒绝。"""
    assert wiki_docs.get_doc("wiki-manifest.json") is None
    assert wiki_docs.get_doc("") is None


def test_fix_mojibake_restores_chinese_name():
    """GBK 文件名被按 CP437 解码的乱码可逆映射还原。"""
    assert wiki_docs._fix_mojibake("CLI╩╣╙├") == "CLI使用"
    assert wiki_docs._fix_mojibake("Web┐╪╓╞╠¿") == "Web控制台"
    assert wiki_docs._fix_mojibake("┐∞╦┘┐¬╩╝") == "快速开始"
    assert wiki_docs._fix_mojibake("║╦╨─╣ª─▄") == "核心功能"
    # 正常名/非中文名原样返回
    assert wiki_docs._fix_mojibake("核心功能") == "核心功能"
    assert wiki_docs._fix_mojibake("README.md") == "README.md"
    assert wiki_docs._fix_mojibake("") == ""


def test_docs_tree_fixes_mojibake_category(monkeypatch, tmp_path):
    """乱码目录/文件名在目录树中显示正确中文名，path 仍按磁盘真实名可读。"""
    root = tmp_path / "wiki"
    moji_dir = "CLI╩╣╙├"
    (root / moji_dir).mkdir(parents=True)
    (root / moji_dir / "CLI╩╣╙├.md").write_text("# CLI 使用\n正文内容", encoding="utf-8")
    monkeypatch.setattr(wiki_docs, "_wiki_root", lambda: root)
    tree = wiki_docs.docs_tree()
    assert len(tree) == 1
    cat = tree[0]
    # 分类名先修复乱码再映射展示标签
    assert cat["category"] == "CLI 使用"
    assert cat["docs"][0]["name"] == "CLI使用"
    assert cat["docs"][0]["title"] == "CLI 使用"
    # path 指向磁盘真实乱码名，能正常读取内容
    doc = wiki_docs.get_doc(cat["docs"][0]["path"])
    assert doc is not None
    assert "正文内容" in doc["content"]

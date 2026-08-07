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

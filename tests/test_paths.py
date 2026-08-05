"""paths 模块单元测试：统一运行时目录与旧文件迁移。"""
import json

import pytest

from paths import _migrate, base_dir, runtime_dir


def test_runtime_dir_is_data_folder():
    """runtime_dir 固定返回 <程序根>/data 且自动创建。"""
    d = runtime_dir()
    assert d.name == "data"
    assert d.is_dir()
    assert d == base_dir() / "data"


def test_migrate_moves_legacy_files(tmp_path):
    """旧版散落的运行文件（config/db/log/密钥）应迁移到 data/ 下。"""
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tavily_keys.db").write_bytes(b"db")
    (tmp_path / "tavily_keys.db-wal").write_bytes(b"wal")
    (tmp_path / "tavily.log").write_text("log", encoding="utf-8")
    (tmp_path / ".tavily-secret.key").write_bytes(b"key")
    # 非运行文件（如源码/keys.txt）不应被迁移
    (tmp_path / "keys.txt").write_text("tvly-xxx", encoding="utf-8")

    _migrate(tmp_path)

    data = tmp_path / "data"
    assert (data / "config.json").read_text(encoding="utf-8") == "{}"
    assert (data / "tavily_keys.db").read_bytes() == b"db"
    assert (data / "tavily_keys.db-wal").read_bytes() == b"wal"
    assert (data / "tavily.log").read_text(encoding="utf-8") == "log"
    assert (data / ".tavily-secret.key").read_bytes() == b"key"
    # 原位置已清空，且 keys.txt 原样保留
    assert not (tmp_path / "config.json").exists()
    assert (tmp_path / "keys.txt").exists()


def test_migrate_keeps_existing_target(tmp_path):
    """data/ 已存在同名文件时不应覆盖（以既有文件为准）。"""
    (tmp_path / "config.json").write_text("old", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text("new", encoding="utf-8")

    _migrate(tmp_path)

    assert (tmp_path / "data" / "config.json").read_text(encoding="utf-8") == "new"


def test_migrate_merges_config_json(tmp_path):
    """data/ 已存在 config.json 时按源键合并（源键优先，避免升级后丢失新配置项）。"""
    (tmp_path / "config.json").write_text('{"port": 8000, "new_key": 1}', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text('{"port": 9000, "old_key": 2}', encoding="utf-8")

    _migrate(tmp_path)

    merged = json.loads((tmp_path / "data" / "config.json").read_text(encoding="utf-8"))
    assert merged == {"port": 8000, "new_key": 1, "old_key": 2}
    assert not (tmp_path / "config.json").exists()  # 源已移除


def test_migrate_config_invalid_json_keeps_both(tmp_path):
    """config.json 合并失败（非法 JSON）时两份都保留，绝不丢数据。"""
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text('{"port": 9000}', encoding="utf-8")

    _migrate(tmp_path)

    assert (tmp_path / "data" / "config.json").read_text(encoding="utf-8") == '{"port": 9000}'
    assert (tmp_path / "config.json").exists()  # 源保留


def test_migrate_db_keeps_existing_target_removes_source(tmp_path):
    """db 等非配置文件目标已存在时：保留 data/ 版本并移除源（数据收敛到 data/）。"""
    (tmp_path / "tavily_keys.db").write_bytes(b"src")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tavily_keys.db").write_bytes(b"dst")

    _migrate(tmp_path)

    assert (tmp_path / "data" / "tavily_keys.db").read_bytes() == b"dst"
    assert not (tmp_path / "tavily_keys.db").exists()


def test_migrate_missing_src_is_noop(tmp_path):
    """源文件都不存在时迁移不报错。"""
    _migrate(tmp_path)  # 不应抛异常
    assert (tmp_path / "data").is_dir()

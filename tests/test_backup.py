"""app/backup.py 备份/恢复单元测试。"""
import zipfile

import pytest

import backup


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """隔离的运行时 data/ 目录（backup.runtime_dir 指向临时目录）。"""
    monkeypatch.setattr(backup, "runtime_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text('{"k": 1}', encoding="utf-8")
    (tmp_path / "tavily_keys.db").write_bytes(b"db-bytes")
    (tmp_path / "tavily_keys.db-wal").write_bytes(b"wal-bytes")
    (tmp_path / ".tavily-secret.key").write_bytes(b"secret")
    (tmp_path / "research_keys.json").write_text("{}", encoding="utf-8")
    (tmp_path / "research_tasks_cache.json").write_text("{}", encoding="utf-8")
    (tmp_path / "unrelated.log").write_text("log", encoding="utf-8")
    return tmp_path


def test_backup_zip_contains_backup_set_only(data_dir, tmp_path):
    dest = backup.backup_to(tmp_path / "out.zip")
    assert dest.name == "out.zip"
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "config.json" in names
    assert "tavily_keys.db" in names
    assert "tavily_keys.db-wal" in names
    assert ".tavily-secret.key" in names
    assert "unrelated.log" not in names  # 日志不备份


def test_backup_to_dir_generates_timestamped(data_dir, tmp_path):
    dest = backup.backup_to(tmp_path / "bk")
    assert dest.parent == tmp_path / "bk"
    assert dest.name.startswith("tavily-backup-")
    assert dest.suffix == ".zip"


def test_restore_roundtrip_preserves_old(data_dir, tmp_path):
    """恢复后文件回到备份内容，且现有文件保留 .pre-restore 副本。"""
    dest = backup.backup_to(tmp_path / "bk.zip")
    # 篡改现有数据，模拟损坏/丢失
    (data_dir / "config.json").write_text('{"k": 9}', encoding="utf-8")
    (data_dir / "tavily_keys.db").write_bytes(b"tampered")
    n = backup.restore_from(dest)
    assert n >= 3
    assert (data_dir / "config.json").read_text(encoding="utf-8") == '{"k": 1}'
    assert (data_dir / "tavily_keys.db").read_bytes() == b"db-bytes"
    pres = [p for p in data_dir.iterdir() if ".pre-restore-" in p.name]
    assert pres, "恢复前应保留现有文件副本"


def test_restore_rejects_incomplete_zip(data_dir, tmp_path):
    """缺必需文件（如无 .tavily-secret.key）的 zip 直接拒绝，不写盘。"""
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr("tavily_keys.db", "db")
    with pytest.raises(ValueError):
        backup.restore_from(bad)
    # 现有数据未被破坏
    assert (data_dir / "config.json").read_text(encoding="utf-8") == '{"k": 1}'
    assert (data_dir / "tavily_keys.db").read_bytes() == b"db-bytes"


def test_restore_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.restore_from(tmp_path / "nope.zip")

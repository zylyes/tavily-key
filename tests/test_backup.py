"""app/backup.py 备份/恢复单元测试。"""
import re
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
    """缺必需文件（如无 tavily_keys.db）的 zip 直接拒绝，不写盘。"""
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("config.json", "{}")
    with pytest.raises(ValueError, match=r"tavily_keys\.db"):
        backup.restore_from(bad)
    # 现有数据未被破坏
    assert (data_dir / "config.json").read_text(encoding="utf-8") == '{"k": 1}'
    assert (data_dir / "tavily_keys.db").read_bytes() == b"db-bytes"


def test_restore_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.restore_from(tmp_path / "nope.zip")


@pytest.mark.parametrize(
    "present, missing",
    [
        (["tavily_keys.db"], "config.json"),
        (["config.json"], "tavily_keys.db"),
    ],
)
def test_backup_missing_required_file_raises(tmp_path, monkeypatch, present, missing):
    """缺少必需文件（config.json / tavily_keys.db）时抛清晰异常，且不产出 zip。"""
    monkeypatch.setattr(backup, "runtime_dir", lambda: tmp_path)
    for name in present:
        (tmp_path / name).write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match=re.escape(missing)):
        backup.backup_to(tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()


def test_backup_restore_roundtrip_without_secret(data_dir, tmp_path):
    """无 .tavily-secret.key（Windows DPAPI / 无加密后端）时备份→恢复闭环成功。"""
    (data_dir / ".tavily-secret.key").unlink()
    dest = backup.backup_to(tmp_path / "bk.zip")
    with zipfile.ZipFile(dest) as zf:
        assert ".tavily-secret.key" not in zf.namelist()
    # 篡改现有数据后从无 secret 备份恢复
    (data_dir / "config.json").write_text('{"k": 9}', encoding="utf-8")
    (data_dir / "tavily_keys.db").write_bytes(b"tampered")
    n = backup.restore_from(dest)
    assert n >= 2
    assert (data_dir / "config.json").read_text(encoding="utf-8") == '{"k": 1}'
    assert (data_dir / "tavily_keys.db").read_bytes() == b"db-bytes"


def test_backup_with_secret_includes_secret(data_dir, tmp_path):
    """Fernet 环境已有 .tavily-secret.key 时仍打包进备份。"""
    dest = backup.backup_to(tmp_path / "bk.zip")
    with zipfile.ZipFile(dest) as zf:
        assert ".tavily-secret.key" in zf.namelist()
        assert zf.read(".tavily-secret.key") == b"secret"


def test_backup_removes_invalid_zip_on_post_validation(tmp_path, monkeypatch):
    """生成后校验失败（zip 损坏）时抛异常并删除残留 zip，不留残次备份。"""
    monkeypatch.setattr(backup, "runtime_dir", lambda: tmp_path)
    for name in ("config.json", "tavily_keys.db", "tavily_keys.db-wal",
                 ".tavily-secret.key"):
        (tmp_path / name).write_bytes(b"x")
    calls = {"n": 0}
    real_zipfile = zipfile.ZipFile

    def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:  # 第二次打开（生成后校验）模拟 zip 损坏
            raise zipfile.BadZipFile("corrupt")
        return real_zipfile(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", _flaky)
    with pytest.raises(ValueError, match="校验失败"):
        backup.backup_to(tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()

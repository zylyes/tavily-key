"""data/ 目录备份与恢复。

运行时数据统一集中到 runtime_dir()/data（v0.3.0 起，见 app/paths.py），
备份 = 把关键文件打包为 zip。备份集：
  - config.json                  （配置）
  - tavily_keys.db / -wal        （Key 池与请求日志；WAL 模式下需连 wal 一起）
  - research_keys.json           （research 任务 → key 映射，任务按 key 隔离）
  - research_tasks_cache.json    （research 任务看板终态缓存）
  - .tavily-secret.key           （Fernet 加密密钥；条件性：存在才打包。Windows DPAPI /
                                   无加密后端下不存在，属于正常情况）

注意：
  - 不备份 *-shm（sqlite WAL 的共享内存索引，可由 db+wal 重建，恢复时删除旧的）。
  - 不备份 *.log（可再生）。
  - 恢复前把现有同名文件改名为 <name>.pre-restore-<ts>，绝不静默覆盖丢数据。
  - 只解压备份集白名单内的条目，防路径穿越 / zip 炸弹。
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

from logging_setup import get_logger
from paths import runtime_dir

_log = get_logger("backup")

# 备份集（zip 内条目名 = data/ 下文件名）
_BACKUP_FILES = (
    "config.json",
    "tavily_keys.db",
    "tavily_keys.db-wal",
    "research_keys.json",
    "research_tasks_cache.json",
    ".tavily-secret.key",
)
# 恢复时必需的文件（缺任一视为备份不完整，直接拒绝）。
# .tavily-secret.key 是条件性条目：Windows DPAPI / 无加密后端下不存在，不能作为必需项。
_REQUIRED = {"config.json", "tavily_keys.db"}


def backup_to(target: str | Path | None = None) -> Path:
    """把 data/ 关键文件打包为 zip，返回生成的 zip 路径。

    - target 为空：写入系统临时目录（tavily-backup-<时间戳>.zip）。
    - target 以 .zip 结尾：直接写入该文件。
    - target 是目录：生成 <target>/tavily-backup-<时间戳>.zip。

    失败即抛异常、不产出 zip：
    - 必需文件（config.json / tavily_keys.db）缺失 → FileNotFoundError，
      避免生成无法恢复的残缺备份；
    - 生成后校验（zip 可正常打开、含全部必需条目且条目完整）失败 →
      ValueError，并删除残留 zip。

    异常与日志只含文件名/数量/路径，不包含任何文件内容（密钥等机密）。
    """
    data_dir = runtime_dir()
    # 写入前校验：必需文件缺失时直接失败，不生成残缺备份
    missing = _REQUIRED - {n for n in _REQUIRED if (data_dir / n).is_file()}
    if missing:
        raise FileNotFoundError(
            f"备份数据不完整，缺少必需文件: {', '.join(sorted(missing))}"
        )
    if target is None:
        import tempfile

        dest = Path(tempfile.gettempdir()) / time.strftime("tavily-backup-%Y%m%d-%H%M%S.zip")
    else:
        p = Path(target)
        if p.suffix.lower() == ".zip":
            dest = p
        else:
            p.mkdir(parents=True, exist_ok=True)
            dest = p / time.strftime("tavily-backup-%Y%m%d-%H%M%S.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _BACKUP_FILES:
            src = data_dir / name
            if src.exists() and src.is_file():
                zf.write(src, arcname=name)
                written += 1
    if written == 0:
        _log.warning("备份为空：data/ 下无任何备份集文件（%s）", data_dir)
    # 生成后校验：zip 可正常打开、含全部必需条目且条目完整（CRC 校验），
    # 防止写入中断/磁盘问题留下坏备份；失败时删除残留 zip 并抛异常。
    try:
        with zipfile.ZipFile(dest) as zf:
            names = set(zf.namelist())
            missing = _REQUIRED - names
            if missing:
                raise ValueError(
                    f"备份文件生成后校验失败，缺少必需条目: {', '.join(sorted(missing))}"
                )
            bad = zf.testzip()
            if bad is not None:
                raise ValueError(f"备份文件生成后校验失败，条目损坏: {bad}")
    except ValueError:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    except Exception as e:  # noqa: BLE001 —— zip 打不开 / 读取异常
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValueError(f"备份文件生成后校验失败: {e}") from e
    _log.info("备份完成: %s（%d 个文件）", dest, written)
    return dest


def restore_from(zip_path: str | Path) -> int:
    """从备份 zip 恢复 data/ 文件，返回恢复的文件数。

    安全策略：
    - 校验 zip 含必需文件（config.json / tavily_keys.db），缺任一直接拒绝
      （避免半成品备份破坏现有数据）；.tavily-secret.key 为条件性条目，
      备份中没有（DPAPI / 无加密后端）时不影响恢复。
    - 只解压白名单条目，防路径穿越 / 恶意条目。
    - 恢复前把现有同名文件改名为 <name>.pre-restore-<ts>，避免覆盖丢数据。
    - 删除旧的 -shm（WAL 共享索引可重建），避免与恢复的 db/wal 不一致。
    """
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(f"备份文件不存在: {zp}")
    data_dir = runtime_dir()
    ts = time.strftime("%Y%m%d-%H%M%S")
    with zipfile.ZipFile(zp) as zf:
        names = {n for n in zf.namelist() if n in _BACKUP_FILES}
        missing = _REQUIRED - names
        if missing:
            raise ValueError(f"备份文件不完整，缺少必需文件: {', '.join(sorted(missing))}")
        # 先做校验（含 zip 完整性与条目可读性），再动手写盘
        payload = {}
        for name in sorted(names):
            try:
                payload[name] = zf.read(name)
            except zipfile.BadZipFile as e:  # noqa: BLE001
                raise ValueError(f"备份文件损坏: {name}: {e}") from e
        data_dir.mkdir(parents=True, exist_ok=True)
        # 删除 WAL 共享内存索引，避免与恢复的 db/wal 不一致
        for stale in ("tavily_keys.db-shm", "tavily_keys.db-wal"):
            sp = data_dir / stale
            if sp.exists() and stale not in payload:
                try:
                    sp.unlink()
                except OSError:  # noqa: BLE001
                    _log.warning("删除旧 %s 失败（可能被占用）", stale)
        restored = 0
        for name, content in payload.items():
            cur = data_dir / name
            if cur.exists():
                backup = cur.with_name(f"{name}.pre-restore-{ts}")
                try:
                    cur.rename(backup)
                except OSError as e:  # noqa: BLE001
                    raise OSError(
                        f"恢复前备份现有 {name} 失败（文件可能被占用，请先停止服务）: {e}"
                    ) from e
            (data_dir / name).write_bytes(content)
            restored += 1
    _log.info("恢复 %d 个文件，来源 %s", restored, zp)
    return restored

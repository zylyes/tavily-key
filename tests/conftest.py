import sys
from pathlib import Path

import pytest

# 让测试能 import app/ 下的模块
APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


@pytest.fixture(autouse=True)
def _isolate_cache_signal(tmp_path):
    """隔离跨进程失效信号文件到临时目录，避免测试写真实 data/cache_invalidate.sig。

    KeyPool 写操作会 emit_invalidate() 广播失效信号（默认 runtime_dir()），
    若测试触发会污染真实运行目录；这里统一重定向到每个测试的临时目录。
    """
    from cache import set_signal_file

    set_signal_file(tmp_path / "test-cache-invalidate.sig")
    yield
    set_signal_file(None)

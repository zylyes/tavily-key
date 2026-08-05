import sys
from pathlib import Path

# 让测试能 import app/ 下的模块
APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

"""Windows 开机自启（当前用户注册表 Run 键）。

仅 Windows 生效；其他平台 is_enabled() 返回 False、set_enabled() 为空操作
（保证 Linux server 部署可导入）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TavilyKeyPool"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> str:
    """开机自启命令：打包后为 exe 路径；开发模式为 python + dashboard.py。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    py = sys.executable
    script = Path(__file__).resolve().parent / "dashboard.py"
    return f'"{py}" "{script}"'


def is_enabled() -> bool:
    """查询当前用户 Run 键中是否存在本应用自启项。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """设置开机自启；返回操作后的实际状态。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        if enabled:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
                ) as k:
                    winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
    except OSError:
        pass
    return is_enabled()

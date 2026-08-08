"""Windows 系统托盘图标（纯 ctypes 实现，无第三方依赖）。

在专用后台线程创建隐藏消息窗口，接收 Shell_NotifyIcon 回调：
  - 左键单击 / 双击  -> on_show（恢复主窗口）
  - 右键菜单          -> 「显示主窗口 / 退出」（on_show / on_exit）

仅 Windows 生效；其他平台 start() 为空操作（保证 Linux server 部署可导入）。
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _shell32 = ctypes.windll.shell32

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    # ── Win32 函数签名（64 位下句柄/指针必须显式声明 argtypes，
    #    否则 ctypes 默认按 c_int 处理 64 位句柄会溢出/截断）───
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]

    _user32.LoadImageW.restype = wintypes.HICON
    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]

    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_uint, wintypes.LPCWSTR]
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.TrackPopupMenu.restype = ctypes.c_int
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
    ]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

else:
    _user32 = _kernel32 = _shell32 = None
    WNDPROC = None
    NOTIFYICONDATAW = None
    WNDCLASSW = None

# ── Win32 常量 ────────────────────────────────────────────────
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
# 托盘气泡（balloon）被点击：Shell_NotifyIcon 回调 lParam 的低位为 WM_APP+19
NIN_BALLOONUSERCLICK = 0x0400 + 19

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RETURNCMD = 0x00000100
ID_SHOW = 1
ID_EXIT = 2

_CLASS_NAME = "TavilyTrayHostWindow"


class TrayIcon:
    """系统托盘图标。

    参数:
        icon_path: 应用图标(.ico)路径，与窗口图标统一
        on_show:   恢复主窗口的回调（在托盘线程内执行）
        on_exit:   退出应用的回调（在托盘线程内执行）
    """

    def __init__(self, icon_path, on_show=None, on_exit=None, items=None,
                 on_balloon_click=None):
        self._icon_path = str(icon_path)
        self._on_show = on_show or (lambda: None)
        self._on_exit = on_exit or (lambda: None)
        # 托盘气泡（系统通知）被点击：打开主窗口并展示更新公告等
        self._on_balloon_click = on_balloon_click or (lambda: None)
        # 自定义菜单项：[(item_id, label, callback)]，item_id 从 1000 起（避开
        # ID_SHOW/ID_EXIT）。回调在托盘线程内执行，长耗时操作请自行开线程。
        self._items = list(items or [])
        self._item_handlers: dict[int, Callable] = {}
        for item_id, _label, cb in self._items:
            self._item_handlers[int(item_id)] = cb
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._nid = None
        self._wndproc = WNDPROC(self._wnd_proc) if WNDPROC is not None else None
        self._started = threading.Event()

    # ── 生命周期 ─────────────────────────────────────────────
    def start(self) -> None:
        """后台线程启动托盘；非 Windows / 图标缺失时为空操作。"""
        if os.name != "nt" or not _user32:
            return
        if not os.path.exists(self._icon_path):
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tavily-tray"
        )
        self._thread.start()

    def stop(self) -> None:
        """移除托盘图标并结束线程（应用退出时调用）。"""
        if os.name != "nt" or self._thread is None:
            return
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
        t = self._thread
        if t is not threading.current_thread():
            t.join(timeout=2)
        self._thread = None

    def notify(self, title: str, message: str, icon: int = NIIF_INFO) -> None:
        """显示托盘气泡通知（NIF_INFO 气球提示）。未运行/非 Windows 时为空操作。

        icon: NIIF_NONE / NIIF_INFO / NIIF_WARNING / NIIF_ERROR。
        可从任意线程调用（Shell_NotifyIconW 本身线程安全）。
        """
        if os.name != "nt" or self._nid is None or not self._hwnd:
            return
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_INFO
            nid.szInfoTitle = str(title)[:63]
            nid.szInfo = str(message)[:255]
            nid.dwInfoFlags = icon
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:  # noqa: BLE001
            pass

    # ── 内部实现 ─────────────────────────────────────────────
    def _run(self) -> None:
        try:
            hinst = _kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p).value
            wc.hInstance = hinst
            wc.lpszClassName = _CLASS_NAME
            _user32.RegisterClassW(ctypes.byref(wc))  # 重复注册时失败可忽略

            hwnd = _user32.CreateWindowExW(
                0, _CLASS_NAME, "Tavily", 0, 0, 0, 0, 0,
                None, None, hinst, None,
            )
            if not hwnd:
                return
            self._hwnd = hwnd
            if not self._add_icon(hwnd):
                print("[tavily] 系统托盘图标注册失败", flush=True)
                return
            # 图标注册完成后再置位，保证外部 wait() 返回时托盘已就绪
            self._started.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._cleanup()

    def _add_icon(self, hwnd) -> bool:
        """注册托盘图标；图标加载失败时返回 False。"""
        try:
            h_icon = _user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE
            )
            if not h_icon:
                h_icon = _user32.LoadImageW(
                    None, self._icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE
                )
            if not h_icon:
                return False
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = h_icon
            nid.szTip = "Tavily Key Pool"
            self._nid = nid
            _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            return True
        except Exception:
            return False

    def _cleanup(self) -> None:
        try:
            if self._nid is not None:
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                self._nid = None
            if self._hwnd:
                _user32.DestroyWindow(self._hwnd)
                self._hwnd = 0
        except Exception:
            pass

    # ── 回调 / 菜单 ──────────────────────────────────────────
    def _wnd_proc(self, hwnd, msg, wp, lp) -> int:
        try:
            if msg == WM_TRAY:
                code = lp & 0xFFFF
                if code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._safe(self._on_show)
                elif code == NIN_BALLOONUSERCLICK:
                    self._safe(self._on_balloon_click)
                elif code == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
            elif msg == WM_COMMAND:
                cmd = wp & 0xFFFF
                if cmd == ID_SHOW:
                    self._safe(self._on_show)
                elif cmd == ID_EXIT:
                    self._safe(self._on_exit)
                elif cmd in self._item_handlers:
                    self._safe(self._item_handlers[cmd])
            elif msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _popup_menu(self, hwnd) -> None:
        try:
            hmenu = _user32.CreatePopupMenu()
            _user32.AppendMenuW(hmenu, MF_STRING, ID_SHOW, "显示主窗口")
            if self._items:
                _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                for item_id, label, _cb in self._items:
                    _user32.AppendMenuW(hmenu, MF_STRING, item_id, label)
            _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "退出")
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            _user32.SetForegroundWindow(hwnd)
            cmd = _user32.TrackPopupMenu(
                hmenu, TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None
            )
            _user32.DestroyMenu(hmenu)
            if cmd == ID_SHOW:
                self._safe(self._on_show)
            elif cmd == ID_EXIT:
                self._safe(self._on_exit)
            elif cmd in self._item_handlers:
                self._safe(self._item_handlers[cmd])
        except Exception:
            pass

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass

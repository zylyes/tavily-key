"""
API Key 加密存储 — 对称加密，支持 Windows DPAPI 与跨平台 Fernet。

存储格式带前缀标记，便于识别与迁移：
  - d1:<base64>  Windows DPAPI 密文（CryptProtectData，当前用户作用域）
  - f1:<base64>  跨平台 Fernet 密文（密钥文件 .tavily-secret.key，0600）
  - （无前缀）    旧版本明文，读取时触发就地迁移

若当前平台无可用后端（非 Windows 且未安装 cryptography），encrypt_text
原样返回明文并可通过 available() 查询；调用方应记录警告。
"""
from __future__ import annotations

import base64
import ctypes
import os
import threading
from pathlib import Path

from paths import runtime_dir

PREFIX_DPAPI = "d1:"
PREFIX_FERNET = "f1:"


class _DPAPI:
    """Windows 系统级加密（CryptProtectData，当前用户作用域，无需额外密钥）。"""

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("DPAPI only available on Windows")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32
        # 64 位下必须显式声明 argtypes/restype，否则指针参数按 32 位截断
        # （与 tray.py 中 Win32 调用的踩坑一致）
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_int
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(self, data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        blob = self._DATA_BLOB()
        blob.cbData = len(data)
        blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        return blob

    def encrypt(self, data: bytes) -> bytes:
        blob_in = self._blob(data)
        blob_out = self._DATA_BLOB()
        if not self._crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            if blob_out.pbData:
                self._kernel32.LocalFree(blob_out.pbData)

    def decrypt(self, data: bytes) -> bytes:
        blob_in = self._blob(data)
        blob_out = self._DATA_BLOB()
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            if blob_out.pbData:
                self._kernel32.LocalFree(blob_out.pbData)


class _FernetCipher:
    """跨平台加密（cryptography.fernet），密钥文件持久化在本机。"""

    def __init__(self):
        from cryptography.fernet import Fernet  # 惰性导入

        self._fernet = Fernet(self._load_or_create_key())

    def _key_path(self) -> Path:
        return runtime_dir() / ".tavily-secret.key"

    def _load_or_create_key(self) -> bytes:
        p = self._key_path()
        if p.exists():
            return p.read_bytes()
        key = Fernet.generate_key()
        p.write_bytes(key)
        try:
            os.chmod(p, 0o600)  # 仅当前用户可读
        except OSError:
            pass
        return key

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._fernet.decrypt(data)


_backend = None
_backend_lock = threading.Lock()


def _get_backend():
    """惰性初始化加密后端：Windows 优先 DPAPI，其次 Fernet；均不可用时为 None。"""
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                if os.name == "nt":
                    try:
                        _backend = _DPAPI()
                    except Exception:  # noqa: BLE001
                        _backend = None
                if _backend is None:
                    try:
                        _backend = _FernetCipher()
                    except Exception:  # noqa: BLE001
                        _backend = None
    return _backend


def available() -> bool:
    """当前平台是否存在可用的加密后端。"""
    return _get_backend() is not None


def encrypt_text(plain: str) -> str:
    """加密字符串；无可用后端时原样返回（由调用方决定是否降级/警告）。"""
    if not plain:
        return plain
    backend = _get_backend()
    if backend is None:
        return plain
    payload = backend.encrypt(plain.encode("utf-8"))
    b64 = base64.b64encode(payload).decode("ascii")
    if isinstance(backend, _DPAPI):
        return PREFIX_DPAPI + b64
    return PREFIX_FERNET + b64


def decrypt_text(stored: str) -> str:
    """解密存储值；无前缀（旧明文）或后端不匹配时原样返回。"""
    if not stored:
        return stored
    backend = _get_backend()
    if backend is None:
        return stored
    if stored.startswith(PREFIX_DPAPI) and isinstance(backend, _DPAPI):
        raw = base64.b64decode(stored[len(PREFIX_DPAPI):])
        return backend.decrypt(raw).decode("utf-8")
    if stored.startswith(PREFIX_FERNET) and isinstance(backend, _FernetCipher):
        raw = base64.b64decode(stored[len(PREFIX_FERNET):])
        return backend.decrypt(raw).decode("utf-8")
    return stored


def is_ciphertext(stored: str) -> bool:
    """是否为已加密存储值（带前缀）。"""
    return stored.startswith((PREFIX_DPAPI, PREFIX_FERNET))

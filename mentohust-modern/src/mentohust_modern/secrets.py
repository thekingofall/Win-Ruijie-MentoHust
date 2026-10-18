"""Windows DPAPI storage; machine scope is reserved for the protected boot runtime."""
from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import os

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

def _transform(data: bytes, decrypt: bool, *, machine: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("密码保存需要 Windows 当前用户的 DPAPI。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    result = DATA_BLOB()
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1 | (4 if machine and not decrypt else 0), ctypes.byref(result)):
        raise RuntimeError("Windows 无法读取或保护密码，请在当前用户下重新输入。")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(result.pbData)

def protect(value: str, *, machine: bool = False) -> str:
    return base64.b64encode(_transform(value.encode("utf-8"), False, machine=machine)).decode("ascii") if value else ""

def unprotect(value: str) -> str:
    return _transform(base64.b64decode(value, validate=True), True).decode("utf-8") if value else ""

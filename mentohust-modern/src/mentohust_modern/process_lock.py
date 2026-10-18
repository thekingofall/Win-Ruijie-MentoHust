"""Cross-process exclusion for authentication and installation."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes

@contextmanager
def exclusive(name):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:
        raise RuntimeError("无法检查正在运行的连接或设置操作。")
    acquired=False
    try:
        acquired=kernel.WaitForSingleObject(handle,0) in (0,0x80)
        if not acquired:
            raise RuntimeError("已有新锐捷正在连接或更新设置，请稍后重试。")
        yield
    finally:
        if acquired: kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

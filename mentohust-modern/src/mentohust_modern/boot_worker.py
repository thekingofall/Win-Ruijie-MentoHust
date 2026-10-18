"""SYSTEM entry points bundled in the same executable as the desktop app."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import time

from . import APP_VERSION
from .boot import runtime_root
from .config import MentohustConfig


def write_json(path, data):
    temporary = path.with_suffix(".writing")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def preflight():
    from scapy.all import conf
    from .interfaces import find_interface
    root = runtime_root()
    result = {"ok": False, "version": APP_VERSION}
    try:
        config = MentohustConfig.load_json(root / "candidate.json")
        if not config.username or not config.password:
            raise ValueError("Missing credentials")
        interface = find_interface(config.interface_id)
        if not interface or not interface.is_wired:
            raise ValueError("Missing wired interface")
        conf.use_pcap = True
        channel = conf.L2socket(iface=interface.network_name)
        channel.close()
        result.update(ok=True, credentials_decrypted=True, packet_driver_opened=True)
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    write_json(root / "preflight.json", result)


def retry_delay(failures):
    return min(300, 15 * 2 ** min(max(failures - 1, 0), 5))


def run():
    from .client import MentohustClient
    from .interfaces import adapter_metadata
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Global\\XinRuijieBootAuthenticationV1")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return
    root = runtime_root()
    logger = logging.getLogger("xinruijie.boot")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(root / "connection.log", maxBytes=1048576, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    client = None
    state = "等待网卡"
    failures = 0
    next_attempt = 0.0
    next_link_check = 0.0
    link = None
    def on_state(value):
        nonlocal state
        state = value
        logger.info("state=%s", value)
    try:
        config = MentohustConfig.load_json(root / "profile.json")
        while not (root / "stop.request").exists():
            now = time.monotonic()
            if now >= next_link_check:
                metadata = adapter_metadata().get(config.interface_id.lower(), {})
                link = metadata.get("status")
                next_link_check = now + 30
            if link in ("Disconnected", "Disabled", "Not Present"):
                if client:
                    client.stop()
                    client = None
                state = "等待网线"
            elif client is None and now >= next_attempt:
                client = MentohustClient(config, log_callback=logger.info, state_callback=on_state)
                client.start()
            if client and client.thread and not client.thread.is_alive():
                client.stop()
                client = None
                failures += 1
                next_attempt = time.monotonic() + retry_delay(failures)
                state = "等待重连"
            if client and client.authenticated:
                failures = 0
            try:
                write_json(root / "status.json", {"version": APP_VERSION, "pid": os.getpid(),
                    "updated_at": time.time(), "state": state,
                    "authenticated": bool(client and client.authenticated), "failures": failures})
            except OSError:
                logger.warning("Status write deferred")
            time.sleep(2)
    except Exception as exc:
        logger.error("Worker failure: %s", type(exc).__name__)
        raise
    finally:
        if client:
            client.stop()
        write_json(root / "status.json", {"version": APP_VERSION, "pid": os.getpid(),
            "updated_at": time.time(), "state": "已停止", "authenticated": False})
        handler.close()
        logger.removeHandler(handler)
        kernel.CloseHandle(handle)

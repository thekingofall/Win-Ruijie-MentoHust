from __future__ import annotations

from pathlib import Path
import os
import sys


APP_DIR_NAME = "新锐捷"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return repo_root()


def app_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / APP_DIR_NAME


def logs_dir() -> Path:
    return app_dir() / "logs"


def profiles_dir() -> Path:
    return app_dir() / "profiles"


def default_profile_path() -> Path:
    return profiles_dir() / "default.json"


def default_log_path() -> Path:
    return logs_dir() / "xin-ruijie.log"


def ensure_app_dirs() -> None:
    for path in (app_dir(), logs_dir(), profiles_dir()):
        path.mkdir(parents=True, exist_ok=True)


def bundled_client_exe() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ruijie Networks" / "Ruijie Supplicant" / "8021x.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Ruijie Networks" / "Ruijie Supplicant" / "8021x.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def asset_path(name: str) -> Path:
    candidates = [
        runtime_root() / "mentohust_modern" / "assets" / name,
        runtime_root() / "assets" / name,
        Path(__file__).resolve().parent / "assets" / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]

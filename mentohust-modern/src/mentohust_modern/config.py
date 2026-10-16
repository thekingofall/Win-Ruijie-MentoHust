from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
import json
import socket
import os
from .secrets import protect, unprotect

from .app_paths import bundled_client_exe


def default_client_exe() -> str:
    bundled = bundled_client_exe()
    return str(bundled) if bundled is not None else ""


@dataclass(slots=True)
class MentohustConfig:
    enable: bool = True
    auto_connect: bool = False
    username: str = ""
    password: str = field(default="", repr=False)
    remember_password: bool = False
    interface_description: str = ""
    interface_id: str = ""
    ipaddr: str = "0.0.0.0"
    gateway: str = "0.0.0.0"
    mask: str = "0.0.0.0"
    ping: str = "0.0.0.0"
    timeout: int = 8
    interval: int = 30
    wait: int = 15
    fail_number: int = 3
    multicast_address: int = 1
    dhcp_mode: int = 2
    dhcp_script: str = ""  # legacy input ignored; DHCP is GUID scoped
    version: str = "5.00"
    dns: str = "0.0.0.0"
    client_exe: str = default_client_exe()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MentohustConfig":
        if not isinstance(payload, dict):
            raise ValueError("配置文件应为 JSON 对象。")
        names = {item.name for item in fields(cls)}
        data = {key: value for key, value in payload.items() if key in names}
        for key in ("enable", "auto_connect", "remember_password"):
            if key in data and not isinstance(data[key], bool):
                raise ValueError("开关配置必须为 true 或 false。")
        for key in ("timeout", "interval", "wait", "fail_number", "multicast_address", "dhcp_mode"):
            if key in data:
                data[key] = int(data[key])
        data["dhcp_script"] = ""
        return cls(**data)

    def save_json(self, path: str | Path, *, export: bool = False) -> None:
        data = self.to_dict()
        data.pop("password", None)
        data.pop("dhcp_script", None)
        data["password_dpapi"] = protect(self.password) if self.remember_password and not export else ""
        if export:
            data["username"] = ""
            data["remember_password"] = False
            data["auto_connect"] = False
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".writing")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)

    @classmethod
    def load_json(cls, path: str | Path) -> "MentohustConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置文件应为 JSON 对象。")
        payload.pop("password", None)
        encrypted = payload.pop("password_dpapi", "")
        result = cls.from_dict(payload)
        result.password = unprotect(encrypted) if encrypted else ""
        return result

    def client_exe_path(self) -> Path:
        return Path(self.client_exe)

    def resolved_ping_host(self) -> str:
        value = self.ping.strip()
        if not value or value == "0.0.0.0":
            return "0.0.0.0"
        try:
            socket.inet_aton(value)
            return value
        except OSError:
            return socket.gethostbyname(value)

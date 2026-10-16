from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import subprocess
import uuid

from scapy.all import conf
from scapy.arch.windows import get_windows_if_list

from .windows import hidden_subprocess_kwargs


@dataclass(slots=True)
class WindowsInterface:
    index: int
    name: str
    description: str
    guid: str
    network_name: str
    mac: str
    ipv4: str
    nameservers: list[str]
    is_wired: bool = False
    status: str = "Unknown"

    @property
    def label(self) -> str:
        status = {"Up": "已插线", "Disconnected": "未插线", "Disabled": "已禁用"}.get(self.status, self.status)
        return f"{self.name} · {self.description} · {self.mac} · {status} · {self.guid}"


@dataclass(slots=True)
class InterfaceNetworkDetails:
    ipv4: str
    mask: str
    gateway: str
    dns: str


def _first_ipv4(values: list[str]) -> str:
    for item in values:
        try:
            parsed = ipaddress.ip_address(item)
        except ValueError:
            continue
        if parsed.version == 4:
            return str(parsed)
    return "0.0.0.0"


def _prefix_to_mask(prefix_length: int) -> str:
    network = ipaddress.IPv4Network(f"0.0.0.0/{prefix_length}")
    return str(network.netmask)


def list_interfaces() -> list[WindowsInterface]:
    conf.use_pcap = True
    conf.ifaces.reload()
    raw_interfaces = get_windows_if_list()
    pcap_map = {
        getattr(iface, "guid", "").lower(): getattr(iface, "network_name", str(iface))
        for iface in conf.ifaces.values()
    }
    details = adapter_metadata()
    results: list[WindowsInterface] = []
    for item in raw_interfaces:
        description = item.get("description", "")
        if not description or "Npcap Packet Driver" in description or "WFP " in description:
            continue
        mac = item.get("mac", "")
        network_name = pcap_map.get(item.get("guid", "").lower(), "")
        if not mac or not network_name:
            continue
        results.append(
            WindowsInterface(
                index=int(item["index"]),
                name=item.get("name", ""),
                description=description,
                guid=item.get("guid", ""),
                network_name=network_name,
                mac=mac,
                ipv4=_first_ipv4(item.get("ips", [])),
                nameservers=[value for value in item.get("nameservers", []) if ":" not in value],
                is_wired=details.get(item.get("guid", "").lower(), {}).get("wired", False),
                status=details.get(item.get("guid", "").lower(), {}).get("status", "Unknown"),
            )
        )
    return results


def find_interface(interface_id: str = "", description: str = "") -> WindowsInterface | None:
    # Explicit stable identity is mandatory; descriptions can collide or change.
    normalized = interface_id.strip().lower()
    if not normalized:
        return None
    return next((item for item in list_interfaces()
                 if normalized in (item.guid.lower(), item.network_name.lower())), None)


def _powershell(script: str, *, timeout: int = 20) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; " + script],
        check=False, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
        **hidden_subprocess_kwargs())
    if result.returncode:
        raise RuntimeError("Windows 网卡查询或操作失败，请确认权限和所选网卡状态。")
    return result.stdout.strip()


def adapter_metadata() -> dict:
    try:
        result = json.loads(_powershell("Get-NetAdapter -IncludeHidden | Select-Object InterfaceGuid,HardwareInterface,NdisPhysicalMedium,Status | ConvertTo-Json -Compress"))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return {}
    if isinstance(result, dict):
        result = [result]
    return {entry["InterfaceGuid"].lower(): {
        "wired": bool(entry["HardwareInterface"]) and int(entry["NdisPhysicalMedium"]) == 14,
        "status": entry["Status"]} for entry in result or []}


def canonical_guid(interface: WindowsInterface) -> str:
    return "{" + str(uuid.UUID(interface.guid.strip("{}"))).upper() + "}"


def _cim_selection(interface: WindowsInterface) -> str:
    guid = canonical_guid(interface)
    return (f"$cfg=@(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter \"SettingID='{guid}'\"); "
            "if ($cfg.Count -ne 1) { throw 'Selected adapter unavailable' }; $cfg=$cfg[0]; ")


def read_network_details(interface: WindowsInterface) -> InterfaceNetworkDetails:
    data = json.loads(_powershell(_cim_selection(interface) +
        "$cfg | Select-Object IPAddress,IPSubnet,DefaultIPGateway,DNSServerSearchOrder | ConvertTo-Json -Compress"))
    addresses = data.get("IPAddress") or []
    subnets = data.get("IPSubnet") or []
    ipv4 = _first_ipv4(addresses)
    index = addresses.index(ipv4) if ipv4 in addresses else -1
    mask = subnets[index] if 0 <= index < len(subnets) else "0.0.0.0"
    return InterfaceNetworkDetails(ipv4, mask, _first_ipv4(data.get("DefaultIPGateway") or []),
                                   _first_ipv4(data.get("DNSServerSearchOrder") or []))


def renew_selected_adapter(interface: WindowsInterface) -> None:
    # SettingID is exact GUID; neither shell commands nor wildcard adapter aliases.
    script = _cim_selection(interface) + (
        "if (-not $cfg.DHCPEnabled) { throw 'DHCP is disabled on selected adapter' }; "
        "$result=Invoke-CimMethod -InputObject $cfg -MethodName RenewDHCPLease; "
        "$result.ReturnValue")
    code = _powershell(script, timeout=45)
    if code != "0":
        raise RuntimeError(f"所选网卡 DHCP 续租未完成（Windows 状态 {code}）。可尝试 DHCP 模式 0 或联系网络管理员。")

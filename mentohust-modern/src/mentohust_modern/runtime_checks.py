from __future__ import annotations
import ipaddress
from pathlib import Path
from scapy.all import conf
from .config import MentohustConfig
from .interfaces import find_interface

def npcap_installed() -> bool:
    """Scapy resolves both existing WinPcap and Npcap libraries."""
    try:
        conf.use_pcap = True
        return bool(conf.use_pcap)
    except (OSError, ImportError):
        return False

def validation_errors(config: MentohustConfig) -> list[str]:
    errors = []
    if not npcap_installed():
        errors.append("未找到可用的 WinPcap / Npcap。请确认现有抓包驱动可用。")
    if not config.client_exe or not config.client_exe_path().is_file():
        errors.append("请选择本机锐捷安装目录中的 8021x.exe。")
    if not config.username.strip() or len(config.username.encode("utf-8")) > 128:
        errors.append("请输入校园网账号（UTF-8 长度不超过 128 字节）。")
    if not config.password or len(config.password.encode("utf-8")) > 512:
        errors.append("请输入校园网密码（UTF-8 长度不超过 512 字节）。")
    if not config.interface_id:
        errors.append("请明确选择插入校园网线的物理以太网卡。")
    else:
        selected = find_interface(config.interface_id)
        if not selected or not selected.is_wired:
            errors.append("所选物理以太网卡已不可用，请刷新并重新选择。")
        elif selected.status.lower() != "up":
            errors.append("所选以太网卡未连接，请插好校园网线后再连接。")
    for key in ("ipaddr", "mask", "gateway", "dns"):
        try:
            ipaddress.IPv4Address(getattr(config, key))
        except (ValueError, TypeError):
            errors.append(f"{key} 应填写有效 IPv4 地址。")
    if config.multicast_address not in (0, 1, 2) or config.dhcp_mode not in (0, 1, 2, 3):
        errors.append("组播模式为 0–2，DHCP 模式为 0–3。")
    if not all(1 <= v <= 300 for v in (config.timeout, config.interval, config.wait)):
        errors.append("超时、心跳和重试间隔应为 1–300 秒。")
    if not 1 <= config.fail_number <= 100:
        errors.append("失败上限应为 1–100 次。")
    try:
        major, minor = config.version.split(".")
        if not all(0 <= int(v) <= 255 for v in (major, minor)):
            raise ValueError()
    except (ValueError, AttributeError):
        errors.append("客户端版本格式如 5.00。")
    return errors

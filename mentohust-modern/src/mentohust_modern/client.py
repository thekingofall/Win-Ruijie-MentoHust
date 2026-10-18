from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import queue
import subprocess
import threading
import time

from scapy.all import AsyncSniffer, Ether, conf

from .config import MentohustConfig
from .interfaces import WindowsInterface, find_interface, read_network_details, renew_selected_adapter
from .ruijie import (
    RUIJIE_ADDR,
    STANDARD_ADDR,
    RuntimeNetworkConfig,
    apply_md5_check,
    build_challenge_packet,
    build_echo_packet,
    build_fill_buffer,
    build_identity_packet,
    build_logoff_packet,
    build_start_packet,
    extract_echo_key,
    get_version_from_exe,
    parse_server_messages,
)
from .windows import hidden_subprocess_kwargs


DISCONNECT = 0
START = 1
IDENTITY = 2
CHALLENGE = 3
ECHO = 4
DHCP = 5
WAITECHO = 6

MAX_SEND_COUNT = 3


def _mask_auto(value: str) -> bool:
    return not value or value == "0.0.0.0"


class MentohustClient:
    def __init__(
        self,
        config: MentohustConfig,
        *,
        log_callback: Callable[[str], None] | None = None,
        state_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.log_callback = log_callback or (lambda message: None)
        self.state_callback = state_callback or (lambda state: None)
        self.interface: WindowsInterface | None = None
        self.runtime_network: RuntimeNetworkConfig | None = None
        self.runtime_mode = config.multicast_address
        self.dhcp_mode_runtime = config.dhcp_mode
        self.state = DISCONNECT
        self.send_count = 0
        self.fail_count = 0
        self.echo_key = 0
        self.echo_no = 0x0000102B
        self.dest_mac = STANDARD_ADDR
        self.server_mac_locked = False
        self.next_timeout = float(config.timeout)
        self.packet_queue: queue.Queue[bytes] = queue.Queue(maxsize=256)
        self.send_lock = threading.RLock()
        self.terminal_error = False
        self.authenticated = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.sniffer: AsyncSniffer | None = None
        self.socket = None
        self.base_fill = bytearray()
        self._latest_packet = b""

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.packet_queue = queue.Queue(maxsize=256)
        self.terminal_error = False
        self.authenticated = False
        self.state = DISCONNECT
        self.thread = threading.Thread(target=self._run, daemon=True, name="MentohustClient")
        self.thread.start()

    def stop(self, *, wait: bool = True) -> None:
        self.stop_event.set()
        try:
            self.packet_queue.put_nowait(b"")
        except queue.Full:
            pass
        # Wake the receive loop before joining the worker.
        if self.socket is not None and (self.authenticated or self.state in (START, IDENTITY, CHALLENGE, ECHO, WAITECHO)):
            try:
                self._send_logoff()
            except Exception:
                pass
            self.state = DISCONNECT
            self.authenticated = False
        if self.sniffer is not None:
            try:
                self.sniffer.stop(join=False)
            except Exception:
                pass
        if wait and self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5)

    def _log(self, message: str) -> None:
        # Server strings and exception messages may contain credentials.
        for secret in (self.config.password, self.config.username):
            if secret:
                message = message.replace(secret, "[已隐藏]")
        self.log_callback(message)

    def _set_state(self, state: str) -> None:
        self.state_callback(state)

    def _run(self) -> None:
        from .process_lock import exclusive
        try:
            with exclusive("Global\\XinRuijieAuthenticationSessionV1"):
                self._run_owned()
        except RuntimeError as exc:
            self.terminal_error = True
            self._log(f"[错误] {exc}")
            self._set_state("错误")

    def _run_owned(self) -> None:
        try:
            self._initialize()
            if self.stop_event.is_set():
                return
            if self.dhcp_mode_runtime == 3:
                self._switch_state(DHCP)
            else:
                self._switch_state(START)
            while not self.stop_event.is_set():
                try:
                    packet = self.packet_queue.get(timeout=self.next_timeout)
                    if not self.stop_event.is_set():
                        self._handle_packet(packet)
                except queue.Empty:
                    self._switch_state(self.state)
        except Exception as exc:
            self.terminal_error = True
            self._log(f"[错误] {exc}")
            self._set_state("错误")
        finally:
            self._shutdown()

    def _initialize(self) -> None:
        self.interface = find_interface(self.config.interface_id, self.config.interface_description)
        if self.interface is None or not self.interface.is_wired:
            raise RuntimeError("所选物理以太网卡已不可用，请刷新后明确选择插校园网线的网卡。")
        if self.interface.status.lower() != "up":
            raise RuntimeError("所选以太网卡未连接，请插好校园网线后重试。")

        if self.config.version.strip() == "auto":
            self.config.version = "5.00"
        if self.config.client_exe and self.config.client_exe_path().exists():
            if not self.config.version.strip():
                major, minor = get_version_from_exe(self.config.client_exe_path())
                self.config.version = f"{major}.{minor:02d}"

        self.runtime_mode = self.config.multicast_address
        self.dhcp_mode_runtime = self.config.dhcp_mode
        self.fail_count = 0
        self.send_count = 0
        self.echo_key = 0
        self.echo_no = 0x0000102B
        self.server_mac_locked = False
        self.dest_mac = STANDARD_ADDR if self.runtime_mode in (0, 2) else RUIJIE_ADDR

        self.runtime_network = self._load_runtime_network()
        self.base_fill = build_fill_buffer(self.config, self.runtime_network, self.dhcp_mode_runtime)

        conf.use_pcap = True
        self.socket = conf.L2socket(iface=self.interface.network_name)
        ready = threading.Event()
        self.sniffer = AsyncSniffer(
            iface=self.interface.network_name,
            filter=self._build_filter(),
            store=False,
            prn=self._enqueue_packet,
            started_callback=ready.set,
        )
        self.sniffer.start()
        if not ready.wait(3):
            raise RuntimeError("无法启动所选网卡的 EAPOL 接收，请检查抓包驱动权限。")

        self._log(f"[初始化] 网卡: {self.interface.description}")
        self._log(f"[初始化] Scapy 接口: {self.interface.network_name}")
        self._log(f"[初始化] MAC: {self.interface.mac}")
        self._log(f"[初始化] IP: {self.runtime_network.ipaddr}")
        self._log(f"[初始化] Mask: {self.runtime_network.mask}")
        self._log(f"[初始化] Gateway: {self.runtime_network.gateway}")
        self._log(f"[初始化] DNS: {self.runtime_network.dns}")
        self._log(f"[初始化] 客户端版本: {self.config.version}")
        self._set_state("已初始化")

    def _shutdown(self) -> None:
        if self.socket is not None and (self.authenticated or self.state in (START, IDENTITY, CHALLENGE, ECHO, WAITECHO)):
            try:
                self._send_logoff()
            except Exception:
                pass
        if self.sniffer is not None:
            try:
                self.sniffer.stop(join=False)
            except Exception:
                pass
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
        self.socket = None
        self.sniffer = None
        self.state = DISCONNECT
        self.authenticated = False
        self._set_state("错误" if self.terminal_error else "已停止")

    def _enqueue_packet(self, packet) -> None:
        if self.stop_event.is_set():
            return
        raw = bytes(packet)
        if len(raw) > 4096:
            return
        try:
            self.packet_queue.put_nowait(raw)
        except queue.Full:
            pass

    def _load_runtime_network(self) -> RuntimeNetworkConfig:
        assert self.interface is not None
        detected = read_network_details(self.interface)
        ipaddr = self.config.ipaddr if not _mask_auto(self.config.ipaddr) else detected.ipv4
        mask = self.config.mask if not _mask_auto(self.config.mask) else detected.mask
        gateway = self.config.gateway if not _mask_auto(self.config.gateway) else detected.gateway
        dns = self.config.dns if not _mask_auto(self.config.dns) else detected.dns
        return RuntimeNetworkConfig(
            ipaddr=ipaddr,
            gateway=gateway,
            mask=mask,
            dns=dns,
            ping_ip=self.config.resolved_ping_host(),
            local_mac=self.interface.mac,
        )

    def _build_filter(self) -> str:
        assert self.interface is not None
        mac = self.interface.mac.lower()
        return f"ether proto 0x888e and not ether src {mac}"

    def _switch_state(self, target: int) -> None:
        if self.stop_event.is_set() and target != DISCONNECT:
            return
        if self.state == target:
            self.send_count += 1
        else:
            self.state = target
            self.send_count = 0

        if self.send_count >= MAX_SEND_COUNT and target != ECHO:
            if target == START:
                self._log("[状态] 找不到服务器，准备重试。")
                self._restart()
                return
            if target == IDENTITY:
                self._log("[状态] 发送用户名超时，准备重试。")
                self._restart()
                return
            if target == CHALLENGE:
                self._log("[状态] 发送密码超时，准备重试。")
                self._restart()
                return
            if target == WAITECHO:
                self._log("[状态] 等待心跳响应超时，自行发送心跳。")
                self._switch_state(ECHO)
                return

        if target == DHCP:
            self._renew_ip()
            return
        if target == START:
            self._send_start()
            return
        if target == IDENTITY:
            self._send_identity()
            return
        if target == CHALLENGE:
            self._send_challenge()
            return
        if target == WAITECHO:
            self._set_state("等待心跳")
            self.next_timeout = float(self.config.interval)
            return
        if target == ECHO:
            self._send_echo()
            return
        if target == DISCONNECT:
            self._send_logoff()

    def _restart(self) -> None:
        self.authenticated = False
        self.server_mac_locked = False
        self.runtime_mode = self.config.multicast_address
        self.dest_mac = STANDARD_ADDR if self.runtime_mode in (0, 2) else RUIJIE_ADDR
        self.state = START
        self.send_count = -1
        self.next_timeout = float(self.config.wait)
        self._set_state("等待重连")

    def _send_packet(self, payload: bytes, state_text: str) -> None:
        if self.socket is None:
            raise RuntimeError("底层数据链路套接字还没有初始化。")
        with self.send_lock:
            if self.stop_event.is_set() and payload[15] != 2:
                return
            self.socket.send(Ether(payload))
            self._set_state(state_text)

    def _send_start(self) -> None:
        assert self.runtime_network is not None
        packet = build_start_packet(self.base_fill, self.runtime_network.local_mac, self.dest_mac, self.runtime_mode)
        self._send_packet(packet, "寻找服务器")
        self.next_timeout = float(self.config.timeout)
        self._log("[发送] Start")

    def _send_identity(self) -> None:
        assert self.runtime_network is not None
        packet = build_identity_packet(
            self.base_fill,
            self.runtime_network.local_mac,
            self.dest_mac,
            self._latest_packet,
            self.config.username,
            self.runtime_mode,
        )
        self._send_packet(packet, "发送用户名")
        self.next_timeout = float(self.config.timeout)
        self._log("[发送] Identity")

    def _send_challenge(self) -> None:
        assert self.runtime_network is not None
        fill_buf = bytearray(self.base_fill)
        apply_md5_check(fill_buf, self._latest_packet[0x18 : 0x18 + self._latest_packet[0x17]], self.config)
        packet = build_challenge_packet(
            fill_buf,
            self.runtime_network.local_mac,
            self.dest_mac,
            self._latest_packet,
            self.config.username,
            self.config.password,
            self.runtime_mode,
        )
        self._send_packet(packet, "发送密码")
        self.next_timeout = float(self.config.timeout)
        self._log("[发送] Challenge")

    def _send_echo(self) -> None:
        assert self.runtime_network is not None
        packet = build_echo_packet(
            self.runtime_network.local_mac,
            self.dest_mac,
            self.echo_key,
            self.echo_no,
            self.runtime_mode,
        )
        self.echo_no = (self.echo_no + 1) & 0xFFFFFFFF
        self._send_packet(packet, "在线保持")
        self.next_timeout = float(self.config.interval)
        self._log("[发送] 在线心跳")

    def _send_logoff(self) -> None:
        assert self.runtime_network is not None
        packet = build_logoff_packet(self.base_fill, self.runtime_network.local_mac, self.dest_mac, self.runtime_mode)
        try:
            self._send_packet(packet, "断开连接")
            self._log("[发送] Logoff")
        finally:
            self.next_timeout = 0.2

    def _renew_ip(self) -> None:
        assert self.interface is not None
        self._set_state("更新 IP")
        self._log("[DHCP] 开始更新 IP")
        renew_selected_adapter(self.interface)
        if self.stop_event.wait(1.0):
            return
        self.dhcp_mode_runtime += 3
        self.runtime_network = self._load_runtime_network()
        self.base_fill = build_fill_buffer(self.config, self.runtime_network, self.dhcp_mode_runtime)
        self._log(f"[DHCP] 新 IP: {self.runtime_network.ipaddr}")
        if self.dhcp_mode_runtime == 5:
            self._switch_state(ECHO)
        else:
            self._switch_state(START)

    def _handle_packet(self, packet: bytes) -> None:
        if self.stop_event.is_set() or self.state == DISCONNECT or len(packet) < 18 or len(packet) > 4096:
            return
        if packet[12:14] != b"\x88\x8e" or packet[14] not in (1, 2, 3):
            return
        if self.runtime_network is None:
            return
        local = bytes.fromhex(self.runtime_network.local_mac.replace(":", ""))
        if packet[:6] not in (local, STANDARD_ADDR, RUIJIE_ADDR):
            return
        if packet[6:12] == local or packet[6] & 1 or packet[6:12] == b"\0" * 6:
            return
        if self.server_mac_locked and packet[6:12] != self.dest_mac:
            return
        if packet[15] == 5:
            if self.server_mac_locked and self.state in (ECHO, WAITECHO):
                self._switch_state(ECHO)
            return
        if packet[15] != 0 or len(packet) < 22:
            return
        eapol_size = int.from_bytes(packet[16:18], "big")
        eap_size = int.from_bytes(packet[20:22], "big")
        if eap_size < 4 or eap_size != eapol_size or 18 + eapol_size > len(packet):
            return
        code, identifier = packet[18], packet[19]
        if code == 1:
            if eap_size < 5:
                return
            kind = packet[22]
            if kind == 1:
                if not self.server_mac_locked:
                    self.dest_mac = packet[6:12]
                    self.server_mac_locked = True
                    self._log("[服务器] 已定位校园网认证服务器")
                self._latest_packet = packet
                if packet[23:32] == b"User name":
                    if eap_size < 18:
                        return
                    self.runtime_mode = 2
                self._switch_state(IDENTITY)
            elif kind == 4:
                if not self.server_mac_locked or self.state not in (IDENTITY, CHALLENGE):
                    return
                if eap_size < 6 or packet[23] != 16 or 24 + packet[23] > 18 + eap_size:
                    return
                self._latest_packet = packet
                self._switch_state(CHALLENGE)
            return
        if code == 3:
            if not self.server_mac_locked or self.state != CHALLENGE or identifier != self._latest_packet[19]:
                return
            if self.runtime_mode != 2:
                try:
                    self.echo_key = extract_echo_key(packet)
                except ValueError:
                    self._log("[协议] 成功响应缺少完整心跳字段，等待服务器重发。")
                    return
            self.fail_count = 0
            self.authenticated = True
            self._set_state("认证成功")
            self._log("[状态] 认证成功")
            for message in parse_server_messages(packet):
                self._log(f"[服务端] {message}")
            if self.dhcp_mode_runtime in (1, 2):
                self._switch_state(DHCP)
            elif self.runtime_mode == 2:
                self._switch_state(WAITECHO)
            else:
                self._switch_state(ECHO)
            return
        if code == 4 and self.server_mac_locked and self.state in (IDENTITY, CHALLENGE, ECHO, WAITECHO):
            if self.state in (IDENTITY, CHALLENGE) and identifier != self._latest_packet[19]:
                return
            for message in parse_server_messages(packet):
                self._log(f"[服务端] {message}")
            self.authenticated = False
            self.fail_count += 1
            self._log(f"[状态] 认证失败或会话结束，第 {self.fail_count} 次。")
            if self.fail_count >= max(1, self.config.fail_number):
                self._log("[状态] 达到失败上限，请检查账号或网络提示后重新连接。")
                self.stop_event.set()
            else:
                self._restart()

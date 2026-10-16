from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from mentohust_modern.config import MentohustConfig
from mentohust_modern.client import MentohustClient, START, IDENTITY, CHALLENGE, ECHO, DISCONNECT
from mentohust_modern.ruijie import RuntimeNetworkConfig, build_echo_packet, extract_echo_key, parse_server_messages

LOCAL = bytes.fromhex("001122334455")
SERVER = bytes.fromhex("aabbccddeeff")

def frame(code, identifier=7, body=b"", suffix=b""):
    size = 4+len(body)
    return LOCAL+SERVER+b"\x88\x8e\x01\x00"+size.to_bytes(2,"big")+bytes([code,identifier])+size.to_bytes(2,"big")+body+suffix

def client():
    c = MentohustClient(MentohustConfig(dhcp_mode=0, username="private-user", password="private-secret"))
    c.runtime_network = RuntimeNetworkConfig("0.0.0.0","0.0.0.0","0.0.0.0","0.0.0.0","0.0.0.0","00:11:22:33:44:55")
    c.state = START
    def transition(state):
        c.state = state
    c._switch_state = Mock(side_effect=transition)
    return c

class SecretsTests(unittest.TestCase):
    def test_dpapi_and_credential_free_export(self):
        c=MentohustConfig(username="private-user",password="sensitive-secret-123",remember_password=True)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"profile.json"
            c.save_json(path)
            text=path.read_text(encoding="utf-8")
            self.assertNotIn(c.password,text)
            self.assertNotIn('"password":',text)
            self.assertEqual(MentohustConfig.load_json(path).password,c.password)
            c.save_json(path,export=True)
            data=json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["username"],"")
            self.assertEqual(data["password_dpapi"],"")
            self.assertFalse(data["auto_connect"])
    def test_empty_config_and_legacy_plaintext_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"profile.json"
            MentohustConfig().save_json(path)
            self.assertEqual(MentohustConfig.load_json(path).password,"")
            path.write_text('{"password":"legacy-secret"}',encoding="utf-8")
            self.assertEqual(MentohustConfig.load_json(path).password,"")
    def test_repr_and_server_messages_redact_credentials(self):
        c=client()
        c.log_callback=Mock()
        c._log("private-user / private-secret rejected")
        self.assertNotIn("private-secret",repr(c.config))
        self.assertEqual(c.log_callback.call_args.args[0],"[已隐藏] / [已隐藏] rejected")

class ProtocolTests(unittest.TestCase):
    def test_identity_challenge_success_sequence(self):
        c=client()
        c._handle_packet(frame(1,body=b"\x01"))
        self.assertEqual(c.state,IDENTITY)
        c._handle_packet(frame(1,body=b"\x04\x10"+b"a"*16))
        self.assertEqual(c.state,CHALLENGE)
        success=frame(3,suffix=b"\0"*140)
        c._handle_packet(success)
        self.assertEqual(c.state,ECHO)
    def test_unsolicited_and_wrong_identifier_success_are_ignored(self):
        c=client()
        c._handle_packet(frame(3,suffix=b"\0"*140))
        self.assertEqual(c.state,START)
        c._handle_packet(frame(1,body=b"\x01"))
        c._handle_packet(frame(1,body=b"\x04\x10"+b"a"*16))
        c._handle_packet(frame(3,identifier=8,suffix=b"\0"*140))
        self.assertEqual(c.state,CHALLENGE)
    def test_truncated_success_does_not_become_online(self):
        c=client()
        c._handle_packet(frame(1,body=b"\x01"))
        c._handle_packet(frame(1,body=b"\x04\x10"+b"a"*16))
        c._handle_packet(frame(3))
        self.assertEqual(c.state,CHALLENGE)
    def test_malformed_packets_and_other_destinations_are_ignored(self):
        good=frame(1,body=b"\x01")
        for data in [good[:i] for i in range(len(good))]+[b"\0"*5000,b"\xff"*6+good[6:]]:
            c=client()
            c._handle_packet(data)
            self.assertEqual(c.state,START)
        c=client()
        c._handle_packet(frame(1,body=b"\x01"))
        c._handle_packet(frame(1,body=b"\x04\xff"+b"a"*16))
        self.assertEqual(c.state,IDENTITY)
    def test_heartbeat_before_auth_does_not_mark_online(self):
        c=client()
        p=bytearray(frame(1,body=b"\x01"));p[15]=5
        c._handle_packet(bytes(p))
        self.assertEqual(c.state,START)
    def test_foreign_server_after_lock_is_ignored(self):
        c=client()
        c._handle_packet(frame(1,body=b"\x01"))
        p=bytearray(frame(1,body=b"\x04\x10"+b"a"*16));p[6]=0x88
        c._handle_packet(bytes(p))
        self.assertEqual(c.state,IDENTITY)
    def test_echo_wrap_and_short_messages(self):
        build_echo_packet("00:11:22:33:44:55",SERVER,0xffffffff,0xffffffff,1)
        self.assertEqual(parse_server_messages(b""),[])
        with self.assertRaises(ValueError):extract_echo_key(b"")
    def test_bounded_queue_and_stop(self):
        c=client()
        for _ in range(300):c._enqueue_packet(b"x"*100)
        self.assertEqual(c.packet_queue.qsize(),256)
        c.stop(wait=False)
        c._handle_packet(frame(1,body=b"\x01"))
        self.assertEqual(c.state,START)
    def test_stop_wakes_worker_and_reconnects_without_old_packets(self):
        c=MentohustClient(MentohustConfig())
        c._initialize=Mock()
        c._switch_state=Mock(side_effect=lambda state: setattr(c,"state",state))
        c.next_timeout=60
        c.start()
        time.sleep(.05)
        begin=time.monotonic();c.stop();elapsed=time.monotonic()-begin
        self.assertFalse(c.thread.is_alive())
        self.assertLess(elapsed,1)
        c.packet_queue.put(b"old")
        c.start();time.sleep(.05);c.stop()
        self.assertFalse(c.thread.is_alive())
        self.assertEqual(c._initialize.call_count,2)

class WireWorkflowTests(unittest.TestCase):
    def test_real_packet_builders_with_fake_socket(self):
        from mentohust_modern.ruijie import build_fill_buffer, compute_password_md5
        c = client()
        del c._switch_state
        c.state = DISCONNECT
        c.base_fill = build_fill_buffer(c.config, c.runtime_network, 0)
        sent = []
        c.socket = Mock()
        c.socket.send.side_effect = lambda packet: sent.append(bytes(packet))
        c._switch_state(START)
        self.assertEqual(sent[-1][15], 1)
        c._handle_packet(frame(1, identifier=5, body=b"\x01"))
        self.assertEqual(sent[-1][18:20], b"\x02\x05")
        self.assertEqual(sent[-1][22], 1)
        self.assertEqual(sent[-1][23:35], b"private-user")
        seed = bytes(range(16))
        c._handle_packet(frame(1, identifier=7, body=b"\x04\x10"+seed))
        self.assertEqual(sent[-1][18:20], b"\x02\x07")
        self.assertEqual(sent[-1][22:24], b"\x04\x10")
        self.assertEqual(sent[-1][24:40], compute_password_md5(7, "private-secret", seed, False))
        c._handle_packet(frame(3, identifier=7, suffix=b"\0"*140))
        self.assertEqual(c.state, ECHO)
        self.assertEqual(sent[-1][15], 0xbf)
        c.stop(wait=False)
        self.assertEqual(sent[-1][15], 2)
        self.assertEqual(c.state, DISCONNECT)
        self.assertEqual(len(sent), 5)
        c._shutdown()
        self.assertEqual(len(sent), 5)
        for packet in sent:
            self.assertEqual(packet[6:12], LOCAL)
        for packet in sent[1:]:
            self.assertEqual(packet[:6], SERVER)

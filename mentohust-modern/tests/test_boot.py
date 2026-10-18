import json
import queue
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from mentohust_modern import APP_VERSION, boot
from mentohust_modern.boot_worker import retry_delay
from mentohust_modern.config import MentohustConfig
from mentohust_modern.gui import MentohustApp
from mentohust_modern.secrets import protect, unprotect


class BootTests(unittest.TestCase):
    def test_machine_dpapi_profile_roundtrip_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            config = MentohustConfig(username="test-account",password="test-password",remember_password=True)
            config.save_json(path,machine=True)
            self.assertNotIn("test-password",path.read_text())
            self.assertEqual(MentohustConfig.load_json(path).password,"test-password")
            config.save_json(path,machine=True,export=True)
            self.assertEqual(json.loads(path.read_text())["password_dpapi"],"")
        self.assertEqual(unprotect(protect("machine-test",machine=True)),"machine-test")

    def test_query_requires_boolean_flags(self):
        for value in ({},{"enabled":"false","legacy_enabled":False}):
            with patch.object(boot,"runtime_root",return_value=Path("C:/ProgramData/XinRuijieBoot")), patch.object(boot.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value))):
                with self.assertRaises(RuntimeError): boot.boot_status()

    def test_source_cannot_install(self):
        with patch.object(boot.sys,"frozen",False,create=True),patch.object(boot,"_operation") as op:
            with self.assertRaises(RuntimeError):boot.set_boot_enabled(True,MentohustConfig())
            op.assert_not_called()

    def test_stale_or_future_heartbeat_is_not_live(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(boot,"runtime_root",return_value=Path(folder)),patch.object(boot.time,"time",return_value=100):
            for timestamp in (1,150):
                (Path(folder)/"status.json").write_text(json.dumps({"updated_at":timestamp,"authenticated":True}))
                self.assertFalse(boot.read_status()["fresh"])

    def test_retry_backoff_is_bounded(self):
        self.assertEqual([retry_delay(n) for n in range(1,8)],[15,30,60,120,240,300,300])
        self.assertEqual(retry_delay(10000),300)

    def test_local_auth_is_suppressed_until_background_ownership_known(self):
        for state in ({"ready":False,"enabled":False,"legacy_enabled":False},
                      {"ready":True,"enabled":True,"legacy_enabled":False},
                      {"ready":True,"enabled":False,"legacy_enabled":True}):
            app=Mock(client=None,exiting=False,_boot_busy=False,_boot=state)
            with patch("mentohust_modern.gui.MentohustClient") as client:
                MentohustApp._connect(app)
                client.assert_not_called()

    def test_failed_activation_restores_task_and_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            exe=root/"app.exe"
            exe.write_bytes(b"packaged-test")
            old=b'{"existing":"encrypted-config"}'
            (root/"profile.json").write_bytes(old)
            operations=[]
            def operation(name, target=None):
                operations.append(name)
                if name=="query": return {"enabled":False,"legacy_enabled":True}
                if name=="activate": raise RuntimeError("activation failed")
                return {"ok":True}
            config=MentohustConfig(username="test",password="test",remember_password=True)
            with patch.object(boot,"runtime_root",return_value=root),patch.object(boot.sys,"frozen",True,create=True),patch.object(boot.sys,"executable",str(exe)),patch("mentohust_modern.elevation.is_admin",return_value=True),patch("mentohust_modern.runtime_checks.validation_errors",return_value=[]),patch.object(boot,"_operation",side_effect=operation),patch.object(boot,"_await_report",return_value={"ok":True,"version":APP_VERSION}):
                with self.assertRaisesRegex(RuntimeError,"activation failed"):
                    boot.set_boot_enabled(True,config)
            self.assertEqual((root/"profile.json").read_bytes(),old)
            self.assertEqual(operations[-2:],["stop","restore"])

    def test_failed_system_preflight_keeps_existing_task_running(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            exe=root/"app.exe"
            exe.write_bytes(b"packaged-test")
            with patch.object(boot,"runtime_root",return_value=root),patch.object(boot.sys,"frozen",True,create=True),patch.object(boot.sys,"executable",str(exe)),patch("mentohust_modern.elevation.is_admin",return_value=True),patch("mentohust_modern.runtime_checks.validation_errors",return_value=[]),patch.object(boot,"_operation",return_value={"enabled":False,"legacy_enabled":True,"ok":True}) as operation,patch.object(boot,"_await_report",return_value={"ok":False}):
                with self.assertRaises(RuntimeError):
                    boot.set_boot_enabled(True,MentohustConfig(username="test",password="test"))
            self.assertNotIn("stop",[call.args[0] for call in operation.call_args_list])

    def test_stale_query_cannot_unlock_install_or_overwrite_result(self):
        for busy in (True, False):
            for kind,payload in (("boot_query",{"enabled":False,"legacy_enabled":False}),
                                 ("boot_query_error","late error")):
                with self.subTest(busy=busy,kind=kind):
                    app=Mock(events=queue.Queue(),_boot_query_serial=2,_boot_busy=busy,
                             _boot={"ready":True,"enabled":True,"legacy_enabled":False},
                             _boot_message="current",_next_boot_status=float("inf"),
                             online_since=None,client=None,_hide_after_connect=False,exiting=False)
                    app.events.put((kind,(1,payload)))
                    MentohustApp._drain(app)
                    self.assertEqual(app._boot_busy,busy)
                    self.assertTrue(app._boot["enabled"])
                    self.assertEqual(app._boot_message,"current")
                    app._update_boot_controls.assert_not_called()

    def test_latest_query_updates_controls_when_idle(self):
        app=Mock(events=queue.Queue(),_boot_query_serial=2,_boot_busy=False,
                 _boot={"ready":False,"enabled":False,"legacy_enabled":False},
                 _next_boot_status=float("inf"),online_since=None,client=None,
                 _hide_after_connect=False,exiting=False)
        app.events.put(("boot_query",(2,{"enabled":True,"legacy_enabled":False})))
        MentohustApp._drain(app)
        self.assertTrue(app._boot["ready"])
        self.assertTrue(app._boot["enabled"])
        app._update_boot_controls.assert_called_once()

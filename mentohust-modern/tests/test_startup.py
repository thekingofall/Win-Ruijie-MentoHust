import base64
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mentohust_modern import startup


class StartupTests(unittest.TestCase):
    def test_source_run_cannot_register_python_as_startup(self):
        with patch.object(startup.sys, "frozen", False, create=True), patch.object(startup.subprocess, "run") as run:
            with self.assertRaises(RuntimeError): startup.set_startup(True)
            run.assert_not_called()

    def test_paths_are_data_not_powershell_source(self):
        target=Path("C:/校园网 O'Brien/$example/新锐捷.exe")
        with patch.object(startup.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout='{"enabled":true}')) as run:
            self.assertTrue(startup._operation("enable",target)["enabled"])
        arguments=run.call_args.args[0]
        script=base64.b64decode(arguments[-1]).decode("utf-16-le")
        prefix="FromBase64String('"
        payload=script.split(prefix,1)[1].split("'",1)[0]
        self.assertEqual(json.loads(base64.b64decode(payload))["exe"],str(target))
        self.assertNotIn(str(target),script)

    def test_failure_does_not_report_enabled(self):
        with patch.object(startup.subprocess,"run",return_value=SimpleNamespace(returncode=1,stdout='{"enabled":true}')):
            with self.assertRaises(RuntimeError): startup.startup_status()

    def test_query_rejects_unknown_or_nonboolean_state(self):
        for output in ('{}','{"enabled":"false"}'):
            with self.subTest(output=output), patch.object(startup.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout=output)):
                with self.assertRaises(RuntimeError): startup.startup_status()

    def test_disabled_state_is_read_back(self):
        with patch.object(startup.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout='{"enabled":false,"executable":""}')):
            self.assertFalse(startup._operation("disable")["enabled"])

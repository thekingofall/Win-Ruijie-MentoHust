import json
import subprocess
import unittest
from unittest.mock import patch
from mentohust_modern.interfaces import WindowsInterface, adapter_metadata, read_network_details, renew_selected_adapter

GUID = "{11111111-2222-3333-4444-555555555555}"
def nic():
    return WindowsInterface(5, "Ethernet * odd alias", "Same description", GUID, "NPF_"+GUID,
                            "00:11:22:33:44:55", "0.0.0.0", [], True, "Up")

class InterfaceTests(unittest.TestCase):
    def test_multiple_adapters_classify_wired_without_disabling_others(self):
        data = [{"InterfaceGuid": GUID, "HardwareInterface": True, "NdisPhysicalMedium": 14, "Status": "Up"},
                {"InterfaceGuid": "wifi", "HardwareInterface": True, "NdisPhysicalMedium": 9, "Status": "Up"},
                {"InterfaceGuid": "vpn", "HardwareInterface": False, "NdisPhysicalMedium": 0, "Status": "Up"}]
        with patch("mentohust_modern.interfaces._powershell", return_value=json.dumps(data)):
            found = adapter_metadata()
        self.assertTrue(found[GUID.lower()]["wired"])
        self.assertFalse(found["wifi"]["wired"])
        self.assertFalse(found["vpn"]["wired"])
        self.assertEqual(len(found), 3)

    def test_guid_scoped_dhcp_never_executes_alias_or_custom_shell(self):
        done = subprocess.CompletedProcess([], 0, "0", "")
        with patch("mentohust_modern.interfaces.subprocess.run", return_value=done) as run:
            renew_selected_adapter(nic())
        args, kwargs = run.call_args
        command = args[0][-1]
        self.assertIn("SettingID='"+GUID+"'", command)
        self.assertIn("-InputObject $cfg -MethodName RenewDHCPLease", command)
        self.assertNotIn("ipconfig", command)
        self.assertNotIn("Ethernet *", command)
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["timeout"], 45)

    def test_invalid_guid_cannot_inject_powershell(self):
        interface = nic()
        interface.guid = "'; Invoke-Expression x"
        with patch("mentohust_modern.interfaces.subprocess.run") as run:
            with self.assertRaises(ValueError):
                renew_selected_adapter(interface)
            run.assert_not_called()

    def test_dhcp_failure_is_visible(self):
        with patch("mentohust_modern.interfaces._powershell", return_value="82"):
            with self.assertRaises(RuntimeError):
                renew_selected_adapter(nic())

    def test_read_network_uses_guid_and_selected_dns(self):
        data = {"IPAddress": ["fe80::1", "10.1.2.3"], "IPSubnet": ["64", "255.255.0.0"],
                "DefaultIPGateway": ["10.1.0.1"], "DNSServerSearchOrder": ["10.2.0.1"]}
        with patch("mentohust_modern.interfaces._powershell", return_value=json.dumps(data)) as run:
            details = read_network_details(nic())
        self.assertEqual(details.ipv4, "10.1.2.3")
        self.assertEqual(details.mask, "255.255.0.0")
        self.assertEqual(details.dns, "10.2.0.1")
        self.assertIn(GUID, run.call_args.args[0])

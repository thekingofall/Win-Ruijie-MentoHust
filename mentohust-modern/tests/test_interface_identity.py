import unittest
from unittest.mock import patch

from mentohust_modern.interfaces import WindowsInterface, find_interface


def adapter(guid):
    return WindowsInterface(1, "Ethernet", "Same adapter description", guid,
                            "NPF_" + guid, "00:11:22:33:44:55", "0.0.0.0", [])


class InterfaceIdentityTests(unittest.TestCase):
    def test_stale_guid_does_not_fall_back_to_another_adapter(self):
        with patch("mentohust_modern.interfaces.list_interfaces", return_value=[adapter("B")]):
            self.assertIsNone(find_interface("A", "Same adapter description"))

    def test_exact_guid_wins_over_earlier_description_match(self):
        expected = adapter("B")
        with patch("mentohust_modern.interfaces.list_interfaces", return_value=[adapter("A"), expected]):
            self.assertIs(find_interface("b", "Same adapter description"), expected)

    def test_description_without_guid_requires_explicit_selection(self):
        expected = adapter("B")
        with patch("mentohust_modern.interfaces.list_interfaces", return_value=[expected]):
            self.assertIsNone(find_interface("", "Same adapter description"))

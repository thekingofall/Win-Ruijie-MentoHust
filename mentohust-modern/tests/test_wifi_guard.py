"""Coexistence regression: GUI connection never queries unrelated adapters."""
import unittest
from unittest.mock import Mock, patch
from mentohust_modern.gui import MentohustApp
from mentohust_modern.config import MentohustConfig

class CoexistenceTests(unittest.TestCase):
    def test_connect_uses_selected_config_without_wifi_guard(self):
        app = Mock()
        app.client = None
        app.exiting = False
        config = Mock(spec=MentohustConfig)
        app._collect.return_value = config
        with patch("mentohust_modern.gui.validation_errors", return_value=[]), patch("mentohust_modern.gui.MentohustClient") as client:
            MentohustApp._connect(app)
        client.assert_called_once()
        self.assertIs(client.call_args.args[0], config)
        client.return_value.start.assert_called_once()
        self.assertFalse(hasattr(MentohustApp, "_guard_wifi_connection"))

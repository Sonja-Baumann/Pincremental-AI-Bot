import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import BotConfig


class BotConfigTests(unittest.TestCase):
    def test_xdg_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"),
            }
            Path(env["HOME"]).mkdir()
            with patch.dict(os.environ, env, clear=True):
                config = BotConfig.load(debug_port=9333, dry_run=True)
            self.assertEqual(config.app_id, "1369470")
            self.assertEqual(config.debug_port, 9333)
            self.assertTrue(config.dry_run)
            self.assertEqual(config.config_path, root / "config/pincremental-ai-bot/config.json")
            self.assertEqual(config.state_path, root / "data/pincremental-ai-bot/learning.json")
            self.assertEqual(config.log_path, root / "state/pincremental-ai-bot/sessions.jsonl")

    def test_config_file_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            config_file = root / "config.json"
            config_file.write_text(json.dumps({
                "app_id": "12345",
                "debug_port": 9001,
                "state_path": str(root / "configured.json"),
                "dry_run": True,
            }), encoding="utf-8")
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                config = BotConfig.load(config_file, app_id="54321", dry_run=False)
            self.assertEqual(config.app_id, "54321")
            self.assertEqual(config.debug_port, 9001)
            self.assertEqual(config.state_path, root / "configured.json")
            self.assertFalse(config.dry_run)

    def test_auto_upgrade_options_default_off_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                config = BotConfig.load()
                self.assertFalse(config.auto_upgrades)
                self.assertEqual(config.upgrade_strategy, "balanced")
                configured = BotConfig.load(
                    auto_upgrades=True,
                    upgrade_strategy="ticket-first",
                )
                configured.save()
                saved = json.loads(configured.config_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["auto_upgrades"])
            self.assertEqual(saved["upgrade_strategy"], "ticket-first")

    def test_rejects_unknown_upgrade_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"HOME": directory}, clear=True):
                with self.assertRaisesRegex(ValueError, "upgrade_strategy"):
                    BotConfig.load(upgrade_strategy="spend-everything")

    def test_legacy_learning_file_is_copied_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            legacy = home / ".pincremental_bot.json"
            legacy.write_text('{"legacy": true}', encoding="utf-8")
            data = root / "data"
            with patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": str(data)}, clear=True):
                config = BotConfig.load()
            self.assertEqual(config.state_path.read_text(encoding="utf-8"), '{"legacy": true}')
            self.assertTrue(legacy.exists())

    def test_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"HOME": directory}, clear=True):
                with self.assertRaisesRegex(ValueError, "debug_port"):
                    BotConfig.load(debug_port=70000)


if __name__ == "__main__":
    unittest.main()

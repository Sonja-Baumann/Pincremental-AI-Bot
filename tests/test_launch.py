import unittest

from pincremental_bot import build_steam_command


class SteamLaunchTests(unittest.TestCase):
    def test_command_passes_app_id_and_loopback_debug_port_flags(self):
        self.assertEqual(
            build_steam_command("/usr/bin/steam", "1369470", 9222),
            [
                "/usr/bin/steam",
                "-applaunch",
                "1369470",
                "--remote-debugging-port=9222",
                "--remote-allow-origins=*",
            ],
        )

    def test_invalid_app_id_and_port_are_rejected(self):
        with self.assertRaises(ValueError):
            build_steam_command("steam", "not-an-id", 9222)
        with self.assertRaises(ValueError):
            build_steam_command("steam", "1369470", 0)


if __name__ == "__main__":
    unittest.main()

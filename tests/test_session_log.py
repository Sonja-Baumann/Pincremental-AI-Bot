import json
from pathlib import Path
import tempfile
import unittest

from session_log import JsonlSessionLogger, default_session_directory


class SessionLoggerTests(unittest.TestCase):
    def test_writes_typed_jsonl_records_and_flushes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            logger = JsonlSessionLogger(path, session_id="test-session")
            logger.ball(x=12.5, y=300, vx=-4, vy=55)
            logger.action(action_id="a1", side="left", lead=0.12)
            logger.outcome(action_id="a1", reward=2.0, rebound=True)

            lines = path.read_text(encoding="utf-8").splitlines()
            logger.close()

        records = [json.loads(line) for line in lines]
        self.assertEqual([item["event"] for item in records], ["session_start", "ball", "action", "outcome"])
        self.assertEqual({item["session_id"] for item in records}, {"test-session"})
        self.assertEqual(records[1]["x"], 12.5)
        self.assertTrue(all("timestamp" in item for item in records))

    def test_context_manager_closes_and_closed_logger_rejects_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            with JsonlSessionLogger(Path(directory) / "run.jsonl") as logger:
                path = logger.path
                logger.ball(x=1, y=2)
            with self.assertRaises(RuntimeError):
                logger.ball(x=3, y=4)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_default_path_uses_xdg_data_home_when_set(self):
        import os
        old = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = "/tmp/pincremental-test-data"
            self.assertEqual(
                default_session_directory(),
                Path("/tmp/pincremental-test-data/pincremental-ai-bot/sessions"),
            )
        finally:
            if old is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old


if __name__ == "__main__":
    unittest.main()

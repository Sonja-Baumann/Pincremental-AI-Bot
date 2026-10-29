import tempfile
import unittest
from pathlib import Path

from learning import TimingLearner
from session_report import format_session_report


class SessionReportTests(unittest.TestCase):
    def test_formats_session_stats_and_best_learned_timing(self):
        learner = TimingLearner()
        learner.record_result("left", 400, 0.12, 3.5)
        learner.record_result("left", 400, 0.16, -1.0)
        learner.record_result("right", 800, 0.20, 2.0)

        report = format_session_report(
            dry_run=False,
            duration_seconds=3723,
            hits_gained=84,
            best_run_record=118,
            lifetime_record=245,
            hits_per_minute=25.5,
            actions=12,
            resolved_actions=9,
            learner=learner,
            log_path=Path("/tmp/bot-session.jsonl"),
        )

        self.assertIn("Mode: Live play", report)
        self.assertIn("Duration: 1h 02m 03s", report)
        self.assertIn("Hits gained: 84", report)
        self.assertIn("Best run record: 118", report)
        self.assertIn("Lifetime high score: 245", report)
        self.assertIn("left:medium: 0.12s lead, mean reward +3.50 (1 samples)", report)
        self.assertIn("Session log: /tmp/bot-session.jsonl", report)

    def test_reports_when_there_are_no_completed_policy_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            report = format_session_report(
                dry_run=True,
                duration_seconds=11,
                hits_gained=0,
                best_run_record=0,
                lifetime_record=245,
                hits_per_minute=0,
                actions=4,
                resolved_actions=0,
                learner=TimingLearner(),
                log_path=Path(directory) / "sessions.jsonl",
            )

        self.assertIn("Mode: Dry run", report)
        self.assertIn("No completed timing samples yet.", report)
        self.assertIn("Flipper suggestions: 4 (0 outcomes)", report)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from learning import ProgressTracker, TimingLearner
from pincremental_bot import _format_session_report, _update_progress


class ProgressIntegrationTests(unittest.TestCase):
    def test_progress_snapshot_updates_persisted_lifetime_stats(self):
        progress = ProgressTracker(best_lifetime_record=245, started_at=0)
        learner = TimingLearner()

        snapshot = _update_progress(progress, learner, 5, 9, 245, now=10)

        self.assertEqual(snapshot.lifetime_record, 245)
        self.assertEqual(learner.best_lifetime_record, 245)
        self.assertEqual(learner.best_hits_per_minute, snapshot.best_hits_per_minute)

    def test_session_report_shows_learned_timing_and_run_stats(self):
        learner = TimingLearner()
        learner.record_result("left", 400, 0.12, 3.5)
        report = _format_session_report(
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

        self.assertIn("Duration: 1h 02m 03s", report)
        self.assertIn("Hits gained: 84", report)
        self.assertIn("left:medium: 0.12s lead, mean reward +3.50 (1 samples)", report)


if __name__ == "__main__":
    unittest.main()

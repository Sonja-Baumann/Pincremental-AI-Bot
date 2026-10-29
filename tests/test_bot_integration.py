import unittest

from learning import ProgressTracker, TimingLearner
from pincremental_bot import _update_progress


class ProgressIntegrationTests(unittest.TestCase):
    def test_progress_snapshot_updates_persisted_lifetime_stats(self):
        progress = ProgressTracker(best_lifetime_record=245, started_at=0)
        learner = TimingLearner()

        snapshot = _update_progress(progress, learner, 5, 9, 245, now=10)

        self.assertEqual(snapshot.lifetime_record, 245)
        self.assertEqual(learner.best_lifetime_record, 245)
        self.assertEqual(learner.best_hits_per_minute, snapshot.best_hits_per_minute)


if __name__ == "__main__":
    unittest.main()

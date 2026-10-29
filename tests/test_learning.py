import unittest

from learning import (
    ActionOutcomeTracker,
    ProgressTracker,
    TimingLearner,
    velocity_bucket,
)


class VelocityBucketTests(unittest.TestCase):
    def test_buckets_speed_magnitude(self):
        self.assertEqual(velocity_bucket(120), "slow")
        self.assertEqual(velocity_bucket(-400), "medium")
        self.assertEqual(velocity_bucket(900), "fast")
        self.assertEqual(velocity_bucket(None), "unknown")


class TimingLearnerTests(unittest.TestCase):
    def test_left_and_right_contexts_keep_independent_samples(self):
        learner = TimingLearner()
        learner.record_result("left", 400, 0.12, 5.0)

        left = learner.summary("left", 400)
        right = learner.summary("right", 400)
        self.assertEqual(left["0.12"]["samples"], 1)
        self.assertEqual(right["0.12"]["samples"], 0)

    def test_untried_leads_are_explored_before_repeat(self):
        learner = TimingLearner()
        selected = [learner.choose_lead("left", 400) for _ in range(4)]
        self.assertEqual(set(selected), {0.08, 0.12, 0.16, 0.20})

    def test_summary_exposes_samples_and_uncertainty(self):
        learner = TimingLearner()
        learner.record_result("right", 800, 0.08, 1.0)
        summary = learner.summary("right", 800)["0.08"]
        self.assertEqual(summary["samples"], 1)
        self.assertGreater(summary["standard_error"], 0)
        self.assertIn("ucb", summary)

    def test_legacy_state_migrates_to_shared_prior_and_preserves_bests(self):
        learner = TimingLearner.from_dict({
            "leads": [0.08, 0.12, 0.16, 0.20],
            "trials": {"0.08": 20, "0.12": 1, "0.16": 0, "0.2": 0},
            "values": {"0.08": 3.5, "0.12": -1.0, "0.16": 0, "0.2": 0},
            "best_record": 245,
            "best_hits_per_minute": 31.5,
        })
        state = learner.to_dict()
        self.assertEqual(state["best_lifetime_record"], 245)
        self.assertEqual(state["best_hits_per_minute"], 31.5)
        self.assertEqual(learner.summary("left", 400)["0.08"]["prior_samples"], 5)
        # Legacy values act only as priors; no fake left/right observations.
        self.assertEqual(learner.summary("right", 800)["0.08"]["samples"], 0)


class ActionOutcomeTrackerTests(unittest.TestCase):
    def test_rebound_waits_to_collect_nearby_score_and_record(self):
        learner = TimingLearner()
        tracker = ActionOutcomeTracker(learner, settle_seconds=0.5)
        tracker.begin("a1", "left", 400, 0.12, hits=10, record=20,
                      lifetime_record=245, now=1.0)

        self.assertIsNone(tracker.observe(
            "a1", hits=11, record=21, lifetime_record=246, rebound=True, now=1.2
        ))
        result = tracker.observe(
            "a1", hits=13, record=22, lifetime_record=250,
            rebound=True, score_rate=30, now=1.6
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.score_delta, 3)
        self.assertEqual(result.record_delta, 2)
        self.assertEqual(result.lifetime_record_delta, 5)
        self.assertTrue(result.rebound)
        self.assertAlmostEqual(result.reward, 3 + 10 + 1 + 0.6)
        self.assertEqual(learner.summary("left", 400)["0.12"]["samples"], 1)

    def test_drain_penalizes_unrebounded_action_and_ignores_counter_reset(self):
        learner = TimingLearner()
        tracker = ActionOutcomeTracker(learner)
        tracker.begin("a2", "right", 700, 0.16, hits=20, record=40, now=2.0)
        result = tracker.observe("a2", hits=2, record=3, drained=True, now=2.2)
        self.assertEqual(result.score_delta, 0)
        self.assertEqual(result.record_delta, 0)
        self.assertEqual(result.reward, -3.0)
        self.assertTrue(result.drained)

    def test_unknown_action_observations_are_ignored(self):
        self.assertIsNone(ActionOutcomeTracker(TimingLearner()).observe("missing", now=1))


class ProgressTrackerTests(unittest.TestCase):
    def test_tracks_current_lifetime_and_session_rate_separately(self):
        tracker = ProgressTracker(best_lifetime_record=245, best_hits_per_minute=20, started_at=0)
        first = tracker.update(10, 12, lifetime_record=245, now=10)
        second = tracker.update(16, 15, lifetime_record=250, now=40)
        self.assertEqual(first.current_record, 12)
        self.assertEqual(second.current_record, 15)
        self.assertEqual(second.lifetime_record, 250)
        self.assertEqual(second.session_hits, 6)
        self.assertAlmostEqual(second.session_hits_per_minute, 9.0)
        self.assertEqual(second.best_hits_per_minute, 20)

    def test_counter_reset_does_not_subtract_prior_session_progress(self):
        tracker = ProgressTracker(started_at=0)
        tracker.update(10, 10, now=10)
        snapshot = tracker.update(0, 0, now=20)
        self.assertEqual(snapshot.session_hits, 0)
        later = tracker.update(3, 3, now=30)
        self.assertEqual(later.session_hits, 3)


if __name__ == "__main__":
    unittest.main()

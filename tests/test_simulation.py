import unittest

from simulation import (
    BOARD_HEIGHT,
    BOARD_WIDTH,
    BUMPER_POSITIONS,
    BUMPER_RADIUS,
    FLIPPER_ZONE_X,
    FLIPPER_ZONE_Y,
    GRAVITY,
    MAX_VELOCITY,
    SimulationConfig,
    TimingPolicy,
    compare_policies,
    scenario_from_seed,
    simulate_run,
)


class SimulationTests(unittest.TestCase):
    def test_constants_match_game_board_source(self):
        self.assertEqual((BOARD_WIDTH, BOARD_HEIGHT), (500.0, 800.0))
        self.assertEqual(GRAVITY, 0.75)
        self.assertEqual(MAX_VELOCITY, 26.0)
        self.assertEqual(BUMPER_RADIUS, 25.0)
        self.assertEqual(len(BUMPER_POSITIONS), 5)
        self.assertEqual(FLIPPER_ZONE_X, (150.0, 295.0))
        self.assertEqual(FLIPPER_ZONE_Y, (644.0, 690.0))

    def test_scenario_and_run_are_seed_deterministic(self):
        self.assertEqual(scenario_from_seed(42), scenario_from_seed(42))
        policy = TimingPolicy("lead-80ms", 0.08)
        self.assertEqual(simulate_run(42, policy), simulate_run(42, policy))

    def test_policy_comparison_reports_score_rate_and_action_metrics(self):
        policies = [
            TimingPolicy.no_flippers(),
            TimingPolicy("lead-40ms", 0.04),
            TimingPolicy("lead-200ms", 0.20),
        ]
        result = compare_policies(policies, range(10), SimulationConfig(balls=2))
        self.assertEqual(set(result), {item.name for item in policies})
        self.assertEqual(result["lead-40ms"].trials, 10)
        self.assertGreater(result["lead-40ms"].mean_high_score, result["no-flippers"].mean_high_score)
        self.assertGreater(result["lead-40ms"].mean_hits_per_minute, 0)
        self.assertGreater(result["lead-40ms"].mean_saves, result["lead-200ms"].mean_saves)

    def test_invalid_seed_set_and_lead_are_rejected(self):
        with self.assertRaises(ValueError):
            compare_policies([TimingPolicy.no_flippers()], [])
        with self.assertRaises(ValueError):
            compare_policies([TimingPolicy("negative", -0.1)], [1])

    def test_config_rejects_nonpositive_time_parameters(self):
        with self.assertRaises(ValueError):
            SimulationConfig(dt=0)
        with self.assertRaises(ValueError):
            SimulationConfig(action_hold_seconds=-1)


if __name__ == "__main__":
    unittest.main()

"""Deterministic, lightweight trajectory simulator for comparing flipper timing.

Board constants are copied from Pincremental's bundled ``Code/js/Pinball.js``
(Steam global version 1.107): 500x800 world, gravity 0.75 per engine tick,
velocity cap 26, radius-14 ball, five radius-25 bumpers, and the auto-paddle
activation zone x=(150, 295), y=(644, 690).

This is a policy-comparison harness, not a Matter.js replacement. It uses
semi-implicit Euler integration at 16 ms, circle bumper collisions, simple side
walls, and a fixed-duration flipper impulse. It omits Matter.js polygon paths,
slingshots, flipper angular dynamics, zone/upgrades multipliers, and the game's
exact reset/life logic. Results are reproducible and useful for rejecting poor
timing ideas, but they should not be read as exact in-game high scores.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable, Mapping


# Values mirrored from the installed game's Pinball.js source.
BOARD_WIDTH = 500.0
BOARD_HEIGHT = 800.0
GRAVITY = 0.75
MAX_VELOCITY = 26.0
BALL_RADIUS = 14.0
BUMPER_RADIUS = 25.0
BUMPER_RESTITUTION = 1.4
BUMPER_POSITIONS = ((105.0, 250.0), (225.0, 250.0), (345.0, 250.0), (165.0, 340.0), (285.0, 340.0))
FLIPPER_ZONE_X = (150.0, 295.0)
FLIPPER_ZONE_Y = (644.0, 690.0)
FLIPPER_TARGET_Y = 662.0
STEP_SECONDS = 0.016  # game's setInterval(..., 16)
FLIPPER_HOLD_SECONDS = 0.100  # bot key hold interval
DEFAULT_BALLS = 3
DEFAULT_MAX_STEPS_PER_BALL = 2500


@dataclass(frozen=True)
class TimingPolicy:
    """One policy tries a fixed lead time before the predicted flipper crossing."""

    name: str
    lead_seconds: float | None

    @classmethod
    def no_flippers(cls, name: str = "no-flippers") -> "TimingPolicy":
        return cls(name=name, lead_seconds=None)


@dataclass(frozen=True)
class Scenario:
    """A seeded initial trajectory; coordinates use the game's 500x800 world."""

    seed: int
    x: float
    y: float
    vx: float
    vy: float


@dataclass(frozen=True)
class SimulationConfig:
    balls: int = DEFAULT_BALLS
    max_steps_per_ball: int = DEFAULT_MAX_STEPS_PER_BALL
    dt: float = STEP_SECONDS
    action_hold_seconds: float = FLIPPER_HOLD_SECONDS

    def __post_init__(self) -> None:
        if self.balls < 1:
            raise ValueError("balls must be at least 1")
        if self.max_steps_per_ball < 1:
            raise ValueError("max_steps_per_ball must be at least 1")
        if self.dt <= 0 or self.action_hold_seconds <= 0:
            raise ValueError("dt and action_hold_seconds must be positive")


@dataclass(frozen=True)
class RunResult:
    seed: int
    policy: str
    hits: int
    high_score: int
    hits_per_minute: float
    elapsed_seconds: float
    balls_saved: int
    flipper_presses: int
    drained_balls: int
    timed_out_balls: int


@dataclass(frozen=True)
class PolicySummary:
    policy: str
    trials: int
    mean_high_score: float
    mean_hits_per_minute: float
    mean_saves: float
    mean_presses: float


def scenario_from_seed(seed: int) -> Scenario:
    """Create reproducible lower-board starting conditions for a seeded run."""
    rng = random.Random(seed)
    return Scenario(
        seed=seed,
        x=rng.uniform(165.0, 280.0),
        y=rng.uniform(485.0, 585.0),
        vx=rng.uniform(-4.0, 4.0),
        vy=rng.uniform(8.0, 14.0),
    )


def _cap_velocity(vx: float, vy: float) -> tuple[float, float]:
    return (
        max(-MAX_VELOCITY, min(MAX_VELOCITY, vx)),
        max(-MAX_VELOCITY, min(MAX_VELOCITY, vy)),
    )


def _seconds_to_flipper(y: float, vy: float, dt: float) -> float | None:
    """Predict time to target from downward velocity and constant gravity."""
    distance = FLIPPER_TARGET_Y - y
    if distance < 0 or vy <= 0:
        return None
    # Matter coordinates are discrete per engine tick; solve the discrete-time
    # distance approximation and convert ticks to wall-clock seconds.
    ticks = (-vy + math.sqrt(vy * vy + 2.0 * GRAVITY * distance)) / GRAVITY
    return ticks * dt


def _reflect_from_bumpers(
    x: float,
    y: float,
    vx: float,
    vy: float,
    previous_x: float,
    previous_y: float,
) -> tuple[float, float, float, float, int]:
    hits = 0
    min_distance = BALL_RADIUS + BUMPER_RADIUS
    for bumper_x, bumper_y in BUMPER_POSITIONS:
        dx = x - bumper_x
        dy = y - bumper_y
        distance = math.hypot(dx, dy)
        prev_distance = math.hypot(previous_x - bumper_x, previous_y - bumper_y)
        if distance >= min_distance or distance == 0 or prev_distance < min_distance:
            continue
        nx = dx / distance
        ny = dy / distance
        # Only count and bounce on a new inward collision.
        if vx * nx + vy * ny >= 0:
            continue
        x = bumper_x + nx * min_distance
        y = bumper_y + ny * min_distance
        normal_speed = vx * nx + vy * ny
        vx -= (1.0 + BUMPER_RESTITUTION) * normal_speed * nx
        vy -= (1.0 + BUMPER_RESTITUTION) * normal_speed * ny
        vx, vy = _cap_velocity(vx, vy)
        hits += 1
    return x, y, vx, vy, hits


def simulate_run(
    seed: int,
    policy: TimingPolicy,
    config: SimulationConfig = SimulationConfig(),
) -> RunResult:
    """Simulate one deterministic run with a small supply of balls/lives."""
    run_rng = random.Random(seed)
    total_hits = 0
    total_steps = 0
    balls_saved = 0
    presses = 0
    drained = 0
    timed_out = 0
    hold_ticks = max(1, math.ceil(config.action_hold_seconds / config.dt))

    for ball_index in range(config.balls):
        if ball_index == 0:
            scenario = scenario_from_seed(seed)
        else:
            # The in-game launcher has random vertical velocity; the simplified
            # model re-enters in the lower playfield to focus on flipper timing.
            sub_seed = run_rng.randrange(0, 2**32)
            scenario = scenario_from_seed(sub_seed)
        x, y, vx, vy = scenario.x, scenario.y, scenario.vx, scenario.vy
        active_until = -1
        action_side: str | None = None
        armed = True
        steps = 0
        drained_this_ball = False

        for step in range(config.max_steps_per_ball):
            steps += 1
            total_steps += 1

            if policy.lead_seconds is not None and armed and vy > 0:
                eta = _seconds_to_flipper(y, vy, config.dt)
                if eta is not None and eta <= policy.lead_seconds:
                    predicted_x = x + vx * max(0.0, eta) / config.dt
                    if FLIPPER_ZONE_X[0] <= predicted_x <= FLIPPER_ZONE_X[1]:
                        action_side = "left" if predicted_x < (FLIPPER_ZONE_X[0] + FLIPPER_ZONE_X[1]) / 2 else "right"
                        active_until = step + hold_ticks - 1
                        presses += 1
                    armed = False

            previous_x, previous_y = x, y
            vy += GRAVITY
            x += vx
            y += vy
            vx, vy = _cap_velocity(vx, vy)

            x, y, vx, vy, bumper_hits = _reflect_from_bumpers(x, y, vx, vy, previous_x, previous_y)
            total_hits += bumper_hits

            # Static outer walls; the detailed dome, aprons, and lanes are omitted.
            left_bound = BALL_RADIUS + 2.0
            right_bound = BOARD_WIDTH - BALL_RADIUS - 2.0
            top_bound = BALL_RADIUS + 20.0
            if x < left_bound:
                x = left_bound
                vx = abs(vx) * 0.8
            elif x > right_bound:
                x = right_bound
                vx = -abs(vx) * 0.8
            if y < top_bound:
                y = top_bound
                vy = abs(vy) * 0.8

            inside_x = FLIPPER_ZONE_X[0] <= x <= FLIPPER_ZONE_X[1]
            inside_y = FLIPPER_ZONE_Y[0] <= y <= FLIPPER_ZONE_Y[1]
            on_flipper = step <= active_until and action_side is not None
            if on_flipper and inside_x and inside_y and vy > 0 and y >= FLIPPER_TARGET_Y:
                side_mid = (FLIPPER_ZONE_X[0] + FLIPPER_ZONE_X[1]) / 2
                if (action_side == "left" and x < side_mid) or (action_side == "right" and x >= side_mid):
                    # Fixed impulse standing in for the game's rotating compound
                    # flipper; stronger detail belongs in a Matter.js harness.
                    vy = -min(MAX_VELOCITY, max(14.0, abs(vy) * 1.05 + 7.0))
                    vx = (-1.0 if action_side == "left" else 1.0) * min(5.0, max(2.0, abs(vx) * 0.25 + 1.0))
                    vx, vy = _cap_velocity(vx, vy)
                    balls_saved += 1
                    action_side = None
                    active_until = -1
                    armed = False

            if vy < 0 and y < FLIPPER_ZONE_Y[0] - 35:
                armed = True

            if y >= BOARD_HEIGHT - 20:
                drained_this_ball = True
                break

        if drained_this_ball:
            drained += 1
        else:
            timed_out += 1

    elapsed_seconds = total_steps * config.dt
    score_per_minute = total_hits * 60.0 / elapsed_seconds if elapsed_seconds else 0.0
    return RunResult(
        seed=seed,
        policy=policy.name,
        hits=total_hits,
        high_score=total_hits,
        hits_per_minute=score_per_minute,
        elapsed_seconds=elapsed_seconds,
        balls_saved=balls_saved,
        flipper_presses=presses,
        drained_balls=drained,
        timed_out_balls=timed_out,
    )


def compare_policies(
    policies: Iterable[TimingPolicy],
    seeds: Iterable[int],
    config: SimulationConfig = SimulationConfig(),
) -> dict[str, PolicySummary]:
    """Run each policy on the same seed set and return mean score/rate metrics."""
    seed_list = tuple(int(seed) for seed in seeds)
    if not seed_list:
        raise ValueError("at least one scenario seed is required")
    summaries: dict[str, PolicySummary] = {}
    for policy in policies:
        if policy.lead_seconds is not None and policy.lead_seconds < 0:
            raise ValueError("policy lead_seconds must be non-negative or None")
        results = [simulate_run(seed, policy, config) for seed in seed_list]
        summaries[policy.name] = PolicySummary(
            policy=policy.name,
            trials=len(results),
            mean_high_score=sum(item.high_score for item in results) / len(results),
            mean_hits_per_minute=sum(item.hits_per_minute for item in results) / len(results),
            mean_saves=sum(item.balls_saved for item in results) / len(results),
            mean_presses=sum(item.flipper_presses for item in results) / len(results),
        )
    return summaries


def format_comparison(summaries: Mapping[str, PolicySummary]) -> str:
    """Render a small deterministic text table for experiments and CLI output."""
    rows = ["policy                 trials   mean high score   hits/min   saves   presses"]
    for name, result in summaries.items():
        rows.append(
            f"{name:<22} {result.trials:>6} {result.mean_high_score:>17.2f} "
            f"{result.mean_hits_per_minute:>10.2f} {result.mean_saves:>7.2f} {result.mean_presses:>9.2f}"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    policies = [
        TimingPolicy.no_flippers(),
        TimingPolicy("lead-40ms", 0.04),
        TimingPolicy("lead-80ms", 0.08),
        TimingPolicy("lead-120ms", 0.12),
        TimingPolicy("lead-200ms", 0.20),
    ]
    print(format_comparison(compare_policies(policies, range(100))))

"""Timing policy, action credit assignment, and run/lifetime statistics.

Bot integration API::

    learner = TimingLearner.from_dict(saved_state)
    lead = learner.choose_lead("left", speed=520.0)
    outcomes = ActionOutcomeTracker(learner, logger=session_log)
    outcomes.begin("action-1", "left", 520, lead, hits=12, record=20)
    result = outcomes.observe("action-1", hits=13, record=21, rebound=True)
    saved_state = learner.to_dict()

The tracker waits briefly after a save so later hit/record changes can still be
credited to the action. A drain ends attribution immediately; a missing result
times out with a penalty. Left/right and slow/medium/fast approaches are
independent policy contexts. Old v1 ``trials``/``values`` saves migrate to a
small shared prior instead of being discarded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import random
import time
from typing import Any, Mapping


DEFAULT_LEADS = (0.08, 0.12, 0.16, 0.20)
SPEED_BUCKETS = ((250.0, "slow"), (600.0, "medium"), (math.inf, "fast"))
STATE_VERSION = 2


def velocity_bucket(speed: float | None) -> str:
    """Return a stable speed bucket for the magnitude of the incoming velocity."""
    if speed is None or not math.isfinite(float(speed)):
        return "unknown"
    magnitude = abs(float(speed))
    for ceiling, name in SPEED_BUCKETS:
        if magnitude < ceiling:
            return name
    return "fast"


def _validate_side(side: str) -> str:
    normalized = str(side).lower()
    if normalized not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    return normalized


def _lead_key(lead: float) -> str:
    return format(float(lead), ".4f").rstrip("0").rstrip(".")


@dataclass
class ArmStats:
    """Online mean/variance plus a small, non-counted legacy prior."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    prior_count: int = 0
    prior_mean: float = 0.0

    @property
    def effective_count(self) -> int:
        return self.count + self.prior_count

    @property
    def effective_mean(self) -> float:
        n = self.effective_count
        if n == 0:
            return 0.0
        return (self.count * self.mean + self.prior_count * self.prior_mean) / n

    @property
    def variance(self) -> float:
        if self.count > 1:
            return max(0.0, self.m2 / (self.count - 1))
        return 1.0

    @property
    def standard_error(self) -> float:
        n = max(1, self.effective_count)
        return math.sqrt(self.variance / n)

    def add(self, reward: float) -> None:
        self.count += 1
        delta = float(reward) - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (float(reward) - self.mean)

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArmStats:
        return cls(
            count=max(0, int(value.get("count", 0))),
            mean=float(value.get("mean", 0.0)),
            m2=max(0.0, float(value.get("m2", 0.0))),
            prior_count=max(0, int(value.get("prior_count", 0))),
            prior_mean=float(value.get("prior_mean", 0.0)),
        )


class TimingLearner:
    """Contextual upper-confidence-bound policy over flipper press leads.

    ``speed`` is the incoming velocity magnitude in game units per second.
    ``choose_lead`` returns seconds before the predicted flipper crossing.
    """

    def __init__(
        self,
        leads: tuple[float, ...] = DEFAULT_LEADS,
        *,
        exploration: float = 1.25,
        rng: random.Random | None = None,
    ) -> None:
        if not leads or any(float(lead) <= 0 for lead in leads):
            raise ValueError("leads must contain positive timing values")
        self.leads = tuple(float(lead) for lead in leads)
        self.exploration = float(exploration)
        self._rng = rng or random.Random()
        self._contexts: dict[str, dict[str, ArmStats]] = {}
        self._legacy_prior: dict[str, ArmStats] = {}
        self._selections: dict[str, int] = {}
        self.best_lifetime_record = 0
        self.best_hits_per_minute = 0.0

    @staticmethod
    def context_key(side: str, speed: float | None) -> str:
        return f"{_validate_side(side)}:{velocity_bucket(speed)}"

    def _arms(self, side: str, speed: float | None) -> tuple[str, dict[str, ArmStats]]:
        context = self.context_key(side, speed)
        if context not in self._contexts:
            self._contexts[context] = {
                _lead_key(lead): ArmStats(
                    prior_count=self._legacy_prior.get(_lead_key(lead), ArmStats()).prior_count,
                    prior_mean=self._legacy_prior.get(_lead_key(lead), ArmStats()).prior_mean,
                )
                for lead in self.leads
            }
        return context, self._contexts[context]

    def choose_lead(self, side: str, speed: float | None) -> float:
        """Choose a lead; seed exploration evenly before applying UCB scores."""
        context, arms = self._arms(side, speed)
        untried = [lead for lead in self.leads if arms[_lead_key(lead)].count == 0]
        # Evenly sample unseen arms, rather than repeatedly selecting the first.
        if untried:
            cursor = self._selections.get(context, 0) % len(untried)
            lead = untried[cursor]
            self._selections[context] = self._selections.get(context, 0) + 1
            return lead

        total = sum(arm.effective_count for arm in arms.values())
        log_total = math.log(max(2, total))
        scores = {
            lead: arms[_lead_key(lead)].effective_mean
            + self.exploration * math.sqrt(log_total / max(1, arms[_lead_key(lead)].effective_count))
            for lead in self.leads
        }
        best_score = max(scores.values())
        best = [lead for lead, score in scores.items() if math.isclose(score, best_score, rel_tol=1e-12)]
        return self._rng.choice(best)

    def record_result(self, side: str, speed: float | None, lead: float, reward: float) -> ArmStats:
        """Update the contextual arm with one fully attributed action reward."""
        _, arms = self._arms(side, speed)
        actual_lead = min(self.leads, key=lambda candidate: abs(candidate - float(lead)))
        stats = arms[_lead_key(actual_lead)]
        stats.add(float(reward))
        return stats

    def summary(self, side: str, speed: float | None) -> dict[str, dict[str, float | int]]:
        """Expose means, real sample counts, uncertainty, and UCB for the UI."""
        _, arms = self._arms(side, speed)
        total = sum(arm.effective_count for arm in arms.values())
        log_total = math.log(max(2, total))
        output: dict[str, dict[str, float | int]] = {}
        for lead in self.leads:
            arm = arms[_lead_key(lead)]
            ucb = arm.effective_mean + self.exploration * math.sqrt(
                log_total / max(1, arm.effective_count)
            )
            output[_lead_key(lead)] = {
                "samples": arm.count,
                "prior_samples": arm.prior_count,
                "mean_reward": arm.effective_mean,
                "standard_error": arm.standard_error,
                "ucb": ucb,
            }
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "leads": list(self.leads),
            "exploration": self.exploration,
            "contexts": {
                context: {lead: stats.to_dict() for lead, stats in arms.items()}
                for context, arms in self._contexts.items()
            },
            "legacy_prior": {lead: stats.to_dict() for lead, stats in self._legacy_prior.items()},
            "selections": self._selections.copy(),
            "best_lifetime_record": self.best_lifetime_record,
            "best_hits_per_minute": self.best_hits_per_minute,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> TimingLearner:
        value = value or {}
        raw_leads = value.get("leads", DEFAULT_LEADS)
        try:
            leads = tuple(float(item) for item in raw_leads)
            if not leads:
                leads = DEFAULT_LEADS
        except (TypeError, ValueError):
            leads = DEFAULT_LEADS
        learner = cls(leads=leads, exploration=float(value.get("exploration", 1.25)))
        contexts = value.get("contexts")
        if isinstance(contexts, Mapping):
            for context, raw_arms in contexts.items():
                if not isinstance(raw_arms, Mapping):
                    continue
                learner._contexts[str(context)] = {
                    _lead_key(lead): ArmStats.from_dict(stats)
                    for lead, stats in raw_arms.items()
                    if _lead_key(float(lead)) in {_lead_key(item) for item in learner.leads}
                    and isinstance(stats, Mapping)
                }
        else:
            # Migrate the bot's v1 global table into low-weight priors. They seed
            # each new context but do not masquerade as left/right observations.
            old_trials = value.get("trials", {})
            old_values = value.get("values", {})
            if isinstance(old_trials, Mapping) and isinstance(old_values, Mapping):
                for lead in learner.leads:
                    key = _lead_key(lead)
                    legacy_keys = (key, str(lead), format(lead, "g"))
                    n = next((old_trials[k] for k in legacy_keys if k in old_trials), 0)
                    mean = next((old_values[k] for k in legacy_keys if k in old_values), 0.0)
                    try:
                        count = max(0, min(5, int(n)))
                        learner._legacy_prior[key] = ArmStats(
                            prior_count=count,
                            prior_mean=float(mean),
                        )
                    except (TypeError, ValueError):
                        continue
        raw_prior = value.get("legacy_prior")
        if isinstance(raw_prior, Mapping):
            learner._legacy_prior = {
                _lead_key(float(lead)): ArmStats.from_dict(stats)
                for lead, stats in raw_prior.items()
                if isinstance(stats, Mapping)
            }
        selections = value.get("selections", {})
        if isinstance(selections, Mapping):
            learner._selections = {str(k): max(0, int(v)) for k, v in selections.items()}
        learner.best_lifetime_record = max(
            0,
            int(value.get("best_lifetime_record", value.get("best_record", 0)) or 0),
        )
        learner.best_hits_per_minute = max(
            0.0,
            float(value.get("best_hits_per_minute", 0.0) or 0.0),
        )
        return learner


@dataclass(frozen=True)
class ActionOutcome:
    action_id: str
    side: str
    speed: float | None
    speed_bucket: str
    lead: float
    reward: float
    score_delta: int
    record_delta: int
    lifetime_record_delta: int
    rebound: bool
    drained: bool
    duration_seconds: float
    hits_per_minute: float | None


@dataclass
class _PendingAction:
    action_id: str
    side: str
    speed: float | None
    lead: float
    hits_before: int | None
    record_before: int | None
    lifetime_record_before: int | None
    started_at: float
    hits_latest: int | None
    record_latest: int | None
    lifetime_record_latest: int | None
    score_rate: float | None = None
    rebound: bool = False


class ActionOutcomeTracker:
    """Credit nearby score/record changes and a rebound to a particular press.

    Call ``begin`` when a paddle is pressed. Feed observations for that
    ``action_id`` while the ball approaches/rebounds. A rebound is finalized
    after ``settle_seconds`` so a hit or record update immediately after the
    save remains attributable. A drain finalizes immediately; otherwise the
    action times out with a configurable penalty.
    """

    def __init__(
        self,
        learner: TimingLearner,
        *,
        logger: Any = None,
        score_weight: float = 1.0,
        record_bonus: float = 10.0,
        save_bonus: float = 1.0,
        drain_penalty: float = 3.0,
        timeout_penalty: float = 2.0,
        rate_weight: float = 0.02,
        settle_seconds: float = 0.70,
        timeout_seconds: float = 1.60,
    ) -> None:
        self.learner = learner
        self.logger = logger
        self.score_weight = float(score_weight)
        self.record_bonus = float(record_bonus)
        self.save_bonus = float(save_bonus)
        self.drain_penalty = abs(float(drain_penalty))
        self.timeout_penalty = abs(float(timeout_penalty))
        self.rate_weight = float(rate_weight)
        self.settle_seconds = float(settle_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.pending: dict[str, _PendingAction] = {}

    def begin(
        self,
        action_id: str,
        side: str,
        speed: float | None,
        lead: float,
        hits: int | None,
        record: int | None,
        lifetime_record: int | None = None,
        *,
        now: float | None = None,
    ) -> None:
        if action_id in self.pending:
            raise ValueError(f"action id already pending: {action_id}")
        side = _validate_side(side)
        started_at = time.monotonic() if now is None else float(now)
        action = _PendingAction(
            action_id=str(action_id), side=side, speed=None if speed is None else float(speed),
            lead=float(lead), hits_before=hits, record_before=record,
            lifetime_record_before=lifetime_record, started_at=started_at,
            hits_latest=hits, record_latest=record, lifetime_record_latest=lifetime_record,
        )
        self.pending[action.action_id] = action
        if self.logger is not None:
            self.logger.action(
                action_id=action.action_id, side=side, speed=action.speed,
                speed_bucket=velocity_bucket(action.speed), lead=action.lead,
                hits_before=hits, record_before=record, lifetime_record_before=lifetime_record,
                monotonic_time=started_at,
            )

    def observe(
        self,
        action_id: str,
        *,
        hits: int | None = None,
        record: int | None = None,
        lifetime_record: int | None = None,
        rebound: bool = False,
        drained: bool = False,
        score_rate: float | None = None,
        now: float | None = None,
    ) -> ActionOutcome | None:
        action_id = str(action_id)
        action = self.pending.get(action_id)
        if action is None:
            return None
        current = time.monotonic() if now is None else float(now)
        if hits is not None:
            if action.hits_latest is None or hits >= action.hits_latest:
                action.hits_latest = int(hits)
        if record is not None:
            if action.record_latest is None or record >= action.record_latest:
                action.record_latest = int(record)
        if lifetime_record is not None:
            if action.lifetime_record_latest is None or lifetime_record >= action.lifetime_record_latest:
                action.lifetime_record_latest = int(lifetime_record)
        action.rebound = action.rebound or bool(rebound)
        if score_rate is not None and math.isfinite(float(score_rate)):
            action.score_rate = max(0.0, float(score_rate))

        age = max(0.0, current - action.started_at)
        should_finish = bool(drained) or (action.rebound and age >= self.settle_seconds) or age >= self.timeout_seconds
        if not should_finish:
            return None

        score_delta = max(0, (action.hits_latest or 0) - (action.hits_before or 0))
        record_delta = max(0, (action.record_latest or 0) - (action.record_before or 0))
        lifetime_record_delta = max(
            0,
            (action.lifetime_record_latest or 0) - (action.lifetime_record_before or 0),
        )
        reward = self.score_weight * score_delta
        if record_delta or lifetime_record_delta:
            reward += self.record_bonus
        if action.rebound:
            reward += self.save_bonus
        elif drained:
            reward -= self.drain_penalty
        else:
            reward -= self.timeout_penalty
        if action.score_rate is not None:
            reward += self.rate_weight * action.score_rate

        stats = self.learner.record_result(action.side, action.speed, action.lead, reward)
        outcome = ActionOutcome(
            action_id=action.action_id, side=action.side, speed=action.speed,
            speed_bucket=velocity_bucket(action.speed), lead=action.lead,
            reward=reward, score_delta=score_delta, record_delta=record_delta,
            lifetime_record_delta=lifetime_record_delta,
            rebound=action.rebound, drained=bool(drained), duration_seconds=age,
            hits_per_minute=action.score_rate,
        )
        del self.pending[action.action_id]
        if self.logger is not None:
            self.logger.outcome(**asdict(outcome), samples=stats.count, mean_reward=stats.effective_mean)
        return outcome

    def expire(self, *, now: float | None = None) -> list[ActionOutcome]:
        """Finalize timed-out actions; convenient when a ball/action disappears."""
        current = time.monotonic() if now is None else float(now)
        finished: list[ActionOutcome] = []
        for action_id, action in list(self.pending.items()):
            if current - action.started_at >= self.timeout_seconds:
                outcome = self.observe(action_id, now=current)
                if outcome is not None:
                    finished.append(outcome)
        return finished


@dataclass(frozen=True)
class ProgressSnapshot:
    current_hits: int
    current_record: int
    lifetime_record: int
    session_hits: int
    session_hits_per_minute: float
    best_hits_per_minute: float


class ProgressTracker:
    """Track current screen counters separately from persisted lifetime bests."""

    def __init__(
        self,
        *,
        best_lifetime_record: int = 0,
        best_hits_per_minute: float = 0.0,
        started_at: float | None = None,
    ) -> None:
        self.best_lifetime_record = max(0, int(best_lifetime_record))
        self.best_hits_per_minute = max(0.0, float(best_hits_per_minute))
        self.started_at = time.monotonic() if started_at is None else float(started_at)
        self.last_hits: int | None = None
        self.session_hits = 0
        self.current_hits = 0
        self.current_record = 0
        self.lifetime_record = self.best_lifetime_record

    def update(
        self,
        hits: int,
        current_record: int,
        lifetime_record: int | None = None,
        *,
        now: float | None = None,
    ) -> ProgressSnapshot:
        current_time = time.monotonic() if now is None else float(now)
        hits = max(0, int(hits))
        current_record = max(0, int(current_record))
        if self.last_hits is not None and hits >= self.last_hits:
            self.session_hits += hits - self.last_hits
        self.last_hits = hits
        self.current_hits = hits
        self.current_record = current_record
        if lifetime_record is not None:
            self.lifetime_record = max(self.lifetime_record, max(0, int(lifetime_record)))
        self.best_lifetime_record = max(self.best_lifetime_record, self.lifetime_record)
        elapsed_minutes = max(1.0 / 60.0, current_time - self.started_at) / 60.0
        rate = self.session_hits / elapsed_minutes
        self.best_hits_per_minute = max(self.best_hits_per_minute, rate)
        return ProgressSnapshot(
            current_hits=hits,
            current_record=current_record,
            lifetime_record=self.lifetime_record,
            session_hits=self.session_hits,
            session_hits_per_minute=rate,
            best_hits_per_minute=self.best_hits_per_minute,
        )

    def to_dict(self) -> dict[str, int | float]:
        return {
            "version": STATE_VERSION,
            "best_lifetime_record": self.best_lifetime_record,
            "best_hits_per_minute": self.best_hits_per_minute,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None, *, started_at: float | None = None) -> ProgressTracker:
        value = value or {}
        return cls(
            best_lifetime_record=int(value.get("best_lifetime_record", value.get("best_record", 0)) or 0),
            best_hits_per_minute=float(value.get("best_hits_per_minute", 0.0) or 0.0),
            started_at=started_at,
        )

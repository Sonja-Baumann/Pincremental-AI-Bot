"""Human-readable summary of one bot run and its learned timing results."""

from __future__ import annotations

from pathlib import Path
from learning import TimingLearner


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _best_context_timings(learner: TimingLearner, limit: int = 3) -> list[str]:
    contexts = learner.to_dict().get("contexts", {})
    ranked: list[tuple[float, str, str, int]] = []
    if not isinstance(contexts, dict):
        return []
    for context, arms in contexts.items():
        if not isinstance(arms, dict):
            continue
        observed = [
            (str(lead), arm)
            for lead, arm in arms.items()
            if isinstance(arm, dict) and int(arm.get("count", 0)) > 0
        ]
        if not observed:
            continue
        lead, arm = max(observed, key=lambda item: float(item[1].get("mean", 0.0)))
        ranked.append((float(arm.get("mean", 0.0)), str(context), lead, int(arm["count"])))
    ranked.sort(reverse=True)
    return [
        f"{context}: {lead}s lead, mean reward {mean:+.2f} ({count} samples)"
        for mean, context, lead, count in ranked[:limit]
    ]


def format_session_report(
    *,
    dry_run: bool,
    duration_seconds: float,
    hits_gained: int,
    best_run_record: int,
    lifetime_record: int,
    hits_per_minute: float,
    actions: int,
    resolved_actions: int,
    learner: TimingLearner,
    log_path: str | Path,
) -> str:
    """Format session progress and the highest-reward learned timing per context."""
    lines = [
        "",
        "Session report",
        f"Mode: {'Dry run' if dry_run else 'Live play'}",
        f"Duration: {_duration(duration_seconds)}",
        f"Hits gained: {max(0, int(hits_gained))}",
        f"Best run record: {max(0, int(best_run_record))}",
        f"Session rate: {max(0.0, float(hits_per_minute)):.1f} hits/min",
        f"Lifetime high score: {max(0, int(lifetime_record))}",
        f"Flipper suggestions: {max(0, int(actions))} ({max(0, int(resolved_actions))} outcomes)",
        "Best learned timing by context (mean reward; cumulative):",
    ]
    timings = _best_context_timings(learner)
    if timings:
        lines.extend(f"  {entry}" for entry in timings)
    else:
        lines.append("  No completed timing samples yet.")
    lines.append(f"Session log: {Path(log_path).expanduser()}")
    return "\n".join(lines)

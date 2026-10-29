#!/usr/bin/env python3
"""Control Pincremental through its loopback-only Electron debugging interface."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cdp_client import (
    CDPClientError,
    CDPDisconnected,
    CDPTimeout,
    GameInterface,
    TargetNotFound,
    connect_pincremental,
)
from config import BotConfig, UPGRADE_STRATEGIES
from learning import ActionOutcomeTracker, ProgressSnapshot, ProgressTracker, TimingLearner
from session_log import JsonlSessionLogger


TARGET_Y = 662.0
RELEASE_SECONDS = 0.10
ACTION_COOLDOWN = 0.28
SAMPLE_SLEEP = 0.015
BALL_DRAIN_Y = 790.0


def _duration_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_session_report(
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
    log_path: Path,
) -> str:
    """Summarize this run and the highest-reward learned timing per context."""
    lines = [
        "",
        "Session report",
        f"Mode: {'Dry run' if dry_run else 'Live play'}",
        f"Duration: {_duration_text(duration_seconds)}",
        f"Hits gained: {max(0, int(hits_gained))}",
        f"Best run record: {max(0, int(best_run_record))}",
        f"Session rate: {max(0.0, float(hits_per_minute)):.1f} hits/min",
        f"Lifetime high score: {max(0, int(lifetime_record))}",
        f"Flipper suggestions: {max(0, int(actions))} ({max(0, int(resolved_actions))} outcomes)",
        "Best learned timing by context (mean reward; cumulative):",
    ]
    contexts = learner.to_dict().get("contexts", {})
    ranked: list[tuple[float, str, str, int]] = []
    if isinstance(contexts, dict):
        for context, arms in contexts.items():
            if not isinstance(arms, dict):
                continue
            observed = [
                (str(lead), arm)
                for lead, arm in arms.items()
                if isinstance(arm, dict) and int(arm.get("count", 0)) > 0
            ]
            if observed:
                lead, arm = max(observed, key=lambda item: float(item[1].get("mean", 0.0)))
                ranked.append((float(arm.get("mean", 0.0)), str(context), lead, int(arm["count"])))
    ranked.sort(reverse=True)
    if ranked:
        lines.extend(
            f"  {context}: {lead}s lead, mean reward {mean:+.2f} ({count} samples)"
            for mean, context, lead, count in ranked[:3]
        )
    else:
        lines.append("  No completed timing samples yet.")
    lines.append(f"Session log: {log_path.expanduser()}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Learn flipper timing and play Pincremental")
    parser.add_argument("--config", type=Path, help="JSON config file (defaults to XDG config path)")
    parser.add_argument("--app-id", help="Steam application ID")
    parser.add_argument("--port", type=int, help="local Electron debugging port")
    parser.add_argument("--state", type=Path, help="learning state JSON path")
    parser.add_argument("--log", type=Path, help="session JSONL log path")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                        help="observe and recommend paddle actions without pressing them")
    parser.add_argument("--play", dest="dry_run", action="store_false",
                        help="override config and enable paddle control")
    upgrades = parser.add_mutually_exclusive_group()
    upgrades.add_argument("--auto-upgrades", dest="auto_upgrades", action="store_true",
                          default=None, help="buy affordable upgrades across supported game shops")
    upgrades.add_argument("--no-auto-upgrades", dest="auto_upgrades", action="store_false",
                          default=None, help="disable automatic upgrade purchases")
    parser.add_argument("--upgrade-strategy", choices=sorted(UPGRADE_STRATEGIES),
                        help="priority order for automatic purchases across game shops")
    parser.add_argument("--no-launch", action="store_true",
                        help="connect only; do not launch the game through Steam")
    parser.add_argument("--no-ui", action="store_true", help="run without the status window")
    parser.add_argument("--connect-timeout", type=float, default=45.0,
                        help="seconds to wait for Steam's game page after launch")
    return parser


def _load_learning(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read learning state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Learning state must be a JSON object: {path}")
    return value


def _save_learning(path: Path, learner: TimingLearner) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(learner.to_dict(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _game_running() -> bool:
    """Return whether a Pincremental executable appears in this user's proc list."""
    try:
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", "ignore"
                ).lower()
            except OSError:
                continue
            if "pincremental.exe" in command:
                return True
    except OSError:
        return False
    return False


def build_steam_command(executable: str, app_id: str, port: int) -> list[str]:
    """Build the Steam invocation used to enable this Electron bot session."""
    if not str(app_id).isdigit():
        raise ValueError("Steam app ID must contain only digits")
    if not 1 <= int(port) <= 65535:
        raise ValueError("debug port must be between 1 and 65535")
    return [
        executable,
        "-applaunch",
        str(app_id),
        f"--remote-debugging-port={int(port)}",
        "--remote-allow-origins=*",
    ]


def _launch_steam(app_id: str, port: int) -> None:
    steam = shutil.which("steam")
    if not steam:
        raise RuntimeError("Steam command was not found on PATH. Start Steam, then run the bot again.")
    command = build_steam_command(steam, app_id, port)
    print(f"Launching Steam app {app_id} with local game-state access.", flush=True)
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _connect_or_launch(config: BotConfig, *, no_launch: bool, timeout: float) -> GameInterface:
    try:
        return connect_pincremental(port=config.debug_port, timeout=0.8)
    except TargetNotFound:
        if no_launch:
            raise RuntimeError(
                f"No Pincremental page is exposed on 127.0.0.1:{config.debug_port}; "
                "remove --no-launch to start it through Steam."
            )

    if _game_running():
        print("Pincremental is open without the bot interface.")
        print("Close only the game window, then press Enter here. The bot will relaunch it.")
        if not sys.stdin.isatty():
            raise RuntimeError("Close Pincremental and rerun the bot so Steam can enable its local interface.")
        input()
        deadline = time.monotonic() + 120.0
        while _game_running() and time.monotonic() < deadline:
            time.sleep(0.5)
        if _game_running():
            raise RuntimeError("Pincremental is still running; close its window and try again.")
        time.sleep(2.1)  # Pincremental autosaves every two seconds.

    _launch_steam(config.app_id, config.debug_port)
    try:
        return connect_pincremental(port=config.debug_port, timeout=timeout)
    except TargetNotFound as exc:
        raise RuntimeError(
            f"Steam did not expose a Pincremental page on 127.0.0.1:{config.debug_port} "
            f"within {timeout:g}s. Check the Steam launch and game version. ({exc})"
        ) from exc


def _recover_connection(client: GameInterface, logger: JsonlSessionLogger, error: Exception) -> None:
    """Release controls best-effort and reattach to a restarted Electron page."""
    try:
        client.release_all()
    except CDPClientError:
        pass
    logger.log("connection_lost", error=str(error))
    client.reconnect(timeout=20.0)
    client.dismiss_known_popup()
    client.disable_auto_paddles()
    logger.log("reconnected", target=client.target_info)


def _ui_snapshot(
    *,
    status: str,
    ball: dict[str, float] | None,
    velocity: dict[str, float] | None,
    hits: int | None,
    current_record: int | None,
    lifetime_record: int,
    score_rate: float,
    action: str,
    lead: float | None,
    learner: TimingLearner,
    side: str,
    speed: float | None,
    paused: bool,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "ball": ball,
        "velocity": velocity,
        "hits": hits,
        "run_record": current_record,
        "lifetime_record": lifetime_record,
        "score_per_minute": score_rate,
        "action": action,
        "lead": lead,
        "timings": learner.summary(side, speed),
        "paused": paused,
        "dry_run": dry_run,
    }


def _update_progress(
    progress: ProgressTracker,
    learner: TimingLearner,
    hits: int,
    current_record: int,
    lifetime_record: int,
    *,
    now: float,
) -> ProgressSnapshot:
    """Update session counters and copy the tracked lifetime best to learning state."""
    snapshot = progress.update(hits, current_record, lifetime_record, now=now)
    learner.best_lifetime_record = snapshot.lifetime_record
    learner.best_hits_per_minute = snapshot.best_hits_per_minute
    return snapshot


def run_bot(
    *,
    config: BotConfig,
    no_launch: bool = False,
    launch_timeout: float = 45.0,
    no_ui: bool = False,
) -> None:
    config.ensure_directories()
    config.save()
    learner = TimingLearner.from_dict(_load_learning(config.state_path))
    progress = ProgressTracker.from_dict(learner.to_dict())
    logger = JsonlSessionLogger(config.log_path)
    ui: StatusWindow | None = None
    client: GameInterface | None = None
    last_sample: dict[str, Any] = {}
    session_start: float | None = None
    dry_run = config.dry_run
    last_rate = 0.0
    best_run_record = 0
    total_actions = 0
    resolved_actions = 0
    close_game_requested = False
    try:
        client = _connect_or_launch(config, no_launch=no_launch, timeout=launch_timeout)
        hook_deadline = time.monotonic() + 20
        while not client.install_hook():
            if time.monotonic() >= hook_deadline:
                raise RuntimeError("Pincremental's physics page did not finish loading the bot hook.")
            time.sleep(0.2)
        popup = client.dismiss_known_popup()
        if popup:
            logger.log("popup_dismissed", title=popup)
            print(f"Dismissed {popup} popup.")
        client.disable_auto_paddles()
        client.release_all()

        if not no_ui:
            try:
                from bot_ui import StatusWindow

                ui = StatusWindow(dry_run=config.dry_run)
            except (ImportError, RuntimeError) as exc:
                print(f"Status window unavailable; using terminal controls: {exc}")

        outcomes = ActionOutcomeTracker(learner, logger=logger)
        paused = False
        stop_requested = False
        previous: tuple[float, float, float] | None = None
        last_ball_log = 0.0
        last_status = 0.0
        last_action = 0.0
        last_upgrade_check = 0.0
        action_lead: float | None = None
        planned_action: tuple[str, float, float] | None = None
        action_label = "observing"
        armed = True
        last_key: str | None = None
        release_at = 0.0
        pending_actions: dict[str, float] = {}
        session_start = time.monotonic()
        last_sample: dict[str, Any] = {}
        last_side = "left"
        last_speed: float | None = None
        reconnect_delay = 1.0
        print("Connected to Pincremental. Window position and size are yours to choose.")
        print(
            "Mode: DRY RUN (observing only; no paddles will be pressed)."
            if dry_run else "Mode: LIVE PLAY (the bot will press paddles).",
            flush=True,
        )
        print("Use the status window to pause, emergency-stop, or toggle dry-run; Ctrl+C also stops.")
        logger.log("session_ready", target=client.target_info, dry_run=dry_run)
        logger.log(
            "upgrade_automation",
            enabled=config.auto_upgrades,
            strategy=config.upgrade_strategy,
        )
        if config.auto_upgrades:
            print(
                f"Automatic machine upgrades enabled ({config.upgrade_strategy}); "
                "this setting is independent of paddle control.",
                flush=True,
            )
        else:
            print("Automatic machine upgrades are disabled.", flush=True)

        while not stop_requested:
            now = time.monotonic()
            if ui:
                for command in ui.poll_commands():
                    if command == "pause_toggle":
                        paused = not paused
                        action_label = "paused" if paused else "resumed"
                        if paused:
                            client.release_all()
                            last_key = None
                    elif command == "emergency_stop" or command == "stop":
                        try:
                            client.release_all()
                        except CDPClientError:
                            pass
                        last_key = None
                        stop_requested = True
                        action_label = "emergency stop" if command == "emergency_stop" else "stopped"
                    elif command.startswith("dry_run:"):
                        dry_run = command.split(":", 1)[1].strip().lower() == "true"
                        config = replace(config, dry_run=dry_run)
                        config.save()
                        action_label = "dry run on" if dry_run else "live control on"

            if stop_requested:
                break

            if config.auto_upgrades and not paused and now - last_upgrade_check >= 3.0:
                last_upgrade_check = now
                try:
                    bought_upgrade = client.buy_available_upgrade(config.upgrade_strategy)
                except (CDPDisconnected, CDPTimeout) as exc:
                    last_key = None
                    action_label = "reconnecting"
                    print(f"Game connection lost: {exc}; reconnecting…", flush=True)
                    try:
                        _recover_connection(client, logger, exc)
                        reconnect_delay = 1.0
                        previous = None
                        print("Reconnected to Pincremental.", flush=True)
                    except CDPClientError as reconnect_error:
                        logger.log("reconnect_failed", error=str(reconnect_error))
                        time.sleep(reconnect_delay)
                        reconnect_delay = min(10.0, reconnect_delay * 1.8)
                    continue
                if bought_upgrade:
                    logger.log(
                        "upgrade_purchased",
                        store=bought_upgrade["store"],
                        upgrade=bought_upgrade["upgrade"],
                        strategy=config.upgrade_strategy,
                    )
                    print(
                        f"Purchased {bought_upgrade['store']} upgrade "
                        f"{bought_upgrade['upgrade']}.",
                        flush=True,
                    )
                    action_label = f"upgrade {bought_upgrade['upgrade']}"

            try:
                sample = client.read_state()
            except (CDPDisconnected, CDPTimeout) as exc:
                last_key = None
                action_label = "reconnecting"
                print(f"Game connection lost: {exc}; reconnecting…", flush=True)
                try:
                    _recover_connection(client, logger, exc)
                    reconnect_delay = 1.0
                    previous = None
                    print("Reconnected to Pincremental.", flush=True)
                except CDPClientError as reconnect_error:
                    logger.log("reconnect_failed", error=str(reconnect_error))
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(10.0, reconnect_delay * 1.8)
                continue

            if sample is None or sample.get("ball") is None:
                if ui:
                    ui.update(_ui_snapshot(
                        status="Waiting for ball",
                        ball=None,
                        velocity=None,
                        hits=None,
                        current_record=None,
                        lifetime_record=progress.best_lifetime_record,
                        score_rate=last_rate,
                        action="clearing popup" if sample and sample.get("popup_visible") else "waiting",
                        lead=action_lead,
                        learner=learner,
                        side=last_side,
                        speed=last_speed,
                        paused=paused,
                        dry_run=dry_run,
                    ))
                if now - last_status >= 1.0:
                    print("Waiting for the game ball…", flush=True)
                    last_status = now
                time.sleep(SAMPLE_SLEEP)
                continue
            ball_state = sample["ball"]
            x, y = float(ball_state["x"]), float(ball_state["y"])
            hits = sample.get("hits")
            current_record = sample.get("best")
            lifetime_record = sample.get("lifetime_best") or 0
            hits = int(hits) if hits is not None else None
            current_record = int(current_record) if current_record is not None else None
            lifetime_record = int(lifetime_record)
            if current_record is not None:
                best_run_record = max(best_run_record, current_record)

            vx = vy = 0.0
            if previous:
                dt = now - previous[2]
                if 0.005 < dt < 0.25:
                    vx = (x - previous[0]) / dt
                    vy = (y - previous[1]) / dt
            speed = math.hypot(vx, vy)
            side_guess = "left" if x < 250 else "right"
            progress_snapshot = None
            if hits is not None and current_record is not None:
                progress_snapshot = _update_progress(
                    progress, learner, hits, current_record, lifetime_record, now=now
                )
                last_rate = progress_snapshot.session_hits_per_minute

            if last_key and now >= release_at:
                try:
                    client.release_paddle(last_key)
                except (CDPDisconnected, CDPTimeout) as exc:
                    _recover_connection(client, logger, exc)
                    previous = None
                    last_key = None
                    continue
                last_key = None

            # Detect a reset teleport to the launch lane; the game drains the
            # ball and relaunches it at x≈465, y≈765 within one physics frame.
            drained = bool(
                previous
                and y > 740
                and abs(x - previous[0]) > 140
                and (x > 420 or x < 30)
            )
            for action_id, action_started in list(pending_actions.items()):
                rebound = now > action_started and vy < -30
                outcome = outcomes.observe(
                    action_id,
                    hits=hits,
                    record=current_record,
                    lifetime_record=lifetime_record,
                    rebound=rebound,
                    drained=drained,
                    score_rate=last_rate,
                    now=now,
                )
                if outcome:
                    resolved_actions += 1
                    logger.log("policy_update", action_id=outcome.action_id,
                               side=outcome.side, lead=outcome.lead,
                               reward=outcome.reward, score_delta=outcome.score_delta,
                               record_delta=outcome.record_delta,
                               lifetime_record_delta=outcome.lifetime_record_delta,
                               samples=learner.summary(outcome.side, outcome.speed))
                    pending_actions.pop(action_id, None)
                    _save_learning(config.state_path, learner)
                    action_label = f"{outcome.side} result {outcome.reward:+.1f}"
            for outcome in outcomes.expire(now=now):
                resolved_actions += 1
                logger.log("policy_update", action_id=outcome.action_id,
                           side=outcome.side, lead=outcome.lead,
                           reward=outcome.reward, score_delta=outcome.score_delta,
                           record_delta=outcome.record_delta,
                           lifetime_record_delta=outcome.lifetime_record_delta,
                           samples=learner.summary(outcome.side, outcome.speed))
                pending_actions.pop(outcome.action_id, None)
                _save_learning(config.state_path, learner)

            if vy < -20 and y < TARGET_Y - 35:
                armed = True
                planned_action = None
            if not paused and vy > 30 and armed and now - last_action > ACTION_COOLDOWN:
                seconds_to_flipper = (TARGET_Y - y) / vy
                if 0 <= seconds_to_flipper <= max(learner.leads):
                    predicted_x = x + vx * seconds_to_flipper
                    side = "left" if predicted_x < 250 else "right"
                    if planned_action is None or planned_action[0] != side:
                        planned_action = (side, speed, learner.choose_lead(side, speed))
                    used_lead = planned_action[2]
                    last_side, last_speed, action_lead = side, speed, used_lead
                    action_label = f"suggest {side} at {used_lead:.2f}s"
                    if seconds_to_flipper <= used_lead:
                        action_id = uuid.uuid4().hex
                        total_actions += 1
                        logger.action(
                            action_id=action_id, side=side, speed=speed, lead=used_lead,
                            predicted_x=predicted_x, x=x, y=y, vx=vx, vy=vy,
                            seconds_to_flipper=seconds_to_flipper, dry_run=dry_run,
                        )
                        if not dry_run:
                            try:
                                client.press_paddle(side)
                            except (CDPDisconnected, CDPTimeout) as exc:
                                _recover_connection(client, logger, exc)
                                previous = None
                                continue
                            last_key = side
                            release_at = now + RELEASE_SECONDS
                            outcomes.begin(
                                action_id, side, speed, used_lead, hits, current_record,
                                lifetime_record, now=now,
                            )
                            pending_actions[action_id] = now
                        last_action = now
                        armed = False
                        planned_action = None

            if now - last_ball_log >= 0.10:
                logger.ball(x=x, y=y, vx=vx, vy=vy, hits=hits,
                            record=current_record, lifetime_record=lifetime_record,
                            paused=paused, dry_run=dry_run)
                last_ball_log = now

            status = "Paused" if paused else ("Dry run" if dry_run else "Playing")
            if ui:
                ui.update(_ui_snapshot(
                    status=status,
                    ball={"x": x, "y": y},
                    velocity={"x": vx, "y": vy},
                    hits=hits,
                    current_record=current_record,
                    lifetime_record=progress.best_lifetime_record,
                    score_rate=last_rate,
                    action=action_label,
                    lead=action_lead,
                    learner=learner,
                    side=last_side,
                    speed=last_speed,
                    paused=paused,
                    dry_run=dry_run,
                ))

            if now - last_status >= 1.0:
                elapsed = max(1.0, now - session_start)
                print(
                    f"{status}: ball=({x:.0f},{y:.0f}) v=({vx:.0f},{vy:.0f}) "
                    f"hits={hits} run-record={current_record} lifetime={progress.best_lifetime_record} "
                    f"rate={last_rate:.1f}/min action={action_label}",
                    flush=True,
                )
                last_status = now
            previous = (x, y, now)
            last_sample = sample
            time.sleep(SAMPLE_SLEEP)

    except KeyboardInterrupt:
        print("Stopping bot.")
        close_game_requested = True
    finally:
        if client:
            try:
                client.release_all()
            except CDPClientError as exc:
                logger.log("release_failed", error=str(exc))
            if close_game_requested:
                # Pincremental autosaves every two seconds; wait before closing its Electron page.
                time.sleep(2.1)
                try:
                    client.close_game()
                    print("Closed Pincremental.", flush=True)
                except CDPClientError as exc:
                    logger.log("game_close_failed", error=str(exc))
                    print(f"Could not close Pincremental automatically: {exc}", flush=True)
            client.close(release=False)
        _save_learning(config.state_path, learner)
        logger.log(
            "session_end",
            last_sample=last_sample,
            duration_seconds=(time.monotonic() - session_start) if session_start is not None else 0.0,
            hits_gained=progress.session_hits,
            best_run_record=best_run_record,
            lifetime_record=progress.best_lifetime_record,
            actions=total_actions,
            resolved_actions=resolved_actions,
        )
        logger.close()
        if session_start is not None:
            print(_format_session_report(
                dry_run=dry_run,
                duration_seconds=time.monotonic() - session_start,
                hits_gained=progress.session_hits,
                best_run_record=best_run_record,
                lifetime_record=progress.best_lifetime_record,
                hits_per_minute=last_rate,
                actions=total_actions,
                resolved_actions=resolved_actions,
                learner=learner,
                log_path=logger.path,
            ), flush=True)
        if ui:
            ui.close()


def main() -> None:
    args = _parser().parse_args()
    try:
        config = BotConfig.load(
            args.config,
            app_id=args.app_id,
            debug_port=args.port,
            state_path=args.state,
            log_path=args.log,
            dry_run=args.dry_run,
            auto_upgrades=args.auto_upgrades,
            upgrade_strategy=args.upgrade_strategy,
        )
        run_bot(
            config=config,
            no_launch=args.no_launch,
            launch_timeout=args.connect_timeout,
            no_ui=args.no_ui,
        )
    except (RuntimeError, ValueError, CDPClientError) as exc:
        raise SystemExit(f"Pincremental bot: {exc}") from exc


if __name__ == "__main__":
    main()

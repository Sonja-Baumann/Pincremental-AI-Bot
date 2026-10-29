# Roadmap and current limits

The original reliability, learning, usability, and maintenance roadmap is implemented in this repository. The checklist below records what is available and what still needs real-game validation.

## Implemented

- [x] Match the Pincremental page before attaching to Chrome DevTools; reject unsupported game pages and report the detected game version.
- [x] Release both flippers on shutdown, reconnect after dropped DevTools sessions, and report Steam launch/connection failures with recovery steps.
- [x] Read ball state from the game's Matter.js engine and send paddle input through the game handlers, independent of window position and size.
- [x] Log ball observations, actions, score changes, and outcomes as JSONL session records.
- [x] Attribute subsequent hits, records, rebounds, drains, and score rate to pending actions; learn timing separately by side and velocity bucket with confidence/sample counts.
- [x] Track session progress and lifetime high score separately, including when counters reset.
- [x] Provide a resizable status window with ball/velocity, score, learned timing, pause, dry run, and emergency stop.
- [x] Configure Steam app ID, debugging port, state/log paths, and dry-run mode with CLI/config-file overrides.
- [x] Use XDG config/data/state paths and copy forward the legacy learning file without deleting it.
- [x] Include unit tests, an opt-in live purchase check, a deterministic policy comparison simulator, Python CI for tests/lint/format/types/package build, and setup/run documentation.

## Validation still needed

- The game integration has been checked against the installed Pincremental Global Version 1.107 source and its observed page structure. The opt-in live check has completed an in-game prestige/reset and verified the visible shop state changed. Test after game updates; DevTools integration cannot guarantee compatibility with future versions.
- The trajectory simulator is deliberately a light approximation, not a complete Matter.js replica. It models gravity, velocity limits, bumpers, walls, and a simplified flipper impulse. It omits the board's polygon paths, slingshot and flipper angular dynamics, upgrades/multipliers, and exact lives/reset rules. Use it to compare timing policies, then validate finalists in the game.
- Steam launch and user-interface behavior depend on the local Steam/Electron desktop session and need an interactive run on each supported Linux desktop. Automated CI covers the Python components only.

Run the local checks with `python -m pytest`, `python -m ruff check .`, `python -m ruff format --check .`, `python -m mypy`, and `python -m build` after installing `.[dev]` and `build`.

## Future ideas

- [x] Treat the offline progress dialog as a startup-only popup: dismiss it once when the game opens, then leave popup handling idle during normal play unless another supported dialog appears.
- [x] Add optional automatic purchases across in-game purchase and reset menus, with configurable priorities and an independent on/off setting; exclude real-money controls.
- [ ] Refresh the status window with a more polished visual design while keeping its resize, pause, dry-run, and emergency-stop controls.
- [ ] Add session history and a score/timing progress chart to make improvement across runs easier to see.

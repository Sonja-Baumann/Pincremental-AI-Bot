# Pincremental AI Bot

A Linux companion bot for Pincremental. It launches or connects to the Steam game, reads the ball position from the game's Matter.js physics through Electron's local debugging interface, and sends flipper inputs. It learns flipper timing from rebounds, hit/high-score changes, and hits per minute.

The controller uses the game's physics coordinates rather than screen coordinates. You can move and resize the Pincremental window without recalibrating it. The bot does not maximize the window.

## Requirements

- Linux with Steam installed and signed in
- Python 3.10+
- The game should be closed the first time the bot launches it, so Steam can start it with Electron's debugging port enabled

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Run

```bash
.venv/bin/pincremental-ai-bot
```

Or run the script directly:

```bash
.venv/bin/python pincremental_bot.py
```

If Pincremental is already open without the bot interface, the script waits while you close **the game window only**. Press Enter in the terminal afterward; it will relaunch the game through Steam. No computer restart is needed. Stop the bot with **Ctrl+C**.

The bot dismisses the offline progress dialog and turns off built-in auto paddles after connecting. Its status window can be moved and resized freely. Space pauses/resumes, **D** toggles dry-run mode, and **Esc** releases both paddles and stops the bot. Ctrl+C in the terminal releases both paddles, waits for the game's autosave, then closes Pincremental.

When you stop a connected session, the terminal prints a report with elapsed time, hits gained, best run record, hits per minute, lifetime high score, action outcomes, and the strongest learned timing for each side/speed context. It also shows the session log path. Timing rewards summarize attributed rebounds and score changes; they are not game points.

The first launch must start Pincremental through Steam so Electron receives the local debugging flags. If the game is already running without those flags, close only the game window when prompted; the bot will relaunch it. No laptop restart is required.

Learning data, configuration, and JSONL session logs use XDG locations by default:

- Config: `~/.config/pincremental-ai-bot/config.json`
- Learning data: `~/.local/share/pincremental-ai-bot/learning.json`
- Session logs: `~/.local/state/pincremental-ai-bot/sessions.jsonl`

An existing `~/.pincremental_bot.json` is copied to the new learning-data location on first run and left intact. Set options in the config JSON or override them on the command line; run `pincremental-ai-bot --help` for the available flags. Use `--dry-run` to observe and log suggested actions without pressing flippers. `--no-ui` runs without the dashboard; `--no-launch` attaches only to an already-debuggable game.

For development, install the extra tools and run the checks:

```bash
python -m pip install -e '.[dev]' build
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m build
```

## How it works

The game is an Electron app. The bot launches it with Electron's local `--remote-debugging-port` switch and uses the Chrome DevTools Protocol over loopback to inspect the game's page. It wraps Matter.js's engine update to read live ball coordinates, then dispatches left/right paddle events through the game's own input handlers. It does not use screenshots, so window position, window size, and Wayland screen-capture permissions do not affect ball tracking.

The debugger listens on `127.0.0.1` only. Keep it on loopback; do not expose the debugging port to a public network. Compatibility is currently checked against Pincremental Global Version 1.107; future game updates may change its internal JavaScript hooks. The simulator is an approximate policy-comparison tool and does not exactly reproduce Matter.js or predict in-game scores.

See [ROADMAP.md](ROADMAP.md) for planned improvements and known limitations.

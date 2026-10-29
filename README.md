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

The bot dismisses the offline progress dialog and turns off built-in auto paddles after connecting. Learning data is stored in `~/.pincremental_bot.json`.

## How it works

The game is an Electron app. The bot launches it with Electron's local `--remote-debugging-port` switch and uses the Chrome DevTools Protocol over loopback to inspect the game's page. It wraps Matter.js's engine update to read live ball coordinates, then dispatches left/right paddle events through the game's own input handlers. It does not use screenshots, so window position, window size, and Wayland screen-capture permissions do not affect ball tracking.

The debugger listens on `127.0.0.1` only. Keep it on loopback; do not expose the debugging port to a public network.

See [ROADMAP.md](ROADMAP.md) for planned improvements and known limitations.

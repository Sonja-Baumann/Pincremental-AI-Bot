# Repository guidance

## Project purpose

This project is a local Linux bot for the Steam game Pincremental. It reads live ball state from the game's Electron/Matter.js runtime through Chrome DevTools Protocol and sends flipper events through the game's page handlers. It learns timing preferences between sessions.

## Architecture constraints

- Keep game tracking independent of desktop screen coordinates. Pincremental's Matter.js board uses its own physics coordinate space, so moving or resizing the window must not require recalibration.
- Keep DevTools bound to `127.0.0.1`; never expose the debugging port on a public or LAN interface.
- Do not patch or overwrite the installed Steam game. Make changes in this repository and inject runtime instrumentation through CDP.
- Keep injected JavaScript compatible with the older Chromium version bundled by this game; avoid newer syntax such as optional chaining, nullish coalescing, and spread syntax in `INSTALL_HOOK`.
- Preserve the user's learning state at `~/.pincremental_bot.json` across code changes unless a migration is necessary.

## Project files

- `pincremental_bot.py`: Steam launch, CDP connection, runtime hook, paddle control, and persisted timing learner.
- `pyproject.toml`: package metadata, dependencies, and console entry point.
- `requirements.txt`: direct dependency list for pip-based setup.
- `README.md`: installation and usage.
- `ROADMAP.md`: candidate reliability, learning, usability, and maintenance work.

## Development

- Use Python 3.10 or newer.
- Keep runtime dependencies minimal and declare them in `pyproject.toml` and `requirements.txt`.
- Prefer explicit status messages and fail-safe behavior. Always release paddle inputs if the bot exits or loses its connection.
- Keep UI and window geometry out of the controller unless a future overlay explicitly needs them.

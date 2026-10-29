# Repository Guidelines

## Project Structure

The application is a Python 3.10+ Linux bot. Core modules live at the repository root: `pincremental_bot.py` coordinates launch and control, while `cdp_client.py`, `config.py`, `bot_ui.py`, `learning.py`, `session_log.py`, and `simulation.py` provide supporting components. Automated tests are in `tests/`. `README.md` documents setup and use; `ROADMAP.md` tracks implemented work and future ideas.

## Development Commands

Create an editable development install with `python -m pip install -e '.[dev]' build`. Run locally with `.venv/bin/pincremental-ai-bot` (or `python pincremental_bot.py`). The project checks are `python -m pytest`, `python -m ruff check .`, `python -m ruff format --check .`, `python -m mypy`, and `python -m build`.

## Style and Tests

Use four-space Python indentation, descriptive `snake_case` names, and lines up to 100 characters. Keep type annotations current; Ruff and mypy settings are in `pyproject.toml`. Add focused `pytest` tests under `tests/`, named `test_<area>.py`, for behavior changes. Tests cover Python components; Steam/Electron launch and UI behavior still need interactive Linux validation.

## Architecture and Safety

Track the game through Matter.js physics coordinates, never desktop screen coordinates. Keep Chrome DevTools bound to `127.0.0.1`; do not patch the installed Steam game. Inject instrumentation through CDP and keep `INSTALL_HOOK` JavaScript compatible with the bundled older Chromium (no optional chaining, nullish coalescing, or spread syntax). Release both paddle inputs on shutdown or connection loss. Preserve existing learning data and migrate it only when necessary; never discard the legacy `~/.pincremental_bot.json` during migration.

## Commits and Pull Requests

Use short, imperative commit subjects, matching the history (for example, “Repair stale Pincremental hooks on reconnect”). Pull requests should explain the behavior change, list relevant validation commands and results, and note any game-version or interactive validation limits. Include screenshots only for visible UI changes.

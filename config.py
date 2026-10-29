"""Configuration and XDG-aware paths for Pincremental AI Bot.

The bot's historical learning file at ``~/.pincremental_bot.json`` is copied
to the XDG data directory on first load, never deleted. Explicit CLI arguments
take precedence over config-file values, which take precedence over defaults.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_NAME = "pincremental-ai-bot"
DEFAULT_APP_ID = "1369470"
DEFAULT_DEBUG_PORT = 9222

def _legacy_state_path() -> Path:
    return Path.home() / ".pincremental_bot.json"


def _xdg_dir(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    if not value:
        return fallback
    path = Path(value).expanduser()
    return path if path.is_absolute() else fallback


def config_home() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")


def data_home() -> Path:
    return _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")


def state_home() -> Path:
    return _xdg_dir("XDG_STATE_HOME", Path.home() / ".local" / "state")


@dataclass(frozen=True)
class BotConfig:
    """Resolved paths and runtime options for one bot invocation."""

    app_id: str
    debug_port: int
    state_path: Path
    log_path: Path
    dry_run: bool
    config_path: Path

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        app_id: str | None = None,
        debug_port: int | None = None,
        state_path: str | Path | None = None,
        log_path: str | Path | None = None,
        dry_run: bool | None = None,
    ) -> "BotConfig":
        """Load JSON config, apply explicit overrides, and preserve legacy state.

        The default config file is ``$XDG_CONFIG_HOME/pincremental-ai-bot/config.json``.
        Config keys use the same names as dataclass fields except ``config_path``.
        ``None`` overrides mean no CLI override; ``dry_run=False`` is respected.
        """

        default_config_path = config_home() / APP_NAME / "config.json"
        resolved_config_path = _path(config_path, default_config_path)
        try:
            raw: dict[str, Any] = json.loads(resolved_config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read config {resolved_config_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Config must contain a JSON object: {resolved_config_path}")

        app_value = app_id if app_id is not None else raw.get("app_id", DEFAULT_APP_ID)
        port_value = debug_port if debug_port is not None else raw.get("debug_port", DEFAULT_DEBUG_PORT)
        explicit_state = state_path if state_path is not None else raw.get("state_path")
        explicit_log = log_path if log_path is not None else raw.get("log_path")
        dry_value = dry_run if dry_run is not None else raw.get("dry_run", False)

        if not isinstance(app_value, (str, int)) or not str(app_value).isdigit():
            raise ValueError("app_id must contain only digits")
        try:
            port_value = int(port_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("debug_port must be an integer from 1 to 65535") from exc
        if not 1 <= port_value <= 65535:
            raise ValueError("debug_port must be an integer from 1 to 65535")
        if not isinstance(dry_value, bool):
            raise ValueError("dry_run must be a JSON boolean")

        state_default = data_home() / APP_NAME / "learning.json"
        log_default = state_home() / APP_NAME / "sessions.jsonl"
        resolved_state = _path(explicit_state, state_default)
        resolved_log = _path(explicit_log, log_default)

        # Preserve the established save file. Copying, rather than moving it,
        # means older versions can still resume without losing learning data.
        legacy_state = _legacy_state_path()
        if explicit_state is None and legacy_state.is_file() and not resolved_state.exists():
            try:
                resolved_state.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_state, resolved_state)
            except OSError:
                # If the XDG location is not writable, keep using the legacy file.
                resolved_state = legacy_state

        return cls(
            app_id=str(app_value),
            debug_port=port_value,
            state_path=resolved_state,
            log_path=resolved_log,
            dry_run=dry_value,
            config_path=resolved_config_path,
        )

    def ensure_directories(self) -> None:
        """Create only the parent directories required for state and logs."""

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        """Write resolved user options to the XDG config file."""

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        values = {
            "app_id": self.app_id,
            "debug_port": self.debug_port,
            "state_path": str(self.state_path),
            "log_path": str(self.log_path),
            "dry_run": self.dry_run,
        }
        temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.config_path)


def _path(value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default
    return Path(value).expanduser().resolve()

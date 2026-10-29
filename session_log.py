"""Append-only JSONL session logging for ball, action, and reward events.

The default location is ``$XDG_DATA_HOME/pincremental-ai-bot/sessions`` or
``~/.local/share/pincremental-ai-bot/sessions``. Bot integration uses
``logger.ball(x=..., y=..., vx=..., vy=...)``, ``logger.action(...)``, and
``logger.outcome(...)``. Each call writes and flushes one self-contained JSON
line so a stopped or crashed session still leaves useful observations.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid
from typing import Any


def default_session_directory() -> Path:
    """Return the standard per-user application data directory."""
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "share"
    return base / "pincremental-ai-bot" / "sessions"


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class JsonlSessionLogger:
    """Write timestamped, typed events to one append-only JSONL file."""

    def __init__(self, path: str | Path | None = None, *, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        if path is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = default_session_directory() / f"session-{timestamp}-{self.session_id[:8]}.jsonl"
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._file = self.path.open("a", encoding="utf-8", buffering=1)
        self._closed = False
        self.log("session_start", pid=os.getpid())

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        """Write a generic event with UTC time and this session's identifier."""
        record = {
            "schema": 1,
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": str(event),
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        with self._lock:
            if self._closed:
                raise RuntimeError("session logger is closed")
            self._file.write(line + "\n")
            self._file.flush()
        return record

    def ball(self, **fields: Any) -> dict[str, Any]:
        """Record one ball state sample."""
        return self.log("ball", **fields)

    def action(self, **fields: Any) -> dict[str, Any]:
        """Record a flipper action and the state used to choose it."""
        return self.log("action", **fields)

    def outcome(self, **fields: Any) -> dict[str, Any]:
        """Record an attributed action result and policy update."""
        return self.log("outcome", **fields)

    def close(self) -> None:
        """Flush and close; repeated calls are safe."""
        with self._lock:
            if not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True

    def __enter__(self) -> JsonlSessionLogger:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

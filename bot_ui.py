"""Resizable Tk status window and control queue.

The UI stays on the bot's main thread. Call ``poll_commands`` from the control
loop to service Tk and retrieve commands, and ``update`` with a mapping of
status values. The controller is responsible for releasing flippers when it
receives ``emergency_stop`` or ``stop``.

Snapshot keys: ``status``, ``ball`` (``{x, y}`` or None), ``velocity``
(``{x, y}`` or None), ``hits``, ``run_record``, ``lifetime_record``,
``score_per_minute``, ``action``, ``lead``, ``timings`` (mapping), ``paused``,
and ``dry_run``.

Commands: ``pause_toggle``, ``emergency_stop``, ``stop``, and
``dry_run:<true|false>``. Space toggles pause, Escape requests emergency stop,
and D toggles dry-run mode.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Any, Mapping


class StatusWindow:
    """Create a resizable dashboard whose commands are polled by the caller."""

    def __init__(self, *, dry_run: bool = False, title: str = "Pincremental AI Bot"):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(f"Cannot open the bot status window: {exc}") from exc
        self.root.title(title)
        self.root.geometry("470x390")
        self.root.minsize(360, 300)
        self.root.resizable(True, True)
        self._commands: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._closed = False
        self._paused = False
        self._dry_run = bool(dry_run)
        self._values = {
            "status": tk.StringVar(value="Connecting"),
            "ball": tk.StringVar(value="—"),
            "velocity": tk.StringVar(value="—"),
            "hits": tk.StringVar(value="—"),
            "run_record": tk.StringVar(value="—"),
            "lifetime_record": tk.StringVar(value="—"),
            "score_per_minute": tk.StringVar(value="—"),
            "action": tk.StringVar(value="—"),
            "lead": tk.StringVar(value="—"),
            "timings": tk.StringVar(value="No observations yet"),
        }
        self._dry_var = tk.BooleanVar(value=self._dry_run)
        self._build()
        self.root.bind("<space>", self._hotkey_pause)
        self.root.bind("<Escape>", self._hotkey_stop)
        self.root.bind("d", self._hotkey_dry_run)
        self.root.protocol("WM_DELETE_WINDOW", self._window_close)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(10, weight=1)

        ttk.Label(outer, text="Pincremental AI Bot", font=("TkDefaultFont", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        fields = (
            ("Status", "status"),
            ("Ball (x, y)", "ball"),
            ("Velocity (x/s, y/s)", "velocity"),
            ("Hits", "hits"),
            ("Current run record", "run_record"),
            ("Lifetime record", "lifetime_record"),
            ("Hits per minute", "score_per_minute"),
            ("Last action", "action"),
            ("Selected lead", "lead"),
        )
        for row, (label, key) in enumerate(fields, start=1):
            ttk.Label(outer, text=label + ":").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
            ttk.Label(outer, textvariable=self._values[key]).grid(row=row, column=1, sticky="ew", pady=2)

        ttk.Label(outer, text="Timing results:").grid(row=10, column=0, sticky="nw", padx=(0, 12), pady=(6, 2))
        ttk.Label(outer, textvariable=self._values["timings"], justify="left", anchor="nw").grid(
            row=10, column=1, sticky="nsew", pady=(6, 2)
        )

        controls = ttk.Frame(outer)
        controls.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._pause_button = ttk.Button(controls, text="Pause", command=self._emit_pause)
        self._pause_button.pack(side="left")
        ttk.Button(controls, text="Emergency stop", command=self._emit_emergency_stop).pack(side="left", padx=6)
        ttk.Checkbutton(controls, text="Dry run", variable=self._dry_var,
                        command=self._emit_dry_run).pack(side="left", padx=6)
        ttk.Button(controls, text="Quit", command=self._window_close).pack(side="right")
        ttk.Label(outer, text="Space: pause  •  Esc: emergency stop  •  D: dry run").grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _emit_pause(self) -> None:
        self._commands.put("pause_toggle")

    def _emit_emergency_stop(self) -> None:
        self._commands.put("emergency_stop")

    def _emit_dry_run(self) -> None:
        self._dry_run = bool(self._dry_var.get())
        self._commands.put(f"dry_run:{str(self._dry_run).lower()}")

    def _hotkey_pause(self, _event: Any = None) -> str:
        self._emit_pause()
        return "break"

    def _hotkey_stop(self, _event: Any = None) -> str:
        self._emit_emergency_stop()
        return "break"

    def _hotkey_dry_run(self, _event: Any = None) -> str:
        self._dry_var.set(not self._dry_var.get())
        self._emit_dry_run()
        return "break"

    def _window_close(self) -> None:
        if not self._closed:
            self._commands.put("stop")
            self._closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def poll_commands(self) -> list[str]:
        """Process UI events and return pending controller commands."""

        if not self._closed:
            try:
                self.root.update_idletasks()
                self.root.update()
            except tk.TclError:
                self._window_close()
        commands: list[str] = []
        while True:
            try:
                commands.append(self._commands.get_nowait())
            except queue.Empty:
                return commands

    def update(self, snapshot: Mapping[str, Any]) -> None:
        """Refresh visible status from the controller's latest snapshot."""

        if self._closed:
            return
        def fmt_pair(value: Any, suffix: str = "") -> str:
            if not isinstance(value, Mapping):
                return "—"
            try:
                return f"{float(value['x']):.1f}, {float(value['y']):.1f}{suffix}"
            except (KeyError, TypeError, ValueError):
                return "—"

        try:
            ball = snapshot.get("ball")
            self._values["status"].set(str(snapshot.get("status", "Running")))
            self._values["ball"].set(fmt_pair(ball))
            self._values["velocity"].set(fmt_pair(snapshot.get("velocity")))
            for key in ("hits", "run_record", "lifetime_record"):
                value = snapshot.get(key)
                self._values[key].set("—" if value is None else str(value))
            rate = snapshot.get("score_per_minute")
            self._values["score_per_minute"].set("—" if rate is None else f"{float(rate):.1f}")
            self._values["action"].set(str(snapshot.get("action") or "—"))
            lead = snapshot.get("lead")
            self._values["lead"].set("—" if lead is None else f"{float(lead):.3f} s")
            timings = snapshot.get("timings")
            self._values["timings"].set(_format_timings(timings))
            self._paused = bool(snapshot.get("paused", self._paused))
            self._pause_button.configure(text="Resume" if self._paused else "Pause")
            dry = bool(snapshot.get("dry_run", self._dry_run))
            self._dry_run = dry
            self._dry_var.set(dry)
        except tk.TclError:
            self._window_close()

    def close(self) -> None:
        """Close the window without emitting a stop command."""

        if self._closed:
            return
        self._closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _format_timings(values: Any) -> str:
    if not isinstance(values, Mapping) or not values:
        return "No observations yet"
    lines = []
    for key, value in list(values.items())[:12]:
        if isinstance(value, Mapping):
            count = value.get("count", value.get("trials", 0))
            score = value.get("mean_reward", value.get("value", "—"))
            lines.append(f"{key}: n={count}, reward={score}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)

"""Reliable, loopback-only Chrome DevTools client for Pincremental.

Public API::

    client = connect_pincremental(port=9222, timeout=45)
    while not client.install_hook():
        time.sleep(0.25)  # page is still loading
    state = client.read_state()  # GameState or None while ball is unavailable
    client.press_paddle("left")
    client.release_paddle("left")
    client.release_all()
    client.close()

The WebSocket transport is restricted to loopback even if the target JSON
contains a different host. The injected JavaScript intentionally uses syntax
supported by the older Chromium version bundled with Pincremental.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

import websocket


class CDPClientError(RuntimeError):
    """Base error for DevTools discovery, transport, and page failures."""


class TargetNotFound(CDPClientError):
    """No Pincremental page appeared before the discovery timeout."""


class UnsupportedGameVersion(CDPClientError):
    """The attached page does not expose the expected Pincremental interface."""


class CDPTimeout(CDPClientError):
    """A DevTools command did not return before its deadline."""


class CDPDisconnected(CDPClientError):
    """The target WebSocket closed or became unreachable."""


class CDPProtocolError(CDPClientError):
    """DevTools returned a protocol error or invalid response."""


class PageEvaluationError(CDPClientError):
    """JavaScript evaluation raised an exception in the game page."""


INSTALL_HOOK = r"""(function() {
  if (window.__pincrementalBotInstalled) return { ready: true, installed: true };
  if (document.readyState !== 'complete') return { ready: false, reason: 'page-loading' };
  var botMatter = window.Matter;
  var botJQuery = window.jQuery || window.$;
  // Pincremental's Electron entry script imports both dependencies with
  // require(), so some Chromium contexts do not expose them as window globals.
  if (typeof window.require === 'function') {
    try {
      if (!botMatter) botMatter = window.require('matter-js');
      if (!botJQuery) botJQuery = window.require('jquery');
    } catch (dependencyError) {}
  }
  if (!botMatter || !botMatter.Engine || !botMatter.Engine.update || !botJQuery) {
    return { ready: false, unsupported: true, reason: 'Matter.js or jQuery missing' };
  }
  // DOM counters can be hidden/rebuilt while offline progress or menus are
  // active. The file-backed Pincremental target is already verified during
  // discovery; Matter.js is the required live-game capability for tracking.
  var engineApi = botMatter.Engine;
  var originalUpdate = engineApi.update;
  engineApi.update = function() {
    var engine = arguments[0];
    var result = originalUpdate.apply(this, arguments);
    var bodies = engine.world.bodies;
    var ball = null;
    for (var i = 0; i < bodies.length; i++) {
      if (bodies[i].label === 'pinball') { ball = bodies[i]; break; }
    }
    if (ball) window.__pincrementalBotBall = {
      x: ball.position.x, y: ball.position.y,
      vx: ball.velocity.x, vy: ball.velocity.y,
      time: performance.now()
    };
    return result;
  };
  window.__pincrementalBotRead = function() {
    var ball = window.__pincrementalBotBall || null;
    function number(selector) {
      var element = document.querySelector(selector);
      var value = element ? element.textContent.replace(/[^0-9]/g, '') : '';
      return value ? Number(value) : null;
    }
    var titleElement = document.querySelector('#BasicPopup .PopTitle');
    var holder = document.querySelector('.BasicPopHolder');
    return {
      ball: ball,
      hits: number('.current-score span'),
      best: number('.high-score span'),
      lifetime_best: number('.lifetime-high-score span'),
      title: titleElement ? titleElement.textContent.trim() : '',
      game_version: (document.querySelector('#GameVersion') || {}).textContent || '',
      popupVisible: !!holder && getComputedStyle(holder).display !== 'none'
    };
  };
  window.__pincrementalBotKey = function(side, down) {
    var code = side === 'left' ? 37 : 39;
    var event = botJQuery.Event(down ? 'keydown' : 'keyup', { which: code, keyCode: code });
    botJQuery('body').trigger(event);
    return true;
  };
  window.__pincrementalBotDismissOffline = function() {
    var titleElement = document.querySelector('#BasicPopup .PopTitle');
    var title = titleElement ? titleElement.textContent.trim() : '';
    if (/offline progress|^welcome!?$/i.test(title)) {
      var button = document.querySelector('#BasicPopup .DonationHolder#PopConfirm button');
      if (button) button.click();
      return title;
    }
    return '';
  };
  window.__pincrementalBotDisableAutoPaddles = function() {
    botJQuery('.AutoPaddleToggle.Off').trigger('click');
    return true;
  };
  window.__pincrementalBotInstalled = true;
  return { ready: true, installed: true };
})()"""

READ_STATE = "window.__pincrementalBotRead ? window.__pincrementalBotRead() : null"


def is_pincremental_target(target: Mapping[str, Any]) -> bool:
    """Accept only a file-backed Pincremental page, never an arbitrary tab."""
    if target.get("type") != "page" or not target.get("webSocketDebuggerUrl"):
        return False
    title = str(target.get("title", "")).strip().casefold()
    url = str(target.get("url", ""))
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "file":
        return False
    path = urllib.parse.unquote(parsed.path).replace("\\", "/").casefold()
    file_name = path.rsplit("/", 1)[-1]
    valid_page = file_name in {"index.html", "pinball.html", "universe1.html", "universe2.html", "universe3.html"}
    return valid_page and ("pincremental" in title or "/pincremental/" in path)


def _loopback_websocket_url(value: str, port: int) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"ws", "wss"} or not parsed.path.startswith("/devtools/"):
        raise CDPProtocolError("Pincremental returned an invalid DevTools WebSocket URL")
    # CDP is intentionally always accessed over local plain WebSocket.
    return urllib.parse.urlunparse(("ws", f"127.0.0.1:{port}", parsed.path, "", parsed.query, ""))


def discover_target(
    port: int = 9222,
    timeout: float = 45.0,
    poll_interval: float = 0.4,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Mapping[str, Any]:
    """Poll the loopback target list and return only the Pincremental page."""
    if not 1 <= int(port) <= 65535:
        raise ValueError("CDP port must be between 1 and 65535")
    url = f"http://127.0.0.1:{int(port)}/json/list"
    deadline = time.monotonic() + max(0.0, timeout)
    last_error: Exception | None = None
    while True:
        try:
            with opener(url, timeout=min(1.0, max(0.1, timeout))) as response:
                targets = json.loads(response.read().decode("utf-8"))
            if not isinstance(targets, list):
                raise CDPProtocolError("DevTools target list was not a JSON list")
            candidates = [item for item in targets if isinstance(item, dict) and is_pincremental_target(item)]
            if candidates:
                # Prefer the actual Pinball board document over the generic shell page.
                # The bundled game document is named ``Pinball.html`` with a
                # capital P. Prefer it over the Electron shell's index page.
                candidates.sort(
                    key=lambda item: str(item.get("url", "")).casefold().endswith("/pinball.html"),
                    reverse=True,
                )
                return candidates[0]
            last_error = None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            detail = f" Last probe error: {last_error}" if last_error else ""
            raise TargetNotFound(
                f"No Pincremental page found at 127.0.0.1:{port} within {timeout:g}s.{detail}"
            )
        time.sleep(max(0.01, poll_interval))


class PincrementalClient:
    """Small typed wrapper around one selected Pincremental page target."""

    def __init__(
        self,
        target: Mapping[str, Any],
        port: int = 9222,
        timeout: float = 2.0,
        websocket_factory: Callable[..., Any] = websocket.create_connection,
    ) -> None:
        if not is_pincremental_target(target):
            raise UnsupportedGameVersion("Refusing to connect: target is not a Pincremental page")
        self.target = dict(target)
        self.port = int(port)
        self.timeout = timeout
        self._websocket_factory = websocket_factory
        self._game_version = ""
        ws_url = _loopback_websocket_url(str(target["webSocketDebuggerUrl"]), self.port)
        try:
            self._ws = websocket_factory(ws_url, timeout=timeout, suppress_origin=True)
        except Exception as exc:
            raise CDPDisconnected(f"Could not connect to Pincremental DevTools: {exc}") from exc
        self._seq = 0
        self._closed = False

    @property
    def target_info(self) -> dict[str, Any]:
        """Non-secret target identification; the CDP URL remains loopback-only."""
        return {
            "app": "Pincremental",
            "app_id": "1369470",
            "target_id": self.target.get("id"),
            "title": self.target.get("title", ""),
            "url": self.target.get("url", ""),
            "type": self.target.get("type", ""),
            "game_version": self._game_version,
        }

    @classmethod
    def connect(
        cls,
        port: int = 9222,
        timeout: float = 45.0,
        poll_interval: float = 0.4,
        opener: Callable[..., Any] = urllib.request.urlopen,
        websocket_factory: Callable[..., Any] = websocket.create_connection,
    ) -> "PincrementalClient":
        target = discover_target(port, timeout, poll_interval, opener)
        return cls(target, port=port, websocket_factory=websocket_factory)

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        """Send one CDP request, skipping event messages until its reply arrives."""
        self._seq += 1
        call_id = self._seq
        try:
            self._ws.send(json.dumps({"id": call_id, "method": method, "params": dict(params or {})}))
        except Exception as exc:
            raise CDPDisconnected(f"DevTools disconnected while sending {method}: {exc}") from exc
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv()
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise CDPTimeout(f"Timed out waiting for {method}") from exc
                if getattr(exc, "timeout", False) or exc.__class__.__name__ in {"WebSocketTimeoutException", "TimeoutError"}:
                    continue
                raise CDPDisconnected(f"DevTools disconnected while waiting for {method}: {exc}") from exc
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise CDPProtocolError(f"DevTools returned invalid JSON for {method}") from exc
            if not isinstance(message, dict) or message.get("id") != call_id:
                continue
            if "error" in message:
                error = message["error"]
                raise CDPProtocolError(f"DevTools {method} failed: {error.get('message', error)}")
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise CDPProtocolError(f"DevTools returned an invalid {method} result")
            return result
        raise CDPTimeout(f"Timed out waiting for {method}")

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        exception = result.get("exceptionDetails")
        if exception:
            text = exception.get("text") or exception.get("exception", {}).get("description") or "page evaluation failed"
            raise PageEvaluationError(str(text))
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise PageEvaluationError(str(remote.get("description", "page evaluation failed")))
        return remote.get("value")

    def install_hook(self) -> bool:
        """Install the compatible Matter.js hook; False means page still loading."""
        result = self.evaluate(INSTALL_HOOK)
        if not isinstance(result, dict):
            raise UnsupportedGameVersion("Pincremental hook returned an unexpected response")
        if result.get("unsupported"):
            raise UnsupportedGameVersion(f"Pincremental page is missing expected features: {result.get('reason')}")
        return bool(result.get("ready"))

    def read_state(self) -> dict[str, Any] | None:
        """Read ball physics and counters as a dict; ball is None before first frame.

        The result includes ``ball`` (with x/y/vx/vy/time), ``hits``, ``best``,
        ``lifetime_best``, popup visibility/title, and the visible game version.
        """
        sample = self.evaluate(READ_STATE)
        if sample is None:
            return None
        if not isinstance(sample, dict) or "hits" not in sample or "best" not in sample:
            raise UnsupportedGameVersion("Pincremental state response has an unexpected shape")
        ball = sample.get("ball")
        if ball is not None and not isinstance(ball, dict):
            raise UnsupportedGameVersion("Pincremental ball state is malformed")

        def optional_number(container: Mapping[str, Any] | None, key: str) -> float | None:
            value = container.get(key) if container else None
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError) as exc:
                raise UnsupportedGameVersion(f"Invalid ball field: {key}") from exc

        def optional_int(key: str) -> int | None:
            value = sample.get(key)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError) as exc:
                raise UnsupportedGameVersion(f"Invalid score field: {key}") from exc

        normalized_ball = None
        if ball is not None:
            normalized_ball = {
                key: optional_number(ball, key)
                for key in ("x", "y", "vx", "vy", "time")
            }
            if normalized_ball["x"] is None or normalized_ball["y"] is None:
                raise UnsupportedGameVersion("Pincremental ball state is missing x/y coordinates")
        self._game_version = str(sample.get("game_version") or "").strip()
        return {
            "ball": normalized_ball,
            "hits": optional_int("hits"),
            "best": optional_int("best"),
            "lifetime_best": optional_int("lifetime_best"),
            "popup_title": str(sample.get("title") or ""),
            "popup_visible": bool(sample.get("popupVisible", False)),
            "game_version": self._game_version,
        }

    def _side(self, side: str) -> str:
        if side not in {"left", "right"}:
            raise ValueError("paddle side must be 'left' or 'right'")
        return side

    def press(self, side: str, down: bool) -> None:
        self.evaluate("window.__pincrementalBotKey(%r, %s)" % (self._side(side), "true" if down else "false"))

    def press_paddle(self, side: str) -> None:
        self.press(side, True)

    def release_paddle(self, side: str) -> None:
        self.press(side, False)

    def release_all(self) -> None:
        """Release both paddles; safe to call repeatedly during shutdown."""
        errors = []
        for side in ("left", "right"):
            try:
                self.release_paddle(side)
            except CDPClientError as exc:
                errors.append(str(exc))
        if errors:
            raise CDPDisconnected("Could not release both paddles: " + "; ".join(errors))

    def dismiss_known_popup(self) -> str:
        return str(self.evaluate("window.__pincrementalBotDismissOffline()") or "")

    def dismiss_offline_popup(self) -> str:
        """Compatibility name for callers that only dismiss the known offline dialog."""
        return self.dismiss_known_popup()

    def disable_auto_paddles(self) -> None:
        self.evaluate("window.__pincrementalBotDisableAutoPaddles()")

    def close_game(self) -> None:
        """Close the Pincremental page; the Linux Electron app exits with its window."""
        self.call("Page.close")

    def reconnect(self, timeout: float = 20.0, poll_interval: float = 0.4) -> None:
        """Re-discover the app page and attach again after a target restart."""
        self.close(release=False)
        target = discover_target(self.port, timeout, poll_interval)
        self.target = dict(target)
        ws_url = _loopback_websocket_url(str(target["webSocketDebuggerUrl"]), self.port)
        try:
            self._ws = self._websocket_factory(ws_url, timeout=self.timeout, suppress_origin=True)
        except Exception as exc:
            self._closed = True
            raise CDPDisconnected(f"Could not reconnect to Pincremental DevTools: {exc}") from exc
        self._seq = 0
        self._closed = False
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.install_hook():
            if time.monotonic() >= deadline:
                raise CDPTimeout("Reconnected page did not finish loading the Pincremental hook")
            time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        self.release_all()

    def close(self, release: bool = True) -> None:
        """Release controls best-effort, then close the transport safely."""
        if self._closed:
            return
        if release:
            try:
                self.release_all()
            except CDPClientError:
                pass
        try:
            self._ws.close()
        except Exception:
            pass
        self._closed = True


def connect_pincremental(
    port: int = 9222,
    timeout: float = 45.0,
    poll_interval: float = 0.4,
    opener: Callable[..., Any] = urllib.request.urlopen,
    websocket_factory: Callable[..., Any] = websocket.create_connection,
) -> PincrementalClient:
    """Discover and connect to Pincremental on the loopback CDP endpoint."""
    return PincrementalClient.connect(port, timeout, poll_interval, opener, websocket_factory)


# Name used by the controller layer; keep PincrementalClient for explicitness.
GameInterface = PincrementalClient

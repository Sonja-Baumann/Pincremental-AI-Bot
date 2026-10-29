#!/usr/bin/env python3
"""Pincremental bot using the game's local Electron debugging interface.

This avoids screen capture (which can be black under Wayland). The game is
launched with a loopback-only Chromium debugging port; the bot reads the
Matter.js ball state and sends paddle events into the game's own page.
"""

import argparse
import json
import math
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket


APP_ID = "1369470"
DEBUG_PORT = 9222
TARGET_Y = 662.0
LEADS = (0.08, 0.12, 0.16, 0.20)
STATE_FILE = Path.home() / ".pincremental_bot.json"

INSTALL_HOOK = r"""(function() {
  if (window.__pincrementalBotInstalled) return true;
  if (!window.Matter || !window.Matter.Engine || !window.$) return false;
  var engineApi = window.Matter.Engine;
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
      title: titleElement ? titleElement.textContent.trim() : '',
      popupVisible: !!holder && getComputedStyle(holder).display !== 'none'
    };
  };
  window.__pincrementalBotKey = function(side, down) {
    var code = side === 'left' ? 37 : 39;
    var event = window.$.Event(down ? 'keydown' : 'keyup', { which: code, keyCode: code });
    window.$('body').trigger(event);
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
    window.$('.AutoPaddleToggle.Off').trigger('click');
    return true;
  };
  window.__pincrementalBotInstalled = true;
  return true;
})()"""


class CDP:
    def __init__(self, ws_url, timeout=2):
        self.ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self.seq = 0

    def call(self, method, params=None):
        self.seq += 1
        call_id = self.seq
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "DevTools command failed"))
            return message.get("result", {})
        raise TimeoutError(f"Timed out waiting for {method}")

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        result = response.get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description", "Page evaluation failed"))
        return result.get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def debug_targets():
    url = f"http://127.0.0.1:{DEBUG_PORT}/json/list"
    with urllib.request.urlopen(url, timeout=0.5) as response:
        return json.loads(response.read().decode("utf-8"))


def find_game_target():
    try:
        targets = debug_targets()
    except Exception:
        return None
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return target
    return None


def game_running():
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore").lower()
        except OSError:
            continue
        if "pincremental.exe" in command:
            return True
    return False


def start_steam_game():
    steam = shutil.which("steam")
    if not steam:
        raise RuntimeError("Steam command not found on PATH")
    print("Launching Pincremental from Steam with local game-state access…")
    subprocess.Popen(
        [steam, "-applaunch", APP_ID,
         f"--remote-debugging-port={DEBUG_PORT}",
         "--remote-allow-origins=*"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def wait_for_target(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        target = find_game_target()
        if target:
            return target
        time.sleep(0.5)
    return None


def read_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if tuple(state.get("leads", ())) == LEADS:
            return state
    except (OSError, ValueError, TypeError):
        pass
    return {
        "leads": list(LEADS),
        "trials": {str(lead): 0 for lead in LEADS},
        "values": {str(lead): 0.0 for lead in LEADS},
        "best_record": 0,
        "runs": 0,
        "sessions": 0,
        "best_hits_per_minute": 0.0,
    }


def save_state(path, state):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(path)


def select_lead(state):
    # Explore timings occasionally; otherwise prefer the timing with better results.
    import random
    if random.random() < 0.15:
        return random.choice(LEADS)
    return max(LEADS, key=lambda lead: (
        state["values"][str(lead)] + 0.35 / math.sqrt(1 + state["trials"][str(lead)])
    ))


def train_result(state, lead, reward, path):
    key = str(lead)
    count = state["trials"][key] + 1
    state["values"][key] += (reward - state["values"][key]) / count
    state["trials"][key] = count
    save_state(path, state)


def main():
    global DEBUG_PORT
    parser = argparse.ArgumentParser(description="Play Pincremental with learned flipper timing")
    parser.add_argument("--state", type=Path, default=STATE_FILE, help="persistent learning data")
    parser.add_argument("--port", type=int, default=DEBUG_PORT, help="local Electron debugging port")
    args = parser.parse_args()
    DEBUG_PORT = args.port

    target = find_game_target()
    if not target:
        if game_running():
            print("Pincremental is open without bot access.")
            print("Close only the Pincremental window normally, wait for it to exit, then press Enter here.")
            input()
            deadline = time.monotonic() + 120
            while game_running() and time.monotonic() < deadline:
                time.sleep(0.5)
            if game_running():
                raise SystemExit("Pincremental is still running; close its window and try again.")
            time.sleep(2)  # the game autosaves every two seconds
        start_steam_game()
        target = wait_for_target(45)
    if not target:
        raise SystemExit(
            "Steam did not expose the local game interface. Close Pincremental and run this script again; "
            "the app must be launched by this script with the debugging switch."
        )

    client = CDP(target["webSocketDebuggerUrl"])
    state = read_state(args.state)
    try:
        ready = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = client.evaluate(INSTALL_HOOK)
            if ready:
                break
            time.sleep(0.25)
        if not ready:
            raise SystemExit("Could not attach to Matter.js in the game page.")

        closed = client.evaluate("window.__pincrementalBotDismissOffline()")
        if closed:
            print(f"Dismissed {closed} popup.")
        client.evaluate("window.__pincrementalBotDisableAutoPaddles()")

        print("Connected to Pincremental. Window position and size are yours to choose.")
        print("Press Ctrl+C in this terminal to stop the bot.")
        state["sessions"] = state.get("sessions", 0) + 1
        session_start = time.monotonic()
        previous = None
        last_status = 0.0
        last_key = None
        release_at = 0.0
        last_action = 0.0
        action_lead = 0.12
        pending = None
        hits_at_start = None
        best_at_start = None
        top_record = state.get("best_record", 0)

        while True:
            now = time.monotonic()
            sample = client.evaluate("window.__pincrementalBotRead()")
            if not sample or not sample.get("ball"):
                time.sleep(0.03)
                continue
            ball = sample["ball"]
            x, y = float(ball["x"]), float(ball["y"])
            hits, best = sample.get("hits"), sample.get("best")
            if hits is not None:
                hits = int(hits)
            if best is not None:
                best = int(best)
            if hits_at_start is None and hits is not None:
                hits_at_start = hits
            if best_at_start is None and best is not None:
                best_at_start = best

            vx = vy = 0.0
            if previous:
                dt = now - previous[2]
                if 0.005 < dt < 0.25:
                    vx = (x - previous[0]) / dt
                    vy = (y - previous[1]) / dt

            if sample.get("popupVisible") and "offline progress" in sample.get("title", "").lower():
                client.evaluate("window.__pincrementalBotDismissOffline()")

            if last_key and now >= release_at:
                client.evaluate(f"window.__pincrementalBotKey('{last_key}', false)")
                last_key = None

            if pending:
                old_hits = pending.get("hits")
                if hits is not None and old_hits is not None and hits > old_hits:
                    elapsed = max(5.0, now - session_start)
                    rate = max(0, hits - (hits_at_start or 0)) * 60 / elapsed
                    pending["reward"] += (hits - old_hits) * (1.0 + 0.02 * rate)
                    pending["hits"] = hits
                if best is not None and best > top_record:
                    top_record = best
                    state["best_record"] = max(state.get("best_record", 0), best)
                    elapsed = max(5.0, now - session_start)
                    rate = max(0, (hits or 0) - (hits_at_start or 0)) * 60 / elapsed
                    pending["reward"] += 10.0 + 0.1 * rate
                if now - pending["time"] > 0.55 and vy < -10:
                    pending["reward"] += 1.0
                    train_result(state, pending["lead"], pending["reward"], args.state)
                    pending = None
                elif now - pending["time"] > 1.5:
                    pending["reward"] -= 3.0
                    train_result(state, pending["lead"], pending["reward"], args.state)
                    pending = None

            target_y = TARGET_Y
            if vy < -20 and y < target_y - 35:
                ready = True
            if vy > 30 and ready and now - last_action > 0.28:
                seconds_to_flipper = (target_y - y) / vy
                if 0 <= seconds_to_flipper <= action_lead:
                    predicted_x = x + vx * seconds_to_flipper
                    side = "left" if predicted_x < 250 else "right"
                    used_lead = action_lead
                    client.evaluate(f"window.__pincrementalBotKey('{side}', true)")
                    last_key = side
                    release_at = now + 0.10
                    pending = {"lead": used_lead, "reward": 0.0, "time": now, "hits": hits}
                    action_lead = select_lead(state)
                    last_action = now
                    ready = False

            if now - last_status > 1:
                elapsed = max(1.0, now - session_start)
                rate = max(0, (hits or 0) - (hits_at_start or 0)) * 60 / elapsed
                print(f"ball=({x:.0f},{y:.0f}) velocity=({vx:.0f},{vy:.0f}) hits={hits} "
                      f"record={best} rate={rate:.1f}/min lead={action_lead:.2f}s", flush=True)
                last_status = now
            previous = (x, y, now)
            time.sleep(0.015)

    except KeyboardInterrupt:
        print("Stopping; releasing both flippers.")
        for side in ("left", "right"):
            try:
                client.evaluate(f"window.__pincrementalBotKey('{side}', false)")
            except Exception:
                pass
    finally:
        client.close()
        save_state(args.state, state)
        print(f"Learning state saved to {args.state}")


if __name__ == "__main__":
    main()

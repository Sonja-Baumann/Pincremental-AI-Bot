import json
import unittest

from cdp_client import (
    CDPProtocolError,
    DISABLE_AUTO_PADDLES,
    DISMISS_KNOWN_POPUP,
    DISPATCH_PADDLE_KEY,
    GameInterface,
    INSTALL_HOOK,
    PageEvaluationError,
    UnsupportedGameVersion,
    discover_target,
    is_pincremental_target,
)


def target(**updates):
    value = {
        "id": "page-1",
        "type": "page",
        "title": "Pincremental",
        "url": "file:///home/player/Steam/Pincremental/index.html",
        "webSocketDebuggerUrl": "ws://remote-host:9222/devtools/page/page-1",
    }
    value.update(updates)
    return value


class FakeSocket:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        return json.dumps(self.responses.pop(0))

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class CDPClientTests(unittest.TestCase):
    def make_client(self, responses=()):
        socket = FakeSocket(responses)
        calls = []

        def factory(url, **kwargs):
            calls.append((url, kwargs))
            return socket

        client = GameInterface(target(), websocket_factory=factory)
        return client, socket, calls

    def test_selects_only_pincremental_file_page(self):
        self.assertTrue(is_pincremental_target(target()))
        self.assertFalse(is_pincremental_target(target(type="service_worker")))
        self.assertFalse(is_pincremental_target(target(url="https://example.com/index.html")))
        self.assertFalse(is_pincremental_target(target(title="New Tab", url="file:///tmp/index.html")))

    def test_discovery_skips_other_targets_and_prefers_pinball_document(self):
        generic = target(id="shell", url="file:///home/player/Pincremental/index.html")
        board = target(id="board", url="file:///home/player/Pincremental/Pinball.html")
        opener_calls = []

        def opener(url, timeout):
            opener_calls.append((url, timeout))
            return FakeResponse([{"type": "page", "title": "Other", "url": "https://example.com"}, generic, board])

        result = discover_target(timeout=0.5, opener=opener)
        self.assertEqual(result["id"], "board")
        self.assertEqual(opener_calls[0][0], "http://127.0.0.1:9222/json/list")

    def test_websocket_is_forced_to_loopback(self):
        client, _socket, calls = self.make_client()
        self.assertEqual(calls[0][0], "ws://127.0.0.1:9222/devtools/page/page-1")
        self.assertTrue(calls[0][1]["suppress_origin"])
        client.close(release=False)

    def test_read_state_returns_required_scores_and_app_info(self):
        sample = {
            "ball": {"x": 220, "y": 650, "vx": 3, "vy": -4, "time": 55.2},
            "hits": 23,
            "best": 31,
            "lifetime_best": 245,
            "title": "Offline Progress",
            "popupVisible": True,
            "game_version": "1.107",
        }
        client, _socket, _calls = self.make_client([{
            "id": 1,
            "result": {"result": {"type": "object", "value": sample}},
        }])
        state = client.read_state()
        self.assertEqual(state["ball"]["x"], 220)
        self.assertEqual(state["hits"], 23)
        self.assertEqual(state["best"], 31)
        self.assertEqual(state["lifetime_best"], 245)
        self.assertEqual(state["game_version"], "1.107")
        self.assertEqual(client.target_info["app"], "Pincremental")
        self.assertEqual(client.target_info["game_version"], "1.107")
        client.close(release=False)

    def test_press_validates_side_and_dispatches_key_state(self):
        client, socket, _calls = self.make_client([{
            "id": 1, "result": {"result": {"type": "boolean", "value": True}}
        }])
        client.press("left", True)
        expression = socket.sent[0]["params"]["expression"]
        self.assertIn('("left", true)', expression)
        self.assertNotIn("window.__pincrementalBotKey", expression)
        self.assertIn("dispatchEvent(event)", DISPATCH_PADDLE_KEY)
        with self.assertRaises(ValueError):
            client.press("middle", True)
        client.close(release=False)

    def test_evaluation_errors_are_reported(self):
        client, _socket, _calls = self.make_client([{
            "id": 1,
            "result": {"exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": "TypeError: test failure"},
                "stackTrace": {"callFrames": [{
                    "url": "file:///Pinball.html", "lineNumber": 8, "columnNumber": 2,
                }]},
            }},
        }])
        with self.assertRaisesRegex(PageEvaluationError, r"TypeError: test failure.*Pinball.html:9:3"):
            client.evaluate("broken()")
        client.close(release=False)

    def test_protocol_errors_are_reported(self):
        client, _socket, _calls = self.make_client([{
            "id": 1,
            "error": {"code": -32601, "message": "method not found"},
        }])
        with self.assertRaises(CDPProtocolError):
            client.call("Missing.method")
        client.close(release=False)

    def test_close_releases_both_paddles_before_closing_socket(self):
        response = {"result": {"result": {"type": "boolean", "value": True}}}
        client, socket, _calls = self.make_client([
            dict(response, id=1),
            dict(response, id=2),
        ])
        client.close()
        self.assertTrue(socket.closed)
        expressions = [message["params"]["expression"] for message in socket.sent]
        self.assertIn('("left", false)', expressions[0])
        self.assertIn('("right", false)', expressions[1])

    def test_close_game_uses_page_close_command(self):
        client, socket, _calls = self.make_client([{"id": 1, "result": {}}])
        client.close_game()
        self.assertEqual(socket.sent[0]["method"], "Page.close")
        client.close(release=False)

    def test_popup_and_auto_paddle_controls_do_not_depend_on_injected_functions(self):
        client, socket, _calls = self.make_client([
            {"id": 1, "result": {"result": {"type": "string", "value": "Offline Progress"}}},
            {"id": 2, "result": {"result": {"type": "boolean", "value": True}}},
        ])
        self.assertEqual(client.dismiss_known_popup(), "Offline Progress")
        self.assertTrue(client.disable_auto_paddles())
        self.assertIn("button.click()", DISMISS_KNOWN_POPUP)
        self.assertIn("toggle.click()", DISABLE_AUTO_PADDLES)
        self.assertNotIn("__pincrementalBotDismissOffline", socket.sent[0]["params"]["expression"])
        client.close(release=False)

    def test_invalid_ball_coordinates_are_rejected(self):
        sample = {"ball": {"x": 1}, "hits": 1, "best": 1}
        client, _socket, _calls = self.make_client([{
            "id": 1,
            "result": {"result": {"type": "object", "value": sample}},
        }])
        with self.assertRaises(UnsupportedGameVersion):
            client.read_state()
        client.close(release=False)

    def test_injected_hook_uses_legacy_chromium_syntax(self):
        self.assertNotIn("?.", INSTALL_HOOK)
        self.assertNotIn("=>", INSTALL_HOOK)
        self.assertNotIn("...", INSTALL_HOOK)

    def test_injected_hook_loads_game_modules_from_electron_require(self):
        self.assertIn("window.require('matter-js')", INSTALL_HOOK)
        self.assertIn("window.require('jquery')", INSTALL_HOOK)
        self.assertIn("botMatter.Engine", INSTALL_HOOK)
        self.assertNotIn("Pincremental board selectors missing", INSTALL_HOOK)

    def test_injected_hook_repairs_stale_partial_install_without_wrapping_twice(self):
        for function_name in (
            "__pincrementalBotRead",
            "__pincrementalBotKey",
            "__pincrementalBotDismissOffline",
            "__pincrementalBotDisableAutoPaddles",
        ):
            self.assertIn(f"typeof window.{function_name} === 'function'", INSTALL_HOOK)
        self.assertIn("engineApi.update.__pincrementalBotWrapped", INSTALL_HOOK)


if __name__ == "__main__":
    unittest.main()

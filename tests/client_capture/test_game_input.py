"""Focus/input automation: opt-in gate, limits, focus-loss abort and key release."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP

from mcp_server.adapters.client_capture import CaptureError, ClientWindow
from mcp_server.services.client_capture import ClientCaptureService
from mcp_server.services.game_input import GameInputService
from mcp_server.tools.client_capture import register_client_capture_tools
from mcp_server.tools.game_input import register_game_input_tools
from tests.client_capture.test_client_capture import FakeBackend

ROOT = Path(__file__).resolve().parents[2]


class FakeInput:
    def __init__(self):
        self.focused, self.runs = 0, []

    def focus(self, client):
        self.focused += 1
        return {"focused": True, "already_foreground": False, "method": "direct"}

    def run(self, client, actions):
        self.runs.append(actions)
        return [{"index": i, "type": a["type"]} for i, a in enumerate(actions)]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(temporary.cleanup)
        self.captures = ClientCaptureService(Path(temporary.name), backend=FakeBackend())
        self.client_id = self.captures.list_clients()["clients"][0]["client_id"]
        self.backend = FakeInput()
        self.service = GameInputService(self.captures, backend=self.backend, enabled=True)

    def test_input_is_opt_in_but_focus_is_not(self):
        service = GameInputService(self.captures, backend=self.backend, enabled=False)
        with self.assertRaises(CaptureError) as error:
            service.send(self.client_id, [{"type": "wait", "ms": 1}])
        self.assertEqual(error.exception.code, "input_disabled")
        self.assertEqual(self.backend.runs, [])
        self.assertTrue(service.focus(self.client_id)["focused"])
        with patch.dict(os.environ, {"CRAFTMCP_GAME_INPUT": "1"}):
            self.assertTrue(GameInputService(self.captures, backend=self.backend).enabled)

    def test_chat_command_sequence_and_unknown_client(self):
        result = self.service.chat(self.client_id, "/heal 4")
        self.assertEqual(self.backend.focused, 1)
        self.assertEqual([a["type"] for a in self.backend.runs[0]], ["key", "wait", "type_text", "wait", "key"])
        self.assertEqual(self.backend.runs[0][2]["text"], "/heal 4")
        self.assertEqual(result["functional_result"], "unknown")
        with self.assertRaises(CaptureError) as error:
            self.service.chat("not-listed", "/heal 1")
        self.assertEqual(error.exception.code, "unknown_client")

    def test_limits_rejected_before_any_input(self):
        for actions, code in (([], "invalid_actions"),
                              ([{"type": "wait", "ms": 10_000}] * 4, "sequence_too_long"),
                              ([{"type": "type_text", "text": "a\nb"}], "invalid_text")):
            with self.subTest(code=code), self.assertRaises(CaptureError) as error:
                self.service.send(self.client_id, actions)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(self.backend.runs, [])


@unittest.skipUnless(os.name == "nt", "SendInput adapter requires Windows")
class AdapterTests(unittest.TestCase):
    def make(self, foreground):
        from mcp_server.adapters.client_capture import WindowsClientCapture
        from mcp_server.adapters.game_input import WindowsGameInput
        capture = WindowsClientCapture()
        adapter = WindowsGameInput(capture, sleep=lambda _s: None)
        client = ClientWindow(101, 1, 1, "test", "GLFW30", "java.exe")
        sent = []
        patches = [patch.object(capture, "_inspect", return_value=client),
                   patch.object(adapter, "_key", side_effect=lambda vk, up: sent.append(("key", vk, up))),
                   patch.object(adapter, "_mouse", side_effect=lambda f, dx=0, dy=0, data=0: sent.append(("mouse", f, dx, dy))),
                   patch.object(adapter, "is_foreground", side_effect=foreground)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return adapter, client, sent

    def test_focus_loss_aborts_and_releases_held_keys(self):
        states = iter([True, True, False])
        adapter, client, sent = self.make(lambda _c: next(states, False))
        with self.assertRaises(CaptureError) as error:
            adapter.run(client, [{"type": "key", "keys": ["w", "shift"], "hold_ms": 5000},
                                 {"type": "click", "button": "left"}])
        self.assertEqual(error.exception.code, "focus_lost")
        downs = [vk for kind, vk, up in sent if kind == "key" and not up]
        ups = [vk for kind, vk, up in sent if kind == "key" and up]
        self.assertEqual(sorted(downs), sorted(ups))  # nothing left pressed
        self.assertFalse(any(item[0] == "mouse" for item in sent))  # stopped before the click

    def test_look_is_split_into_exact_relative_steps(self):
        adapter, client, sent = self.make(lambda _c: True)
        adapter.run(client, [{"type": "look", "dx": 101, "dy": -37}])
        moves = [item for item in sent if item[0] == "mouse"]
        self.assertGreater(len(moves), 1)
        self.assertEqual(sum(m[2] for m in moves), 101)
        self.assertEqual(sum(m[3] for m in moves), -37)

    def test_unknown_key_rejected(self):
        from mcp_server.adapters.game_input import key_code
        self.assertEqual(key_code("W"), ord("W"))
        self.assertEqual(key_code("space"), 0x20)
        with self.assertRaises(CaptureError):
            key_code("ctrl+alt+del")


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_validate_actions_and_capture_focuses_first(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            captures = ClientCaptureService(Path(directory), backend=FakeBackend())
            backend = FakeInput()
            service = GameInputService(captures, backend=backend, enabled=True)
            app = FastMCP("input-test", strict_input_validation=True)
            register_client_capture_tools(app, captures, focus=service.focus)
            register_game_input_tools(app, service)
            async with Client(app) as client:
                names = {tool.name for tool in await client.list_tools()}
                self.assertTrue({"focus_game_window", "send_game_input", "run_game_chat_command"} <= names)
                client_id = (await client.call_tool("list_game_clients", {})).structured_content["clients"][0]["client_id"]
                captured = await client.call_tool("capture_game_view", {"client_id": client_id, "run_id": "r", "label": "x"})
                self.assertTrue(captured.structured_content["ok"])
                self.assertEqual(backend.focused, 1)
                await client.call_tool("capture_game_view", {"client_id": client_id, "run_id": "r", "label": "y",
                                                             "focus_window": False})
                self.assertEqual(backend.focused, 1)
                sent = await client.call_tool("send_game_input", {"client_id": client_id, "actions": [
                    {"type": "key", "keys": ["w"], "hold_ms": 500}, {"type": "look", "dx": 300},
                    {"type": "click", "button": "right"}]})
                self.assertTrue(sent.structured_content["ok"], sent.structured_content)
                self.assertEqual(sent.structured_content["completed_actions"], 3)
                with self.assertRaises(Exception):
                    await client.call_tool("send_game_input", {"client_id": client_id,
                                                               "actions": [{"type": "teleport"}]})


if __name__ == "__main__":
    unittest.main()

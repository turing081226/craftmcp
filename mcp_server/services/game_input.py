"""Focus and input automation for a selected Minecraft client (opt-in for input).

Focusing a window is always allowed. Sending keyboard/mouse input requires
CRAFTMCP_GAME_INPUT=1, because it acts as the logged-in player on this desktop.
"""
from __future__ import annotations

import os
import threading

from mcp_server.adapters.client_capture import CaptureError, ClientWindow
from mcp_server.services.client_capture import ClientCaptureService, response

MAX_ACTIONS = 50
MAX_TOTAL_MS = 30_000
MAX_TEXT = 256
ENABLE_ENV = "CRAFTMCP_GAME_INPUT"


def input_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class GameInputService:
    def __init__(self, captures: ClientCaptureService, *, backend=None, enabled=None):
        self.captures = captures
        self.backend = backend
        self._enabled = enabled
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return input_enabled() if self._enabled is None else self._enabled

    def _backend(self):
        if self.backend is None:
            from mcp_server.adapters.game_input import WindowsGameInput
            self.backend = WindowsGameInput(self.captures._backend())
        return self.backend

    def _client(self, client_id) -> ClientWindow:
        self.captures._identifier(client_id)
        with self.captures._clients_lock:
            client = self.captures.clients.get(client_id)
        if client is None:
            raise CaptureError("unknown_client", "Call list_game_clients and select a current client_id first.")
        return client

    def _exclusive(self):
        if not self._lock.acquire(blocking=False):
            raise CaptureError("input_busy", "Another focus/input sequence is still running.")
        return self._lock

    def focus(self, client_id):
        client = self._client(client_id)
        lock = self._exclusive()
        try:
            return response(client_id=client_id, **self._backend().focus(client))
        finally:
            lock.release()

    @staticmethod
    def validate(actions: list[dict]) -> None:
        if not 1 <= len(actions) <= MAX_ACTIONS:
            raise CaptureError("invalid_actions", f"Send 1..{MAX_ACTIONS} actions per call.")
        total = 0
        for action in actions:
            kind = action.get("type")
            if kind == "key":
                total += action.get("hold_ms", 50)
            elif kind == "click":
                total += (action.get("hold_ms", 50) + 50) * action.get("count", 1)
            elif kind == "wait":
                total += action["ms"]
            elif kind == "type_text":
                text = action["text"]
                if not 1 <= len(text) <= MAX_TEXT or any(ord(char) < 32 or ord(char) == 127 for char in text):
                    raise CaptureError("invalid_text", f"Text must be 1..{MAX_TEXT} printable characters (no newlines).")
                total += 10 * len(text)
        if total > MAX_TOTAL_MS:
            raise CaptureError("sequence_too_long", f"Holds/waits add up to {total} ms; split into calls of at most {MAX_TOTAL_MS} ms.")

    def send(self, client_id, actions: list[dict], *, focus=True):
        if not self.enabled:
            raise CaptureError("input_disabled", f"Game input is off. Set {ENABLE_ENV}=1 in mcp_server/.env and restart the MCP server.")
        self.validate(actions)
        client = self._client(client_id)
        lock = self._exclusive()
        try:
            backend = self._backend()
            focused = backend.focus(client) if focus else None
            done = backend.run(client, actions)
            return response(client_id=client_id, focus=focused, completed_actions=len(done),
                            functional_result="unknown",
                            warnings=["Input was delivered, not verified; capture the screen or query game state to confirm."])
        finally:
            lock.release()

    def chat(self, client_id, text: str, *, focus=True):
        """Open chat, type text (a /command or a message), press Enter."""
        actions = [{"type": "key", "keys": ["t"], "hold_ms": 40}, {"type": "wait", "ms": 300},
                   {"type": "type_text", "text": text}, {"type": "wait", "ms": 100},
                   {"type": "key", "keys": ["enter"], "hold_ms": 40}]
        result = self.send(client_id, actions, focus=focus)
        result["text"] = text
        result["warnings"].append("If a menu/pause screen was open, chat may not have opened; check with a capture.")
        return result

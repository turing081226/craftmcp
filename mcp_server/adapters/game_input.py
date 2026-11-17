"""Bring a Minecraft Java window forward and inject keyboard/mouse input into it.

Input is only ever delivered while the selected, identity-checked Minecraft window
is the foreground window. Focus is re-checked before every step and while keys
are held; if the user switches away, the sequence stops and everything pressed is
released. Uses SendInput, so Minecraft receives it like real hardware input
(including raw mouse motion for camera look).
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field

from mcp_server.adapters.client_capture import CaptureError, ClientWindow, WindowsClientCapture

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_MOVE = 0x0001
MOUSE_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
MOUSEEVENTF_WHEEL, WHEEL_DELTA = 0x0800, 120
VK_MENU = 0x12

# Minecraft default key bindings use these names; values are Windows virtual keys.
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B, "tab": 0x09, "backspace": 0x08,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1, "ctrl": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0xA4, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "slash": 0xBF, "/": 0xBF, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
_EXTENDED = {0x26, 0x28, 0x25, 0x27, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x2D, 0xA3}


def key_code(name: str) -> int:
    key = name.strip().lower()
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        return ord(key.upper())
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    raise CaptureError("invalid_key", f"Unsupported key {name!r}; use a-z, 0-9, or one of {sorted(_NAMED_KEYS)}.")


@dataclass
class _Held:
    keys: list[int] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)


class WindowsGameInput:
    """Focus and SendInput helpers bound to WindowsClientCapture's identity checks."""

    def __init__(self, capture: WindowsClientCapture, *, sleep=time.sleep, clock=time.monotonic):
        self.capture, self.user, self.w = capture, capture.user, capture.w
        self.kernel = capture.kernel
        self.sleep, self.clock = sleep, clock
        w = self.w

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]

        class _UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", w.DWORD), ("u", _UNION)]

        self.INPUT = INPUT

        def api(lib, name, args, returns):
            function = getattr(lib, name)
            function.argtypes, function.restype = args, returns

        api(self.user, "SendInput", [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int], w.UINT)
        api(self.user, "MapVirtualKeyW", [w.UINT, w.UINT], w.UINT)
        api(self.user, "SetForegroundWindow", [w.HWND], w.BOOL)
        api(self.user, "BringWindowToTop", [w.HWND], w.BOOL)
        api(self.user, "ShowWindow", [w.HWND, ctypes.c_int], w.BOOL)
        api(self.user, "AttachThreadInput", [w.DWORD, w.DWORD, w.BOOL], w.BOOL)
        api(self.kernel, "GetCurrentThreadId", [], w.DWORD)

    # ---- low level -------------------------------------------------------
    def _send(self, *inputs) -> None:
        array = (self.INPUT * len(inputs))(*inputs)
        if self.user.SendInput(len(inputs), array, ctypes.sizeof(self.INPUT)) != len(inputs):
            raise CaptureError("input_blocked", "Windows rejected the input (UIPI: is the game running as administrator?).")

    def _key(self, vk: int, up: bool) -> None:
        scan = self.user.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
        item = self.INPUT(type=INPUT_KEYBOARD)
        item.ki.wVk, item.ki.wScan, item.ki.dwFlags = vk, scan, flags
        self._send(item)

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        item = self.INPUT(type=INPUT_MOUSE)
        item.mi.dx, item.mi.dy, item.mi.dwFlags, item.mi.mouseData = dx, dy, flags, data & 0xFFFFFFFF
        self._send(item)

    def _unicode(self, text: str) -> None:
        for char in text:
            data = char.encode("utf-16-le")
            for index in range(0, len(data), 2):
                unit = int.from_bytes(data[index:index + 2], "little")
                items = []
                for up in (False, True):
                    item = self.INPUT(type=INPUT_KEYBOARD)
                    item.ki.wScan, item.ki.dwFlags = unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
                    items.append(item)
                self._send(*items)
            self.sleep(0.01)

    # ---- focus -----------------------------------------------------------
    def _check_identity(self, client: ClientWindow) -> None:
        current = self.capture._inspect(client.handle)
        if current.identity != client.identity:
            raise CaptureError("stale_client", "Game process identity changed; list clients again.")

    def is_foreground(self, client: ClientWindow) -> bool:
        return self.user.GetForegroundWindow() == client.handle

    def focus(self, client: ClientWindow, settle_seconds: float = 0.4) -> dict:
        self._check_identity(client)
        hwnd = client.handle
        if self.is_foreground(client):
            return {"focused": True, "already_foreground": True, "method": "none"}
        if self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)  # SW_RESTORE
        method = None
        for name, attempt in (("direct", self._focus_direct), ("attach_thread", self._focus_attached),
                              ("alt_unlock", self._focus_alt)):
            attempt(hwnd)
            self.sleep(0.15)
            if self.is_foreground(client):
                method = name
                break
        if method is None:
            raise CaptureError("focus_failed", "Windows refused to bring the game window forward; click it once manually.")
        self.sleep(settle_seconds)  # let Minecraft process the focus change
        return {"focused": True, "already_foreground": False, "method": method}

    def _focus_direct(self, hwnd):
        self.user.BringWindowToTop(hwnd)
        self.user.SetForegroundWindow(hwnd)

    def _focus_attached(self, hwnd):
        foreground = self.user.GetForegroundWindow()
        target = self.user.GetWindowThreadProcessId(foreground, None) if foreground else 0
        own = self.kernel.GetCurrentThreadId()
        attached = bool(target) and target != own and self.user.AttachThreadInput(own, target, True)
        try:
            self._focus_direct(hwnd)
        finally:
            if attached:
                self.user.AttachThreadInput(own, target, False)

    def _focus_alt(self, hwnd):
        # A synthetic Alt tap counts as user input and lifts the foreground lock.
        self._key(VK_MENU, False)
        self._key(VK_MENU, True)
        self._focus_direct(hwnd)

    # ---- action sequences ------------------------------------------------
    def _guard(self, client: ClientWindow) -> None:
        if not self.is_foreground(client):
            raise CaptureError("focus_lost", "The game window lost focus; input stopped and all keys were released.")

    def _hold(self, client: ClientWindow, seconds: float) -> None:
        deadline = self.clock() + seconds
        while (remaining := deadline - self.clock()) > 0:
            self._guard(client)
            self.sleep(min(0.05, remaining))

    def run(self, client: ClientWindow, actions: list[dict]) -> list[dict]:
        self._check_identity(client)
        held, done = _Held(), []
        try:
            for index, action in enumerate(actions):
                self._guard(client)
                kind = action["type"]
                if kind == "key":
                    codes = [key_code(name) for name in action["keys"]]
                    for code in codes:
                        self._key(code, False)
                        held.keys.append(code)
                    self._hold(client, action.get("hold_ms", 50) / 1000)
                    for code in reversed(codes):
                        self._key(code, True)
                        held.keys.remove(code)
                elif kind == "click":
                    down, up = MOUSE_BUTTONS[action["button"]]
                    for _ in range(action.get("count", 1)):
                        self._mouse(down)
                        held.buttons.append(action["button"])
                        self._hold(client, action.get("hold_ms", 50) / 1000)
                        self._mouse(up)
                        held.buttons.remove(action["button"])
                        self.sleep(0.05)
                elif kind == "look":
                    dx, dy = action.get("dx", 0), action.get("dy", 0)
                    steps = max(1, max(abs(dx), abs(dy)) // 20)
                    sent_x = sent_y = 0
                    for step in range(1, steps + 1):
                        self._guard(client)
                        x, y = dx * step // steps, dy * step // steps
                        self._mouse(MOUSEEVENTF_MOVE, x - sent_x, y - sent_y)
                        sent_x, sent_y = x, y
                        self.sleep(0.005)
                elif kind == "scroll":
                    self._mouse(MOUSEEVENTF_WHEEL, data=action["amount"] * WHEEL_DELTA)
                elif kind == "type_text":
                    self._unicode(action["text"])
                elif kind == "wait":
                    self._hold(client, action["ms"] / 1000)
                else:
                    raise CaptureError("invalid_action", f"Unsupported action type {kind!r}.")
                done.append({"index": index, "type": kind})
            return done
        finally:
            for code in reversed(held.keys):
                self._key(code, True)
            for button in reversed(held.buttons):
                self._mouse(MOUSE_BUTTONS[button][1])

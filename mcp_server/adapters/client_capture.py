"""Windows Java/OpenGL client discovery and foreground client-area capture.

No screenshots are taken during discovery. GDI calls are scoped to the selected
client area, with visibility, process identity and overlap checks before/after.
"""
from __future__ import annotations

import ctypes
import os
import struct
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PureWindowsPath


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ClientWindow:
    handle: int
    pid: int
    process_started: int
    title: str
    window_class: str
    executable: str

    @property
    def identity(self):
        return (self.handle, self.pid, self.process_started)


@dataclass(frozen=True)
class CapturedFrame:
    png: bytes
    width: int
    height: int
    source_rect: tuple[int, int, int, int]
    warnings: tuple[str, ...] = ()


def encode_rgb_png(width: int, height: int, rgb: bytes) -> bytes:
    if not (0 < width <= 1920 and 0 < height <= 1080) or len(rgb) != width * height * 3:
        raise ValueError("Invalid RGB frame dimensions.")

    def chunk(kind, value):
        return struct.pack("!I", len(value)) + kind + value + struct.pack("!I", zlib.crc32(kind + value))

    stride = width * 3
    scanlines = b"".join(b"\0" + rgb[row:row + stride] for row in range(0, len(rgb), stride))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))


class WindowsClientCapture:
    simulated = False
    def __init__(self):
        if os.name != "nt":
            raise CaptureError("unsupported_platform", "Game-window capture currently requires Windows.")
        from ctypes import wintypes as w
        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def api(lib, name, args, returns):
            function = getattr(lib, name)
            function.argtypes, function.restype = args, returns

        for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
            api(self.user, name, [w.HWND], w.BOOL)
        api(self.user, "GetForegroundWindow", [], w.HWND)
        api(self.user, "EnumWindows", [self.callback_type, w.LPARAM], w.BOOL)
        api(self.user, "GetWindowTextW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int)
        api(self.user, "GetClassNameW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int)
        api(self.user, "GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD)
        api(self.user, "GetClientRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL)
        api(self.user, "GetWindowRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL)
        api(self.user, "ClientToScreen", [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL)
        api(self.user, "GetDC", [w.HWND], w.HDC)
        api(self.user, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int)
        api(self.user, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int)
        api(self.user, "SetThreadDpiAwarenessContext", [w.HANDLE], w.HANDLE)
        api(self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
        api(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)
        api(self.kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL)
        api(self.kernel, "GetProcessTimes", [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL)
        api(self.dwm, "DwmGetWindowAttribute", [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD], ctypes.c_long)
        api(self.gdi, "CreateCompatibleDC", [w.HDC], w.HDC)
        api(self.gdi, "CreateCompatibleBitmap", [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP)
        api(self.gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE)
        api(self.gdi, "DeleteObject", [w.HANDLE], w.BOOL)
        api(self.gdi, "DeleteDC", [w.HDC], w.BOOL)
        api(self.gdi, "SetStretchBltMode", [w.HDC, ctypes.c_int], ctypes.c_int)
        api(self.gdi, "StretchBlt", [w.HDC] + [ctypes.c_int] * 4 + [w.HDC] + [ctypes.c_int] * 4 + [w.DWORD], w.BOOL)
        api(self.gdi, "GetDIBits", [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT], ctypes.c_int)

    @contextmanager
    def _dpi(self):
        previous = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise CaptureError("dpi_unavailable", "Cannot establish physical-pixel coordinates.")
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)

    def _windows(self):
        handles = []
        callback = self.callback_type(lambda hwnd, _: handles.append(int(hwnd)) or True)
        if not self.user.EnumWindows(callback, 0):
            raise CaptureError("window_discovery_failed", "Cannot enumerate desktop windows.")
        return handles

    def _cloaked(self, hwnd):
        value = self.w.DWORD()
        hr = self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
        return hr == 0 and bool(value.value)

    def _inspect(self, hwnd) -> ClientWindow:
        if not self.user.IsWindow(hwnd):
            raise CaptureError("stale_client", "The selected game window has closed.")
        title, kind = ctypes.create_unicode_buffer(1024), ctypes.create_unicode_buffer(256)
        self.user.GetWindowTextW(hwnd, title, len(title))
        self.user.GetClassNameW(hwnd, kind, len(kind))
        if kind.value not in ("GLFW30", "LWJGL"):
            raise CaptureError("unsupported_client", "Expected a Minecraft Java GLFW/LWJGL window.")
        pid = self.w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise CaptureError("process_unavailable", "Cannot inspect the selected game process.")
        try:
            executable, size = ctypes.create_unicode_buffer(32768), self.w.DWORD(32768)
            times = [self.w.FILETIME() for _ in range(4)]
            if (not self.kernel.QueryFullProcessImageNameW(process, 0, executable, ctypes.byref(size))
                    or not self.kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times))):
                raise CaptureError("process_unavailable", "Cannot confirm game process identity.")
            filename = PureWindowsPath(executable.value).name
            if filename.casefold() not in ("java.exe", "javaw.exe"):
                raise CaptureError("unsupported_client", "Expected a Java game process.")
            started = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return ClientWindow(hwnd, pid.value, started, title.value, kind.value, filename)
        finally:
            self.kernel.CloseHandle(process)

    def discover(self) -> list[ClientWindow]:
        clients = []
        for hwnd in self._windows():
            if not self.user.IsWindowVisible(hwnd) or self._cloaked(hwnd):
                continue
            try:
                clients.append(self._inspect(hwnd))
            except CaptureError:
                continue
        return clients

    def _ready(self, client: ClientWindow):
        current = self._inspect(client.handle)
        if current.identity != client.identity:
            raise CaptureError("stale_client", "Game process identity changed; list clients again.")
        if (not self.user.IsWindowVisible(client.handle) or self.user.IsIconic(client.handle)
                or self._cloaked(client.handle)):
            raise CaptureError("client_not_visible", "Restore the game window before capturing.")
        if self.user.GetForegroundWindow() != client.handle:
            raise CaptureError("client_not_foreground", "Bring the selected Minecraft window to the foreground.")
        rect, origin = self.w.RECT(), self.w.POINT()
        if not self.user.GetClientRect(client.handle, ctypes.byref(rect)) or not self.user.ClientToScreen(client.handle, ctypes.byref(origin)):
            raise CaptureError("window_geometry_failed", "Cannot determine the game client area.")
        bounds = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
        left, top, right, bottom = bounds
        desktop_left, desktop_top = self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77)
        desktop_right = desktop_left + self.user.GetSystemMetrics(78)
        desktop_bottom = desktop_top + self.user.GetSystemMetrics(79)
        if (right <= left or bottom <= top or left < desktop_left or top < desktop_top
                or right > desktop_right or bottom > desktop_bottom):
            raise CaptureError("client_offscreen", "The full game client area must be on the desktop.")
        for hwnd in self._windows():
            if hwnd == client.handle:
                break
            if not self.user.IsWindowVisible(hwnd) or self.user.IsIconic(hwnd) or self._cloaked(hwnd):
                continue
            other = self.w.RECT()
            if self.user.GetWindowRect(hwnd, ctypes.byref(other)):
                if max(left, other.left) < min(right, other.right) and max(top, other.top) < min(bottom, other.bottom):
                    raise CaptureError("client_occluded", "Another window overlaps the game; clear it before capturing.")
        return bounds

    def _copy_pixels(self, source_dc, rect) -> CapturedFrame:
        left, top, right, bottom = rect
        source_width, source_height = right - left, bottom - top
        scale = min(1, 1920 / source_width, 1080 / source_height)
        width, height = max(1, int(source_width * scale)), max(1, int(source_height * scale))
        memory = self.gdi.CreateCompatibleDC(source_dc)
        bitmap = self.gdi.CreateCompatibleBitmap(source_dc, width, height)
        previous = None
        try:
            if not memory or not bitmap:
                raise CaptureError("capture_failed", "Cannot allocate a capture bitmap.")
            previous = self.gdi.SelectObject(memory, bitmap)
            if not previous or previous == ctypes.c_void_p(-1).value:
                previous = None
                raise CaptureError("capture_failed", "Cannot select the capture bitmap.")
            self.gdi.SetStretchBltMode(memory, 4)  # HALFTONE
            if not self.gdi.StretchBlt(memory, 0, 0, width, height, source_dc, left, top,
                                       source_width, source_height, 0x00CC0020 | 0x40000000):
                raise CaptureError("capture_failed", "Desktop capture failed.")
            self.gdi.SelectObject(memory, previous)
            previous = None
            # BITMAPINFOHEADER: top-down, uncompressed 32-bit BGRX.
            header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0))
            pixels = ctypes.create_string_buffer(width * height * 4)
            if self.gdi.GetDIBits(memory, bitmap, 0, height, pixels, header, 0) != height:
                raise CaptureError("capture_failed", "Cannot read the captured bitmap.")
            raw = pixels.raw
            rgb = bytearray(width * height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            warning = ("possible_blank_frame",) if bytes(rgb) == bytes(rgb[:3]) * (width * height) else ()
            return CapturedFrame(encode_rgb_png(width, height, rgb), width, height, rect, warning)
        finally:
            if previous:
                self.gdi.SelectObject(memory, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)

    def capture(self, client: ClientWindow) -> CapturedFrame:
        with self._dpi():
            rect = self._ready(client)
            screen = self.user.GetDC(None)
            if not screen:
                raise CaptureError("capture_failed", "An interactive desktop session is required.")
            try:
                frame = self._copy_pixels(screen, rect)
            finally:
                self.user.ReleaseDC(None, screen)
            if self._ready(client) != rect:
                raise CaptureError("client_changed", "The game window moved during capture; frame discarded.")
            return frame

"""Capture protocol, real PNG/GDI buffers, filesystem containment and MCP images."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP

from mcp_server.adapters.client_capture import (
    CapturedFrame, CaptureError, ClientWindow, WindowsClientCapture, encode_rgb_png,
)
from mcp_server.services.client_capture import ClientCaptureService
from mcp_server.tools.client_capture import register_client_capture_tools

ROOT = Path(__file__).resolve().parents[2]


def unpack_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    index, compressed, dimensions = 8, b"", None
    while index < len(png):
        length = struct.unpack("!I", png[index:index + 4])[0]
        kind = png[index + 4:index + 8]
        payload = png[index + 8:index + 8 + length]
        crc = struct.unpack("!I", png[index + 8 + length:index + 12 + length])[0]
        assert crc == zlib.crc32(kind + payload)
        if kind == b"IHDR":
            dimensions = struct.unpack("!2I5B", payload)
        if kind == b"IDAT":
            compressed += payload
        index += length + 12
    return dimensions, zlib.decompress(compressed)


class FakeBackend:
    simulated = True
    def __init__(self):
        self.window = ClientWindow(101, 202, 303, "Minecraft test", "GLFW30", "javaw.exe")
        self.fail = None
        self.calls = 0

    def discover(self):
        return [self.window]

    def capture(self, client):
        self.calls += 1
        if self.fail:
            raise CaptureError(self.fail, "Simulated capture refusal.")
        return CapturedFrame(encode_rgb_png(2, 1, b"\xff\0\0\0\xff\0"), 2, 1, (10, 20, 12, 21))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.backend = FakeBackend()
        self.service = ClientCaptureService(self.root, backend=self.backend)
        self.client_id = self.service.list_clients()["clients"][0]["client_id"]

    def test_capture_saved_image_retrieval_and_explicit_unknown_functional_result(self):
        metadata, png = self.service.capture(self.client_id, "run-1", "before", runtime_id="paper-1", build_id="build-1")
        self.assertEqual(metadata["functional_result"], "unknown")
        self.assertFalse(metadata["correlation_verified"])
        self.assertEqual(metadata["runtime_id"], "paper-1")
        self.assertEqual(Path(metadata["image_path"]).read_bytes(), png)
        saved, retrieved = self.service.read_capture("run-1", metadata["capture_id"])
        self.assertEqual(retrieved, png)
        self.assertEqual(saved, metadata)
        dimensions, scanlines = unpack_png(png)
        self.assertEqual(dimensions[:2], (2, 1))
        self.assertEqual(scanlines, b"\0\xff\0\0\0\xff\0")

    def test_png_tampering_and_cross_run_access_are_rejected(self):
        metadata, _ = self.service.capture(self.client_id, "run-1", "before")
        with self.assertRaises(CaptureError):
            self.service.read_capture("other-run", metadata["capture_id"])
        Path(metadata["image_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(CaptureError, "hash differs"):
            self.service.read_capture("run-1", metadata["capture_id"])

    def test_invalid_paths_rejected_before_backend_capture(self):
        for identifier in ("../escape", "C:escape", "\\escape", "a/b", "CON", "NUL", "x\x00", "a" * 65):
            with self.subTest(identifier=identifier), self.assertRaises(CaptureError):
                self.service.capture(self.client_id, identifier, "before")
        self.assertEqual(self.backend.calls, 0)
        self.assertFalse(self.service.store.exists())

    def test_unknown_client_and_backend_errors_do_not_write_evidence(self):
        with self.assertRaises(CaptureError):
            self.service.capture("unknown", "run-1", "before")
        for code in ("stale_client", "client_not_foreground", "client_occluded", "client_not_visible", "client_changed"):
            self.backend.fail = code
            with self.assertRaises(CaptureError) as raised:
                self.service.capture(self.client_id, "run-1", "before")
            self.assertEqual(raised.exception.code, code)
        self.assertFalse(self.service.store.exists())

    def test_caller_visual_assessment_and_paged_evidence_do_not_assert_server_success(self):
        metadata, _ = self.service.capture(self.client_id, "run-1", "shop-open")
        judgment = self.service.record_assessment("run-1", metadata["capture_id"],
                                                  "Shop title displayed", "Title visible", "passed")
        self.assertEqual(judgment["verdict"], "passed")
        self.assertEqual(judgment["assessment_source"], "caller_visual_review")
        self.assertEqual(judgment["functional_result"], "unknown")
        page = self.service.evidence("run-1", limit=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["next_offset"], 1)
        second = self.service.evidence("run-1", offset=1, limit=1)
        self.assertIsNone(second["next_offset"])
        with self.assertRaises(CaptureError):
            self.service.record_assessment("run-1", "missing", "x", "y", "passed")
        with self.assertRaises(CaptureError):
            self.service.record_assessment("run-1", metadata["capture_id"], "x", "y", "maybe")

    def test_capture_busy_does_not_capture_another_frame(self):
        self.service._operation_lock.acquire()
        try:
            with self.assertRaises(CaptureError) as raised:
                self.service.capture(self.client_id, "run-1", "before")
            self.assertEqual(raised.exception.code, "capture_busy")
        finally:
            self.service._operation_lock.release()
        self.assertEqual(self.backend.calls, 0)

    def test_evidence_symlink_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        directory = self.service.store / "run-1"
        directory.mkdir(parents=True)
        # A symlink inside this workspace is still not allowed as an evidence file.
        link = directory / "fake.json"
        try:
            link.symlink_to(outside / "record.json")
        except OSError:
            self.skipTest("Symlink creation requires developer mode/privilege.")
        with self.assertRaises(CaptureError):
            self.service.evidence("run-1")


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_actual_mcp_image_blocks_and_structured_errors(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            backend = FakeBackend()
            service = ClientCaptureService(Path(directory), backend=backend)
            app = FastMCP("capture-test", strict_input_validation=True)
            register_client_capture_tools(app, service)
            async with Client(app) as client:
                tools = {item.name for item in await client.list_tools()}
                self.assertEqual(tools, {"list_game_clients", "capture_game_view", "get_game_capture",
                                         "record_visual_assertion", "get_visual_run_evidence"})
                listed = await client.call_tool("list_game_clients", {})
                client_id = listed.structured_content["clients"][0]["client_id"]
                captured = await client.call_tool("capture_game_view", {"client_id": client_id, "run_id": "mcp-run", "label": "before"})
                self.assertEqual([item.type for item in captured.content], ["text", "image"])
                self.assertEqual(captured.content[1].mimeType, "image/png")
                self.assertTrue(captured.structured_content["ok"])
                capture_id = captured.structured_content["capture_id"]
                reloaded = await client.call_tool("get_game_capture", {"run_id": "mcp-run", "capture_id": capture_id})
                self.assertEqual(reloaded.content[1].data, captured.content[1].data)
                reviewed = await client.call_tool("record_visual_assertion", {"run_id": "mcp-run", "capture_id": capture_id,
                    "expected": "red and green", "observed": "red and green", "verdict": "passed"})
                self.assertEqual(reviewed.structured_content["functional_result"], "unknown")
                backend.fail = "client_not_foreground"
                failed = await client.call_tool("capture_game_view", {"client_id": client_id, "run_id": "mcp-run", "label": "after"})
                self.assertFalse(failed.structured_content["ok"])
                self.assertEqual(failed.structured_content["error"]["code"], "client_not_foreground")
                self.assertFalse(any(item.type == "image" for item in failed.content))

    async def test_capture_worker_does_not_block_other_tools(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            backend = FakeBackend()
            started, finish = threading.Event(), threading.Event()
            original = backend.capture
            def waiting(window):
                started.set()
                if not finish.wait(30):
                    raise TimeoutError("test capture not released")
                return original(window)
            backend.capture = waiting
            service = ClientCaptureService(Path(directory), backend=backend)
            identifier = service.list_clients()["clients"][0]["client_id"]
            app = FastMCP("capture-concurrency")
            register_client_capture_tools(app, service)
            async with Client(app) as client:
                await client.list_tools()
                capture = asyncio.create_task(client.call_tool("capture_game_view", {"client_id": identifier, "run_id": "run", "label": "before"}))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 10))
                    listed = await asyncio.wait_for(client.call_tool("list_game_clients", {}), 10)
                    self.assertTrue(listed.structured_content["ok"])
                finally:
                    finish.set()
                self.assertTrue((await capture).structured_content["ok"])


@unittest.skipUnless(os.name == "nt", "Native GDI tests require Windows")
class NativeWindowsTests(unittest.TestCase):
    def test_gdi_capture_from_synthetic_bitmap_has_correct_png_pixels(self):
        # Exercise the real GDI capture/readback path without capturing desktop pixels.
        backend = WindowsClientCapture()
        w = backend.w
        backend.gdi.CreateSolidBrush.argtypes = [w.DWORD]
        backend.gdi.CreateSolidBrush.restype = w.HBRUSH
        backend.user.FillRect.argtypes = [w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH]
        backend.user.FillRect.restype = ctypes.c_int
        screen = backend.user.GetDC(None)
        if not screen:
            self.skipTest("No interactive desktop DC available")
        memory = backend.gdi.CreateCompatibleDC(screen)
        bitmap = backend.gdi.CreateCompatibleBitmap(screen, 64, 32)
        previous = backend.gdi.SelectObject(memory, bitmap)
        brush = backend.gdi.CreateSolidBrush(0x0000FF)  # COLORREF red
        try:
            rect = w.RECT(0, 0, 64, 32)
            self.assertTrue(backend.user.FillRect(memory, ctypes.byref(rect), brush))
            frame = backend._copy_pixels(memory, (0, 0, 64, 32))
            dimensions, rows = unpack_png(frame.png)
            self.assertEqual(dimensions[:2], (64, 32))
            self.assertEqual(rows, (b"\0" + b"\xff\0\0" * 64) * 32)
            self.assertIn("possible_blank_frame", frame.warnings)
        finally:
            backend.gdi.DeleteObject(brush)
            backend.gdi.SelectObject(memory, previous)
            backend.gdi.DeleteObject(bitmap)
            backend.gdi.DeleteDC(memory)
            backend.user.ReleaseDC(None, screen)

    def test_native_discovery_is_read_only_and_returns_only_java_opengl_candidates(self):
        backend = WindowsClientCapture()
        for window in backend.discover():
            self.assertIn(window.executable.lower(), ("java.exe", "javaw.exe"))
            self.assertIn(window.window_class, ("GLFW30", "LWJGL"))
            self.assertGreater(window.process_started, 0)

    def test_stale_window_is_rejected_before_screenshot(self):
        backend = WindowsClientCapture()
        with patch.object(backend, "_copy_pixels") as capture:
            with self.assertRaises(CaptureError):
                backend.capture(ClientWindow(0, 1, 1, "closed", "GLFW30", "java.exe"))
        capture.assert_not_called()

    def test_background_or_minimized_window_is_rejected_before_capture(self):
        backend = WindowsClientCapture()
        client = ClientWindow(101, 1, 1, "test", "GLFW30", "java.exe")
        with patch.object(backend, "_inspect", return_value=client), \
                patch.object(backend, "_cloaked", return_value=False), \
                patch.object(backend.user, "IsWindowVisible", return_value=True), \
                patch.object(backend.user, "IsIconic", return_value=False), \
                patch.object(backend.user, "GetForegroundWindow", return_value=202):
            with self.assertRaises(CaptureError) as error:
                backend._ready(client)
            self.assertEqual(error.exception.code, "client_not_foreground")
            with patch.object(backend.user, "IsIconic", return_value=True):
                with self.assertRaises(CaptureError) as error:
                    backend._ready(client)
                self.assertEqual(error.exception.code, "client_not_visible")

    def test_reused_handle_or_pid_identity_is_rejected(self):
        backend = WindowsClientCapture()
        old = ClientWindow(101, 1, 1, "test", "GLFW30", "java.exe")
        new = ClientWindow(101, 1, 2, "test", "GLFW30", "java.exe")
        with patch.object(backend, "_inspect", return_value=new):
            with self.assertRaises(CaptureError) as error:
                backend._ready(old)
            self.assertEqual(error.exception.code, "stale_client")


if __name__ == "__main__":
    unittest.main()

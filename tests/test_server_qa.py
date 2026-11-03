"""Regression tests for docs/qa*.md using isolated files and loopback RCON peers."""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from mcp_server.adapters.rcon import execute_rcon
from mcp_server.services.server_log import read_server_log

ROOT = Path(__file__).resolve().parents[1]


def packet(packet_type: int, text: str = "", request_id: int = 0) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + text.encode("utf-8") + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


class RconPeer:
    """A tiny protocol peer; no existing Minecraft instance or fixed port is used."""

    def __init__(self, mode="normal"):
        self.mode = mode
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.connections = 0
        self.commands = []
        self.errors = []
        self.tasks = set()

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections += 1
        self.closed.clear()
        mode = self.mode

        async def receive():
            length, = struct.unpack("<i", await reader.readexactly(4))
            data = await reader.readexactly(length)
            _, kind = struct.unpack("<ii", data[:8])
            return kind, data[8:-2].decode("utf-8")

        try:
            kind, _ = await receive()
            if kind != 3:
                raise AssertionError("Expected authentication")
            self.started.set()
            if mode == "auth_eof":
                return
            if mode == "auth_silence":
                await reader.read()  # Must see client EOF on timeout, without driver termination.
                return
            if mode == "auth_rejected":
                writer.write(packet(2, request_id=-1))
                await writer.drain()
                await reader.read()
                return
            writer.write(packet(2))
            await writer.drain()
            kind, command = await receive()
            if kind != 2:
                raise AssertionError("Expected command")
            self.commands.append(command)
            if mode == "command_eof":
                return
            if mode == "command_silence":
                await reader.read()
                return
            if mode == "drip":
                # Every byte arrives within the socket timeout, but not the total deadline.
                for byte in packet(0, "slow"):
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(0.2)
                return
            if mode == "bad_length":
                writer.write(struct.pack("<i", 2**31 - 1))
            else:
                writer.write(packet(0, "접속자 0명"))
            await writer.drain()
            if mode == "missing_marker":
                await reader.read()
                return
            if mode != "bad_length":
                kind, text = await receive()
                if kind != 0 or text:
                    raise AssertionError("Expected completion marker")
                writer.write(packet(0, "wrong marker" if mode == "invalid_marker" else "Unknown request 0", request_id=1))
                await writer.drain()
            await reader.read()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # Expected when the bounded client closes a failed/partial response.
        except Exception as error:
            self.errors.append(error)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.closed.set()
            self.tasks.discard(task)


class RconTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_auth_failure_and_protocol_error(self):
        for mode, exception in (("normal", None), ("auth_rejected", PermissionError),
                                ("bad_length", ConnectionError), ("missing_marker", TimeoutError),
                                ("invalid_marker", ConnectionError)):
            with self.subTest(mode=mode):
                async with RconPeer(mode) as peer:
                    call = asyncio.to_thread(execute_rcon, "list", "fake-password", port=peer.port, timeout=2)
                    if exception:
                        with self.assertRaises(exception):
                            await asyncio.wait_for(call, 4)
                    else:
                        self.assertEqual(await asyncio.wait_for(call, 4), "접속자 0명")
                    await asyncio.wait_for(peer.closed.wait(), 2)
                    self.assertFalse(peer.errors)

    async def test_no_response_eof_and_drip_have_bounded_cleanup_without_retry(self):
        for mode in ("auth_silence", "auth_eof", "command_silence", "command_eof", "drip"):
            async with RconPeer(mode) as peer:
                for _ in range(3):
                    with self.subTest(mode=mode):
                        peer.closed.clear()
                        started = time.monotonic()
                        with self.assertRaises((TimeoutError, ConnectionError)):
                            await asyncio.wait_for(asyncio.to_thread(execute_rcon, "list", "fake-password",
                                                                    port=peer.port, timeout=1), 4)
                        self.assertLess(time.monotonic() - started, 3)
                        await asyncio.wait_for(peer.closed.wait(), 2)
                self.assertEqual(peer.connections, 3)
                self.assertEqual(len(peer.commands), 0 if mode.startswith("auth") else 3)
                self.assertFalse(peer.errors)

    async def test_connection_refused(self):
        async with RconPeer() as peer:
            port = peer.port
        with self.assertRaises((ConnectionError, TimeoutError)):
            await asyncio.wait_for(asyncio.to_thread(execute_rcon, "list", "fake-password", port=port, timeout=1), 3)

    async def test_delayed_packets_are_collected_until_marker(self):
        async def handle(reader, writer):
            try:
                async def receive():
                    length, = struct.unpack("<i", await reader.readexactly(4))
                    return await reader.readexactly(length)
                await receive()
                writer.write(packet(2))
                await writer.drain()
                await receive()
                writer.write(packet(0, "first-"))
                await writer.drain()
                await receive()  # Completion request arrives before the delayed final packet.
                await asyncio.sleep(0.15)
                writer.write(packet(0, "second") + packet(0, "Unknown request 0", request_id=1))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
        listener = await asyncio.start_server(handle, "127.0.0.1", 0)
        try:
            port = listener.sockets[0].getsockname()[1]
            for _ in range(3):
                result = await asyncio.wait_for(asyncio.to_thread(execute_rcon, "list", "fake", port=port, timeout=3), 5)
                self.assertEqual(result, "first-second")
        finally:
            listener.close()
            await listener.wait_closed()

    async def test_common_stdio_app_recovers_and_stays_responsive_during_rcon_wait(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        async with RconPeer() as peer:
            with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
                root = Path(directory)
                (root / "logs").mkdir()
                (root / "logs/latest.log").write_bytes("테스트\n".encode("utf-8"))
                transport = StdioTransport(
                    command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(ROOT), keep_alive=False,
                    env={**os.environ, "DEBUGGER_CONFIG": str(ROOT / "debugger.example.json"),
                         "MC_RCON_PASSWORD": "fake-password", "MC_RCON_PORT": str(peer.port),
                         "MC_RCON_TIMEOUT": "1.5", "MC_SERVER_DIR": str(root)},
                    log_file=root / "mcp-stderr.log",
                )
                async with Client(transport, timeout=10, init_timeout=30) as client:
                    names = {tool.name for tool in await client.list_tools()}
                    self.assertTrue({"list_players", "get_server_log", "attach_debugger",
                                     "get_debug_state", "resume_execution"} <= names)
                    for mode in ("auth_silence", "auth_eof", "auth_rejected", "normal"):
                        with self.subTest(mode=mode):
                            peer.mode = mode
                            peer.started.clear()
                            peer.closed.clear()
                            call = asyncio.create_task(client.call_tool("list_players", {}))
                            try:
                                await asyncio.wait_for(peer.started.wait(), 4)
                                if mode == "auth_silence":
                                    # These must return while the original RCON call still waits.
                                    await asyncio.wait_for(client.list_tools(), 1)
                                    state = await asyncio.wait_for(client.call_tool("get_debug_state", {"session_id": "unknown"}), 1)
                                    self.assertEqual(state.data["error"]["code"], "session_not_found")
                                    self.assertFalse(call.done())
                                if mode == "normal":
                                    result = await asyncio.wait_for(call, 4)
                                    self.assertEqual(result.content[0].text, "접속자 0명")
                                else:
                                    with self.assertRaises(ToolError):
                                        await asyncio.wait_for(call, 4)
                                await asyncio.wait_for(peer.closed.wait(), 2)
                            finally:
                                if not call.done():
                                    call.cancel()
                                await asyncio.gather(call, return_exceptions=True)
                    response = await client.call_tool("get_server_log", {"max_lines": 1})
                    log = json.loads(response.content[0].text)
                    self.assertTrue(log["ok"])
                    self.assertEqual(log["raw_output"], "테스트\n")
                    continued = await client.call_tool("get_server_log", {"cursor": log["next_cursor"]})
                    self.assertEqual(json.loads(continued.content[0].text)["raw_output"], "")
                self.assertEqual(peer.connections, 4)
                self.assertEqual(peer.commands, ["list"])
                self.assertFalse(peer.errors)


class LogTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "latest.log"

    def test_paging_append_and_eof(self):
        self.path.write_bytes("첫 줄\n둘째 줄\n".encode("utf-8"))
        first = read_server_log(self.path, max_lines=1)
        self.assertEqual(first["raw_output"], "첫 줄\n")
        second = read_server_log(self.path, cursor=first["next_cursor"], start_offset=first["next_offset"])
        self.assertEqual(second["raw_output"], "둘째 줄\n")
        self.assertFalse(second["reset"])
        eof = read_server_log(self.path, cursor=second["next_cursor"])
        self.assertEqual(eof["next_offset"], second["next_offset"])
        self.assertEqual(eof["raw_output"], "")
        with self.path.open("ab") as output:
            output.write("추가\n".encode("utf-8"))
        self.assertEqual(read_server_log(self.path, cursor=eof["next_cursor"])["raw_output"], "추가\n")

    def test_replacement_and_same_file_rewrite_reset_without_missing_prefix(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.path.write_bytes(b"old\n")
                first = read_server_log(self.path)
                if replace:
                    replacement = self.path.with_name("replacement.log")
                    replacement.write_bytes(b"new-first\nnew-second\n")
                    replacement.replace(self.path)
                else:
                    self.path.write_bytes(b"new-first\nnew-second\n")
                result = read_server_log(self.path, cursor=first["next_cursor"])
                self.assertTrue(result["reset"])
                self.assertEqual(result["raw_output"], "new-first\nnew-second\n")

    def test_truncation_resets(self):
        self.path.write_bytes(b"old-long-line\n")
        first = read_server_log(self.path)
        self.path.write_bytes(b"new\n")
        result = read_server_log(self.path, cursor=first["next_cursor"])
        self.assertEqual(result["reset_reason"], "file_truncated")
        self.assertEqual(result["raw_output"], "new\n")

    def test_partial_utf8_and_unterminated_line_do_not_advance_cursor(self):
        encoded = "한\n".encode("utf-8")
        self.path.write_bytes(b"complete\n" + encoded[:1])
        first = read_server_log(self.path)
        self.assertEqual(first["raw_output"], "complete\n")
        self.assertEqual(first["next_offset"], len(b"complete\n"))
        with self.path.open("ab") as output:
            output.write(encoded[1:-1])
        pending = read_server_log(self.path, cursor=first["next_cursor"])
        self.assertEqual(pending["raw_output"], "")
        self.assertEqual(pending["next_offset"], first["next_offset"])
        with self.path.open("ab") as output:
            output.write(b"\n")
        self.assertEqual(read_server_log(self.path, cursor=pending["next_cursor"])["raw_output"], "한\n")

    def test_invalid_or_missing_cursor_cannot_silently_skip_log_prefix(self):
        self.path.write_bytes(b"new-first\nnew-second\n")
        for cursor in (None, "not json", "[]", "null", '{"v":1}'):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                read_server_log(self.path, start_offset=4, cursor=cursor)
        first = read_server_log(self.path, max_lines=1)
        with self.assertRaises(ValueError):
            read_server_log(self.path, start_offset=2, cursor=first["next_cursor"])

    def test_tool_reports_missing_file_and_invalid_arguments(self):
        from mcp_server.server import get_server_log

        with patch.dict(os.environ, {"MC_SERVER_DIR": self.temp.name}):
            missing = json.loads(get_server_log.fn())
            self.assertFalse(missing["ok"])
            for args in ({"start_offset": -1}, {"max_lines": 0}, {"max_lines": 1001},
                         {"start_offset": 4}, {"cursor": "invalid"}):
                with self.subTest(args=args):
                    result = json.loads(get_server_log.fn(**args))
                    self.assertFalse(result["ok"])
            numeric = json.loads(get_server_log.fn(start_offset=4))
            self.assertTrue(numeric["reset_required"])
            self.assertEqual(numeric["next_offset"], 0)


if __name__ == "__main__":
    unittest.main()

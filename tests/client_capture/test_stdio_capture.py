"""Exercise actual shared stdio registration without taking desktop screenshots."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[2]


class SharedStdioCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_tools_and_legacy_tools_coexist_on_shared_stdio_app(self):
        asyncio.get_running_loop().set_debug(False)
        (ROOT / ".cache").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            transport = StdioTransport(
                command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(ROOT), keep_alive=False,
                env={**os.environ, "DEBUGGER_CONFIG": str(ROOT / "debugger.example.json")},
                log_file=Path(directory) / "stdio.log",
            )
            async with Client(transport, timeout=30, init_timeout=60) as client:
                tools = await client.list_tools()
                names = [tool.name for tool in tools]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(len(names), 26)
                self.assertTrue({"list_game_clients", "capture_game_view", "get_game_capture",
                                 "record_visual_assertion", "get_visual_run_evidence",
                                 "list_players", "get_server_log", "build_plugin", "deploy_plugin",
                                 "attach_debugger", "detach_debugger", "list_runtimes", "get_runtime_status",
                                 "start_server", "stop_server", "get_plugin_status", "rollback_plugin",
                                 "execute_command", "get_job", "cancel_job"} <= set(names))
                listed = await client.call_tool("list_game_clients", {})
                if os.name == "nt":
                    self.assertTrue(listed.structured_content["ok"], listed.structured_content)
                else:
                    self.assertEqual(listed.structured_content["error"]["code"], "unsupported_platform")
                # An unregistered ID guarantees no desktop capture in this test.
                failed = await client.call_tool("capture_game_view", {
                    "client_id": "not-registered", "run_id": "stdio-smoke", "label": "not-captured",
                })
                self.assertFalse(failed.structured_content["ok"])
                self.assertEqual(failed.structured_content["error"]["code"], "unknown_client")
                self.assertFalse(any(item.type == "image" for item in failed.content))


if __name__ == "__main__":
    unittest.main()

"""Real in-memory MCP calls to A's hook; no shared server changes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from mcp_server.models.contracts import RuntimeTarget
from mcp_server.services.debugger import DebuggerConfig
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.tools.runtime import register_runtime_tools


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_debugger_config_maps_to_the_same_target(self):
        root = Path(__file__).resolve().parents[2]
        config = DebuggerConfig.load(root / "debugger.example.json", workspace=root)
        service = RuntimeService(RuntimeRegistry.from_debugger_config(config))
        target = service.resolve("paper-local")
        self.assertEqual(target.project_id, config.runtimes["paper-local"].project_id)
        self.assertEqual(target.server_jar, config.runtimes["paper-local"].jar)
        self.assertEqual(target.jdwp.port, config.runtimes["paper-local"].port)
        self.assertFalse(target.managed)
        self.assertIsNone(target.rcon)

    async def test_discovery_calls_and_strict_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = RuntimeTarget("paper", "sample", root, root, root / "paper.jar")
            app = FastMCP("runtime test", strict_input_validation=True)
            register_runtime_tools(app, RuntimeService(RuntimeRegistry([target])))
            async with Client(app) as client:
                tools = await client.list_tools()
                self.assertEqual({tool.name for tool in tools}, {"list_runtimes", "get_runtime_status"})
                self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
                listing = (await client.call_tool("list_runtimes", {})).data
                self.assertEqual(listing["result"]["runtimes"][0]["runtime_id"], "paper")
                status = (await client.call_tool("get_runtime_status", {"runtime_id": "paper"})).data
                self.assertTrue(status["ok"])
                missing = (await client.call_tool("get_runtime_status", {"runtime_id": "typo"})).data
                self.assertEqual(missing["error"]["code"], "runtime_not_found")
                for invalid in (0, True, ""):
                    with self.subTest(invalid=invalid), self.assertRaises(ToolError):
                        await client.call_tool("get_runtime_status", {"runtime_id": invalid})

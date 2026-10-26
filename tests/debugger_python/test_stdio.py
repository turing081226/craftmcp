"""Real MCP stdio transport against the simulated helper, never real JDI."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[2]


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_tools_over_stdio(self):
        transport = StdioTransport(command=sys.executable,
                                   args=["-m", "mcp_server.debugger_app", "--config", str(ROOT / "debugger.example.json"),
                                         "--workspace", str(ROOT), "--mock-helper"], cwd=str(ROOT), keep_alive=False)
        evidence_path = None
        async with Client(transport, timeout=20) as client:
            self.assertEqual(len(await client.list_tools()), 8)
            attach = (await client.call_tool("attach_debugger", {"runtime_id": "paper-local", "project_id": "debugger-sample"})).data
            self.assertTrue(attach["ok"])
            self.assertTrue(attach["evidence"]["simulated"])
            evidence_path = Path(attach["evidence"]["path"])
            sid = attach["result"]["session_id"]
            bp = (await client.call_tool("set_breakpoint", {"session_id": sid,
                  "class_name": "io.github.craftmcp.debuggersample.DamageCalculator", "line": 7,
                  "source_path": "src/main/java/io/github/craftmcp/debuggersample/DamageCalculator.java"})).data
            self.assertTrue(bp["ok"])
            state = (await client.call_tool("get_debug_state", {"session_id": sid})).data["result"]
            stop = state["active_stop"]
            stack = (await client.call_tool("get_stack_trace", {"session_id": sid, "stop_id": stop["stop_id"],
                     "thread_id": stop["thread_id"], "limit": 1})).data["result"]
            variables = (await client.call_tool("get_variables", {"session_id": sid, "stop_id": stop["stop_id"],
                         "frame_id": stack["frames"][0]["frame_id"]})).data["result"]
            self.assertEqual(next(v["display_value"] for v in variables["variables"] if v["name"] == "damage"), "10")
            self.assertTrue((await client.call_tool("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})).data["ok"])
            stale = (await client.call_tool("get_variables", {"session_id": sid, "stop_id": stop["stop_id"],
                     "frame_id": stack["frames"][0]["frame_id"]})).data
            self.assertEqual(stale["error"]["code"], "stale_reference")
            self.assertTrue((await client.call_tool("remove_breakpoint", {"session_id": sid, "breakpoint_id": bp["result"]["breakpoint_id"]})).data["ok"])
            self.assertTrue((await client.call_tool("detach_debugger", {"session_id": sid})).data["ok"])
        records = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["kind"] == "service_closed" for r in records))
        self.assertTrue(any(r["kind"] == "process_exited" and r["payload"]["returncode"] == 0 for r in records))
        print(f"Simulated stdio evidence: {evidence_path}")


if __name__ == "__main__":
    unittest.main()

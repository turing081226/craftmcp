"""Real helper/JVM probe over MCP. Run manually while a developer uses the Paper console."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[2]


async def probe(args):
    transport = StdioTransport(sys.executable, ["-m", "mcp_server.debugger_app", "--config", str(args.config.resolve()),
                               "--workspace", str(ROOT)], cwd=str(ROOT), keep_alive=False)
    evidence_path = None
    sid = None
    observed = {}
    async with Client(transport, timeout=30) as client:
        async def call(name, params, *, expected_error=None):
            nonlocal evidence_path
            data = (await client.call_tool(name, params)).data
            evidence_path = Path(data["evidence"]["path"])
            if data["evidence"]["simulated"]:
                raise AssertionError("Real probe must never use simulated observations.")
            if expected_error:
                if data.get("error", {}).get("code") != expected_error:
                    raise AssertionError(f"Expected {expected_error}: {data}")
            elif not data["ok"]:
                raise AssertionError(data["error"])
            return data.get("result")

        try:
            attach = await call("attach_debugger", {"runtime_id": args.runtime, "project_id": args.project})
            sid = attach["session_id"]
            bp = await call("set_breakpoint", {"session_id": sid, "class_name": args.class_name,
                                              "line": args.line, "source_path": args.source})
            if bp["status"] == "rejected":
                raise AssertionError(bp)
            print("Now run in the local Paper console: craftdebug calculate probe-001 7 3", file=sys.stderr, flush=True)
            deadline = time.monotonic() + args.wait_seconds
            stop = None
            while time.monotonic() < deadline:
                state = await call("get_debug_state", {"session_id": sid})
                if state["active_stop"]:
                    stop = state["active_stop"]
                    break
                await asyncio.sleep(0.25)
            if stop is None:
                raise AssertionError("No stop observed before the reproduction deadline.")
            frames = await call("get_stack_trace", {"session_id": sid, "stop_id": stop["stop_id"],
                               "thread_id": stop["thread_id"], "offset": 0, "limit": 100})
            frame = next(f for f in frames["frames"] if f["class_name"] == args.class_name)
            params = {"session_id": sid, "stop_id": stop["stop_id"], "frame_id": frame["frame_id"], "offset": 0, "limit": 100}
            variables = await call("get_variables", params)
            damage = next(v for v in variables["variables"] if v["name"] == "damage")
            if damage["display_value"] != str(args.expected_damage):
                raise AssertionError(f"Unexpected damage: {damage}")
            for name, expected in (("base", "7"), ("bonus", "3")):
                if next(v["display_value"] for v in variables["variables"] if v["name"] == name) != expected:
                    raise AssertionError(f"Unexpected local {name}.")
            result_ref = next(v["object_ref"] for v in variables["variables"] if v["name"] == "result")
            fields = await call("get_variables", {"session_id": sid, "stop_id": stop["stop_id"], "object_ref": result_ref, "offset": 0, "limit": 100})
            field_damage = next(v for v in fields["variables"] if v["name"] == "damage")
            if field_damage["display_value"] != str(args.expected_damage):
                raise AssertionError(field_damage)
            array_ref = next(v["object_ref"] for v in fields["variables"] if v["name"] == "inputs")
            elements = await call("get_variables", {"session_id": sid, "stop_id": stop["stop_id"], "object_ref": array_ref, "offset": 0, "limit": 100})
            if [(v["name"], v["display_value"]) for v in elements["variables"]] != [("[0]", "7"), ("[1]", "3")]:
                raise AssertionError("Unexpected inputs array.")
            observed = {"stop": stop, "frame": frame, "variables": variables, "fields": fields, "elements": elements}
            await call("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})
            await call("get_variables", params, expected_error="stale_reference")
            await call("get_variables", {"session_id": sid, "stop_id": stop["stop_id"], "object_ref": result_ref, "offset": 0, "limit": 100}, expected_error="stale_reference")
            await call("remove_breakpoint", {"session_id": sid, "breakpoint_id": bp["breakpoint_id"]})
            await call("detach_debugger", {"session_id": sid})
            sid = None
        finally:
            if sid:
                await client.call_tool("detach_debugger", {"session_id": sid})
    summary = {"simulated": False, "mcp_jvm_flow": "passed", "console_command_completion": "manual confirmation required",
               "expected_damage": args.expected_damage, "observed": observed, "evidence_path": str(evidence_path)}
    (evidence_path.parent / "probe-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "observed"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "debugger.local.json")
    parser.add_argument("--runtime", default="paper-local")
    parser.add_argument("--project", default="debugger-sample")
    parser.add_argument("--class-name", default="io.github.craftmcp.debuggersample.DamageCalculator")
    parser.add_argument("--source", default="src/main/java/io/github/craftmcp/debuggersample/DamageCalculator.java")
    parser.add_argument("--line", type=int, default=7)
    parser.add_argument("--wait-seconds", type=float, default=60)
    parser.add_argument("--expected-damage", type=int, default=10)
    asyncio.run(probe(parser.parse_args()))

"""Transport-independent D driver. call_tool is an actual MCP client's JSON boundary."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

from mcp_server.services.debugger_fixture import (
    BREAKPOINT_LINE, CLASS_NAME, COMMAND_ID, SOURCE_PATH, TIMEOUT_SECONDS, command_text, parse_output,
)

TERMINAL = {"succeeded", "failed", "unknown", "cancelled"}


class DebuggerFlow:
    def __init__(self, call_tool, record, *, runtime_id, project_id, run_id, timeout_seconds=TIMEOUT_SECONDS):
        self.call_tool = call_tool
        self.record = record
        self.runtime_id = runtime_id
        self.project_id = project_id
        self.run_id = run_id
        self.timeout_seconds = timeout_seconds
        self.session_id = None
        self.breakpoint_id = None
        self.bindings = {"runtime_id": runtime_id, "project_id": project_id, "run_id": run_id,
                         "job_id": None, "session_id": None, "stop_id": None, "debugger_run_id": None}

    async def call(self, name, params, *, error=None):
        data = await self.call_tool(name, params)
        if isinstance(data, str):
            data = json.loads(data)
        self.record("tool_result", {"tool": name, "arguments": params, "response": data})
        if data.get("evidence"):
            self.bindings["debugger_run_id"] = data["evidence"]["run_id"]
            self.bindings["debugger_evidence_path"] = data["evidence"]["path"]
        if error:
            if data.get("ok") is not False or data.get("error", {}).get("code") != error:
                raise AssertionError(f"Expected {error}: {data}")
            return data["error"]
        if data.get("ok") is not True:
            raise AssertionError(f"{name} failed: {data}")
        # B's public job tools return a JSON string with job fields at the top level.
        # Debugger tools and the D-only test seam use the existing result envelope.
        return data.get("result", data)

    async def attach(self):
        attached = await self.call("attach_debugger", {"runtime_id": self.runtime_id, "project_id": self.project_id})
        self.session_id = attached["session_id"]
        self.bindings.update(session_id=self.session_id, stop_id=None, job_id=None, request_id=None)
        bp = await self.call("set_breakpoint", {"session_id": self.session_id, "class_name": CLASS_NAME,
                                               "source_path": SOURCE_PATH, "line": BREAKPOINT_LINE})
        if bp["status"] not in {"pending", "verified"}:
            raise AssertionError(f"Breakpoint rejected: {bp}")
        self.breakpoint_id = bp["breakpoint_id"]
        self.record("id_binding", self.bindings.copy())

    async def submit(self, *, arguments=None, request_id=None):
        arguments = {"base": 7, "bonus": 3} if arguments is None else arguments
        request = {"command_id": COMMAND_ID, "arguments": arguments, "runtime_id": self.runtime_id,
                   "run_id": self.run_id, "request_id": request_id or uuid.uuid4().hex,
                   "timeout_seconds": self.timeout_seconds}
        submitted = await self.call("execute_command", request)
        self.bindings.update(job_id=submitted["job_id"], request_id=request["request_id"],
                             session_id=self.session_id, stop_id=None)
        self.record("id_binding", {**self.bindings, "command_text": command_text(arguments, self.run_id)})
        return submitted

    async def wait_stop(self):
        deadline = time.monotonic() + min(self.timeout_seconds, 15)
        while time.monotonic() < deadline:
            state = await self.call("get_debug_state", {"session_id": self.session_id})
            if state["active_stop"]:
                stop = state["active_stop"]
                if stop["breakpoint_id"] != self.breakpoint_id:
                    raise AssertionError("Stopped at a different breakpoint")
                self.bindings["stop_id"] = stop["stop_id"]
                self.record("id_binding", self.bindings.copy())
                return stop
            job = await self.call("get_job", {"job_id": self.bindings["job_id"]})
            if job["status"] in TERMINAL:
                raise AssertionError(f"Command finished before hitting the breakpoint: {job}")
            await asyncio.sleep(0.02)
        raise AssertionError("No breakpoint before deadline")

    async def wait_job(self):
        deadline = time.monotonic() + self.timeout_seconds + 2
        while time.monotonic() < deadline:
            job = await self.call("get_job", {"job_id": self.bindings["job_id"]})
            if job["status"] in TERMINAL:
                if job["run_id"] != self.run_id or job["runtime_id"] != self.runtime_id:
                    raise AssertionError("Job correlation mismatch")
                return job
            await asyncio.sleep(0.05)
        raise AssertionError("get_job did not reach a terminal state")

    async def cleanup(self):
        if self.session_id:
            # Detach first on failure: removing a breakpoint alone does not release a held stop.
            await self.call("detach_debugger", {"session_id": self.session_id})
            self.session_id = None
            self.breakpoint_id = None

    async def run(self):
        try:
            await self.attach()
            await self.submit()
            stop = await self.wait_stop()
            job = await self.call("get_job", {"job_id": self.bindings["job_id"]})
            if job["status"] not in {"queued", "running"}:
                raise AssertionError("Command job must remain pending while the JVM is held")
            frame = None
            offset = 0
            while True:
                page = await self.call("get_stack_trace", {"session_id": self.session_id,
                    "stop_id": stop["stop_id"], "thread_id": stop["thread_id"], "offset": offset, "limit": 100})
                frame = next((f for f in page["frames"] if f["class_name"] == CLASS_NAME
                              and f["method_name"] == "calculate" and f["line"] == BREAKPOINT_LINE), None)
                if frame or page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            if frame is None:
                raise AssertionError("No matching calculate frame at the contracted source line")
            params = {"session_id": self.session_id, "stop_id": stop["stop_id"], "frame_id": frame["frame_id"],
                      "offset": 0, "limit": 100}
            variables = await self.call("get_variables", params)
            rows = {v["name"]: v for v in variables["variables"]}
            for name, expected in (("base", "7"), ("bonus", "3"), ("damage", "10")):
                if rows[name]["display_value"] != expected:
                    raise AssertionError(f"Wrong {name}: {rows[name]}")
            # String values are rendered by JDI; check the exact run via the result object's String display.
            if rows["runId"]["display_value"] not in {self.run_id, f'"{self.run_id}"'}:
                raise AssertionError("Breakpoint belongs to a different fixture invocation")
            result_ref = rows["result"]["object_ref"]
            fields = await self.call("get_variables", {"session_id": self.session_id, "stop_id": stop["stop_id"],
                                                       "object_ref": result_ref, "offset": 0, "limit": 100})
            fields = {v["name"]: v for v in fields["variables"]}
            for name, expected in (("base", "7"), ("bonus", "3"), ("damage", "10")):
                if fields[name]["display_value"] != expected:
                    raise AssertionError(f"Unexpected result.{name}")
            if fields["runId"]["display_value"] not in {self.run_id, f'"{self.run_id}"'}:
                raise AssertionError("Result object belongs to a different invocation")
            elements = await self.call("get_variables", {"session_id": self.session_id, "stop_id": stop["stop_id"],
                "object_ref": fields["inputs"]["object_ref"], "offset": 0, "limit": 100})
            if [(v["name"], v["display_value"]) for v in elements["variables"]] != [("[0]", "7"), ("[1]", "3")]:
                raise AssertionError("Unexpected result.inputs")
            await self.call("resume_execution", {"session_id": self.session_id, "stop_id": stop["stop_id"]})
            final_job = await self.wait_job()
            parsed = parse_output(final_job["raw_output"], run_id=self.run_id, arguments={"base": 7, "bonus": 3})
            if final_job["status"] != "succeeded" or parsed["status"] != "succeeded":
                raise AssertionError(f"No successful RCON completion: {final_job}")
            await self.call("get_variables", params, error="stale_reference")
            await self.call("get_variables", {"session_id": self.session_id, "stop_id": stop["stop_id"],
                "object_ref": result_ref, "offset": 0, "limit": 100}, error="stale_reference")
            debug_bindings = self.bindings.copy()
            await self.call("remove_breakpoint", {"session_id": self.session_id, "breakpoint_id": self.breakpoint_id})
            await self.cleanup()
            # A new request executes a new command; the run groups the debug and regression phases.
            await self.submit()
            regression = await self.wait_job()
            if regression["status"] != "succeeded" or parse_output(regression["raw_output"],
                    run_id=self.run_id, arguments={"base": 7, "bonus": 3})["status"] != "succeeded":
                raise AssertionError("Breakpoint-free regression failed")
            summary = {"status": "passed", "bindings": debug_bindings, "regression_bindings": self.bindings.copy(),
                       "debug_job": final_job, "regression_job": regression, "observed_stop": stop}
            self.record("flow_passed", summary)
            return summary
        finally:
            if self.session_id:
                already_failing = sys.exc_info()[0] is not None
                try:
                    await self.cleanup()
                except Exception as exc:
                    self.record("cleanup_failed", {"error": str(exc), "session_id": self.session_id})
                    if not already_failing:
                        raise

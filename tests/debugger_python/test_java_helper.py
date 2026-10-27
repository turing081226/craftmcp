"""Actual A helper + actual JVM, including a separate stdio MCP server."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp_server.adapters.debugger import DebuggerError
from mcp_server.services.debugger import DebuggerConfig, DebuggerService, Project, Runtime

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "debugger_helper/build/libs/craftmcp-debugger-helper.jar"
CLASSES = ROOT / "debugger_helper/build/fixtures/debuggee"
NO_VARS = ROOT / "debugger_helper/build/fixtures/no-vars"
SOURCES = ROOT / "tests/fixtures/java_debuggee"
CLASS = "io.craftmcp.fixture.Calculator"
SOURCE = SOURCES / "src/io/craftmcp/fixture/Calculator.java"
LINE = next(i for i, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1) if "BREAKPOINT_AFTER_CALC" in line)
BUILD_AVAILABLE = HELPER.is_file() and (CLASSES / "io/craftmcp/fixture/Debuggee.class").is_file() and NO_VARS.is_dir()


def make_runtime_jar(classes: Path, path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nMain-Class: io.craftmcp.fixture.Debuggee\r\n\r\n")
        for compiled in classes.rglob("*.class"):
            jar.write(compiled, compiled.relative_to(classes).as_posix())


class Fixture:
    def __init__(self, jar: Path):
        self.jar = jar
        self.boot_id = uuid.uuid4().hex
        self.lines = []
        self.queue = asyncio.Queue()
        self.tasks = []
        self.record = lambda kind, payload: None

    async def start(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = await asyncio.create_subprocess_exec("java",
            "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:0",
            "-jar", str(self.jar), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=flags)
        self.tasks = [asyncio.create_task(self.stdout()), asyncio.create_task(self.stderr())]
        listening = await self.wait_line("Listening for transport dt_socket at address:")
        self.port = int(re.search(r"address: (\d+)", listening).group(1))
        await self.wait_line("READY")

    async def stdout(self):
        while line := await self.process.stdout.readline():
            value = line.decode("utf-8").rstrip("\r\n")
            self.lines.append(value)
            self.record("debuggee_stdout", {"runtime_boot_id": self.boot_id, "text": value})
            await self.queue.put(value)
        await self.queue.put(None)

    async def stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self.record("debuggee_stderr", {"text": chunk.decode("utf-8", errors="replace")})

    async def command(self, command):
        self.record("debuggee_input", {"command": command})
        self.process.stdin.write((command + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def wait_line(self, prefix, timeout=8):
        async def wait():
            while True:
                line = await self.queue.get()
                if line is None:
                    raise AssertionError(f"Debuggee exited before {prefix}: {self.lines}")
                if line.startswith(prefix):
                    return line
        return await asyncio.wait_for(wait(), timeout)

    async def close(self):
        if self.process.returncode is None:
            try:
                await self.command("quit")
                self.process.stdin.close()
                await asyncio.wait_for(self.process.wait(), 3)
            except (OSError, asyncio.TimeoutError):
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(*self.tasks, return_exceptions=True)


@unittest.skipUnless(BUILD_AVAILABLE, "Build A helper + both Java fixtures; see docs/debugger-python.md")
class JavaHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="jdi-python-", dir=ROOT / ".cache")
        self.directory = Path(self.temp.name).resolve()
        self.targets = []
        self.services = []

    async def asyncTearDown(self):
        try:
            for service in self.services:
                await service.close()
        finally:
            for target in self.targets:
                await target.close()
            self.temp.cleanup()

    async def target(self, no_vars=False):
        jar = self.directory / ("no-vars.jar" if no_vars else "debuggee.jar")
        make_runtime_jar(NO_VARS if no_vars else CLASSES, jar)
        target = Fixture(jar)
        self.targets.append(target)
        await target.start()
        return target

    def config(self, target, lease=5000):
        return DebuggerConfig(ROOT, HELPER, "java", {"fixture": Project(SOURCES)},
                              {"fixture-local": Runtime("fixture", "127.0.0.1", target.port, target.jar,
                                                        target.boot_id, "standalone Java fixture; not Paper")},
                              ROOT / "data/debugger", stop_timeout_ms=lease)

    async def service(self, target, lease=5000):
        service = DebuggerService(self.config(target, lease))
        self.services.append(service)
        target.record = service.evidence.write
        service.evidence.write("fixture_started", {"runtime_boot_id": target.boot_id, "stdout": target.lines,
                              "command": ["java", "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:0", "-jar", str(target.jar)]})
        attach = await service.attach_debugger("fixture-local", "fixture")
        return service, attach["session_id"]

    async def call(self, service, method, **params):
        return await service.call(method, params)

    async def stop(self, service, sid, target):
        bp = await self.call(service, "set_breakpoint", session_id=sid, class_name=CLASS, line=LINE,
                             source_path="src/io/craftmcp/fixture/Calculator.java")
        self.assertEqual(bp["status"], "pending")
        await target.command("run")
        state = await self.poll_state(service, sid, lambda state: state["active_stop"] is not None)
        self.assertEqual(state["breakpoints"][0]["status"], "verified")
        return bp, state["active_stop"]

    async def poll_state(self, service, sid, predicate, timeout=5):
        async def wait():
            while True:
                state = await self.call(service, "get_debug_state", session_id=sid)
                if predicate(state):
                    return state
                await asyncio.sleep(0.02)
        return await asyncio.wait_for(wait(), timeout)

    async def frame(self, service, sid, stop):
        stack = await self.call(service, "get_stack_trace", session_id=sid, stop_id=stop["stop_id"],
                                thread_id=stop["thread_id"], offset=0, limit=20)
        return next(f for f in stack["frames"] if f["class_name"] == CLASS)

    def summary(self, service, name, **details):
        record = {"test": name, "status": "passed", "simulated": False, "target": "standalone Java fixture",
                  "paper_verified": False, "evidence_path": str(service.evidence.path), **details}
        service.evidence.write("validation_summary", record)
        (service.evidence.directory / "validation-summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Actual JVM evidence ({name}): {service.evidence.path}")

    async def test_all_eight_tools_over_real_stdio_and_jdi(self):
        target = await self.target()
        config = self.config(target)
        config_path = self.directory / "stdio.json"
        config_path.write_text(json.dumps({"helper_jar": str(HELPER), "evidence_dir": "data/debugger",
            "stop_timeout_ms": 10000, "projects": {"fixture": {"root": str(SOURCES)}},
            "runtimes": {"fixture-local": {"project_id": "fixture", "host": "127.0.0.1", "port": target.port,
                                         "jar": str(target.jar), "boot_id": target.boot_id}}}), encoding="utf-8")
        transport = StdioTransport(sys.executable, ["-m", "mcp_server.debugger_app", "--config", str(config_path),
                                  "--workspace", str(ROOT)], cwd=str(ROOT), keep_alive=False)
        async with Client(transport, timeout=15) as client:
            self.assertEqual(len(await client.list_tools()), 8)
            async def tool(name, params, error=None):
                data = (await client.call_tool(name, params)).data
                self.assertFalse(data["evidence"]["simulated"])
                if error:
                    self.assertFalse(data["ok"])
                    self.assertEqual(data["error"]["code"], error)
                else:
                    self.assertTrue(data["ok"], data)
                return data
            attach = await tool("attach_debugger", {"runtime_id": "fixture-local", "project_id": "fixture"})
            evidence = Path(attach["evidence"]["path"])
            sid = attach["result"]["session_id"]
            # Capture fixture input/output in the same JSON Lines evidence file.
            def record(kind, payload):
                with (evidence.parent / "fixture.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"run_id": attach["evidence"]["run_id"], "kind": kind,
                                            "simulated": False, "payload": payload}, ensure_ascii=False) + "\n")
            target.record = record
            record("fixture_started", {"runtime_boot_id": target.boot_id, "stdout": target.lines})
            bp = (await tool("set_breakpoint", {"session_id": sid, "class_name": CLASS, "line": LINE,
                  "source_path": "src/io/craftmcp/fixture/Calculator.java"}))["result"]
            self.assertEqual(bp["status"], "pending")
            await target.command("run")
            async def stopped():
                while True:
                    state = (await tool("get_debug_state", {"session_id": sid}))["result"]
                    if state["active_stop"]:
                        return state
                    await asyncio.sleep(0.02)
            state = await asyncio.wait_for(stopped(), 5)
            self.assertEqual(state["breakpoints"][0]["status"], "verified")
            stop = state["active_stop"]
            stack_args = {"session_id": sid, "stop_id": stop["stop_id"], "thread_id": stop["thread_id"], "offset": 0, "limit": 1}
            frame = (await tool("get_stack_trace", stack_args))["result"]["frames"][0]
            self.assertEqual(frame["class_name"], CLASS)
            self.assertEqual(frame["line"], LINE)
            self.assertEqual((await tool("get_stack_trace", stack_args))["result"]["frames"][0]["frame_id"], frame["frame_id"])
            args = {"session_id": sid, "stop_id": stop["stop_id"], "frame_id": frame["frame_id"], "offset": 0, "limit": 2}
            rows = []
            while True:
                page = (await tool("get_variables", args))["result"]
                rows += page["variables"]
                if page["next_offset"] is None:
                    break
                args["offset"] = page["next_offset"]
            values = {v["name"]: v for v in rows}
            for name, expected in (("base", "4"), ("multiplier", "3"), ("damage", "12")):
                self.assertEqual(values[name]["display_value"], expected)
            self.assertEqual(values["large"]["display_value"], "\uD55C" * 4096)
            self.assertTrue(values["large"]["truncated"])
            ref = values["this"]["object_ref"]
            self.assertEqual(ref, values["target"]["object_ref"])
            object_args = {"session_id": sid, "stop_id": stop["stop_id"], "object_ref": ref, "offset": 0, "limit": 100}
            fields = {v["name"]: v for v in (await tool("get_variables", object_args))["result"]["variables"]}
            self.assertEqual(fields["self"]["object_ref"], ref)
            self.assertEqual(fields["label"]["display_value"], "\uD14C\uC2A4\uD2B8 \uB300\uC0C1\n\uB450 \uBC88\uC9F8 \uC904")
            items = (await tool("get_variables", {**object_args, "object_ref": fields["samples"]["object_ref"]}))["result"]
            self.assertEqual([v["display_value"] for v in items["variables"]], ["4", "3", "12"])
            wide_args = {**object_args, "object_ref": fields["wide"]["object_ref"]}
            wide = (await tool("get_variables", wide_args))["result"]
            self.assertTrue(wide["truncated"])
            self.assertIsNotNone(wide["next_offset"])
            self.assertLessEqual(len(json.dumps(wide, ensure_ascii=False).encode("utf-8")), 512 * 1024)
            await tool("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})
            result = await target.wait_line("RESULT damage=12")
            await tool("get_variables", args, error="stale_reference")
            await tool("get_variables", object_args, error="stale_reference")
            await tool("remove_breakpoint", {"session_id": sid, "breakpoint_id": bp["breakpoint_id"]})
            await tool("detach_debugger", {"session_id": sid})
        records = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["kind"] == "service_closed" for r in records))
        summary = {"test": "stdio_mcp_jdi", "status": "passed", "simulated": False, "paper_verified": False,
                   "expected_damage": 12, "observed_damage": values["damage"]["display_value"], "normal_completion": result,
                   "frame": frame, "evidence_path": str(evidence), "fixture_log": str(evidence.parent / "fixture.jsonl")}
        (evidence.parent / "validation-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Actual stdio MCP/JDI evidence: {evidence}")

    async def test_lease_expiry_releases_actual_jvm(self):
        target = await self.target()
        service, sid = await self.service(target, lease=500)
        bp, stop = await self.stop(service, sid, target)
        frame = await self.frame(service, sid, stop)
        await target.wait_line("RESULT damage=12")
        state = await self.poll_state(service, sid, lambda state: state["active_stop"] is None)
        error = await service.invoke("get_variables", {"session_id": sid, "stop_id": stop["stop_id"],
                                      "frame_id": frame["frame_id"], "offset": 0, "limit": 20})
        self.assertEqual(error["error"]["code"], "stale_reference")
        records = [json.loads(line) for line in service.evidence.path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["kind"] == "event" and r["payload"]["event"] == "resumed" and r["payload"]["data"]["reason"] == "lease_expired" for r in records))
        self.summary(service, "lease_expiry", observed_damage=12)

    async def test_shutdown_releases_actual_jvm(self):
        target = await self.target()
        service, sid = await self.service(target)
        await self.stop(service, sid, target)
        await service.close()
        result = await target.wait_line("RESULT damage=12")
        self.assertEqual(service._client.process.returncode, 0)
        self.summary(service, "shutdown", normal_completion=result)

    async def test_stdin_eof_releases_actual_jvm(self):
        target = await self.target()
        service, sid = await self.service(target)
        await self.stop(service, sid, target)
        service._client.process.stdin.close()
        await asyncio.wait_for(service._client.process.wait(), 5)
        result = await target.wait_line("RESULT damage=12")
        self.assertEqual(service._client.process.returncode, 0)
        self.summary(service, "stdin_eof", normal_completion=result)

    async def test_vm_termination_keeps_helper_alive(self):
        target = await self.target()
        service, sid = await self.service(target)
        await target.command("quit")
        await target.wait_line("BYE")
        await asyncio.wait_for(target.process.wait(), 5)
        state = await self.poll_state(service, sid, lambda state: state["connection_state"] != "connected")
        self.assertIsNone(state["active_stop"])
        self.assertIsNone(service._client.process.returncode)
        error = await service.invoke("set_breakpoint", {"session_id": sid, "class_name": CLASS, "line": LINE})
        self.assertEqual(error["error"]["code"], "vm_disconnected")
        self.summary(service, "vm_termination", connection_state=state["connection_state"])

    async def test_missing_metadata_preserves_fallback_pages(self):
        target = await self.target(no_vars=True)
        service, sid = await self.service(target)
        bp, stop = await self.stop(service, sid, target)
        frame = await self.frame(service, sid, stop)
        args = {"session_id": sid, "stop_id": stop["stop_id"], "frame_id": frame["frame_id"], "offset": 0, "limit": 1}
        error = await service.invoke("get_variables", args)
        self.assertEqual(error["error"]["code"], "debug_info_missing")
        details = error["error"]["details"]
        self.assertEqual(details["argument_values"][0]["display_value"], "4")
        self.assertEqual(details["argument_next_offset"], 1)
        second = await service.invoke("get_variables", {**args, "offset": 1})
        self.assertEqual(second["error"]["details"]["argument_values"][0]["display_value"], "3")
        self.assertIsNone(second["error"]["details"]["argument_next_offset"])
        this = details["this_value"]["object_ref"]
        fields = await self.call(service, "get_variables", session_id=sid, stop_id=stop["stop_id"], object_ref=this, offset=0, limit=100)
        self.assertIn("label", {v["name"] for v in fields["variables"]})
        await self.call(service, "resume_execution", session_id=sid, stop_id=stop["stop_id"])
        await target.wait_line("RESULT damage=12")
        self.summary(service, "debug_info_missing", fallback_values=["4", "3"])

    async def test_helper_force_exit_releases_actual_jvm(self):
        target = await self.target()
        service, sid = await self.service(target)
        await self.stop(service, sid, target)
        service._client.process.kill()
        await asyncio.wait_for(service._client.process.wait(), 5)
        result = await target.wait_line("RESULT damage=12")
        response = await service.invoke("get_debug_state", {"session_id": sid})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "backend_unavailable")
        self.summary(service, "helper_force_exit", normal_completion=result)


if __name__ == "__main__":
    if not BUILD_AVAILABLE:
        sys.exit("Build A helper and Java fixtures before running the real integration suite.")
    unittest.main()

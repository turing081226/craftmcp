from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_server.adapters.debugger import DebuggerError, HelperClient
from mcp_server.adapters.debugger.models import decode_message, validate_params
from mcp_server.services.debugger import DebuggerConfig, DebuggerService

ROOT = Path(__file__).resolve().parents[2]
MOCK = Path(__file__).with_name("mock_helper.py")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []
        self.records = []

    def client(self, mode="normal", **kwargs):
        client = HelperClient([sys.executable, "-u", str(MOCK), "--mode", mode],
                              record=lambda k, p: self.records.append((k, p)), **kwargs)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()

    async def attach(self, client, lease=2000):
        result = await client.request("attach_debugger", {"host": "127.0.0.1", "port": 5005,
                                     "connect_timeout_ms": 100, "stop_timeout_ms": lease})
        return result["session_id"]

    async def stopped(self, client, session, **kwargs):
        result = await client.request("set_breakpoint", {"session_id": session, "class_name": "sample.Test", "line": 12, **kwargs})
        state = await client.request("get_debug_state", {"session_id": session})
        return result, state["active_stop"]

    async def test_full_flow_utf8_paging_objects_and_stale_references(self):
        client = self.client()
        session = await self.attach(client)
        bp, stop = await self.stopped(client, session)
        response_position = next(i for i, (k, p) in enumerate(self.records) if k == "response" and p.get("result", {}).get("breakpoint_id") == bp["breakpoint_id"])
        event_position = next(i for i, (k, p) in enumerate(self.records) if k == "event" and p["event"] == "stopped")
        self.assertLess(event_position, response_position)
        stack = await client.request("get_stack_trace", {"session_id": session, "stop_id": stop["stop_id"],
                                    "thread_id": stop["thread_id"], "offset": 0, "limit": 1})
        self.assertEqual(stack["next_offset"], 1)
        frame = stack["frames"][0]["frame_id"]
        params = {"session_id": session, "stop_id": stop["stop_id"], "frame_id": frame, "offset": 0, "limit": 100}
        values = await client.request("get_variables", params)
        self.assertEqual(values["variables"][2]["display_value"], "10")
        self.assertEqual(values["variables"][3]["display_value"], "한글\n테스트")
        self.assertTrue(values["variables"][-1]["truncated"])
        self.assertTrue(values["truncated"])
        again = await client.request("get_variables", params)
        ref = values["variables"][4]["object_ref"]
        self.assertEqual(again["variables"][4]["object_ref"], ref)
        page = await client.request("get_variables", {"session_id": session, "stop_id": stop["stop_id"], "object_ref": ref, "offset": 0, "limit": 1})
        self.assertEqual(page["next_offset"], 1)
        fields = await client.request("get_variables", {"session_id": session, "stop_id": stop["stop_id"], "object_ref": ref, "offset": 1, "limit": 1})
        array = fields["variables"][0]["object_ref"]
        items = await client.request("get_variables", {"session_id": session, "stop_id": stop["stop_id"], "object_ref": array, "offset": 0, "limit": 100})
        self.assertEqual(items["variables"][1]["display_value"], "3")
        await client.request("resume_execution", {"session_id": session, "stop_id": stop["stop_id"]})
        with self.assertRaises(DebuggerError) as error:
            await client.request("get_variables", params)
        self.assertEqual(error.exception.code, "stale_reference")
        await client.request("remove_breakpoint", {"session_id": session, "breakpoint_id": bp["breakpoint_id"]})
        await client.request("detach_debugger", {"session_id": session})
        with self.assertRaises(DebuggerError) as error:
            await client.request("set_breakpoint", {"session_id": session, "class_name": "Test", "line": 1})
        self.assertEqual(error.exception.code, "vm_disconnected")
        new = await self.attach(client)
        self.assertNotEqual(new, session)

    async def test_lease_expiry_and_pending_breakpoint(self):
        client = self.client()
        sid = await self.attach(client, lease=150)
        bp = await client.request("set_breakpoint", {"session_id": sid, "class_name": "sample.Pending", "line": 12})
        self.assertEqual(bp["status"], "pending")
        await asyncio.sleep(0.20)
        state = await client.request("get_debug_state", {"session_id": sid})
        self.assertEqual(state["breakpoints"][0]["status"], "verified")
        stop = state["active_stop"]
        self.assertIsNotNone(stop)
        await asyncio.sleep(0.20)
        state = await client.request("get_debug_state", {"session_id": sid})
        self.assertIsNone(state["active_stop"])
        self.assertTrue(any(p.get("event") == "resumed" and p["data"]["reason"] == "lease_expired" for k, p in self.records if k == "event"))
        with self.assertRaises(DebuggerError) as error:
            await client.request("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})
        self.assertEqual(error.exception.code, "stale_reference")
        rejected = await client.request("set_breakpoint", {"session_id": sid, "class_name": "sample.Test", "line": 999})
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["locations"], [])

    async def test_timeout_late_response_is_not_retried(self):
        client = self.client("late_attach", request_timeout=0.05)
        with self.assertRaises(DebuggerError) as error:
            await self.attach(client)
        self.assertEqual(error.exception.code, "request_timeout")
        await asyncio.sleep(0.25)
        late = [p for k, p in self.records if k == "late_response"]
        self.assertEqual(len(late), 1)
        sid = late[0]["result"]["session_id"]
        state = await client.request("get_debug_state", {"session_id": sid})
        self.assertEqual(state["connection_state"], "connected")
        self.assertEqual(sum(k == "request" and p["method"] == "attach_debugger" for k, p in self.records), 1)

    async def test_out_of_order_and_cancellation(self):
        client = self.client("delay_state")
        sid = await self.attach(client)
        bp, stop = await self.stopped(client, sid)
        slow = asyncio.create_task(client.request("get_debug_state", {"session_id": sid}))
        await asyncio.sleep(0.02)
        await client.request("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})
        snapshot = await slow
        self.assertIsNotNone(snapshot["active_stop"])  # old snapshot is not a Python state cache
        live = await client.request("get_debug_state", {"session_id": sid})
        self.assertIsNone(live["active_stop"])
        cancelled = asyncio.create_task(client.request("get_debug_state", {"session_id": sid}))
        await asyncio.sleep(0.02)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        await asyncio.sleep(0.20)
        self.assertTrue(any(k == "late_response" for k, p in self.records))
        self.assertEqual(client._pending, {})

    async def test_pending_requests_fail_on_exit(self):
        client = self.client("exit_pending")
        with self.assertRaises(DebuggerError) as error:
            await self.attach(client)
        self.assertEqual(error.exception.code, "backend_unavailable")
        self.assertEqual(client._pending, {})

    async def test_reject_invalid_stdout_and_version(self):
        for mode in ("bad_json", "stdout_log", "version_mismatch", "oversized", "invalid_utf8"):
            with self.subTest(mode=mode):
                client = self.client(mode)
                with self.assertRaises(DebuggerError) as error:
                    await client.start()
                self.assertEqual(error.exception.code, "protocol_error")
                self.assertIsNotNone(client.process.returncode)

    async def test_stderr_drain_and_error_extension_fields(self):
        flood = self.client("stderr_flood")
        await self.attach(flood)
        self.assertGreaterEqual(sum(len(p["text"]) for k, p in self.records if k == "stderr"), 2 * 1024 * 1024)
        client = self.client("extra_error_fields")
        sid = await self.attach(client)
        with self.assertRaises(DebuggerError) as error:
            await self.stopped(client, sid)
        self.assertEqual(error.exception.code, "ambiguous_class")
        self.assertEqual(error.exception.details["class_loader_ids"], ["loader-a", "loader-b"])

    async def test_evidence_failure_still_closes_helper(self):
        client = self.client()
        sid = await self.attach(client)
        await self.stopped(client, sid)
        def fail_record(kind, payload):
            raise OSError("disk full")
        client.record = fail_record
        with self.assertRaises(DebuggerError) as error:
            await client.close()
        self.assertEqual(error.exception.code, "evidence_unavailable")
        self.assertEqual(client.process.returncode, 0)
        self.assertTrue(client._closed)

    async def test_vm_termination_keeps_helper_alive(self):
        client = self.client("vm_terminated")
        sid = await self.attach(client)
        await client.request("set_breakpoint", {"session_id": sid, "class_name": "sample.Test", "line": 12})
        state = await client.request("get_debug_state", {"session_id": sid})
        self.assertEqual(state["connection_state"], "terminated")
        self.assertIsNone(state["active_stop"])
        self.assertIsNone(client.process.returncode)
        with self.assertRaises(DebuggerError) as error:
            await client.request("get_stack_trace", {"session_id": sid, "stop_id": "expired", "thread_id": "mock-thread", "offset": 0, "limit": 1})
        self.assertEqual(error.exception.code, "vm_disconnected")
        self.assertTrue(any(k == "event" and p["event"] == "disconnected" and p["data"]["reason"] == "vm_terminated" for k, p in self.records))

    async def test_utf8_stderr_survives_chunk_boundaries(self):
        client = self.client("stderr_unicode")
        await self.attach(client)
        await client.close()
        logged = "".join(p["text"] for k, p in self.records if k == "stderr")
        self.assertIn("\uD55C\uAE00" * 3000, logged)
        self.assertNotIn("\uFFFD", logged)

    async def test_ready_timeout_cleans_process(self):
        client = self.client("no_ready", ready_timeout=0.05, shutdown_timeout=0.05)
        with self.assertRaises(DebuggerError) as error:
            await client.start()
        self.assertEqual(error.exception.code, "ready_timeout")
        self.assertIsNotNone(client.process.returncode)

    async def test_shutdown_and_stdin_eof_release_stop(self):
        for eof in (False, True):
            client = self.client()
            sid = await self.attach(client)
            await self.stopped(client, sid)
            if eof:
                client.process.stdin.close()
                await asyncio.wait_for(client.process.wait(), 2)
                await asyncio.sleep(0.05)
            await client.close()
            self.assertEqual(client.process.returncode, 0)
        self.assertGreaterEqual(sum(k == "event" and p["event"] == "resumed" and p["data"]["reason"] == "shutdown" for k, p in self.records), 2)


class ModelTests(unittest.TestCase):
    def test_strict_params_and_exclusive_refs(self):
        valid = {"session_id": "s", "stop_id": "t", "frame_id": "f", "offset": 0, "limit": 1}
        validate_params("get_variables", valid)
        for extra in ({"limit": True}, {"limit": 101}, {"offset": "0"}, {"expression": "evil()"}, {"object_ref": "o"}, {"frame_id": None}):
            with self.subTest(extra=extra), self.assertRaises(DebuggerError):
                validate_params("get_variables", {**valid, **extra})

    def test_duplicate_json_and_wrong_envelopes(self):
        for line in (b'{"protocol_version":1,"protocol_version":1}\n', b'[]\n',
                     b'{"protocol_version":true,"type":"response"}\n',
                     b'{"protocol_version":1,"type":"response","id":"r","ok":true,"result":{},"error":{}}\n'):
            with self.subTest(line=line), self.assertRaises(DebuggerError):
                decode_message(line)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.root = Path(self.temp.name).resolve()
        (self.root / "source.java").write_text("class Test {}", encoding="utf-8")
        config_data = {"helper_jar": "helper.jar", "projects": {"sample": {"root": "."}},
                       "runtimes": {"local": {"project_id": "sample", "host": "127.0.0.1", "port": 5005, "jar": "paper.jar"}}}
        path = self.root / "config.json"
        path.write_text(json.dumps(config_data), encoding="utf-8")
        self.config = DebuggerConfig.load(path, workspace=self.root)
        factory = lambda command, **kwargs: HelperClient([sys.executable, "-u", str(MOCK)], **kwargs)
        self.service = DebuggerService(self.config, client_factory=factory, simulated=True)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_registry_path_validation_and_evidence(self):
        error = await self.service.invoke("attach_debugger", {"runtime_id": "unknown", "project_id": "sample"})
        self.assertEqual(error["error"]["code"], "invalid_params")
        result = await self.service.invoke("attach_debugger", {"runtime_id": "local", "project_id": "sample"})
        sid = result["result"]["session_id"]
        for source in ("../escape.java", str(ROOT / "README.md")):
            response = await self.service.invoke("set_breakpoint", {"session_id": sid, "class_name": "sample.Test", "line": 12, "source_path": source})
            self.assertEqual(response["error"]["code"], "invalid_params")
        response = await self.service.invoke("set_breakpoint", {"session_id": sid, "class_name": "sample.Test", "line": 12, "source_path": "source.java"})
        self.assertTrue(response["ok"])
        records = [json.loads(line) for line in self.service.evidence.path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(item["simulated"] for item in records))
        requests = [r["payload"] for r in records if r["kind"] == "request" and r["payload"]["method"] == "set_breakpoint"]
        self.assertEqual(requests[0]["params"]["source_path"], str(self.root / "source.java"))
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.service.evidence.context["source_match"], "unverified")

    async def test_late_attach_can_be_inspected_without_retry(self):
        await self.service.close()
        factory = lambda command, **kwargs: HelperClient([sys.executable, "-u", str(MOCK), "--mode", "late_attach"], **kwargs)
        from dataclasses import replace
        config = replace(self.config, request_timeout=0.05)
        self.service = DebuggerService(config, client_factory=factory, simulated=True)
        timed_out = await self.service.invoke("attach_debugger", {"runtime_id": "local", "project_id": "sample"})
        self.assertEqual(timed_out["error"]["code"], "request_timeout")
        await asyncio.sleep(0.25)
        records = [json.loads(line) for line in self.service.evidence.path.read_text(encoding="utf-8").splitlines()]
        binding = next(r["payload"] for r in records if r["kind"] == "late_session_bound")
        state = await self.service.invoke("get_debug_state", {"session_id": binding["session_id"]})
        self.assertTrue(state["ok"])
        self.assertEqual(sum(r["kind"] == "request" and r["payload"]["method"] == "attach_debugger" for r in records), 1)

    async def test_registered_external_runtime_is_read_only(self):
        data = {"helper_jar": "helper.jar", "projects": {"sample": {"root": ".", "artifact_jar": str(ROOT / "plugin_template/debugger_sample/build/libs/craftmcp-debugger-sample.jar")}},
                "runtimes": {"external": {"project_id": "sample", "host": "127.0.0.1", "port": 5005, "jar": str(ROOT / "mc_server/paper.jar")}}}
        path = self.root / "external.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        config = DebuggerConfig.load(path, workspace=self.root)
        self.assertEqual(config.runtimes["external"].jar, ROOT / "mc_server/paper.jar")
        self.assertEqual(config.projects["sample"].artifact_jar, ROOT / "plugin_template/debugger_sample/build/libs/craftmcp-debugger-sample.jar")
        self.assertFalse(config.runtimes["external"].jar.is_file())

    async def test_real_mode_requires_runtime_jar(self):
        service = DebuggerService(self.config)
        try:
            with self.assertRaises(DebuggerError) as error:
                await service.attach_debugger("local", "sample")
            self.assertEqual(error.exception.code, "invalid_params")
        finally:
            await service.close()

    async def test_mcp_tool_contract(self):
        from fastmcp import Client
        from mcp_server.debugger_app import create_app
        from fastmcp.exceptions import ToolError
        app = create_app(self.service)
        async with Client(app) as client:
            names = {tool.name for tool in await client.list_tools()}
            self.assertEqual(names, {"attach_debugger", "set_breakpoint", "remove_breakpoint", "get_debug_state",
                                     "get_stack_trace", "get_variables", "resume_execution", "detach_debugger"})
            attach = (await client.call_tool("attach_debugger", {"runtime_id": "local", "project_id": "sample"})).data
            self.assertTrue(attach["ok"])
            sid = attach["result"]["session_id"]
            bp = (await client.call_tool("set_breakpoint", {"session_id": sid, "class_name": "sample.Test", "line": 12})).data
            state = (await client.call_tool("get_debug_state", {"session_id": sid})).data["result"]
            stop = state["active_stop"]
            args = {"session_id": sid, "stop_id": stop["stop_id"], "thread_id": stop["thread_id"]}
            stack = (await client.call_tool("get_stack_trace", args)).data["result"]
            variables = (await client.call_tool("get_variables", {"session_id": sid, "stop_id": stop["stop_id"], "frame_id": stack["frames"][0]["frame_id"]})).data
            self.assertEqual(variables["result"]["variables"][2]["display_value"], "10")
            with self.assertRaises(ToolError):
                await client.call_tool("get_stack_trace", {**args, "limit": True})
            with self.assertRaises(ToolError):
                await client.call_tool("get_stack_trace", {**args, "limit": 101})
            bad = (await client.call_tool("get_variables", {"session_id": sid, "stop_id": stop["stop_id"]})).data
            self.assertEqual(bad["error"]["code"], "invalid_params")
            await client.call_tool("resume_execution", {"session_id": sid, "stop_id": stop["stop_id"]})
            await client.call_tool("remove_breakpoint", {"session_id": sid, "breakpoint_id": bp["result"]["breakpoint_id"]})
            await client.call_tool("detach_debugger", {"session_id": sid})


if __name__ == "__main__":
    unittest.main()

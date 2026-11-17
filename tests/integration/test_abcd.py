"""Actual A lifecycle + B jobs + C artifacts + shared D registration boundaries."""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
import threading
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from fastmcp import Client, FastMCP
from mcp_server import server
from mcp_server.models.contracts import RuntimeTarget, RconEndpoint
from mcp_server.services.debugger import DebuggerService, Runtime, Project
from mcp_server.adapters.debugger import DebuggerError
from dataclasses import replace
from mcp_server.services.app_runtime import parse_plugin_status, create_runtime
from mcp_server.services.deployment import digest
from mcp_server.services.jobs import JobError, JobManager, default_commands
from mcp_server.services.plugin_runtime import ManagedPluginService, PluginJobs
from mcp_server.services.plugins import _jar_manifest, _write_json
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.services.runtime_jobs import RuntimeJobRouter, RuntimeJobs
from mcp_server.services.runtime_lifecycle import LaunchSpec, RuntimeLifecycle
from mcp_server.paper_runner import installation_ready
from mcp_server.tools.plugins import register_plugin_tools
from mcp_server.tools.runtime import register_runtime_tools


def jar(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.yml", "name: Probe\nversion: '" + version + "'\nmain: local.Probe\n")


async def wait_job(manager, identifier):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = await manager.get(identifier)
        if job.status in {"succeeded", "failed", "unknown", "cancelled"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Job did not finish")


class ABCDTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "paper.jar").touch()
        self.target = RuntimeTarget("paper", "project", self.root, self.root, self.root / "paper.jar", managed=True)
        registry = RuntimeRegistry([self.target])
        script = "import sys\nprint('READY', flush=True)\nfor line in sys.stdin:\n if line.strip() == 'stop': break\n"
        launch = LaunchSpec((sys.executable, "-u", "-c", script, "-jar", str(self.target.server_jar)), r"^READY$")
        self.releases = []
        async def release(rid):
            self.releases.append(rid)
        self.lifecycle = RuntimeLifecycle(registry, {"paper": launch}, release_runtime=release)
        self.runtime = RuntimeService(registry, lifecycle=self.lifecycle)
        async def no_rcon(command, timeout):
            raise AssertionError("Lifecycle/plugin jobs must not become RCON strings")
        router = RuntimeJobRouter(self.runtime, no_rcon)
        self.manager = JobManager(router.execute, {**default_commands(), **router.commands()})
        self.runtime_jobs = RuntimeJobs(self.runtime, self.manager)
        async def observe(rid, name, timeout):
            state = await self.runtime.status(rid)
            manifest = _jar_manifest(self.root / "plugins/Probe.jar")
            return {"plugin_name": name, "version": manifest["version"],
                    "enabled": manifest["version"] != "broken", "boot_id": state["result"]["boot_id"]}
        self.plugins = ManagedPluginService(self.root, runtime=self.runtime, observe_plugin=observe)
        jar(self.root / "plugins/Probe.jar", "1.0")

    async def asyncTearDown(self):
        await self.manager.close()
        cleanup = await self.runtime.close(timeout_seconds=5)
        self.assertTrue(cleanup["ok"], cleanup)
        self.temp.cleanup()

    def build_record(self, version):
        identifier = uuid.uuid4().hex
        directory = self.plugins._directory("builds", identifier)
        artifact = directory / "artifact.jar"
        jar(artifact, version)
        _write_json(directory / "record.json", {"ok": True, "build_id": identifier,
            "jar_path": str(artifact), "artifact_sha256": digest(artifact)})
        return identifier

    async def test_real_lifecycle_plugin_queue_deploy_failure_and_rollback(self):
        app = FastMCP("ABCD", strict_input_validation=True)
        register_runtime_tools(app, self.runtime, self.runtime_jobs)
        register_plugin_tools(app, self.plugins, PluginJobs(self.manager, self.runtime))
        async with Client(app) as client:
            started = (await client.call_tool("start_server", {"runtime_id": "paper", "request_id": "start"})).data
            start = await wait_job(self.manager, started["result"]["job_id"])
            self.assertEqual(start.status, "succeeded", start.errors)
            original_boot = json.loads(start.raw_output)["boot_id"]
            for version, expected in (("2.0", "succeeded"), ("broken", "failed")):
                build = self.build_record(version)
                request = {"runtime_id": "paper", "build_id": build, "request_id": version, "run_id": "abcd"}
                submitted = (await client.call_tool("deploy_plugin", request)).data
                repeated = (await client.call_tool("deploy_plugin", request)).data
                self.assertEqual(submitted["job_id"], repeated["job_id"])
                final = await wait_job(self.manager, submitted["job_id"])
                self.assertEqual(final.status, expected, final.as_dict())
                self.assertEqual(final.parsed_output["previous_boot_id"], original_boot)
                self.assertNotEqual(final.parsed_output["boot_id"], original_boot)
                original_boot = final.parsed_output["boot_id"]
            # Disabled in an observed new boot is a definite failure, not unknown.
            self.assertFalse(final.parsed_output["execution_unknown"])
            rollback = (await client.call_tool("rollback_plugin", {"runtime_id": "paper",
                "deployment_id": final.parsed_output["deployment_id"], "request_id": "rollback"})).data
            final = await wait_job(self.manager, rollback["job_id"])
            self.assertEqual(final.status, "succeeded", final.as_dict())
            self.assertEqual(_jar_manifest(self.root / "plugins/Probe.jar")["version"], "2.0")
            self.assertEqual(len(self.manager._jobs), 4)  # start + two deploys + rollback; no nested jobs
            self.assertEqual(self.releases, ["paper"] * 3)

    async def test_shared_app_has_all_tools_and_generic_command_cannot_call_lifecycle(self):
        names = set(await server.mcp.get_tools())
        self.assertEqual(len(names), 26)
        self.assertTrue({"start_server", "stop_server", "rollback_plugin", "get_plugin_status",
                         "list_game_clients", "capture_game_view", "get_game_capture",
                         "record_visual_assertion", "get_visual_run_evidence"} <= names)
        rejected = json.loads(await server.execute_command.fn(command_id="stop_server", runtime_id="paper"))
        self.assertFalse(rejected["ok"])
        self.assertIn("Unsupported command_id", rejected["errors"][0])

    async def test_operations_share_queue_deadlines_and_do_not_retry(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def hold(remaining):
            entered.set()
            await release.wait()
            return {"ok": True}
        first = await self.manager.submit_operation(kind="deploy_plugin", inputs={}, runtime_id="paper",
                                                   request_id="hold", operation=hold, timeout=5)
        await entered.wait()
        async def never(remaining):
            calls.append(remaining)
            return {"ok": True}
        expired = await self.manager.submit_operation(kind="deploy_plugin", inputs={"build": "second"},
            runtime_id="paper", request_id="expired", operation=never, timeout=0.1)
        self.assertEqual((await wait_job(self.manager, expired.job_id)).status, "failed")
        self.assertEqual(expired.sent, "not_sent")
        duplicate = await self.manager.submit_operation(kind="deploy_plugin", inputs={"build": "second"},
            runtime_id="paper", request_id="expired", operation=never, timeout=0.1)
        self.assertEqual(duplicate.job_id, expired.job_id)
        self.assertEqual(calls, [])
        with self.assertRaises(JobError):
            await self.manager.submit_operation(kind="rollback_plugin", inputs={}, runtime_id="paper",
                request_id="expired", operation=never, timeout=0.1)
        release.set()
        self.assertEqual((await wait_job(self.manager, first.job_id)).status, "succeeded")

    async def test_managed_lifespan_keeps_release_callback_alive_and_reaps_child(self):
        await self.runtime.start("paper", timeout_seconds=5)
        order = []
        runtime, manager = self.runtime, self.manager
        class Jobs:
            async def close(inner):
                order.append("jobs")
                await manager.close()
        class Runtime:
            lifecycle = self.lifecycle
            async def close(inner):
                order.append("runtime")
                return await runtime.close(timeout_seconds=5)
        class Debugger:
            async def close(inner):
                order.append("debugger")
        with patch.object(server, "runtime_service", Runtime()), patch.object(server, "job_manager", Jobs()), \
                patch.object(server, "debugger_service", Debugger()):
            async with server._lifespan(None):
                pass
        self.assertEqual(order, ["jobs", "runtime", "debugger"])
        self.assertEqual(self.releases, ["paper"])
        self.assertIsNotNone(self.lifecycle._children["paper"].process.returncode)

    async def test_status_does_not_accept_other_boot_or_unbound_diagnostics(self):
        await self.runtime.start("paper", timeout_seconds=5)
        async def stale(*args):
            return {"plugin_name": "Probe", "version": "1.0", "enabled": True, "boot_id": "old"}
        self.plugins.observe_plugin = stale
        status = await self.plugins.get_plugin_status("paper", "Probe")
        self.assertIsNone(status["enabled"])
        self.assertIsNotNone(status["installed"])

    async def test_close_before_operation_task_starts_is_cancelled_without_execution(self):
        operation = AsyncMock(return_value={"ok": True})
        job = await self.manager.submit_operation(kind="deploy_plugin", inputs={}, runtime_id="paper",
                                                  operation=operation)
        await self.manager.close()
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.sent, "not_sent")
        operation.assert_not_awaited()

    async def test_generic_command_routes_only_to_the_selected_registered_endpoint(self):
        other = self.root / "other"
        other.mkdir()
        first = replace(self.target, rcon=RconEndpoint("127.0.0.1", 21001, "ONE_PASSWORD"))
        second = RuntimeTarget("other", "project", other, other, other / "paper.jar",
                               rcon=RconEndpoint("127.0.0.1", 21002, "TWO_PASSWORD"))
        runtime = RuntimeService(RuntimeRegistry([first, second]))
        execute = AsyncMock(return_value="There are 0 of a max of 20 players online")
        with patch.object(server, "runtime_service", runtime), patch.object(server, "job_manager", self.manager), \
                patch.object(server, "execute_endpoint", execute):
            for rid, endpoint in (("paper", first.rcon), ("other", second.rcon)):
                result = json.loads(await server.execute_command.fn(command_id="list_players", runtime_id=rid))
                job = await wait_job(self.manager, result["job_id"])
                self.assertEqual(job.status, "succeeded")
                self.assertEqual(execute.await_args.args[0], endpoint)
                self.assertEqual(job.parsed_output["online"], 0)

    async def test_release_runtime_does_not_detach_other_runtime_and_pending_attach_blocks_stop(self):
        config = replace(server.debugger_service.config, evidence_dir=self.root / "evidence")
        service = DebuggerService(config, simulated=True)
        backend = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
            request=AsyncMock(side_effect=lambda method, params: {"connection_state": "connected"}
                              if method == "get_debug_state" else {"detached": True, "released_stop_ids": []}))
        service._client = backend
        service._sessions = {"first": ("paper", "project"), "second": ("other", "project")}
        await service.release_runtime("paper")
        self.assertEqual([c.args[1]["session_id"] for c in backend.request.await_args_list], ["first", "first"])
        service._attach_requests["pending"] = {"runtime_id": "paper"}
        with self.assertRaises(DebuggerError) as error:
            await service.release_runtime("paper")
        self.assertEqual(error.exception.code, "backend_busy")
        await service.close()

    @unittest.skipUnless(os.name == "nt", "Windows real sharing-lock regression")
    async def test_managed_deploy_keeps_ownership_while_a_reader_holds_jar_for_three_seconds(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        await self.runtime.start("paper", timeout_seconds=5)
        path = self.root / "plugins/Probe.jar"
        handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        released = threading.Event()
        def unlock():
            released.wait(3)
            api.CloseHandle(handle)
        reader = threading.Thread(target=unlock)
        reader.start()
        try:
            deployed = await self.plugins.deploy("paper", self.build_record("2.0"), timeout=8)
        finally:
            released.set()
            reader.join()
        self.assertTrue(deployed["ok"], deployed)
        self.assertGreater(deployed["file_replace"]["attempts"], 1)
        self.assertGreater(deployed["file_replace"]["wait_seconds"], 2)
        self.assertEqual(self.releases, ["paper"])

    async def test_failed_atomic_replace_preserves_old_jar_and_allows_explicit_restart(self):
        await self.runtime.start("paper", timeout_seconds=5)
        old = digest(self.root / "plugins/Probe.jar")
        with patch("mcp_server.services.plugin_runtime._replace_file", side_effect=PermissionError("persistent reader")):
            failed = await self.plugins.deploy("paper", self.build_record("2.0"), timeout=5)
        self.assertFalse(failed["ok"])
        self.assertFalse(failed["files_modified"])
        self.assertTrue(failed["installation_preserved"])
        self.assertEqual(digest(self.root / "plugins/Probe.jar"), old)
        self.assertTrue(installation_ready(self.root))
        self.assertIsNotNone(self.lifecycle._children["paper"].process.returncode)
        started = await self.runtime.start("paper", timeout_seconds=5)
        self.assertIsNotNone(started["pid"])
        self.assertEqual(_jar_manifest(self.root / "plugins/Probe.jar")["version"], "1.0")


class DiagnosticTests(unittest.TestCase):
    def test_runtime_configuration_is_read_only_by_default_and_checks_legacy_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = SimpleNamespace(projects={"p": Project(root)},
                runtimes={"paper": Runtime("p", "127.0.0.1", 5005, root / "paper.jar")}, java_executable="java")
            debugger = SimpleNamespace(config=config, release_runtime=AsyncMock(), runtime_state=AsyncMock(return_value=None))
            with patch.dict("os.environ", {}, clear=True):
                runtime = create_runtime(debugger, workspace=root)
                self.assertFalse(runtime.can_manage("paper"))
                self.assertIsNone(runtime.resolve("paper").rcon)
            with patch.dict("os.environ", {"MC_RUNTIME_ID": "paper", "MC_SERVER_DIR": str(root / "wrong")}, clear=True):
                with self.assertRaisesRegex(ValueError, "same server"):
                    create_runtime(debugger, workspace=root)

    def test_exact_name_version_encoding_and_rejection(self):
        def encode(value):
            return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
        raw = f"CRAFTMCP_PLUGIN_STATUS v=1 name={encode('Probe')} version={encode('한글 1.0')} enabled=true\n\n"
        self.assertEqual(parse_plugin_status(raw, "Probe")["version"], "한글 1.0")
        self.assertIsNone(parse_plugin_status(raw, "Other"))
        self.assertIsNone(parse_plugin_status(raw + raw, "Probe"))
        self.assertFalse(parse_plugin_status("CRAFTMCP_PLUGIN_STATUS v=1 missing=" + encode("Probe"), "Probe")["enabled"])

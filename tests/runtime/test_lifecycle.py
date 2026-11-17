"""Owned subprocess lifecycle and failure paths; no Minecraft server is touched."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from mcp_server.models.contracts import ContractError, Endpoint, RuntimeTarget
from mcp_server.paper_runner import read_state
from mcp_server.services.file_gate import FileGate
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.services.runtime_lifecycle import LaunchSpec, Operation, RuntimeLifecycle, paper_launch

FIXTURE = """import sys, time
from pathlib import Path
if '--exit-immediately' in sys.argv:
    raise SystemExit(7)
if '--never-ready' not in sys.argv:
    print('READY', flush=True)
for line in sys.stdin:
    if line.strip() == 'stop':
        print('STOP_RECEIVED', flush=True)
        if '--ignore-stop' not in sys.argv:
            break
"""


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.py"
        self.fixture.write_text(FIXTURE, encoding="utf-8")
        (self.root / "paper.jar").write_bytes(b"not a Java artifact: subprocess test only")
        self.target = RuntimeTarget("paper", "project", self.root, self.root, self.root / "paper.jar", managed=True)
        self.controllers = []
        self.make_service()

    def make_service(self, *, target=None, flags=(), release_runtime=None):
        target = target or self.target
        registry = RuntimeRegistry([target])
        if target.jdwp:
            flags = (*flags, f"-agentlib:jdwp=transport=dt_socket,address={target.jdwp.host}:{target.jdwp.port}")
        launch = LaunchSpec((sys.executable, "-u", str(self.fixture), "-jar", str(target.server_jar), *flags),
                            ready_pattern=r"^READY$")
        lifecycle = RuntimeLifecycle(registry, {target.runtime_id: launch}, release_runtime=release_runtime)
        self.controllers.append(lifecycle)
        self.lifecycle = lifecycle
        self.service = RuntimeService(registry, lifecycle=lifecycle)
        return self.service

    async def asyncTearDown(self):
        async def release(runtime_id):
            return None

        for controller in self.controllers:
            controller.release_runtime = release
            await controller.close(timeout_seconds=0.5)
            # Only uncooperative test fixtures are forcibly reaped by test cleanup.
            for child in controller._children.values():
                if child.process.returncode is None:
                    child.process.terminate()
                    await asyncio.wait_for(child.process.wait(), 5)
                if child.watcher:
                    await asyncio.wait_for(child.watcher, 5)
        self.temp.cleanup()

    async def assert_code(self, code, awaitable):
        with self.assertRaises(ContractError) as error:
            await awaitable
        self.assertEqual(error.exception.code, code)

    async def test_start_duplicate_stop_and_restart_have_real_boot_identity(self):
        first = await self.service.start("paper", timeout_seconds=5)
        self.assertTrue(first["game_ready"])
        self.assertIs(first["already_running"], False)
        again = await self.service.start("paper", timeout_seconds=5)
        self.assertEqual(again["pid"], first["pid"])
        self.assertEqual(again["boot_id"], first["boot_id"])
        self.assertTrue(again["already_running"])
        observed = read_state(self.root)
        self.assertEqual(observed["boot_id"], first["boot_id"])
        stopped = await self.service.stop("paper", timeout_seconds=5)
        self.assertTrue(stopped["exit_confirmed"])
        self.assertEqual(stopped["exit_code"], 0)
        self.assertIsNone(stopped["pid"])
        await asyncio.sleep(0.15)
        self.assertEqual((await self.service.status("paper"))["result"]["observations"]["process"]["value"], "stopped")
        self.assertTrue((await self.service.stop("paper"))["already_stopped"])
        second = await self.service.start("paper", timeout_seconds=5)
        self.assertNotEqual(second["boot_id"], first["boot_id"])

    async def test_unmanaged_and_foreign_processes_cannot_be_stopped(self):
        self.make_service(target=replace(self.target, managed=False))
        await self.assert_code("runtime_unmanaged", self.service.start("paper"))
        await self.assert_code("runtime_unmanaged", self.service.stop("paper"))
        self.assertFalse((self.root / ".craftmcp").exists())
        self.make_service()
        await self.assert_code("runtime_not_owned", self.service.stop("paper"))
        with FileGate(self.root / ".craftmcp/runner.lock"):
            await self.assert_code("runtime_not_owned", self.service.start("paper"))

    async def test_second_controller_cannot_adopt_an_owned_process(self):
        original = self.service
        first = await original.start("paper", timeout_seconds=5)
        other = self.make_service()
        await self.assert_code("runtime_not_owned", other.start("paper"))
        await self.assert_code("runtime_not_owned", other.stop("paper"))
        self.assertEqual(self.controllers[0]._children["paper"].process.pid, first["pid"])
        self.assertIsNone(self.controllers[0]._children["paper"].process.returncode)

    async def test_stale_running_state_is_not_adopted_or_overwritten(self):
        control = self.root / ".craftmcp"
        control.mkdir()
        previous = {"phase": "running", "pid": 123, "boot_id": "unconfirmed"}
        state = control / "state.json"
        state.write_text(json.dumps(previous), encoding="utf-8")
        await self.assert_code("ownership_unknown", self.service.start("paper"))
        self.assertEqual(json.loads(state.read_text()), previous)

    async def test_start_timeout_preserves_owned_handle_until_explicit_stop(self):
        self.make_service(flags=("--never-ready",))
        await self.assert_code("start_timeout", self.service.start("paper", timeout_seconds=0.1))
        state = (await self.service.status("paper"))["result"]
        self.assertIsNotNone(state["boot_id"])
        self.assertEqual(state["observations"]["game_ready"]["status"], "unknown")
        self.assertTrue((await self.service.stop("paper", timeout_seconds=5))["exit_confirmed"])

    async def test_early_exit_and_missing_executable_release_gates(self):
        self.make_service(flags=("--exit-immediately",))
        await self.assert_code("start_failed", self.service.start("paper", timeout_seconds=5))
        await self.lifecycle._children["paper"].exited.wait()
        self.make_service()
        self.lifecycle.launches["paper"] = LaunchSpec((str(self.root / "missing-executable"), "-jar", str(self.target.server_jar)), None)
        await self.assert_code("start_failed", self.service.start("paper", timeout_seconds=5))
        with FileGate(self.root / ".craftmcp/runner.lock"):
            pass
        self.make_service()
        self.assertTrue((await self.service.start("paper", timeout_seconds=5))["game_ready"])

    async def test_stop_timeout_never_resends_or_claims_exit(self):
        self.make_service(flags=("--ignore-stop",))
        await self.service.start("paper", timeout_seconds=5)
        await self.assert_code("stop_timeout", self.service.stop("paper", timeout_seconds=0.2))
        await self.assert_code("stop_timeout", self.service.stop("paper", timeout_seconds=0.1))
        child = self.lifecycle._children["paper"]
        self.assertIsNone(child.process.returncode)
        self.assertEqual(child.log_path.read_text().count("STOP_RECEIVED"), 1)
        self.assertFalse((await self.service.close(timeout_seconds=0.1))["ok"])

    async def test_deployment_token_avoids_nested_lock_and_rejects_reuse(self):
        await self.service.start("paper", timeout_seconds=5)
        forged = Operation("paper", self.lifecycle, asyncio.current_task())
        await self.assert_code("invalid_operation", self.service.stop("paper", operation=forged))
        async with self.service.exclusive("paper") as operation:
            await self.assert_code("operation_busy", self.service.stop("paper"))
            stopped = await self.service.stop("paper", timeout_seconds=5, operation=operation)
            self.assertTrue(stopped["exit_confirmed"])
            (self.root / "paper.jar").write_bytes(b"replacement after confirmed exit")
            self.assertTrue((await self.service.start("paper", timeout_seconds=5, operation=operation))["game_ready"])
            await self.assert_code("invalid_operation", asyncio.create_task(
                self.service.stop("paper", operation=operation)))
        await self.assert_code("invalid_operation", self.service.stop("paper", operation=operation))

    async def test_status_stays_available_during_exclusive_operation(self):
        await self.service.start("paper", timeout_seconds=5)
        async with self.service.exclusive("paper"):
            status = await asyncio.wait_for(self.service.status("paper"), 1)
            self.assertEqual(status["result"]["observations"]["process"]["source"], "owned_process")

    async def test_debugger_release_precedes_stop_and_failure_sends_nothing(self):
        target = replace(self.target, jdwp=Endpoint("127.0.0.1", 5005))
        events = []

        async def release(runtime_id):
            events.append(runtime_id)
            self.assertFalse(self.lifecycle._children[runtime_id].stop_sent)

        self.make_service(target=target, release_runtime=release)
        await self.service.start("paper", timeout_seconds=5)
        self.lifecycle.release_runtime = None
        await self.assert_code("debugger_release_unavailable", self.service.stop("paper"))

        async def broken(runtime_id):
            raise RuntimeError("secret must not reach the public result")

        self.lifecycle.release_runtime = broken
        await self.assert_code("debugger_release_failed", self.service.stop("paper"))
        self.assertFalse(self.lifecycle._children["paper"].stop_sent)
        self.lifecycle.release_runtime = release
        self.assertTrue((await self.service.stop("paper", timeout_seconds=5))["exit_confirmed"])
        self.assertEqual(events, ["paper"])

    async def test_jdwp_start_requires_a_release_hook_before_spawning(self):
        self.make_service(target=replace(self.target, jdwp=Endpoint("127.0.0.1", 5005)))
        await self.assert_code("debugger_release_unavailable", self.service.start("paper"))
        self.assertNotIn("paper", self.lifecycle._children)

    async def test_cancelled_start_remains_owned_and_close_reaps_it(self):
        self.make_service(flags=("--never-ready",))
        task = asyncio.create_task(self.service.start("paper", timeout_seconds=5))
        until = time.monotonic() + 5
        while "paper" not in self.lifecycle._children:
            self.assertLess(time.monotonic(), until)
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue((await self.service.close(timeout_seconds=5))["ok"])
        self.assertIsNotNone(self.lifecycle._children["paper"].process.returncode)
        await self.assert_code("service_closed", self.service.start("paper"))

    async def test_partial_install_blocks_start(self):
        control = self.root / ".craftmcp"
        control.mkdir()
        (control / "install.json").write_text('{"phase":"installing"}')
        await self.assert_code("installation_incomplete", self.service.start("paper"))
        self.assertNotIn("paper", self.lifecycle._children)

    async def test_paper_launch_uses_same_jar_and_loopback_jdwp(self):
        target = replace(self.target, jdwp=Endpoint("127.0.0.1", 5005))
        command = paper_launch(target).command
        self.assertEqual(command[command.index("-jar") + 1], str(self.target.server_jar))
        self.assertTrue(any("address=127.0.0.1:5005" in arg for arg in command))
        with self.assertRaises(ValueError):
            paper_launch(replace(target, jdwp=Endpoint("remote.example", 5005)))


if __name__ == "__main__":
    unittest.main()

"""A lifecycle operations through the unmodified B JobManager and real MCP."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastmcp import Client, FastMCP

from mcp_server.models.contracts import ContractError, RuntimeTarget
from mcp_server.services.jobs import JobManager, default_commands
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.services.runtime_jobs import RuntimeJobRouter, RuntimeJobs
from mcp_server.services.runtime_lifecycle import LaunchSpec, RuntimeLifecycle
from mcp_server.tools.runtime import register_runtime_tools


class RuntimeJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "paper.jar").touch()
        target = RuntimeTarget("paper", "project", self.root, self.root, self.root / "paper.jar", managed=True)
        registry = RuntimeRegistry([target])
        fixture = "import sys\nprint('READY', flush=True)\nfor line in sys.stdin:\n if line.strip() == 'stop': break\n"
        launch = LaunchSpec((sys.executable, "-u", "-c", fixture, "-jar", str(target.server_jar)), r"^READY$")
        self.lifecycle = RuntimeLifecycle(registry, {"paper": launch})
        self.runtime = RuntimeService(registry, lifecycle=self.lifecycle)
        self.commands = []

        async def execute(command, timeout):
            self.commands.append(command)
            return "There are 0 of a max of 20 players online"

        router = RuntimeJobRouter(self.runtime, execute)
        self.manager = JobManager(router.execute, {**default_commands(), **router.commands()})
        self.jobs = RuntimeJobs(self.runtime, self.manager)

    async def asyncTearDown(self):
        await self.manager.close()
        result = await self.runtime.close(timeout_seconds=5)
        self.assertTrue(result["ok"], result)
        self.temp.cleanup()

    async def wait_job(self, job_id):
        deadline = time.monotonic() + 10
        while True:
            job = await self.manager.get(job_id)
            if job.status in {"succeeded", "failed", "unknown", "cancelled"}:
                return job
            self.assertLess(time.monotonic(), deadline)
            await asyncio.sleep(0.02)

    async def test_shared_manager_deduplicates_and_routes_without_rcon(self):
        first = await self.jobs.submit("start_server", "paper", request_id="start-1", run_id="run-1", timeout_seconds=5)
        duplicate = await self.jobs.submit("start_server", "paper", request_id="start-1", run_id="run-1", timeout_seconds=5)
        self.assertEqual(first["job_id"], duplicate["job_id"])
        start = await self.wait_job(first["job_id"])
        self.assertEqual(start.status, "succeeded", start.errors)
        self.assertEqual(start.run_id, "run-1")
        pid = json.loads(start.raw_output)["pid"]
        self.assertEqual((await self.manager.get(start.job_id)).raw_output, start.raw_output)
        self.assertEqual(self.lifecycle._children["paper"].process.pid, pid)
        # Ordinary B commands and A lifecycle jobs use this same manager/lookup.
        command = await self.manager.submit(command_id="list_players", runtime_id="paper")
        self.assertEqual((await self.wait_job(command.job_id)).status, "succeeded")
        stop = await self.jobs.submit("stop_server", "paper", request_id="stop-1", timeout_seconds=5)
        completed = await self.wait_job(stop["job_id"])
        self.assertEqual(completed.status, "succeeded", completed.errors)
        self.assertTrue(json.loads(completed.raw_output)["exit_confirmed"])
        self.assertEqual(self.commands, ["list"])

    async def test_request_conflict_is_rejected_before_another_operation(self):
        await self.jobs.submit("start_server", "paper", request_id="same", timeout_seconds=5)
        with self.assertRaises(ContractError) as error:
            await self.jobs.submit("stop_server", "paper", request_id="same", timeout_seconds=5)
        self.assertEqual(error.exception.code, "request_conflict")

    async def test_dependent_jobs_complete_without_submitting_nested_jobs(self):
        start = await self.jobs.submit("start_server", "paper", request_id="one", timeout_seconds=5)
        stop = await self.jobs.submit("stop_server", "paper", request_id="two", timeout_seconds=5)
        self.assertEqual((await self.wait_job(start["job_id"])).status, "succeeded")
        stopped = await self.wait_job(stop["job_id"])
        self.assertEqual(stopped.status, "succeeded", stopped.errors)
        self.assertEqual(len(self.manager._jobs), 2)

    async def test_cancel_queued_operation_never_sends_stop(self):
        launch = self.lifecycle.launches["paper"]
        self.lifecycle.launches["paper"] = LaunchSpec(launch.command, r"NEVER_EMITTED")
        first = await self.jobs.submit("start_server", "paper", request_id="one", timeout_seconds=0.2)
        deadline = time.monotonic() + 5
        while "paper" not in self.lifecycle._children:
            self.assertLess(time.monotonic(), deadline)
            await asyncio.sleep(0.01)
        queued = await self.jobs.submit("stop_server", "paper", request_id="two", timeout_seconds=5)
        cancelled = await self.manager.cancel(queued["job_id"])
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual((await self.wait_job(first["job_id"])).status, "unknown")
        self.assertFalse(self.lifecycle._children["paper"].stop_sent)

    async def test_manager_close_then_runtime_close_reaps_cancelled_start(self):
        launch = self.lifecycle.launches["paper"]
        self.lifecycle.launches["paper"] = LaunchSpec(launch.command, r"NEVER_EMITTED")
        await self.jobs.submit("start_server", "paper", request_id="one", timeout_seconds=5)
        deadline = time.monotonic() + 5
        while "paper" not in self.lifecycle._children:
            self.assertLess(time.monotonic(), deadline)
            await asyncio.sleep(0.01)
        await self.manager.close()
        self.assertTrue((await self.runtime.close(timeout_seconds=5))["ok"])
        self.assertIsNotNone(self.lifecycle._children["paper"].process.returncode)

    async def test_real_mcp_start_and_stop_are_queryable_in_shared_job_registry(self):
        app = FastMCP("lifecycle integration", strict_input_validation=True)
        register_runtime_tools(app, self.runtime, self.jobs)

        @app.tool()
        async def get_job(job_id: str) -> dict:
            return (await self.manager.get(job_id)).as_dict()

        async with Client(app) as client:
            self.assertEqual({tool.name for tool in await client.list_tools()},
                             {"list_runtimes", "get_runtime_status", "start_server", "stop_server", "get_job"})
            submitted = (await client.call_tool("start_server", {"runtime_id": "paper", "request_id": "mcp-start",
                                                                "timeout_seconds": 5})).data
            self.assertTrue(submitted["ok"], submitted)
            start = await self.wait_job(submitted["result"]["job_id"])
            self.assertEqual(start.status, "succeeded", start.errors)
            queried = (await client.call_tool("get_job", {"job_id": start.job_id})).data
            self.assertEqual(queried["job_id"], start.job_id)
            response = (await client.call_tool("stop_server", {"runtime_id": "paper", "request_id": "mcp-stop",
                                                               "timeout_seconds": 5})).data
            stop = await self.wait_job(response["result"]["job_id"])
            self.assertEqual(stop.status, "succeeded", stop.errors)
            self.assertTrue(json.loads(stop.raw_output)["exit_confirmed"])

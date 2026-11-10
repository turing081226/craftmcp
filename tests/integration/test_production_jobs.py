"""Regressions found while connecting B's real jobs to D's fixture."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_server_qa import RconPeer
from mcp_server import server
from mcp_server.services.debugger_fixture import COMMAND_ID
from mcp_server.services.jobs import JobError, JobManager, default_commands


async def wait_terminal(job):
    async def wait():
        while job.status in {"queued", "running"}:
            await asyncio.sleep(0.01)
        return job
    return await asyncio.wait_for(wait(), 4)


class ProductionJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_builds_from_envelope_run_and_parses_success_error_or_unknown(self):
        for terminal, expected in (("status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n", "succeeded"),
                ("status=error run_id=run-1 code=INTEGER_OVERFLOW end=1\n", "failed"),
                ("status=ok run_id=other base=7 bonus=3 damage=10 end=1\n", "unknown"),
                ("", "unknown")):
            calls = []

            async def execute(command, timeout):
                calls.append(command)
                return "CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n" + (
                    "CRAFTDEBUG_RCON v=1 " + terminal if terminal else "")

            manager = JobManager(execute, default_commands())
            try:
                job = await manager.submit(command_id=COMMAND_ID, arguments={"base": 7, "bonus": 3},
                    run_id="run-1", request_id="request-1", timeout=2)
                await wait_terminal(job)
                self.assertEqual(job.status, expected)
                self.assertEqual(job.sent, "confirmed")
                self.assertEqual(job.parsed_output["status"], expected)
                self.assertEqual(calls, ["craftdebugrcon calculate run-1 7 3"])
                self.assertIn("deadline_at", job.as_dict())
                duplicate = await manager.submit(command_id=COMMAND_ID, arguments={"base": 7, "bonus": 3},
                    run_id="run-1", request_id="request-1", timeout=2)
                self.assertEqual(duplicate.job_id, job.job_id)
                with self.assertRaisesRegex(JobError, "different input"):
                    await manager.submit(command_id=COMMAND_ID, arguments={"base": 7, "bonus": 3},
                        run_id="run-2", request_id="request-1", timeout=2)
                self.assertEqual(len(calls), 1)
            finally:
                await manager.close()

    async def test_invalid_fixture_or_timeout_is_rejected_before_transmission(self):
        calls = []

        async def execute(*args):
            calls.append(args)
            return ""

        manager = JobManager(execute, default_commands())
        try:
            for kwargs in ({"run_id": None}, {"run_id": "bad\nstop"},
                           {"arguments": {"base": True, "bonus": 3}},
                           {"timeout": True}, {"timeout": float("nan")}):
                request = {"command_id": COMMAND_ID, "arguments": {"base": 7, "bonus": 3}, "run_id": "run-1", **kwargs}
                with self.subTest(kwargs=kwargs), self.assertRaises(JobError):
                    await manager.submit(**request)
            self.assertEqual(calls, [])
        finally:
            await manager.close()

    async def test_queued_deadline_expires_without_sending(self):
        calls = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def execute(command, timeout):
            calls.append(command)
            started.set()
            await release.wait()
            return "done"

        manager = JobManager(execute, default_commands())
        try:
            await manager.submit(command_id="say", arguments={"message": "first"}, timeout=3)
            await asyncio.wait_for(started.wait(), 1)
            queued = await manager.submit(command_id="say", arguments={"message": "second"}, timeout=0.15)
            await wait_terminal(queued)
            self.assertEqual(queued.status, "failed")
            self.assertEqual(queued.sent, "not_sent")
            self.assertIsNone(queued.started_at)
            self.assertEqual(calls, ["say first"])
        finally:
            release.set()
            await manager.close()

    async def test_queue_time_is_subtracted_from_transport_deadline(self):
        started, release = asyncio.Event(), asyncio.Event()
        budgets = []

        async def execute(command, timeout):
            if command == "say first":
                started.set()
                await release.wait()
            else:
                budgets.append(timeout)
            return "done"

        manager = JobManager(execute, default_commands())
        try:
            await manager.submit(command_id="say", arguments={"message": "first"}, timeout=3)
            await asyncio.wait_for(started.wait(), 1)
            queued = await manager.submit(command_id="say", arguments={"message": "second"}, timeout=2)
            asyncio.get_running_loop().call_later(0.1, release.set)
            await wait_terminal(queued)
            self.assertEqual(queued.status, "succeeded")
            self.assertTrue(0 < budgets[0] < 1.95, budgets)
        finally:
            release.set()
            await manager.close()

    async def test_running_operation_that_ignores_timeout_is_bounded(self):
        cancelled = asyncio.Event()

        async def execute(command, timeout):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        manager = JobManager(execute, default_commands())
        try:
            job = await manager.submit(command_id="list_players", timeout=0.15)
            await wait_terminal(job)
            self.assertEqual(job.status, "unknown")
            self.assertTrue(cancelled.is_set())
        finally:
            await manager.close()

    async def test_eof_or_timeout_remains_unknown_and_duplicate_requests_do_not_resend(self):
        for mode in ("command_eof", "command_silence"):
            with self.subTest(mode=mode):
                async with RconPeer(mode) as peer:
                    with patch.dict(os.environ, {"MC_RCON_PASSWORD": "fake", "MC_RCON_PORT": str(peer.port)}):
                        manager = JobManager(server._execute_job_command, default_commands())
                        try:
                            job = await manager.submit(command_id="say", arguments={"message": "once"},
                                request_id="same", timeout=2)
                            await wait_terminal(job)
                            self.assertEqual(job.status, "unknown")
                            self.assertEqual(job.sent, "unknown")
                            for _ in range(3):
                                duplicate = await manager.submit(command_id="say", arguments={"message": "once"},
                                    request_id="same", timeout=2)
                                self.assertEqual((await manager.get(duplicate.job_id)).job_id, job.job_id)
                            await asyncio.wait_for(peer.closed.wait(), 1)
                            self.assertEqual(peer.commands, ["say once"])
                        finally:
                            await manager.close()

    async def test_close_cancels_socket_worker_instead_of_waiting_for_long_rcon_deadline(self):
        async with RconPeer("command_silence") as peer:
            with patch.dict(os.environ, {"MC_RCON_PASSWORD": "fake", "MC_RCON_PORT": str(peer.port)}):
                manager = JobManager(server._execute_job_command, default_commands())
                job = await manager.submit(command_id="say", arguments={"message": "once"}, timeout=30)
                async def received():
                    while not peer.commands:
                        await asyncio.sleep(0.01)
                try:
                    await asyncio.wait_for(received(), 2)
                finally:
                    await asyncio.wait_for(manager.close(), 2)
                await asyncio.wait_for(peer.closed.wait(), 1)
                self.assertEqual(job.status, "unknown")
                self.assertEqual(peer.commands, ["say once"])

    async def test_unknown_runtime_is_rejected_at_shared_tool_boundary(self):
        with patch.dict(os.environ, {"MC_RUNTIME_ID": "selected"}):
            result = json.loads(await server.execute_command.fn(command_id="list_players", runtime_id="wrong"))
        self.assertFalse(result["ok"])
        self.assertIn("Unknown runtime_id", result["errors"][0])

    async def test_lifespan_closes_debugger_before_jobs_even_if_debugger_cleanup_fails(self):
        order = []

        class Debugger:
            async def close(self):
                order.append("debugger")
                raise RuntimeError("simulated cleanup failure")

        class Jobs:
            async def close(self):
                order.append("jobs")

        with patch.object(server, "debugger_service", Debugger()), patch.object(server, "job_manager", Jobs()):
            with self.assertRaisesRegex(RuntimeError, "simulated cleanup"):
                async with server._lifespan(None):
                    pass
        self.assertEqual(order, ["debugger", "jobs"])


if __name__ == "__main__":
    unittest.main()

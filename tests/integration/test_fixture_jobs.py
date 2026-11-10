import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_server_qa import RconPeer
from fixture_app import FixtureJobs
from mcp_server.services.debugger_fixture import COMMAND_ID


class FixtureJobTests(unittest.IsolatedAsyncioTestCase):
    def request(self, **updates):
        return {"command_id": COMMAND_ID, "runtime_id": "fixture", "run_id": "run-1", "request_id": "request-1",
                "arguments": {"base": 7, "bonus": 3}, "timeout_seconds": 2, **updates}

    async def terminal(self, jobs, job_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            result = jobs.get(job_id)
            if result["status"] not in {"queued", "running"}:
                return result
            await asyncio.sleep(0.01)
        self.fail("Fixture job did not finish")

    async def test_loopback_timeout_and_eof_never_resend_on_poll_or_duplicate_request(self):
        for mode, code in (("command_silence", "TIMEOUT"), ("command_eof", "EOF_OR_TRANSPORT_ERROR")):
            with self.subTest(mode=mode):
                async with RconPeer(mode) as peer:
                    jobs = FixtureJobs("fixture", "fake-password", peer.port)
                    original = await jobs.submit(**self.request())
                    final = await self.terminal(jobs, original["job_id"])
                    self.assertEqual(final["status"], "unknown")
                    self.assertEqual(final["send_status"], "unknown")
                    self.assertEqual(final["error"]["code"], code)
                    for _ in range(4):
                        duplicate = await jobs.submit(**self.request())
                        self.assertEqual(jobs.get(original["job_id"]), final)
                        self.assertEqual(duplicate["job_id"], original["job_id"])
                    await jobs.close()
                    await asyncio.wait_for(peer.closed.wait(), 2)
                    self.assertEqual(len(peer.commands), 1)
                    self.assertEqual(peer.connections, 1)
                    with self.assertRaisesRegex(ValueError, "REQUEST_ID_CONFLICT"):
                        await jobs.submit(**self.request(arguments={"base": 8, "bonus": 3}))

    async def test_submit_returns_before_operation_finishes_and_get_is_a_snapshot(self):
        import threading
        started, release = threading.Event(), threading.Event()

        def operation(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError()
            return ("CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n"
                    "CRAFTDEBUG_RCON v=1 status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n")

        jobs = FixtureJobs("fixture", "fake", 12345, operation=operation)
        first = await jobs.submit(**self.request())
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
            self.assertEqual(jobs.get(first["job_id"])["status"], "running")
            duplicate = await jobs.submit(**self.request())
            self.assertEqual(duplicate["job_id"], first["job_id"])
            duplicate["status"] = "tampered"
            self.assertEqual(jobs.get(first["job_id"])["status"], "running")
        finally:
            release.set()
            await jobs.close()
        self.assertEqual(jobs.get(first["job_id"])["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()

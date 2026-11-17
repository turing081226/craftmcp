from __future__ import annotations

import asyncio
import struct
import unittest

from mcp_server.adapters.rcon import execute_rcon
from mcp_server.services.jobs import JobManager, default_commands


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.commands: list[str] = []
        self.release = asyncio.Event()

        async def execute(command: str, timeout: float) -> str:
            self.commands.append(command)
            await self.release.wait()
            return "There are 2 of a max of 20 players online"

        self.execute = execute
        self.manager = JobManager(execute, default_commands())

    async def asyncTearDown(self) -> None:
        await self.manager.close()

    async def test_submit_is_async_and_get_does_not_resend(self) -> None:
        job = await self.manager.submit(
            command_id="list_players",
            request_id="request-1",
            runtime_id="paper",
        )
        self.assertEqual(job.status, "queued")
        await asyncio.sleep(0)
        self.assertEqual(self.commands, ["list"])
        same = await self.manager.submit(
            command_id="list_players",
            request_id="request-1",
            runtime_id="paper",
        )
        self.assertEqual(same.job_id, job.job_id)
        self.assertIs(await self.manager.get(job.job_id), job)
        self.assertEqual(self.commands, ["list"])
        self.release.set()
        for _ in range(10):
            if job.status == "succeeded":
                break
            await asyncio.sleep(0)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.parsed_output, {"online": 2, "max": 20})

    async def test_request_id_with_different_input_is_rejected(self) -> None:
        await self.manager.submit(command_id="list_players", request_id="same")
        with self.assertRaisesRegex(ValueError, "different input"):
            await self.manager.submit(
                command_id="say",
                arguments={"message": "different"},
                request_id="same",
            )

    async def test_cancel_running_job_is_unknown_without_retry(self) -> None:
        job = await self.manager.submit(command_id="list_players")
        await asyncio.sleep(0)
        cancelled = await self.manager.cancel(job.job_id)
        self.assertTrue(cancelled.cancellation_requested)
        self.release.set()
        for _ in range(10):
            if job.status in {"succeeded", "unknown"}:
                break
            await asyncio.sleep(0)
        self.assertEqual(job.status, "unknown")
        self.assertEqual(self.commands, ["list"])

    async def test_mutating_jobs_have_a_runtime_queue_limit(self) -> None:
        manager = JobManager(
            self.execute,
            default_commands(),
            max_mutating_jobs_per_runtime=1,
        )
        try:
            first = await manager.submit(
                command_id="say",
                arguments={"message": "first"},
                runtime_id="paper",
            )
            await asyncio.sleep(0)
            with self.assertRaisesRegex(ValueError, "Too many pending"):
                await manager.submit(
                    command_id="say",
                    arguments={"message": "second"},
                    runtime_id="paper",
                )
            self.assertIn(first.status, {"queued", "running"})
        finally:
            await manager.close()

    async def test_round_trip_uses_real_rcon_adapter_without_resend(self) -> None:
        commands: list[str] = []

        def packet(request_id: int, packet_type: int, text: str = "") -> bytes:
            body = struct.pack("<ii", request_id, packet_type) + text.encode() + b"\0\0"
            return struct.pack("<i", len(body)) + body

        async def receive(reader: asyncio.StreamReader) -> tuple[int, str]:
            length = struct.unpack("<i", await reader.readexactly(4))[0]
            body = await reader.readexactly(length)
            _, packet_type = struct.unpack("<ii", body[:8])
            return packet_type, body[8:-2].decode()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                packet_type, _ = await receive(reader)
                self.assertEqual(packet_type, 3)
                writer.write(packet(0, 2))
                await writer.drain()
                packet_type, command = await receive(reader)
                self.assertEqual(packet_type, 2)
                commands.append(command)
                writer.write(packet(0, 0, "There are 1 of a max of 20 players online"))
                await writer.drain()
                marker_type, marker = await receive(reader)
                self.assertEqual((marker_type, marker), (0, ""))
                writer.write(packet(1, 0, "Unknown request 0"))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]

            async def execute(command: str, timeout: float) -> str:
                return await asyncio.to_thread(
                    execute_rcon, command, "fake", port=port, timeout=timeout
                )

            manager = JobManager(execute, default_commands())
            try:
                job = await manager.submit(command_id="list_players", runtime_id="paper")
                for _ in range(20):
                    if job.status == "succeeded":
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(job.status, "succeeded")
                self.assertEqual(job.parsed_output, {"online": 1, "max": 20})
                self.assertEqual(commands, ["list"])
            finally:
                await manager.close()
        finally:
            server.close()
            await server.wait_closed()

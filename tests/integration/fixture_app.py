"""TEST ONLY: minimal in-memory jobs around real RCON, alongside real debugger tools.

This is D's integration seam, NOT B's queue/journal/cancellation implementation.
It is never imported or registered by mcp_server.server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP

from mcp_server.adapters.rcon import execute_rcon
from mcp_server.services.debugger import DebuggerConfig, DebuggerService
from mcp_server.services.debugger_fixture import COMMAND_ID, command_text, parse_output
from mcp_server.tools.debugger import register_debugger_tools

class FixtureJobs:
    def __init__(self, runtime_id, password, port, *, operation=None):
        self.runtime_id = runtime_id
        self.password = password
        self.port = port
        self.operation = operation or execute_rcon
        self.jobs = {}
        self.requests = {}
        self.tasks = []

    async def submit(self, *, command_id, arguments, runtime_id, run_id, request_id, timeout_seconds):
        if command_id != COMMAND_ID or runtime_id != self.runtime_id:
            raise ValueError("Unknown fixture command/runtime")
        text = command_text(arguments, run_id)
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("request_id must be a nonempty string of at most 128 characters")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise ValueError("Invalid timeout")
        fingerprint = json.dumps([command_id, arguments, runtime_id, run_id, timeout_seconds], sort_keys=True)
        if request_id in self.requests:
            previous, job_id = self.requests[request_id]
            if previous != fingerprint:
                raise ValueError("REQUEST_ID_CONFLICT")
            return self.get(job_id)
        job_id = "fixture-job-" + uuid.uuid4().hex
        job = {"job_id": job_id, "runtime_id": runtime_id, "run_id": run_id, "request_id": request_id,
               "command_id": command_id, "status": "queued", "send_status": "not_sent",
               "raw_output": "", "parsed": None, "error": None, "test_only": True}
        self.requests[request_id] = (fingerprint, job_id)
        self.jobs[job_id] = job
        self.tasks.append(asyncio.create_task(self._execute(job, text, arguments.copy(), timeout_seconds)))
        return self.get(job_id)

    def get(self, job_id):
        return deepcopy(self.jobs[job_id])

    async def _execute(self, job, text, arguments, timeout):
        job.update(status="running", send_status="unknown")
        try:
            raw = await asyncio.to_thread(self.operation, text, self.password, port=self.port, timeout=timeout)
            parsed = parse_output(raw, run_id=job["run_id"], arguments=arguments)
            job.update(status=parsed["status"], send_status="confirmed", raw_output=raw, parsed=parsed)
        except Exception as exc:
            code = "TIMEOUT" if isinstance(exc, TimeoutError) else "EOF_OR_TRANSPORT_ERROR"
            # The adapter cannot prove whether the command was sent on an exception.
            job.update(status="unknown", send_status="unknown", error={"code": code})

    async def close(self):
        # Caller must release the debugger before waiting for blocked RCON operations.
        await asyncio.gather(*self.tasks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime", required=True)
    args = parser.parse_args()
    service = DebuggerService(DebuggerConfig.load(args.config, workspace=ROOT))
    jobs = FixtureJobs(args.runtime, os.environ["MC_RCON_PASSWORD"], int(os.environ["MC_RCON_PORT"]))

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            try:
                await service.close()
            finally:
                await jobs.close()

    app = FastMCP("D integration fixture (test-only jobs)", lifespan=lifespan, strict_input_validation=True)
    register_debugger_tools(app, service)

    @app.tool()
    async def execute_command(command_id: str, arguments: dict, runtime_id: str, run_id: str,
                              request_id: str, timeout_seconds: float = 30) -> dict:
        return {"ok": True, "result": await jobs.submit(command_id=command_id, arguments=arguments,
            runtime_id=runtime_id, run_id=run_id, request_id=request_id, timeout_seconds=timeout_seconds)}

    @app.tool()
    async def get_job(job_id: str) -> dict:
        return {"ok": True, "result": jobs.get(job_id)}

    app.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()

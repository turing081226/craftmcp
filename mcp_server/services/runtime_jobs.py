"""Route lifecycle operations through B's public CommandSpec/JobManager API.

One manager can serve command and lifecycle tools. Lifecycle commands stay private
to start_server/stop_server; the generic execute_command tool keeps its own allowlist.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from mcp_server.models.contracts import ContractError
from mcp_server.services.jobs import CommandSpec, JobError, JobManager
from mcp_server.services.runtime import RuntimeService

PREFIX = "\0craftmcp-lifecycle:"
ACTIONS = {"start_server": "start", "stop_server": "stop"}


class RuntimeJobRouter:
    def __init__(self, runtime: RuntimeService, execute_command: Callable[[str, float], Awaitable[str]]):
        self.runtime = runtime
        self.execute_command = execute_command

    def commands(self) -> dict[str, CommandSpec]:
        def builder(action):
            def build(arguments):
                if set(arguments) != {"runtime_id"}:
                    raise ValueError("A lifecycle operation accepts only runtime_id.")
                target = self.runtime.resolve(arguments["runtime_id"])
                if not self.runtime.can_manage(target.runtime_id):
                    raise ValueError("The runtime is not managed by this service.")
                return PREFIX + json.dumps({"action": action, "runtime_id": target.runtime_id}, sort_keys=True)
            return build

        return {name: CommandSpec(name, builder(action), mutating=True) for name, action in ACTIONS.items()}

    async def execute(self, command: str, timeout: float) -> str:
        if not command.startswith(PREFIX):
            return await self.execute_command(command, timeout)
        payload = json.loads(command[len(PREFIX):])
        try:
            if payload["action"] == "start":
                result = await self.runtime.start(payload["runtime_id"], timeout_seconds=timeout)
            elif payload["action"] == "stop":
                result = await self.runtime.stop(payload["runtime_id"], timeout_seconds=timeout)
            else:
                raise ValueError("Unsupported lifecycle operation.")
        except ContractError as error:
            if error.code in {"start_timeout", "stop_timeout", "release_timeout"}:
                raise TimeoutError(f"{error.code}: {error.message}") from None
            raise RuntimeError(f"{error.code}: {error.message}") from None
        return json.dumps(result)


class RuntimeJobs:
    """Submission adapter for a shared B manager configured with the router above."""

    def __init__(self, runtime: RuntimeService, manager: JobManager):
        self.runtime = runtime
        self.manager = manager

    async def submit(self, action: str, runtime_id: str, *, request_id: str,
                     run_id: str | None = None, timeout_seconds: float = 120) -> dict:
        if action not in ACTIONS:
            raise ContractError("invalid_argument", "Unsupported lifecycle operation.")
        self.runtime.resolve(runtime_id)
        if not self.runtime.can_manage(runtime_id):
            raise ContractError("runtime_unmanaged", "The runtime is not enabled for lifecycle management.")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ContractError("invalid_argument", "request_id must be nonempty.")
        try:
            job = await self.manager.submit(command_id=action, arguments={"runtime_id": runtime_id},
                                            runtime_id=runtime_id, request_id=request_id,
                                            run_id=run_id, timeout=timeout_seconds)
        except JobError as error:
            code = "request_conflict" if "different input" in str(error) else "job_rejected"
            raise ContractError(code, "The shared job manager rejected the lifecycle request.") from None
        return job.as_dict()

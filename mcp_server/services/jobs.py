"""Asynchronous, non-retrying command jobs backed by the RCON adapter."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from mcp_server.services.debugger_fixture import COMMAND_ID, command_text, parse_output


CommandBuilder = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    build: CommandBuilder | None = None
    mutating: bool = False
    build_for_run: Callable[[dict[str, Any], str | None], str] | None = None

    def render(self, arguments: dict[str, Any], run_id: str | None) -> str:
        if self.build_for_run is not None:
            return self.build_for_run(arguments, run_id)
        if self.build is None:
            raise ValueError("Command has no builder.")
        return self.build(arguments)


@dataclass
class Job:
    job_id: str
    request_id: str
    run_id: str | None
    runtime_id: str
    command_id: str
    arguments: dict[str, Any]
    timeout: float
    fingerprint: str
    mutating: bool = False
    status: str = "queued"
    sent: str = "not_sent"
    raw_output: str = ""
    parsed_output: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    created_monotonic: float = field(default_factory=time.monotonic, repr=False)
    started_at: float | None = None
    finished_at: float | None = None
    cancellation_requested: bool = False

    def as_dict(self, *, page: int = 0, page_size: int = 4000) -> dict[str, Any]:
        output = self.raw_output
        start = page * page_size
        end = start + page_size
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "command_id": self.command_id,
            "status": self.status,
            "execution_status": self.sent,
            "sent": self.sent,
            "timeout": self.timeout,
            "created_at": self.created_at,
            "deadline_at": self.created_at + self.timeout,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "raw_output": output[start:end],
            "output_page": page,
            "output_has_more": end < len(output),
            "parsed_output": self.parsed_output,
            "errors": list(self.errors),
        }


class JobError(ValueError):
    """A user-correctable job request error."""


class JobManager:
    def __init__(
        self,
        execute: Callable[[str, float], Awaitable[str]],
        commands: dict[str, CommandSpec],
        *,
        max_mutating_jobs_per_runtime: int = 16,
    ) -> None:
        if max_mutating_jobs_per_runtime < 1:
            raise ValueError("max_mutating_jobs_per_runtime must be at least 1.")
        self._execute = execute
        self._commands = commands
        self._max_mutating_jobs_per_runtime = max_mutating_jobs_per_runtime
        self._jobs: dict[str, Job] = {}
        self._requests: dict[str, tuple[str, str]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._runtime_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(
        self,
        *,
        command_id: str,
        arguments: dict[str, Any] | None = None,
        runtime_id: str = "default",
        request_id: str | None = None,
        run_id: str | None = None,
        timeout: float = 5,
    ) -> Job:
        if self._closed:
            raise JobError("Job manager is closed.")
        spec = self._commands.get(command_id)
        if spec is None:
            raise JobError(f"Unsupported command_id: {command_id}")
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise JobError("runtime_id must not be empty.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise JobError("timeout must be greater than 0 and at most 300 seconds.")
        if arguments is not None and not isinstance(arguments, dict):
            raise JobError("arguments must be an object.")
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
            raise JobError("request_id must not be empty.")
        values = dict(arguments or {})
        try:
            spec.render(values, run_id)
        except (KeyError, TypeError, ValueError) as error:
            raise JobError(f"Invalid arguments for {command_id}: {error}") from error

        return await self._submit(spec, values, runtime_id=runtime_id, request_id=request_id,
                                  run_id=run_id, timeout=timeout)

    async def submit_operation(
        self, *, kind: str, inputs: dict[str, Any],
        operation: Callable[[float], Awaitable[dict[str, Any]]],
        runtime_id: str, request_id: str | None = None,
        run_id: str | None = None, timeout: float = 120,
        mutating: bool = True,
    ) -> Job:
        """Trusted internal operations share the command queue, IDs and deadlines.

        The callable receives the remaining total deadline after queueing. It is
        never serialized or accepted from an MCP caller. There are no nested jobs.
        """
        if self._closed:
            raise JobError("Job manager is closed.")
        if not isinstance(kind, str) or not kind.strip() or not callable(operation):
            raise JobError("An operation requires a kind and callable.")
        if not isinstance(inputs, dict) or not isinstance(runtime_id, str) or not runtime_id.strip():
            raise JobError("Operation inputs/runtime_id are invalid.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise JobError("timeout must be greater than 0 and at most 300 seconds.")
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
            raise JobError("request_id must not be empty.")
        spec = CommandSpec(kind, mutating=mutating)
        return await self._submit(spec, inputs, runtime_id=runtime_id, request_id=request_id,
                                  run_id=run_id, timeout=timeout, operation=operation)

    async def _submit(self, spec, values, *, runtime_id, request_id, run_id, timeout, operation=None):
        # Snapshot nested inputs before queueing; caller edits cannot change a job
        # after its duplicate-request fingerprint has been computed.
        try:
            values = json.loads(json.dumps(values, allow_nan=False))
        except (TypeError, ValueError) as error:
            raise JobError("Job inputs must be JSON values.") from error
        request = request_id or str(uuid.uuid4())
        command_id = spec.command_id
        fingerprint = hashlib.sha256(
            json.dumps(
                [runtime_id, command_id, values, timeout, run_id],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        async with self._lock:
            previous = self._requests.get(request)
            if previous:
                previous_job_id, previous_fingerprint = previous
                if previous_fingerprint != fingerprint:
                    raise JobError("request_id is already used with different input.")
                return self._jobs[previous_job_id]
            if spec.mutating:
                pending = sum(
                    1
                    for item in self._jobs.values()
                    if item.runtime_id == runtime_id
                    and item.mutating
                    and item.status in {"queued", "running"}
                )
                if pending >= self._max_mutating_jobs_per_runtime:
                    raise JobError(
                        f"Too many pending mutating jobs for runtime_id: {runtime_id}"
                    )
            job = Job(
                job_id=str(uuid.uuid4()),
                request_id=request,
                run_id=run_id,
                runtime_id=runtime_id,
                command_id=command_id,
                arguments=values,
                timeout=timeout,
                fingerprint=fingerprint,
                mutating=spec.mutating,
            )
            self._jobs[job.job_id] = job
            self._requests[request] = (job.job_id, fingerprint)
            task = asyncio.create_task(self._run(job, spec, operation))
            self._tasks[job.job_id] = task
            task.add_done_callback(lambda finished, key=job.job_id: self._tasks.pop(key, None))
            return job

    async def get(self, job_id: str) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobError(f"Unknown job_id: {job_id}")
            return job

    async def cancel(self, job_id: str) -> Job:
        job = await self.get(job_id)
        if job.status in {"succeeded", "failed", "unknown", "cancelled"}:
            return job
        job.cancellation_requested = True
        task = self._tasks.get(job_id)
        if task and job.status == "queued":
            task.cancel()
            job.status = "cancelled"
            job.finished_at = time.time()
        return job

    async def close(self) -> None:
        self._closed = True
        entries = list(self._tasks.items())
        tasks = [task for _, task in entries]
        for job_id, task in entries:
            job = self._jobs[job_id]
            # A task cancelled before its coroutine starts cannot run _run's
            # cancellation handler. Record that definite non-execution here.
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                job.errors.append("Job cancelled before execution during shutdown.")
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, job: Job, spec: CommandSpec, operation=None) -> None:
        lock = self._runtime_locks.setdefault(job.runtime_id, asyncio.Lock())
        try:
            async with asyncio.timeout(job.created_monotonic + job.timeout - time.monotonic()):
                if spec.mutating:
                    async with lock:
                        await self._execute_job(job, spec, operation)
                else:
                    await self._execute_job(job, spec, operation)
        except TimeoutError:
            job.status = "failed" if job.sent == "not_sent" else "unknown"
            job.errors.append("Job deadline expired; command was not sent." if job.sent == "not_sent"
                              else "Job deadline expired; command execution is unknown.")
            job.finished_at = time.time()
        except asyncio.CancelledError:
            if job.status != "cancelled":
                job.status = "cancelled" if job.sent == "not_sent" else "unknown"
                job.errors.append("Job cancelled before transmission." if job.sent == "not_sent"
                                  else "Job task cancelled; command execution may be unknown.")
                job.finished_at = time.time()
            raise
        finally:
            self._tasks.pop(job.job_id, None)

    async def _execute_job(self, job: Job, spec: CommandSpec, operation=None) -> None:
        if job.cancellation_requested:
            job.status = "cancelled"
            job.finished_at = time.time()
            return
        job.status = "running"
        job.started_at = time.time()
        try:
            command = None if operation is not None else spec.render(job.arguments, job.run_id)
            remaining = job.created_monotonic + job.timeout - time.monotonic()
            if remaining <= 0:
                job.status = "failed"
                job.errors.append("Job deadline expired before transmission.")
                return
            job.sent = "unknown"  # The callback cannot prove the exact socket-send instant.
            if operation is not None:
                parsed = await operation(remaining)
                if not isinstance(parsed, dict) or type(parsed.get("ok")) is not bool:
                    raise ValueError("Operation must return an explicit ok result.")
                raw_output = parsed.get("raw_output", json.dumps(parsed, ensure_ascii=False, allow_nan=False))
                if not isinstance(raw_output, str):
                    raise ValueError("Operation raw_output must be text.")
                job.raw_output = raw_output
                job.parsed_output = {**parsed, "status": (
                    "unknown" if parsed.get("execution_unknown") else
                    "succeeded" if parsed["ok"] else "failed")}
                job.sent = "unknown" if parsed.get("execution_unknown") else "confirmed"
            else:
                output = await self._execute(command, remaining)
                job.sent = "confirmed"
                job.raw_output = output
                job.parsed_output = _parse_command_result(job.command_id, output, job.arguments, job.run_id)
            job.status = "unknown" if job.cancellation_requested else job.parsed_output.get("status", "succeeded")
            if job.cancellation_requested:
                job.errors.append("Cancellation requested after transmission; execution is unknown.")
        except TimeoutError as error:
            job.sent = "unknown" if job.started_at else "not_sent"
            job.status = "unknown"
            job.errors.append(str(error))
        except (ConnectionError, OSError, RuntimeError, UnicodeError, ValueError, TypeError) as error:
            job.status = "unknown" if job.sent != "not_sent" else "failed"
            job.errors.append(str(error))
        finally:
            job.finished_at = time.time()


def _parse_command_result(command_id: str, output: str, arguments: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    if command_id == COMMAND_ID:
        return parse_output(output, run_id=run_id, arguments=arguments)
    if command_id == "list_players":
        match = re.search(r"There are (\d+) of a max of (\d+) players online", output)
        if match:
            return {"online": int(match.group(1)), "max": int(match.group(2))}
    return {"text": output}


def default_commands() -> dict[str, CommandSpec]:
    return {
        "list_players": CommandSpec("list_players", _no_arguments("list")),
        "say": CommandSpec("say", _say_command, mutating=True),
        COMMAND_ID: CommandSpec(COMMAND_ID, build_for_run=command_text),
    }


def _no_arguments(command: str) -> CommandBuilder:
    def build(arguments: dict[str, Any]) -> str:
        if arguments:
            raise ValueError("this command does not accept arguments")
        return command

    return build


def _say_command(arguments: dict[str, Any]) -> str:
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip() or "\n" in message or "\r" in message:
        raise ValueError("message must be a non-empty single-line string")
    return f"say {message}"

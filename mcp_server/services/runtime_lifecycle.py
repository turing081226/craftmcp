"""Explicit lifecycle of processes created by this service, using runner gates.

The legacy paper_runner keeps its automatic restart behavior. These owned children
restart only through start(), so a confirmed stop remains stopped.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from mcp_server.models.contracts import ContractError, Observation, RuntimeTarget
from mcp_server.paper_runner import CONTROL_DIR, installation_ready
from mcp_server.services.file_gate import FileGate

if TYPE_CHECKING:
    from mcp_server.services.runtime import RuntimeRegistry

ReleaseRuntime = Callable[[str], Awaitable[None]]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class LaunchSpec:
    """Trusted local configuration, never supplied by an MCP tool caller."""

    command: tuple[str, ...]
    ready_pattern: str | None = r"\bDone \([^)]+\)!"

    def __post_init__(self):
        object.__setattr__(self, "command", tuple(self.command))
        if not self.command or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in self.command):
            raise ValueError("Launch commands must contain nonempty arguments without NUL bytes.")
        if self.ready_pattern is not None:
            re.compile(self.ready_pattern)


def paper_launch(target: RuntimeTarget, *, java: str = "java") -> LaunchSpec:
    command = [java, "-Xms256M", "-Xmx1G", "-Dfile.encoding=UTF-8",
               "-Dterminal.jline=false", "-Dterminal.ansi=false"]
    if target.jdwp:
        if target.jdwp.host not in LOCAL_HOSTS:
            raise ValueError("Managed JDWP must use a loopback address.")
        host = f"[{target.jdwp.host}]" if ":" in target.jdwp.host else target.jdwp.host
        command.append("-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,"
                       f"address={host}:{target.jdwp.port}")
    return LaunchSpec(tuple([*command, "-jar", str(target.server_jar), "--nogui"]))


@dataclass(eq=False)
class Operation:
    runtime_id: str
    owner: object
    task: asyncio.Task
    active: bool = True


@dataclass
class _Child:
    target: RuntimeTarget
    process: asyncio.subprocess.Process
    gate: FileGate
    log: BinaryIO
    log_path: Path
    state: dict
    ready: bool | None = None
    stop_sent: bool = False
    exited: asyncio.Event = field(default_factory=asyncio.Event)
    watcher: asyncio.Task | None = None


class RuntimeLifecycle:
    def __init__(self, registry: RuntimeRegistry, launches: Mapping[str, LaunchSpec], *,
                 release_runtime: ReleaseRuntime | None = None):
        self.registry = registry
        self.launches = dict(launches)
        for runtime_id, spec in self.launches.items():
            registry.resolve(runtime_id)
            if not isinstance(spec, LaunchSpec):
                raise ValueError("A runtime launch must be a LaunchSpec.")
        self.release_runtime = release_runtime
        self._children: dict[str, _Child] = {}
        self._operations: dict[str, Operation] = {}
        self._runner_id = uuid.uuid4().hex
        self._closing = False

    def can_manage(self, target: RuntimeTarget) -> bool:
        return (target.managed and target.runtime_id in self.launches
                and (target.jdwp is None or target.jdwp.host in LOCAL_HOSTS))

    def _target(self, runtime_id: str) -> RuntimeTarget:
        target = self.registry.resolve(runtime_id)
        if not self.can_manage(target):
            raise ContractError("runtime_unmanaged", "This runtime is not enabled for local lifecycle management.")
        return target

    @staticmethod
    def _control(target: RuntimeTarget) -> Path:
        control = target.server_dir / CONTROL_DIR
        if not target.server_dir.is_dir():
            raise ContractError("invalid_launch", "The managed server directory must exist.")
        if control.is_symlink() or not control.resolve().is_relative_to(target.server_dir):
            raise ContractError("invalid_launch", "The control directory must remain inside the server directory.")
        for name in ("operation.lock", "deploy.lock", "runner.lock", "state.lock", "state.json", "boots"):
            if (control / name).is_symlink():
                raise ContractError("invalid_launch", "Control paths must not be symbolic links.")
        return control

    @asynccontextmanager
    async def exclusive(self, runtime_id: str):
        target = self._target(runtime_id)
        control = self._control(target)
        # Nonblocking gates also coordinate with the existing deployment service.
        # A deployment passes this token to internal stop/start instead of relocking.
        try:
            with FileGate(control / "operation.lock"), FileGate(control / "deploy.lock"):
                token = Operation(runtime_id, self, asyncio.current_task())
                self._operations[runtime_id] = token
                try:
                    yield token
                finally:
                    token.active = False
                    self._operations.pop(runtime_id, None)
        except BlockingIOError:
            raise ContractError("operation_busy", "Another server operation is active.") from None

    def _validate_token(self, runtime_id: str, token: Operation):
        if (not isinstance(token, Operation) or token.owner is not self or not token.active
                or token.runtime_id != runtime_id or token.task is not asyncio.current_task()
                or self._operations.get(runtime_id) is not token):
            raise ContractError("invalid_operation", "The operation token is expired or belongs to another operation.")

    @staticmethod
    def _deadline(timeout_seconds: float) -> float:
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 300):
            raise ContractError("invalid_argument", "timeout_seconds must be greater than 0 and at most 300.")
        return time.monotonic() + timeout_seconds

    @staticmethod
    def _publish(target: RuntimeTarget, state: dict):
        with FileGate(target.server_dir / CONTROL_DIR / "state.lock"):
            state["heartbeat"] = time.time()
            (target.server_dir / CONTROL_DIR / "state.json").write_text(json.dumps(state), encoding="utf-8")

    async def _watch(self, child: _Child):
        try:
            while child.process.returncode is None:
                try:
                    self._publish(child.target, child.state)
                except OSError:
                    pass  # Direct process ownership remains valid if a snapshot cannot be written.
                await asyncio.sleep(0.1)
            await child.process.wait()
        finally:
            # Cancellation of the application never kills a child or adopts its PID.
            # A running orphan is left marked running; the next service refuses it.
            if child.process.returncode is not None:
                child.state.update(phase="stopped", pid=None, exit_code=child.process.returncode)
                try:
                    self._publish(child.target, child.state)
                except OSError:
                    pass
                child.exited.set()
                child.process.stdin.close()
            child.log.close()
            child.gate.__exit__(None, None, None)

    @staticmethod
    def _snapshot(child: _Child, **extra) -> dict:
        alive = child.process.returncode is None
        return {"runtime_id": child.target.runtime_id, "phase": "running" if alive else "stopped",
                "boot_id": child.state["boot_id"], "pid": child.process.pid if alive else None,
                "exit_code": child.process.returncode, "game_ready": child.ready if alive else None,
                "stop_requested": child.stop_sent, **extra}

    def observe(self, target: RuntimeTarget) -> Observation | None:
        child = self._children.get(target.runtime_id)
        if child is None:
            return None
        snapshot = self._snapshot(child)
        return Observation("observed", snapshot["phase"], source="owned_process",
                           details={"pid": snapshot["pid"], "runner_id": self._runner_id,
                                    "boot_id": snapshot["boot_id"] if snapshot["pid"] else None})

    def observe_ready(self, target: RuntimeTarget) -> Observation:
        child = self._children.get(target.runtime_id)
        if child and child.process.returncode is None and child.ready is True:
            return Observation("observed", True, source="current_boot_log",
                               details={"boot_id": child.state["boot_id"]})
        return Observation("unknown", source="readiness_not_observed")

    async def start(self, runtime_id: str, *, timeout_seconds: float = 120,
                    operation: Operation | None = None) -> dict:
        deadline = self._deadline(timeout_seconds)
        self._target(runtime_id)
        if self._closing:
            raise ContractError("service_closed", "The lifecycle service is closing.")
        if operation is None:
            async with self.exclusive(runtime_id) as token:
                return await self._start(runtime_id, deadline, token)
        self._validate_token(runtime_id, operation)
        return await self._start(runtime_id, deadline, operation)

    async def _start(self, runtime_id: str, deadline: float, operation: Operation) -> dict:
        target = self._target(runtime_id)
        if target.jdwp is not None and self.release_runtime is None:
            raise ContractError("debugger_release_unavailable", "Connect runtime-specific debugger release before starting a JDWP server.")
        child = self._children.get(runtime_id)
        if child and child.process.returncode is None:
            await self._wait_ready(child, deadline)
            return self._snapshot(child, already_running=True)
        if child:
            await child.exited.wait()
        if not target.server_jar.is_file():
            raise ContractError("invalid_launch", "The configured server JAR must exist.")
        command = self.launches[runtime_id].command
        try:
            jar = (target.server_dir / command[command.index("-jar") + 1]).resolve()
        except (ValueError, IndexError, OSError):
            raise ContractError("invalid_launch", "The launch must identify the configured server JAR.") from None
        if jar != target.server_jar:
            raise ContractError("invalid_launch", "The launch JAR differs from the registered runtime.")
        if target.jdwp:
            host = f"[{target.jdwp.host}]" if ":" in target.jdwp.host else target.jdwp.host
            address = f"address={host}:{target.jdwp.port}"
            if not any(arg.startswith("-agentlib:jdwp=") and address in arg.split(",") for arg in command):
                raise ContractError("invalid_launch", "The launch JDWP address differs from the registered runtime.")
        control = self._control(target)
        if not installation_ready(target.server_dir):
            raise ContractError("installation_incomplete", "Complete or recover the pending installation before starting.")
        gate = FileGate(control / "runner.lock")
        try:
            gate.__enter__()
        except BlockingIOError:
            raise ContractError("runtime_not_owned", "Another runner owns this server; it cannot be adopted.") from None
        log = None
        state = None
        transferred = False
        try:
            previous = control / "state.json"
            if previous.exists():
                try:
                    old = json.loads(previous.read_text(encoding="utf-8"))
                    safe = old.get("phase") in {"stopped", "closed"} and old.get("pid") is None
                except (OSError, ValueError, AttributeError):
                    safe = False
                if not safe:
                    raise ContractError("ownership_unknown", "Previous process exit is unconfirmed; automatic adoption is refused.")
            boot_id = uuid.uuid4().hex
            logs = control / "boots"
            logs.mkdir(exist_ok=True)
            path = logs / f"{boot_id}.log"
            log = path.open("xb")
            state = {"server_dir": str(target.server_dir), "runner_id": self._runner_id,
                     "runner_pid": os.getpid(), "command": list(self.launches[runtime_id].command),
                     "boot_id": boot_id, "phase": "starting", "pid": None, "console_log": str(path)}
            self._publish(target, state)
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                *self.launches[runtime_id].command, cwd=target.server_dir,
                stdin=asyncio.subprocess.PIPE, stdout=log, stderr=asyncio.subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
            cancelled = False
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                # Finish obtaining the handle before propagating cancellation.
                process = await spawn
                cancelled = True
            state.update(phase="running", pid=process.pid)
            child = _Child(target, process, gate, log, path, state)
            self._children[runtime_id] = child
            child.watcher = asyncio.create_task(self._watch(child))
            transferred = True
            log = None  # Ownership of the file and gate has moved to the watcher.
            if cancelled:
                raise asyncio.CancelledError
        except BaseException as error:
            if not transferred:
                if state is not None:
                    state.update(phase="stopped", pid=None)
                    try:
                        self._publish(target, state)
                    except OSError:
                        pass
                if log:
                    log.close()
                gate.__exit__(None, None, None)
            if isinstance(error, OSError):
                raise ContractError("start_failed", "The owned server process could not be started.") from None
            raise
        await self._wait_ready(child, deadline)
        return self._snapshot(child, already_running=False)

    async def _wait_ready(self, child: _Child, deadline: float):
        pattern = self.launches[child.target.runtime_id].ready_pattern
        while True:
            if child.process.returncode is not None:
                raise ContractError("start_failed", "The owned server exited before readiness was confirmed.")
            if pattern is None:
                return  # Process running is the only claim for launches without a readiness probe.
            with child.log_path.open("rb") as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 65536))
                text = log.read(65536).decode("utf-8", errors="replace").replace("\r\n", "\n")
            if re.search(pattern, text, re.MULTILINE):
                child.ready = True
                return
            if time.monotonic() >= deadline:
                raise ContractError("start_timeout", "Readiness is unconfirmed; the owned process may still be running.")
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))

    async def stop(self, runtime_id: str, *, timeout_seconds: float = 30,
                   operation: Operation | None = None) -> dict:
        deadline = self._deadline(timeout_seconds)
        self._target(runtime_id)
        if operation is None:
            async with self.exclusive(runtime_id) as token:
                return await self._stop(runtime_id, deadline, token)
        self._validate_token(runtime_id, operation)
        return await self._stop(runtime_id, deadline, operation)

    async def _stop(self, runtime_id: str, deadline: float, operation: Operation) -> dict:
        child = self._children.get(runtime_id)
        if child is None:
            raise ContractError("runtime_not_owned", "Only a process created by this service can be stopped.")
        if child.process.returncode is not None:
            await child.exited.wait()
            return self._snapshot(child, already_stopped=True, exit_confirmed=True)
        if not child.stop_sent:
            if child.target.jdwp is not None and self.release_runtime is None:
                raise ContractError("debugger_release_unavailable", "Connect runtime-specific debugger release before stopping a JDWP server.")
            if self.release_runtime:
                try:
                    await asyncio.wait_for(self.release_runtime(runtime_id), max(0, deadline - time.monotonic()))
                except TimeoutError:
                    raise ContractError("release_timeout", "Debugger release timed out; stop was not sent.") from None
                except Exception:
                    raise ContractError("debugger_release_failed", "Debugger release failed; stop was not sent.") from None
            if time.monotonic() >= deadline:
                raise ContractError("stop_timeout", "The deadline expired before stop could be sent.")
            # Mark before writing: EOF/drain errors never cause automatic retransmission.
            child.stop_sent = True
            try:
                child.process.stdin.write(b"stop\n")
                await asyncio.wait_for(child.process.stdin.drain(), max(0, deadline - time.monotonic()))
            except (OSError, ConnectionError, TimeoutError):
                pass
        try:
            await asyncio.wait_for(child.exited.wait(), max(0, deadline - time.monotonic()))
        except TimeoutError:
            raise ContractError("stop_timeout", "The same owned process has not exited; stop is not retried or forced.") from None
        return self._snapshot(child, already_stopped=False, exit_confirmed=True)

    async def close(self, *, timeout_seconds: float = 30) -> dict:
        self._deadline(timeout_seconds)
        self._closing = True

        async def finish(runtime_id):
            try:
                return {"ok": True, "result": await self.stop(runtime_id, timeout_seconds=timeout_seconds)}
            except ContractError as error:
                return {"ok": False, "runtime_id": runtime_id, "error": {"code": error.code, "message": error.message}}

        results = await asyncio.gather(*(finish(key) for key in list(self._children)))
        return {"ok": all(item["ok"] for item in results), "runtimes": results}

"""Runtime resolution, bounded observations, and optional owned lifecycle."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from mcp_server.models.contracts import (
    ContractError, Endpoint, Observation, RconEndpoint, RuntimeTarget, result_envelope,
)
from mcp_server.paper_runner import read_state

if TYPE_CHECKING:
    from mcp_server.services.debugger import DebuggerConfig
    from mcp_server.services.runtime_lifecycle import Operation, RuntimeLifecycle

Observer = Callable[[RuntimeTarget], Awaitable[Observation]]
CHANNELS = ("process", "rcon", "jdwp", "suspension", "game_ready")


class RuntimeRegistry:
    def __init__(self, targets: Iterable[RuntimeTarget]):
        records = {}
        directories = set()
        for target in targets:
            if target.runtime_id in records:
                raise ValueError("Duplicate runtime ID.")
            # One canonical ID per server prevents independent mutation locks later.
            if target.server_dir in directories:
                raise ValueError("A server directory must have one canonical runtime ID.")
            records[target.runtime_id] = target
            directories.add(target.server_dir)
        self._targets = MappingProxyType(records)

    def resolve(self, runtime_id: str) -> RuntimeTarget:
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise ContractError("invalid_argument", "runtime_id must be a nonempty string.")
        try:
            return self._targets[runtime_id]
        except KeyError:
            raise ContractError("runtime_not_found", "The runtime is not registered.") from None

    def list(self) -> tuple[RuntimeTarget, ...]:
        return tuple(self._targets[key] for key in sorted(self._targets))

    @classmethod
    def from_debugger_config(cls, config: DebuggerConfig, *,
                             rcon_by_runtime: Mapping[str, RconEndpoint] | None = None,
                             managed_ids: Iterable[str] = ()) -> RuntimeRegistry:
        """Bridge the existing validated config without changing D's debugger models.

        RCON bindings are explicit per runtime; never copy global MC_* settings to
        every server. Legacy boot_id/server_version strings are not observations.
        """
        bindings = dict(rcon_by_runtime or {})
        managed = set(managed_ids)
        if bindings.keys() - config.runtimes.keys():
            raise ValueError("RCON bindings reference an unregistered runtime.")
        if managed - config.runtimes.keys():
            raise ValueError("Managed IDs reference an unregistered runtime.")
        return cls(RuntimeTarget(
            runtime_id=runtime_id, project_id=runtime.project_id,
            project_dir=config.projects[runtime.project_id].root,
            server_dir=runtime.jar.parent, server_jar=runtime.jar,
            jdwp=Endpoint(runtime.host, runtime.port), rcon=bindings.get(runtime_id),
            managed=runtime_id in managed,
        ) for runtime_id, runtime in config.runtimes.items())


def _runner_observation(target: RuntimeTarget) -> Observation:
    # Do not read local process state as evidence for a remote JDWP target.
    if target.jdwp is not None and target.jdwp.host not in {"127.0.0.1", "localhost", "::1"}:
        return Observation("unknown", source="remote_runtime")
    try:
        state = read_state(target.server_dir)
    except RuntimeError:
        return Observation("unknown", source="runner_unavailable")
    command = state.get("command")
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        return Observation("unknown", source="runner_target_mismatch")
    try:
        jar = Path(command[command.index("-jar") + 1])
        jar = (target.server_dir / jar).resolve()
    except (ValueError, IndexError, OSError):
        return Observation("unknown", source="runner_target_mismatch")
    if jar != target.server_jar:
        return Observation("unknown", source="runner_target_mismatch")
    if target.jdwp is not None:
        host = f"[{target.jdwp.host}]" if ":" in target.jdwp.host else target.jdwp.host
        address = f"address={host}:{target.jdwp.port}"
        if not any(arg.startswith("-agentlib:jdwp=") and address in arg.split(",") for arg in command):
            return Observation("unknown", source="runner_target_mismatch")
    phase, pid = state["phase"], state.get("pid")
    if phase == "running" and (type(pid) is not int or pid <= 0):
        return Observation("unknown", source="runner_invalid_pid")
    details = {"pid": pid, "runner_id": state["runner_id"], "heartbeat": state["heartbeat"]}
    if phase == "running":
        details["boot_id"] = state["boot_id"]
    return Observation("observed", value=phase, source="paper_runner", details=details)


async def observe_runner(target: RuntimeTarget) -> Observation:
    return await asyncio.to_thread(_runner_observation, target)


class RuntimeService:
    def __init__(self, registry: RuntimeRegistry, *, observers: Mapping[str, Observer] | None = None,
                 observation_timeout: float = 1, lifecycle: RuntimeLifecycle | None = None):
        if (type(observation_timeout) not in (float, int) or not math.isfinite(observation_timeout)
                or not 0 < observation_timeout <= 5):
            raise ValueError("Observation timeout must be greater than 0 and at most 5 seconds.")
        self.registry = registry
        if lifecycle is not None and lifecycle.registry is not registry:
            raise ValueError("Lifecycle and observations must use the same registry.")
        self.lifecycle = lifecycle
        self.observers = {"process": observe_runner, **dict(observers or {})}
        if lifecycle:
            async def owned_process(target):
                return lifecycle.observe(target) or await observe_runner(target)

            async def owned_ready(target):
                return lifecycle.observe_ready(target)

            if not observers or "process" not in observers:
                self.observers["process"] = owned_process
            if not observers or "game_ready" not in observers:
                self.observers["game_ready"] = owned_ready
        if self.observers.keys() - set(CHANNELS):
            raise ValueError("Unknown observation channel.")
        self.observation_timeout = observation_timeout

    def resolve(self, runtime_id: str) -> RuntimeTarget:
        return self.registry.resolve(runtime_id)

    def can_manage(self, runtime_id: str) -> bool:
        return bool(self.lifecycle and self.lifecycle.can_manage(self.resolve(runtime_id)))

    def exclusive(self, runtime_id: str):
        if self.lifecycle is None:
            raise ContractError("unsupported", "Lifecycle management is not connected.")
        return self.lifecycle.exclusive(runtime_id)

    async def start(self, runtime_id: str, *, timeout_seconds: float = 120,
                    operation: Operation | None = None) -> dict:
        if self.lifecycle is None:
            raise ContractError("unsupported", "Lifecycle management is not connected.")
        return await self.lifecycle.start(runtime_id, timeout_seconds=timeout_seconds, operation=operation)

    async def stop(self, runtime_id: str, *, timeout_seconds: float = 30,
                   operation: Operation | None = None) -> dict:
        if self.lifecycle is None:
            raise ContractError("unsupported", "Lifecycle management is not connected.")
        return await self.lifecycle.stop(runtime_id, timeout_seconds=timeout_seconds, operation=operation)

    async def close(self, *, timeout_seconds: float = 30) -> dict:
        return await self.lifecycle.close(timeout_seconds=timeout_seconds) if self.lifecycle else {"ok": True, "runtimes": []}

    def list_runtimes(self) -> dict:
        return result_envelope(result={"runtimes": [
            {**target.as_dict(), "origin": "configured",
             "capabilities": {"resolve": True, "status": True,
                              "start": self.can_manage(target.runtime_id), "stop": self.can_manage(target.runtime_id),
                              "observations": sorted(self.observers)}}
            for target in self.registry.list()
        ]})

    async def status(self, runtime_id: str) -> dict:
        try:
            target = self.resolve(runtime_id)
        except ContractError as error:
            return result_envelope(error=error)

        async def observe(channel: str) -> tuple[str, Observation, str | None]:
            observer = self.observers.get(channel)
            if observer is None:
                return channel, Observation("unsupported", source="observer_not_connected"), None
            try:
                value = await asyncio.wait_for(observer(target), self.observation_timeout)
                if not isinstance(value, Observation):
                    raise TypeError("Observer must return an Observation.")
                return channel, value, None
            except TimeoutError:
                return channel, Observation("unknown", source="observation_timeout"), f"{channel}: observation timed out"
            except Exception:
                # Backend exception text may contain credentials or command payloads.
                return channel, Observation("unknown", source="observation_failed"), f"{channel}: observation failed"

        values = await asyncio.gather(*(observe(channel) for channel in CHANNELS))
        observations = {channel: observation.as_dict() for channel, observation, _ in values}
        process = observations["process"]
        boot_id = (process["details"].get("boot_id") if process["status"] == "observed"
                   and process["value"] == "running" else None)
        return result_envelope(result={"runtime_id": target.runtime_id, "managed": target.managed,
                                      "boot_id": boot_id, "observations": observations},
                               warnings=[warning for _, _, warning in values if warning])

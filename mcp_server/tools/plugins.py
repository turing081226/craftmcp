"""Plugin tools for D's shared app registration (no duplicate legacy tools)."""
from __future__ import annotations

from functools import partial
import inspect
from copy import deepcopy
from typing import Protocol, Callable, Awaitable

from fastmcp import FastMCP

from mcp_server.services.plugins import PluginService, result
from mcp_server.services.plugin_runtime import blocking


class JobService(Protocol):
    async def submit(self, spec: dict, operation: Callable[[], Awaitable[dict]]) -> dict: ...


def register_plugin_tools(mcp: FastMCP, service: PluginService, jobs: JobService | None = None) -> None:
    async def submit(kind, inputs, operation, *, runtime_id=None, request_id=None, run_id=None, timeout_seconds=180):
        if jobs is None:
            if kind == "build_plugin":
                return await blocking(operation, timeout=timeout_seconds)
            return result(False, kind, error="Job service integration is required.", files_modified=False)
        # A lifecycle operations stay on the owning asyncio task. Only C's bounded
        # build/file operations go to a thread, and no nested jobs are submitted.
        async def execute(remaining=timeout_seconds):
            if inspect.iscoroutinefunction(operation):
                return await operation(timeout=remaining)
            return await blocking(operation, timeout=remaining)
        return await jobs.submit({"kind": kind, "runtime_id": runtime_id, "request_id": request_id,
                                  "run_id": run_id, "inputs": inputs, "timeout_seconds": timeout_seconds}, execute)

    @mcp.tool()
    async def build_plugin(plugin_name: str, main_class: str, source_files: dict[str, str],
                           plugin_manifest: dict | None = None, resource_files: dict[str, str] | None = None,
                           project_id: str | None = None, offline: bool = True,
                           request_id: str | None = None, run_id: str | None = None,
                           timeout_seconds: float = 180) -> dict:
        """Build with the pinned Wrapper; offline=False prepares the dependency cache."""
        inputs = deepcopy(dict(plugin_name=plugin_name, main_class=main_class, source_files=source_files,
                      plugin_manifest=plugin_manifest, resource_files=resource_files,
                      project_id=project_id, offline=offline))
        return await submit("build_plugin", inputs, partial(service.build, **inputs),
                            request_id=request_id, run_id=run_id, timeout_seconds=timeout_seconds)

    @mcp.tool()
    async def deploy_plugin(runtime_id: str, build_id: str, request_id: str,
                            run_id: str | None = None, timeout_seconds: float = 120) -> dict:
        """Submit one exact build for stop, replacement, new boot and activation verification."""
        return await submit("deploy_plugin", {"build_id": build_id},
                            partial(service.deploy, runtime_id, build_id, run_id=run_id),
                            runtime_id=runtime_id, request_id=request_id, run_id=run_id, timeout_seconds=timeout_seconds)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_plugin_status(runtime_id: str, plugin_name: str) -> dict:
        """Read disk JAR metadata separately from exact current-boot plugin observations."""
        if inspect.iscoroutinefunction(service.get_plugin_status):
            return await service.get_plugin_status(runtime_id, plugin_name)
        return await blocking(service.get_plugin_status, runtime_id, plugin_name)

    @mcp.tool()
    async def rollback_plugin(runtime_id: str, deployment_id: str, request_id: str,
                              run_id: str | None = None, timeout_seconds: float = 120) -> dict:
        """Restore the hash-verified previous JAR. World/database contents are not restored."""
        return await submit("rollback_plugin", {"deployment_id": deployment_id},
                            partial(service.rollback, runtime_id, deployment_id, run_id=run_id),
                            runtime_id=runtime_id, request_id=request_id, run_id=run_id, timeout_seconds=timeout_seconds)

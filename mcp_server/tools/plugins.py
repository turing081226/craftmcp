"""Plugin tools for D's shared app registration (no duplicate legacy tools)."""
from __future__ import annotations

from functools import partial
from typing import Protocol, Callable, Awaitable

import anyio
from fastmcp import FastMCP

from mcp_server.services.plugins import PluginService, result


class JobService(Protocol):
    async def submit(self, spec: dict, operation: Callable[[], Awaitable[dict]]) -> dict: ...


def register_plugin_tools(mcp: FastMCP, service: PluginService, jobs: JobService | None = None) -> None:
    async def submit(kind, inputs, operation, *, runtime_id=None, request_id=None, run_id=None):
        if jobs is None:
            if kind == "build_plugin":
                return await anyio.to_thread.run_sync(operation)
            return result(False, kind, error="Job service integration is required.", files_modified=False)
        # B owns deduplication and scheduling. Lifecycle calls inside the operation
        # are synchronous internal calls and never submit nested jobs.
        async def execute():
            return await anyio.to_thread.run_sync(operation)
        return await jobs.submit({"kind": kind, "runtime_id": runtime_id, "request_id": request_id,
                                  "run_id": run_id, "inputs": inputs}, execute)

    @mcp.tool()
    async def build_plugin(plugin_name: str, main_class: str, source_files: dict[str, str],
                           plugin_manifest: dict | None = None, resource_files: dict[str, str] | None = None,
                           project_id: str | None = None, offline: bool = True,
                           request_id: str | None = None, run_id: str | None = None) -> dict:
        """Build with the pinned Wrapper; offline=False prepares the dependency cache."""
        inputs = dict(plugin_name=plugin_name, main_class=main_class, source_files=source_files,
                      plugin_manifest=plugin_manifest, resource_files=resource_files,
                      project_id=project_id, offline=offline)
        return await submit("build_plugin", inputs, partial(service.build, **inputs),
                            request_id=request_id, run_id=run_id)

    @mcp.tool()
    async def deploy_plugin(runtime_id: str, build_id: str, request_id: str,
                            run_id: str | None = None) -> dict:
        """Submit one exact build for stop, replacement, new boot and activation verification."""
        return await submit("deploy_plugin", {"build_id": build_id},
                            partial(service.deploy, runtime_id, build_id, run_id=run_id),
                            runtime_id=runtime_id, request_id=request_id, run_id=run_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_plugin_status(runtime_id: str, plugin_name: str) -> dict:
        """Read disk JAR metadata separately from exact current-boot plugin observations."""
        return await anyio.to_thread.run_sync(service.get_plugin_status, runtime_id, plugin_name)

    @mcp.tool()
    async def rollback_plugin(runtime_id: str, deployment_id: str, request_id: str,
                              run_id: str | None = None) -> dict:
        """Restore the hash-verified previous JAR. World/database contents are not restored."""
        return await submit("rollback_plugin", {"deployment_id": deployment_id},
                            partial(service.rollback, runtime_id, deployment_id, run_id=run_id),
                            runtime_id=runtime_id, request_id=request_id, run_id=run_id)

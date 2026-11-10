"""A's tool registration hook; D attaches it to the shared app."""
from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field, StrictStr

from mcp_server.models.contracts import ContractError, result_envelope
from mcp_server.services.runtime import RuntimeService
from mcp_server.services.runtime_jobs import RuntimeJobs

RuntimeID = Annotated[StrictStr, Field(min_length=1)]
Timeout = Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)]


def register_runtime_tools(mcp: FastMCP, service: RuntimeService, jobs: RuntimeJobs | None = None) -> None:
    if jobs is not None and jobs.runtime is not service:
        raise ValueError("Runtime tools and jobs must share the same runtime service.")
    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_runtimes() -> dict:
        """List configured server targets and supported observation channels."""
        return service.list_runtimes()

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_runtime_status(runtime_id: RuntimeID) -> dict:
        """Read bounded observations without sending RCON commands or attaching JDWP."""
        return await service.status(runtime_id)

    if jobs is not None:
        @mcp.tool()
        async def start_server(runtime_id: RuntimeID, request_id: RuntimeID,
                               run_id: RuntimeID | None = None, timeout_seconds: Timeout = 120) -> dict:
            """Submit an owned-server start job; inspect it through the shared get_job tool."""
            try:
                return result_envelope(result=await jobs.submit("start_server", runtime_id, request_id=request_id,
                                                               run_id=run_id, timeout_seconds=timeout_seconds))
            except ContractError as error:
                return result_envelope(error=error)

        @mcp.tool()
        async def stop_server(runtime_id: RuntimeID, request_id: RuntimeID,
                              run_id: RuntimeID | None = None, timeout_seconds: Timeout = 30) -> dict:
            """Submit a graceful stop job; only confirmed process exit counts as stopped."""
            try:
                return result_envelope(result=await jobs.submit("stop_server", runtime_id, request_id=request_id,
                                                               run_id=run_id, timeout_seconds=timeout_seconds))
            except ContractError as error:
                return result_envelope(error=error)

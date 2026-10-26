"""Register exactly the debugger MVP tools on an existing FastMCP instance."""
from __future__ import annotations

from typing import Annotated
from pydantic import Field, StrictInt, StrictStr
from fastmcp import FastMCP
from mcp_server.services.debugger import DebuggerService

Positive = Annotated[StrictInt, Field(ge=1)]
Offset = Annotated[StrictInt, Field(ge=0)]
Limit = Annotated[StrictInt, Field(ge=1, le=100)]
ID = Annotated[StrictStr, Field(min_length=1)]


def register_debugger_tools(mcp: FastMCP, service: DebuggerService) -> None:
    @mcp.tool()
    async def attach_debugger(runtime_id: ID, project_id: ID) -> dict:
        """Attach to a registered JVM. A timeout means execution is unknown; do not retry automatically."""
        return await service.invoke("attach_debugger", {"runtime_id": runtime_id, "project_id": project_id})

    @mcp.tool()
    async def set_breakpoint(session_id: ID, class_name: ID, line: Positive,
                             source_path: ID | None = None, class_loader_id: ID | None = None) -> dict:
        """Set an exact class/line breakpoint. Relative source paths use the registered project root."""
        params = {"session_id": session_id, "class_name": class_name, "line": line}
        if source_path is not None:
            params["source_path"] = source_path
        if class_loader_id is not None:
            params["class_loader_id"] = class_loader_id
        return await service.invoke("set_breakpoint", params)

    @mcp.tool()
    async def remove_breakpoint(session_id: ID, breakpoint_id: ID) -> dict:
        """Remove a previously registered breakpoint."""
        return await service.invoke("remove_breakpoint", {"session_id": session_id, "breakpoint_id": breakpoint_id})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_debug_state(session_id: ID) -> dict:
        """Query live helper state, active stop and event cursor. Events do not run the agent automatically."""
        return await service.invoke("get_debug_state", {"session_id": session_id})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_stack_trace(session_id: ID, stop_id: ID, thread_id: ID,
                              offset: Offset = 0, limit: Limit = 20) -> dict:
        """Read a stack page for a currently suspended stop. References expire after resume."""
        return await service.invoke("get_stack_trace", {"session_id": session_id, "stop_id": stop_id,
                                    "thread_id": thread_id, "offset": offset, "limit": limit})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_variables(session_id: ID, stop_id: ID, frame_id: ID | None = None,
                            object_ref: ID | None = None, offset: Offset = 0, limit: Limit = 20) -> dict:
        """Read locals or fields; specify exactly one frame_id/object_ref. No method invocation.

        debug_info_missing preserves details.argument_values, argument_next_offset and this_value.
        Use offset/limit to page unnamed arguments and this_value.object_ref to inspect fields.
        All references still expire with the stop.
        """
        params = {"session_id": session_id, "stop_id": stop_id, "offset": offset, "limit": limit}
        if frame_id is not None:
            params["frame_id"] = frame_id
        if object_ref is not None:
            params["object_ref"] = object_ref
        return await service.invoke("get_variables", params)

    @mcp.tool()
    async def resume_execution(session_id: ID, stop_id: ID) -> dict:
        """Resume the held stop. All its frame/object references become stale."""
        return await service.invoke("resume_execution", {"session_id": session_id, "stop_id": stop_id})

    @mcp.tool()
    async def detach_debugger(session_id: ID) -> dict:
        """Release held stops and detach from the JVM."""
        return await service.invoke("detach_debugger", {"session_id": session_id})

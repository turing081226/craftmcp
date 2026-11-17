"""Expose screenshots as MCP image blocks, with structured evidence metadata."""
from __future__ import annotations

import json
from functools import partial
from typing import Annotated, Literal

import anyio
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from pydantic import Field, StrictInt, StrictStr

from mcp_server.adapters.client_capture import CaptureError
from mcp_server.services.client_capture import ClientCaptureService, response

ID = Annotated[StrictStr, Field(min_length=1, max_length=64)]
Description = Annotated[StrictStr, Field(min_length=1, max_length=4000)]
Label = Annotated[StrictStr, Field(min_length=1, max_length=200)]


def register_client_capture_tools(mcp: FastMCP, service: ClientCaptureService, focus=None):
    """focus: optional callable(client_id) that brings the game window forward before capture."""
    async def call(operation, *, image=False):
        try:
            value = await anyio.to_thread.run_sync(operation)
            if not image:
                return ToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                                  structured_content=value)
            metadata, png = value
            return ToolResult(content=[TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                                       Image(data=png, format="png").to_image_content()], structured_content=metadata)
        except (CaptureError, OSError, ValueError, KeyError) as error:
            value = response(ok=False, error={"code": getattr(error, "code", "capture_error"), "message": str(error)},
                             functional_result="unknown")
            return ToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                              structured_content=value)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_game_clients() -> ToolResult:
        """List Java GLFW/LWJGL game-window candidates. Select the intended Minecraft client_id."""
        return await call(service.list_clients)

    @mcp.tool()
    async def capture_game_view(client_id: ID, run_id: ID, label: Label, runtime_id: ID | None = None,
                                build_id: ID | None = None, deployment_id: ID | None = None,
                                delay_seconds: Annotated[StrictInt, Field(ge=0, le=10)] = 0,
                                focus_window: bool = True) -> ToolResult:
        """Capture a visible foreground Minecraft client area and return the PNG plus evidence metadata.

        With focus_window (default) the game window is restored and brought forward automatically first;
        no keys or clicks are sent. Set focus_window=false to leave window focus to the user.
        Correlation IDs are caller-supplied references, not proof of server/build identity.
        Capture success is not a plugin assertion; inspect the image and query game state.
        delay_seconds waits before capturing (e.g. for an animation, or for the user to switch manually).
        """
        if focus_window and focus is not None:
            try:
                await anyio.to_thread.run_sync(focus, client_id)
            except (CaptureError, OSError) as error:
                value = response(ok=False, error={"code": getattr(error, "code", "focus_failed"), "message": str(error)},
                                 functional_result="unknown")
                return ToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                                  structured_content=value)
        if delay_seconds:
            await anyio.sleep(delay_seconds)
        return await call(partial(service.capture, client_id, run_id, label, runtime_id=runtime_id,
                                  build_id=build_id, deployment_id=deployment_id), image=True)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_game_capture(run_id: ID, capture_id: ID) -> ToolResult:
        """Return a previously saved PNG with hash verification, for before/after visual review."""
        return await call(partial(service.read_capture, run_id, capture_id), image=True)

    @mcp.tool()
    async def record_visual_assertion(run_id: ID, capture_id: ID, expected: Description, observed: Description,
                                      verdict: Literal["passed", "failed", "unknown"]) -> ToolResult:
        """Record the caller's review of an actual captured image. This tool does not run a vision model.

        The visual verdict is separate from server/game-state assertions. Use unknown when uncertain.
        """
        return await call(partial(service.record_assessment, run_id, capture_id, expected, observed, verdict))

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_visual_run_evidence(run_id: ID, offset: Annotated[StrictInt, Field(ge=0)] = 0,
                                       limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20) -> ToolResult:
        """Page screenshot metadata and caller visual reviews for one run; functional state remains unknown."""
        return await call(partial(service.evidence, run_id, offset, limit))

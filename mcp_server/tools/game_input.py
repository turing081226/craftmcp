"""MCP tools that bring the game forward and play it with keyboard/mouse input."""
from __future__ import annotations

import json
from functools import partial
from typing import Annotated, Literal, Union

import anyio
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from mcp_server.adapters.client_capture import CaptureError
from mcp_server.services.client_capture import response
from mcp_server.services.game_input import MAX_TEXT, GameInputService

ID = Annotated[StrictStr, Field(min_length=1, max_length=64)]
HoldMs = Annotated[StrictInt, Field(ge=0, le=10_000)]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyAction(_Action):
    """Press keys together, hold, release. Default Minecraft keys: w/a/s/d move, space jump,
    shift sneak, ctrl sprint, e inventory, q drop, 1-9 hotbar, escape menu, f5 camera."""
    type: Literal["key"]
    keys: Annotated[list[StrictStr], Field(min_length=1, max_length=4)]
    hold_ms: HoldMs = 50


class ClickAction(_Action):
    """Mouse button. left = attack/break (hold ~1000-3000 ms to mine), right = use/place/open."""
    type: Literal["click"]
    button: Literal["left", "right", "middle"]
    hold_ms: HoldMs = 50
    count: Annotated[StrictInt, Field(ge=1, le=10)] = 1


class LookAction(_Action):
    """Turn the camera by raw mouse counts. With default sensitivity ~600 counts ≈ 90°;
    positive dx turns right, positive dy looks down. Calibrate with a capture."""
    type: Literal["look"]
    dx: Annotated[StrictInt, Field(ge=-3000, le=3000)] = 0
    dy: Annotated[StrictInt, Field(ge=-3000, le=3000)] = 0


class ScrollAction(_Action):
    """Mouse wheel notches; changes the hotbar slot (positive = previous slot)."""
    type: Literal["scroll"]
    amount: Annotated[StrictInt, Field(ge=-9, le=9)]


class TypeTextAction(_Action):
    """Type Unicode text into an already-open text field (chat, sign, anvil)."""
    type: Literal["type_text"]
    text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_TEXT)]


class WaitAction(_Action):
    type: Literal["wait"]
    ms: Annotated[StrictInt, Field(ge=1, le=10_000)]


GameAction = Annotated[Union[KeyAction, ClickAction, LookAction, ScrollAction, TypeTextAction, WaitAction],
                       Field(discriminator="type")]


def register_game_input_tools(mcp: FastMCP, service: GameInputService):
    async def call(operation):
        try:
            value = await anyio.to_thread.run_sync(operation)
        except (CaptureError, OSError, ValueError, KeyError) as error:
            value = response(ok=False, error={"code": getattr(error, "code", "input_error"), "message": str(error)},
                             functional_result="unknown")
        return ToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                          structured_content=value)

    @mcp.tool()
    async def focus_game_window(client_id: ID) -> ToolResult:
        """Restore and bring the selected Minecraft window to the foreground (no clicks or keys sent to the game).

        If Minecraft shows the pause menu after regaining focus, send the escape key to resume.
        """
        return await call(partial(service.focus, client_id))

    @mcp.tool()
    async def send_game_input(client_id: ID, actions: Annotated[list[GameAction], Field(min_length=1, max_length=50)],
                              focus_window: bool = True) -> ToolResult:
        """Play the game: run keyboard/mouse actions in order on the selected Minecraft window.

        Requires CRAFTMCP_GAME_INPUT=1. The window is brought forward first (focus_window), and input
        stops immediately with code focus_lost if the user switches to another window; held keys are
        always released. Total hold/wait time per call is limited to 30 s. Input delivery is not proof
        of an in-game result: capture the screen or query server state afterwards.
        """
        plain = [action.model_dump() for action in actions]
        return await call(partial(service.send, client_id, plain, focus=focus_window))

    @mcp.tool()
    async def run_game_chat_command(client_id: ID, text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_TEXT)],
                                    focus_window: bool = True) -> ToolResult:
        """Type a chat message or player command as the logged-in player (e.g. "/heal 4"): T, text, Enter.

        Use this for player-only commands that RCON cannot run. Requires CRAFTMCP_GAME_INPUT=1.
        The game must be in normal play (no menu open). Verify the effect with a capture or server log.
        """
        return await call(partial(service.chat, client_id, text, focus=focus_window))

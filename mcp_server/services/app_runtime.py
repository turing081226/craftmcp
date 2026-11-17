"""Trusted runtime wiring and bounded exact-plugin observations for the shared app."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
from pathlib import Path

from mcp_server.adapters.rcon import execute_rcon
from mcp_server.models.contracts import Observation, RconEndpoint
from mcp_server.services.plugins import NAME
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.services.runtime_lifecycle import RuntimeLifecycle, paper_launch


def create_runtime(debugger, *, workspace: Path, config_path: Path | None = None):
    settings = {}
    if config_path is not None:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) != {"runtimes"} or not isinstance(data["runtimes"], dict):
            raise ValueError("Runtime configuration must contain a runtimes object.")
        settings = data["runtimes"]
    if settings.keys() - debugger.config.runtimes.keys():
        raise ValueError("Runtime settings reference an unregistered debugger target.")
    bindings, managed = {}, set()
    for rid, item in settings.items():
        if (not isinstance(item, dict) or item.keys() - {"managed", "rcon", "java_executable"}
                or type(item.get("managed", False)) is not bool):
            raise ValueError("Invalid trusted runtime settings.")
        if item.get("managed"):
            managed.add(rid)
        if "rcon" in item:
            bindings[rid] = RconEndpoint(**item["rcon"])
    # Legacy MC_* settings bind exactly one known folder/ID, never every registry entry.
    selected = os.environ.get("MC_RUNTIME_ID", "default")
    if selected in debugger.config.runtimes and os.environ.get("MC_SERVER_DIR"):
        target = debugger.config.runtimes[selected]
        if Path(os.environ["MC_SERVER_DIR"]).resolve() != target.jar.parent:
            raise ValueError("MC_SERVER_DIR and the selected debugger runtime must identify the same server.")
        legacy = RconEndpoint("127.0.0.1", int(os.environ.get("MC_RCON_PORT", "25575")), "MC_RCON_PASSWORD")
        if selected in bindings and bindings[selected] != legacy:
            raise ValueError("Legacy MC_* and explicit runtime RCON bindings disagree.")
        bindings[selected] = legacy
    registry = RuntimeRegistry.from_debugger_config(debugger.config, rcon_by_runtime=bindings, managed_ids=managed)
    launches = {rid: paper_launch(registry.resolve(rid),
                 java=settings[rid].get("java_executable", debugger.config.java_executable)) for rid in managed}
    lifecycle = RuntimeLifecycle(registry, launches, release_runtime=debugger.release_runtime) if managed else None

    async def jdwp(target):
        state = await debugger.runtime_state(target.runtime_id)
        return Observation("unknown", source="no_debugger_session") if state is None else Observation(
            "observed", state["connection_state"], source="existing_debugger_session")

    async def suspension(target):
        state = await debugger.runtime_state(target.runtime_id)
        return Observation("unknown", source="no_debugger_session") if state is None else Observation(
            "observed", state["active_stop"], source="existing_debugger_session")

    return RuntimeService(registry, lifecycle=lifecycle, observers={"jdwp": jdwp, "suspension": suspension})


async def execute_endpoint(endpoint: RconEndpoint | None, command: str, timeout: float) -> str:
    if endpoint is None:
        raise ValueError("RCON is not explicitly bound to this runtime.")
    password = os.environ.get(endpoint.password_env)
    if not password:
        raise ValueError("The runtime RCON credential environment variable is not set.")
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(execute_rcon, command, password,
        host=endpoint.host, port=endpoint.port, timeout=min(300, timeout), cancel_event=cancelled))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        try:
            await asyncio.shield(worker)
        except Exception:
            pass
        raise


def parse_plugin_status(raw: str, plugin_name: str):
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    missing = re.fullmatch(r"CRAFTMCP_PLUGIN_STATUS v=1 missing=([A-Za-z0-9_-]+)", lines[0])
    expected_name = base64.urlsafe_b64encode(plugin_name.encode("utf-8")).decode("ascii").rstrip("=")
    if missing and missing[1] == expected_name:
        return {"plugin_name": plugin_name, "version": None, "enabled": False, "raw_output": raw}
    match = re.fullmatch(r"CRAFTMCP_PLUGIN_STATUS v=1 name=([A-Za-z0-9_-]+) version=([A-Za-z0-9_-]+) enabled=(true|false)", lines[0])
    if not match:
        return None
    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    try:
        name, version = decode(match[1]), decode(match[2])
    except (ValueError, UnicodeError):
        return None
    if name != plugin_name:
        return None
    return {"plugin_name": name, "version": version, "enabled": match[3] == "true", "raw_output": raw}


async def observe_plugin(runtime: RuntimeService, runtime_id: str, plugin_name: str, timeout: float):
    if not NAME.fullmatch(plugin_name):
        raise ValueError("Invalid plugin name.")
    target = runtime.resolve(runtime_id)
    before = await runtime.status(runtime_id)
    boot_id = before.get("result", {}).get("boot_id")
    if not boot_id:
        return None
    raw = await execute_endpoint(target.rcon, "craftmcpstatus " + plugin_name, timeout)
    parsed = parse_plugin_status(raw, plugin_name)
    after = await runtime.status(runtime_id)
    if parsed is None or after.get("result", {}).get("boot_id") != boot_id:
        return None
    return {**parsed, "boot_id": boot_id}

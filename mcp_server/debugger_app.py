"""Debugger-only stdio entry point; shared RCON app may reuse the registration function."""
from __future__ import annotations

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from mcp_server.adapters.debugger import HelperClient
from mcp_server.services.debugger import DebuggerConfig, DebuggerService
from mcp_server.tools.debugger import register_debugger_tools


def create_app(service: DebuggerService) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {"debugger": service}
        finally:
            await service.close()

    mcp = FastMCP("CraftMCP Debugger", lifespan=lifespan, strict_input_validation=True)
    register_debugger_tools(mcp, service)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("debugger.local.json"))
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--mock-helper", action="store_true", help="TEST ONLY: simulated data, never actual JDI observations")
    args = parser.parse_args()
    config = DebuggerConfig.load(args.config, workspace=args.workspace)
    factory = None
    if args.mock_helper:
        fixture = Path(__file__).resolve().parents[1] / "tests/debugger_python/mock_helper.py"
        if not fixture.is_file():
            parser.error("Mock helper is only available in a source checkout.")
        factory = lambda command, **kwargs: HelperClient([sys.executable, "-u", str(fixture)], **kwargs)
    service = DebuggerService(config, client_factory=factory, simulated=args.mock_helper)
    create_app(service).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()

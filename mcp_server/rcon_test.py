import os

from mcp_server.adapters.rcon import execute_rcon

password = os.environ["MC_RCON_PASSWORD"]

print(execute_rcon("list", password,
                   port=int(os.environ.get("MC_RCON_PORT", "25575")),
                   timeout=float(os.environ.get("MC_RCON_TIMEOUT", "5"))))

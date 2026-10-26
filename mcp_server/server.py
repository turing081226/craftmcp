import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcrcon import MCRcon

load_dotenv(Path(__file__).with_name(".env"))

mcp = FastMCP("minecraft-dev")


@mcp.tool()
def list_players() -> str:
    """테스트 Minecraft 서버의 접속자 목록을 조회합니다."""
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        raise RuntimeError("MC_RCON_PASSWORD 환경변수가 설정되지 않았습니다.")

    with MCRcon("127.0.0.1", password, port=25575) as rcon:
        return rcon.command("list")


if __name__ == "__main__":
    mcp.run(transport="stdio")
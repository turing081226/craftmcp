import os
import json
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcrcon import MCRcon

load_dotenv(Path(__file__).with_name(".env"))

mcp = FastMCP("minecraft-dev")
MAX_LOG_LINES = 1000


def _result(
    *,
    ok: bool,
    stage: str,
    errors: list[str] | None = None,
    raw_output: str = "",
    **extra: object,
) -> str:
    return json.dumps(
        {
            "ok": ok,
            "stage": stage,
            "errors": errors or [],
            "raw_output": raw_output,
            **extra,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def list_players() -> str:
    """테스트 Minecraft 서버의 접속자 목록을 조회합니다."""
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        raise RuntimeError("MC_RCON_PASSWORD 환경변수가 설정되지 않았습니다.")

    with MCRcon("127.0.0.1", password, port=25575) as rcon:
        return rcon.command("list")


@mcp.tool()
def get_server_log(start_offset: int = 0, max_lines: int = 100) -> str:
    """서버 로그에서 start_offset 이후의 새 로그를 최대 max_lines줄 반환합니다."""
    server_dir = os.environ.get("MC_SERVER_DIR")
    if not server_dir:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=["MC_SERVER_DIR 환경변수가 설정되지 않았습니다."],
        )
    if start_offset < 0:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=["start_offset은 0 이상이어야 합니다."],
        )
    if not 1 <= max_lines <= MAX_LOG_LINES:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"max_lines는 1부터 {MAX_LOG_LINES} 사이여야 합니다."],
        )

    log_path = Path(server_dir) / "logs" / "latest.log"
    try:
        file_size = log_path.stat().st_size
        if start_offset > file_size:
            return _result(
                ok=False,
                stage="get_server_log",
                errors=["start_offset이 현재 로그 파일 크기보다 큽니다."],
                next_offset=file_size,
            )

        with log_path.open("rb") as log_file:
            log_file.seek(start_offset)
            chunks: list[bytes] = []
            next_offset = start_offset
            for _ in range(max_lines):
                line = log_file.readline()
                if not line:
                    break
                chunks.append(line)
                next_offset = log_file.tell()

        return _result(
            ok=True,
            stage="get_server_log",
            raw_output=b"".join(chunks).decode("utf-8", errors="replace"),
            next_offset=next_offset,
        )
    except FileNotFoundError:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"로그 파일을 찾을 수 없습니다: {log_path}"],
        )
    except OSError as error:
        return _result(
            ok=False,
            stage="get_server_log",
            errors=[f"로그 파일을 읽을 수 없습니다: {error}"],
        )


if __name__ == "__main__":
    mcp.run(transport="stdio")
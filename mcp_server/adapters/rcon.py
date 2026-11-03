"""Synchronous Minecraft RCON with one deadline and deterministic socket cleanup."""
from __future__ import annotations

import math
import select
import socket
import struct
import time

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def execute_rcon(
    command: str,
    password: str,
    *,
    host: str = "127.0.0.1",
    port: int = 25575,
    timeout: float = 5,
) -> str:
    """Connect, authenticate, execute once, and close even on timeout/auth/EOF errors.

    The deadline covers connect, authentication, and all response reads together.
    Commands are never retried: a lost response does not imply non-execution.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("RCON timeout must be greater than 0 and at most 300 seconds.")
    if not 1 <= port <= 65535:
        raise ValueError("RCON port must be between 1 and 65535.")
    if "\0" in command or "\0" in password:
        raise ValueError("RCON command and password cannot contain NUL bytes.")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("RCON operation timed out; execution may be unknown.")
        return value

    # create_connection closes its socket if connect fails; with closes it if auth fails.
    with socket.create_connection((host, port), timeout=remaining()) as connection:
        def read_exact(length: int) -> bytes:
            result = bytearray()
            while len(result) < length:
                connection.settimeout(remaining())
                chunk = connection.recv(length - len(result))
                if not chunk:
                    raise ConnectionError("RCON connection closed before a complete response.")
                result.extend(chunk)
            return bytes(result)

        def exchange(packet_type: int, text: str) -> str:
            payload = struct.pack("<ii", 0, packet_type) + text.encode("utf-8") + b"\0\0"
            connection.settimeout(remaining())
            connection.sendall(struct.pack("<i", len(payload)) + payload)
            chunks = bytearray()
            while True:
                length, = struct.unpack("<i", read_exact(4))
                if not 10 <= length <= MAX_RESPONSE_BYTES:
                    raise ConnectionError("Invalid RCON response length.")
                response = read_exact(length)
                request_id, response_type = struct.unpack("<ii", response[:8])
                if request_id == -1:
                    raise PermissionError("RCON authentication failed.")
                if request_id != 0 or response[-2:] != b"\0\0":
                    raise ConnectionError("Invalid RCON response packet.")
                expected_type = 2 if packet_type == 3 else 0
                if response_type != expected_type:
                    raise ConnectionError("Unexpected RCON response type.")
                chunks.extend(response[8:-2])
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise ConnectionError("RCON response exceeds the size limit.")
                # Preserve mcrcon's currently-available packet draining behavior.
                if not select.select([connection], [], [], 0)[0]:
                    return chunks.decode("utf-8")

        exchange(3, password)
        return exchange(2, command)

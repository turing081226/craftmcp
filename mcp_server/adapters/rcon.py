"""Synchronous Minecraft RCON with one deadline and deterministic socket cleanup."""
from __future__ import annotations

import math
import socket
import struct
import time
from threading import Event

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def execute_rcon(
    command: str,
    password: str,
    *,
    host: str = "127.0.0.1",
    port: int = 25575,
    timeout: float = 5,
    cancel_event: Event | None = None,
) -> str:
    """Connect, authenticate, execute once, and close even on timeout/auth/EOF errors.

    The deadline covers connect, authentication, and all response reads together.
    Commands are never retried: a lost response does not imply non-execution.
    cancel_event interrupts response waits so a cancelled job can close its socket.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("RCON timeout must be greater than 0 and at most 300 seconds.")
    if not 1 <= port <= 65535:
        raise ValueError("RCON port must be between 1 and 65535.")
    if "\0" in command or "\0" in password:
        raise ValueError("RCON command and password cannot contain NUL bytes.")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        if cancel_event is not None and cancel_event.is_set():
            raise ConnectionAbortedError("RCON wait cancelled; execution may be unknown.")
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError("RCON operation timed out; execution may be unknown.")
        return value

    # create_connection closes its socket if connect fails; with closes it if auth fails.
    with socket.create_connection((host, port), timeout=remaining()) as connection:
        def read_exact(length: int) -> bytes:
            result = bytearray()
            while len(result) < length:
                connection.settimeout(min(remaining(), 0.1) if cancel_event is not None else remaining())
                try:
                    chunk = connection.recv(length - len(result))
                except socket.timeout:
                    if cancel_event is None:
                        raise
                    continue
                if not chunk:
                    raise ConnectionError("RCON connection closed before a complete response.")
                result.extend(chunk)
            return bytes(result)

        def send(request_id: int, packet_type: int, text: str) -> None:
            payload = struct.pack("<ii", request_id, packet_type) + text.encode("utf-8") + b"\0\0"
            connection.settimeout(remaining())
            connection.sendall(struct.pack("<i", len(payload)) + payload)

        def receive() -> tuple[int, int, bytes]:
            length, = struct.unpack("<i", read_exact(4))
            if not 10 <= length <= MAX_RESPONSE_BYTES:
                raise ConnectionError("Invalid RCON response length.")
            response = read_exact(length)
            request_id, response_type = struct.unpack("<ii", response[:8])
            if request_id == -1:
                raise PermissionError("RCON authentication failed.")
            if response[-2:] != b"\0\0":
                raise ConnectionError("Invalid RCON response packet.")
            return request_id, response_type, response[8:-2]

        send(0, 3, password)
        request_id, response_type, _ = receive()
        if (request_id, response_type) != (0, 2):
            raise ConnectionError("Unexpected RCON authentication response.")
        send(0, 2, command)
        chunks = bytearray()
        marker_sent = False
        while True:
            request_id, response_type, body = receive()
            if request_id == 1:
                if response_type != 0 or body != b"Unknown request 0" or not marker_sent:
                    raise ConnectionError("RCON response completion could not be verified.")
                return chunks.decode("utf-8")
            if (request_id, response_type) != (0, 0):
                raise ConnectionError("Unexpected RCON command response.")
            chunks.extend(body)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise ConnectionError("RCON response exceeds the size limit.")
            if not marker_sent:
                # Minecraft/Paper processes packets in order. Type 0 produces an
                # identifiable response after all command packets, without running a command.
                # Send after the first response so requests are not coalesced in old servers.
                send(1, 0, "")
                marker_sent = True

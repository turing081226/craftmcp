"""Stateless, rotation-aware cursors; only complete log lines are consumed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import BinaryIO


def _prefix_hash(source: BinaryIO, length: int):
    digest = hashlib.sha256()
    source.seek(0)
    while length:
        chunk = source.read(min(length, 65536))
        if not chunk:
            raise ValueError("Log changed while reading; restart at offset 0.")
        digest.update(chunk)
        length -= len(chunk)
    return digest


def read_server_log(
    path: Path, *, start_offset: int = 0, max_lines: int = 100, cursor: str | None = None,
) -> dict:
    previous = None
    if cursor is not None:
        try:
            if len(cursor) > 4096:
                raise ValueError
            previous = json.loads(cursor)
            if (not isinstance(previous, dict)
                    or set(previous) != {"v", "file_id", "offset", "sha256"}
                    or type(previous["v"]) is not int or previous["v"] != 1
                    or type(previous["offset"]) is not int or previous["offset"] < 0
                    or not isinstance(previous["file_id"], list)
                    or len(previous["file_id"]) != 2
                    or any(type(item) is not int for item in previous["file_id"])
                    or not isinstance(previous["sha256"], str)
                    or len(previous["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in previous["sha256"])):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise ValueError("Invalid log cursor; restart at offset 0.") from error
        if start_offset not in (0, previous["offset"]):
            raise ValueError("start_offset does not match the log cursor.")
        start_offset = previous["offset"]
    elif start_offset:
        raise ValueError("Continuing from a numeric offset requires the previous next_cursor; restart at offset 0.")

    with path.open("rb") as source:
        metadata = os.fstat(source.fileno())
        file_id = [metadata.st_dev, metadata.st_ino]
        reset_reason = None
        if previous:
            if previous["file_id"] != file_id:
                reset_reason = "file_replaced"
            elif start_offset > metadata.st_size:
                reset_reason = "file_truncated"
        if reset_reason:
            start_offset = 0
        digest = _prefix_hash(source, start_offset)
        if previous and not reset_reason and digest.hexdigest() != previous["sha256"]:
            reset_reason = "content_changed"
            start_offset = 0
            digest = _prefix_hash(source, 0)

        chunks = []
        next_offset = start_offset
        for _ in range(max_lines):
            line = source.readline()
            if not line.endswith(b"\n"):
                break  # Keep partial UTF-8 bytes (and unterminated lines) for the next poll.
            chunks.append(line)
            digest.update(line)
            next_offset = source.tell()

    return {
        "raw_output": b"".join(chunks).decode("utf-8", errors="replace"),
        "next_offset": next_offset,
        "next_cursor": json.dumps({"v": 1, "file_id": file_id, "offset": next_offset,
                                   "sha256": digest.hexdigest()}, separators=(",", ":")),
        "reset": reset_reason is not None,
        "reset_reason": reset_reason,
    }

"""Version 1 runtime and result contracts owned by team A."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "\r\n\0")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    def __post_init__(self):
        if not _text(self.host) or type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("An endpoint requires a host and a port between 1 and 65535.")


@dataclass(frozen=True)
class RconEndpoint(Endpoint):
    password_env: str

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.password_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.password_env):
            raise ValueError("RCON credentials must reference an environment variable name.")


@dataclass(frozen=True)
class RuntimeTarget:
    runtime_id: str
    project_id: str
    project_dir: Path
    server_dir: Path
    server_jar: Path
    jdwp: Endpoint | None = None
    rcon: RconEndpoint | None = None
    managed: bool = False

    def __post_init__(self):
        if not _text(self.runtime_id) or not _text(self.project_id) or type(self.managed) is not bool:
            raise ValueError("Runtime and project IDs must be nonempty; managed must be boolean.")
        for name in ("project_dir", "server_dir", "server_jar"):
            value = Path(getattr(self, name)).resolve()
            object.__setattr__(self, name, value)
        if self.server_jar.parent != self.server_dir:
            raise ValueError("The server JAR must belong to the selected server directory.")
        if self.jdwp is not None and not isinstance(self.jdwp, Endpoint):
            raise ValueError("Invalid JDWP endpoint.")
        if self.rcon is not None and not isinstance(self.rcon, RconEndpoint):
            raise ValueError("Invalid RCON endpoint.")

    def as_dict(self) -> dict:
        data = asdict(self)
        for name in ("project_dir", "server_dir", "server_jar"):
            data[name] = str(data[name])
        return data


@dataclass(frozen=True)
class Observation:
    status: Literal["observed", "unknown", "unsupported"]
    value: Any = None
    source: str = "unavailable"
    observed_at: str = field(default_factory=utc_now)
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in {"observed", "unknown", "unsupported"}:
            raise ValueError("Invalid observation status.")
        if self.status != "observed" and self.value is not None:
            raise ValueError("An unobserved value must be null.")

    def as_dict(self) -> dict:
        return asdict(self)


class ContractError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def result_envelope(*, result: dict | None = None, error: ContractError | None = None,
                    warnings: list[str] | None = None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "ok": error is None,
            "result": result if error is None else None,
            "error": {"code": error.code, "message": error.message} if error else None,
            "observed_at": utc_now(), "warnings": list(warnings or ()), "evidence_ids": []}

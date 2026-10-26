"""Strict JSON Lines v1 envelopes and method parameters (no ID reinterpretation)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024
MAX_QUERY_BYTES = 512 * 1024


class DebuggerError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


def require(condition: bool, message: str, code: str = "protocol_error") -> None:
    if not condition:
        raise DebuggerError(code, message)


def string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def integer(value: Any, minimum: int = 0, maximum: int | None = None) -> bool:
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


# key: (validator, optional). Optional values are omitted, never sent as null.
S = lambda v: string(v)
P = lambda v: integer(v, 1)
O = lambda v: integer(v, 0)
L = lambda v: integer(v, 1, 100)
METHODS = {
    "attach_debugger": {"host": (S, False), "port": (lambda v: integer(v, 1, 65535), False),
                        "connect_timeout_ms": (P, False), "stop_timeout_ms": (P, False)},
    "set_breakpoint": {"session_id": (S, False), "class_name": (S, False), "line": (P, False),
                       "source_path": (S, True), "class_loader_id": (S, True)},
    "remove_breakpoint": {"session_id": (S, False), "breakpoint_id": (S, False)},
    "get_debug_state": {"session_id": (S, False)},
    "get_stack_trace": {"session_id": (S, False), "stop_id": (S, False), "thread_id": (S, False),
                        "offset": (O, False), "limit": (L, False)},
    "get_variables": {"session_id": (S, False), "stop_id": (S, False), "offset": (O, False),
                      "limit": (L, False), "frame_id": (S, True), "object_ref": (S, True)},
    "resume_execution": {"session_id": (S, False), "stop_id": (S, False)},
    "detach_debugger": {"session_id": (S, False)},
    "shutdown": {},
}


def validate_params(method: str, params: dict) -> None:
    require(method in METHODS, "Unsupported method.", "unsupported_method")
    schema = METHODS[method]
    require(isinstance(params, dict) and params.keys() <= schema.keys(),
            "Unknown parameter fields.", "invalid_params")
    for key, (validator, optional) in schema.items():
        require((optional and key not in params) or (key in params and validator(params[key])),
                f"Invalid or missing parameter: {key}.", "invalid_params")
    if method == "get_variables":
        require(("frame_id" in params) != ("object_ref" in params),
                "Specify exactly one of frame_id or object_ref.", "invalid_params")


def encode_request(request_id: str, method: str, params: dict) -> bytes:
    validate_params(method, params)
    payload = json.dumps({"protocol_version": 1, "type": "request", "id": request_id,
                          "method": method, "params": params}, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    require(len(payload) + 1 <= MAX_LINE_BYTES, "Request exceeds 1 MiB.", "invalid_params")
    return payload + b"\n"


@dataclass(frozen=True)
class Response:
    id: str
    ok: bool
    result: dict | None
    error: dict | None
    raw: dict


@dataclass(frozen=True)
class Event:
    seq: int
    event: str
    session_id: str | None
    observed_at: str
    data: dict
    raw: dict


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field.")
        result[key] = value
    return result


def decode_message(line: bytes) -> Response | Event:
    require(len(line) <= MAX_LINE_BYTES and line.endswith(b"\n"), "Invalid JSON line size/framing.")
    try:
        raw = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite number")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DebuggerError("protocol_error", "Invalid UTF-8 JSON message.") from exc
    require(isinstance(raw, dict), "Message must be an object.")
    require(type(raw.get("protocol_version")) is int and raw["protocol_version"] == 1,
            "Helper protocol version must be 1.")
    if raw.get("type") == "response":
        require(string(raw.get("id")) and type(raw.get("ok")) is bool, "Invalid response envelope.")
        if raw["ok"]:
            require(isinstance(raw.get("result"), dict) and "error" not in raw, "Invalid success response.")
        else:
            error = raw.get("error")
            require(isinstance(error, dict) and "result" not in raw, "Invalid error response.")
            require(string(error.get("code")) and isinstance(error.get("message"), str)
                    and isinstance(error.get("details"), dict), "Invalid error fields.")
        return Response(raw["id"], raw["ok"], raw.get("result"), raw.get("error"), raw)
    require(raw.get("type") == "event", "Helper stdout accepts only responses and events.")
    require(integer(raw.get("seq"), 1) and string(raw.get("event"))
            and string(raw.get("observed_at")) and isinstance(raw.get("data"), dict), "Invalid event envelope.")
    name, sid, data = raw["event"], raw.get("session_id"), raw["data"]
    require("session_id" in raw and (sid is None if name == "ready" else string(sid)), "Invalid event session.")
    fields = {"ready": ("helper_version",), "stopped": ("stop_id", "thread_id", "reason", "suspend_policy", "expires_at"),
              "resumed": ("stop_id", "reason"), "disconnected": ("reason",),
              "breakpoint_changed": ("breakpoint_id", "status")}
    if name in fields:
        require(all(string(data.get(k)) for k in fields[name]), "Invalid event data.")
    return Event(raw["seq"], name, sid, raw["observed_at"], data, raw)


def validate_result(method: str, result: dict) -> None:
    """Reject malformed backend results before handing them to an MCP client."""
    required = {
        "attach_debugger": ("session_id", "connection_state", "capabilities"),
        "set_breakpoint": ("breakpoint_id", "status", "locations", "reason"),
        "remove_breakpoint": ("breakpoint_id", "removed"),
        "get_debug_state": ("connection_state", "active_stop", "breakpoints", "last_event_seq"),
        "get_stack_trace": ("frames", "next_offset", "truncated"),
        "get_variables": ("variables", "next_offset", "truncated", "warnings"),
        "resume_execution": ("resumed_stop_id",),
        "detach_debugger": ("detached", "released_stop_ids"), "shutdown": ("shutdown",),
    }
    require(all(k in result for k in required[method]), f"Missing result fields for {method}.")
    for k in ("session_id", "breakpoint_id", "resumed_stop_id"):
        if k in result:
            require(string(result[k]), f"Invalid {k}.")
    if "connection_state" in result:
        require(result["connection_state"] in ("connected", "disconnected", "terminated"), "Invalid connection state.")
    if "status" in result:
        require(result["status"] in ("pending", "verified", "rejected"), "Invalid breakpoint status.")
        require(result["reason"] is None or isinstance(result["reason"], str), "Invalid breakpoint reason.")
    for k in ("removed", "detached", "shutdown", "truncated"):
        if k in result:
            require(type(result[k]) is bool, f"Invalid {k}.")
    for k in ("locations", "breakpoints", "frames", "variables", "warnings", "released_stop_ids"):
        if k in result:
            require(isinstance(result[k], list), f"Invalid {k}.")
    if "capabilities" in result:
        require(isinstance(result["capabilities"], dict)
                and all(type(v) is bool for v in result["capabilities"].values()), "Invalid capabilities.")
    if "last_event_seq" in result:
        require(integer(result["last_event_seq"]), "Invalid event cursor.")
        stop = result["active_stop"]
        require(stop is None or isinstance(stop, dict), "Invalid active stop.")
        if stop is not None:
            require(all(string(stop.get(k)) for k in ("stop_id", "thread_id", "reason", "suspend_policy", "observed_at", "expires_at")),
                    "Invalid active stop fields.")
    if "next_offset" in result:
        require(result["next_offset"] is None or integer(result["next_offset"]), "Invalid page cursor.")
        require(len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_QUERY_BYTES,
                "Query result exceeds 512 KiB.")
    if "frames" in result:
        for f in result["frames"]:
            require(isinstance(f, dict) and all(string(f.get(k)) for k in ("frame_id", "class_name", "method_name"))
                    and integer(f.get("index")) and (f.get("line") is None or integer(f["line"], 1))
                    and (f.get("source_name") is None or isinstance(f["source_name"], str)), "Invalid frame.")
    if "variables" in result:
        require(all(isinstance(v, str) for v in result["warnings"]), "Invalid warnings.")
        for v in result["variables"]:
            require(isinstance(v, dict) and string(v.get("name")) and string(v.get("type_name"))
                    and v.get("kind") in ("primitive", "string", "null", "object", "array")
                    and isinstance(v.get("display_value"), str) and len(v["display_value"]) <= 4096
                    and (v.get("object_ref") is None or string(v["object_ref"]))
                    and type(v.get("has_children")) is bool and type(v.get("truncated")) is bool, "Invalid variable.")

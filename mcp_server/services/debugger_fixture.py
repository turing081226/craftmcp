"""D-owned fixture contract for B's command registry; no runtime/job implementation."""
from __future__ import annotations

import re

COMMAND_ID = "debugger.calculate.v1"
CLASS_NAME = "io.github.craftmcp.debuggersample.DamageCalculator"
SOURCE_PATH = "src/main/java/io/github/craftmcp/debuggersample/DamageCalculator.java"
BREAKPOINT_LINE = 7
TIMEOUT_SECONDS = 30
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)", re.ASCII)
ERROR_CODES = {"REMOTE_CONSOLE_REQUIRED", "INVALID_ARGS", "INVALID_INTEGER", "INTEGER_OVERFLOW"}


def command_text(arguments: dict, run_id: str) -> str:
    """Build one allowlisted command. IDs belong to the request envelope, not arguments."""
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain 1..64 ASCII letters/digits/underscore/hyphen")
    if not isinstance(arguments, dict) or arguments.keys() != {"base", "bonus"}:
        raise ValueError("arguments must contain exactly base and bonus")
    if any(type(value) is not int or not -(2**31) <= value < 2**31 for value in arguments.values()):
        raise ValueError("base and bonus must be signed 32-bit integers, excluding booleans")
    return f"craftdebugrcon calculate {run_id} {arguments['base']} {arguments['bonus']}"


def parse_output(raw_output: str, *, run_id: str, arguments: dict) -> dict:
    """Require a complete, correlated terminal record; never infer success from transport."""
    command_text(arguments, run_id)
    parsed = {"schema_version": 1, "command_id": COMMAND_ID, "run_id": run_id,
              "status": "unknown", "command_success": None, "completion": "missing",
              "started": False, "result": None, "error": None, "raw_output": raw_output}

    def invalid(code="INVALID_FIXTURE_OUTPUT"):
        parsed.update(completion="invalid", error={"code": code})
        return parsed

    if not isinstance(raw_output, str):
        return invalid()
    # A packet/line cut off before its delimiter is not a completed fixture result.
    if raw_output and not raw_output.endswith("\n"):
        return invalid("INCOMPLETE_FIXTURE_OUTPUT")
    records = []
    for line in raw_output.splitlines():
        if not line:
            continue
        tokens = line.split(" ")
        if tokens[:2] != ["CRAFTDEBUG_RCON", "v=1"]:
            return invalid()
        fields = {}
        for token in tokens[2:]:
            key, separator, value = token.partition("=")
            if not separator or not value or key in fields:
                return invalid()
            fields[key] = value
        if fields.get("run_id") != run_id:
            return invalid("RUN_ID_MISMATCH")
        records.append(fields)
    if not records:
        return parsed
    first = records[0]
    if first.get("status") == "started":
        if first.keys() != {"status", "run_id", "base", "bonus"}:
            return invalid()
        if first["base"] != str(arguments["base"]) or first["bonus"] != str(arguments["bonus"]):
            return invalid("ARGUMENT_MISMATCH")
        parsed["started"] = True
        records = records[1:]
    if not records:
        return parsed
    if len(records) != 1:
        return invalid("DUPLICATE_TERMINAL_OUTPUT")
    terminal = records[0]
    if terminal.get("end") != "1":
        return invalid()
    if terminal.get("status") == "error":
        if terminal.keys() != {"status", "run_id", "code", "end"} or terminal["code"] not in ERROR_CODES:
            return invalid()
        parsed.update(status="failed", command_success=False, completion="terminal",
                      error={"code": terminal["code"]})
    elif terminal.get("status") == "ok":
        if not parsed["started"] or terminal.keys() != {"status", "run_id", "base", "bonus", "damage", "end"}:
            return invalid()
        for key in ("base", "bonus", "damage"):
            if len(terminal[key]) > 11 or not INTEGER.fullmatch(terminal[key]) or not -(2**31) <= int(terminal[key]) < 2**31:
                return invalid()
        base, bonus, damage = (int(terminal[key]) for key in ("base", "bonus", "damage"))
        if (base, bonus) != (arguments["base"], arguments["bonus"]) or damage != base + bonus:
            return invalid("RESULT_MISMATCH")
        parsed.update(status="succeeded", command_success=True, completion="terminal",
                      result={"run_id": run_id, "base": base, "bonus": bonus, "damage": damage})
    else:
        return invalid()
    return parsed

"""Isolated real Paper/RCON/JDI + stdio MCP. Default jobs are explicitly test-only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/debugger_python"))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from probe_paper import HELPER, PLUGIN, LoggedProcess, now, sha256
from debugger_flow import DebuggerFlow
from mcp_server.adapters.rcon import execute_rcon
from mcp_server.services.debugger_fixture import command_text, parse_output


async def wait_stop(flow):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = await flow.call("get_debug_state", {"session_id": flow.session_id})
        if state["active_stop"]:
            return state["active_stop"]
        await asyncio.sleep(0.02)
    raise AssertionError("Recovery scenario did not hit its breakpoint")


async def recover(flow, record):
    for mode in ("detach", "lease"):
        await flow.attach()
        await flow.submit()
        stop = await flow.wait_stop()
        if mode == "detach":
            old_session = flow.session_id
            await flow.cleanup()
            job = await flow.wait_job()
            await flow.attach()
            await flow.call("get_debug_state", {"session_id": old_session}, error="session_not_found")
            await flow.cleanup()
        else:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = await flow.call("get_debug_state", {"session_id": flow.session_id})
                if state["active_stop"] is None:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("Stop lease did not release the JVM")
            await flow.call("get_stack_trace", {"session_id": flow.session_id, "stop_id": stop["stop_id"],
                "thread_id": stop["thread_id"], "offset": 0, "limit": 100}, error="stale_reference")
            job = await flow.wait_job()
            await flow.cleanup()
        if job["status"] != "succeeded":
            raise AssertionError(f"{mode} failed to release pending RCON: {job}")
        record("recovery_passed", {"scenario": mode, "stop": stop, "job": job})


async def terminate_suspended_target(client, process, record, run_id):
    """Only the isolated child created by this probe is terminated."""
    async def call_tool(name, params):
        return (await client.call_tool(name, params)).data

    flow = DebuggerFlow(call_tool, record, runtime_id="paper-rcon-fixture", project_id="debugger-sample", run_id=run_id)
    await flow.attach()
    await flow.submit()
    stop = await flow.wait_stop()
    process.kill()
    await asyncio.wait_for(process.wait(), 15)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = await flow.call("get_debug_state", {"session_id": flow.session_id})
        if state["connection_state"] != "connected":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("Helper did not observe target termination")
    if state["active_stop"] is not None:
        raise AssertionError("Terminated JVM retained an active stop")
    await flow.call("get_stack_trace", {"session_id": flow.session_id, "stop_id": stop["stop_id"],
        "thread_id": stop["thread_id"], "offset": 0, "limit": 100}, error="vm_disconnected")
    job = await flow.wait_job()
    if job["status"] != "unknown":
        raise AssertionError(f"Lost RCON response must leave execution unknown: {job}")
    record("recovery_passed", {"scenario": "target_terminated_while_suspended", "state": state, "job": job})


async def run(args):
    source = args.server_dir.resolve()
    if not all(p.is_file() for p in (source / "paper.jar", source / "eula.txt", PLUGIN, HELPER)):
        raise RuntimeError("Provide an existing Paper folder and build the helper/sample first")
    if not any(line.strip().lower() == "eula=true" for line in (source / "eula.txt").read_text(encoding="utf-8-sig").splitlines()):
        raise RuntimeError("The supplied server must already have an accepted EULA")
    run_id = "rcon-" + uuid.uuid4().hex
    directory = ROOT / ".cache/rcon-debugger" / run_id
    directory.mkdir(parents=True)
    report = {"schema_version": 1, "run_id": run_id, "status": "failed", "target": "isolated real Paper",
              "job_implementation": "production" if args.production_jobs else "D test-only in-memory seam",
              "verification": {"production_B_jobs": "unknown", "user_MCP_host": "unknown", "real_player": "unknown",
                               "A_runtime_lifecycle": "unknown", "C_build_deploy": "unknown",
                               "server_exit_while_suspended": "unknown", "MCP_close_while_suspended": "unknown"},
              "evidence_path": str(directory / "flow-evidence.jsonl"), "validation_server": str(directory)}
    sequence = 0

    def record(kind, payload):
        nonlocal sequence
        sequence += 1
        row = {"schema_version": 1, "run_id": run_id, "sequence": sequence, "observed_at": now(),
               "kind": kind, "verification_level": "real_Paper_RCON_JDI_stdio_MCP",
               "job_implementation": report["job_implementation"], "payload": payload}
        with (directory / "flow-evidence.jsonl").open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(row, ensure_ascii=False) + "\n")

    for name in ("paper.jar", "eula.txt"):
        shutil.copy2(source / name, directory / name)
    for name in ("cache", "libraries", "versions"):
        if (source / name).is_dir():
            shutil.copytree(source / name, directory / name)
    (directory / "plugins").mkdir()
    deployed = directory / "plugins" / PLUGIN.name
    shutil.copy2(PLUGIN, deployed)
    with socket.socket() as rcon_reservation, socket.socket() as jdwp_reservation:
        rcon_reservation.bind(("127.0.0.1", 0))
        jdwp_reservation.bind(("127.0.0.1", 0))
        rcon_port = rcon_reservation.getsockname()[1]
        requested_jdwp_port = jdwp_reservation.getsockname()[1]
    password = secrets.token_hex(24)
    properties = {"server-ip": "127.0.0.1", "server-port": "0", "enable-rcon": "true", "rcon.port": rcon_port,
                  "rcon.password": password, "broadcast-rcon-to-ops": "false", "online-mode": "false",
                  "level-name": "debugger-world", "level-seed": "1234", "gamemode": "creative", "difficulty": "peaceful",
                  "spawn-monsters": "false", "spawn-animals": "false", "view-distance": "2", "simulation-distance": "2",
                  "max-players": "1", "spawn-protection": "0", "allow-nether": "false", "generate-structures": "false",
                  "max-tick-time": "60000"}
    (directory / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in properties.items()), encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(args.java, "-Xms256M", "-Xmx1G", "-Dfile.encoding=UTF-8",
        "-Dterminal.jline=false", "-Dterminal.ansi=false",
        f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:{requested_jdwp_port}", "-jar", str(directory / "paper.jar"),
        "--nogui", cwd=directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=1024 * 1024, creationflags=flags)
    paper = LoggedProcess(process, directory / "paper-console.jsonl")
    pending_rcon = None
    try:
        jdwp_line = await paper.wait_line("Listening for transport dt_socket at address:", timeout=30)
        jdwp_port = int(re.search(r"address: (\d+)", jdwp_line).group(1))
        if jdwp_port != requested_jdwp_port:
            raise AssertionError("Observed JDWP port differs from the isolated runtime configuration")
        await paper.wait_line("Done (", timeout=args.start_timeout)
        # Listener startup can precede Done; wait_line has already consumed those lines.
        if not any("RCON running on" in row["text"] for row in paper.records):
            await paper.wait_line("RCON running on", timeout=15)
        version = next(r["text"] for r in paper.records if "This server is running Paper version" in r["text"])
        if "MC: 1.20.1" not in version:
            raise AssertionError("Expected Paper 1.20.1")
        report.update(server_version_observed=version, paper_sha256=sha256(source / "paper.jar"),
                      copied_paper_sha256=sha256(directory / "paper.jar"), sample_sha256=sha256(PLUGIN),
                      deployed_sample_sha256=sha256(deployed), runtime_boot_id=directory.name)
        record("runtime_observed", {k: v for k, v in report.items() if k.endswith("sha256") or k in {"runtime_boot_id", "server_version_observed"}})

        async def rcon(text):
            return await asyncio.to_thread(execute_rcon, text, password, port=rcon_port, timeout=30)

        # Preserve the old local-console behavior; test real remote rejection as well.
        await paper.command("craftdebug calculate local-baseline 7 3")
        await paper.wait_line("CRAFTDEBUG status=ok run_id=local-baseline damage=10", timeout=15)
        rejected = await rcon("craftdebug calculate old-remote 7 3")
        if "LOCAL_CONSOLE_REQUIRED" not in rejected:
            raise AssertionError("Legacy command accepted RCON")
        await paper.command(f"craftdebugrcon calculate {run_id} 7 3")
        await paper.wait_line("code=REMOTE_CONSOLE_REQUIRED", timeout=15)
        raw = await rcon(command_text({"base": 7, "bonus": 3}, run_id))
        if parse_output(raw, run_id=run_id, arguments={"base": 7, "bonus": 3})["status"] != "succeeded":
            raise AssertionError(f"Real RCON fixture baseline failed: {raw!r}")
        overflow = await rcon(command_text({"base": 2147483647, "bonus": 1}, run_id))
        if parse_output(overflow, run_id=run_id, arguments={"base": 2147483647, "bonus": 1})["error"] != {"code": "INTEGER_OVERFLOW"}:
            raise AssertionError("Real RCON overflow was not a terminal failure")
        record("sender_and_baseline_passed", {"legacy_rcon_rejection": rejected, "rcon_output": raw, "overflow": overflow,
               "local_console_legacy": "passed", "local_console_new_command_rejection": "passed", "real_player_sender": "unknown"})
        config_path = directory / "debugger.json"
        config_path.write_text(json.dumps({"helper_jar": str(HELPER), "java_executable": args.java,
            "evidence_dir": "data/debugger", "stop_timeout_ms": 10000,
            "projects": {"debugger-sample": {"root": str(ROOT / "plugin_template/debugger_sample"), "artifact_jar": str(deployed)}},
            "runtimes": {"paper-rcon-fixture": {"project_id": "debugger-sample", "host": "127.0.0.1", "port": jdwp_port,
                "jar": str(directory / "paper.jar"), "boot_id": directory.name, "server_version": version}}}, indent=2), encoding="utf-8")
        env = {"DEBUGGER_CONFIG": str(config_path), "MC_RCON_PASSWORD": password, "MC_RCON_PORT": str(rcon_port),
               "MC_RCON_TIMEOUT": "30", "MC_SERVER_DIR": str(directory)}
        command_args = (["-m", "mcp_server.server"] if args.production_jobs else
                        [str(ROOT / "tests/integration/fixture_app.py"), "--config", str(config_path), "--runtime", "paper-rcon-fixture"])
        transport = StdioTransport(sys.executable, command_args, cwd=str(ROOT), env=env, keep_alive=False)
        async with Client(transport, timeout=15) as client:
            names = {tool.name for tool in await client.list_tools()}
            required = {"execute_command", "get_job", "attach_debugger", "set_breakpoint", "get_debug_state",
                        "get_stack_trace", "get_variables", "resume_execution", "remove_breakpoint", "detach_debugger"}
            if required - names:
                raise RuntimeError(f"MCP app missing required tools: {sorted(required - names)}")

            async def call_tool(name, params):
                return (await client.call_tool(name, params)).data

            flow = DebuggerFlow(call_tool, record, runtime_id="paper-rcon-fixture", project_id="debugger-sample", run_id=run_id)
            report["flow"] = await flow.run()
            report["verification"]["production_B_jobs"] = "passed" if args.production_jobs else "unknown"
            if args.recoveries:
                await recover(flow, record)
                # Independent RCON observer survives host shutdown, proving the held JVM is released.
                await flow.attach()
                pending_rcon = asyncio.create_task(rcon(command_text({"base": 7, "bonus": 3}, run_id)))
                stopped = await wait_stop(flow)
                record("MCP_closing_while_suspended", {"session_id": flow.session_id, "stop_id": stopped["stop_id"],
                                                       "transport": "independent real RCON observer"})
                # Intentionally leave stop held: the app lifespan must release before exiting.
        if pending_rcon:
            released_output = await asyncio.wait_for(asyncio.shield(pending_rcon), 15)
            if parse_output(released_output, run_id=run_id, arguments={"base": 7, "bonus": 3})["status"] != "succeeded":
                raise AssertionError("MCP shutdown did not complete the held RCON command")
            report["verification"]["MCP_close_while_suspended"] = "passed"
            record("recovery_passed", {"scenario": "MCP_client_close_while_suspended", "raw_output": released_output})
        if args.terminate_target:
            # Fresh MCP process: old service references cannot be reused after host exit.
            async with Client(StdioTransport(sys.executable, command_args, cwd=str(ROOT), env=env, keep_alive=False), timeout=15) as client:
                await terminate_suspended_target(client, process, record, run_id)
            report["verification"]["server_exit_while_suspended"] = "passed"
            report["shutdown"] = "expected_target_termination"
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        record("probe_failed", {"error": report["error"]})
    finally:
        if pending_rcon:
            await asyncio.gather(pending_rcon, return_exceptions=True)
        if process.returncode is None:
            try:
                await paper.command("stop")
                await asyncio.wait_for(process.wait(), 30)
            except (OSError, asyncio.TimeoutError):
                process.kill()
                await process.wait()
                report["shutdown"] = "forced"
        await paper.drain()
        report.setdefault("shutdown", "normal" if process.returncode == 0 else "failed")
        if report["shutdown"] not in {"normal", "expected_target_termination"}:
            report["status"] = "failed"
        record("probe_finished", {"status": report["status"], "shutdown": report["shutdown"], "verification": report["verification"]})
        (directory / "validation-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "summary": str(directory / "validation-summary.json"),
                      "job_implementation": report["job_implementation"], "error": report.get("error")}, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--start-timeout", type=float, default=120)
    parser.add_argument("--production-jobs", action="store_true", help="Require B's tools in the actual shared app; never fall back to test jobs")
    parser.add_argument("--recoveries", action="store_true", help="Also test detach, old session, lease and MCP close on held Paper")
    parser.add_argument("--terminate-target", action="store_true", help="Finally terminate ONLY the isolated child while suspended; require unknown job + vm_disconnected")
    asyncio.run(run(parser.parse_args()))

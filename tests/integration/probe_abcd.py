"""Real shared stdio MCP: A ownership, B jobs, C build/deploy/rollback, D JDI.

Uses an isolated copy of already accepted Paper files. Never adopts or stops the
source server. Evidence is local under .cache/ab-*/summary.json.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/runtime"))
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from probe_paper_lifecycle import prepare
from debugger_flow import DebuggerFlow

SOURCE = '''package local;
import org.bukkit.plugin.java.JavaPlugin;
public final class Probe extends JavaPlugin {
    @Override public void onEnable() { BODY }
}
'''


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run(args):
    source = args.server_dir.resolve(strict=True)
    root = ROOT / ".cache" / ("ab-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    original = {name: digest(source / name) for name in ("paper.jar", "eula.txt", "server.properties")}
    summary = {"status": "failed", "scope": "real shared stdio MCP, isolated managed Paper", "root": str(root)}
    records = root / "flow-evidence.jsonl"
    def record(kind, payload):
        with records.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"at": time.time(), "kind": kind, "payload": payload}, ensure_ascii=False) + "\n")
        (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence: " + str(root), flush=True)
    directory, artifact = await asyncio.to_thread(prepare, source, root, args.java)
    port, jdwp = free_port(), free_port()
    password = secrets.token_urlsafe(24)
    properties = directory / "server.properties"
    text = properties.read_text(encoding="utf-8").replace("enable-rcon=false", "enable-rcon=true")
    properties.write_text(text + f"rcon.port={port}\nrcon.password={password}\n", encoding="utf-8")
    config = root / "debugger.json"
    config.write_text(json.dumps({
        "helper_jar": str(ROOT / "debugger_helper/build/libs/craftmcp-debugger-helper.jar"),
        "java_executable": args.java, "evidence_dir": str(root / "debugger"),
        "request_timeout": 15, "stop_timeout_ms": 30000,
        "projects": {"debugger-sample": {"root": str(ROOT / "plugin_template/debugger_sample"), "artifact_jar": str(artifact)}},
        "runtimes": {"paper-abcd": {"project_id": "debugger-sample", "host": "127.0.0.1", "port": jdwp,
            "jar": str(directory / "paper.jar")}}}), encoding="utf-8")
    runtime_config = root / "runtime.json"
    runtime_config.write_text(json.dumps({"runtimes": {"paper-abcd": {"managed": True,
        "rcon": {"host": "127.0.0.1", "port": port, "password_env": "ABCD_RCON_PASSWORD"}}}}), encoding="utf-8")
    env = {"DEBUGGER_CONFIG": str(config), "CRAFTMCP_RUNTIME_CONFIG": str(runtime_config),
           "ABCD_RCON_PASSWORD": password, "MC_RUNTIME_ID": "paper-abcd", "MC_SERVER_DIR": str(directory),
           "MC_RCON_PASSWORD": password, "MC_RCON_PORT": str(port), "MC_RCON_TIMEOUT": "30"}
    # Explicit per-runtime credentials use the legacy name when the MC_* binding
    # is present, to verify those settings refer to the exact same endpoint.
    data = json.loads(runtime_config.read_text())
    data["runtimes"]["paper-abcd"]["rcon"]["password_env"] = "MC_RCON_PASSWORD"
    runtime_config.write_text(json.dumps(data), encoding="utf-8")
    transport = StdioTransport(sys.executable, ["-m", "mcp_server.server"], cwd=str(ROOT), env=env,
                               keep_alive=False, log_file=root / "mcp-stderr.log")
    async with Client(transport, timeout=20, init_timeout=40) as client:
        async def raw_call(name, params):
            response = await client.call_tool(name, params)
            data = response.data
            if isinstance(data, str):
                data = json.loads(data)
            record("tool", {"name": name, "arguments": params, "response": data})
            return data
        async def call(name, params):
            data = await raw_call(name, params)
            if data.get("ok") is not True:
                raise AssertionError(f"{name}: {data}")
            return data.get("result", data)
        async def wait_job(job_id):
            deadline = time.monotonic() + 310
            while time.monotonic() < deadline:
                job = await call("get_job", {"job_id": job_id})
                if job["status"] in {"succeeded", "failed", "unknown", "cancelled"}:
                    return job
                await asyncio.sleep(0.1)
            raise AssertionError("Job exceeded probe deadline")
        async def job(name, params, expected="succeeded"):
            submitted = await call(name, {"request_id": uuid.uuid4().hex, "run_id": "abcd-probe", **params})
            final = await wait_job(submitted["job_id"])
            record("job_completed", final)
            assert final["status"] == expected, final
            return final
        names = {tool.name for tool in await client.list_tools()}
        assert len(names) == 29, names
        assert {"list_game_clients", "capture_game_view", "get_game_capture",
                "record_visual_assertion", "get_visual_run_evidence"} <= names, names
        summary["tools"] = sorted(names)
        listing = await call("list_runtimes", {})
        assert listing["runtimes"][0]["runtime_id"] == "paper-abcd"
        first = await job("start_server", {"runtime_id": "paper-abcd", "timeout_seconds": 150})
        summary["initial_boot"] = json.loads(first["raw_output"])
        print("Owned Paper started; checking JDI and command flow.", flush=True)
        flow = DebuggerFlow(raw_call, record, runtime_id="paper-abcd", project_id="debugger-sample", run_id="abcd-debug")
        summary["debugger_flow"] = await flow.run()
        builds = []
        if args.build_ids:
            builds = args.build_ids
        else:
            for version in ("1.0", "2.0", "broken"):
                body = 'throw new IllegalStateException("intentional integration failure");' if version == "broken" else 'int marker = 7; getLogger().info("integration=" + marker);'
                built = await job("build_plugin", {"plugin_name": "IntegrationProbe", "main_class": "local.Probe",
                    "source_files": {"local/Probe.java": SOURCE.replace("BODY", body)},
                    "plugin_manifest": {"version": version}, "resource_files": {"config.yml": "message: 통합 검증\n"},
                    "timeout_seconds": 180, "offline": True})
                builds.append(built["parsed_output"]["build_id"])
                print("Built " + version + ": " + builds[-1], flush=True)
        summary["build_ids"] = builds
        deployed = await job("deploy_plugin", {"runtime_id": "paper-abcd", "build_id": builds[0]})
        summary["deploy_v1"] = deployed
        # C must detach D before A stops the JVM, while the original B job waits.
        held = DebuggerFlow(raw_call, record, runtime_id="paper-abcd", project_id="debugger-sample", run_id="abcd-held")
        await held.attach()
        await held.submit()
        stop = await held.wait_stop()
        state = await call("get_runtime_status", {"runtime_id": "paper-abcd"})
        assert state["observations"]["suspension"]["value"]["stop_id"] == stop["stop_id"]
        summary["held_stop_before_deploy"] = stop
        deployed = await job("deploy_plugin", {"runtime_id": "paper-abcd", "build_id": builds[1]})
        summary["deploy_v2_while_suspended"] = deployed
        original_job = await held.wait_job()
        assert original_job["status"] in {"succeeded", "unknown"}, original_job
        summary["held_command_after_restart"] = original_job
        old = await raw_call("get_stack_trace", {"session_id": held.session_id, "stop_id": stop["stop_id"], "thread_id": stop["thread_id"]})
        assert old["ok"] is False and old["error"]["code"] in {"vm_disconnected", "session_not_found"}, old
        print("Normal deployments and held-stop release passed; checking failures/rollback.", flush=True)
        failed = await job("deploy_plugin", {"runtime_id": "paper-abcd", "build_id": builds[2]}, expected="failed")
        summary["disabled_plugin_failure"] = failed
        recovered = await job("rollback_plugin", {"runtime_id": "paper-abcd", "deployment_id": failed["parsed_output"]["deployment_id"]})
        summary["rollback_after_enable_failure"] = recovered
        assert recovered["parsed_output"]["version"] == "2.0"
        # A real startup failure in this stopped, disposable copy only.
        await job("stop_server", {"runtime_id": "paper-abcd"})
        missing = directory / "paper.jar.disabled"
        (directory / "paper.jar").rename(missing)
        try:
            startup_failed = await job("rollback_plugin", {"runtime_id": "paper-abcd",
                "deployment_id": deployed["parsed_output"]["deployment_id"]}, expected="unknown")
            summary["startup_failure"] = startup_failed
        finally:
            missing.rename(directory / "paper.jar")
        recovered = await job("rollback_plugin", {"runtime_id": "paper-abcd",
            "deployment_id": startup_failed["parsed_output"]["deployment_id"]})
        summary["rollback_after_startup_failure"] = recovered
        assert recovered["parsed_output"]["version"] == "2.0"
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            api.CreateFileW.restype = wintypes.HANDLE
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            installed = directory / "plugins/IntegrationProbe.jar"
            original_hash = digest(installed)
            reader = api.CreateFileW(str(installed), 0x80000000, 3, None, 3, 0, None)
            assert reader != ctypes.c_void_p(-1).value
            try:
                locked = await job("deploy_plugin", {"runtime_id": "paper-abcd", "build_id": builds[0],
                    "timeout_seconds": 40}, expected="failed")
                summary["persistent_windows_lock"] = locked
                assert locked["parsed_output"]["installation_preserved"] is True, locked
                assert locked["parsed_output"]["files_modified"] is False, locked
                assert digest(installed) == original_hash
            finally:
                api.CloseHandle(reader)
            await job("start_server", {"runtime_id": "paper-abcd", "timeout_seconds": 150})
            status = await call("get_plugin_status", {"runtime_id": "paper-abcd", "plugin_name": "IntegrationProbe"})
            assert status["version"] == "2.0" and status["enabled"] is True, status
            summary["explicit_restart_after_lock_failure"] = status
        # Shared-app shutdown must keep debugger release alive until owned exit.
        held = DebuggerFlow(raw_call, record, runtime_id="paper-abcd", project_id="debugger-sample", run_id="abcd-close")
        await held.attach()
        await held.submit()
        summary["held_stop_before_mcp_close"] = await held.wait_stop()
        summary["pre_close_runtime"] = await call("get_runtime_status", {"runtime_id": "paper-abcd"})
        print("Closing shared MCP with an owned Paper held at a breakpoint.", flush=True)
    final_state = json.loads((directory / ".craftmcp/state.json").read_text(encoding="utf-8"))
    assert final_state["phase"] == "stopped" and final_state["pid"] is None, final_state
    assert final_state.get("exit_code") == 0, final_state
    summary["shutdown"] = final_state
    summary["source_files_unchanged"] = {name: digest(source / name) == value for name, value in original.items()}
    assert all(summary["source_files_unchanged"].values())
    summary["status"] = "passed"
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "summary": str(root / "summary.json"), "build_ids": builds}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--build-ids", nargs=3)
    asyncio.run(run(parser.parse_args()))

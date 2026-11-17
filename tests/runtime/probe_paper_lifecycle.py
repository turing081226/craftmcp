"""Opt-in: real Paper/JDI lifecycle jobs in a copy, preserving the source server."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mcp_server.models.contracts import Endpoint, RuntimeTarget
from mcp_server.services.debugger import DebuggerConfig, DebuggerService, Project, Runtime
from mcp_server.services.jobs import JobManager
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService
from mcp_server.services.runtime_jobs import RuntimeJobRouter, RuntimeJobs
from mcp_server.services.runtime_lifecycle import RuntimeLifecycle, paper_launch


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def prepare(source: Path, root: Path, java: str):
    if not (source / "paper.jar").is_file():
        raise RuntimeError("The supplied source must contain paper.jar.")
    if not any(line.strip().lower() == "eula=true" for line in
               (source / "eula.txt").read_text(encoding="utf-8-sig").splitlines()):
        raise RuntimeError("The exact source Paper binary must already have an accepted EULA.")
    directory = root / "server"
    directory.mkdir(parents=True)
    for name in ("paper.jar", "eula.txt"):
        shutil.copy2(source / name, directory / name)
    for name in ("cache", "libraries", "versions"):
        if (source / name).is_dir():
            shutil.copytree(source / name, directory / name)
    properties = {"server-ip": "127.0.0.1", "server-port": "0", "enable-rcon": "false",
                  "online-mode": "false", "level-name": "lifecycle-test", "level-seed": "1234",
                  "level-type": "minecraft:flat", "view-distance": "2", "simulation-distance": "2",
                  "generator-settings": json.dumps({"layers": [{"height": 1, "block": "minecraft:bedrock"},
                                                               {"height": 2, "block": "minecraft:dirt"},
                                                               {"height": 1, "block": "minecraft:grass_block"}],
                                                    "biome": "minecraft:plains"}),
                  "allow-nether": "false", "generate-structures": "false", "max-tick-time": "60000"}
    (directory / "server.properties").write_text(
        "".join(f"{key}={value}\n" for key, value in properties.items()), encoding="utf-8")
    # Compile the existing D sample into the isolated copy; no Gradle or network.
    sample = ROOT / "plugin_template/debugger_sample"
    classes = root / "classes"
    classes.mkdir()
    java_path = Path(shutil.which(java) or java)
    javac = java_path.with_name("javac.exe" if os.name == "nt" else "javac")
    jars = list((directory / "libraries").rglob("*.jar"))
    classpath = os.pathsep.join(path.as_posix() for path in jars)
    arguments = ["-g", "-encoding", "UTF-8", "-cp", classpath, "-d", classes.as_posix(),
                 *(path.as_posix() for path in (sample / "src/main/java").rglob("*.java"))]
    response = root / "javac.args"
    response.write_text("\n".join('"' + item.replace('"', '\\"') + '"' for item in arguments), encoding="utf-8")
    completed = subprocess.run([str(javac), "@" + str(response)], capture_output=True, timeout=60,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    (root / "javac.log").write_bytes(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError(f"Local sample compilation failed; inspect {root / 'javac.log'}")
    plugins = directory / "plugins"
    plugins.mkdir()
    artifact = plugins / "lifecycle-sample.jar"
    with zipfile.ZipFile(artifact, "w") as archive:
        for path in classes.rglob("*.class"):
            archive.write(path, path.relative_to(classes).as_posix())
        archive.write(sample / "src/main/resources/plugin.yml", "plugin.yml")
    return directory, artifact


async def run(source: Path, java: str):
    source = source.resolve(strict=True)
    # Keep copied Maven paths below Windows' legacy MAX_PATH limit.
    root = ROOT / ".cache" / ("rl-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    report = {"status": "failed", "simulated": False, "source_server_changed": False,
              "scope": "isolated Paper, B lifecycle jobs, real JDI suspend/release; no shared app or IDE host"}
    directory, artifact = await asyncio.to_thread(prepare, source, root, java)
    report.update(source_paper_sha256=sha256(source / "paper.jar"), paper_sha256=sha256(directory / "paper.jar"),
                  plugin_sha256=sha256(artifact))
    print(f"Isolated Paper evidence: {root}", flush=True)
    target = RuntimeTarget("paper-lifecycle", "debugger-sample", ROOT / "plugin_template/debugger_sample",
                           directory, directory / "paper.jar", jdwp=Endpoint("127.0.0.1", free_port()), managed=True)
    debugger = DebuggerService(DebuggerConfig(
        workspace=ROOT, helper_jar=ROOT / "debugger_helper/build/libs/craftmcp-debugger-helper.jar",
        java_executable=java, projects={target.project_id: Project(target.project_dir, artifact)},
        runtimes={target.runtime_id: Runtime(target.project_id, target.jdwp.host, target.jdwp.port, target.server_jar)},
        evidence_dir=root / "debugger", ready_timeout=10, request_timeout=15, stop_timeout_ms=30000))
    session_id = None
    released = []

    async def release(runtime_id):
        nonlocal session_id
        if runtime_id != target.runtime_id:
            raise RuntimeError("Release was routed to another runtime.")
        if session_id:
            await debugger.call("detach_debugger", {"session_id": session_id})
            session_id = None
            released.append(runtime_id)

    registry = RuntimeRegistry([target])
    lifecycle = RuntimeLifecycle(registry, {target.runtime_id: paper_launch(target, java=java)}, release_runtime=release)
    runtime = RuntimeService(registry, lifecycle=lifecycle)

    async def no_rcon(command, timeout):
        raise RuntimeError("This lifecycle probe never sends RCON commands.")

    router = RuntimeJobRouter(runtime, no_rcon)
    manager = JobManager(router.execute, router.commands())
    jobs = RuntimeJobs(runtime, manager)

    async def job(action, request_id):
        submitted = await jobs.submit(action, target.runtime_id, request_id=request_id, timeout_seconds=150)
        deadline = time.monotonic() + 160
        while True:
            item = await manager.get(submitted["job_id"])
            if item.status in {"succeeded", "failed", "unknown", "cancelled"}:
                if item.status != "succeeded":
                    raise RuntimeError(f"{action}: {item.status}: {item.errors}")
                return item, json.loads(item.raw_output)
            if time.monotonic() >= deadline:
                raise RuntimeError("Lifecycle job exceeded the probe deadline.")
            await asyncio.sleep(0.1)

    try:
        first_job, first = await job("start_server", "start-1")
        print("Paper ready; attaching the real debugger.", flush=True)
        report["first_boot"] = first
        duplicate = await jobs.submit("start_server", target.runtime_id, request_id="start-1", timeout_seconds=150)
        assert duplicate["job_id"] == first_job.job_id
        attached = await debugger.attach_debugger(target.runtime_id, target.project_id)
        session_id = attached["session_id"]
        await debugger.call("set_breakpoint", {"session_id": session_id,
                            "class_name": "io.github.craftmcp.debuggersample.DamageCalculator", "line": 7,
                            "source_path": str(target.project_dir / "src/main/java/io/github/craftmcp/debuggersample/DamageCalculator.java")})
        child = lifecycle._children[target.runtime_id]
        child.process.stdin.write(b"craftdebug calculate lifecycle-1 7 3\n")
        await child.process.stdin.drain()
        deadline = time.monotonic() + 20
        while True:
            state = await debugger.call("get_debug_state", {"session_id": session_id})
            if state.get("active_stop"):
                report["active_stop"] = state["active_stop"]
                break
            if time.monotonic() > deadline:
                raise RuntimeError("The real Paper fixture did not reach its breakpoint.")
            await asyncio.sleep(0.05)
        print("Real Paper breakpoint held; stopping through the release hook.", flush=True)
        _, stopped = await job("stop_server", "stop-1")
        assert stopped["exit_confirmed"] and stopped["exit_code"] == 0
        assert released == [target.runtime_id]
        report.update(suspended_stop=stopped, released_runtimes=released.copy())
        _, second = await job("start_server", "start-2")
        assert second["boot_id"] != first["boot_id"]
        _, stopped_again = await job("stop_server", "stop-2")
        assert stopped_again["exit_confirmed"] and stopped_again["exit_code"] == 0
        report.update(second_boot=second, normal_stop=stopped_again, status="passed")
    finally:
        await manager.close()
        cleanup = await runtime.close(timeout_seconds=30)
        await debugger.close()
        report["cleanup"] = cleanup
        # Only this disposable copy's directly owned handle may need forced cleanup.
        for child in lifecycle._children.values():
            if child.process.returncode is None:
                assert child.target.server_dir.is_relative_to(root)
                child.process.kill()
                await child.process.wait()
                report.update(status="failed", forced_fixture_cleanup=True)
            if child.watcher:
                await child.watcher
        report["source_jar_unchanged"] = sha256(source / "paper.jar") == report["source_paper_sha256"]
        report["shutdown"] = "normal" if cleanup["ok"] else "unconfirmed"
        (root / "lifecycle-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "shutdown": report["shutdown"], "evidence_dir": str(root)}), flush=True)
    if report["status"] != "passed":
        raise RuntimeError("The lifecycle probe did not pass.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    asyncio.run(run(args.server_dir, args.java))

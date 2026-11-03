"""Opt-in real Paper/MCP deployment validation in a fresh, disposable server directory."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp_server.adapters.rcon import execute_rcon
from mcp_server.paper_runner import read_state
from mcp_server.services.file_gate import FileGate
from mcp_server.services.deployment import digest


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def main(source: Path):
    source = source.resolve()
    if not any(line.strip().lower() == "eula=true" for line in (source / "eula.txt").read_text().splitlines()):
        raise RuntimeError("Use an existing server with an accepted EULA for this Paper binary.")
    artifact = ROOT / "plugin_template/debugger_sample/build/libs/craftmcp-debugger-sample.jar"
    with zipfile.ZipFile(artifact) as jar:
        name = yaml.safe_load(jar.read("plugin.yml"))["name"]
    root = ROOT / ".cache/paper-managed" / uuid.uuid4().hex
    directory = root / "server"
    directory.mkdir(parents=True)
    for filename in ("paper.jar", "eula.txt"):
        shutil.copy2(source / filename, directory / filename)
    for filename in ("cache", "libraries", "versions"):
        if (source / filename).is_dir():
            shutil.copytree(source / filename, directory / filename)
    target = directory / "plugins" / f"{name}.jar"
    target.parent.mkdir()
    shutil.copy2(artifact, target)
    project = root / "project"
    built = project / "data/build_workspace" / name / "build/libs/sample.jar"
    built.parent.mkdir(parents=True)
    shutil.copy2(artifact, built)
    rcon_port, jdwp_port = free_port(), free_port()
    password = uuid.uuid4().hex
    properties = {"server-ip": "127.0.0.1", "server-port": "0", "enable-rcon": "true", "rcon.port": str(rcon_port),
                  "rcon.password": password, "online-mode": "false", "level-name": "qa-world", "level-seed": "1234",
                  "view-distance": "2", "simulation-distance": "2", "allow-nether": "false", "generate-structures": "false"}
    (directory / "server.properties").write_text("".join(f"{key}={value}\n" for key, value in properties.items()))
    java = ["java", "-Xms256M", "-Xmx1G", "-Dfile.encoding=UTF-8", "-Dterminal.jline=false", "-Dterminal.ansi=false",
            f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:{jdwp_port}", "-jar", "paper.jar", "--nogui"]
    stop_runner = root / "stop-runner"
    command = [sys.executable, "-u", "-c",
               "import sys,json; from pathlib import Path; from mcp_server.paper_runner import run; "
               "run(Path(sys.argv[1]), json.loads(sys.argv[2]), restart_delay=0.1, shutdown=lambda: Path(sys.argv[3]).exists())",
               str(directory), json.dumps(java), str(stop_runner)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    report = {"status": "failed", "server_dir": str(directory), "simulated": False, "original_server_changed": False}
    report.update(paper_sha256=digest(directory / "paper.jar"), source_paper_sha256=digest(source / "paper.jar"))
    console = (root / "runner-console.log").open("wb")
    runner = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT,
                              creationflags=flags)

    async def boot():
        until = time.monotonic() + 150
        while time.monotonic() < until:
            try:
                state = read_state(directory)
                log = directory / ".craftmcp/boots" / f"{state['boot_id']}.log"
                if state["phase"] == "running" and log.is_file() and re.search(r'\bDone \([^)]+\)!', log.read_text(encoding="utf-8", errors="replace")):
                    return state
            except RuntimeError:
                pass
            if runner.poll() is not None:
                raise RuntimeError("Owned Paper runner exited; inspect runner-console.log.")
            await asyncio.sleep(0.2)
        raise TimeoutError("Paper startup timed out.")

    try:
        old = await boot()
        config = root / "debugger.json"
        config.write_text(json.dumps({"helper_jar": "debugger_helper/build/libs/craftmcp-debugger-helper.jar",
            "projects": {"sample": {"root": "plugin_template/debugger_sample", "artifact_jar": str(built)}},
            "runtimes": {"managed": {"project_id": "sample", "host": "127.0.0.1", "port": jdwp_port, "jar": str(directory / "paper.jar")}}}))
        transport = StdioTransport(command=sys.executable,
            args=["-c", "import sys; from mcp_server import server; server.__file__=sys.argv[1]; server.mcp.run(transport='stdio',show_banner=False)", str(project / "mcp_server/server.py")],
            cwd=str(ROOT), keep_alive=False, log_file=root / "mcp-stderr.log",
            env={**os.environ, "MC_SERVER_DIR": str(directory), "MC_RCON_PORT": str(rcon_port),
                 "MC_RCON_PASSWORD": password, "MC_RCON_TIMEOUT": "10", "DEBUGGER_CONFIG": str(config)})
        async with Client(transport, timeout=160, init_timeout=30) as client:
            listed = await client.call_tool("list_players", {})
            report["list_players"] = listed.content[0].text
            attached = (await client.call_tool("attach_debugger", {"runtime_id": "managed", "project_id": "sample"})).data
            if not attached["ok"]:
                raise RuntimeError(str(attached))
            deployment = json.loads((await client.call_tool("deploy_plugin", {"plugin_name": name})).content[0].text)
            report["deployment"] = deployment
            if not deployment["ok"] or deployment["boot_id"] == old["boot_id"]:
                raise RuntimeError("Real Paper deployment failed verification.")
            reattached = (await client.call_tool("attach_debugger", {"runtime_id": "managed", "project_id": "sample"})).data
            if not reattached["ok"]:
                raise RuntimeError("Cannot reattach to the restarted Paper JVM.")
            if read_state(directory)["command"] != old["command"]:
                raise RuntimeError("The Java/JDWP startup profile changed during deployment.")
            report.update(status="passed", debugger_reattach=True, previous_boot_id=old["boot_id"], boot_id=deployment["boot_id"])
    finally:
        try:
            with FileGate(directory / ".craftmcp/deploy.lock"):
                try:
                    await asyncio.to_thread(execute_rcon, "stop", password, port=rcon_port, timeout=5)
                except OSError:
                    pass
                until = time.monotonic() + 45
                while time.monotonic() < until:
                    try:
                        if read_state(directory)["phase"] == "stopped":
                            report["shutdown"] = "normal"
                            break
                    except RuntimeError:
                        break
                    await asyncio.sleep(0.2)
                stop_runner.touch()
                await asyncio.to_thread(runner.wait, 45)
        finally:
            stop_runner.touch()
            if runner.poll() is None:
                runner.kill()
                runner.wait()
            console.close()
            (root / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "shutdown": report.get("shutdown"), "summary": str(root / "summary.json")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    asyncio.run(main(parser.parse_args().server_dir))

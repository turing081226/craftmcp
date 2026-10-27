"""Validate a supplied Paper binary in an isolated workspace with the real stdio MCP client."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin_template/debugger_sample/build/libs/craftmcp-debugger-sample.jar"
HELPER = ROOT / "debugger_helper/build/libs/craftmcp-debugger-helper.jar"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LoggedProcess:
    def __init__(self, process, log_path):
        self.process = process
        self.log_path = log_path
        self.queue = asyncio.Queue()
        self.records = []
        self.ended_streams = 0
        self.tasks = [asyncio.create_task(self.read(process.stdout, "stdout")),
                      asyncio.create_task(self.read(process.stderr, "stderr"))]

    def record(self, stream, text):
        item = {"recorded_at": now(), "stream": stream, "text": text}
        self.records.append(item)
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(item, ensure_ascii=False) + "\n")

    async def read(self, source, name):
        while line := await source.readline():
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            self.record(name, text)
            await self.queue.put(text)
        self.ended_streams += 1
        await self.queue.put(None)

    async def wait_line(self, needle, timeout=90):
        async def wait():
            while True:
                line = await self.queue.get()
                if line is None and self.ended_streams == 2:
                    raise RuntimeError(f"Process exited before {needle}; inspect {self.log_path}")
                if line is not None and needle in line:
                    return line
        return await asyncio.wait_for(wait(), timeout)

    async def command(self, command):
        self.record("stdin", command)
        self.process.stdin.write((command + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def drain(self):
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def run(args):
    source = args.server_dir.resolve()
    if not (source / "paper.jar").is_file() or not PLUGIN.is_file() or not HELPER.is_file():
        raise RuntimeError("Build the helper/sample and provide a server folder containing paper.jar.")
    # Reuse the user's existing agreement for this exact Paper binary; never invent an EULA acceptance.
    accepted = any(line.strip().lower() == "eula=true" for line in (source / "eula.txt").read_text(encoding="utf-8-sig").splitlines())
    if not accepted:
        raise RuntimeError("The supplied server must already have an accepted EULA.")
    directory = ROOT / ".cache/paper-debugger" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    shutil.copy2(source / "paper.jar", directory / "paper.jar")
    shutil.copy2(source / "eula.txt", directory / "eula.txt")
    for name in ("cache", "libraries", "versions"):
        if (source / name).is_dir():
            shutil.copytree(source / name, directory / name)
    (directory / "plugins").mkdir()
    deployed = directory / "plugins" / PLUGIN.name
    shutil.copy2(PLUGIN, deployed)
    properties = {"server-ip": "127.0.0.1", "server-port": "0", "enable-rcon": "false", "online-mode": "false",
                  "level-name": "debugger-world", "level-seed": "1234", "gamemode": "creative", "difficulty": "peaceful",
                  "spawn-monsters": "false", "spawn-animals": "false", "view-distance": "2", "simulation-distance": "2",
                  "max-players": "1", "spawn-protection": "0", "allow-nether": "false", "generate-structures": "false",
                  "max-tick-time": "60000"}
    (directory / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in properties.items()), encoding="utf-8")
    command = ["java", "-Xms256M", "-Xmx1G", "-Dfile.encoding=UTF-8", "-Dterminal.jline=false", "-Dterminal.ansi=false",
               "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:0",
               "-jar", str(directory / "paper.jar"), "--nogui"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(*command, cwd=directory, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024 * 1024, creationflags=flags)
    paper = LoggedProcess(process, directory / "paper-console.jsonl")
    report = {"simulated": False, "source_server": str(source), "validation_server": str(directory),
              "paper_sha256": sha256(directory / "paper.jar"), "source_paper_sha256": sha256(source / "paper.jar"),
              "sample_sha256": sha256(PLUGIN), "deployed_sample_sha256": sha256(deployed),
              "command": command, "runtime_boot_id": directory.name, "status": "failed"}
    probe = None
    evidence = None
    try:
        listening = await paper.wait_line("Listening for transport dt_socket at address:", timeout=30)
        port = int(re.search(r"address: (\d+)", listening).group(1))
        await paper.wait_line("Done (", timeout=args.start_timeout)
        version_line = next((r["text"] for r in paper.records if "This server is running Paper version" in r["text"]), None)
        if version_line is None or "MC: 1.20.1" not in version_line:
            raise RuntimeError("Expected Paper 1.20.1; inspect the observed startup version.")
        report["server_version_observed"] = version_line
        report["jdwp_port"] = port
        await paper.command("craftdebug calculate baseline-001 7 3")
        baseline = await paper.wait_line("CRAFTDEBUG status=ok run_id=baseline-001 damage=10")
        report["baseline_console_result"] = baseline
        config = {"helper_jar": str(HELPER), "evidence_dir": "data/debugger", "stop_timeout_ms": 10000,
                  "projects": {"debugger-sample": {"root": str(ROOT / "plugin_template/debugger_sample"), "artifact_jar": str(deployed)}},
                  "runtimes": {"paper-validation": {"project_id": "debugger-sample", "host": "127.0.0.1", "port": port,
                      "jar": str(directory / "paper.jar"), "boot_id": directory.name, "server_version": version_line}}}
        config_path = directory / "debugger.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        probe_process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "tests/debugger_python/probe_real.py"),
            "--config", str(config_path), "--runtime", "paper-validation", cwd=ROOT, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=flags)
        probe = LoggedProcess(probe_process, directory / "mcp-probe.jsonl")
        await probe.wait_line("Now run in the local Paper console:", timeout=30)
        await paper.command("craftdebug calculate probe-001 7 3")
        normal_completion = await paper.wait_line("CRAFTDEBUG status=ok run_id=probe-001 damage=10", timeout=30)
        await asyncio.wait_for(probe_process.wait(), 30)
        await probe.drain()
        if probe_process.returncode != 0:
            raise RuntimeError(f"MCP probe failed; inspect {probe.log_path}")
        output = "\n".join(r["text"] for r in probe.records if r["stream"] == "stdout")
        result = json.loads(output)
        evidence = Path(result["evidence_path"])
        summary_path = evidence.parent / "probe-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update(console_command_completion="passed", console_result=normal_completion,
                       server_version_observed=version_line, validation_server=str(directory))
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        await paper.command("craftdebug calculate regression-001 7 3")
        report["regression_console_result"] = await paper.wait_line("CRAFTDEBUG status=ok run_id=regression-001 damage=10")
        report.update(status="passed", mcp_jdi_flow="passed", console_command_completion="passed",
                      console_result=normal_completion, evidence_path=str(evidence), paper_console_log=str(paper.log_path))
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        if probe and probe.process.returncode is None:
            probe.process.terminate()
            await probe.process.wait()
            await probe.drain()
        if process.returncode is None:
            try:
                await paper.command("stop")
            except (OSError, ConnectionError):
                pass
            try:
                await asyncio.wait_for(process.wait(), 30)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                report["shutdown"] = "forced; unverified"
        await paper.drain()
        report.setdefault("shutdown", "normal" if process.returncode == 0 else "failed")
        if report["shutdown"] != "normal":
            report["status"] = "failed"
        (directory / "validation-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if evidence:
            (evidence.parent / "paper-validation-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise RuntimeError("Paper shutdown did not pass.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--start-timeout", type=float, default=120)
    asyncio.run(run(parser.parse_args()))

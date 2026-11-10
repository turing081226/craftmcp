"""Opt-in C service probe using an isolated copy and the existing Paper runner.

This test-only adapter exercises the proposed runtime contract. It does not
replace A's production lifecycle service or register tools on D's shared app.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mcp_server.adapters.rcon import execute_rcon
from mcp_server.paper_runner import read_state
from mcp_server.services.file_gate import FileGate
from mcp_server.services.plugins import PluginService, _write_json

SOURCE = '''package local;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
public final class Probe extends JavaPlugin {
    @Override public void onEnable() { ENABLE_BODY }
    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof org.bukkit.command.RemoteConsoleCommandSender)) return false;
        sender.sendMessage("CRAFTMCP_PLUGIN name=" + getName() + " version=" + getDescription().getVersion()
            + " enabled=" + isEnabled());
        return true;
    }
}
'''


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class RunnerFixture:
    def __init__(self, directory, port, password):
        self.directory, self.port, self.password = directory, port, password
        self.gate = None
        self.fail_next_start = False

    def command(self, text):
        return execute_rcon(text, self.password, port=self.port, timeout=5)

    def resolve(self, identifier):
        return {"managed": True, "server_dir": str(self.directory)}

    def status(self, identifier):
        return read_state(self.directory)

    @contextmanager
    def exclusive(self, identifier):
        with FileGate(self.directory / ".craftmcp/operation.lock"):
            self.gate = FileGate(self.directory / ".craftmcp/deploy.lock")
            self.gate.__enter__()
            try:
                yield
            finally:
                if self.gate:
                    self.gate.__exit__()
                    self.gate = None

    def stop(self, identifier, *, timeout):
        before = self.status(identifier)
        try:
            self.command("stop")
        except OSError:
            pass  # A lost response is resolved by observed exit, never a retry.
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            state = self.status(identifier)
            if state["boot_id"] != before["boot_id"]:
                raise RuntimeError("Runner restarted despite held installation gate.")
            if state["phase"] == "stopped" and state["pid"] is None:
                return state
            time.sleep(0.1)
        raise TimeoutError("Old Paper JVM did not exit.")

    def start(self, identifier, *, timeout):
        before = self.status(identifier)
        saved_paper = None
        if self.fail_next_start:
            self.fail_next_start = False
            saved_paper = self.directory / "paper-held-for-start-failure.jar"
            os.replace(self.directory / "paper.jar", saved_paper)
        self.gate.__exit__()
        self.gate = None
        if saved_paper:
            try:
                until = time.monotonic() + min(timeout, 15)
                while time.monotonic() < until:
                    state = self.status(identifier)
                    if (state["boot_id"] != before["boot_id"] and state["phase"] == "stopped"
                            and state.get("exit_code") not in (None, 0)):
                        raise RuntimeError("Intentional startup failure: observed new JVM exit without paper.jar.")
                    time.sleep(0.1)
                raise TimeoutError("Intentional startup failure exit was not observed.")
            finally:
                os.replace(saved_paper, self.directory / "paper.jar")
        return self.ready(timeout, previous=before["boot_id"])

    def ready(self, timeout=150, previous=None):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                state = self.status("test")
                log = self.directory / ".craftmcp/boots" / f"{state['boot_id']}.log"
                if (state["phase"] == "running" and state["boot_id"] != previous and log.is_file()
                        and re.search(r'\bDone \([^)]+\)!', log.read_text(encoding="utf-8", errors="replace"))):
                    return state
            except RuntimeError:
                pass
            time.sleep(0.2)
        raise TimeoutError("Paper boot readiness was not observed.")

    def observe(self, identifier, name):
        before = self.status(identifier)
        response = self.command("cprobe")
        after = self.status(identifier)
        if before["boot_id"] != after["boot_id"]:
            return {}
        match = re.search(r"CRAFTMCP_PLUGIN name=(\S+) version=(\S+) enabled=(true|false)(?:\s|$)", response)
        if match:
            return {"plugin_name": match[1], "version": match[2], "enabled": match[3] == "true",
                    "boot_id": after["boot_id"], "raw_output": response}
        # The fixture's exact disable event belongs to this boot and descriptor.
        log = self.directory / ".craftmcp/boots" / f"{after['boot_id']}.log"
        text = log.read_text(encoding="utf-8", errors="replace")
        if f"[{name}] Disabling {name} vbroken" in text:
            return {"plugin_name": name, "version": "broken", "enabled": False, "boot_id": after["boot_id"]}
        return {}


def main(source: Path, build_ids=None):
    source = source.resolve(strict=True)
    if "eula=true" not in (source / "eula.txt").read_text().lower():
        raise ValueError("An existing accepted EULA is required.")
    root = ROOT / ".cache/plugins/paper" / uuid.uuid4().hex
    directory = root / "server"
    directory.mkdir(parents=True)
    for name in ("paper.jar", "eula.txt"):
        shutil.copy2(source / name, directory / name)
    for name in ("cache", "libraries", "versions"):
        if (source / name).is_dir():
            shutil.copytree(source / name, directory / name)
    password = uuid.uuid4().hex
    rcon_port, game_port, jdwp_port = free_port(), free_port(), free_port()
    (directory / "server.properties").write_text(
        f"server-ip=127.0.0.1\nserver-port={game_port}\nenable-rcon=true\nrcon.port={rcon_port}\n"
        f"rcon.password={password}\nonline-mode=false\nlevel-name=c-world\nlevel-seed=1234\n"
        "view-distance=2\nsimulation-distance=2\nallow-nether=false\ngenerate-structures=false\n")
    runtime = RunnerFixture(directory, rcon_port, password)
    service = PluginService(ROOT, runtime=runtime, release_runtime=lambda _: None, observe_plugin=runtime.observe)
    report = {"status": "failed", "original_server_changed": False, "lifecycle_adapter": "test-only",
              "debugger_release": "no attached debugger in this probe", "steps": {}}
    console = None
    runner = None
    stop = root / "stop-runner"
    try:
        builds = []
        for index, (version, body) in enumerate((("1.0", 'getLogger().info("ready one");'),
                              ("2.0", 'getLogger().info("ready two");'),
                              ("broken", 'throw new IllegalStateException("C probe intentional enable failure");'))):
            build = service.get_build(build_ids[index]) if build_ids else service.build(
                                  "CPluginProbe", "local.Probe", {"local/Probe.java": SOURCE.replace("ENABLE_BODY", body)},
                                  plugin_manifest={"version": version, "commands": {"cprobe": {"description": "C diagnostic"}}})
            if not build["ok"]:
                raise RuntimeError(str(build))
            builds.append(build)
        report["build_ids"] = [build["build_id"] for build in builds]
        target = directory / "plugins/CPluginProbe.jar"
        target.parent.mkdir()
        shutil.copy2(builds[0]["jar_path"], target)
        java = ["java", "-Xms256M", "-Xmx1G", "-Dfile.encoding=UTF-8", "-Dterminal.jline=false",
                f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:{jdwp_port}",
                "-jar", "paper.jar", "--nogui"]
        command = [sys.executable, "-u", "-c",
                   "import sys,json; from pathlib import Path; from mcp_server.paper_runner import run; "
                   "run(Path(sys.argv[1]), json.loads(sys.argv[2]), restart_delay=0.2, shutdown=lambda: Path(sys.argv[3]).exists())",
                   str(directory), json.dumps(java), str(stop)]
        console = (root / "runner.log").open("wb")
        runner = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        report["initial_boot"] = runtime.ready()
        deployed = service.deploy("c-paper", builds[1]["build_id"], timeout=150)
        report["steps"]["normal_deploy"] = deployed
        if not deployed["ok"]:
            raise RuntimeError("Normal deployment failed.")
        failed = service.deploy("c-paper", builds[2]["build_id"], timeout=150)
        report["steps"]["enable_failure"] = failed
        if failed["ok"] or not failed["files_modified"]:
            raise RuntimeError("Intentional enable failure was not reported correctly.")
        restored = service.rollback("c-paper", failed["deployment_id"], timeout=150)
        report["steps"]["rollback"] = restored
        if not restored["ok"] or restored["version"] != "2.0":
            raise RuntimeError("Rollback activation was not verified.")
        runtime.fail_next_start = True
        failed_start = service.deploy("c-paper", builds[0]["build_id"], timeout=150)
        report["steps"]["start_failure"] = failed_start
        if failed_start["ok"] or not any("observed new JVM exit" in error for error in failed_start["errors"]):
            raise RuntimeError("Intentional startup failure was not observed correctly.")
        # The fixture restores paper.jar after observing failed JVM exit. The
        # existing runner recovers, then the normal rollback must restore v2.
        runtime.ready()
        recovered = service.rollback("c-paper", failed_start["deployment_id"], timeout=150)
        report["steps"]["start_failure_rollback"] = recovered
        if not recovered["ok"] or recovered["version"] != "2.0":
            raise RuntimeError("Rollback after startup failure was not verified.")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
    finally:
        if runner:
            stop.touch()
            try:
                runtime.command("stop")
            except (OSError, RuntimeError):
                pass
            try:
                runner.wait(timeout=45)
            except subprocess.TimeoutExpired:
                runner.kill()
                runner.wait()
            report["runner_exit_code"] = runner.returncode
        if console:
            console.close()
        _write_json(root / "summary.json", report)
    print(root / "summary.json")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--build-ids", nargs=3, help="Reuse explicit v1/v2/broken build IDs instead of rebuilding.")
    args = parser.parse_args()
    sys.exit(main(args.server_dir, args.build_ids))

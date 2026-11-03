"""Regression tests for additional QA; never modify an existing Paper server."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client
from mcp_server import server
from mcp_server.paper_runner import read_state
from mcp_server.services.deployment import deploy, digest, log_status, plugin_in_list
from mcp_server.services.file_gate import FileGate

ROOT = Path(__file__).resolve().parents[1]


def make_jar(path: Path, *, name="QAProbe", version="1.0", content="new"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.yml", f"name: {name}\nversion: '{version}'\nmain: local.Probe\n")
        archive.writestr("marker.txt", content)


class Fixtures(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.template = self.project / "plugin_template"
        (self.template / "src/main/resources").mkdir(parents=True)
        for path in ("build.gradle", "settings.gradle", "src/main/resources/plugin.yml"):
            (self.template / path).write_text("// fixture\n", encoding="utf-8")

    def build(self, path):
        with patch.object(server, "__file__", str(self.project / "mcp_server/server.py")), \
                patch.object(server, "_find_gradle", return_value=None):
            return json.loads(server._build_locked("QAProbe", "local.Probe", {path: "class Probe {}"}))


class BuildTests(Fixtures):
    def test_rooted_drive_relative_traversal_and_absolute_sources_are_rejected_before_mutation(self):
        escaped = self.root / "escape.java"
        rooted = str(escaped)[len(escaped.drive):] if os.name == "nt" else "/escape.java"
        for name in (rooted, "C:escape.java", "C:\\escape.java", "../escape.java", "..\\escape.java",
                     "//server/share/escape.java", str(escaped)):
            with self.subTest(name=name):
                result = self.build(name)
                self.assertFalse(result["ok"])
                self.assertFalse(escaped.exists())
                self.assertFalse((self.project / "data/build_workspace/QAProbe").exists())

    def test_relative_source_is_created_inside_build_root(self):
        self.build("local/Probe.java")
        self.assertEqual((self.project / "data/build_workspace/QAProbe/src/main/java/local/Probe.java").read_text(), "class Probe {}")

    def test_concurrent_build_is_busy_without_deleting_existing_workspace(self):
        marker = self.project / "data/build_workspace/QAProbe/marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("keep")
        with FileGate(self.project / ".cache/build-locks/QAProbe.lock"):
            result = self.build("local/Probe.java")
        self.assertFalse(result["ok"])
        self.assertEqual(marker.read_text(), "keep")

    def test_plugin_match_is_exact_and_log_disable_is_observed(self):
        self.assertFalse(plugin_in_list("Plugins (1): QAProbeHelper", "QAProbe"))
        self.assertTrue(plugin_in_list("Plugins (2): §aQAProbe§r, Other", "QAProbe"))
        self.assertTrue(plugin_in_list("§fServer Plugins (1):\n§x§e§d§8§1§0§6Bukkit Plugins:\n §8- §aQAProbe\n", "QAProbe"))
        self.assertFalse(plugin_in_list("Server Plugins (1):\nBukkit Plugins:\n - QAProbeHelper\n", "QAProbe"))
        enabled, errors = log_status("[QAProbe] Enabling QAProbe v1.0\n[QAProbe] Disabling QAProbe v1.0\n", "QAProbe", "1.0")
        self.assertFalse(enabled)
        self.assertFalse(errors)
        self.assertFalse(log_status("[QAProbe] Enabling QAProbe v1.01\n", "QAProbe", "1.0")[0])


class DeploymentTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.server_dir = self.root / "server"
        self.control = self.server_dir / ".craftmcp"
        self.control.mkdir(parents=True)
        self.target = self.server_dir / "plugins/QAProbe.jar"
        self.target.parent.mkdir()
        make_jar(self.target, content="old")
        self.old_bytes = self.target.read_bytes()
        self.artifact = self.root / "candidate.jar"
        make_jar(self.artifact)
        self.state = {"server_dir": str(self.server_dir), "runner_id": "owned-runner", "boot_id": "old",
                      "pid": 100, "phase": "running"}
        self.events = []

    def observed_state(self, directory):
        if self.state["phase"] == "stopped":
            # The old generation may restart only after the file installation gate is released.
            try:
                with FileGate(self.control / "deploy.lock"):
                    self.assertEqual(self.target.read_bytes(), self.artifact.read_bytes())
                    self.state.update(phase="running", pid=200, boot_id="new")
                    (self.control / "boots").mkdir(exist_ok=True)
                    (self.control / "boots/new.log").write_text(self.boot_log, encoding="utf-8")
            except BlockingIOError:
                pass
        return self.state.copy()

    boot_log = "[QAProbe] Enabling QAProbe v1.0\nDone (0.1s)!\n"

    def rcon(self, command):
        self.events.append(command)
        if command == "stop":
            self.assertEqual(self.target.read_bytes(), self.old_bytes)
            self.state.update(phase="stopped", pid=None)
            return "stopping"
        return "Plugins (1): QAProbe" if command == "plugins" else "ok"

    def perform(self, rcon=None, timeout=1):
        with patch("mcp_server.services.deployment.read_state", side_effect=self.observed_state):
            return deploy("QAProbe", self.artifact, self.server_dir, rcon=rcon or self.rcon,
                          prepare_stop=lambda: self.events.append("detach"), timeout=timeout)

    def test_unmanaged_server_is_rejected_without_changing_jar(self):
        result = deploy("QAProbe", self.artifact, self.server_dir, rcon=self.rcon, prepare_stop=lambda: None)
        self.assertFalse(result["ok"])
        self.assertFalse(result["files_modified"])
        self.assertEqual(self.target.read_bytes(), self.old_bytes)
        self.assertEqual(self.events, [])

    def test_stop_failure_does_not_modify_jar_and_returns_backup(self):
        def fail(command):
            if command == "stop":
                raise ConnectionError("simulated stop failure")
            return "ok"
        result = self.perform(fail, timeout=0.05)
        self.assertFalse(result["ok"])
        self.assertTrue(result["execution_unknown"])
        self.assertFalse(result["files_modified"])
        self.assertEqual(self.target.read_bytes(), self.old_bytes)
        self.assertEqual(Path(result["backup_path"]).read_bytes(), self.old_bytes)

    def test_success_waits_for_old_exit_and_checks_new_boot_and_hash(self):
        result = self.perform()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["phase"], "verified")
        self.assertEqual(self.events[:3], ["list", "detach", "stop"])
        self.assertEqual(result["boot_id"], "new")
        self.assertEqual(result["artifact_sha256"], digest(self.target))
        self.assertEqual(Path(result["backup_path"]).read_bytes(), self.old_bytes)

    def test_stop_response_lost_but_observed_exit_allows_single_install(self):
        def lost(command):
            result = self.rcon(command)
            if command == "stop":
                raise ConnectionError("response lost")
            return result
        result = self.perform(lost)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["execution_unknown"])
        self.assertEqual(self.events.count("stop"), 1)

    def test_different_plugin_cannot_succeed(self):
        def different(command):
            return "Plugins (1): QAProbeHelper" if command == "plugins" else self.rcon(command)
        result = self.perform(different, timeout=0.1)
        self.assertFalse(result["ok"])

    def test_new_boot_first_line_error_is_not_skipped(self):
        self.boot_log = "ERROR: plugin failed to enable\n[QAProbe] Enabling QAProbe v1.0\nDone (0.1s)!\n"
        result = self.perform()
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"], ["ERROR: plugin failed to enable"])
        self.assertTrue(result["raw_output"].startswith("ERROR:"))

    def test_disabled_plugin_cannot_succeed(self):
        self.boot_log += "[QAProbe] Disabling QAProbe v1.0\n"
        self.assertFalse(self.perform(timeout=0.1)["ok"])

    def test_server_operation_gate_rejects_concurrent_deploy(self):
        with FileGate(self.control / "operation.lock"):
            result = self.perform()
        self.assertFalse(result["ok"])
        self.assertEqual(self.target.read_bytes(), self.old_bytes)
        self.assertEqual(self.events, [])

    def test_artifact_name_mismatch_is_rejected_before_stop(self):
        make_jar(self.artifact, name="WrongName")
        self.assertFalse(self.perform()["ok"])
        self.assertEqual(self.events, [])

    def test_partial_install_retains_backup_and_blocks_restart(self):
        def interrupted(source, target):
            target.write_bytes(b"partial")
            raise OSError("simulated write interruption")
        with patch("mcp_server.services.deployment.install_artifact", side_effect=interrupted):
            result = self.perform()
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovery_required"])
        self.assertTrue(result["files_modified"])
        self.assertEqual(Path(result["backup_path"]).read_bytes(), self.old_bytes)
        self.assertEqual(json.loads(Path(result["install_journal"]).read_text())["phase"], "installing")


class AsyncBuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_call_finishes_worker_without_blocking_other_tools(self):
        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()

        def work(*args):
            started.set()
            try:
                if not release.wait(5):
                    raise AssertionError("Cancelled worker did not get its cleanup signal.")
                return server._result(ok=True, stage="fixture")
            finally:
                finished.set()

        with patch.object(server, "_build_locked", side_effect=work) as worker:
            async with Client(server.mcp) as client:
                pending = asyncio.create_task(client.call_tool("build_plugin", {"plugin_name": "QAProbe", "main_class": "local.Probe", "source_files": {"Probe.java": "class Probe {}"}}))
                try:
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                    await asyncio.wait_for(client.list_tools(), 2)
                finally:
                    release.set()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))
                self.assertEqual(worker.call_count, 1)

    async def test_build_and_deploy_worker_do_not_block_debugger_calls(self):
        release = threading.Event()
        started = threading.Event()

        def work(*args):
            started.set()
            if not release.wait(5):
                raise AssertionError("The event loop did not release the worker.")
            return server._result(ok=True, stage="fixture")

        async with Client(server.mcp) as client:
            for name, target, params in (
                ("build_plugin", "_build_locked", {"plugin_name": "QAProbe", "main_class": "local.Probe", "source_files": {"Probe.java": "class Probe {}"}}),
                ("deploy_plugin", "_deploy_plugin", {"plugin_name": "QAProbe"}),
            ):
                with self.subTest(name=name), patch.object(server, target, side_effect=work):
                    started.clear()
                    release.clear()
                    pending = asyncio.create_task(client.call_tool(name, params))
                    try:
                        while not started.is_set():
                            await asyncio.sleep(0.01)
                        state = await asyncio.wait_for(client.call_tool("get_debug_state", {"session_id": "unknown"}), 2)
                        self.assertEqual(state.data["error"]["code"], "session_not_found")
                        self.assertFalse(pending.done())
                    finally:
                        release.set()
                        await pending


class RunnerTests(Fixtures):
    def test_real_runner_reaps_old_process_and_restarts_after_install(self):
        directory = self.root / "runner-server"
        (directory / "plugins").mkdir(parents=True)
        target = directory / "plugins/QAProbe.jar"
        make_jar(target, content="old")
        artifact = self.root / "new.jar"
        make_jar(artifact)
        fixture = self.root / "fixture.py"
        fixture.write_text(
            "import time\nfrom pathlib import Path\n"
            "print('[QAProbe] Enabling QAProbe v1.0', flush=True)\n"
            "print('Done (0.1s)!', flush=True)\n"
            "while not Path('stop-child').exists(): time.sleep(0.01)\n"
            "Path('stop-child').unlink()\n", encoding="utf-8")
        stop_runner = self.root / "stop-runner"
        command = [sys.executable, "-u", "-c",
                   "import sys; from pathlib import Path; from mcp_server.paper_runner import run; "
                   "run(Path(sys.argv[1]), [sys.executable, '-u', sys.argv[2]], restart_delay=0.1, "
                   "shutdown=lambda: Path(sys.argv[3]).exists())",
                   str(directory), str(fixture), str(stop_runner)]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, creationflags=flags)
        try:
            until = time.monotonic() + 15
            while True:
                try:
                    state = read_state(directory)
                    log = directory / ".craftmcp/boots" / f"{state['boot_id']}.log"
                    if state["phase"] == "running" and log.is_file() and "Done" in log.read_text():
                        break
                except RuntimeError:
                    pass
                if child.poll() is not None or time.monotonic() > until:
                    self.fail("Owned fixture runner failed to start.")
                time.sleep(0.1)
            old_boot = state["boot_id"]

            def rcon(text):
                if text == "stop":
                    self.assertNotEqual(target.read_bytes(), artifact.read_bytes())
                    (directory / "stop-child").write_text("stop")
                return "Plugins (1): QAProbe" if text == "plugins" else "ok"

            result = deploy("QAProbe", artifact, directory, rcon=rcon, prepare_stop=lambda: None, timeout=15)
            self.assertTrue(result["ok"], result)
            self.assertNotEqual(result["boot_id"], old_boot)
            self.assertEqual(target.read_bytes(), artifact.read_bytes())
            with FileGate(directory / ".craftmcp/deploy.lock"):
                (directory / ".craftmcp/install.json").write_text('{"phase":"installing"}')
                (directory / "stop-child").touch()
                until = time.monotonic() + 5
                while read_state(directory)["phase"] != "stopped":
                    self.assertLess(time.monotonic(), until)
                    time.sleep(0.1)
            until = time.monotonic() + 5
            while not read_state(directory).get("blocked_reason"):
                self.assertLess(time.monotonic(), until)
                time.sleep(0.1)
            self.assertIsNone(read_state(directory)["pid"])
        finally:
            (directory / "stop-child").touch()
            stop_runner.touch()
            try:
                _, errors = child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                _, errors = child.communicate()
            self.assertEqual(child.returncode, 0, errors.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()

"""C contracts: real files, fake lifecycle, and optional real cached Gradle build."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastmcp import Client, FastMCP
from mcp_server.services.deployment import digest
from mcp_server.services.plugins import PluginService, _write_json, _run, _replace_file
from mcp_server.tools.plugins import register_plugin_tools

ROOT = Path(__file__).resolve().parents[2]
SOURCE = '''package local;
import org.bukkit.plugin.java.JavaPlugin;
public final class Probe extends JavaPlugin {
    @Override public void onEnable() {
        int value = 7;
        getLogger().info("probe=" + value);
    }
}
'''


def jar(path, version="1.0", name="Probe"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.yml", f"name: {name}\nversion: '{version}'\nmain: local.Probe\n")
        archive.writestr("marker", version)


class FakeRuntime:
    def __init__(self, root):
        self.root = root
        self.root.mkdir()
        self.state = {"phase": "running", "pid": 10, "boot_id": "boot-1"}
        self.managed = True
        self.locked = False
        self.events = []
        self.stop_confirmed = True
        self.start_fails = False
        self.enabled = True
        self.stale = False
        self.target = root / "plugins/Probe.jar"
        jar(self.target)

    def resolve(self, identifier):
        return {"server_dir": str(self.root), "managed": self.managed}

    def status(self, identifier):
        # Read-only calls must work even while deploy holds the mutation gate.
        return self.state.copy()

    @contextmanager
    def exclusive(self, identifier):
        if self.locked:
            raise BlockingIOError("busy")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def release(self, identifier):
        self.events.append("release")

    def stop(self, identifier, *, timeout):
        assert self.locked
        self.events.append(("stop", digest(self.target)))
        if self.stop_confirmed:
            self.state.update(phase="stopped", pid=None)

    def start(self, identifier, *, timeout):
        assert self.locked
        self.events.append(("start", digest(self.target)))
        if self.start_fails:
            raise RuntimeError("start failed")
        self.state.update(phase="running", pid=20, boot_id=uuid.uuid4().hex)

    def observe(self, identifier, name):
        with zipfile.ZipFile(self.target) as archive:
            import yaml
            manifest = yaml.safe_load(archive.read("plugin.yml"))
        return {"plugin_name": "ProbeHelper" if name == "ProbeHelper" else name,
                "version": manifest["version"], "enabled": self.enabled,
                "boot_id": "stale-boot" if self.stale else self.state["boot_id"]}


class PluginTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".cache")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = FakeRuntime(self.root / "server")
        self.service = PluginService(self.root, runtime=self.runtime,
                                     release_runtime=self.runtime.release, observe_plugin=self.runtime.observe)
        self.build_id = uuid.uuid4().hex
        directory = self.service._directory("builds", self.build_id)
        directory.mkdir(parents=True)
        artifact = directory / "artifact.jar"
        jar(artifact, "2.0")
        self.build = {"ok": True, "jar_path": str(artifact), "artifact_sha256": digest(artifact)}
        _write_json(directory / "record.json", self.build)
        self.old = digest(self.runtime.target)

    def test_deploy_stop_replace_start_and_exact_new_boot(self):
        outcome = self.service.deploy("test", self.build_id, run_id="run-1")
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(self.runtime.events, ["release", ("stop", self.old),
                                              ("start", self.build["artifact_sha256"])])
        self.assertNotEqual(outcome["boot_id"], outcome["previous_boot_id"])
        self.assertEqual(outcome["run_id"], "run-1")
        self.assertIsNone(outcome["plugin_status"]["runtime_bytecode_sha256"])

    def test_stop_not_confirmed_never_changes_jar(self):
        self.runtime.stop_confirmed = False
        outcome = self.service.deploy("test", self.build_id)
        self.assertFalse(outcome["ok"])
        self.assertFalse(outcome["files_modified"])
        self.assertEqual(digest(self.runtime.target), self.old)
        self.assertTrue(outcome["execution_unknown"])

    def test_unmanaged_and_busy_refused_before_stop(self):
        self.runtime.managed = False
        self.assertFalse(self.service.deploy("test", self.build_id)["ok"])
        self.runtime.managed = True
        with self.runtime.exclusive("test"):
            self.assertFalse(self.service.deploy("test", self.build_id)["ok"])
            self.assertTrue(self.service.get_plugin_status("test", "Probe")["ok"])
        self.assertEqual(self.runtime.events, [])

    def test_tampered_build_and_failed_build_never_stop(self):
        Path(self.build["jar_path"]).write_bytes(b"tampered")
        self.assertFalse(self.service.deploy("test", self.build_id)["ok"])
        _write_json(self.service._directory("builds", self.build_id) / "record.json", {"ok": False})
        self.assertFalse(self.service.deploy("test", self.build_id)["ok"])
        self.assertEqual(self.runtime.events, [])

    def test_loading_failure_retains_verified_backup_for_rollback(self):
        self.runtime.enabled = False
        failed = self.service.deploy("test", self.build_id)
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["files_modified"])
        self.runtime.enabled = True
        restored = self.service.rollback("test", failed["deployment_id"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(digest(self.runtime.target), self.old)
        self.assertEqual(restored["version"], "1.0")
        self.assertTrue(restored["warnings"])

    def test_duplicate_plugin_under_another_filename_is_rejected_before_stop(self):
        jar(self.runtime.target.with_name("renamed-plugin.jar"))
        outcome = self.service.deploy("test", self.build_id)
        self.assertFalse(outcome["ok"])
        self.assertEqual(self.runtime.events, [])
        self.assertEqual(digest(self.runtime.target), self.old)

    def test_start_failure_reports_recovery_and_stopped_state(self):
        self.runtime.start_fails = True
        failed = self.service.deploy("test", self.build_id)
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["recovery_required"])
        self.assertIsNotNone(failed["previous_jar"])
        self.assertEqual(self.runtime.state["phase"], "stopped")
        self.runtime.start_fails = False
        restored = self.service.rollback("test", failed["deployment_id"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(digest(self.runtime.target), self.old)

    def test_stale_boot_and_wrong_plugin_observation_cannot_succeed(self):
        self.runtime.stale = True
        failed = self.service.deploy("test", self.build_id, timeout=0.05)
        self.assertFalse(failed["ok"])
        self.runtime.stale = False
        self.service.observe_plugin = lambda *args: {"plugin_name": "ProbeHelper", "enabled": True,
            "version": "2.0", "boot_id": self.runtime.state["boot_id"]}
        self.assertIsNone(self.service.get_plugin_status("test", "Probe")["enabled"])

    def test_rollback_rejects_changed_backup_or_current_installation(self):
        deployed = self.service.deploy("test", self.build_id)
        Path(deployed["previous_jar"]["jar_path"]).write_bytes(b"tampered")
        self.assertFalse(self.service.rollback("test", deployed["deployment_id"])["ok"])
        jar(Path(deployed["previous_jar"]["jar_path"]))
        jar(self.runtime.target, "3.0")
        self.assertFalse(self.service.rollback("test", deployed["deployment_id"])["ok"])

    def test_no_observer_means_unknown_even_if_disk_matches(self):
        self.service.observe_plugin = None
        status = self.service.get_plugin_status("test", "Probe")
        self.assertIsNone(status["enabled"])
        self.assertEqual(status["installed"]["artifact_sha256"], self.old)
        self.assertFalse(self.service.deploy("test", self.build_id)["ok"])

    def test_build_rejects_escaping_paths_and_conflicting_descriptors(self):
        for path in ("../escape.java", "C:escape.java", "\\escape.java", "a/../../escape.java",
                     "NUL.java", "foo.java:stream", "x./Probe.java"):
            with self.subTest(path=path):
                self.assertFalse(self.service.build("Probe", "local.Probe", {path: SOURCE})["ok"])
        for kwargs in ({"plugin_manifest": {"name": "Other"}},
                       {"plugin_manifest": {"commands": []}},
                       {"resource_files": {"plugin.yml": "replace"}},
                       {"resource_files": {"../config.yml": "replace"}}):
            self.assertFalse(self.service.build("Probe", "local.Probe", {"local/Probe.java": SOURCE}, **kwargs)["ok"])
        self.assertFalse((self.root / "escape.java").exists())

    def test_failed_gradle_cannot_return_previous_build(self):
        shutil.copytree(ROOT / "plugin_template", self.root / "plugin_template",
                        ignore=shutil.ignore_patterns("debugger_sample", "build", ".gradle"))
        with patch("mcp_server.services.plugins._run", return_value=subprocess.CompletedProcess([], 1, "compile failed", "")):
            failed = self.service.build("Probe", "local.Probe", {"local/Probe.java": "not java"})
        self.assertFalse(failed["ok"])
        self.assertNotEqual(failed["build_id"], self.build_id)
        self.assertNotIn("jar_path", failed)
        self.assertEqual(Path(failed["output_path"]).read_text(), "compile failed")
        self.assertTrue(self.service.get_build(self.build_id)["ok"])

    def test_owned_build_process_tree_is_terminated_at_deadline(self):
        marker = self.root / "child-finished"
        child = "import time; from pathlib import Path; time.sleep(2); Path(" + repr(str(marker)) + ").touch()"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(30)"
        before = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run([sys.executable, "-c", parent], cwd=self.root, timeout=0.5)
        self.assertLess(time.monotonic() - before, 8)
        time.sleep(2.1)
        self.assertFalse(marker.exists(), "The owned child survived build timeout cleanup.")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing regression")
    def test_windows_jar_replace_waits_for_a_temporary_reader_lock(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        candidate = self.root / "replacement.tmp"
        candidate.write_bytes(b"replacement")
        # A real reader without FILE_SHARE_DELETE temporarily prevents rename.
        handle = api.CreateFileW(str(self.runtime.target), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(OSError):
                _replace_file(candidate, self.runtime.target, timeout=0.03)
            self.assertTrue(candidate.exists())
            self.assertEqual(digest(self.runtime.target), self.old)
        except BaseException:
            api.CloseHandle(handle)
            raise
        release = threading.Timer(0.2, lambda: api.CloseHandle(handle))
        release.start()
        try:
            _replace_file(candidate, self.runtime.target, timeout=2)
        finally:
            release.join()
        self.assertEqual(self.runtime.target.read_bytes(), b"replacement")
        self.assertFalse(candidate.exists())

    def test_tools_register_and_submit_one_job_without_nested_lifecycle_jobs(self):
        async def scenario():
            class Jobs:
                calls = []
                async def submit(inner, spec, operation):
                    inner.calls.append(spec)
                    return {"job_id": "job", "result": await operation()}
            jobs = Jobs()
            app = FastMCP("C test", strict_input_validation=True)
            register_plugin_tools(app, self.service, jobs)
            async with Client(app) as client:
                self.assertEqual({tool.name for tool in await client.list_tools()},
                                 {"build_plugin", "deploy_plugin", "get_plugin_status", "rollback_plugin"})
                response = await client.call_tool("deploy_plugin", {"runtime_id": "test", "build_id": self.build_id,
                                                                      "request_id": "request-1"})
                self.assertTrue(response.data["result"]["ok"])
            self.assertEqual(len(jobs.calls), 1)
            self.assertEqual(jobs.calls[0]["request_id"], "request-1")
        asyncio.run(scenario())


@unittest.skipUnless(os.environ.get("CRAFTMCP_REAL_BUILD") == "1", "opt-in real Gradle build")
class RealBuildTests(unittest.TestCase):
    def test_manifest_resources_debug_tables_and_failed_rebuild(self):
        service = PluginService(ROOT)
        outcome = service.build("CBuildProbe", "local.Probe", {"local/Probe.java": SOURCE},
                                plugin_manifest={"version": "1.2.3", "commands": {"cprobe": {"description": "test"}},
                                                 "permissions": {"cprobe.use": {"default": "op"}}},
                                resource_files={"config.yml": "message: 안녕하세요\n"})
        self.assertTrue(outcome["ok"], outcome)
        self.assertTrue(outcome["debug_info"]["verified"])
        self.assertFalse((Path(outcome["project_root"]) / "debugger_sample").exists())
        with zipfile.ZipFile(outcome["jar_path"]) as archive:
            import yaml
            manifest = yaml.safe_load(archive.read("plugin.yml"))
            self.assertIn("cprobe", manifest["commands"])
            self.assertIn("cprobe.use", manifest["permissions"])
            self.assertIn("안녕하세요", archive.read("config.yml").decode())
        failed = service.build("CBuildProbe", "local.Probe", {"local/Probe.java": "invalid Java"})
        self.assertFalse(failed["ok"])
        self.assertNotEqual(outcome["build_id"], failed["build_id"])
        self.assertTrue(service.get_build(outcome["build_id"])["ok"])
        print("REAL_BUILD_RECORD=" + str(Path(outcome["jar_path"]).parent / "record.json"))


if __name__ == "__main__":
    unittest.main()

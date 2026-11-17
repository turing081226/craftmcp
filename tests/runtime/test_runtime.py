"""A's isolated runtime tests: temporary files and owned processes only."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp_server.models.contracts import ContractError, Endpoint, Observation, RconEndpoint, RuntimeTarget
from mcp_server.services.runtime import RuntimeRegistry, RuntimeService, _runner_observation

ROOT = Path(__file__).resolve().parents[2]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = RuntimeTarget("paper", "sample", self.root / "project", self.root,
                                    self.root / "paper.jar", jdwp=Endpoint("127.0.0.1", 5005),
                                    rcon=RconEndpoint("127.0.0.1", 25575, "TEST_RCON_PASSWORD"))

    def test_resolve_and_sorted_listing(self):
        other = replace(self.target, runtime_id="alpha", server_dir=self.root / "other",
                        server_jar=self.root / "other/paper.jar")
        service = RuntimeService(RuntimeRegistry([self.target, other]))
        self.assertIs(service.resolve("paper"), self.target)
        data = service.list_runtimes()
        self.assertEqual([x["runtime_id"] for x in data["result"]["runtimes"]], ["alpha", "paper"])
        self.assertEqual(data["schema_version"], 1)
        self.assertFalse(data["result"]["runtimes"][0]["capabilities"]["stop"])

    def test_unknown_runtime_does_not_echo_untrusted_input(self):
        registry = RuntimeRegistry([])
        with self.assertRaises(ContractError) as error:
            registry.resolve("sensitive-untrusted-input")
        self.assertEqual(error.exception.code, "runtime_not_found")
        self.assertNotIn("sensitive", str(error.exception))
        with self.assertRaises(ContractError):
            registry.resolve([])

    def test_duplicate_ids_and_aliases_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeRegistry([self.target, self.target])
        with self.assertRaises(ValueError):
            RuntimeRegistry([self.target, replace(self.target, runtime_id="alias")])

    def test_paths_ports_and_credentials_validated(self):
        with self.assertRaises(ValueError):
            replace(self.target, server_jar=self.root / "elsewhere/paper.jar")
        for port in (True, 0, 65536, "25575"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                Endpoint("127.0.0.1", port)
        with self.assertRaises(ValueError):
            RconEndpoint("127.0.0.1", 25575, "raw password!")

    def test_bridge_preserves_target_and_ignores_configured_boot_id(self):
        legacy = SimpleNamespace(project_id="sample", host="127.0.0.1", port=5005,
                                 jar=self.root / "paper.jar", boot_id="static-unverified")
        config = SimpleNamespace(projects={"sample": SimpleNamespace(root=self.root / "project")},
                                 runtimes={"paper": legacy})
        registry = RuntimeRegistry.from_debugger_config(config, rcon_by_runtime={"paper": self.target.rcon})
        resolved = registry.resolve("paper")
        self.assertEqual(resolved, self.target)
        self.assertNotIn("static-unverified", json.dumps(RuntimeService(registry).list_runtimes()))
        self.assertIsNone(RuntimeRegistry.from_debugger_config(config).resolve("paper").rcon)
        with self.assertRaises(ValueError):
            RuntimeRegistry.from_debugger_config(config, rcon_by_runtime={"typo": self.target.rcon})

    def test_listing_does_not_read_credentials(self):
        with patch.dict(os.environ, {"TEST_RCON_PASSWORD": "never-expose-this-value"}):
            result = RuntimeService(RuntimeRegistry([self.target])).list_runtimes()
        self.assertNotIn("never-expose-this-value", json.dumps(result))
        self.assertIn("TEST_RCON_PASSWORD", json.dumps(result))


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = RuntimeTarget("paper", "sample", self.root / "project", self.root,
                                    self.root / "paper.jar")
        self.registry = RuntimeRegistry([self.target])

    async def test_absent_runner_is_unknown_without_writing_or_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("No RCON/JDWP probes")):
            result = await RuntimeService(self.registry).status("paper")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["result"]["boot_id"])
        self.assertEqual(result["result"]["observations"]["process"]["status"], "unknown")
        self.assertEqual(result["result"]["observations"]["game_ready"]["status"], "unsupported")
        self.assertFalse((self.root / ".craftmcp").exists())

    async def test_missing_and_invalid_id_return_versioned_errors(self):
        for runtime_id, code in (("missing", "runtime_not_found"), (" ", "invalid_argument")):
            response = await RuntimeService(self.registry).status(runtime_id)
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], code)
            self.assertEqual(response["schema_version"], 1)

    async def test_stalled_rcon_does_not_block_suspension_or_event_loop(self):
        debugger_queried = asyncio.Event()
        rcon_cancelled = asyncio.Event()

        async def rcon(target):
            try:
                await asyncio.Event().wait()
            finally:
                rcon_cancelled.set()

        async def suspension(target):
            debugger_queried.set()
            return Observation("observed", "suspended", source="fake_debugger")

        service = RuntimeService(self.registry, observers={"rcon": rcon, "suspension": suspension},
                                 observation_timeout=0.1)
        pending = asyncio.create_task(service.status("paper"))
        await asyncio.wait_for(debugger_queried.wait(), 1)
        self.assertFalse(pending.done())
        data = (await asyncio.wait_for(pending, 1))["result"]["observations"]
        self.assertTrue(rcon_cancelled.is_set())
        self.assertEqual(data["rcon"]["source"], "observation_timeout")
        self.assertEqual(data["suspension"]["value"], "suspended")
        self.assertIsNone(data["game_ready"]["value"])

    async def test_observer_failure_does_not_disclose_exception(self):
        async def broken(target):
            raise RuntimeError("password=secret-value")

        result = await RuntimeService(self.registry, observers={"rcon": broken}).status("paper")
        self.assertTrue(result["ok"])
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertTrue(result["warnings"])
        self.assertEqual(result["result"]["observations"]["rcon"]["status"], "unknown")

    async def test_request_cancellation_propagates_to_observers(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def rcon(target):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        pending = asyncio.create_task(RuntimeService(self.registry, observers={"rcon": rcon}).status("paper"))
        await asyncio.wait_for(started.wait(), 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(cancelled.is_set())

    async def test_open_port_does_not_imply_game_ready(self):
        async def tcp(target):
            return Observation("observed", "tcp_open", source="fake_tcp")

        result = await RuntimeService(self.registry, observers={"rcon": tcp}).status("paper")
        self.assertEqual(result["result"]["observations"]["rcon"]["value"], "tcp_open")
        self.assertIsNone(result["result"]["observations"]["game_ready"]["value"])

    async def test_runner_target_mismatch_and_remote_targets_are_unknown(self):
        state = {"command": ["java", "-jar", "another.jar"]}
        with patch("mcp_server.services.runtime.read_state", return_value=state):
            self.assertEqual(_runner_observation(self.target).source, "runner_target_mismatch")
        remote = replace(self.target, jdwp=Endpoint("remote.example", 5005))
        with patch("mcp_server.services.runtime.read_state", side_effect=AssertionError("Local state is unrelated")):
            self.assertEqual(_runner_observation(remote).status, "unknown")

    async def test_boot_identity_requires_matching_jdwp_and_valid_pid(self):
        target = replace(self.target, jdwp=Endpoint("127.0.0.1", 5005))
        state = {"command": ["java", "-agentlib:jdwp=transport=dt_socket,address=127.0.0.1:5005",
                             "-jar", "paper.jar"],
                 "phase": "running", "pid": 123, "runner_id": "owned-runner",
                 "boot_id": "observed-boot", "heartbeat": time.time()}
        with patch("mcp_server.services.runtime.read_state", return_value=state):
            self.assertEqual(_runner_observation(target).details["boot_id"], "observed-boot")
            mismatch = replace(target, jdwp=Endpoint("127.0.0.1", 5006))
            observation = _runner_observation(mismatch)
            self.assertEqual(observation.source, "runner_target_mismatch")
            self.assertNotIn("boot_id", observation.details)
            for pid in (None, True, -1, "123"):
                state["pid"] = pid
                with self.subTest(pid=pid):
                    self.assertEqual(_runner_observation(target).source, "runner_invalid_pid")

    async def test_real_owned_runner_reports_boot_and_releases_files_on_exit(self):
        # The runner owns a tiny Python child, never a developer's Paper server.
        script = "\n".join([
            "import sys",
            "from pathlib import Path",
            "from mcp_server.paper_runner import run",
            "root = Path(sys.argv[1])",
            "run(root, [sys.executable, '-c', 'import time; time.sleep(0.3)', '-jar', 'paper.jar'],",
            "    restart_delay=0.01, shutdown=lambda: (root / 'finish').exists())",
        ])
        runner = subprocess.Popen([sys.executable, "-c", script, str(self.root)], cwd=ROOT,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 8
            service = RuntimeService(self.registry)
            while True:
                result = await service.status("paper")
                if result["result"]["boot_id"]:
                    break
                if time.monotonic() > deadline or runner.poll() is not None:
                    self.fail("The owned test runner did not report a running generation.")
                await asyncio.sleep(0.02)
            process = result["result"]["observations"]["process"]
            self.assertEqual(process["source"], "paper_runner")
            self.assertGreater(process["details"]["pid"], 0)
            (self.root / "finish").touch()
            _, errors = await asyncio.to_thread(runner.communicate, timeout=8)
            self.assertEqual(runner.returncode, 0, errors.decode(errors="replace"))
            result = await service.status("paper")
            self.assertIsNone(result["result"]["boot_id"])
            self.assertEqual(result["result"]["observations"]["process"]["status"], "unknown")
        finally:
            (self.root / "finish").touch()
            if runner.poll() is None:
                await asyncio.to_thread(runner.wait, timeout=8)
            if runner.stderr:
                runner.stderr.close()


if __name__ == "__main__":
    unittest.main()

"""C artifacts with A's async lifecycle and B's shared operation queue."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import yaml

from mcp_server.models.contracts import ContractError
from mcp_server.paper_runner import CONTROL_DIR
from mcp_server.services.deployment import digest
from mcp_server.services.jobs import JobError, JobManager
from mcp_server.services.plugins import (
    NAME, PluginService, _inside, _jar_manifest, _replace_file, _write_json, result,
)
from mcp_server.services.runtime import RuntimeService


async def blocking(function, *args, **kwargs):
    """Keep ownership until a bounded file/build worker has actually finished."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


class ManagedPluginService(PluginService):
    def __init__(self, workspace: Path, *, runtime: RuntimeService, observe_plugin, java_executable="java"):
        super().__init__(workspace, java_executable=java_executable)
        self.runtime = runtime
        self.observe_plugin = observe_plugin

    async def _state(self, runtime_id):
        envelope = await self.runtime.status(runtime_id)
        if not envelope["ok"]:
            raise ValueError(envelope["error"]["code"])
        state = envelope["result"]
        process = state["observations"]["process"]
        return {"phase": process["value"] if process["status"] == "observed" else "unknown",
                "pid": process["details"].get("pid"), "boot_id": state["boot_id"]}

    def _target(self, runtime, name):
        return _inside(runtime.server_dir, runtime.server_dir / "plugins" / f"{name}.jar")

    async def get_plugin_status(self, runtime_id, plugin_name, *, timeout=5):
        try:
            target = self.runtime.resolve(runtime_id)
            if not NAME.fullmatch(plugin_name):
                raise ValueError("Invalid plugin name.")
            state = await self._state(runtime_id)
            jar = self._target(target, plugin_name)
            installed = None
            if jar.is_file():
                installed = {"jar_path": str(jar), "artifact_sha256": await blocking(digest, jar),
                             "manifest": await blocking(_jar_manifest, jar)}
            observation = None
            if state["phase"] == "running" and self.observe_plugin is not None:
                try:
                    observation = await self.observe_plugin(runtime_id, plugin_name, timeout)
                except (OSError, ValueError, RuntimeError, TimeoutError):
                    pass  # Missing diagnostics are unknown, never a successful activation.
            current = await self._state(runtime_id)
            if (not observation or observation.get("plugin_name") != plugin_name
                    or not state["boot_id"] or state["boot_id"] != current["boot_id"]
                    or observation.get("boot_id") != current["boot_id"] or current["phase"] != "running"):
                observation = None
            return result(True, "get_plugin_status", runtime_id=runtime_id, plugin_name=plugin_name,
                          boot_id=current["boot_id"], installed=installed, observation=observation,
                          enabled=observation.get("enabled") if observation else None,
                          version=observation.get("version") if observation else None,
                          runtime_bytecode_sha256=None,
                          warnings=[] if observation else ["Exact current-boot plugin observation is unavailable."])
        except (ContractError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, yaml.YAMLError) as error:
            return result(False, "get_plugin_status", error=str(error))

    async def deploy(self, runtime_id, build_id, *, run_id=None, timeout=120):
        try:
            build = await blocking(self.get_build, build_id)
            return await self._install(runtime_id, Path(build["jar_path"]), build["artifact_sha256"],
                                       build_id=build_id, run_id=run_id, timeout=timeout)
        except (OSError, ValueError, KeyError) as error:
            return result(False, "deploy_plugin", error=str(error), files_modified=False)

    async def rollback(self, runtime_id, deployment_id, *, run_id=None, timeout=120):
        try:
            previous = await blocking(self._record, "deployments", deployment_id)
            if previous["runtime_id"] != runtime_id or not previous.get("previous_jar"):
                raise ValueError("No verified previous JAR for this runtime/deployment.")
            backup = previous["previous_jar"]
            path = _inside(self._directory("deployments", deployment_id), Path(backup["jar_path"]))
            return await self._install(runtime_id, path, backup["artifact_sha256"], run_id=run_id,
                                       rollback_of=deployment_id, timeout=timeout,
                                       expected_current=previous["artifact_sha256"])
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            return result(False, "rollback_plugin", error=str(error), files_modified=False)

    async def _install(self, runtime_id, artifact, expected, *, timeout, build_id=None,
                       run_id=None, rollback_of=None, expected_current=None):
        identifier = uuid.uuid4().hex
        stage = "rollback_plugin" if rollback_of else "deploy_plugin"
        record = result(False, stage, deployment_id=identifier, runtime_id=runtime_id,
                        build_id=build_id, run_id=run_id, rollback_of=rollback_of,
                        phase="preflight", files_modified=False, previous_jar=None,
                        execution_unknown=False, artifact_sha256=expected,
                        warnings=["JAR rollback does not restore world or database state."])
        directory = candidate = target = journal = None
        journal_before = None
        deadline = time.monotonic() + timeout

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("Deployment deadline exceeded.")
            return value

        def persist():
            _write_json(directory / "record.json", record)

        try:
            target_runtime = self.runtime.resolve(runtime_id)
            if not self.runtime.can_manage(runtime_id):
                raise ValueError("runtime_unmanaged: deployment requires an owned lifecycle service.")
            if self.observe_plugin is None:
                raise ValueError("An exact plugin diagnostic service is required.")
            manifest = await blocking(_jar_manifest, artifact)
            name = manifest["name"]
            record.update(plugin_name=name, version=manifest["version"])
            directory = self._directory("deployments", identifier)
            directory.mkdir(parents=True)
            async with self.runtime.exclusive(runtime_id) as operation:
                target = self._target(target_runtime, name)
                for installed in target.parent.glob("*.jar"):
                    if installed.resolve() == target.resolve():
                        continue
                    installed = _inside(target.parent, installed)
                    try:
                        other = await blocking(_jar_manifest, installed)
                    except (ValueError, KeyError, zipfile.BadZipFile, yaml.YAMLError):
                        continue
                    if other["name"].casefold() == name.casefold():
                        raise ValueError("Plugin exists under another JAR filename; resolve duplicate installations first.")
                if expected_current and (not target.is_file() or await blocking(digest, target) != expected_current):
                    raise ValueError("Installed JAR changed since this deployment; rollback refused.")
                frozen = directory / "candidate.jar"
                await blocking(shutil.copy2, artifact, frozen)
                if await blocking(digest, frozen) != expected:
                    raise ValueError("Artifact hash mismatch.")
                old = await self._state(runtime_id)
                if old["phase"] != "running" and not (rollback_of and old["phase"] == "stopped"):
                    raise ValueError("An owned running boot is required; only rollback may recover a stopped server.")
                record.update(previous_boot_id=old["boot_id"], phase="stopping", execution_unknown=True)
                persist()
                # A releases only this runtime's debugger and confirms its own child exited.
                stopped = await self.runtime.stop(runtime_id, timeout_seconds=min(300, remaining()), operation=operation)
                if not stopped.get("exit_confirmed") or stopped.get("pid") is not None:
                    raise ValueError("Old process exit was not confirmed; JAR unchanged.")
                if old["boot_id"] and stopped["boot_id"] != old["boot_id"]:
                    raise ValueError("A different boot was stopped; JAR unchanged.")
                record.update(previous_boot_id=stopped["boot_id"], execution_unknown=False)
                if target.is_file():
                    metadata = await blocking(_jar_manifest, target)
                    if metadata["name"] != name:
                        raise ValueError("Installed JAR belongs to a different plugin.")
                    backup = directory / "previous.jar"
                    await blocking(shutil.copy2, target, backup)
                    record["previous_jar"] = {"jar_path": str(backup), "artifact_sha256": await blocking(digest, backup),
                                              "manifest": metadata}
                remaining()
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = target.with_suffix(f".{identifier}.tmp")
                await blocking(shutil.copy2, frozen, candidate)
                if await blocking(digest, candidate) != expected:
                    raise ValueError("Staged JAR hash mismatch.")
                record["phase"] = "installing"
                persist()
                journal = _inside(target_runtime.server_dir, target_runtime.server_dir / CONTROL_DIR / "install.json")
                if journal.exists():
                    journal_before = journal.read_text(encoding="utf-8")
                journal_data = {"phase": "installing", "target": str(target), "artifact_sha256": expected,
                                "deployment_id": identifier}
                _write_json(journal, journal_data)
                # File scanners can retain sharing locks for several seconds even
                # after the owned JVM exited. Only the atomic rename is retried;
                # the server lock and the original total deadline remain in force.
                record["file_replace"] = await blocking(_replace_file, candidate, target, timeout=min(10, remaining()))
                record.update(files_modified=True, phase="starting", execution_unknown=True)
                persist()
                journal_data["phase"] = "installed"
                _write_json(journal, journal_data)
                started = await self.runtime.start(runtime_id, timeout_seconds=min(300, remaining()), operation=operation)
                record.update(phase="verifying", boot_id=started["boot_id"])
                if started["boot_id"] == stopped["boot_id"]:
                    raise ValueError("A new server boot was not observed.")
                while True:
                    status = await self.get_plugin_status(runtime_id, name, timeout=min(5, remaining()))
                    observation = status.get("observation")
                    if observation and status["boot_id"] == started["boot_id"]:
                        record["plugin_status"] = status
                        record["execution_unknown"] = False
                        if observation.get("enabled") is False:
                            raise ValueError("Plugin failed to enable in the new boot.")
                        if observation.get("enabled") is True:
                            if observation.get("version") != manifest["version"] or await blocking(digest, target) != expected:
                                raise ValueError("Installed JAR/version differs from requested deployment.")
                            record.update(ok=True, phase="verified", execution_unknown=False)
                            break
                    await asyncio.sleep(min(0.1, remaining()))
        except asyncio.CancelledError:
            record.update(errors=["Deployment cancelled; inspect process and artifact before retrying."],
                          execution_unknown=True, recovery_required=True)
            raise
        except (ContractError, OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, yaml.YAMLError) as error:
            record["errors"] = [str(error)]
            record["recovery_required"] = record["files_modified"] or record["phase"] != "preflight"
            # A definite failed rename must not strand the intact installation
            # behind our new pending journal. Restore only after checking the old
            # bytes; never restart the server or retry a lifecycle operation here.
            if record["phase"] == "installing" and not record["files_modified"] and journal is not None:
                try:
                    previous = record["previous_jar"]
                    intact = (await blocking(digest, target) == previous["artifact_sha256"]
                              if previous else not target.exists())
                    if intact:
                        if journal_before is None:
                            journal.unlink(missing_ok=True)
                        else:
                            _write_json(journal, json.loads(journal_before))
                        record["installation_preserved"] = True
                except (OSError, ValueError):
                    record["warnings"].append("Installation journal remains pending; inspect it before starting.")
        finally:
            if candidate is not None:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    record["warnings"].append("Could not remove the staged temporary file.")
            if directory is not None:
                persist()
        return record


class PluginJobs:
    """C's submission interface backed by the one B manager used by A and D."""
    def __init__(self, manager: JobManager, runtime: RuntimeService):
        self.manager = manager
        self.runtime = runtime

    async def submit(self, spec, operation):
        try:
            runtime_id = spec["runtime_id"]
            if runtime_id is not None:
                self.runtime.resolve(runtime_id)
                if not self.runtime.can_manage(runtime_id):
                    raise ValueError("runtime_unmanaged: lifecycle management is not enabled.")
            else:
                runtime_id = "build/" + spec["inputs"]["plugin_name"]
            job = await self.manager.submit_operation(kind=spec["kind"], inputs=spec["inputs"],
                runtime_id=runtime_id, request_id=spec["request_id"], run_id=spec["run_id"],
                timeout=spec.get("timeout_seconds", 180), operation=operation)
            return result(True, spec["kind"], **job.as_dict())
        except (ContractError, JobError, ValueError) as error:
            return result(False, spec["kind"], error=str(error), files_modified=False)

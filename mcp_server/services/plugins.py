"""C-owned build records and deployment orchestration; no process ownership here.

Runtime lifecycle, exclusion, debugger release and exact plugin observations are
injected. See docs/plugin-build-deploy.md for the integration contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, ContextManager, Protocol

import yaml

from mcp_server.services.deployment import digest
from mcp_server.services.file_gate import FileGate

NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
CLASS = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
ID = re.compile(r"[0-9a-f]{32}")
TEMPLATE_FILES = ("build.gradle", "settings.gradle", "gradlew", "gradlew.bat",
                  "gradle/wrapper/gradle-wrapper.jar", "gradle/wrapper/gradle-wrapper.properties")


class RuntimeService(Protocol):
    def resolve(self, runtime_id: str) -> dict: ...
    def status(self, runtime_id: str) -> dict: ...
    def exclusive(self, runtime_id: str) -> ContextManager: ...
    def stop(self, runtime_id: str, *, timeout: float) -> dict: ...
    def start(self, runtime_id: str, *, timeout: float) -> dict: ...


def result(ok: bool, stage: str, *, error: str | None = None, **values) -> dict:
    return {"schema_version": "1", "ok": ok, "stage": stage,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "errors": [error] if error else [], "warnings": [], "evidence_ids": [], **values}


def _relative(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if (not value or path.is_absolute() or PureWindowsPath(value).anchor
            or any(part in ("..", ".") for part in value.replace("\\", "/").split("/"))
            or any(char in value for char in ':<>"|?*\x00')
            or any(part.endswith((" ", ".")) or re.fullmatch(
                r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                   for part in path.parts)):
        raise ValueError(f"Invalid relative path: {value}")
    return path.as_posix()


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes its workspace: {path}")
    return resolved


def _replace_file(source: Path, target: Path, *, timeout: float = 2):
    """Retry known Windows file-sharing failures; replacement remains atomic.

    Indexers/scanners can briefly deny rename after a reader has closed a JAR.
    This retries only the local rename, never lifecycle/command operations.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if (os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def _write_json(path: Path, data: dict):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    _replace_file(temporary, path)


class _WindowsJob:
    """Keep all descendants in a kill-on-close Windows job, including Gradle daemons."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        import ctypes
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        import ctypes
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _run(command: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """Bound an owned process tree, including Gradle's single-use daemon."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    job = _WindowsJob() if os.name == "nt" else None
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", creationflags=flags,
                                   start_new_session=os.name != "nt")
        if job:
            job.assign(process)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if job:
                job.terminate()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=5)
            raise subprocess.TimeoutExpired(command, timeout, output=output)
        return subprocess.CompletedProcess(command, process.returncode, output, "")
    finally:
        if job:
            job.close()
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process and process.stdout:
            process.stdout.close()


def _jar_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("plugin.yml")
        if info.file_size > 65536:
            raise ValueError("plugin.yml exceeds 64 KiB.")
        value = yaml.safe_load(archive.read(info))
    if (not isinstance(value, dict) or not isinstance(value.get("name"), str)
            or not NAME.fullmatch(value["name"]) or not isinstance(value.get("version"), str)):
        raise ValueError("JAR must contain a named plugin with a string version.")
    return value


class PluginService:
    def __init__(self, workspace: Path, *, runtime: RuntimeService | None = None,
                 release_runtime: Callable[[str], None] | None = None,
                 observe_plugin: Callable[[str, str], dict] | None = None):
        self.workspace = workspace.resolve()
        self.template = self.workspace / "plugin_template"
        # Existing .gitignore excludes .cache; local JARs and records stay private.
        self.store = _inside(self.workspace, self.workspace / ".cache/plugins")
        self.runtime = runtime
        self.release_runtime = release_runtime
        self.observe_plugin = observe_plugin

    def _directory(self, kind: str, identifier: str) -> Path:
        if not ID.fullmatch(identifier):
            raise ValueError(f"Invalid {kind} ID.")
        return _inside(self.workspace, self.store / kind / identifier)

    def _record(self, kind: str, identifier: str) -> dict:
        return json.loads((self._directory(kind, identifier) / "record.json").read_text(encoding="utf-8"))

    def get_build(self, build_id: str) -> dict:
        record = self._record("builds", build_id)
        if not record["ok"]:
            raise ValueError("The requested build failed; it has no deployable artifact.")
        artifact = _inside(self._directory("builds", build_id), Path(record["jar_path"]))
        if digest(artifact) != record["artifact_sha256"]:
            raise ValueError("Build artifact hash mismatch.")
        return record

    def build(self, plugin_name: str, main_class: str, source_files: dict[str, str], *,
              plugin_manifest: dict | None = None, resource_files: dict[str, str] | None = None,
              project_id: str | None = None, offline: bool = True, timeout: float = 180) -> dict:
        identifier = uuid.uuid4().hex
        directory = None
        try:
            if not NAME.fullmatch(plugin_name) or not CLASS.fullmatch(main_class):
                raise ValueError("Invalid plugin name or main class.")
            if not source_files or not 0 < timeout <= 600:
                raise ValueError("Sources are required and timeout must be within 0..600 seconds.")
            sources, resources = {}, {}
            for supplied, destination, java in ((source_files, sources, True),
                                                (resource_files or {}, resources, False)):
                seen = set()
                for name, content in supplied.items():
                    name = _relative(name)
                    if name.casefold() in seen or (java and not name.endswith(".java")):
                        raise ValueError("Duplicate paths or a non-Java source.")
                    if not isinstance(content, str) or (java and not content.strip()):
                        raise ValueError("Sources/resources must contain text.")
                    if not java and name.casefold() in ("plugin.yml", "paper-plugin.yml"):
                        raise ValueError("Use plugin_manifest for the plugin descriptor.")
                    seen.add(name.casefold())
                    destination[name] = content
            manifest = dict(plugin_manifest or {})
            for field, expected in (("name", plugin_name), ("main", main_class)):
                if manifest.get(field, expected) != expected:
                    raise ValueError(f"Manifest {field} must match the build input.")
                manifest[field] = expected
            manifest.setdefault("version", "0.1.0")
            manifest.setdefault("api-version", "1.20")
            if not isinstance(manifest["version"], str) or not manifest["version"].strip():
                raise ValueError("Manifest version must be a nonempty string.")
            for key in ("commands", "permissions"):
                if key in manifest and not isinstance(manifest[key], dict):
                    raise ValueError(f"Manifest {key} must be a mapping.")
            manifest_text = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
            if len(manifest_text.encode()) > 65536:
                raise ValueError("Manifest exceeds 64 KiB.")
            payload = {"sources": sources, "resources": resources, "manifest": manifest}
            source_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            directory = self._directory("builds", identifier)
            directory.mkdir(parents=True)
            with FileGate(_inside(self.workspace, self.store / "locks" / f"{plugin_name}.lock")):
                project = directory / "project"
                project.mkdir()
                hashes = {}
                for name in TEMPLATE_FILES:
                    source = _inside(self.template, self.template / name)
                    target = project / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    hashes[name] = digest(target)
                for group, files in (("java", sources), ("resources", resources)):
                    for name, content in files.items():
                        target = project / "src/main" / group / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                (project / "src/main/resources").mkdir(parents=True, exist_ok=True)
                (project / "src/main/resources/plugin.yml").write_text(manifest_text, encoding="utf-8")
                cache = _inside(self.workspace, self.workspace / ".cache/gradle")
                command = ["java", "-cp", str(project / "gradle/wrapper/gradle-wrapper.jar"),
                           "org.gradle.wrapper.GradleWrapperMain", "--gradle-user-home", str(cache),
                           "--no-daemon", "--console=plain", "clean", "build"]
                if offline:
                    command.append("--offline")
                completed = _run(command, cwd=project, timeout=timeout)
                (directory / "build.log").write_text(completed.stdout, encoding="utf-8")
                if completed.returncode:
                    raise ValueError("Gradle build failed; inspect build.log. Use offline=False to prepare dependencies.")
                jars = list((project / "build/libs").glob("*.jar"))
                if len(jars) != 1:
                    raise ValueError("Expected exactly one fresh JAR.")
                if _jar_manifest(jars[0]) != manifest:
                    raise ValueError("Built plugin descriptor does not match the input.")
                debug = self._debug_info(jars[0], main_class)
                artifact = directory / "artifact.jar"
                shutil.copy2(jars[0], artifact)
                record = result(True, "build_plugin", build_id=identifier, project_id=project_id,
                                source_sha256=source_hash, source_revision=self._revision(),
                                plugin_name=plugin_name, version=manifest["version"],
                                jar_path=str(artifact), artifact_sha256=digest(artifact),
                                project_root=str(project), template_sha256=hashes, debug_info=debug,
                                output_path=str(directory / "build.log"), offline=offline)
                properties = (project / "gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8")
                version = re.search(r"gradle-([0-9.]+)-bin\.zip", properties)
                record["build_tools"] = {"gradle": version[1] if version else None,
                                         "java_release": 17, "debug_inspector": "javap"}
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, yaml.YAMLError,
                subprocess.TimeoutExpired) as error:
            record = result(False, "build_plugin", error=str(error), build_id=identifier)
            if directory is not None and isinstance(error, subprocess.TimeoutExpired):
                (directory / "build.log").write_text(error.output or "", encoding="utf-8")
        if directory is not None:
            record["output_path"] = str(directory / "build.log")
            _write_json(directory / "record.json", record)
        return record

    def _revision(self) -> dict:
        try:
            head = _run(["git", "rev-parse", "HEAD"], cwd=self.workspace, timeout=10)
            dirty = _run(["git", "status", "--porcelain"], cwd=self.workspace, timeout=10)
            return {"head": head.stdout.strip() if head.returncode == 0 else None,
                    "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None}
        except (OSError, subprocess.TimeoutExpired):
            return {"head": None, "dirty": None}

    def _debug_info(self, jar: Path, main_class: str) -> dict:
        # Inspect real bytecode, rather than inferring debug metadata from Gradle options.
        with zipfile.ZipFile(jar) as archive:
            classes = [name[:-6].replace("/", ".") for name in archive.namelist() if name.endswith(".class")]
        if main_class not in classes:
            raise ValueError("Main class is missing from the JAR.")
        inspected = {}
        for name in classes:
            output = _run(["javap", "-p", "-l", "-verbose", "-classpath", str(jar), name],
                          cwd=jar.parent, timeout=30)
            if output.returncode or "major version: 61" not in output.stdout:
                raise ValueError(f"Cannot verify Java 17 bytecode for {name}.")
            inspected[name] = {"lines": "LineNumberTable:" in output.stdout,
                               "locals": "LocalVariableTable:" in output.stdout}
            if "Code:" in output.stdout and not inspected[name]["lines"]:
                raise ValueError(f"Line debug information missing: {name}")
        if not all(inspected[main_class].values()):
            raise ValueError("Main class line/local variable debug information is missing.")
        return {"verified": True, "java_release": 17, "classes": inspected}

    def get_plugin_status(self, runtime_id: str, plugin_name: str) -> dict:
        try:
            runtime = self._resolve(runtime_id)
            if not NAME.fullmatch(plugin_name):
                raise ValueError("Invalid plugin name.")
            state = self.runtime.status(runtime_id)
            target = self._target(runtime, plugin_name)
            installed = None
            if target.is_file():
                installed = {"jar_path": str(target), "artifact_sha256": digest(target),
                             "manifest": _jar_manifest(target)}
            observation = self.observe_plugin(runtime_id, plugin_name) if self.observe_plugin else None
            # Observations from another boot or another exact plugin are never accepted.
            if (not observation or observation.get("plugin_name") != plugin_name
                    or not state.get("boot_id") or observation.get("boot_id") != state["boot_id"]
                    or state.get("phase") != "running"):
                observation = None
            return result(True, "get_plugin_status", runtime_id=runtime_id, plugin_name=plugin_name,
                          boot_id=state.get("boot_id"), installed=installed,
                          observation=observation, enabled=observation.get("enabled") if observation else None,
                          version=observation.get("version") if observation else None,
                          runtime_bytecode_sha256=None,
                          warnings=[] if observation else ["Exact plugin observation is unavailable for this boot."])
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, yaml.YAMLError) as error:
            return result(False, "get_plugin_status", error=str(error))

    def _resolve(self, runtime_id: str) -> dict:
        if self.runtime is None:
            raise ValueError("Runtime service is unavailable; lifecycle integration is required.")
        return self.runtime.resolve(runtime_id)

    def _target(self, runtime: dict, name: str) -> Path:
        root = Path(runtime["server_dir"]).resolve(strict=True)
        return _inside(root, root / "plugins" / f"{name}.jar")

    def deploy(self, runtime_id: str, build_id: str, *, run_id: str | None = None,
               timeout: float = 120) -> dict:
        try:
            build = self.get_build(build_id)
            return self._install(runtime_id, Path(build["jar_path"]), build["artifact_sha256"],
                                 build_id=build_id, run_id=run_id, timeout=timeout)
        except (OSError, ValueError, KeyError) as error:
            return result(False, "deploy_plugin", error=str(error), files_modified=False)

    def rollback(self, runtime_id: str, deployment_id: str, *, run_id: str | None = None,
                 timeout: float = 120) -> dict:
        try:
            previous = self._record("deployments", deployment_id)
            if previous["runtime_id"] != runtime_id or not previous.get("previous_jar"):
                raise ValueError("No verified previous JAR for this runtime/deployment.")
            backup = previous["previous_jar"]
            path = _inside(self._directory("deployments", deployment_id), Path(backup["jar_path"]))
            # Refuse to overwrite a later deployment, or an externally modified installation.
            return self._install(runtime_id, path, backup["artifact_sha256"], run_id=run_id,
                                 rollback_of=deployment_id, timeout=timeout,
                                 expected_current=previous["artifact_sha256"])
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            return result(False, "rollback_plugin", error=str(error), files_modified=False)

    def _install(self, runtime_id: str, artifact: Path, expected: str, *, timeout: float,
                 build_id: str | None = None, run_id: str | None = None,
                 rollback_of: str | None = None, expected_current: str | None = None) -> dict:
        identifier = uuid.uuid4().hex
        stage = "rollback_plugin" if rollback_of else "deploy_plugin"
        record = result(False, stage, deployment_id=identifier, runtime_id=runtime_id,
                        build_id=build_id, run_id=run_id, rollback_of=rollback_of,
                        phase="preflight", files_modified=False, previous_jar=None,
                        execution_unknown=False, artifact_sha256=expected,
                        warnings=["JAR rollback does not restore world or database state."])
        directory = None
        candidate = None
        try:
            if not 0 < timeout <= 600:
                raise ValueError("Timeout must be within 0..600 seconds.")
            deadline = time.monotonic() + timeout

            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise TimeoutError("Deployment deadline exceeded.")
                return value

            runtime = self._resolve(runtime_id)
            if not runtime.get("managed"):
                raise ValueError("unmanaged_runtime: deployment requires an owned lifecycle service.")
            if self.release_runtime is None or self.observe_plugin is None:
                raise ValueError("Debugger release and exact plugin observation services are required.")
            manifest = _jar_manifest(artifact)
            name = manifest["name"]
            record.update(plugin_name=name, version=manifest["version"])
            directory = self._directory("deployments", identifier)
            directory.mkdir(parents=True)
            with self.runtime.exclusive(runtime_id):
                target = self._target(runtime, name)
                for installed in target.parent.glob("*.jar"):
                    if installed.resolve() == target.resolve():
                        continue
                    installed = _inside(target.parent, installed)
                    try:
                        other = _jar_manifest(installed)
                    except (ValueError, KeyError, zipfile.BadZipFile, yaml.YAMLError):
                        continue
                    if other["name"].casefold() == name.casefold():
                        raise ValueError("Plugin exists under another JAR filename; resolve duplicate installations first.")
                if expected_current and (not target.is_file() or digest(target) != expected_current):
                    raise ValueError("Installed JAR changed since this deployment; rollback refused.")
                # Freeze input before stopping; re-check the exact bytes we will install.
                frozen = directory / "candidate.jar"
                shutil.copy2(artifact, frozen)
                if digest(frozen) != expected:
                    raise ValueError("Artifact hash mismatch.")
                old = self.runtime.status(runtime_id)
                already_stopped = (rollback_of is not None and old.get("phase") == "stopped"
                                   and old.get("pid") is None)
                if (old.get("phase") != "running" and not already_stopped) or not old.get("boot_id"):
                    raise ValueError("An observed boot is required; only rollback may recover a stopped server.")
                record["previous_boot_id"] = old["boot_id"]
                _write_json(directory / "record.json", record)
                self.release_runtime(runtime_id)
                record.update(phase="stopping", execution_unknown=True)
                _write_json(directory / "record.json", record)
                if not already_stopped:
                    self.runtime.stop(runtime_id, timeout=remaining())
                stopped = self.runtime.status(runtime_id)
                if (stopped.get("phase") != "stopped" or stopped.get("pid") is not None
                        or stopped.get("boot_id") != old["boot_id"]):
                    raise ValueError("Old process exit was not confirmed; JAR unchanged.")
                record["execution_unknown"] = False
                if target.is_file():
                    metadata = _jar_manifest(target)
                    if metadata["name"] != name:
                        raise ValueError("Installed JAR belongs to a different plugin.")
                    backup = directory / "previous.jar"
                    shutil.copy2(target, backup)
                    record["previous_jar"] = {"jar_path": str(backup), "artifact_sha256": digest(backup),
                                              "manifest": metadata}
                remaining()
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = target.with_suffix(f".{identifier}.tmp")
                shutil.copy2(frozen, candidate)
                if digest(candidate) != expected:
                    raise ValueError("Staged JAR hash mismatch.")
                record["phase"] = "installing"
                _write_json(directory / "record.json", record)
                _replace_file(candidate, target, timeout=min(2, remaining()))
                record.update(files_modified=True, phase="starting", execution_unknown=True)
                _write_json(directory / "record.json", record)
                self.runtime.start(runtime_id, timeout=remaining())
                record["phase"] = "verifying"
                while True:
                    remaining()
                    state = self.runtime.status(runtime_id)
                    record["boot_id"] = state.get("boot_id")
                    status = self.get_plugin_status(runtime_id, name)
                    observation = status.get("observation")
                    if (state.get("phase") == "running" and state.get("boot_id")
                            and state["boot_id"] != old["boot_id"] and observation
                            and observation.get("boot_id") == state["boot_id"]):
                        record["plugin_status"] = status
                        if observation.get("enabled") is False:
                            raise ValueError("Plugin failed to enable in the new boot.")
                        if observation.get("enabled") is True:
                            if observation.get("version") != manifest["version"] or digest(target) != expected:
                                raise ValueError("Installed JAR/version differs from requested deployment.")
                            record.update(ok=True, phase="verified", execution_unknown=False)
                            break
                    time.sleep(min(0.1, remaining()))
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, yaml.YAMLError) as error:
            record["errors"] = [str(error)]
            record["recovery_required"] = record["files_modified"] or record["phase"] != "preflight"
        finally:
            if candidate is not None:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    record["warnings"].append("Could not remove the staged temporary file.")
        if directory is not None:
            _write_json(directory / "record.json", record)
        return record

"""Trusted runtime registry, source containment, evidence, and helper lifetime."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from importlib.metadata import version
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mcp_server.adapters.debugger import DebuggerError, HelperClient
from mcp_server.adapters.debugger.models import integer, require, string, validate_params, validate_result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_hash(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@dataclass(frozen=True)
class Project:
    root: Path
    artifact_jar: Path | None = None
    source_revision: str | None = None


@dataclass(frozen=True)
class Runtime:
    project_id: str
    host: str
    port: int
    jar: Path
    boot_id: str | None = None
    server_version: str | None = None


@dataclass(frozen=True)
class DebuggerConfig:
    workspace: Path
    helper_jar: Path
    java_executable: str
    projects: dict[str, Project]
    runtimes: dict[str, Runtime]
    evidence_dir: Path
    ready_timeout: float = 5
    request_timeout: float = 10
    shutdown_timeout: float = 5
    connect_timeout_ms: int = 5000
    stop_timeout_ms: int = 10000

    @classmethod
    def load(cls, path: Path, *, workspace: Path | None = None) -> DebuggerConfig:
        workspace = (workspace or Path.cwd()).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            require(isinstance(data, dict), "Configuration must be an object.", "invalid_config")
            allowed = {"helper_jar", "java_executable", "projects", "runtimes", "evidence_dir", "ready_timeout",
                       "request_timeout", "shutdown_timeout", "connect_timeout_ms", "stop_timeout_ms"}
            require(data.keys() <= allowed, "Unknown configuration fields.", "invalid_config")

            def resolve(value: str) -> Path:
                require(string(value), "Configuration paths must be strings.", "invalid_config")
                candidate = Path(value)
                return (candidate if candidate.is_absolute() else workspace / candidate).resolve()

            require(isinstance(data.get("projects"), dict) and isinstance(data.get("runtimes"), dict),
                    "Projects and runtimes must be registries.", "invalid_config")
            projects = {}
            for key, item in data["projects"].items():
                require(string(key) and isinstance(item, dict) and item.keys() <= {"root", "artifact_jar", "source_revision"},
                        "Invalid project configuration.", "invalid_config")
                root = resolve(item["root"])
                require(root.is_dir() and within(root, workspace), "Project root must exist inside the workspace.", "invalid_config")
                artifact = resolve(item["artifact_jar"]) if item.get("artifact_jar") else None
                require(item.get("source_revision") is None or string(item["source_revision"]), "Invalid revision.", "invalid_config")
                projects[key] = Project(root, artifact, item.get("source_revision"))
            runtimes = {}
            for key, item in data["runtimes"].items():
                require(string(key) and isinstance(item, dict) and item.keys() <= {"project_id", "host", "port", "jar", "boot_id", "server_version"},
                        "Invalid runtime configuration.", "invalid_config")
                require(item["project_id"] in projects and string(item["host"]) and integer(item["port"], 1, 65535),
                        "Invalid runtime target.", "invalid_config")
                jar = resolve(item["jar"])
                require(all(item.get(k) is None or string(item[k]) for k in ("boot_id", "server_version")),
                        "Invalid runtime metadata.", "invalid_config")
                runtimes[key] = Runtime(item["project_id"], item["host"], item["port"], jar,
                                        item.get("boot_id"), item.get("server_version"))
            evidence = resolve(data.get("evidence_dir", "data/debugger"))
            require(within(evidence, workspace), "Evidence directory must be inside the workspace.", "invalid_config")
            helper = resolve(data["helper_jar"])
            java = data.get("java_executable", "java")
            require(string(java), "Invalid Java executable.", "invalid_config")
            timeouts = {}
            for key, default in (("ready_timeout", 5), ("request_timeout", 10), ("shutdown_timeout", 5)):
                value = data.get(key, default)
                require(type(value) in (int, float) and 0 < value <= 300, "Invalid Python timeout.", "invalid_config")
                timeouts[key] = value
            for key, default in (("connect_timeout_ms", 5000), ("stop_timeout_ms", 10000)):
                value = data.get(key, default)
                require(integer(value, 1, 300000), "Invalid helper timeout.", "invalid_config")
                timeouts[key] = value
            require(timeouts["request_timeout"] * 1000 > timeouts["connect_timeout_ms"],
                    "Request timeout must exceed attach connect timeout.", "invalid_config")
            return cls(workspace, helper, java, projects, runtimes, evidence, **timeouts)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise DebuggerError("invalid_config", "Cannot read debugger configuration.") from exc


class EvidenceStore:
    def __init__(self, directory: Path, *, simulated: bool = False):
        self.run_id = uuid.uuid4().hex
        self.directory = directory / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "evidence.jsonl"
        self.simulated = simulated
        self.context: dict = {}
        self.session_contexts: dict[str, dict] = {}
        self.request_contexts: dict[str, dict] = {}
        self.write("run_started", {"protocol_version": 1, "simulated": simulated})

    def write(self, kind: str, payload: dict) -> None:
        sid = payload.get("session_id") or payload.get("params", {}).get("session_id")
        context = self.session_contexts.get(sid, self.context)
        if kind == "request":
            self.request_contexts[payload["id"]] = context.copy()
            if len(self.request_contexts) > 4096:
                self.request_contexts.pop(next(iter(self.request_contexts)))
        elif kind in ("response", "late_response"):
            context = self.request_contexts.pop(payload["id"], context)
        record = {"run_id": self.run_id, "recorded_at": utc_now(), "kind": kind,
                  "simulated": self.simulated, "context": context, "payload": payload}
        # Each call is synchronous and made on the event loop; complete JSON lines cannot interleave.
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as destination:
                destination.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        except OSError as exc:
            raise DebuggerError("evidence_unavailable", "Cannot persist debugger evidence.") from exc


def project_revision(project: Project) -> dict:
    """Read HEAD without launching Git children that could inherit MCP stdio handles."""
    if project.source_revision:
        return {"revision": project.source_revision, "origin": "configured", "dirty": None}
    try:
        for directory in (project.root, *project.root.parents):
            git_dir = directory / ".git"
            if git_dir.is_file():
                entry = git_dir.read_text(encoding="utf-8").strip()
                if not entry.startswith("gitdir: "):
                    continue
                git_dir = (directory / entry[8:]).resolve()
            if not git_dir.is_dir():
                continue
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                ref = head[5:]
                common_dir = git_dir
                if (git_dir / "commondir").is_file():
                    common_dir = (git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()).resolve()
                loose = common_dir / ref
                if loose.is_file():
                    head = loose.read_text(encoding="utf-8").strip()
                else:
                    head = next(line.split(" ")[0] for line in (common_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
                                if line.endswith(" " + ref))
            if len(head) in (40, 64) and all(c in "0123456789abcdef" for c in head):
                return {"revision": head, "origin": "git_head", "dirty": None}
    except (OSError, StopIteration):
        pass
    return {"revision": None, "origin": "unavailable", "dirty": None}


async def java_version(executable: str) -> str:
    """Bound metadata capture and isolate it from MCP's input/output handles."""
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(executable, "-version", stdin=asyncio.subprocess.DEVNULL,
                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=flags)
        capture = asyncio.create_task(process.communicate())
        try:
            out, err = await asyncio.wait_for(asyncio.shield(capture), 3)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await asyncio.wait_for(capture, 1)
            except asyncio.TimeoutError:
                pass
            return "unverified"
        return (err or out).decode("utf-8", errors="replace").strip()[:4096] if process.returncode == 0 else "unverified"
    except OSError:
        return "unverified"


class DebuggerService:
    def __init__(self, config: DebuggerConfig, *, client_factory: Callable | None = None,
                 simulated: bool = False):
        self.config = config
        self.evidence = EvidenceStore(config.evidence_dir, simulated=simulated)
        self.simulated = simulated
        self._factory = client_factory or HelperClient
        self._client: HelperClient | None = None
        self._sessions: dict[str, tuple[str, str]] = {}
        self._attach_context: dict | None = None
        self._attach_requests: dict[str, dict] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False

    def _record_backend(self, kind: str, payload: dict) -> None:
        self.evidence.write(kind, payload)
        if kind == "request" and payload["method"] == "attach_debugger":
            self._attach_requests[payload["id"]] = self._attach_context.copy()
            if len(self._attach_requests) > 4096:
                self._attach_requests.pop(next(iter(self._attach_requests)))
        elif kind in ("response", "late_response") and payload["id"] in self._attach_requests:
            context = self._attach_requests.pop(payload["id"])
            if payload["ok"]:
                validate_result("attach_debugger", payload["result"])
                sid = payload["result"]["session_id"]
                self._sessions[sid] = (context["runtime_id"], context["project_id"])
                context.update(session_id=sid, helper_version=self._client.helper_version)
                self.evidence.context = context
                self.evidence.session_contexts[sid] = context
                self.evidence.write("late_session_bound" if kind == "late_response" else "session_bound", context)

    async def _backend(self) -> HelperClient:
        if self._closed:
            raise DebuggerError("backend_unavailable", "Debugger service is closed.")
        if self._client is None:
            if not self.simulated and not self.config.helper_jar.is_file():
                raise DebuggerError("backend_unavailable", "Build A's Java helper JAR first.")
            self._client = self._factory(
                [self.config.java_executable, "--add-modules", "jdk.jdi", "-jar", str(self.config.helper_jar)],
                ready_timeout=self.config.ready_timeout, request_timeout=self.config.request_timeout,
                shutdown_timeout=self.config.shutdown_timeout, record=self._record_backend)
        await self._client.start()
        return self._client

    async def attach_debugger(self, runtime_id: str, project_id: str) -> dict:
        async with self._lifecycle_lock:
            runtime = self.config.runtimes.get(runtime_id)
            project = self.config.projects.get(project_id)
            require(runtime is not None and project is not None and runtime.project_id == project_id,
                    "Unknown or mismatched runtime_id/project_id.", "invalid_params")
            require(self.simulated or runtime.jar.is_file(), "Configured runtime JAR is missing.", "invalid_params")
            # Source and artifact identifiers are observations, not proof of what's loaded in the JVM.
            context = {"runtime_id": runtime_id, "project_id": project_id, "runtime_boot_id": runtime.boot_id,
                       "runtime_jar": str(runtime.jar), "server_version": runtime.server_version,
                       "project_root": str(project.root), "source_match": "unverified",
                       "python_version": platform.python_version(), "fastmcp_version": version("fastmcp")}
            self.evidence.write("metadata_started", {"runtime_id": runtime_id, "project_id": project_id})
            context["source"] = await asyncio.to_thread(project_revision, project)
            context["runtime_jar_sha256"] = await asyncio.to_thread(file_hash, runtime.jar)
            context["artifact_jar_sha256"] = await asyncio.to_thread(file_hash, project.artifact_jar)
            context["helper_jar_sha256"] = None if self.simulated else await asyncio.to_thread(file_hash, self.config.helper_jar)
            context["java_version"] = await java_version(self.config.java_executable) if not self.simulated else "not used (simulated)"
            self.evidence.write("metadata_finished", context)
            backend = await self._backend()
            self._attach_context = context
            self.evidence.context = context
            self.evidence.write("attach_target", context)
            result = await backend.request("attach_debugger", {
                "host": runtime.host, "port": runtime.port,
                "connect_timeout_ms": self.config.connect_timeout_ms,
                "stop_timeout_ms": self.config.stop_timeout_ms})
            # Session routing is bound by the response recorder, including late attach responses.
            return result

    async def call(self, method: str, params: dict) -> dict:
        validate_params(method, params)
        require(method not in ("attach_debugger", "shutdown"), "Use the service lifecycle entry point.", "unsupported_method")
        sid = params["session_id"]
        require(sid in self._sessions, "Session is not owned by this service.", "session_not_found")
        if method == "set_breakpoint" and "source_path" in params:
            _, project_id = self._sessions[sid]
            root = self.config.projects[project_id].root
            supplied = Path(params["source_path"])
            source = (supplied if supplied.is_absolute() else root / supplied).resolve()
            require(within(source, root) and source.is_file(), "Source must be an existing file inside the registered project.", "invalid_params")
            self.evidence.write("source_observed", {"session_id": sid, "source_path": str(source),
                                                   "sha256": await asyncio.to_thread(file_hash, source)})
            params = {**params, "source_path": str(source)}
        backend = await self._backend()
        if method == "detach_debugger":
            async with self._lifecycle_lock:
                result = await backend.request(method, params)
                # Keep historical routing: Java decides vm_disconnected/stale_reference after detach.
                return result
        return await backend.request(method, params)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._client:
                await self._client.close()
        finally:
            self.evidence.write("service_closed", {})

    async def invoke(self, method: str, params: dict) -> dict:
        """MCP result envelope preserves machine-readable helper/local error codes."""
        self.evidence.write("tool_invoked", {"method": method, "params": params})
        try:
            result = await (self.attach_debugger(**params) if method == "attach_debugger" else self.call(method, params))
            return {"ok": True, "result": result,
                    "evidence": {"run_id": self.evidence.run_id, "path": str(self.evidence.path), "simulated": self.simulated}}
        except DebuggerError as exc:
            self.evidence.write("tool_error", {"method": method, "error": exc.as_dict()})
            return {"ok": False, "error": exc.as_dict(),
                    "evidence": {"run_id": self.evidence.run_id, "path": str(self.evidence.path), "simulated": self.simulated}}

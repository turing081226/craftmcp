"""Deploy to an observed Paper runner generation while holding its restart gate."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable

import yaml

from mcp_server.paper_runner import CONTROL_DIR, read_state
from mcp_server.services.file_gate import FileGate


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def install_artifact(candidate: Path, target: Path) -> None:
    with candidate.open("rb") as source, target.open("wb") as output:
        shutil.copyfileobj(source, output)
        output.flush()
        os.fsync(output.fileno())


def plugin_in_list(output: str, name: str) -> bool:
    output = re.sub(r"\x1b\[[0-9;]*m|§.", "", output)
    section = False
    for line in output.splitlines():
        match = re.search(r"\bPlugins\s*\(\d+\)\s*:\s*(.*)", line, re.IGNORECASE)
        if match:
            section = True
            values = match[1]
        elif re.fullmatch(r"\s*(?:Bukkit|Paper) Plugins:\s*", line, re.IGNORECASE):
            section = True
            continue
        elif section and (bullet := re.fullmatch(r"\s*-\s+(.*)", line)):
            values = bullet[1]
        else:
            if line.strip():
                section = False
            continue
        if any(item.strip().casefold() == name.casefold() for item in values.split(",")):
            return True
    return False


def log_status(text: str, name: str, version: str) -> tuple[bool, list[str]]:
    enabled = False
    prefix = rf"\[{re.escape(name)}\]\s+"
    enable = re.compile(prefix + rf"Enabling\s+{re.escape(name)}\s+v{re.escape(version)}(?:\s|$)")
    disable = re.compile(prefix + rf"Disabling\s+{re.escape(name)}(?:\s|$)")
    errors = []
    for line in text.splitlines():
        if enable.search(line):
            enabled = True
        if disable.search(line):
            enabled = False
        if re.search(r"\bERROR\b|\bException\b|\bCaused by:", line):
            errors.append(line)
    return enabled, errors


def _deploy(
    name: str, artifact: Path, server_dir: Path, *, rcon: Callable[[str], str],
    prepare_stop: Callable[[], None], timeout: float = 120,
) -> dict:
    server_dir = server_dir.resolve(strict=True)
    control = server_dir / CONTROL_DIR
    details = {"phase": "preflight", "backup_path": None, "files_modified": False,
               "stop_requested": False, "execution_unknown": False}
    staging = None
    candidate = None
    deadline = time.monotonic() + timeout

    def state_for(runner_id):
        state = read_state(server_dir)
        if state["runner_id"] != runner_id:
            raise RuntimeError("The Paper runner changed during deployment.")
        return state

    try:
        with FileGate(control / "deploy.lock"):
            old = read_state(server_dir)
            if old["phase"] != "running":
                raise RuntimeError("The managed server is not running.")
            details.update(previous_boot_id=old["boot_id"], runner_id=old["runner_id"])
            with zipfile.ZipFile(artifact) as archive:
                info = archive.getinfo("plugin.yml")
                if info.file_size > 65536:
                    raise ValueError("plugin.yml is too large.")
                metadata = yaml.safe_load(archive.read(info))
            if (not isinstance(metadata, dict) or metadata.get("name") != name
                    or not isinstance(metadata.get("version"), str)):
                raise ValueError("The artifact plugin.yml must match the exact plugin name and a string version.")
            plugins = server_dir / "plugins"
            if not plugins.resolve().is_relative_to(server_dir):
                raise ValueError("The plugins directory must be inside the managed server.")
            target = plugins / f"{name}.jar"
            if target.is_symlink() or not target.resolve().is_relative_to(plugins.resolve()):
                raise ValueError("The target JAR must be inside the plugins directory.")
            staging = control / "deployments" / uuid.uuid4().hex
            staging.mkdir(parents=True)
            plugins.mkdir(exist_ok=True)
            candidate = plugins / f".{name}.{staging.name}.tmp"
            shutil.copy2(artifact, candidate)
            expected = digest(candidate)
            details["artifact_sha256"] = expected
            if target.is_file():
                backup = staging / "previous.jar"
                shutil.copy2(target, backup)
                details["backup_path"] = str(backup)
            rcon("list")  # Authenticate before any stop request or target change.
            prepare_stop()
            details.update(phase="stopping", stop_requested=True, execution_unknown=True)
            try:
                rcon("stop")
            except (OSError, ValueError, RuntimeError) as error:
                # EOF is common during stop; observe the old JVM's exit, never resend.
                details.update(execution_unknown=True, stop_error=str(error))
            while True:
                current = state_for(old["runner_id"])
                if current["boot_id"] != old["boot_id"]:
                    raise RuntimeError("The server restarted before the deployment gate was acquired.")
                if current["phase"] == "stopped" and current["pid"] is None:
                    details["execution_unknown"] = False
                    break
                if current["phase"] == "closed" or time.monotonic() >= deadline:
                    raise RuntimeError("Could not confirm the old server process exited; the target JAR was not changed.")
                time.sleep(0.1)
            details["phase"] = "installing"
            plugins.mkdir(exist_ok=True)
            # The durable journal prevents the runner starting a partial artifact if
            # this process exits during an in-place Windows write or finalization.
            journal = control / "install.json"
            details["install_journal"] = str(journal)
            record = {"phase": "installing", "target": str(target), "backup_path": details["backup_path"],
                      "artifact_sha256": expected}

            def commit_journal():
                with journal.open("w", encoding="utf-8") as output:
                    json.dump(record, output)
                    output.flush()
                    os.fsync(output.fileno())

            commit_journal()
            details["files_modified"] = True
            install_artifact(candidate, target)
            if digest(target) != expected:
                raise RuntimeError("Installed artifact hash mismatch; the runner is blocked for recovery.")
            record["phase"] = "installed"
            commit_journal()
            details["phase"] = "restarting"
        # Releasing the gate allows the same runner and Java/JDWP profile to restart.
        while time.monotonic() < deadline:
            current = state_for(old["runner_id"])
            if current["phase"] == "closed":
                raise RuntimeError("The Paper runner closed before restart verification.")
            if current["boot_id"] != old["boot_id"] and current["phase"] == "running":
                details["boot_id"] = current["boot_id"]
                details["phase"] = "verifying"
                log = control / "boots" / f"{current['boot_id']}.log"
                text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
                enabled, errors = log_status(text, name, metadata["version"])
                if errors:
                    return {"ok": False, "errors": errors, "raw_output": text, **details}
                ready = re.search(r'\bDone \([^)]+\)!', text)
                if not ready:
                    time.sleep(0.2)
                    continue
                try:
                    output = rcon("plugins")
                except OSError:
                    time.sleep(0.2)
                    continue
                if not enabled or not plugin_in_list(output, name):
                    return {"ok": False, "errors": ["The exact plugin and version were not enabled in the new boot."],
                            "raw_output": text, "rcon_output": output, **details}
                if digest(target) != expected:
                    raise RuntimeError("The installed JAR changed during restart.")
                details["phase"] = "verified"
                return {"ok": True, "errors": [], "raw_output": text, "rcon_output": output, **details}
            time.sleep(0.2)
        raise RuntimeError("Could not verify the exact plugin, enabled version, and new server boot before the deadline.")
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, yaml.YAMLError) as error:
        return {"ok": False, "errors": [str(error)], "raw_output": "",
                "recovery_required": details["phase"] == "installing" and details["files_modified"], **details}
    finally:
        if candidate is not None:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass  # Retain an uninstalled .tmp file if the filesystem denies cleanup.


def deploy(name: str, artifact: Path, server_dir: Path, **kwargs) -> dict:
    try:
        with FileGate(server_dir / CONTROL_DIR / "operation.lock"):
            return _deploy(name, artifact, server_dir, **kwargs)
    except (OSError, ValueError) as error:
        return {"ok": False, "errors": [str(error)], "phase": "preflight", "raw_output": "",
                "files_modified": False, "backup_path": None, "execution_unknown": False}

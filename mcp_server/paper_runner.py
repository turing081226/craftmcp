"""Run a local Paper JVM with a deployment gate and observable restart generations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from mcp_server.services.file_gate import FileGate

CONTROL_DIR = ".craftmcp"


def installation_ready(server_dir: Path) -> bool:
    journal = server_dir / CONTROL_DIR / "install.json"
    if not journal.exists():
        return True
    try:
        record = json.loads(journal.read_text(encoding="utf-8"))
        target = Path(record["target"]).resolve()
        if record["phase"] != "installed" or not target.is_relative_to((server_dir / "plugins").resolve()):
            return False
        with target.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest() == record["artifact_sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_state(server_dir: Path) -> dict:
    try:
        control = server_dir / CONTROL_DIR
        if not all((control / name).is_file() for name in ("state.json", "runner.lock", "state.lock")):
            raise RuntimeError("No initialized managed runner exists. Start with python -m mcp_server.paper_runner.")
        try:
            with FileGate(server_dir / CONTROL_DIR / "runner.lock"):
                raise RuntimeError("No managed runner owns this server. Start with python -m mcp_server.paper_runner.")
        except BlockingIOError:
            pass
        until = time.monotonic() + 3
        while True:
            try:
                with FileGate(server_dir / CONTROL_DIR / "state.lock"):
                    state = json.loads((server_dir / CONTROL_DIR / "state.json").read_text(encoding="utf-8"))
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= until:
                    raise
                time.sleep(0.02)
        if (state.get("server_dir") != str(server_dir.resolve())
                or not 0 <= time.time() - state["heartbeat"] < 10
                or state.get("phase") not in {"starting", "running", "stopped", "closed"}
                or uuid.UUID(state["runner_id"]).hex != state["runner_id"]
                or uuid.UUID(state["boot_id"]).hex != state["boot_id"]):
            raise ValueError("The managed runner state is stale or invalid.")
        return state
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise RuntimeError(f"Managed runner unavailable: {error}. Start with python -m mcp_server.paper_runner before deploying.") from error


def run(server_dir: Path, command: list[str], *, restart_delay: float = 5,
        shutdown: Callable[[], bool] = lambda: False) -> None:
    server_dir = server_dir.resolve(strict=True)
    control = server_dir / CONTROL_DIR
    control.mkdir(exist_ok=True)
    state = {"server_dir": str(server_dir), "runner_id": uuid.uuid4().hex,
             "runner_pid": os.getpid(), "command": command, "boot_id": uuid.uuid4().hex,
             "phase": "stopped", "pid": None}
    destination = control / "state.json"

    def write():
        until = time.monotonic() + 3
        while True:
            try:
                with FileGate(control / "state.lock"):
                    state["heartbeat"] = time.time()
                    destination.write_text(json.dumps(state), encoding="utf-8")
                return
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise
                time.sleep(0.02)

    process = None
    with FileGate(control / "runner.lock"):
        try:
            while not shutdown():
                state.update(phase="stopped", pid=None)
                write()
                while True:
                    if shutdown():
                        return
                    try:
                        with FileGate(control / "deploy.lock"):
                            if not installation_ready(server_dir):
                                state.update(phase="stopped", blocked_reason="incomplete_install")
                                write()
                                raise BlockingIOError("An incomplete installation requires recovery before restart.")
                            state.pop("blocked_reason", None)
                            # A deployment holds this gate until the old JVM is reaped
                            # and the replacement has been fully written and verified.
                            state.update(boot_id=uuid.uuid4().hex, phase="starting")
                            write()
                            boot_log = control / "boots" / f"{state['boot_id']}.log"
                            boot_log.parent.mkdir(exist_ok=True)
                            state["console_log"] = str(boot_log)
                            process = subprocess.Popen(command, cwd=server_dir, stdout=subprocess.PIPE,
                                                       stderr=subprocess.STDOUT)

                            def capture(child=process, destination=boot_log):
                                with destination.open("wb") as output:
                                    for line in iter(child.stdout.readline, b""):
                                        output.write(line)
                                        output.flush()
                                        try:
                                            sys.stdout.buffer.write(line)
                                            sys.stdout.buffer.flush()
                                        except (OSError, AttributeError):
                                            pass
                                child.stdout.close()

                            reader = threading.Thread(target=capture, daemon=True)
                            reader.start()
                            state.update(phase="running", pid=process.pid)
                            write()
                        break
                    except BlockingIOError:
                        write()
                        time.sleep(0.1)
                while process.poll() is None and not shutdown():
                    write()
                    time.sleep(0.2)
                if shutdown():
                    return
                reader.join(timeout=5)
                state.update(phase="stopped", pid=None, exit_code=process.returncode)
                write()
                until = time.monotonic() + restart_delay
                while time.monotonic() < until and not shutdown():
                    write()
                    time.sleep(0.2)
        finally:
            if process and process.poll() is None:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            state.update(phase="closed", pid=None)
            write()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-dir", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--jdwp-port", type=int, default=5005)
    args = parser.parse_args()
    if not (args.server_dir / "paper.jar").is_file():
        parser.error("The server directory must contain paper.jar.")
    command = [args.java, "-Xms1G", "-Xmx2G"]
    if args.debug:
        if not 1 <= args.jdwp_port <= 65535:
            parser.error("JDWP port must be between 1 and 65535.")
        command.append(f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:{args.jdwp_port}")
    command.extend(["-jar", "paper.jar", "--nogui"])
    try:
        run(args.server_dir, command)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

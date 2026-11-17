"""Capture evidence and caller-supplied visual assessments, separate from game assertions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.adapters.client_capture import CaptureError, WindowsClientCapture

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
RESERVED = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])")
MAX_PNG_BYTES = 8 * 1024 * 1024


def response(**values):
    return {"schema_version": "1", "ok": True, "observed_at": datetime.now(timezone.utc).isoformat(),
            "warnings": [], **values}


class ClientCaptureService:
    def __init__(self, workspace: Path, *, backend=None):
        self.workspace = workspace.resolve()
        self.store = self._inside(self.workspace / ".cache/client-captures")
        self.backend = backend
        self.clients = {}
        self._clients_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def _inside(self, path: Path) -> Path:
        if not path.resolve().is_relative_to(self.workspace) or path.is_symlink():
            raise CaptureError("unsafe_evidence_path", "Capture evidence must remain inside the workspace.")
        return path.resolve()

    def _identifier(self, value):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or RESERVED.fullmatch(value):
            raise CaptureError("invalid_id", "IDs must be 1..64 ASCII letters/digits/underscores/hyphens, without reserved Windows names.")
        return value

    def _run_directory(self, run_id):
        return self._inside(self.store / self._identifier(run_id))

    def _backend(self):
        if self.backend is None:
            self.backend = WindowsClientCapture()
        return self.backend

    def list_clients(self):
        discovered = self._backend().discover()
        clients, items = {}, []
        for client in discovered:
            identifier = hashlib.sha256(repr(client.identity).encode()).hexdigest()[:24]
            clients[identifier] = client
            items.append({"client_id": identifier, "title": client.title, "pid": client.pid,
                          "window_class": client.window_class, "executable": client.executable})
        with self._clients_lock:
            self.clients = clients
        return response(clients=items, warnings=["Select the intended Minecraft Java window; Java OpenGL windows are candidates, not server identity proof."])

    def _write_record(self, directory, identifier, record):
        path = self._inside(directory / f"{identifier}.json")
        temporary = self._inside(directory / f"{identifier}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _capacity(self, directory):
        # Per-run records are intentionally bounded for evidence pagination.
        if directory.is_dir() and sum(1 for _ in directory.glob("*.json")) >= 1000:
            raise CaptureError("run_evidence_full", "Use a new run_id after 1000 visual evidence records.")

    def capture(self, client_id, run_id, label, *, runtime_id=None, build_id=None, deployment_id=None):
        directory = self._run_directory(run_id)
        self._identifier(client_id)
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 200:
            raise CaptureError("invalid_label", "A label of 1..200 characters is required.")
        references = {"runtime_id": runtime_id, "build_id": build_id, "deployment_id": deployment_id}
        for identifier in references.values():
            if identifier is not None:
                self._identifier(identifier)
        with self._clients_lock:
            client = self.clients.get(client_id)
        if client is None:
            raise CaptureError("unknown_client", "Call list_game_clients and select a current client_id first.")
        if not self._operation_lock.acquire(blocking=False):
            raise CaptureError("capture_busy", "Another capture/evidence operation is in progress.")
        try:
            self._capacity(directory)
            backend = self._backend()
            frame = backend.capture(client)
            if (not frame.png.startswith(b"\x89PNG\r\n\x1a\n") or len(frame.png) > MAX_PNG_BYTES
                    or not 0 < frame.width <= 1920 or not 0 < frame.height <= 1080):
                raise CaptureError("invalid_frame", "Capture backend returned an invalid/oversized frame.")
            identifier = uuid.uuid4().hex
            directory.mkdir(parents=True, exist_ok=True)
            path = self._inside(directory / f"{identifier}.png")
            record = response(kind="capture", evidence_id=identifier, capture_id=identifier, run_id=run_id,
                              client_id=client_id, label=label.strip(), window_title=client.title,
                              image_path=str(path), image_sha256=hashlib.sha256(frame.png).hexdigest(),
                              width=frame.width, height=frame.height, source_rect=list(frame.source_rect),
                              capture_method="simulated" if getattr(backend, "simulated", True) else "desktop_client_area",
                              simulated=bool(getattr(backend, "simulated", True)), warnings=list(frame.warnings),
                              functional_result="unknown", correlation_verified=False, **references)
            try:
                with path.open("xb") as output:
                    output.write(frame.png)
                    output.flush()
                    os.fsync(output.fileno())
                self._write_record(directory, identifier, record)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            return record, frame.png
        finally:
            self._operation_lock.release()

    def read_capture(self, run_id, capture_id):
        directory = self._run_directory(run_id)
        self._identifier(capture_id)
        metadata = self._inside(directory / f"{capture_id}.json")
        if not metadata.is_file():
            raise CaptureError("capture_not_found", "No capture exists for this run_id/capture_id.")
        if metadata.stat().st_size > 65536:
            raise CaptureError("invalid_evidence", "Capture metadata is oversized.")
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if record.get("kind") != "capture" or record.get("run_id") != run_id or record.get("capture_id") != capture_id:
            raise CaptureError("invalid_evidence", "Capture metadata identity mismatch.")
        path = self._inside(directory / f"{capture_id}.png")
        if not path.is_file() or path.stat().st_size > MAX_PNG_BYTES:
            raise CaptureError("invalid_evidence", "Capture PNG is missing or oversized.")
        png = path.read_bytes()
        if hashlib.sha256(png).hexdigest() != record["image_sha256"]:
            raise CaptureError("evidence_modified", "Capture PNG hash differs from the saved evidence.")
        return record, png

    def record_assessment(self, run_id, capture_id, expected, observed, verdict):
        if verdict not in ("passed", "failed", "unknown"):
            raise CaptureError("invalid_verdict", "Visual verdict must be passed, failed or unknown.")
        for value in (expected, observed):
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 4000:
                raise CaptureError("invalid_assessment", "Expected/observed descriptions must contain 1..4000 characters.")
        with self._operation_lock:
            capture, _ = self.read_capture(run_id, capture_id)
            directory = self._run_directory(run_id)
            self._capacity(directory)
            identifier = uuid.uuid4().hex
            record = response(kind="visual_assessment", evidence_id=identifier, run_id=run_id,
                              capture_id=capture_id, image_sha256=capture["image_sha256"],
                              expected=expected, observed=observed, verdict=verdict,
                              assessment_source="caller_visual_review", functional_result="unknown")
            self._write_record(directory, identifier, record)
            return record

    def evidence(self, run_id, offset=0, limit=20):
        directory = self._run_directory(run_id)
        if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise CaptureError("invalid_page", "offset must be nonnegative; limit must be 1..100.")
        with self._operation_lock:
            records = []
            for index, path in enumerate(directory.glob("*.json")):
                if index >= 1000:
                    raise CaptureError("run_evidence_full", "Run exceeds 1000 visual evidence records.")
                path = self._inside(path)
                if path.stat().st_size > 65536:
                    raise CaptureError("invalid_evidence", "Evidence metadata is oversized.")
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("run_id") != run_id:
                    raise CaptureError("invalid_evidence", "Evidence run_id mismatch.")
                records.append(record)
            records.sort(key=lambda item: (item["observed_at"], item["evidence_id"]))
            selected = records[offset:offset + limit]
            next_offset = offset + len(selected)
            return response(run_id=run_id, records=selected, total=len(records),
                            next_offset=next_offset if next_offset < len(records) else None,
                            functional_result="unknown")

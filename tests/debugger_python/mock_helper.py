"""TEST ONLY: deterministic JSON Lines helper. This never attaches to a JVM."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mcp_server.adapters.debugger.models import DebuggerError, validate_params


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Mock:
    def __init__(self, mode="normal", delay=0.15):
        self.mode, self.delay = mode, delay
        self.lock = threading.RLock()
        self.seq = 0
        self.generation = 0
        self.session = None
        self.connected = False
        self.terminated = False
        self.active = None
        self.breakpoints = {}
        self.counter = 0
        self.closed = False
        self.timers = []

    def write(self, payload):
        with self.lock:
            sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()

    def event(self, name, data):
        with self.lock:
            self.seq += 1
            self.write({"protocol_version": 1, "type": "event", "seq": self.seq, "event": name,
                        "session_id": None if name == "ready" else self.session, "observed_at": now(), "data": data})

    def response(self, request, result=None, error=None):
        payload = {"protocol_version": 1, "type": "response", "id": request["id"], "ok": error is None}
        payload["error" if error else "result"] = error or result
        self.write(payload)

    def schedule(self, seconds, callback):
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        self.timers.append(timer)
        timer.start()

    def cleanup(self, reason):
        if self.active:
            sid = self.active["stop_id"]
            self.active = None
            self.event("resumed", {"stop_id": sid, "reason": reason})

    def stop(self, bp, lease):
        with self.lock:
            if self.closed or not self.connected or self.active:
                return
            self.counter += 1
            stop_id = f"stop-{self.generation}-{self.counter}"
            self.active = {"stop_id": stop_id, "thread_id": "mock-thread", "reason": "breakpoint",
                           "breakpoint_id": bp, "suspend_policy": "event_thread", "observed_at": now(),
                           "expires_at": (datetime.now(timezone.utc) + timedelta(milliseconds=lease)).isoformat().replace("+00:00", "Z")}
            self.event("stopped", self.active.copy())

            def expire():
                with self.lock:
                    if self.active and self.active["stop_id"] == stop_id:
                        self.cleanup("lease_expired")
            self.schedule(lease / 1000, expire)

    def handle(self, request):
        method, params = request["method"], request["params"]
        validate_params(method, params)
        if self.mode == "extra_error_fields" and method == "set_breakpoint":
            self.response(request, error={"code": "ambiguous_class", "message": "Mock loader ambiguity.",
                                          "details": {"class_loader_ids": ["loader-a", "loader-b"]}, "future_field": True})
            return None
        if self.mode == "exit_pending" and method != "shutdown":
            sys.exit(7)
        if method == "attach_debugger":
            if self.connected:
                raise DebuggerError("already_attached", "Mock already attached.")
            self.generation += 1
            self.session = f"mock-session-{self.generation}"
            self.connected = True
            self.terminated = False
            self.lease = params["stop_timeout_ms"]
            self.breakpoints = {}
            return {"session_id": self.session, "connection_state": "connected",
                    "capabilities": {"stack_trace": True, "variables": True, "object_fields": True,
                                     "method_return_events": False}}
        if method == "shutdown":
            self.cleanup("shutdown")
            if self.connected:
                self.connected = False
                self.event("disconnected", {"reason": "shutdown"})
            self.closed = True
            return {"shutdown": True}
        if params["session_id"] != self.session:
            raise DebuggerError("session_not_found", "Unknown mock session.")
        if self.mode == "vm_terminated" and method == "get_debug_state" and self.connected:
            self.active = None
            self.connected = False
            self.terminated = True
            self.event("disconnected", {"reason": "vm_terminated"})
        if not self.connected:
            if method == "get_debug_state":
                return {"connection_state": "terminated" if self.terminated else "disconnected", "active_stop": None,
                        "breakpoints": [], "last_event_seq": self.seq}
            raise DebuggerError("vm_disconnected", "Mock session is disconnected.")
        if method == "set_breakpoint":
            self.counter += 1
            bp = f"mock-bp-{self.generation}-{self.counter}"
            status = "pending" if params["class_name"] == "sample.Pending" else "verified"
            if params["line"] == 999:
                status = "rejected"
            location = {"class_name": params["class_name"], "method_name": "calculate",
                        "source_name": "DamageCalculator.java", "line": params["line"], "class_loader_id": "mock-loader"}
            data = {"breakpoint_id": bp, "status": status, "locations": [location] if status == "verified" else [],
                    "reason": "No executable code at line." if status == "rejected" else None}
            self.breakpoints[bp] = data
            if status == "pending":
                def verify():
                    with self.lock:
                        if self.connected and bp in self.breakpoints:
                            data.update(status="verified", locations=[location])
                            self.event("breakpoint_changed", data.copy())
                            self.stop(bp, self.lease)
                self.schedule(self.delay, verify)
            elif status == "verified":
                # Deliberately emit stopped before the request response.
                self.stop(bp, self.lease)
            return data.copy()
        if method == "remove_breakpoint":
            bp = params["breakpoint_id"]
            if bp not in self.breakpoints:
                raise DebuggerError("breakpoint_not_found", "Unknown mock breakpoint.")
            del self.breakpoints[bp]
            return {"breakpoint_id": bp, "removed": True}
        if method == "get_debug_state":
            return {"connection_state": "connected", "active_stop": self.active.copy() if self.active else None,
                    "breakpoints": list(self.breakpoints.values()), "last_event_seq": self.seq}
        if method == "detach_debugger":
            released = [self.active["stop_id"]] if self.active else []
            self.cleanup("detach")
            self.connected = False
            self.event("disconnected", {"reason": "detach"})
            return {"detached": True, "released_stop_ids": released}
        if not self.active or params["stop_id"] != self.active["stop_id"]:
            raise DebuggerError("stale_reference", "Mock stop was resumed or expired.", {"stop_id": params["stop_id"]})
        stop_id = self.active["stop_id"]
        if method == "resume_execution":
            self.cleanup("user")
            return {"resumed_stop_id": stop_id}
        if method == "get_stack_trace":
            if params["thread_id"] != "mock-thread":
                raise DebuggerError("invalid_params", "Unknown mock thread.")
            items = [{"frame_id": f"{stop_id}-frame-{i}", "index": i, "class_name": "sample.DamageCalculator",
                      "method_name": "calculate" if i == 0 else "main", "source_name": "DamageCalculator.java", "line": 12 + i}
                     for i in range(2)]
            field = "frames"
        else:
            if "frame_id" in params:
                if params["frame_id"] not in (f"{stop_id}-frame-0", f"{stop_id}-frame-1"):
                    raise DebuggerError("stale_reference", "Unknown mock frame.")
                items = [self.value("base", "int", "7"), self.value("bonus", "int", "3"),
                         self.value("damage", "int", "10"), self.value("label", "java.lang.String", "한글\n테스트", "string"),
                         self.value("result", "sample.Result", "sample.Result", "object", f"{stop_id}-object"),
                         self.value("long_text", "java.lang.String", "가" * 4096, "string", truncated=True)]
            else:
                if params["object_ref"] == f"{stop_id}-object":
                    items = [self.value("damage", "int", "10"), self.value("samples", "int[]", "int[2]", "array", f"{stop_id}-array")]
                elif params["object_ref"] == f"{stop_id}-array":
                    items = [self.value("[0]", "int", "7"), self.value("[1]", "int", "3")]
                else:
                    raise DebuggerError("stale_reference", "Unknown mock object.")
            field = "variables"
        offset, limit = params["offset"], params["limit"]
        page = items[offset:offset + limit]
        more = offset + len(page) < len(items)
        result = {field: page, "next_offset": offset + len(page) if more else None,
                  "truncated": more or any(v.get("truncated", False) for v in page)}
        if field == "variables":
            result["warnings"] = []
        return result

    @staticmethod
    def value(name, type_name, value, kind="primitive", ref=None, truncated=False):
        return {"name": name, "kind": kind, "type_name": type_name, "display_value": value,
                "object_ref": ref, "has_children": ref is not None, "truncated": truncated}

    def run(self):
        sys.stderr.buffer.write("모의 헬퍼: 실제 JVM에 연결하지 않습니다.\n".encode("utf-8"))
        sys.stderr.buffer.flush()
        if self.mode == "stderr_flood":
            sys.stderr.buffer.write(b"x" * (2 * 1024 * 1024))
            sys.stderr.buffer.flush()
        if self.mode == "stderr_unicode":
            sys.stderr.buffer.write(b"x" * 4095 + ("\uD55C\uAE00" * 3000).encode("utf-8"))
            sys.stderr.buffer.flush()
        if self.mode == "no_ready":
            time.sleep(10)
            return
        if self.mode in ("bad_json", "stdout_log", "oversized", "invalid_utf8"):
            output = {"bad_json": b"{oops}\n", "stdout_log": b"INFO startup\n",
                      "oversized": b"x" * (1024 * 1024 + 1) + b"\n", "invalid_utf8": b"\xff\n"}[self.mode]
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
            return
        if self.mode == "version_mismatch":
            self.write({"protocol_version": 2, "type": "event", "event": "ready"})
            return
        self.event("ready", {"helper_version": "mock-v1 (simulated)"})
        try:
            for line in sys.stdin.buffer:
                request = json.loads(line)
                with self.lock:
                    try:
                        result = self.handle(request)
                        if self.mode == "late_attach" and request["method"] == "attach_debugger":
                            self.schedule(self.delay, lambda req=request, value=result: self.response(req, value))
                        elif self.mode == "extra_error_fields" and result is None:
                            pass
                        elif self.mode == "delay_state" and request["method"] == "get_debug_state":
                            self.schedule(self.delay, lambda req=request, value=result: self.response(req, value))
                        else:
                            self.response(request, result)
                    except DebuggerError as exc:
                        self.response(request, error=exc.as_dict())
                if self.closed:
                    break
        finally:
            with self.lock:
                if not self.closed:
                    self.cleanup("shutdown")
                    if self.connected:
                        self.event("disconnected", {"reason": "shutdown"})
                    self.closed = True
                for timer in self.timers:
                    timer.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="normal")
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    Mock(args.mode, args.delay).run()

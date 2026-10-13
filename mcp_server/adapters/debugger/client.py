"""Concurrent, bounded subprocess transport. One client owns one helper process."""
from __future__ import annotations

import asyncio
import codecs
import os
import subprocess
from collections.abc import Callable, Sequence

from .models import (DebuggerError, Event, Response, MAX_LINE_BYTES, decode_message,
                     encode_request, require, validate_result, validate_params)

Recorder = Callable[[str, dict], None]


class HelperClient:
    def __init__(self, command: Sequence[str], *, ready_timeout: float = 5,
                 request_timeout: float = 10, shutdown_timeout: float = 5,
                 record: Recorder | None = None):
        self.command = tuple(command)
        self.ready_timeout = ready_timeout
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.record = record or (lambda kind, payload: None)
        self.process: asyncio.subprocess.Process | None = None
        self.helper_version: str | None = None
        self._ready: asyncio.Future | None = None
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        self._tasks: list[asyncio.Task] = []
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._counter = 0
        self._seq = 0
        self._failure: DebuggerError | None = None
        self._closing = False
        self._closed = False

    def _record(self, kind: str, payload: dict) -> None:
        try:
            self.record(kind, payload)
        except Exception as exc:
            raise DebuggerError("evidence_unavailable", "Cannot persist debugger evidence.") from exc

    def _fail(self, error: DebuggerError) -> None:
        if self._failure is None:
            self._failure = error
        # Pending requests always fail as backend_unavailable on transport death.
        pending_error = DebuggerError("backend_unavailable", "Debugger helper is unavailable.",
                                      {"cause": self._failure.code})
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(pending_error)
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(self._failure)

    async def start(self) -> None:
        async with self._start_lock:
            if self._closed or self._closing:
                raise DebuggerError("backend_unavailable", "Helper client is closed.")
            if self._failure:
                raise self._failure
            if self.process is not None:
                return
            self._ready = asyncio.get_running_loop().create_future()
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                self.process = await asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=MAX_LINE_BYTES, creationflags=flags)
                self._record("process_started", {"pid": self.process.pid})
                self._tasks = [asyncio.create_task(self._read_stdout()),
                               asyncio.create_task(self._read_stderr()),
                               asyncio.create_task(self._watch_exit())]
                await asyncio.wait_for(asyncio.shield(self._ready), self.ready_timeout)
            except asyncio.TimeoutError as exc:
                error = DebuggerError("ready_timeout", "Timed out waiting for helper ready event.")
                self._fail(error)
                try:
                    await self.close()
                except DebuggerError:
                    pass  # cleanup is recorded; retain the original startup failure
                raise error from exc
            except (OSError, DebuggerError) as exc:
                error = exc if isinstance(exc, DebuggerError) else DebuggerError("backend_unavailable", "Cannot start Java helper.")
                self._fail(error)
                try:
                    await self.close()
                except DebuggerError:
                    pass  # cleanup is recorded; retain the original startup failure
                raise error from exc
            except asyncio.CancelledError:
                await self.close()
                raise
            finally:
                if self._ready.done() and not self._ready.cancelled():
                    self._ready.exception()  # consume failures even if startup was cancelled

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                message = decode_message(line)
                require(self._ready.done() or (isinstance(message, Event) and message.event == "ready"),
                        "First helper message must be ready.")
                if isinstance(message, Event):
                    require(message.seq > self._seq, "Event sequence must increase.")
                    self._seq = message.seq
                    if message.event == "ready":
                        require(not self._ready.done(), "Duplicate ready event.")
                        self.helper_version = message.data["helper_version"]
                    self._record("event", message.raw)
                    if message.event == "ready":
                        self._ready.set_result(message)
                else:
                    pending = self._pending.get(message.id)
                    if pending and message.ok:
                        validate_result(pending[0], message.result)
                    self._record("response" if pending else "late_response", message.raw)
                    if pending:
                        method, future = pending
                        if not future.done():
                            future.set_result(message)
            self._fail(DebuggerError("backend_unavailable", "Helper stdout reached EOF."))
        except (DebuggerError, ValueError, OSError) as exc:
            error = exc if isinstance(exc, DebuggerError) else DebuggerError("protocol_error", "Invalid or oversized helper output.")
            self._fail(error)
            try:
                self._record("protocol_failure", error.as_dict())
            except DebuggerError:
                pass
            # Invalid stdout is unusable. Close input and bound process cleanup.
            if self.process.stdin:
                self.process.stdin.close()
            async def discard_invalid_output():
                while await self.process.stdout.read(65536):
                    pass
            try:
                await asyncio.wait_for(discard_invalid_output(), self.shutdown_timeout)
            except asyncio.TimeoutError:
                if self.process.returncode is None:
                    self.process.terminate()
                await discard_invalid_output()

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while chunk := await self.process.stderr.read(4096):
                # Preserve UTF-8 characters that straddle read boundaries.
                decoded = decoder.decode(chunk)
                if decoded:
                    self._record("stderr", {"text": decoded})
            tail = decoder.decode(b"", final=True)
            if tail:
                self._record("stderr", {"text": tail})
        except (OSError, DebuggerError) as exc:
            self._fail(exc if isinstance(exc, DebuggerError) else DebuggerError("backend_unavailable", "Cannot read helper stderr."))

    async def _watch_exit(self) -> None:
        assert self.process
        code = await self.process.wait()
        # Drain buffered stdout before failing pending requests on process exit.
        if self._tasks:
            await self._tasks[0]
        try:
            self._record("process_exited", {"returncode": code})
        finally:
            self._fail(DebuggerError("backend_unavailable", "Helper process exited.", {"returncode": code}))

    async def request(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        validate_params(method, params)
        if self.process is None:
            await self.start()
        if self._failure or self._closed or (self._closing and method != "shutdown"):
            raise DebuggerError("backend_unavailable", "Helper is unavailable.",
                                {"cause": self._failure.code if self._failure else "closed"})
        self._counter += 1
        request_id = f"req-{self._counter}"
        payload = encode_request(request_id, method, params)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)

        async def send_and_wait() -> Response:
            async with self._write_lock:
                if self._failure:
                    raise DebuggerError("backend_unavailable", "Helper is unavailable.")
                self._record("request", {"protocol_version": 1, "type": "request", "id": request_id,
                                         "method": method, "params": params})
                self.process.stdin.write(payload)
                await self.process.stdin.drain()
            return await asyncio.shield(future)

        try:
            response = await asyncio.wait_for(send_and_wait(), timeout or self.request_timeout)
            if not response.ok:
                raise DebuggerError(response.error["code"], response.error["message"], response.error["details"])
            return response.result
        except asyncio.TimeoutError as exc:
            self._record("request_timeout", {"id": request_id, "method": method, "execution_unknown": True})
            raise DebuggerError("request_timeout", "Helper request timed out; execution may have occurred.",
                                {"request_id": request_id, "method": method, "execution_unknown": True}) from exc
        except asyncio.CancelledError:
            self._record("request_cancelled", {"id": request_id, "method": method, "execution_unknown": True})
            raise
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            self._fail(DebuggerError("backend_unavailable", "Cannot write to helper stdin."))
            raise self._failure from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            cleanup_error = None
            if self.process:
                if not self._failure and self._ready and self._ready.done() and self.process.returncode is None:
                    try:
                        await self.request("shutdown", {}, timeout=self.shutdown_timeout)
                    except DebuggerError as exc:
                        cleanup_error = exc
                        try:
                            self._record("cleanup_failed", exc.as_dict())
                        except DebuggerError:
                            pass  # evidence failure must not prevent closing stdin
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(), self.shutdown_timeout)
                except asyncio.TimeoutError:
                    cleanup_error = cleanup_error or DebuggerError("shutdown_timeout", "Helper required forced termination; JVM cleanup is unverified.")
                    try:
                        self._record("forced_termination", cleanup_error.as_dict())
                    except DebuggerError:
                        pass
                    self.process.terminate()
                    try:
                        await asyncio.wait_for(self.process.wait(), self.shutdown_timeout)
                    except asyncio.TimeoutError:
                        self.process.kill()
                        await self.process.wait()
            self._fail(DebuggerError("backend_unavailable", "Helper closed."))
            if self._tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), self.shutdown_timeout)
                except asyncio.TimeoutError:
                    for task in self._tasks:
                        task.cancel()
                    await asyncio.gather(*self._tasks, return_exceptions=True)
            self._closed = True
            if cleanup_error:
                raise cleanup_error

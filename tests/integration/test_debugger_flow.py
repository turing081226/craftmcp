import unittest

from debugger_flow import DebuggerFlow
from mcp_server.services.debugger_fixture import BREAKPOINT_LINE, CLASS_NAME


class FakeTools:
    """D3 fake contract, not a real JVM or a replacement for B's implementation."""
    def __init__(self, wrong_variable=False):
        self.calls = []
        self.active = False
        self.breakpoint = False
        self.detached = False
        self.jobs = {}
        self.wrong_variable = wrong_variable

    async def call(self, name, params):
        self.calls.append((name, params.copy()))
        result = {}
        if name == "attach_debugger":
            result = {"session_id": "session-1"}
        elif name == "set_breakpoint":
            self.breakpoint = True
            result = {"status": "verified", "breakpoint_id": "bp-1"}
        elif name == "execute_command":
            job_id = f"job-{len(self.jobs) + 1}"
            self.jobs[job_id] = params.copy()
            self.active = self.breakpoint
            result = {"job_id": job_id, "status": "queued"}
        elif name == "get_job":
            request = self.jobs[params["job_id"]]
            result = {"job_id": params["job_id"], "status": "running" if self.active else "succeeded",
                "runtime_id": request["runtime_id"], "run_id": request["run_id"], "raw_output":
                ("CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n"
                 "CRAFTDEBUG_RCON v=1 status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n")}
        elif name == "get_debug_state":
            result = {"active_stop": {"stop_id": "stop-1", "thread_id": "thread-1", "breakpoint_id": "bp-1"} if self.active else None}
        elif name == "get_stack_trace":
            # Put another frame first to catch drivers that blindly select frames[0].
            result = {"frames": [{"class_name": "other.Frame", "method_name": "run", "line": 1},
                {"class_name": CLASS_NAME, "method_name": "calculate", "line": BREAKPOINT_LINE, "frame_id": "frame-1"}],
                "next_offset": None}
        elif name == "get_variables":
            if not self.active:
                return {"ok": False, "error": {"code": "stale_reference"}}
            if "frame_id" in params:
                rows = [("base", "7"), ("bonus", "3"), ("damage", "11" if self.wrong_variable else "10"),
                        ("runId", '"run-1"')]
                result = {"variables": [{"name": n, "display_value": v} for n, v in rows]
                          + [{"name": "result", "object_ref": "object-1"}]}
            elif params["object_ref"] == "object-1":
                result = {"variables": [{"name": "damage", "display_value": "10"},
                    {"name": "base", "display_value": "7"}, {"name": "bonus", "display_value": "3"},
                    {"name": "runId", "display_value": '"run-1"'}, {"name": "inputs", "object_ref": "array-1"}]}
            else:
                result = {"variables": [{"name": "[0]", "display_value": "7"}, {"name": "[1]", "display_value": "3"}]}
        elif name == "resume_execution":
            self.active = False
        elif name == "remove_breakpoint":
            if self.active:
                raise AssertionError("Must resume before removing the breakpoint")
            self.breakpoint = False
        elif name == "detach_debugger":
            self.active = self.breakpoint = False
            self.detached = True
        else:
            raise AssertionError(name)
        return {"ok": True, "result": result}


class DebuggerFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_pending_job_does_not_block_debugger_and_regression_is_a_new_request(self):
        fake = FakeTools()
        evidence = []
        flow = DebuggerFlow(fake.call, lambda kind, payload: evidence.append((kind, payload)),
                            runtime_id="fixture", project_id="sample", run_id="run-1")
        summary = await flow.run()
        self.assertEqual(summary["status"], "passed")
        requests = [p for name, p in fake.calls if name == "execute_command"]
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(requests[0]["request_id"], requests[1]["request_id"])
        self.assertTrue(fake.detached)
        self.assertIsNone(summary["regression_bindings"]["session_id"])
        self.assertIsNone(summary["regression_bindings"]["stop_id"])
        self.assertEqual(summary["bindings"]["session_id"], "session-1")
        self.assertNotEqual(summary["bindings"]["job_id"], summary["regression_bindings"]["job_id"])
        self.assertTrue(any(kind == "id_binding" and payload.get("stop_id") for kind, payload in evidence))

    async def test_assertion_failure_releases_held_stop_without_retrying_command(self):
        fake = FakeTools(wrong_variable=True)
        flow = DebuggerFlow(fake.call, lambda *args: None, runtime_id="fixture", project_id="sample", run_id="run-1")
        with self.assertRaisesRegex(AssertionError, "Wrong damage"):
            await flow.run()
        self.assertTrue(fake.detached)
        self.assertFalse(fake.active)
        self.assertEqual(sum(name == "execute_command" for name, _ in fake.calls), 1)


if __name__ == "__main__":
    unittest.main()

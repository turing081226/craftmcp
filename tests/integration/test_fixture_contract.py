import unittest

from mcp_server.services.debugger_fixture import command_text, parse_output


class FixtureContractTests(unittest.TestCase):
    def parse(self, text, **kwargs):
        return parse_output(text, run_id="run-1", arguments={"base": 7, "bonus": 3}, **kwargs)

    def test_request_validation_blocks_injection_bool_float_and_integer_overflow(self):
        self.assertEqual(command_text({"base": -7, "bonus": 3}, "run-1"),
                         "craftdebugrcon calculate run-1 -7 3")
        for run in ("x\nstop", "x y", "한글", "", "x" * 65):
            with self.subTest(run=run), self.assertRaises(ValueError):
                command_text({"base": 7, "bonus": 3}, run)
        for args in ({"base": True, "bonus": 3}, {"base": 1.0, "bonus": 3},
                     {"base": 2**31, "bonus": 3}, {"base": 7},
                     {"base": 7, "bonus": 3, "command": "stop"}):
            with self.subTest(arguments=args), self.assertRaises(ValueError):
                command_text(args, "run-1")

    def test_complete_response_and_crlf(self):
        raw = ("CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n"
               "CRAFTDEBUG_RCON v=1 status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n")
        for output in (raw, raw.replace("\n", "\r\n"), raw.replace("\n", "\n\n")):
            result = self.parse(output)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["result"]["damage"], 10)
            self.assertEqual(result["raw_output"], output)

    def test_started_alone_is_not_completion(self):
        for output in ("", "CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n"):
            result = self.parse(output)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["command_success"])

    def test_correlated_failure_is_not_successful_game_result(self):
        raw = "CRAFTDEBUG_RCON v=1 status=error run_id=run-1 code=INTEGER_OVERFLOW end=1\n"
        result = self.parse(raw)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["command_success"])
        self.assertEqual(result["error"]["code"], "INTEGER_OVERFLOW")

    def test_mismatched_truncated_conflicting_or_forged_output_is_unknown(self):
        start = "CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n"
        end = "CRAFTDEBUG_RCON v=1 status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n"
        error = "CRAFTDEBUG_RCON v=1 status=error run_id=run-1 code=INVALID_ARGS end=1\n"
        for output in (end, start + end.rstrip(), start + end + end, start + error + end,
                       start + end.replace("damage=10", "damage=11"),
                       start + end.replace("damage=10", "damage=" + "9" * 5000),
                       start + end.replace("run-1", "run-2"),
                       start.replace("base=7", "base=8") + end,
                       start + end.replace("end=1", "end=1 end=1"),
                       "log prefix " + start + end, start + end.replace("v=1", "v=2")):
            with self.subTest(output=output):
                result = self.parse(output)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["completion"], "invalid")


if __name__ == "__main__":
    unittest.main()

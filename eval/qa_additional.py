"""Run the tracked regression tests for the additional QA findings.

Uses owned temporary files/processes and loopback TCP; no live Paper server.
Exit 0 is regression success; exit 1 is failure. The original observations
are preserved in docs/qa-additional-2026-11-03.md.
"""
from pathlib import Path
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    os.environ["DEBUGGER_CONFIG"] = str(ROOT / "debugger.example.json")
    suite = unittest.TestSuite()
    for pattern in ("test_build_deploy.py", "test_server_qa.py"):
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {"status": "passed" if result.wasSuccessful() else "failed", "tests": result.testsRun,
               "failures": [str(test) for test, _ in result.failures],
               "errors": [str(test) for test, _ in result.errors], "skipped": len(result.skipped)}
    (ROOT / ".cache").mkdir(exist_ok=True)
    (ROOT / ".cache/qa-additional-regression-results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    raise SystemExit(0 if result.wasSuccessful() else 1)

"""All-or-nothing behavior when one API call fails (fake client, no paid calls).

Run with:  python test_api_failure.py   (or: pytest test_api_failure.py)

Scenario: the 3rd submission's API call raises. Expected:
- the pipeline exits with code 1
- exactly one new run folder is created
- no predictions.json is saved in it
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from invoke_llm import POLICY_CHECKS, main


class FakeClient:
    def __init__(self, results):
        self.responses = self
        self._results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def ok():
    checks = [{"policy_check": name, "verdict": "PASS", "justification": "Mock success"}
              for name in POLICY_CHECKS]
    return SimpleNamespace(output_text=json.dumps({"checks": checks}))


def test_api_failure_saves_nothing_and_exits_1():
    client = FakeClient([ok(), ok(), RuntimeError("Simulated API failure")])

    with tempfile.TemporaryDirectory() as tmp:
        runs_root = Path(tmp)
        try:
            main(client=client, runs_root=runs_root)
            raise AssertionError("expected SystemExit")
        except SystemExit as error:
            assert error.code == 1

        new_runs = list(runs_root.iterdir())
        assert len(new_runs) == 1, new_runs
        assert not (new_runs[0] / "predictions.json").exists()
        assert len(client.calls) == 3


if __name__ == "__main__":
    test_api_failure_saves_nothing_and_exits_1()
    print("PASS test_api_failure_saves_nothing_and_exits_1")

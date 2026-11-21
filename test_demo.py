"""End-to-end smoke test for demo.py (offline: no API, no MCP, temporary runs root).

Run with:  python test_demo.py
"""

import json
import tempfile
from pathlib import Path

import demo

PROJECT_DIR = Path(__file__).resolve().parent
REFERENCE_RUN = PROJECT_DIR / "runs" / "2026-11-15_15-36-16"
PROTECTED = [demo.DEMO_PREDICTIONS, demo.DEMO_DECISIONS, demo.GOLDEN_DATASET,
             *sorted(p for p in REFERENCE_RUN.iterdir() if p.is_file())]


def snapshot():
    return {path: path.read_bytes() for path in PROTECTED}


def test_demo_runs_end_to_end_offline():
    before = snapshot()
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = demo.main(["demo.py"], runs_root=Path(tmp))

        assert run_dir.parent == Path(tmp) and run_dir.name.startswith("demo_")
        assert sorted(p.name for p in run_dir.iterdir()) == sorted(demo.ARTIFACTS)
        assert (run_dir / "predictions.json").read_bytes() == demo.DEMO_PREDICTIONS.read_bytes()

        overall = json.loads((run_dir / "eval_results.json").read_text())["overall"]
        assert overall["total_evaluated_pairs"] == 12
        assert overall["correct_decisions"] == 11
        assert overall["incorrect_decisions"] == 0
        assert overall["abstentions"] == 1
        assert round(overall["accuracy"], 4) == 0.9167
        assert round(overall["decision_coverage"], 4) == 0.9167

        completion = json.loads((run_dir / "review_completion.json").read_text())
        assert completion["summary"]["total_submissions"] == 4
        assert completion["summary"]["checks_resolved"] == 3
        status = {s["submission_id"]: s for s in completion["submissions"]}
        assert status["SYN001"]["completion_status"] == "PENDING_HUMAN_REVIEW"
        assert status["SYN001"]["pending_checks"] == ["real_money_gambling"]
        assert status["APP002"]["completion_status"] == "CHECKS_RESOLVED"

        # A second demo run in the same second gets its own folder.
        second = demo.create_demo_run_dir(Path(tmp))
        assert second != run_dir and second.exists()

    assert snapshot() == before, "demo modified a committed fixture or the reference run"


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")

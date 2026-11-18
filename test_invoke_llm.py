"""Tests for invoke_llm.py using a fake API client (no paid calls).

Run with:  python test_invoke_llm.py   (or: pytest test_invoke_llm.py)
"""

import importlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from invoke_llm import (
    POLICY_CHECKS,
    build_schema,
    main,
    review_submission,
    run_inference,
    save_predictions,
)

SYSTEM_PROMPT = "SYSTEM PROMPT"
TEMPLATE = "Name: {app_name}\nDescription: {description}"
APPS = [
    {"submission_id": "A1", "app_name": "App One", "description": "First app."},
    {"submission_id": "A2", "app_name": "App Two", "description": "Second app."},
    {"submission_id": "A3", "app_name": "App Three", "description": "Third app."},
]


class FakeClient:
    """Mimics client.responses.create(); returns or raises scripted results in order."""

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


def checks(verdicts=None):
    verdicts = verdicts or {}
    return [
        {"policy_check": name, "verdict": verdicts.get(name, "PASS"), "justification": f"why {name}"}
        for name in POLICY_CHECKS
    ]


def response(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(output_text=text)


def ok():
    return response({"checks": checks()})


# --- Module import ---

def test_import_has_no_side_effects():
    runs_before = set(Path("runs").iterdir())
    sys.modules.pop("invoke_llm", None)
    module = importlib.import_module("invoke_llm")
    assert set(Path("runs").iterdir()) == runs_before
    assert not hasattr(module, "client")
    assert not hasattr(module, "run_dir")


# --- build_schema ---

def test_schema_is_strict_and_lists_policy_checks():
    schema = build_schema()
    item = schema["properties"]["checks"]["items"]
    assert schema["additionalProperties"] is False and item["additionalProperties"] is False
    assert item["properties"]["policy_check"]["enum"] == list(POLICY_CHECKS)
    assert item["properties"]["verdict"]["enum"] == ["PASS", "FAIL", "UNDECIDED"]
    assert item["required"] == ["policy_check", "verdict", "justification"]


def test_undecided_verdict_is_accepted_and_preserved():
    expected = checks({"real_money_gambling": "UNDECIDED"})
    expected[1]["justification"] = "Unclear whether coins can be cashed out."
    client = FakeClient([response({"checks": expected})])

    predictions = run_inference(client, APPS[:1], SYSTEM_PROMPT, TEMPLATE)

    assert predictions == [{"submission_id": "A1", "checks": expected}]
    gambling = predictions[0]["checks"][1]
    assert gambling["verdict"] == "UNDECIDED"
    assert gambling["justification"] == "Unclear whether coins can be cashed out."


# --- review_submission ---

def test_review_submission_returns_checks_and_sends_expected_request():
    expected = checks({"real_money_gambling": "FAIL"})
    client = FakeClient([response({"checks": expected})])

    result = review_submission(client, APPS[0], SYSTEM_PROMPT, TEMPLATE)

    assert result == expected
    request = client.calls[0]
    assert request["model"] == "gpt-4.1-mini"
    assert request["instructions"] == SYSTEM_PROMPT
    assert request["input"] == "Name: App One\nDescription: First app."
    fmt = request["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"] == build_schema()


def test_review_submission_rejects_missing_or_duplicate_checks():
    missing = checks()[:2]
    duplicate = checks()[:2] + [checks()[0]]
    for bad in (missing, duplicate):
        client = FakeClient([response({"checks": bad})])
        try:
            review_submission(client, APPS[0], SYSTEM_PROMPT, TEMPLATE)
            raise AssertionError("expected ValueError")
        except ValueError as error:
            assert "Missing or duplicate policy checks" in str(error)


def test_review_submission_rejects_malformed_responses():
    for bad in ("not json", {"verdict": "approved"}):   # invalid JSON; legacy shape
        client = FakeClient([response(bad)])
        try:
            review_submission(client, APPS[0], SYSTEM_PROMPT, TEMPLATE)
            raise AssertionError("expected an exception")
        except (ValueError, KeyError):
            pass


# --- run_inference ---

def test_run_inference_all_succeed_preserves_prediction_structure():
    client = FakeClient([ok(), ok(), ok()])
    predictions = run_inference(client, APPS, SYSTEM_PROMPT, TEMPLATE)

    assert len(client.calls) == len(APPS)
    assert [p["submission_id"] for p in predictions] == ["A1", "A2", "A3"]
    for prediction in predictions:
        assert set(prediction) == {"submission_id", "checks"}
        assert prediction["checks"] == checks()


def test_run_inference_skips_failed_submission_and_continues():
    client = FakeClient([ok(), RuntimeError("Simulated API failure"), ok()])
    predictions = run_inference(client, APPS, SYSTEM_PROMPT, TEMPLATE)

    assert len(client.calls) == 3
    assert [p["submission_id"] for p in predictions] == ["A1", "A3"]


# --- save_predictions (all-or-nothing) ---

def test_save_predictions_complete_writes_file():
    predictions = [{"submission_id": app["submission_id"], "checks": checks()} for app in APPS]
    with tempfile.TemporaryDirectory() as tmp:
        path = save_predictions(predictions, APPS, Path(tmp))
        assert path == Path(tmp) / "predictions.json"
        assert json.loads(path.read_text()) == predictions


def test_save_predictions_incomplete_writes_nothing():
    predictions = [{"submission_id": "A1", "checks": checks()}]
    with tempfile.TemporaryDirectory() as tmp:
        assert save_predictions(predictions, APPS, Path(tmp)) is None
        assert not (Path(tmp) / "predictions.json").exists()


# --- main() end-to-end with fake client ---

def test_main_success_saves_predictions_in_new_run_dir():
    with open("submissions.json") as file:
        submissions = json.load(file)
    client = FakeClient([ok() for _ in submissions])

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = main(client=client, runs_root=Path(tmp))

        assert list(Path(tmp).iterdir()) == [run_dir]
        saved = json.loads((run_dir / "predictions.json").read_text())
        assert len(client.calls) == len(submissions)
        assert [p["submission_id"] for p in saved] == [s["submission_id"] for s in submissions]
        for prediction in saved:
            assert set(prediction) == {"submission_id", "checks"}
            for check in prediction["checks"]:
                assert check["verdict"] in ("PASS", "FAIL")


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")

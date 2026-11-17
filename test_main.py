"""Tests for the check-level evaluator in main.py.

Run with:  python test_main.py   (or: pytest test_main.py)

Uses inline data so tests don't depend on saved runs or golden_dataset.json.
"""

import json
import tempfile
from pathlib import Path

from main import evaluate, main, validate

CHECKS = ["metadata_quality", "real_money_gambling", "child_safety"]
ALL_PASS = {name: "PASS" for name in CHECKS}


def pred(submission_id, verdicts):
    return {
        "submission_id": submission_id,
        "checks": [
            {"policy_check": name, "verdict": verdict, "justification": f"why {name}"}
            for name, verdict in verdicts.items()
        ],
    }


def gold(submission_id, verdicts):
    return {"submission_id": submission_id, "expected_checks": dict(verdicts)}


def run(predictions, golden):
    errors, prediction_map, golden_map = validate(predictions, golden, CHECKS)
    assert errors == [], errors
    return evaluate(prediction_map, golden_map, CHECKS)


def errors_for(predictions, golden):
    return validate(predictions, golden, CHECKS)[0]


def assert_error(predictions, golden, fragment):
    errors = errors_for(predictions, golden)
    assert any(fragment in error for error in errors), (fragment, errors)


# --- Evaluation ---

def test_perfect_predictions():
    labels = {"metadata_quality": "PASS", "real_money_gambling": "FAIL", "child_safety": "PASS"}
    results = run([pred("A1", labels), pred("A2", ALL_PASS)],
                  [gold("A1", labels), gold("A2", ALL_PASS)])
    assert results["overall"] == {
        "total_evaluated_pairs": 6, "correct_predictions": 6, "mismatches": 0, "accuracy": 1.0}
    assert results["mismatches"] == []
    gambling = results["per_check"]["real_money_gambling"]
    assert gambling["fail_precision"] == 1.0 and gambling["fail_recall"] == 1.0
    assert gambling["pass_precision"] == 1.0 and gambling["pass_recall"] == 1.0


def test_pass_to_fail_mismatch():
    # Expected PASS, model predicted FAIL (false alarm).
    predicted = dict(ALL_PASS, child_safety="FAIL")
    results = run([pred("A1", predicted)], [gold("A1", ALL_PASS)])
    assert results["overall"]["mismatches"] == 1
    m = results["per_check"]["child_safety"]
    assert m["confusion_matrix"]["expected_PASS"]["predicted_FAIL"] == 1
    assert m["actual_pass"] == 1 and m["predicted_fail"] == 1
    assert m["accuracy"] == 0.0
    assert m["fail_precision"] == 0.0
    assert m["pass_recall"] == 0.0
    assert m["fail_recall"] is None      # no actual FAILs
    assert m["pass_precision"] is None   # no predicted PASSes
    assert results["mismatches"] == [{
        "submission_id": "A1", "policy_check": "child_safety",
        "expected_verdict": "PASS", "predicted_verdict": "FAIL",
        "justification": "why child_safety"}]


def test_fail_to_pass_mismatch():
    # Expected FAIL, model predicted PASS (missed violation).
    expected = dict(ALL_PASS, real_money_gambling="FAIL")
    results = run([pred("A1", ALL_PASS)], [gold("A1", expected)])
    m = results["per_check"]["real_money_gambling"]
    assert m["confusion_matrix"]["expected_FAIL"]["predicted_PASS"] == 1
    assert m["fail_recall"] == 0.0
    assert m["pass_precision"] == 0.0
    assert m["fail_precision"] is None   # no predicted FAILs
    assert results["mismatches"][0]["expected_verdict"] == "FAIL"
    assert results["mismatches"][0]["predicted_verdict"] == "PASS"


def test_mixed_metrics():
    # gambling: 1 TP-FAIL, 1 FAIL->PASS miss, 1 PASS->FAIL false alarm, 1 TP-PASS
    golden = [gold("A1", dict(ALL_PASS, real_money_gambling="FAIL")),
              gold("A2", dict(ALL_PASS, real_money_gambling="FAIL")),
              gold("A3", ALL_PASS),
              gold("A4", ALL_PASS)]
    predictions = [pred("A1", dict(ALL_PASS, real_money_gambling="FAIL")),
                   pred("A2", ALL_PASS),
                   pred("A3", dict(ALL_PASS, real_money_gambling="FAIL")),
                   pred("A4", ALL_PASS)]
    m = run(predictions, golden)["per_check"]["real_money_gambling"]
    assert m["accuracy"] == 0.5
    assert m["fail_precision"] == 0.5 and m["fail_recall"] == 0.5
    assert m["pass_precision"] == 0.5 and m["pass_recall"] == 0.5


def test_report_formats_none_as_na():
    from main import fmt
    assert fmt(None) == "N/A"
    assert fmt(0.5) == "50.00%"
    assert fmt(0.0) == "0.00%"


def test_zero_denominator_metrics_are_none():
    results = run([pred("A1", ALL_PASS)], [gold("A1", ALL_PASS)])
    for name in CHECKS:
        m = results["per_check"][name]
        assert m["fail_precision"] is None
        assert m["fail_recall"] is None
        assert m["pass_precision"] == 1.0 and m["pass_recall"] == 1.0


# --- Validation ---

def test_empty_datasets():
    assert_error([], [gold("A1", ALL_PASS)], "predictions: EMPTY")
    assert_error([pred("A1", ALL_PASS)], [], "golden: EMPTY")


def test_missing_required_fields():
    assert_error([{"checks": []}], [gold("A1", ALL_PASS)], "MISSING submission_id")
    assert_error([{"submission_id": "A1"}], [gold("A1", ALL_PASS)], "MISSING or invalid 'checks'")
    assert_error([pred("A1", ALL_PASS)], [{"submission_id": "A1"}], "MISSING or invalid 'expected_checks'")
    bad = pred("A1", ALL_PASS)
    del bad["checks"][0]["verdict"]
    assert_error([bad], [gold("A1", ALL_PASS)], "MISSING policy_check or verdict")


def test_duplicate_submission_ids():
    assert_error([pred("A1", ALL_PASS), pred("A1", ALL_PASS)], [gold("A1", ALL_PASS)],
                 "predictions A1: DUPLICATE submission_id")
    assert_error([pred("A1", ALL_PASS)], [gold("A1", ALL_PASS), gold("A1", ALL_PASS)],
                 "golden A1: DUPLICATE submission_id")


def test_missing_unknown_and_duplicate_checks():
    missing = {"metadata_quality": "PASS", "real_money_gambling": "PASS"}
    assert_error([pred("A1", missing)], [gold("A1", ALL_PASS)], "child_safety: MISSING policy check")
    assert_error([pred("A1", ALL_PASS)], [gold("A1", missing)], "child_safety: MISSING policy check")

    unknown = dict(ALL_PASS, violence="PASS")
    assert_error([pred("A1", unknown)], [gold("A1", ALL_PASS)], "violence: UNKNOWN policy check")
    assert_error([pred("A1", ALL_PASS)], [gold("A1", unknown)], "violence: UNKNOWN policy check")

    duplicate = pred("A1", ALL_PASS)
    duplicate["checks"].append({"policy_check": "metadata_quality", "verdict": "PASS"})
    assert_error([duplicate], [gold("A1", ALL_PASS)], "metadata_quality: DUPLICATE policy check")


def test_invalid_verdicts():
    for bad_verdict in ["approved", "pass", "", None, ["PASS"]]:
        assert_error([pred("A1", dict(ALL_PASS, child_safety=bad_verdict))],
                     [gold("A1", ALL_PASS)], "INVALID verdict")
        assert_error([pred("A1", ALL_PASS)],
                     [gold("A1", dict(ALL_PASS, child_safety=bad_verdict))], "INVALID verdict")


def test_missing_and_unexpected_submission_ids():
    assert_error([pred("A1", ALL_PASS)], [gold("A1", ALL_PASS), gold("A2", ALL_PASS)],
                 "A2: MISSING PREDICTION")
    assert_error([pred("A1", ALL_PASS), pred("A9", ALL_PASS)], [gold("A1", ALL_PASS)],
                 "A9: MISSING GROUND TRUTH")


def test_valid_data_has_no_errors():
    assert errors_for([pred("A1", ALL_PASS)], [gold("A1", ALL_PASS)]) == []


# --- End-to-end main() with temp files ---

def _write(path, data):
    path.write_text(json.dumps(data))


def test_main_writes_results_and_golden_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        golden = [gold("A1", ALL_PASS)]
        _write(run_dir / "predictions.json", [pred("A1", ALL_PASS)])
        _write(Path(tmp) / "golden.json", golden)

        main(["main.py", str(run_dir / "predictions.json"), str(Path(tmp) / "golden.json")])

        saved = json.loads((run_dir / "eval_results.json").read_text())
        assert saved["overall"]["accuracy"] == 1.0
        assert saved["per_check"]["child_safety"]["fail_recall"] is None
        assert json.loads((run_dir / "golden_dataset_used.json").read_text()) == golden


def test_main_validation_failure_exits_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        _write(run_dir / "predictions.json", [pred("A1", dict(ALL_PASS, child_safety="maybe"))])
        _write(Path(tmp) / "golden.json", [gold("A1", ALL_PASS)])

        try:
            main(["main.py", str(run_dir / "predictions.json"), str(Path(tmp) / "golden.json")])
            raise AssertionError("expected SystemExit")
        except SystemExit as error:
            assert error.code == 1

        assert not (run_dir / "eval_results.json").exists()
        assert not (run_dir / "golden_dataset_used.json").exists()


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")

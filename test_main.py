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
    overall = results["overall"]
    # Original fields keep their values.
    assert {k: overall[k] for k in ("total_evaluated_pairs", "correct_predictions",
                                    "mismatches", "accuracy")} == {
        "total_evaluated_pairs": 6, "correct_predictions": 6, "mismatches": 0, "accuracy": 1.0}
    # No abstentions: decision metrics equal plain metrics.
    assert overall["abstentions"] == 0
    assert overall["decision_coverage"] == 1.0
    assert overall["undecided_rate"] == 0.0
    assert overall["decision_accuracy"] == overall["accuracy"]
    assert results["mismatches"] == [] and results["abstentions"] == []
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


# --- Abstention (UNDECIDED) ---

def test_pass_golden_undecided_prediction():
    results = run([pred("A1", dict(ALL_PASS, child_safety="UNDECIDED"))], [gold("A1", ALL_PASS)])
    overall = results["overall"]
    assert overall["correct_decisions"] == 2
    assert overall["incorrect_decisions"] == 0 and overall["mismatches"] == 0
    assert overall["abstentions"] == 1
    assert overall["accuracy"] == 2 / 3            # abstention is not correct
    assert overall["decision_accuracy"] == 1.0     # 2 correct of 2 decided
    assert overall["decision_coverage"] == 2 / 3
    assert overall["undecided_rate"] == 1 / 3

    m = results["per_check"]["child_safety"]
    assert m["confusion_matrix"]["expected_PASS"]["predicted_UNDECIDED"] == 1
    assert m["actual_pass"] == 1 and m["predicted_pass"] == 0
    assert m["accuracy"] == 0.0
    assert m["decision_accuracy"] is None          # nothing decided
    assert m["decision_coverage"] == 0.0
    assert m["pass_recall"] == 0.0                 # abstention counts as not recalled
    assert m["pass_precision"] is None             # no predicted PASS
    assert results["mismatches"] == []
    assert results["abstentions"] == [{
        "submission_id": "A1", "policy_check": "child_safety",
        "expected_verdict": "PASS", "predicted_verdict": "UNDECIDED",
        "justification": "why child_safety"}]


def test_fail_golden_undecided_prediction():
    expected = dict(ALL_PASS, real_money_gambling="FAIL")
    predicted = dict(ALL_PASS, real_money_gambling="UNDECIDED")
    results = run([pred("A1", predicted)], [gold("A1", expected)])
    m = results["per_check"]["real_money_gambling"]
    assert m["confusion_matrix"]["expected_FAIL"]["predicted_UNDECIDED"] == 1
    assert m["confusion_matrix"]["expected_FAIL"]["predicted_PASS"] == 0   # not converted to PASS
    assert m["fail_recall"] == 0.0
    assert m["fail_precision"] is None
    assert m["incorrect_decisions"] == 0 and m["abstentions"] == 1
    assert results["mismatches"] == []
    assert results["abstentions"][0]["expected_verdict"] == "FAIL"


def test_mixed_pass_fail_undecided_coverage_and_accuracy():
    # gambling, 6 submissions:  golden F F F P P P
    #                           pred   F P U P F U
    golden_g = ["FAIL", "FAIL", "FAIL", "PASS", "PASS", "PASS"]
    pred_g = ["FAIL", "PASS", "UNDECIDED", "PASS", "FAIL", "UNDECIDED"]
    ids = [f"A{i}" for i in range(6)]
    golden = [gold(i, dict(ALL_PASS, real_money_gambling=g)) for i, g in zip(ids, golden_g)]
    predictions = [pred(i, dict(ALL_PASS, real_money_gambling=p)) for i, p in zip(ids, pred_g)]
    results = run(predictions, golden)

    m = results["per_check"]["real_money_gambling"]
    assert m["confusion_matrix"] == {
        "expected_PASS": {"predicted_PASS": 1, "predicted_FAIL": 1, "predicted_UNDECIDED": 1},
        "expected_FAIL": {"predicted_PASS": 1, "predicted_FAIL": 1, "predicted_UNDECIDED": 1}}
    assert (m["correct_decisions"], m["incorrect_decisions"], m["abstentions"]) == (2, 2, 2)
    assert m["accuracy"] == 2 / 6
    assert m["decision_accuracy"] == 2 / 4
    assert m["decision_coverage"] == 4 / 6
    assert m["undecided_rate"] == 2 / 6
    assert m["pass_precision"] == 1 / 2 and m["pass_recall"] == 1 / 3
    assert m["fail_precision"] == 1 / 2 and m["fail_recall"] == 1 / 3

    overall = results["overall"]   # + 12 correct PASS pairs from the other two checks
    assert overall["total_evaluated_pairs"] == 18
    assert (overall["correct_decisions"], overall["incorrect_decisions"],
            overall["abstentions"]) == (14, 2, 2)
    assert overall["accuracy"] == 14 / 18
    assert overall["decision_accuracy"] == 14 / 16
    assert overall["decision_coverage"] == 16 / 18
    assert len(results["mismatches"]) == 2 and len(results["abstentions"]) == 2


def test_all_undecided_predictions():
    all_undecided = {name: "UNDECIDED" for name in CHECKS}
    results = run([pred("A1", all_undecided), pred("A2", all_undecided)],
                  [gold("A1", ALL_PASS), gold("A2", dict(ALL_PASS, child_safety="FAIL"))])
    overall = results["overall"]
    assert overall["abstentions"] == 6 and overall["correct_decisions"] == 0
    assert overall["accuracy"] == 0.0
    assert overall["decision_accuracy"] is None    # zero decided → undefined, not 0
    assert overall["decision_coverage"] == 0.0
    assert overall["undecided_rate"] == 1.0
    assert overall["mismatches"] == 0

    child = results["per_check"]["child_safety"]
    assert child["pass_precision"] is None and child["fail_precision"] is None
    assert child["pass_recall"] == 0.0 and child["fail_recall"] == 0.0
    meta = results["per_check"]["metadata_quality"]   # only PASS golden labels
    assert meta["fail_recall"] is None               # no actual FAIL → undefined


def test_only_fail_golden_labels():
    all_fail = {name: "FAIL" for name in CHECKS}
    results = run([pred("A1", all_fail)], [gold("A1", all_fail)])
    for name in CHECKS:
        m = results["per_check"][name]
        assert m["pass_precision"] is None and m["pass_recall"] is None
        assert m["fail_precision"] == 1.0 and m["fail_recall"] == 1.0
        assert m["undecided_rate"] == 0.0


def test_golden_rejects_undecided_but_predictions_accept_it():
    assert_error([pred("A1", ALL_PASS)],
                 [gold("A1", dict(ALL_PASS, child_safety="UNDECIDED"))],
                 "golden A1/child_safety: INVALID verdict 'UNDECIDED'")
    assert errors_for([pred("A1", dict(ALL_PASS, child_safety="UNDECIDED"))],
                      [gold("A1", ALL_PASS)]) == []


def test_invalid_prediction_spellings_still_rejected():
    for bad in ["undecided", "Undecided", "UNSURE", "SKIP"]:
        assert_error([pred("A1", dict(ALL_PASS, child_safety=bad))],
                     [gold("A1", ALL_PASS)], "INVALID verdict")


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

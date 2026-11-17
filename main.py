"""Check-level model evaluator (M05).

Compares model predictions against the golden dataset for every
(submission_id, policy_check) pair.

Usage:
    python main.py runs/<run_id>/predictions.json golden_dataset.json

Workflow: LOAD -> VALIDATE -> (stop on errors) -> EVALUATE -> REPORT -> SAVE

Writes into the predictions' run folder:
    eval_results.json         metrics + mismatches (undefined metrics are null)
    golden_dataset_used.json  snapshot of the golden dataset used

Scope: evaluates the MODEL's PASS/FAIL verdicts only.
Routing is not evaluated here. routing_config.json is read only as the
source of truth for the expected policy check names.
"""

import json
import sys
from pathlib import Path

from routing_engine import load_routing_config

VERDICTS = ("PASS", "FAIL")
EVAL_OUTPUT_FILENAME = "eval_results.json"
GOLDEN_SNAPSHOT_FILENAME = "golden_dataset_used.json"


# ---------------------------------------------------------------- LOAD

def load_json(path):
    try:
        with open(path, "r") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: Could not load {path}: {error}")
        sys.exit(1)


# ------------------------------------------------------------ VALIDATE

def _is_valid_verdict(verdict):
    return isinstance(verdict, str) and verdict in VERDICTS


def _index_by_submission_id(records, label, errors):
    """Map submission_id -> record. Records missing IDs or duplicated are reported."""
    by_id = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"{label}[{index}]: INVALID RECORD (not an object)")
            continue
        submission_id = record.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id:
            errors.append(f"{label}[{index}]: MISSING submission_id")
            continue
        if submission_id in by_id:
            errors.append(f"{label} {submission_id}: DUPLICATE submission_id")
            continue
        by_id[submission_id] = record
    return by_id


def _validate_check_names(names, label, submission_id, expected_checks, errors):
    for name in dict.fromkeys(names):
        if names.count(name) > 1:
            errors.append(f"{label} {submission_id}/{name}: DUPLICATE policy check")
        if name not in expected_checks:
            errors.append(f"{label} {submission_id}/{name}: UNKNOWN policy check")
    for name in expected_checks:
        if name not in names:
            errors.append(f"{label} {submission_id}/{name}: MISSING policy check")


def validate_predictions(predictions, expected_checks, errors):
    """Return {submission_id: {policy_check: {"verdict", "justification"}}}."""
    label = "predictions"
    if not isinstance(predictions, list) or len(predictions) == 0:
        errors.append(f"{label}: EMPTY or not a list")
        return {}

    normalized = {}
    for submission_id, record in _index_by_submission_id(predictions, label, errors).items():
        normalized[submission_id] = {}
        checks = record.get("checks")
        if not isinstance(checks, list):
            errors.append(f"{label} {submission_id}: MISSING or invalid 'checks' list")
            continue

        names = []
        for check in checks:
            if not isinstance(check, dict) or "policy_check" not in check or "verdict" not in check:
                errors.append(f"{label} {submission_id}: check MISSING policy_check or verdict")
                continue
            name = check["policy_check"]
            if not isinstance(name, str):
                errors.append(f"{label} {submission_id}: INVALID policy_check {name!r}")
                continue
            names.append(name)
            verdict = check["verdict"]
            if not _is_valid_verdict(verdict):
                errors.append(f"{label} {submission_id}/{name}: INVALID verdict {verdict!r}")
            normalized[submission_id][name] = {
                "verdict": verdict,
                "justification": check.get("justification"),
            }
        _validate_check_names(names, label, submission_id, expected_checks, errors)

    return normalized


def validate_golden(golden, expected_checks, errors):
    """Return {submission_id: {policy_check: expected_verdict}}."""
    label = "golden"
    if not isinstance(golden, list) or len(golden) == 0:
        errors.append(f"{label}: EMPTY or not a list")
        return {}

    normalized = {}
    for submission_id, record in _index_by_submission_id(golden, label, errors).items():
        normalized[submission_id] = {}
        expected = record.get("expected_checks")
        if not isinstance(expected, dict):
            errors.append(f"{label} {submission_id}: MISSING or invalid 'expected_checks' object")
            continue

        for name, verdict in expected.items():
            if not _is_valid_verdict(verdict):
                errors.append(f"{label} {submission_id}/{name}: INVALID verdict {verdict!r}")
            normalized[submission_id][name] = verdict
        _validate_check_names(list(expected), label, submission_id, expected_checks, errors)

    return normalized


def validate(predictions, golden, expected_checks):
    """Validate both datasets. Returns (errors, prediction_map, golden_map)."""
    errors = []
    prediction_map = validate_predictions(predictions, expected_checks, errors)
    golden_map = validate_golden(golden, expected_checks, errors)

    for submission_id in golden_map:
        if submission_id not in prediction_map:
            errors.append(f"{submission_id}: MISSING PREDICTION")
    for submission_id in prediction_map:
        if submission_id not in golden_map:
            errors.append(f"{submission_id}: MISSING GROUND TRUTH (unexpected submission)")

    return errors, prediction_map, golden_map


# ------------------------------------------------------------ EVALUATE

def ratio(numerator, denominator):
    """Return numerator/denominator, or None (JSON null) when the denominator is zero."""
    return None if denominator == 0 else numerator / denominator


def _empty_confusion_matrix():
    # Outer key = expected (actual) verdict, inner key = predicted verdict.
    return {f"expected_{e}": {f"predicted_{p}": 0 for p in VERDICTS} for e in VERDICTS}


def check_metrics(confusion_matrix):
    """Accuracy and PASS/FAIL precision/recall from raw confusion-matrix counts."""
    pass_pass = confusion_matrix["expected_PASS"]["predicted_PASS"]
    pass_fail = confusion_matrix["expected_PASS"]["predicted_FAIL"]
    fail_pass = confusion_matrix["expected_FAIL"]["predicted_PASS"]
    fail_fail = confusion_matrix["expected_FAIL"]["predicted_FAIL"]

    total = pass_pass + pass_fail + fail_pass + fail_fail
    actual_pass = pass_pass + pass_fail
    actual_fail = fail_pass + fail_fail
    predicted_pass = pass_pass + fail_pass
    predicted_fail = pass_fail + fail_fail

    return {
        "total_evaluated": total,
        "actual_pass": actual_pass,
        "actual_fail": actual_fail,
        "predicted_pass": predicted_pass,
        "predicted_fail": predicted_fail,
        "accuracy": ratio(pass_pass + fail_fail, total),
        "pass_precision": ratio(pass_pass, predicted_pass),
        "pass_recall": ratio(pass_pass, actual_pass),
        "fail_precision": ratio(fail_fail, predicted_fail),
        "fail_recall": ratio(fail_fail, actual_fail),
        "confusion_matrix": confusion_matrix,
    }


def evaluate(prediction_map, golden_map, expected_checks):
    """Compare every (submission_id, policy_check) pair. Inputs must be validated."""
    confusion = {name: _empty_confusion_matrix() for name in expected_checks}
    mismatches = []
    correct = 0

    for submission_id in golden_map:
        for name in expected_checks:
            expected = golden_map[submission_id][name]
            prediction = prediction_map[submission_id][name]
            predicted = prediction["verdict"]

            confusion[name][f"expected_{expected}"][f"predicted_{predicted}"] += 1

            if expected == predicted:
                correct += 1
            else:
                mismatches.append({
                    "submission_id": submission_id,
                    "policy_check": name,
                    "expected_verdict": expected,
                    "predicted_verdict": predicted,
                    "justification": prediction["justification"],
                })

    total = len(golden_map) * len(expected_checks)
    return {
        "overall": {
            "total_evaluated_pairs": total,
            "correct_predictions": correct,
            "mismatches": total - correct,
            "accuracy": ratio(correct, total),
        },
        "per_check": {name: check_metrics(confusion[name]) for name in expected_checks},
        "mismatches": mismatches,
    }


# -------------------------------------------------------------- REPORT

def fmt(value):
    """Terminal display: undefined metrics (None) are shown as N/A."""
    return "N/A" if value is None else f"{value:.2%}"


def print_report(results):
    overall = results["overall"]
    print("\n=== OVERALL ===")
    print("Total evaluated pairs:", overall["total_evaluated_pairs"])
    print("Correct predictions:", overall["correct_predictions"])
    print("Mismatches:", overall["mismatches"])
    print("Accuracy:", fmt(overall["accuracy"]))

    for name, m in results["per_check"].items():
        cm = m["confusion_matrix"]
        print(f"\n=== {name} ===")
        print(f"Total: {m['total_evaluated']}  "
              f"Actual PASS/FAIL: {m['actual_pass']}/{m['actual_fail']}  "
              f"Predicted PASS/FAIL: {m['predicted_pass']}/{m['predicted_fail']}")
        print("Accuracy:", fmt(m["accuracy"]))
        print(f"PASS precision: {fmt(m['pass_precision'])}  PASS recall: {fmt(m['pass_recall'])}")
        print(f"FAIL precision: {fmt(m['fail_precision'])}  FAIL recall: {fmt(m['fail_recall'])}")
        print("Confusion matrix (rows = expected, cols = predicted):")
        print("               pred PASS  pred FAIL")
        for expected in VERDICTS:
            row = cm[f"expected_{expected}"]
            print(f"  exp {expected:<4}    {row['predicted_PASS']:>9}  {row['predicted_FAIL']:>9}")

    print("\n=== MISMATCHES ===")
    if not results["mismatches"]:
        print("None")
    for mismatch in results["mismatches"]:
        print(f"{mismatch['submission_id']}/{mismatch['policy_check']}: "
              f"expected {mismatch['expected_verdict']}, "
              f"predicted {mismatch['predicted_verdict']}")
        print("  Justification:", mismatch["justification"])


# ---------------------------------------------------------------- MAIN

def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        print("ERROR: Incorrect number of arguments.")
        print("Usage: python main.py runs/<run_id>/predictions.json golden_dataset.json")
        sys.exit(1)

    predictions_path = Path(argv[1])
    golden_path = Path(argv[2])

    # LOAD
    expected_checks = list(load_routing_config())
    predictions = load_json(predictions_path)
    golden = load_json(golden_path)
    print("Policy checks:", expected_checks)
    print("Total predictions:", len(predictions) if isinstance(predictions, list) else "N/A")
    print("Total golden submissions:", len(golden) if isinstance(golden, list) else "N/A")

    # VALIDATE
    errors, prediction_map, golden_map = validate(predictions, golden, expected_checks)
    if errors:
        for error in errors:
            print(error)
        print(f"Validation failed: {len(errors)} error(s). No metrics calculated.")
        sys.exit(1)

    # EVALUATE
    results = {
        "predictions_file": str(predictions_path),
        "golden_dataset_file": str(golden_path),
        "policy_checks": expected_checks,
        **evaluate(prediction_map, golden_map, expected_checks),
    }

    # REPORT
    print_report(results)

    # SAVE
    run_dir = predictions_path.parent
    eval_path = run_dir / EVAL_OUTPUT_FILENAME
    golden_snapshot_path = run_dir / GOLDEN_SNAPSHOT_FILENAME
    with open(eval_path, "w") as file:
        json.dump(results, file, indent=2)
    with open(golden_snapshot_path, "w") as file:
        json.dump(golden, file, indent=2)
    print("\nEval results written to:", eval_path)
    print("Golden dataset snapshot written to:", golden_snapshot_path)

    return results


if __name__ == "__main__":
    main()

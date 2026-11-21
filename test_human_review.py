"""Tests for human_review.py (pure functions, inline synthetic data, no I/O).

Run with:  python test_human_review.py   (or: pytest test_human_review.py)

Routing records are produced by the real routing_engine.route_submission()
from inline predictions that mirror the M07.3 baseline run, so the fixtures
have the exact shape of routing_results.json.
"""

import contextlib
import copy
import io
import json
import tempfile
from pathlib import Path

from human_review import (
    COMPLETION_OUTPUT_FILENAME,
    STATUS_PENDING,
    STATUS_RESOLVED,
    complete_submission,
    main,
    validate_decisions,
)
from routing_engine import route_submission

CONFIG = {   # same permissions as routing_config.json
    "metadata_quality": {"auto_approve": True, "auto_reject": False},
    "real_money_gambling": {"auto_approve": True, "auto_reject": False},
    "child_safety": {"auto_approve": True, "auto_reject": False},
}
EXPECTED = list(CONFIG)


def check(name, verdict, justification=None):
    return {"policy_check": name, "verdict": verdict,
            "justification": justification or f"AI says {verdict} for {name}"}


def routed(submission_id, checks):
    return route_submission({"submission_id": submission_id, "checks": checks}, CONFIG)


# APP001: all automated. APP002: AI FAIL -> CONFIG_REQUIRES_HUMAN.
# SYN001: AI UNDECIDED -> MODEL_UNDECIDED.
APP001 = routed("APP001", [check(n, "PASS") for n in EXPECTED])
APP002 = routed("APP002", [check("metadata_quality", "PASS"),
                           check("real_money_gambling", "FAIL", "Real money and cash prizes."),
                           check("child_safety", "PASS")])
SYN001 = routed("SYN001", [check("metadata_quality", "PASS"),
                           check("real_money_gambling", "UNDECIDED",
                                 "Prizes mentioned; unclear whether cash."),
                           check("child_safety", "PASS")])
# Anomalies: duplicate gambling, missing child_safety, unknown extra check.
ANOM = routed("ANOM01", [check("metadata_quality", "PASS"),
                         check("real_money_gambling", "PASS"),
                         check("real_money_gambling", "FAIL"),
                         check("violence", "PASS")])
ROUTING = {r["submission_id"]: r for r in (APP001, APP002, SYN001, ANOM)}


def decision(submission_id="SYN001", policy_check="real_money_gambling", value="PASS", **overrides):
    record = {"submission_id": submission_id, "policy_check": policy_check, "decision": value,
              "reviewer": "reviewer-01", "rationale": "Checked the evidence.",
              "decided_at": "2026-11-20T10:00:00+00:00"}
    record.update(overrides)
    return record


def validate(decisions):
    errors = []
    valid = validate_decisions(decisions, ROUTING, EXPECTED, errors)
    return valid, errors


def for_submission(valid, submission_id):
    return {name: d for (sid, name), d in valid.items() if sid == submission_id}


def only_error(decisions, code):
    valid, errors = validate(decisions)
    assert valid == {}, valid
    assert len(errors) >= 1 and all(code in e for e in errors), (code, errors)
    return errors


# --- Fixture sanity: routing produced the cases we rely on ---

def test_fixture_routing_reasons():
    reasons = lambda r: [(c["policy_check"], c["routing_reason"]) for c in r["checks"]]
    assert APP002["checks"][1]["routing_reason"] == "CONFIG_REQUIRES_HUMAN"
    assert SYN001["checks"][1]["routing_reason"] == "MODEL_UNDECIDED"
    assert reasons(ANOM) == [("metadata_quality", "CONFIG_ALLOWS_AUTO_APPROVE"),
                             ("real_money_gambling", "DUPLICATE_CHECK"),
                             ("real_money_gambling", "DUPLICATE_CHECK"),
                             ("violence", "UNKNOWN_CHECK"),
                             ("child_safety", "MISSING_CHECK")]


# --- Scenario 1: invalid or unauthorized human decisions ---

def test_scenario1_unauthorized_decisions_are_rejected():
    cases = {
        "NOT_PENDING_HUMAN_REVIEW": decision("APP001", "metadata_quality"),   # AUTO_APPROVE
        "UNKNOWN_SUBMISSION": decision("APP999"),
        "UNKNOWN_CHECK": decision("SYN001", "violence"),
    }
    for code, record in cases.items():
        errors = only_error([record], code)
        if code == "NOT_PENDING_HUMAN_REVIEW":
            assert "routed AUTO_APPROVE" in errors[0]


def test_scenario1_decisions_on_anomalous_checks_are_rejected():
    for name in ("real_money_gambling", "child_safety"):   # DUPLICATE_CHECK, MISSING_CHECK
        errors = only_error([decision("ANOM01", name)], "CHECK_HAS_ANOMALY")
        assert ("DUPLICATE_CHECK" if name == "real_money_gambling" else "MISSING_CHECK") in errors[0]
    # The unknown routed check "violence" is not a policy check a human can decide.
    only_error([decision("ANOM01", "violence")], "UNKNOWN_CHECK")


def test_scenario1_invalid_decision_records_are_rejected():
    cases = {
        "INVALID_DECISION": [decision(value="UNDECIDED"), decision(value=None),
                             decision(value="pass")],
        "MISSING_FIELD reviewer": [{k: v for k, v in decision().items() if k != "reviewer"}],
        "UNKNOWN_FIELD final_disposition": [decision(final_disposition="REJECT")],
        "INVALID_FIELD rationale": [decision(rationale="  ")],
        "INVALID_FIELD decided_at": [decision(decided_at="yesterday")],
        "INVALID_RECORD": ["SYN001 PASS"],
    }
    for code, records in cases.items():
        for record in records:
            only_error([record], code)
    _, errors = validate({"not": "a list"})
    assert errors == ["human_decisions: NOT_A_LIST"]


def test_scenario1_errors_are_collected_not_raised():
    valid, errors = validate([decision("APP001", "metadata_quality"),
                              decision(value="UNDECIDED"),
                              decision("APP002", value="FAIL")])        # this one is valid
    assert len(errors) == 2
    assert list(valid) == [("APP002", "real_money_gambling")]   # caller must still write nothing


# --- Scenario 2: duplicate or conflicting decisions ---

def test_scenario2_conflicting_decisions_are_rejected():
    errors = only_error([decision(value="PASS"), decision(value="FAIL")], "DUPLICATE_DECISION")
    assert errors == ["human_decisions SYN001/real_money_gambling: DUPLICATE_DECISION "
                      "at indexes [0, 1] (decisions ['PASS', 'FAIL'])"]


def test_scenario2_identical_duplicates_are_also_rejected():
    errors = only_error([decision(), decision()], "DUPLICATE_DECISION")
    assert "indexes [0, 1]" in errors[0]


def test_scenario2_duplicate_does_not_affect_other_valid_decisions():
    valid, errors = validate([decision(), decision("APP002", value="FAIL"), decision()])
    assert len(errors) == 1 and "SYN001/real_money_gambling: DUPLICATE_DECISION" in errors[0]
    assert list(valid) == [("APP002", "real_money_gambling")]


# --- Scenario 3: partial completion preserves AI and routing information ---

def test_scenario3_partial_completion_preserves_ai_and_routing():
    originals = copy.deepcopy([SYN001, APP002])
    valid, errors = validate([decision("SYN001", value="PASS")])   # nothing for APP002
    assert errors == []

    syn = complete_submission(SYN001, for_submission(valid, "SYN001"), EXPECTED)
    app = complete_submission(APP002, for_submission(valid, "APP002"), EXPECTED)

    # SYN001: human resolved the UNDECIDED check.
    assert syn["completion_status"] == STATUS_RESOLVED
    assert syn["routing_workflow_status"] == "PENDING_HUMAN_REVIEW"   # routing record unchanged
    assert syn["pending_checks"] == [] and syn["unresolved_anomalies"] == []
    gambling = syn["checks"][1]
    assert gambling["final_outcome"] == "PASS" and gambling["outcome_source"] == "HUMAN"
    assert gambling["human_decision"] == decision()
    assert gambling["human_agrees_with_ai"] is None            # AI abstained
    assert [c["outcome_source"] for c in syn["checks"]] == ["AI", "HUMAN", "AI"]

    # APP002: no decision yet -> still pending, nothing invented.
    assert app["completion_status"] == STATUS_PENDING
    assert app["pending_checks"] == ["real_money_gambling"]
    pending = app["checks"][1]
    assert pending["final_outcome"] is None and pending["outcome_source"] is None
    assert pending["human_decision"] is None

    # Original AI verdict, justification, routing action/reason/source kept verbatim.
    for result, original in ((syn, originals[0]), (app, originals[1])):
        assert [c["routing"] for c in result["checks"]] == original["checks"]
    assert gambling["routing"]["verdict"] == "UNDECIDED"
    assert gambling["routing"]["result_source"] == "PENDING_HUMAN"
    assert pending["routing"]["justification"] == "Real money and cash prizes."
    # Inputs not mutated.
    assert [SYN001, APP002] == originals
    # No application-level disposition anywhere.
    for result in (syn, app):
        assert not {"disposition", "approved", "rejected", "final_disposition"} & set(result)


def test_human_override_and_agreement_are_recorded():
    valid, errors = validate([decision("APP002", value="PASS")])   # human overrides AI FAIL
    assert errors == []
    result = complete_submission(APP002, for_submission(valid, "APP002"), EXPECTED)
    gambling = result["checks"][1]
    assert gambling["final_outcome"] == "PASS" and gambling["routing"]["verdict"] == "FAIL"
    assert gambling["human_agrees_with_ai"] is False
    assert result["completion_status"] == STATUS_RESOLVED   # check-level only, not "approved"

    valid, _ = validate([decision("APP002", value="FAIL")])
    agreed = complete_submission(APP002, for_submission(valid, "APP002"), EXPECTED)["checks"][1]
    assert agreed["human_agrees_with_ai"] is True


def test_fully_automated_submission_is_resolved_by_ai():
    result = complete_submission(APP001, {}, EXPECTED)
    assert result["completion_status"] == STATUS_RESOLVED
    assert [(c["final_outcome"], c["outcome_source"]) for c in result["checks"]] == [("PASS", "AI")] * 3


# --- Anomalies: preserved, listed, never resolved ---

def test_anomalies_are_preserved_and_block_resolution():
    original = copy.deepcopy(ANOM)
    result = complete_submission(ANOM, {}, EXPECTED)

    assert result["completion_status"] == STATUS_PENDING
    assert result["pending_checks"] == []        # nothing a human can decide here
    assert result["unresolved_anomalies"] == [
        {"policy_check": "real_money_gambling", "routing_reason": "DUPLICATE_CHECK"},
        {"policy_check": "real_money_gambling", "routing_reason": "DUPLICATE_CHECK"},
        {"policy_check": "violence", "routing_reason": "UNKNOWN_CHECK"},
        {"policy_check": "child_safety", "routing_reason": "MISSING_CHECK"},
    ]
    # Every routed entry kept, in order, verbatim (both duplicate AI verdicts too).
    assert [c["routing"] for c in result["checks"]] == original["checks"]
    assert [c["routing"]["verdict"] for c in result["checks"][1:3]] == ["PASS", "FAIL"]
    for entry in result["checks"][1:]:
        assert entry["anomaly"] is True and entry["final_outcome"] is None
    assert ANOM == original


def test_invalid_verdict_is_an_anomaly_even_if_other_checks_are_decided():
    record = routed("BAD01", [check("metadata_quality", "PASS"),
                              check("real_money_gambling", "MAYBE"),
                              check("child_safety", "UNDECIDED")])
    assert record["checks"][1]["routing_reason"] == "INVALID_VERDICT"
    errors = []
    valid = validate_decisions([decision("BAD01", "child_safety")], {"BAD01": record},
                               EXPECTED, errors)
    assert errors == []
    result = complete_submission(record, for_submission(valid, "BAD01"), EXPECTED)
    assert result["completion_status"] == STATUS_PENDING   # human decided, anomaly remains
    assert result["unresolved_anomalies"] == [
        {"policy_check": "real_money_gambling", "routing_reason": "INVALID_VERDICT"}]


def test_expected_check_absent_from_malformed_routing_record_blocks_resolution():
    malformed = copy.deepcopy(APP001)
    malformed["checks"] = malformed["checks"][:2]          # child_safety entry removed
    result = complete_submission(malformed, {}, EXPECTED)
    assert result["completion_status"] == STATUS_PENDING
    assert result["unresolved_anomalies"] == [
        {"policy_check": "child_safety", "routing_reason": "NOT_IN_ROUTING_RESULTS"}]


def test_duplicate_automated_entries_are_anomalies():
    duplicated = copy.deepcopy(APP001)
    duplicated["checks"].append(copy.deepcopy(duplicated["checks"][0]))   # 2x AUTO_APPROVE
    original = copy.deepcopy(duplicated)

    result = complete_submission(duplicated, {}, EXPECTED)
    assert result["completion_status"] == STATUS_PENDING
    assert result["unresolved_anomalies"] == [
        {"policy_check": "metadata_quality", "routing_reason": "DUPLICATE_ROUTED_ENTRY"}] * 2
    assert [c["routing"] for c in result["checks"]] == original["checks"]   # both kept
    for entry in (result["checks"][0], result["checks"][3]):
        assert entry["anomaly"] is True and entry["final_outcome"] is None
    assert duplicated == original

    errors = []
    valid = validate_decisions([decision("APP001", "metadata_quality")],
                               {"APP001": duplicated}, EXPECTED, errors)
    assert valid == {} and len(errors) == 1 and "CHECK_HAS_ANOMALY" in errors[0]


def test_automated_action_contradicting_verdict_is_anomaly():
    for action, verdict in (("AUTO_APPROVE", "FAIL"), ("AUTO_REJECT", "PASS"),
                            ("AUTO_APPROVE", "UNDECIDED")):
        record = copy.deepcopy(APP001)
        record["checks"][0].update(routing_action=action, verdict=verdict)
        original = copy.deepcopy(record)

        result = complete_submission(record, {}, EXPECTED)
        assert result["completion_status"] == STATUS_PENDING, (action, verdict)
        assert result["unresolved_anomalies"] == [
            {"policy_check": "metadata_quality",
             "routing_reason": "AUTOMATED_ACTION_CONTRADICTS_VERDICT"}]
        entry = result["checks"][0]
        assert entry["anomaly"] is True and entry["final_outcome"] is None
        assert entry["outcome_source"] is None and entry["routing"] == original["checks"][0]
        assert record == original

        errors = []
        valid = validate_decisions([decision("APP001", "metadata_quality")],
                                   {"APP001": record}, EXPECTED, errors)
        assert valid == {} and len(errors) == 1
        assert "CHECK_HAS_ANOMALY ['AUTOMATED_ACTION_CONTRADICTS_VERDICT']" in errors[0]


def test_unvalidated_decision_for_non_human_check_raises():
    for record, name in ((APP001, "metadata_quality"), (ANOM, "real_money_gambling")):
        try:
            complete_submission(record, {name: decision(record["submission_id"], name)}, EXPECTED)
            raise AssertionError("expected ValueError")
        except ValueError as error:
            assert "validate_decisions" in str(error)


# --- CLI (M08.2): file input/output in a temporary run folder ---

def make_run(tmp, routing_results, decisions, config=CONFIG):
    """Write a run folder like the pipeline does; return (argv, run_dir)."""
    run_dir = Path(tmp) / "run"
    run_dir.mkdir()
    (run_dir / "predictions.json").write_text('[{"untouched": true}]')
    (run_dir / "routing_results.json").write_text(json.dumps(routing_results))
    if config is not None:
        (run_dir / "routing_config_used.json").write_text(json.dumps(config))
    decisions_path = Path(tmp) / "human_decisions.json"
    if decisions is not None:
        decisions_path.write_text(json.dumps(decisions))
    return ["human_review.py", str(run_dir / "routing_results.json"), str(decisions_path)], run_dir


def run_cli(argv):
    """Run main(); return (exit_code, stdout). exit_code is None on success."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            main(argv)
            return None, out.getvalue()
        except SystemExit as error:
            return error.code, out.getvalue()


def test_cli_successful_completion_writes_output_and_keeps_inputs():
    with tempfile.TemporaryDirectory() as tmp:
        argv, run_dir = make_run(tmp, [APP001, APP002], [decision("APP002", value="FAIL")])
        inputs_before = {name: (run_dir / name).read_bytes()
                         for name in ("predictions.json", "routing_results.json")}

        code, out = run_cli(argv)
        assert code is None, out
        saved = json.loads((run_dir / COMPLETION_OUTPUT_FILENAME).read_text())
        assert saved["policy_checks"] == EXPECTED
        assert saved["summary"] == {"total_submissions": 2, "checks_resolved": 2,
                                    "pending_human_review": 0, "human_decisions_applied": 1}
        app002 = saved["submissions"][1]
        assert app002["completion_status"] == STATUS_RESOLVED
        assert app002["checks"][1]["outcome_source"] == "HUMAN"
        assert app002["checks"][1]["routing"] == APP002["checks"][1]
        assert {name: (run_dir / name).read_bytes() for name in inputs_before} == inputs_before
        assert sorted(p.name for p in run_dir.iterdir()) == sorted(
            [*inputs_before, "routing_config_used.json", COMPLETION_OUTPUT_FILENAME])


def test_cli_missing_decisions_leave_checks_pending():
    with tempfile.TemporaryDirectory() as tmp:
        argv, run_dir = make_run(tmp, [APP002, SYN001], [decision("SYN001", value="PASS")])
        code, out = run_cli(argv)
        assert code is None, out
        saved = json.loads((run_dir / COMPLETION_OUTPUT_FILENAME).read_text())
        statuses = {s["submission_id"]: s["completion_status"] for s in saved["submissions"]}
        assert statuses == {"APP002": STATUS_PENDING, "SYN001": STATUS_RESOLVED}
        assert saved["submissions"][0]["pending_checks"] == ["real_money_gambling"]
        assert saved["summary"]["pending_human_review"] == 1

        # An empty decisions file is valid too: everything needing a human stays pending.
        (Path(tmp) / "human_decisions.json").write_text("[]")
        assert run_cli(argv)[0] is None
        saved = json.loads((run_dir / COMPLETION_OUTPUT_FILENAME).read_text())
        assert saved["summary"]["checks_resolved"] == 0


def test_cli_invalid_decision_exits_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        argv, run_dir = make_run(tmp, [APP001, APP002],
                                 [decision("APP002", value="FAIL"),            # valid
                                  decision("APP001", "metadata_quality"),      # AUTO_APPROVE
                                  decision("APP002", value="UNDECIDED")])      # invalid value
        code, out = run_cli(argv)
        assert code == 1
        assert "NOT_PENDING_HUMAN_REVIEW" in out and "INVALID_DECISION" in out
        assert "Validation failed: 2 error(s)" in out
        assert not (run_dir / COMPLETION_OUTPUT_FILENAME).exists()


def test_cli_missing_or_malformed_input_file_exits_and_writes_nothing():
    cases = {
        "decisions": dict(decisions=None),
        "config snapshot": dict(decisions=[], config=None),
    }
    for label, kwargs in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            argv, run_dir = make_run(tmp, [APP001], **kwargs)
            code, out = run_cli(argv)
            assert code == 1, label
            assert out.startswith("ERROR: Could not load"), (label, out)
            assert not (run_dir / COMPLETION_OUTPUT_FILENAME).exists()

    with tempfile.TemporaryDirectory() as tmp:
        argv, run_dir = make_run(tmp, [APP001], [])
        (Path(tmp) / "human_decisions.json").write_text("{not json")
        code, out = run_cli(argv)
        assert code == 1 and "ERROR: Could not load" in out
        (Path(tmp) / "human_decisions.json").write_text("[]")
        (run_dir / "routing_results.json").write_text("[]")       # empty routing results
        code, out = run_cli(argv)
        assert code == 1 and "routing_results: EMPTY or not a list" in out
        assert not (run_dir / COMPLETION_OUTPUT_FILENAME).exists()

    assert run_cli(["human_review.py"])[0] == 1                    # wrong argument count


def test_cli_failure_preserves_existing_output_with_stale_warning():
    with tempfile.TemporaryDirectory() as tmp:
        argv, run_dir = make_run(tmp, [APP002], [decision("APP002", value="FAIL")])
        assert run_cli(argv)[0] is None, "first run should succeed"
        output = run_dir / COMPLETION_OUTPUT_FILENAME
        previous = output.read_bytes()
        decisions_path = Path(tmp) / "human_decisions.json"

        decisions_path.write_text(json.dumps([decision("APP002", value="UNDECIDED")]))
        code, out = run_cli(argv)                                   # validation failure
        assert code == 1 and "was NOT updated and may contain stale results" in out
        assert output.read_bytes() == previous

        decisions_path.unlink()
        code, out = run_cli(argv)                                   # loading failure
        assert code == 1 and "ERROR: Could not load" in out
        assert "was NOT updated and may contain stale results" in out
        assert output.read_bytes() == previous

    with tempfile.TemporaryDirectory() as tmp:                      # no prior output: no warning
        argv, _ = make_run(tmp, [APP002], [decision("APP002", value="UNDECIDED")])
        code, out = run_cli(argv)
        assert code == 1 and "WARNING" not in out


def test_example_decisions_file_is_valid_for_reference_run():
    root = Path(__file__).parent
    decisions = json.loads((root / "fixtures" / "example_human_decisions.json").read_text())
    routing = json.loads((root / "runs" / "2026-11-15_15-36-16" / "routing_results.json").read_text())
    errors = []
    valid = validate_decisions(decisions, {r["submission_id"]: r for r in routing}, EXPECTED, errors)
    assert errors == [] and list(valid) == [("APP002", "real_money_gambling")]


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")

"""Tests for routing_engine.py.

Run with:  python test_routing.py   (or: pytest test_routing.py)

Uses an inline config so tests don't depend on the current routing_config.json.
"""

from routing_engine import route_check, route_submission

TEST_CONFIG = {
    "metadata_quality": {"auto_approve": True, "auto_reject": False},
    "real_money_gambling": {"auto_approve": True, "auto_reject": True},
    "child_safety": {"auto_approve": True, "auto_reject": False},
}


def make_check(policy_check, verdict, justification="Some reason."):
    return {"policy_check": policy_check, "verdict": verdict, "justification": justification}


def all_pass_checks():
    return [make_check(name, "PASS") for name in TEST_CONFIG]


# --- Check-level routing ---

def test_pass_with_auto_approve_is_auto_approved():
    result = route_check(make_check("metadata_quality", "PASS"), TEST_CONFIG)
    assert result["routing_action"] == "AUTO_APPROVE"
    assert result["result_source"] == "AI"
    assert result["routing_reason"] == "CONFIG_ALLOWS_AUTO_APPROVE"


def test_fail_with_auto_reject_is_auto_rejected():
    result = route_check(make_check("real_money_gambling", "FAIL"), TEST_CONFIG)
    assert result["routing_action"] == "AUTO_REJECT"
    assert result["result_source"] == "AI"
    assert result["routing_reason"] == "CONFIG_ALLOWS_AUTO_REJECT"


def test_fail_without_auto_reject_goes_to_human():
    result = route_check(make_check("child_safety", "FAIL"), TEST_CONFIG)
    assert result["routing_action"] == "HUMAN_REVIEW"
    assert result["result_source"] == "PENDING_HUMAN"
    assert result["routing_reason"] == "CONFIG_REQUIRES_HUMAN"


def test_original_verdict_and_justification_preserved():
    result = route_check(make_check("child_safety", "FAIL", "Unsafe chat."), TEST_CONFIG)
    assert result["policy_check"] == "child_safety"
    assert result["verdict"] == "FAIL"
    assert result["justification"] == "Unsafe chat."


def test_unknown_check_goes_to_human():
    result = route_check(make_check("violence", "PASS"), TEST_CONFIG)
    assert result["routing_action"] == "HUMAN_REVIEW"
    assert result["routing_reason"] == "UNKNOWN_CHECK"


def test_invalid_verdicts_go_to_human():
    for bad_verdict in ["approved", "pass", "", None]:
        result = route_check(make_check("metadata_quality", bad_verdict), TEST_CONFIG)
        assert result["routing_action"] == "HUMAN_REVIEW", bad_verdict
        assert result["routing_reason"] == "INVALID_VERDICT", bad_verdict


def test_non_string_verdicts_go_to_human():
    for bad_verdict in [["PASS"], {"value": "PASS"}, 1, True]:
        result = route_check(make_check("metadata_quality", bad_verdict), TEST_CONFIG)
        assert result["routing_action"] == "HUMAN_REVIEW", bad_verdict
        assert result["routing_reason"] == "INVALID_VERDICT", bad_verdict
        assert result["verdict"] == bad_verdict  # original value preserved


def test_missing_config_flag_goes_to_human():
    config = {"metadata_quality": {}}
    result = route_check(make_check("metadata_quality", "PASS"), config)
    assert result["routing_action"] == "HUMAN_REVIEW"
    assert result["routing_reason"] == "CONFIG_REQUIRES_HUMAN"


def test_non_boolean_config_flag_goes_to_human():
    config = {"metadata_quality": {"auto_approve": "true", "auto_reject": False}}
    result = route_check(make_check("metadata_quality", "PASS"), config)
    assert result["routing_action"] == "HUMAN_REVIEW"


def test_missing_justification_does_not_change_routing():
    check = {"policy_check": "metadata_quality", "verdict": "PASS"}
    result = route_check(check, TEST_CONFIG)
    assert result["routing_action"] == "AUTO_APPROVE"
    assert result["justification"] is None


# --- Submission-level workflow ---

def test_all_automated_checks_complete():
    result = route_submission({"submission_id": "APP001", "checks": all_pass_checks()}, TEST_CONFIG)
    assert result["submission_id"] == "APP001"
    assert result["workflow_status"] == "AUTOMATED_CHECKS_COMPLETE"
    assert result["human_review_checks"] == []
    assert len(result["checks"]) == 3


def test_one_human_check_makes_submission_pending():
    checks = all_pass_checks()
    checks[2] = make_check("child_safety", "FAIL")
    result = route_submission({"submission_id": "APP002", "checks": checks}, TEST_CONFIG)
    assert result["workflow_status"] == "PENDING_HUMAN_REVIEW"
    assert result["human_review_checks"] == ["child_safety"]


def test_auto_reject_does_not_reject_whole_submission():
    checks = all_pass_checks()
    checks[1] = make_check("real_money_gambling", "FAIL")
    result = route_submission({"submission_id": "APP003", "checks": checks}, TEST_CONFIG)
    assert result["checks"][1]["routing_action"] == "AUTO_REJECT"
    assert result["workflow_status"] == "AUTOMATED_CHECKS_COMPLETE"
    assert "REJECT" not in result["workflow_status"]


def test_missing_check_gets_placeholder_and_pending():
    checks = all_pass_checks()[:2]  # child_safety missing
    result = route_submission({"submission_id": "APP004", "checks": checks}, TEST_CONFIG)
    assert result["workflow_status"] == "PENDING_HUMAN_REVIEW"
    assert result["human_review_checks"] == ["child_safety"]
    placeholder = result["checks"][-1]
    assert placeholder["routing_reason"] == "MISSING_CHECK"
    assert placeholder["verdict"] is None


def test_duplicate_check_goes_to_human():
    checks = all_pass_checks() + [make_check("metadata_quality", "PASS")]
    result = route_submission({"submission_id": "APP005", "checks": checks}, TEST_CONFIG)
    assert result["workflow_status"] == "PENDING_HUMAN_REVIEW"
    assert result["human_review_checks"] == ["metadata_quality"]
    duplicates = [c for c in result["checks"] if c["routing_reason"] == "DUPLICATE_CHECK"]
    assert len(duplicates) == 2


def test_empty_or_missing_checks_is_pending():
    for submission in [{"submission_id": "APP006", "checks": []},
                       {"submission_id": "APP007"},
                       {"submission_id": "APP008", "checks": "not a list"}]:
        result = route_submission(submission, TEST_CONFIG)
        assert result["workflow_status"] == "PENDING_HUMAN_REVIEW"
        assert result["human_review_checks"] == list(TEST_CONFIG)


def test_unknown_check_makes_submission_pending():
    checks = all_pass_checks() + [make_check("violence", "PASS")]
    result = route_submission({"submission_id": "APP009", "checks": checks}, TEST_CONFIG)
    assert result["workflow_status"] == "PENDING_HUMAN_REVIEW"
    assert result["human_review_checks"] == ["violence"]


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\nAll {len(tests)} tests passed.")

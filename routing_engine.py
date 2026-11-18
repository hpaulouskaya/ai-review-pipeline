"""Check-level routing and submission-level workflow tracking (M04).

Usage:
    python routing_engine.py runs/<run_id>/predictions.json

Writes routing_results.json into the same run folder.

Scope: this module decides, per policy check, whether the AI result can be
used automatically or needs human review. It does NOT decide final
submission dispositions, notifications, or enforcement.
"""

import json
import sys
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "routing_config.json"
ROUTING_OUTPUT_FILENAME = "routing_results.json"
CONFIG_SNAPSHOT_FILENAME = "routing_config_used.json"
VALID_VERDICTS = {"PASS", "FAIL", "UNDECIDED"}


def load_routing_config(path=DEFAULT_CONFIG_PATH):
    """Load routing permissions. Fails loudly if the file is missing or malformed."""
    with open(path, "r") as file:
        routing_config = json.load(file)

    if not isinstance(routing_config, dict) or len(routing_config) == 0:
        raise ValueError(f"{path} must be a non-empty JSON object")

    return routing_config


def _routed_check(policy_check, verdict, justification, routing_action, routing_reason):
    """Build one routed check, preserving the original verdict and justification."""
    return {
        "policy_check": policy_check,
        "verdict": verdict,
        "justification": justification,
        "routing_action": routing_action,
        "result_source": "PENDING_HUMAN" if routing_action == "HUMAN_REVIEW" else "AI",
        "routing_reason": routing_reason,
    }


def route_check(check, routing_config):
    """Route a single policy check to AUTO_APPROVE, AUTO_REJECT or HUMAN_REVIEW.

    Anything unexpected (unknown check, invalid verdict, missing or non-boolean
    config flag) falls through to HUMAN_REVIEW. An UNDECIDED verdict always
    goes to HUMAN_REVIEW, regardless of config. Justification is preserved
    but never affects routing.
    """
    if not isinstance(check, dict):
        check = {}

    policy_check = check.get("policy_check")
    verdict = check.get("verdict")
    justification = check.get("justification")

    if not isinstance(policy_check, str) or policy_check not in routing_config:
        return _routed_check(policy_check, verdict, justification,
                             "HUMAN_REVIEW", "UNKNOWN_CHECK")

    if not isinstance(verdict, str) or verdict not in VALID_VERDICTS:
        return _routed_check(policy_check, verdict, justification,
                             "HUMAN_REVIEW", "INVALID_VERDICT")

    # The model could not reach a justified decision: a human must decide.
    # Checked before the config, so no permission can automate it.
    if verdict == "UNDECIDED":
        return _routed_check(policy_check, verdict, justification,
                             "HUMAN_REVIEW", "MODEL_UNDECIDED")

    permissions = routing_config[policy_check]
    if not isinstance(permissions, dict):
        permissions = {}

    # Only an explicit boolean True allows automation.
    auto_approve_allowed = permissions.get("auto_approve") is True
    auto_reject_allowed = permissions.get("auto_reject") is True

    if verdict == "PASS" and auto_approve_allowed:
        return _routed_check(policy_check, verdict, justification,
                             "AUTO_APPROVE", "CONFIG_ALLOWS_AUTO_APPROVE")

    if verdict == "FAIL" and auto_reject_allowed:
        return _routed_check(policy_check, verdict, justification,
                             "AUTO_REJECT", "CONFIG_ALLOWS_AUTO_REJECT")

    return _routed_check(policy_check, verdict, justification,
                         "HUMAN_REVIEW", "CONFIG_REQUIRES_HUMAN")


def route_submission(submission, routing_config):
    """Route every check of one submission and track its workflow status.

    - Each check is routed independently.
    - Duplicate checks all go to HUMAN_REVIEW.
    - Expected checks (the keys of routing_config) that are missing get a
      HUMAN_REVIEW placeholder.
    - An individual AUTO_REJECT does NOT reject the whole submission.
    """
    if not isinstance(submission, dict):
        submission = {}

    checks = submission.get("checks")
    if not isinstance(checks, list):
        checks = []

    check_names = [
        check.get("policy_check") if isinstance(check, dict) else None
        for check in checks
    ]

    routed_checks = []
    for check, name in zip(checks, check_names):
        if name is not None and check_names.count(name) > 1:
            routed_checks.append(_routed_check(
                name, check.get("verdict"), check.get("justification"),
                "HUMAN_REVIEW", "DUPLICATE_CHECK"))
        else:
            routed_checks.append(route_check(check, routing_config))

    for expected_check in routing_config:
        if expected_check not in check_names:
            routed_checks.append(_routed_check(
                expected_check, None, None,
                "HUMAN_REVIEW", "MISSING_CHECK"))

    human_review_checks = []
    for routed in routed_checks:
        if (routed["routing_action"] == "HUMAN_REVIEW"
                and routed["policy_check"] not in human_review_checks):
            human_review_checks.append(routed["policy_check"])

    needs_human = len(routed_checks) == 0 or any(
        routed["routing_action"] == "HUMAN_REVIEW" for routed in routed_checks
    )

    return {
        "submission_id": submission.get("submission_id"),
        "workflow_status": "PENDING_HUMAN_REVIEW" if needs_human else "AUTOMATED_CHECKS_COMPLETE",
        "human_review_checks": human_review_checks,
        "checks": routed_checks,
    }


def main():
    if len(sys.argv) != 2:
        print("ERROR: Incorrect number of arguments.")
        print("Usage: python routing_engine.py runs/<run_id>/predictions.json")
        sys.exit(1)

    predictions_path = Path(sys.argv[1])
    routing_config = load_routing_config()

    with open(predictions_path, "r") as file:
        predictions = json.load(file)

    if not isinstance(predictions, list) or len(predictions) == 0:
        print("ERROR: Predictions file must be a non-empty JSON list.")
        sys.exit(1)

    routing_results = [
        route_submission(submission, routing_config)
        for submission in predictions
    ]

    output_path = predictions_path.parent / ROUTING_OUTPUT_FILENAME
    with open(output_path, "w") as file:
        json.dump(routing_results, file, indent=2)

    # Record exactly which config produced these results.
    config_snapshot_path = predictions_path.parent / CONFIG_SNAPSHOT_FILENAME
    with open(config_snapshot_path, "w") as file:
        json.dump(routing_config, file, indent=2)

    for result in routing_results:
        print(result["submission_id"], result["workflow_status"],
              "human review:", result["human_review_checks"])
    print("Routing results written to:", output_path)
    print("Config snapshot written to:", config_snapshot_path)


if __name__ == "__main__":
    main()

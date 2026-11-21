"""Human Review Completion (M08.1 core logic, M08.2 CLI).

Combines one submission's routing record (from routing_results.json) with
validated human PASS/FAIL decisions into a derived completion record.

Usage:
    python human_review.py runs/<run_id>/routing_results.json human_decisions.json

Workflow: LOAD -> VALIDATE -> (stop on errors) -> COMPLETE -> REPORT -> SAVE
Expected policy checks come from the run's routing_config_used.json snapshot.
Writes review_completion.json into the run folder; no other file is written.
Any validation error means nothing is written. Missing decisions are valid:
those checks stay pending.

Inputs are never modified. The original AI verdict, justification, routing
action, routing reason and result_source are kept verbatim under "routing".

Scope: CHECK-LEVEL completion only. "CHECKS_RESOLVED" means every policy check
has a final PASS/FAIL and a recorded source (AI or HUMAN). It is NOT a
developer-facing application disposition: a resolved submission with a FAIL
outcome is not "rejected" here. Dispositions remain out of scope.

Human decision record (human_decisions.json, one per decided check):
    {"submission_id", "policy_check", "decision": "PASS"|"FAIL",
     "reviewer", "rationale", "decided_at": ISO-8601}

Which routed checks a human may decide:
    HUMAN_REVIEW with routing_reason in HUMAN_RESOLVABLE_REASONS
    (MODEL_UNDECIDED, CONFIG_REQUIRES_HUMAN) and a known policy check.
Every other HUMAN_REVIEW entry (UNKNOWN_CHECK, DUPLICATE_CHECK, MISSING_CHECK,
INVALID_VERDICT, or anything unexpected) is an unresolved anomaly: it is kept,
listed, never given a final outcome, and blocks CHECKS_RESOLVED.

Integrity anomalies (malformed or hand-edited routing records) are treated the
same way, even when the routing action is automated:
    DUPLICATE_ROUTED_ENTRY               more than one entry for an expected check
    AUTOMATED_ACTION_CONTRADICTS_VERDICT  e.g. verdict FAIL with AUTO_APPROVE
"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

from main import VERDICTS   # ("PASS", "FAIL"): same values as golden labels
from main import _index_by_submission_id, load_json
from routing_engine import CONFIG_SNAPSHOT_FILENAME

COMPLETION_OUTPUT_FILENAME = "review_completion.json"
USAGE = "Usage: python human_review.py runs/<run_id>/routing_results.json human_decisions.json"

HUMAN_RESOLVABLE_REASONS = frozenset({"MODEL_UNDECIDED", "CONFIG_REQUIRES_HUMAN"})
AUTOMATED_OUTCOMES = {"AUTO_APPROVE": "PASS", "AUTO_REJECT": "FAIL"}
DECISION_FIELDS = ("submission_id", "policy_check", "decision",
                   "reviewer", "rationale", "decided_at")

STATUS_RESOLVED = "CHECKS_RESOLVED"
STATUS_PENDING = "PENDING_HUMAN_REVIEW"

AI_FINAL = "AI_FINAL"
HUMAN_PENDING = "HUMAN_PENDING"
ANOMALY = "ANOMALY"

DUPLICATE_ROUTED_ENTRY = "DUPLICATE_ROUTED_ENTRY"
CONTRADICTS_VERDICT = "AUTOMATED_ACTION_CONTRADICTS_VERDICT"


def _classify(routed, expected_checks):
    """AI_FINAL, HUMAN_PENDING (a human may decide it) or ANOMALY."""
    if routed.get("policy_check") not in expected_checks:
        return ANOMALY
    if routed.get("routing_action") in AUTOMATED_OUTCOMES:
        return AI_FINAL
    if (routed.get("routing_action") == "HUMAN_REVIEW"
            and routed.get("routing_reason") in HUMAN_RESOLVABLE_REASONS):
        return HUMAN_PENDING
    return ANOMALY


def _entry_status(routed, expected_checks, duplicated):
    """(kind, anomaly_reason): _classify plus routing-record integrity checks.

    anomaly_reason is the entry's own routing_reason for routing anomalies, an
    integrity code for duplicated or contradictory entries, and None otherwise.
    """
    kind = _classify(routed, expected_checks)
    if kind == ANOMALY:
        return ANOMALY, routed.get("routing_reason")
    if duplicated:
        return ANOMALY, DUPLICATE_ROUTED_ENTRY
    if kind == AI_FINAL and routed.get("verdict") != AUTOMATED_OUTCOMES[routed["routing_action"]]:
        return ANOMALY, CONTRADICTS_VERDICT
    return kind, None


def _routed_entries(routing_record, policy_check):
    checks = routing_record.get("checks")
    if not isinstance(checks, list):
        return []
    return [c for c in checks if isinstance(c, dict) and c.get("policy_check") == policy_check]


def _field_errors(decision):
    """Shape and value problems of one decision record."""
    problems = []
    for field in DECISION_FIELDS:
        if field not in decision:
            problems.append(f"MISSING_FIELD {field}")
    for field in decision:
        if field not in DECISION_FIELDS:
            problems.append(f"UNKNOWN_FIELD {field}")
    for field in ("submission_id", "policy_check", "reviewer", "rationale"):
        value = decision.get(field)
        if field in decision and (not isinstance(value, str) or not value.strip()):
            problems.append(f"INVALID_FIELD {field} (must be a non-empty string)")
    if "decision" in decision and decision["decision"] not in VERDICTS:
        problems.append(f"INVALID_DECISION {decision['decision']!r} (must be PASS or FAIL)")
    if "decided_at" in decision:
        try:
            datetime.fromisoformat(decision["decided_at"])
        except (TypeError, ValueError):
            problems.append(f"INVALID_FIELD decided_at {decision['decided_at']!r} (must be ISO-8601)")
    return problems


def validate_decisions(decisions, routing_by_id, expected_checks, errors):
    """Validate human decisions against routing results.

    decisions:       parsed human_decisions.json (expected: a list of records)
    routing_by_id:   {submission_id: routing_record} for one run
    expected_checks: policy check names for that run (routing config keys)
    errors:          list; every problem is appended, nothing is raised

    Returns {(submission_id, policy_check): decision} for decisions that are
    individually valid and not duplicated. Callers must treat ANY error as
    "write nothing" (all-or-nothing), as main.py does.
    """
    label = "human_decisions"
    if not isinstance(decisions, list):
        errors.append(f"{label}: NOT_A_LIST")
        return {}

    candidates = {}   # key -> [(index, decision)]
    for index, decision in enumerate(decisions):
        where = f"{label}[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{where}: INVALID_RECORD (not an object)")
            continue
        problems = _field_errors(decision)
        if problems:
            errors.extend(f"{where}: {problem}" for problem in problems)
            continue

        submission_id, policy_check = decision["submission_id"], decision["policy_check"]
        where = f"{where} {submission_id}/{policy_check}"
        routing_record = routing_by_id.get(submission_id)
        if routing_record is None:
            errors.append(f"{where}: UNKNOWN_SUBMISSION")
            continue
        if policy_check not in expected_checks:
            errors.append(f"{where}: UNKNOWN_CHECK")
            continue

        entries = _routed_entries(routing_record, policy_check)
        if len(entries) != 1:
            reasons = [entry.get("routing_reason") for entry in entries] or ["NOT_IN_ROUTING_RESULTS"]
            errors.append(f"{where}: CHECK_HAS_ANOMALY {reasons}")
            continue
        kind, reason = _entry_status(entries[0], expected_checks, duplicated=False)
        if kind == ANOMALY:
            errors.append(f"{where}: CHECK_HAS_ANOMALY {[reason]}")
            continue
        if kind == AI_FINAL:
            errors.append(f"{where}: NOT_PENDING_HUMAN_REVIEW "
                          f"(routed {entries[0].get('routing_action')})")
            continue

        candidates.setdefault((submission_id, policy_check), []).append((index, decision))

    valid = {}
    for (submission_id, policy_check), found in candidates.items():
        if len(found) > 1:   # identical or conflicting: both rejected
            indexes = [index for index, _ in found]
            values = [decision["decision"] for _, decision in found]
            errors.append(f"{label} {submission_id}/{policy_check}: DUPLICATE_DECISION "
                          f"at indexes {indexes} (decisions {values})")
            continue
        valid[(submission_id, policy_check)] = found[0][1]
    return valid


def complete_submission(routing_record, decisions_for_submission, expected_checks):
    """Derive one submission's completion record.

    routing_record:           one record from routing_results.json (not modified)
    decisions_for_submission: {policy_check: decision} from validate_decisions()
    expected_checks:          policy check names for the run

    Raises ValueError if a decision targets a check a human may not decide;
    that means validate_decisions() was skipped (a programming error).
    """
    submission_id = routing_record.get("submission_id")
    unused = set(decisions_for_submission)
    checks, pending, anomalies = [], [], []

    routed_checks = routing_record.get("checks")
    routed_checks = routed_checks if isinstance(routed_checks, list) else []
    names = [raw.get("policy_check") for raw in routed_checks if isinstance(raw, dict)]
    for raw in routed_checks:
        routed = raw if isinstance(raw, dict) else {}
        name = routed.get("policy_check")
        kind, anomaly_reason = _entry_status(routed, expected_checks,
                                             duplicated=names.count(name) > 1)
        human = decisions_for_submission.get(name) if kind == HUMAN_PENDING else None
        unused.discard(name if kind == HUMAN_PENDING else None)

        if kind == AI_FINAL:
            outcome, source = AUTOMATED_OUTCOMES[routed["routing_action"]], "AI"
        elif human is not None:
            outcome, source = human["decision"], "HUMAN"
        else:
            outcome, source = None, None
            if kind == HUMAN_PENDING:
                pending.append(name)
            else:
                anomalies.append({"policy_check": name, "routing_reason": anomaly_reason})

        ai_verdict = routed.get("verdict")
        checks.append({
            "policy_check": name,
            "routing": copy.deepcopy(raw),      # original AI + routing fields, verbatim
            "human_decision": copy.deepcopy(human),
            "final_outcome": outcome,
            "outcome_source": source,
            "human_agrees_with_ai": (human["decision"] == ai_verdict
                                     if human is not None and ai_verdict in VERDICTS else None),
            "anomaly": kind == ANOMALY,
        })

    if unused:
        raise ValueError(f"{submission_id}: decisions for checks a human may not decide: "
                         f"{sorted(map(str, unused))} (run validate_decisions first)")

    # Defensive: an expected check absent from a malformed routing record.
    present = {check["policy_check"] for check in checks}
    for name in expected_checks:
        if name not in present:
            anomalies.append({"policy_check": name, "routing_reason": "NOT_IN_ROUTING_RESULTS"})

    resolved = not pending and not anomalies and all(
        check["final_outcome"] in VERDICTS for check in checks)
    return {
        "submission_id": submission_id,
        "routing_workflow_status": routing_record.get("workflow_status"),
        "completion_status": STATUS_RESOLVED if resolved else STATUS_PENDING,
        "pending_checks": pending,
        "unresolved_anomalies": anomalies,
        "checks": checks,
    }


# ------------------------------------------------------------------ CLI

def build_completion(routing_results, decisions, routing_config, errors):
    """Validate all loaded inputs and build the review_completion content.

    Every problem is appended to errors. Returns None when there are errors,
    so the caller writes nothing.
    """
    if not isinstance(routing_config, dict) or len(routing_config) == 0:
        errors.append(f"{CONFIG_SNAPSHOT_FILENAME}: EMPTY or not an object")
    if not isinstance(routing_results, list) or len(routing_results) == 0:
        errors.append("routing_results: EMPTY or not a list")
    if errors:
        return None

    expected_checks = list(routing_config)
    routing_by_id = _index_by_submission_id(routing_results, "routing_results", errors)
    valid = validate_decisions(decisions, routing_by_id, expected_checks, errors)
    if errors:
        return None

    submissions = []
    for submission_id, routing_record in routing_by_id.items():
        mine = {check: d for (sid, check), d in valid.items() if sid == submission_id}
        submissions.append(complete_submission(routing_record, mine, expected_checks))

    resolved = sum(s["completion_status"] == STATUS_RESOLVED for s in submissions)
    return {
        "policy_checks": expected_checks,
        "summary": {
            "total_submissions": len(submissions),
            "checks_resolved": resolved,
            "pending_human_review": len(submissions) - resolved,
            "human_decisions_applied": len(valid),
        },
        "submissions": submissions,
    }


def _exit_without_writing(output_path):
    """Exit 1. An existing output is preserved, but flagged as possibly stale."""
    if output_path.exists():
        print(f"WARNING: existing {output_path} was NOT updated and may contain stale results.")
    sys.exit(1)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        print("ERROR: Incorrect number of arguments.")
        print(USAGE)
        sys.exit(1)

    routing_path = Path(argv[1])
    decisions_path = Path(argv[2])
    run_dir = routing_path.parent
    config_path = run_dir / CONFIG_SNAPSHOT_FILENAME
    output_path = run_dir / COMPLETION_OUTPUT_FILENAME

    # LOAD (main.load_json prints a clear ERROR and exits if a file is missing
    # or not valid JSON; that exit is intercepted only to add the stale warning)
    try:
        routing_results = load_json(routing_path)
        routing_config = load_json(config_path)
        decisions = load_json(decisions_path)
    except SystemExit:
        _exit_without_writing(output_path)

    # VALIDATE + COMPLETE
    errors = []
    completion = build_completion(routing_results, decisions, routing_config, errors)
    if errors:
        for error in errors:
            print(error)
        print(f"Validation failed: {len(errors)} error(s). {COMPLETION_OUTPUT_FILENAME} not written.")
        _exit_without_writing(output_path)

    result = {
        "routing_results_file": str(routing_path),
        "routing_config_file": str(config_path),
        "human_decisions_file": str(decisions_path),
        **completion,
    }

    # REPORT
    for submission in result["submissions"]:
        print(submission["submission_id"], submission["completion_status"],
              "pending:", submission["pending_checks"],
              "anomalies:", [a["policy_check"] for a in submission["unresolved_anomalies"]])
    summary = result["summary"]
    print(f"Resolved: {summary['checks_resolved']}/{summary['total_submissions']}  "
          f"Human decisions applied: {summary['human_decisions_applied']}")

    # SAVE (serialize first, so a failure cannot leave a partial file)
    text = json.dumps(result, indent=2)
    with open(output_path, "w") as file:
        file.write(text)
    print("Review completion written to:", output_path)
    return result


if __name__ == "__main__":
    main()

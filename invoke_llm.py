"""LLM inference: review each submission against the three policy checks.

Usage:
    python invoke_llm.py

Reads submissions.json and prompts/, calls the OpenAI Responses API once per
submission, and writes runs/<timestamp>/predictions.json only if EVERY
submission succeeds (all-or-nothing).

Reusable functions take the API client as a parameter (dependency injection).
Importing this module never creates a client, creates a run folder, or makes
API calls. Only main() does that.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

MODEL = "gpt-4.1-mini"
POLICY_CHECKS = ("metadata_quality", "real_money_gambling", "child_safety")

SUBMISSIONS_PATH = Path("submissions.json")
SYSTEM_PROMPT_PATH = Path("prompts/system_prompt.md")
USER_PROMPT_PATH = Path("prompts/user_prompt.md")
RUNS_ROOT = Path("runs")
PREDICTIONS_FILENAME = "predictions.json"


def build_schema():
    """Strict JSON schema for one submission's review."""
    return {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "policy_check": {"type": "string", "enum": list(POLICY_CHECKS)},
                        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                        "justification": {"type": "string"},
                    },
                    "required": ["policy_check", "verdict", "justification"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["checks"],
        "additionalProperties": False,
    }


def load_inputs(submissions_path=SUBMISSIONS_PATH,
                system_prompt_path=SYSTEM_PROMPT_PATH,
                user_prompt_path=USER_PROMPT_PATH):
    """Return (submissions, system_prompt, user_prompt_template)."""
    with open(submissions_path, "r") as file:
        submissions = json.load(file)
    with open(system_prompt_path, "r") as file:
        system_prompt = file.read()
    with open(user_prompt_path, "r") as file:
        user_prompt_template = file.read()
    return submissions, system_prompt, user_prompt_template


def create_run_dir(runs_root=RUNS_ROOT):
    """Create and return runs/<timestamp>/. Fails if it already exists."""
    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(runs_root) / run_id
    run_dir.mkdir()
    return run_dir


def review_submission(client, app, system_prompt, user_prompt_template, model=MODEL):
    """Call the model for one submission and return its validated list of checks.

    Raises if the response is not valid JSON, lacks 'checks', or does not
    contain exactly one of each policy check.
    """
    user_prompt = user_prompt_template.format(
        app_name=app["app_name"],
        description=app["description"],
    )
    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "app_review",
                "strict": True,
                "schema": build_schema(),
            }
        },
    )
    result = json.loads(response.output_text)

    returned_checks = [check["policy_check"] for check in result["checks"]]
    if (
        len(returned_checks) != len(POLICY_CHECKS)
        or set(returned_checks) != set(POLICY_CHECKS)
    ):
        raise ValueError("Missing or duplicate policy checks")

    return result["checks"]


def run_inference(client, submissions, system_prompt, user_prompt_template, model=MODEL):
    """Review every submission. Failed submissions are reported and skipped.

    Returns the list of successful predictions:
        [{"submission_id": ..., "checks": [...]}, ...]
    """
    predictions = []
    for app in submissions:
        try:
            checks = review_submission(client, app, system_prompt, user_prompt_template, model)
        except Exception as error:
            print("ERROR processing submission:", app["submission_id"])
            print("Details:", error)
            continue

        predictions.append({
            "submission_id": app["submission_id"],
            "checks": checks,
        })
        print("Submission ID:", app["submission_id"])
        print()

    return predictions


def save_predictions(predictions, submissions, run_dir):
    """All-or-nothing save. Returns the file path, or None if any submission failed."""
    if len(predictions) != len(submissions):
        return None
    predictions_path = Path(run_dir) / PREDICTIONS_FILENAME
    with open(predictions_path, "w") as file:
        json.dump(predictions, file, indent=2)
    return predictions_path


def main(client=None, runs_root=RUNS_ROOT):
    """CLI entry point. Creates the real OpenAI client unless one is injected."""
    if client is None:
        # Imported here so importing this module does not require or touch the SDK.
        from openai import OpenAI
        client = OpenAI()

    run_dir = create_run_dir(runs_root)
    submissions, system_prompt, user_prompt_template = load_inputs()

    predictions = run_inference(client, submissions, system_prompt, user_prompt_template)

    print("\nALL PREDICTIONS:")
    print(json.dumps(predictions, indent=2))

    if save_predictions(predictions, submissions, run_dir) is None:
        print("ERROR: Incomplete predictions. Existing file was not overwritten.")
        sys.exit(1)

    return run_dir


if __name__ == "__main__":
    main()

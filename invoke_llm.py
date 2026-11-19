"""LLM inference: review each submission against the three policy checks.

Usage:
    python invoke_llm.py              # description-only (default)
    python invoke_llm.py --evidence   # also retrieve evidence via the MCP server

Reads submissions.json and prompts/, calls the OpenAI Responses API once per
submission, and writes runs/<timestamp>/predictions.json only if EVERY
submission succeeds (all-or-nothing). With --evidence it also writes
evidence.json (provenance) under the same all-or-nothing rule.

Evidence is used in a request only when its status is OK. Otherwise the request
is byte-for-byte the description-only request.

Reusable functions take the API client as a parameter (dependency injection).
Importing this module never creates a client, creates a run folder, starts the
MCP server, or makes API calls. Only main() does that.
"""

import html
import json
import sys
from datetime import datetime
from pathlib import Path

MODEL = "gpt-4.1-mini"
POLICY_CHECKS = ("metadata_quality", "real_money_gambling", "child_safety")

SUBMISSIONS_PATH = Path("submissions.json")
SYSTEM_PROMPT_PATH = Path("prompts/system_prompt.md")
USER_PROMPT_PATH = Path("prompts/user_prompt.md")
EVIDENCE_INSTRUCTIONS_PATH = Path("prompts/evidence_instructions.md")
RUNS_ROOT = Path("runs")
PREDICTIONS_FILENAME = "predictions.json"
EVIDENCE_FILENAME = "evidence.json"
EVIDENCE_TAG = "untrusted_evidence"
EVIDENCE_FLAG = "--evidence"


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
                        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNDECIDED"]},
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


def load_evidence_instructions(path=EVIDENCE_INSTRUCTIONS_PATH):
    with open(path, "r") as file:
        return file.read()


def evidence_is_usable(evidence):
    """Only OK evidence with non-empty text ever reaches the prompt."""
    return (isinstance(evidence, dict)
            and evidence.get("status") == "OK"
            and isinstance(evidence.get("text"), str)
            and bool(evidence["text"].strip()))


def format_evidence_block(evidence):
    """Delimited, HTML-escaped evidence block.

    Escaping turns every < > & " ' in the untrusted text into an entity, so the
    text cannot close the block or open a fake one.
    """
    source_url = html.escape(str(evidence.get("source_url")), quote=True)
    sha256 = html.escape(str(evidence.get("sha256")), quote=True)
    text = html.escape(evidence["text"], quote=True)
    return (f'<{EVIDENCE_TAG} source_url="{source_url}" sha256="{sha256}">\n'
            f"{text}\n"
            f"</{EVIDENCE_TAG}>")


def build_user_prompt(app, user_prompt_template, evidence=None):
    """Description-only prompt, plus the evidence block when evidence is usable.

    The template is formatted BEFORE the evidence is appended, so braces in
    evidence text are never interpreted as format fields.
    """
    user_prompt = user_prompt_template.format(
        app_name=app["app_name"],
        description=app["description"],
    )
    if not evidence_is_usable(evidence):
        return user_prompt
    return f"{user_prompt}\n\n{format_evidence_block(evidence)}"


def build_instructions(system_prompt, evidence=None, evidence_instructions=None):
    """System prompt, plus the evidence rules only when evidence is used."""
    if not evidence_is_usable(evidence):
        return system_prompt
    if not evidence_instructions:
        raise ValueError("evidence_instructions are required when evidence is used")
    return f"{system_prompt}\n\n{evidence_instructions}"


def review_submission(client, app, system_prompt, user_prompt_template, model=MODEL,
                      evidence=None, evidence_instructions=None):
    """Call the model for one submission and return its validated list of checks.

    Raises if the response is not valid JSON, lacks 'checks', or does not
    contain exactly one of each policy check.
    """
    instructions = build_instructions(system_prompt, evidence, evidence_instructions)
    user_prompt = build_user_prompt(app, user_prompt_template, evidence)
    response = client.responses.create(
        model=model,
        instructions=instructions,
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


def run_inference(client, submissions, system_prompt, user_prompt_template, model=MODEL,
                  evidence_by_id=None, evidence_instructions=None):
    """Review every submission. Failed submissions are reported and skipped.

    evidence_by_id: optional {submission_id: evidence_record}. Missing or
    non-OK evidence means a description-only request for that submission.

    Returns the list of successful predictions:
        [{"submission_id": ..., "checks": [...]}, ...]
    """
    evidence_by_id = evidence_by_id or {}
    predictions = []
    for app in submissions:
        try:
            checks = review_submission(client, app, system_prompt, user_prompt_template, model,
                                       evidence=evidence_by_id.get(app["submission_id"]),
                                       evidence_instructions=evidence_instructions)
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


def retrieve_evidence(evidence_fetcher, submissions):
    """Fetch evidence for every submission in ONE fetcher call (one MCP session).

    Raises if the fetcher breaks its contract of one record per submission;
    that is a programming error, not missing evidence.
    """
    submission_ids = [app["submission_id"] for app in submissions]
    evidence_by_id = evidence_fetcher(submission_ids)
    if not isinstance(evidence_by_id, dict) or set(evidence_by_id) != set(submission_ids):
        raise ValueError("Evidence fetcher must return one record per submission_id")
    return evidence_by_id


def build_evidence_records(submissions, evidence_by_id):
    """Provenance per submission, in submission order, with used_in_prompt."""
    records = []
    for app in submissions:
        evidence = evidence_by_id[app["submission_id"]]
        records.append({**evidence, "used_in_prompt": evidence_is_usable(evidence)})
    return records


def save_evidence(records, run_dir):
    evidence_path = Path(run_dir) / EVIDENCE_FILENAME
    with open(evidence_path, "w") as file:
        json.dump(records, file, indent=2)
    return evidence_path


def main(client=None, runs_root=RUNS_ROOT, use_evidence=False, evidence_fetcher=None):
    """CLI entry point. Creates the real OpenAI client unless one is injected.

    use_evidence=False (default) never imports the MCP adapter or starts the
    MCP server. use_evidence=True fetches evidence before inference using
    evidence_fetcher (default: evidence_client.fetch_evidence).
    """
    if client is None:
        # Imported here so importing this module does not require or touch the SDK.
        from dotenv import load_dotenv
        from openai import OpenAI
        # Load OPENAI_API_KEY from the project's .env (git-ignored).
        # Variables already set in the shell take precedence.
        load_dotenv(Path(__file__).parent / ".env")
        client = OpenAI()

    run_dir = create_run_dir(runs_root)
    submissions, system_prompt, user_prompt_template = load_inputs()

    evidence_by_id = None
    evidence_instructions = None
    if use_evidence:
        if evidence_fetcher is None:
            # Imported here so description-only runs never load the MCP client.
            from evidence_client import fetch_evidence as evidence_fetcher
        evidence_instructions = load_evidence_instructions()
        evidence_by_id = retrieve_evidence(evidence_fetcher, submissions)
        for app in submissions:
            evidence = evidence_by_id[app["submission_id"]]
            print("Evidence:", app["submission_id"], evidence["status"],
                  "used" if evidence_is_usable(evidence) else f"not used ({evidence['reason']})")

    predictions = run_inference(client, submissions, system_prompt, user_prompt_template,
                                evidence_by_id=evidence_by_id,
                                evidence_instructions=evidence_instructions)

    print("\nALL PREDICTIONS:")
    print(json.dumps(predictions, indent=2))

    if save_predictions(predictions, submissions, run_dir) is None:
        print("ERROR: Incomplete predictions. Existing file was not overwritten.")
        sys.exit(1)

    if use_evidence:
        save_evidence(build_evidence_records(submissions, evidence_by_id), run_dir)

    return run_dir


def parse_args(argv):
    """Return use_evidence. Only --evidence is accepted."""
    unknown = [arg for arg in argv if arg != EVIDENCE_FLAG]
    if unknown:
        print("ERROR: Unknown arguments:", " ".join(unknown))
        print(f"Usage: python invoke_llm.py [{EVIDENCE_FLAG}]")
        sys.exit(1)
    return EVIDENCE_FLAG in argv


if __name__ == "__main__":
    main(use_evidence=parse_args(sys.argv[1:]))

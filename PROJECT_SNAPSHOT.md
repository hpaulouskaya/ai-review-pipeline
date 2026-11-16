# Project Snapshot — Mini Eval Harness

**Checkpoint:** M04 complete (check-level routing + submission workflow tracking)
**Date:** 2026-11-16
**Python:** 3.13 (`.venv`), dependency: `openai==3.46.0`

> The README currently describes only the legacy evaluator (`main.py`).
> This file is the up-to-date description of the project.

---

## 1. What this project does

An AI app-review pipeline:

1. An LLM reviews each app submission against three independent policy checks
   and returns a `PASS`/`FAIL` verdict with a justification for each.
2. A routing engine decides, per check, whether the AI result can be used
   automatically or needs a human reviewer, based on configurable permissions.
3. Each submission gets a workflow status showing whether any checks still
   await human review.

Final developer-facing decisions (approve/reject the app), notifications and
enforcement are **out of scope** and belong to a later downstream layer.

---

## 2. Architecture

```text
submissions.json ──────────┐
prompts/system_prompt.md ──┼──► invoke_llm.py ──► OpenAI (gpt-4.1-mini, strict JSON schema)
prompts/user_prompt.md ────┘          │
                                      │ validates: exactly 3 checks, no duplicates
                                      │ saves only if ALL submissions succeed
                                      ▼
                         runs/<timestamp>/predictions.json
                                      │
routing_config.json ──────► routing_engine.py runs/<timestamp>/predictions.json
                                      │
                                      ├──► runs/<timestamp>/routing_results.json
                                      └──► runs/<timestamp>/routing_config_used.json
```

### How the active components connect

| Component | Reads | Produces | Connects to |
|---|---|---|---|
| `submissions.json` | — | List of apps (`submission_id`, `app_name`, `description`) | Input to `invoke_llm.py` |
| `prompts/system_prompt.md` | — | The policy definitions (the three checks and PASS/FAIL rules) | Sent to the LLM as instructions by `invoke_llm.py` |
| `prompts/user_prompt.md` | — | Template with `{app_name}` and `{description}` placeholders | Filled in per app by `invoke_llm.py` |
| `invoke_llm.py` | The three files above | `runs/<timestamp>/predictions.json` | Calls OpenAI once per app. A strict JSON schema forces the exact check names and verdict values. Output feeds `routing_engine.py`. |
| `routing_config.json` | — | Per-check `auto_approve` / `auto_reject` permissions | Read by `routing_engine.py`. Its keys define the set of expected checks. |
| `routing_engine.py` | A `predictions.json` + `routing_config.json` | `routing_results.json` + `routing_config_used.json` in the same run folder | Final stage of the current pipeline. Output is the input for the future M05 layer. |
| `test_routing.py` | Imports `route_check`, `route_submission` | Pass/fail report | Unit-tests `routing_engine.py` with its own inline config (independent of `routing_config.json`). |

### Routing rules (`routing_engine.py`)

- `route_check()` — routes one check:
  - `PASS` + `auto_approve` is exactly `true` → `AUTO_APPROVE`
  - `FAIL` + `auto_reject` is exactly `true` → `AUTO_REJECT`
  - anything else → `HUMAN_REVIEW`
  - Unknown check name, invalid or non-string verdict, missing/non-boolean
    config flag → always `HUMAN_REVIEW`.
  - Justification is preserved but never affects routing.
- `route_submission()` — routes all checks of one submission independently:
  - Duplicate checks → `HUMAN_REVIEW` (`DUPLICATE_CHECK`).
  - Expected checks that are missing → placeholder with `HUMAN_REVIEW` (`MISSING_CHECK`).
  - Any check in `HUMAN_REVIEW` (or no checks at all) → `PENDING_HUMAN_REVIEW`,
    otherwise `AUTOMATED_CHECKS_COMPLETE`.
  - A single `AUTO_REJECT` never rejects the whole submission.
- `main()` — loads config + predictions, fails loudly on bad input, writes results
  and a snapshot of the config used.

---

## 3. Data contracts

| Field | Created by | Consumed by | Values |
|---|---|---|---|
| `submission_id` | `submissions.json` | Every stage; the join key across all files | e.g. `APP001` |
| `policy_check` | LLM (enforced by JSON schema) | `routing_engine.py` (looked up in config) | `metadata_quality`, `real_money_gambling`, `child_safety` |
| `verdict` | LLM | `routing_engine.py`; preserved unchanged in output | `PASS`, `FAIL` |
| `justification` | LLM | Preserved unchanged in output | free text |
| `routing_action` | `route_check()` | Per-check result | `AUTO_APPROVE`, `AUTO_REJECT`, `HUMAN_REVIEW` |
| `result_source` | `route_check()` | Per-check result | `AI`, `PENDING_HUMAN` |
| `routing_reason` | `route_check()` / `route_submission()` | Audit trail | `CONFIG_ALLOWS_AUTO_APPROVE`, `CONFIG_ALLOWS_AUTO_REJECT`, `CONFIG_REQUIRES_HUMAN`, `UNKNOWN_CHECK`, `INVALID_VERDICT`, `DUPLICATE_CHECK`, `MISSING_CHECK` |
| `human_review_checks` | `route_submission()` | Reviewers / M05 | list of check names |
| `workflow_status` | `route_submission()` | M05 layer | `PENDING_HUMAN_REVIEW`, `AUTOMATED_CHECKS_COMPLETE` |

**Important:** the three policy check names must stay identical in three places:
`prompts/system_prompt.md`, the JSON schema in `invoke_llm.py`, and the keys of
`routing_config.json`.

### Example routed submission

```json
{
  "submission_id": "APP002",
  "workflow_status": "PENDING_HUMAN_REVIEW",
  "human_review_checks": ["real_money_gambling"],
  "checks": [
    {
      "policy_check": "real_money_gambling",
      "verdict": "FAIL",
      "justification": "The description states users can deposit real money ...",
      "routing_action": "HUMAN_REVIEW",
      "result_source": "PENDING_HUMAN",
      "routing_reason": "CONFIG_REQUIRES_HUMAN"
    }
  ]
}
```
(Other two checks omitted for brevity.)

---

## 4. Execution

Run from the project root.

```bash
# One-time setup
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# LLM inference — makes paid OpenAI API calls.
# OPENAI_API_KEY must be exported in the shell (the code does not load .env itself).
python invoke_llm.py

# Routing on existing predictions — no API calls
python routing_engine.py runs/2026-11-15_15-36-16/predictions.json

# Routing unit tests — no API calls
python test_routing.py
```

---

## 5. Verified M04 results

**Unit tests:** `test_routing.py` — 17 / 17 passed.

**End-to-end run:** `runs/2026-11-15_15-36-16/` (committed as the reference run).

Config used: all three checks `auto_approve: true`, `auto_reject: false`.

| Submission | Workflow status | Human review checks |
|---|---|---|
| APP001 | `AUTOMATED_CHECKS_COMPLETE` | — |
| APP002 | `PENDING_HUMAN_REVIEW` | `real_money_gambling` (FAIL, auto-reject not allowed) |
| APP003 | `AUTOMATED_CHECKS_COMPLETE` | — |

Totals: **8** `AUTO_APPROVE`, **0** `AUTO_REJECT`, **1** `HUMAN_REVIEW`.

Also verified: all original verdicts and justifications preserved, no missing
or duplicate checks, and `routing_config_used.json` matches the config in use.

---

## 6. File inventory

### Active

| File | Role |
|---|---|
| `invoke_llm.py` | LLM inference |
| `routing_engine.py` | Check routing + submission workflow |
| `test_routing.py` | Routing unit tests |
| `routing_config.json` | Routing permissions |
| `prompts/system_prompt.md`, `prompts/user_prompt.md` | LLM prompts |
| `submissions.json` | Input submissions |
| `requirements.txt` | Python dependencies |
| `runs/2026-11-15_15-36-16/*.json` | Verified M04 reference artifacts |

### Legacy (kept unchanged; from the earlier single-verdict design)

These use the old format of one `approved`/`rejected` verdict per submission
and are **not compatible** with the current multi-check pipeline.

| File | Notes |
|---|---|
| `main.py` | Evaluator: accuracy / precision / recall vs. golden dataset |
| `golden_dataset.json` | Old ground truth (`expected_verdict`) |
| `model_predictions.json` | Old prediction sample |
| `test_predictions.json`, `test_predictions_empty.json` | Test data for `main.py` |
| `test_golden_duplicates.json`, `test_golden_empty.json`, `test_golden_invalid.json` | Test data for `main.py` |
| `test_invoke_llm.py` | **Stale:** mocks old response shape; would now fail. Creates a run folder. |
| `test_api_failure.py` | **Stale:** mocks old response shape. Creates a run folder. |
| `README.md` | Describes only `main.py` |

### Not committed (see `.gitignore`)

`.env` (API key), `.venv/`, `__pycache__/`, `.pytest_cache/`, `.DS_Store`,
and all run folders/files except the three reference artifacts.

---

## 7. Known limitations

1. **No evaluation of the multi-check pipeline.** `main.py` and the golden
   dataset still use the old single-verdict format.
2. **Stale tests.** `test_invoke_llm.py` and `test_api_failure.py` mock the old
   response shape and need updating before reuse.
3. **Empty run folders.** `invoke_llm.py` creates the run folder before any API
   call, so failed runs leave empty folders behind.
4. **Re-routing overwrites.** Running `routing_engine.py` on the same run again
   overwrites `routing_results.json` and `routing_config_used.json`.
5. **`.env` is not loaded automatically.** `OPENAI_API_KEY` must be exported in
   the shell.
6. **Unnamed checks.** A check with no `policy_check` name is safely routed to
   human review, but appears as `null` in `human_review_checks`.
7. **README is outdated.** It documents only the legacy evaluator.
8. **Out of scope until later milestones:** final dispositions, developer
   notifications, enforcement.
